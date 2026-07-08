# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - file-based usage instrumentation.

Wraps every registered MCP tool with a fire-and-forget usage recorder that
appends one JSON object per line (JSON Lines) to a local file per call, so an
external collector (filebeat/fluentd/...) can scrape it for statistics. The
instrumentation is strictly best-effort: it never changes a tool's return
value, never raises into the tool call path, and stays completely silent (fully
disabled, a thin pass-through) when ``AWX_MCP_USAGE_LOG_FILE`` is unset.

No network delivery happens here — the file is the only sink. Writes go through
a :class:`logging.handlers.TimedRotatingFileHandler`, which is thread-safe, so
no background queue/worker is needed. The file rotates at midnight (UTC) with a
date-suffixed backup, keeping ``AWX_MCP_LOG_BACKUP_COUNT`` backups (default 7).
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from typing import Any
from urllib.parse import urlparse

from .utils import mask_secrets

logger = logging.getLogger("ansible-mcp.usage")

# --- Configuration (read at import; path unset => instrumentation off) --------
USAGE_LOG_FILE = os.environ.get("AWX_MCP_USAGE_LOG_FILE")


def log_backup_count() -> int:
    """Number of rotated backups to keep (env ``AWX_MCP_LOG_BACKUP_COUNT``, 7)."""
    try:
        return int(os.environ.get("AWX_MCP_LOG_BACKUP_COUNT", "7"))
    except (TypeError, ValueError):
        return 7


def make_timed_rotating_handler(path: str) -> TimedRotatingFileHandler:
    """Build a midnight-rotating (UTC, date-suffixed) file handler.

    Shared by the usage log and the server diagnostic log so both honour the
    same rotation policy.
    """
    return TimedRotatingFileHandler(
        path, when="midnight", utc=True, backupCount=log_backup_count()
    )


# --- User + version resolution ------------------------------------------------
# ``_user_cache`` holds the AWX username resolved once for the process lifetime.
# This public repo has no per-request token context, so a single process-wide
# cache (guarded by ``_user_lock``) is sufficient.
_user_cache: str | None = None
_user_lock = threading.Lock()


def _lookup_me_username() -> str:
    """Query AWX ``/api/v2/me/`` for the username; also record the call.

    Returns the username or ``"unknown"``. This ``/api/v2/me/`` request is the
    single extra AWX API call that usage logging adds (once per process, then
    cached by :func:`resolve_user_identifier`). It is itself written to the
    usage log as an ``internal_api`` entry so statistics can account for the
    added call.
    """
    start = time.monotonic()
    success = True
    error: dict[str, str] | None = None
    username = "unknown"
    try:
        # Imported lazily to avoid an import cycle (client -> server -> usage).
        from .client import get_ansible_client

        with get_ansible_client() as client:
            me = client.request("GET", "/api/v2/me/")
        results = me.get("results") if isinstance(me, dict) else None
        if results:
            username = results[0].get("username") or "unknown"
        elif isinstance(me, dict):
            username = me.get("username") or "unknown"
    except Exception as exc:  # noqa: BLE001 — attribution must never fail a tool
        success = False
        error = _error_info(exc)
        logger.debug("usage user resolution failed: %s", exc)
        username = "unknown"
    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        # Pass the resolved username explicitly so this does NOT re-enter
        # resolve_user_identifier (which is mid-flight calling us).
        _record_internal_api("GET /api/v2/me/", username, success, latency_ms, error)
    return username


def resolve_user_identifier() -> str:
    """Resolve the AWX username for usage attribution (lazy, cached).

    Resolved once and cached for the process lifetime. On any failure the result
    is ``"unknown"`` and still cached, so a broken lookup is not retried on every
    tool call.
    """
    global _user_cache

    if _user_cache is not None:
        return _user_cache
    with _user_lock:
        if _user_cache is not None:
            return _user_cache
        _user_cache = _lookup_me_username()
    return _user_cache


def _server_version() -> str:
    try:
        from importlib.metadata import version

        return version("awx-mcp")
    except Exception:  # noqa: BLE001
        return "unknown"


def _awx_host() -> str:
    """Return the host portion of ``ANSIBLE_BASE_URL`` (or ``"unknown"``)."""
    base = os.environ.get("ANSIBLE_BASE_URL")
    if not base:
        return "unknown"
    try:
        parsed = urlparse(base)
        return parsed.hostname or parsed.netloc or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


# --- Payload ------------------------------------------------------------------
def build_payload(
    *,
    tool: str,
    success: bool,
    latency_ms: int,
    error: dict[str, str] | None = None,
    kind: str = "tool",
    user: str | None = None,
) -> dict[str, Any]:
    """Build the JSON usage document for a single call.

    ``kind`` distinguishes an MCP ``tool`` call from an ``internal_api`` call
    the server makes on its own behalf (e.g. the one-time ``/api/v2/me/`` user
    lookup), so statistics can separate real tool usage from that overhead.
    ``user`` may be supplied directly to skip user resolution — required when
    recording an internal call from inside that resolution to avoid recursion.
    """
    payload: dict[str, Any] = {
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "user": user if user is not None else resolve_user_identifier(),
        "tool": tool,
        "kind": kind,
        "trace_id": str(uuid.uuid4()),
        "server_version": _server_version(),
        "success": success,
        "latency_ms": latency_ms,
        "transport": os.environ.get("AWX_MCP_TRANSPORT", "stdio"),
        "awx_host": _awx_host(),
    }
    if not success and error is not None:
        payload["error"] = error
    return payload


def _error_info(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": mask_secrets(str(exc))}


# --- File sink ----------------------------------------------------------------
_usage_logger: logging.Logger | None = None
_usage_logger_lock = threading.Lock()


def _is_enabled() -> bool:
    return bool(USAGE_LOG_FILE)


def _get_usage_logger() -> logging.Logger | None:
    """Return the JSON Lines logger, building it lazily on first use.

    Returns ``None`` when instrumentation is disabled. The logger writes raw
    JSON (formatter is ``%(message)s``) and never propagates, so nothing reaches
    the root logger's stderr handler or stdout.
    """
    global _usage_logger
    if not USAGE_LOG_FILE:
        return None
    if _usage_logger is not None:
        return _usage_logger
    with _usage_logger_lock:
        if _usage_logger is not None:
            return _usage_logger
        lg = logging.getLogger("ansible-mcp.usage.jsonl")
        # getLogger returns a process-global singleton; drop any handler left by
        # a previous configuration (e.g. a module reload in tests) before adding.
        lg.handlers.clear()
        lg.setLevel(logging.INFO)
        lg.propagate = False
        handler = make_timed_rotating_handler(USAGE_LOG_FILE)
        handler.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(handler)
        _usage_logger = lg
    return _usage_logger


def _record(
    tool_name: str,
    start_monotonic: float,
    success: bool,
    exc: BaseException | None,
) -> None:
    """Build and append a usage document. Swallows all errors."""
    try:
        sink = _get_usage_logger()
        if sink is None:
            return
        latency_ms = int((time.monotonic() - start_monotonic) * 1000)
        error = _error_info(exc) if (not success and exc is not None) else None
        payload = build_payload(
            tool=tool_name,
            success=success,
            latency_ms=latency_ms,
            error=error,
        )
        sink.info(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — instrumentation never affects tools
        logger.debug("usage recording failed: %s", type(exc).__name__)


def _record_internal_api(
    tool_name: str,
    user: str,
    success: bool,
    latency_ms: int,
    error: dict[str, str] | None,
) -> None:
    """Append a usage entry for an internal AWX API call (``kind=internal_api``).

    ``user`` is passed explicitly and never re-resolved, so this is safe to call
    from within user resolution. Swallows all errors.
    """
    try:
        sink = _get_usage_logger()
        if sink is None:
            return
        payload = build_payload(
            tool=tool_name,
            success=success,
            latency_ms=latency_ms,
            error=error,
            kind="internal_api",
            user=user,
        )
        sink.info(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — instrumentation never affects tools
        logger.debug("internal-api usage recording failed: %s", type(exc).__name__)


# --- Public decorator ---------------------------------------------------------
def instrument_tool(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool function with fire-and-forget usage instrumentation.

    ``functools.wraps`` preserves the wrapped function's signature, docstring,
    and annotations so FastMCP's schema generation is unaffected. Supports both
    sync and async tools. When instrumentation is disabled (no log file) the
    wrapper is a thin pass-through.
    """
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _is_enabled():
                return await func(*args, **kwargs)
            start = time.monotonic()
            success = True
            captured: BaseException | None = None
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                success = False
                captured = exc
                raise
            finally:
                _record(func.__name__, start, success, captured)

        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not _is_enabled():
            return func(*args, **kwargs)
        start = time.monotonic()
        success = True
        captured: BaseException | None = None
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            success = False
            captured = exc
            raise
        finally:
            _record(func.__name__, start, success, captured)

    return wrapper

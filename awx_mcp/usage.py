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
import hashlib
import inspect
import json
import logging
import os
import re
import sys
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


def stdout_jsonl_enabled() -> bool:
    """True when JSONL records may also be written to stdout.

    Only the ``--serve`` network transports qualify: there stdout is unused, so
    the usage/access JSON Lines are mirrored to it for log collectors
    (``kubectl logs`` / fluentd). In stdio mode stdout carries the MCP protocol
    and in proxy mode it carries the relayed stdio stream — never write there.
    """
    return os.environ.get("AWX_MCP_EFFECTIVE_TRANSPORT") in (
        "sse",
        "streamable-http",
    )


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
# Static (local) mode has one identity for the process, cached in ``_user_cache``.
# Passthrough (--serve) mode sees a different caller per request, so usernames are
# cached per token in ``_user_cache_by_token`` keyed by a SHA-256 hash of the
# token (the raw token is never stored, in memory or the log). Both caches are
# guarded by ``_user_lock``.
_user_cache: str | None = None
_user_cache_by_token: dict[str, str] = {}
_user_lock = threading.Lock()
_MAX_TOKEN_CACHE = 1024


def _lookup_me_username(token: str | None = None) -> str:
    """Query AWX ``/api/v2/me/`` for the username; also record the call.

    Returns the username or ``"unknown"``. When ``token`` is given (passthrough
    mode) the lookup uses a client built for that specific token; otherwise it
    uses the process's static credentials. This is the single extra AWX API call
    usage logging adds (once per identity, then cached). It is written to the
    usage log as an ``internal_api`` entry so statistics can account for it.
    """
    start = time.monotonic()
    success = True
    error: dict[str, str] | None = None
    username = "unknown"
    try:
        # Imported lazily to avoid an import cycle (client -> server -> usage).
        from .client import AnsibleClient, get_ansible_client
        from .server import ANSIBLE_BASE_URL

        if token is not None:
            # ANSIBLE_BASE_URL is validated non-None at server import in every
            # mode; assert narrows str | None -> str for the type checker.
            assert ANSIBLE_BASE_URL is not None
            with AnsibleClient(base_url=ANSIBLE_BASE_URL, token=token) as client:
                me = client.request("GET", "/api/v2/me/")
        else:
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
        _record_internal_api(
            "me",
            username,
            success,
            latency_ms,
            error,
            method="GET",
            endpoint="/api/v2/me/",
        )
    return username


def resolve_user_identifier() -> str:
    """Resolve the AWX username for usage attribution (lazy, cached).

    In static mode the identity is resolved once and cached for the process
    lifetime. In passthrough mode it is resolved per request from the caller's
    token and cached by token hash (so a repeat caller triggers only one
    ``/api/v2/me/`` lookup). On any failure the result is ``"unknown"``.
    """
    global _user_cache

    # AUTH_MODE is imported lazily: server.py imports this module before it
    # defines AUTH_MODE, so a module-top import would be a cycle.
    from .server import AUTH_MODE

    if AUTH_MODE == "passthrough":
        from .client import get_request_token

        token = get_request_token()
        if not token:
            return "unknown"
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        cached = _user_cache_by_token.get(key)
        if cached is not None:
            return cached
        with _user_lock:
            cached = _user_cache_by_token.get(key)
            if cached is not None:
                return cached
            username = _lookup_me_username(token=token)
            if len(_user_cache_by_token) >= _MAX_TOKEN_CACHE:
                _user_cache_by_token.clear()
            _user_cache_by_token[key] = username
            return username

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
def _effective_transport() -> str:
    """The transport label for usage records.

    Derived from an internal env indicator set by the CLI (``main()``), NOT from
    the removed ``AWX_MCP_TRANSPORT``: ``stdio`` (local), ``streamable-http`` or
    ``sse`` (``--serve``). The proxy passes ``transport="proxy"`` explicitly.
    """
    return os.environ.get("AWX_MCP_EFFECTIVE_TRANSPORT", "stdio")


# Key names whose values are always redacted. Matched on whole ``_``-separated
# segments so a secret-bearing name (``password``, ``ssh_key_data``, credential
# ``inputs``) is redacted while a harmless id reference (``credential_id``) is
# not. Applied to top-level tool arguments AND to nested keys inside dict/JSON
# payloads.
_SENSITIVE_PARAM_RE = re.compile(
    r"(?:^|_)(?:password|passwd|secret|token|key|inputs|private)(?:_|$)",
    re.IGNORECASE,
)
# Free-form payload params that routinely carry arbitrary secrets (AWX launch /
# inventory-source variables, survey answers). Their whole value is redacted —
# we cannot know which nested keys are sensitive, so we do not log them at all.
_FREEFORM_SECRET_PARAMS = frozenset(
    {"extra_vars", "source_vars", "variables", "vars", "survey_spec", "credential"}
)
# Cap each logged string value so a large payload cannot bloat the log line.
_MAX_PARAM_VALUE_LEN = 512
# Bound recursion into nested structures (defensive against cyclic/huge input).
_MAX_REDACT_DEPTH = 6


def _is_sensitive_key(key: Any) -> bool:
    return bool(_SENSITIVE_PARAM_RE.search(str(key)))


def _redact_value(value: Any, depth: int = 0) -> Any:
    """Recursively redact secrets from a parameter value.

    dict -> redact any value whose key is sensitive, recurse into the rest.
    list -> recurse element-wise. JSON-looking strings are parsed and recursed
    so nested ``api_key``/``ssh_key`` values (e.g. inside an ``extra_vars`` blob)
    are caught; other strings pass through :func:`mask_secrets` (inline
    ``token=``/``password=``/``Bearer``) and are truncated. Scalars pass through.
    """
    if depth > _MAX_REDACT_DEPTH:
        return "<max-depth>"
    if isinstance(value, dict):
        return {
            k: ("***" if _is_sensitive_key(k) else _redact_value(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(v, depth + 1) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = value if isinstance(value, str) else str(value)
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            return _redact_value(json.loads(stripped), depth + 1)
        except (ValueError, TypeError):
            pass
    text = mask_secrets(text)
    if len(text) > _MAX_PARAM_VALUE_LEN:
        text = text[:_MAX_PARAM_VALUE_LEN] + "...(truncated)"
    return text


def _safe_params(kwargs: dict[str, Any] | None) -> dict[str, Any] | None:
    """Redact a tool's keyword arguments for safe logging.

    A top-level key matching :data:`_SENSITIVE_PARAM_RE` or listed in
    :data:`_FREEFORM_SECRET_PARAMS` is replaced with ``"***"`` wholesale; every
    other value is passed through :func:`_redact_value`, which recurses into
    nested dicts / lists / JSON strings so secret-named nested keys and inline
    secrets are redacted too. Returns ``None`` when there are no arguments. Never
    raises — logging must not affect the tool call.
    """
    if not kwargs:
        return None
    safe: dict[str, Any] = {}
    for key, value in kwargs.items():
        if _is_sensitive_key(key) or key in _FREEFORM_SECRET_PARAMS:
            safe[key] = "***"
            continue
        try:
            safe[key] = _redact_value(value)
        except Exception:  # noqa: BLE001 — logging never affects the tool call
            safe[key] = "<unserializable>"
    return safe


def build_payload(
    *,
    tool: str,
    success: bool,
    latency_ms: int,
    error: dict[str, str] | None = None,
    record_type: str = "tool",
    user: str | None = None,
    transport: str | None = None,
    awx_host: str | None = None,
    method: str | None = None,
    endpoint: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the JSON usage document for a single call.

    ``record_type`` (the ``type`` field) distinguishes an MCP ``tool`` call from
    an ``internal_api`` call the server makes on its own behalf (e.g. the one-time
    ``/api/v2/me/`` user lookup), so statistics can separate real tool usage from
    that overhead. It shares the ``type`` field name with the access and
    diagnostic log records so every awx-mcp log line can be split by ``type``.
    For an ``internal_api`` call ``method`` (the HTTP verb, e.g. ``GET``) and
    ``endpoint`` (the API path, e.g. ``/api/v2/me/``) are recorded as separate
    fields; ``tool`` then carries a short logical name (e.g. ``me``) so log
    consumers filtering by ``tool`` never see an HTTP verb + path. ``params`` (a
    tool call's redacted arguments) is included when present.
    ``user`` may be supplied directly to skip user resolution — required when
    recording an internal call from inside that resolution to avoid recursion,
    and by the proxy (which has no AWX access to resolve a username). ``transport``
    and ``awx_host`` may likewise be supplied directly; the proxy passes
    ``transport="proxy"`` and the central host because the env-derived defaults
    (``AWX_MCP_EFFECTIVE_TRANSPORT`` / ``ANSIBLE_BASE_URL``) do not apply there.
    """
    payload: dict[str, Any] = {
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "type": record_type,
        "user": user if user is not None else resolve_user_identifier(),
        "tool": tool,
        "trace_id": str(uuid.uuid4()),
        "server_version": _server_version(),
        "success": success,
        "latency_ms": latency_ms,
        "auth_mode": os.environ.get("AWX_MCP_AUTH_MODE", "static"),
        "transport": transport if transport is not None else _effective_transport(),
        "awx_host": awx_host if awx_host is not None else _awx_host(),
    }
    if method is not None:
        payload["method"] = method
    if endpoint is not None:
        payload["endpoint"] = endpoint
    if params is not None:
        payload["params"] = params
    if not success and error is not None:
        payload["error"] = error
    return payload


def _error_info(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": mask_secrets(str(exc))}


# --- File sink ----------------------------------------------------------------
_usage_logger: logging.Logger | None = None
_usage_logger_lock = threading.Lock()


def _is_enabled() -> bool:
    return bool(USAGE_LOG_FILE) or stdout_jsonl_enabled()


def _get_usage_logger() -> logging.Logger | None:
    """Return the JSON Lines logger, building it lazily on first use.

    Returns ``None`` when instrumentation is disabled. Sinks: the rotating file
    (when ``AWX_MCP_USAGE_LOG_FILE`` is set) and, in ``--serve`` mode, stdout
    (see :func:`stdout_jsonl_enabled`) — both raw JSON (formatter is
    ``%(message)s``). The logger never propagates, so nothing reaches the root
    logger's stderr handler.
    """
    global _usage_logger
    if not _is_enabled():
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
        raw = logging.Formatter("%(message)s")
        if USAGE_LOG_FILE:
            handler = make_timed_rotating_handler(USAGE_LOG_FILE)
            handler.setFormatter(raw)
            lg.addHandler(handler)
        if stdout_jsonl_enabled():
            stdout_handler = logging.StreamHandler(sys.stdout)
            stdout_handler.setFormatter(raw)
            lg.addHandler(stdout_handler)
        _usage_logger = lg
    return _usage_logger


def _record(
    tool_name: str,
    start_monotonic: float,
    success: bool,
    exc: BaseException | None,
    params: dict[str, Any] | None = None,
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
            params=_safe_params(params),
        )
        sink.info(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — instrumentation never affects tools
        logger.debug("usage recording failed: %s", type(exc).__name__)


def record_proxy_tool_call(
    tool_name: str,
    start_monotonic: float,
    success: bool,
    exc: BaseException | None,
    *,
    user: str,
    awx_host: str,
    params: dict[str, Any] | None = None,
) -> None:
    """Append a usage entry for a tool call relayed by the proxy.

    A dedicated recorder for proxy (``--remote``) mode. It builds the payload
    directly with ``user``/``transport``/``awx_host`` supplied, and never routes
    through :func:`_record` — ``_record`` omits ``user=``, which would call
    :func:`resolve_user_identifier` -> lazy ``client``/``server`` import ->
    ``ANSIBLE_BASE_URL`` validation, crashing the proxy (which has no BASE_URL).
    ``params`` are the relayed tool arguments; they are redacted before logging.
    Swallows all errors so logging never affects the relay.
    """
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
            user=user,
            transport="proxy",
            awx_host=awx_host,
            params=_safe_params(params),
        )
        sink.info(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — logging never affects the relay
        logger.debug("proxy usage recording failed: %s", type(exc).__name__)


def _record_internal_api(
    tool_name: str,
    user: str,
    success: bool,
    latency_ms: int,
    error: dict[str, str] | None,
    *,
    method: str | None = None,
    endpoint: str | None = None,
) -> None:
    """Append a usage entry for an internal AWX API call (``kind=internal_api``).

    ``user`` is passed explicitly and never re-resolved, so this is safe to call
    from within user resolution. ``method``/``endpoint`` record the HTTP verb and
    API path as separate fields (``tool_name`` is the short logical name). Swallows
    all errors.
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
            record_type="internal_api",
            user=user,
            method=method,
            endpoint=endpoint,
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
                _record(func.__name__, start, success, captured, params=kwargs)

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
            _record(func.__name__, start, success, captured, params=kwargs)

    return wrapper

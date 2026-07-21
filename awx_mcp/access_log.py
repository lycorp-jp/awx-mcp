# SPDX-License-Identifier: Apache-2.0

"""JSON access log for the central network server (``--serve``).

Opt-in via ``AWX_MCP_ACCESS_LOG_FILE``: when set, every inbound HTTP request is
appended to that file as one JSON object per line (JSON Lines), rotating at
midnight (UTC) like the usage log. This is the transport-level access log —
who connected, which HTTP endpoint, status, latency — complementing the usage
log, which records per-tool calls with AWX user attribution.

The middleware is a pure ASGI wrapper: it never modifies the request or
response and swallows all of its own errors, so access logging can never break
the MCP server. For streaming responses (sse / streamable-http) ``latency_ms``
covers the full stream lifetime, not just the response start.

stdio mode has no HTTP socket, so this module does not apply there. The proxy
(``--remote``) likewise runs no HTTP server.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .usage import make_timed_rotating_handler, stdout_jsonl_enabled

logger = logging.getLogger("ansible-mcp.access")

_access_logger: logging.Logger | None = None
_access_logger_lock = threading.Lock()


def _log_file() -> str | None:
    """The access-log path (read per call so tests can toggle the env)."""
    return os.environ.get("AWX_MCP_ACCESS_LOG_FILE") or None


def access_log_enabled() -> bool:
    """True when access records have at least one sink.

    Sinks: the rotating file (``AWX_MCP_ACCESS_LOG_FILE``) and, in ``--serve``
    mode, stdout (always on there — stdout carries no protocol on the network
    transports, so the JSON Lines are mirrored for log collectors).
    """
    return bool(_log_file()) or stdout_jsonl_enabled()


def _get_access_logger() -> logging.Logger | None:
    """Return the JSON Lines access logger, building it lazily on first use.

    Returns ``None`` when disabled. All sinks write raw JSON (formatter is
    ``%(message)s``); the logger never propagates, so nothing reaches the root
    logger's stderr handler.
    """
    global _access_logger
    if not access_log_enabled():
        return None
    if _access_logger is not None:
        return _access_logger
    with _access_logger_lock:
        if _access_logger is not None:
            return _access_logger
        lg = logging.getLogger("ansible-mcp.access.jsonl")
        # getLogger returns a process-global singleton; drop any handler left by
        # a previous configuration (e.g. a module reload in tests) before adding.
        lg.handlers.clear()
        lg.setLevel(logging.INFO)
        lg.propagate = False
        raw = logging.Formatter("%(message)s")
        path = _log_file()
        if path:
            handler = make_timed_rotating_handler(path)
            handler.setFormatter(raw)
            lg.addHandler(handler)
        if stdout_jsonl_enabled():
            stdout_handler = logging.StreamHandler(sys.stdout)
            stdout_handler.setFormatter(raw)
            lg.addHandler(stdout_handler)
        _access_logger = lg
    return _access_logger


def _reset_for_tests() -> None:
    """Drop the cached logger so tests can point the sink at a new file."""
    global _access_logger
    with _access_logger_lock:
        if _access_logger is not None:
            for handler in _access_logger.handlers:
                handler.close()
            _access_logger.handlers.clear()
        _access_logger = None


def _header(scope: dict[str, Any], name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1")
    return None


def _client_ip(scope: dict[str, Any]) -> str:
    """Best-effort client address: first X-Forwarded-For hop, else the peer."""
    forwarded = _header(scope, b"x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = scope.get("client")
    if client:
        return client[0]
    return "unknown"


def _record(scope: dict[str, Any], status: int | None, start_monotonic: float) -> None:
    """Append one access entry. Swallows all errors."""
    try:
        sink = _get_access_logger()
        if sink is None:
            return
        payload: dict[str, Any] = {
            "@timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "access",
            "client_ip": _client_ip(scope),
            "method": scope.get("method", "unknown"),
            "path": scope.get("path", "unknown"),
            "status": status,
            "latency_ms": int((time.monotonic() - start_monotonic) * 1000),
            "user_agent": _header(scope, b"user-agent"),
            # Whether the request carried a caller token (passthrough auth).
            # The token itself is never logged.
            "has_auth": bool(
                _header(scope, b"authorization") or _header(scope, b"x-awx-token")
            ),
        }
        sink.info(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — logging never affects requests
        logger.debug("access-log recording failed: %s", type(exc).__name__)


class AccessLogMiddleware:
    """Pure ASGI middleware appending one JSON object per HTTP request.

    ``status`` is taken from the ``http.response.start`` message; if the app
    crashes before sending one, the entry is written with ``status: null`` and
    the exception propagates unchanged.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        status: int | None = None

        async def send_wrapper(message: Any) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _record(scope, status, start)

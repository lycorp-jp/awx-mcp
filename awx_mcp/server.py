# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Server Configuration

FastMCP instance, environment configuration, and logging setup.
"""

import functools
import inspect
import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import urllib3
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .tls_config import resolve_ssl_verify
from .usage import instrument_tool, make_timed_rotating_handler

# AWX_MCP_TRANSPORT was removed: the running mode is now chosen by CLI flags
# (`awx-mcp` = local stdio; `awx-mcp --serve [--sse]` = central network server).
# Fail fast with a migration hint if the retired env var is still set.
if os.environ.get("AWX_MCP_TRANSPORT"):
    raise ValueError(
        "AWX_MCP_TRANSPORT has been removed. Run the central server with "
        "'awx-mcp --serve' (add '--sse' for the sse transport); plain 'awx-mcp' "
        "runs the local stdio server."
    )

# Authentication mode — an internal channel set by the CLI in __init__.main(),
# NOT a user-facing setting:
#   static      -> this process authenticates to AWX with its own env
#                  credentials (ANSIBLE_TOKEN / USERNAME+PASSWORD). Local mode.
#   passthrough -> the process holds no credentials; each request carries the
#                  caller's AWX token in the Authorization / X-AWX-Token header.
#                  Central `--serve` mode.
AUTH_MODE = os.environ.get("AWX_MCP_AUTH_MODE", "static").lower()
if AUTH_MODE not in ("static", "passthrough"):
    raise ValueError(
        f"Invalid AWX_MCP_AUTH_MODE={AUTH_MODE!r}. Choose 'static' or 'passthrough'."
    )

MCP_HOST = os.environ.get("AWX_MCP_HOST", "127.0.0.1")
try:
    MCP_PORT = int(os.environ.get("AWX_MCP_PORT", "8000"))
except ValueError as exc:
    raise ValueError("AWX_MCP_PORT must be an integer.") from exc

# Initialize FastMCP server (host/port apply to the sse and streamable-http
# transports; ignored for stdio)
mcp = FastMCP("ansible", host=MCP_HOST, port=MCP_PORT)

# Configuration
ANSIBLE_BASE_URL = os.environ.get("ANSIBLE_BASE_URL")
ANSIBLE_USERNAME = os.environ.get("ANSIBLE_USERNAME")
ANSIBLE_PASSWORD = os.environ.get("ANSIBLE_PASSWORD")
ANSIBLE_TOKEN = os.environ.get("ANSIBLE_TOKEN")
# TLS: verification is ON by default (secure). The resolved requests `verify=`
# value is computed into ANSIBLE_SSL_VERIFY below via tls_config.resolve_ssl_verify
# (shared with the proxy), once the logger exists so the disable warning can fire.
ENABLE_CREDENTIAL_MANAGEMENT = os.environ.get(
    "AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT", "false"
).lower() in ("true", "1", "yes")
READ_ONLY = os.environ.get("AWX_MCP_READ_ONLY", "false").lower() in (
    "true",
    "1",
    "yes",
)

# Validate required environment variables
if not ANSIBLE_BASE_URL:
    raise ValueError(
        "ANSIBLE_BASE_URL environment variable is required. "
        "Example: ANSIBLE_BASE_URL=https://awx.example.com/"
    )
# In static (local) mode this process must have its own AWX credentials. In
# passthrough (--serve) mode it must NOT — each request carries the caller's
# token — so credentials are not required here (any that are set are ignored,
# warned about once the logger exists below).
if (
    AUTH_MODE == "static"
    and not ANSIBLE_TOKEN
    and not (ANSIBLE_USERNAME and ANSIBLE_PASSWORD)
):
    raise ValueError(
        "Authentication is required. Set either ANSIBLE_TOKEN or both "
        "ANSIBLE_USERNAME and ANSIBLE_PASSWORD environment variables."
    )

# Logging setup
_PLAIN_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

logging.basicConfig(
    level=os.environ.get("ANSIBLE_LOG_LEVEL", "INFO").upper(),
    format=_PLAIN_LOG_FORMAT,
)
logger = logging.getLogger("ansible-mcp")

# In --serve mode every log record is JSON Lines: the stderr diagnostics switch
# to the JSON formatter below (uvicorn's loggers route here too — main() passes
# log_config=None), matching the usage/access JSONL sinks. Local stdio mode
# keeps the human-readable plain format.
_SERVE_MODE = os.environ.get("AWX_MCP_EFFECTIVE_TRANSPORT") in (
    "sse",
    "streamable-http",
)

# --- Base URL scheme: default to HTTPS ----------------------------------------
# awx-mcp talks to AWX over HTTPS by default. A bare host (no scheme) is upgraded
# to https://; an explicit http:// URL is honored but warned about, since it
# sends the AWX token and all traffic in clear text.
_parsed_base = urlparse(ANSIBLE_BASE_URL)
if not _parsed_base.scheme:
    ANSIBLE_BASE_URL = "https://" + ANSIBLE_BASE_URL
elif _parsed_base.scheme == "http":
    logger.warning(
        "ANSIBLE_BASE_URL uses http:// — the AWX token and all traffic are sent "
        "unencrypted. Use https:// in production."
    )

# --- TLS verification resolution ----------------------------------------------
# ANSIBLE_SSL_VERIFY becomes the value passed to requests' `verify=` (False /
# custom CA bundle path / True). Resolved via the shared, server-free helper so
# the proxy computes it identically without importing this module.
ANSIBLE_SSL_VERIFY: bool | str = resolve_ssl_verify(logger, ANSIBLE_BASE_URL)

# In passthrough mode any server-side AWX credentials are ignored (each request
# carries the caller's token); warn once so a misconfiguration is visible.
if AUTH_MODE == "passthrough" and (
    ANSIBLE_TOKEN or ANSIBLE_USERNAME or ANSIBLE_PASSWORD
):
    logger.warning(
        "AUTH_MODE=passthrough (--serve): server-side ANSIBLE_TOKEN / "
        "ANSIBLE_USERNAME / ANSIBLE_PASSWORD are ignored. Every AWX call uses "
        "the per-request caller token."
    )


class _JsonLogFormatter(logging.Formatter):
    """Render each diagnostic log record as a single JSON object (one per line)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False)


# Optional diagnostic log file (opt-in). When AWX_MCP_SERVER_LOG_FILE is set, the
# existing logging output is ALSO written to that file (stderr behaviour is
# unchanged). Format is selectable via AWX_MCP_SERVER_LOG_FORMAT (plain|json).
# stdout is never touched — it is reserved for the stdio MCP protocol.
SERVER_LOG_FILE = os.environ.get("AWX_MCP_SERVER_LOG_FILE")
SERVER_LOG_FORMAT = os.environ.get("AWX_MCP_SERVER_LOG_FORMAT", "plain").lower()

if SERVER_LOG_FILE:
    _server_log_handler = make_timed_rotating_handler(SERVER_LOG_FILE)
    if SERVER_LOG_FORMAT == "json" or _SERVE_MODE:
        _server_log_handler.setFormatter(_JsonLogFormatter())
    else:
        _server_log_handler.setFormatter(logging.Formatter(_PLAIN_LOG_FORMAT))
    logging.getLogger().addHandler(_server_log_handler)

if _SERVE_MODE:
    for _handler in logging.getLogger().handlers:
        _handler.setFormatter(_JsonLogFormatter())

# Suppress InsecureRequestWarning when SSL verification is disabled
if not ANSIBLE_SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Inbound TLS (sse / streamable-http server) -------------------------------
# In-process TLS for the network transports. stdio has no network socket, so TLS
# does not apply there. Off by default; when AWX_MCP_TLS_ENABLE is true the
# server serves HTTPS and a certificate + private key are required (validated at
# startup by resolve_tls_kwargs). This is inbound (client -> awx-mcp) TLS and is
# unrelated to ANSIBLE_SSL_VERIFY, which secures the outbound connection to AWX.
TLS_ENABLE = os.environ.get("AWX_MCP_TLS_ENABLE", "false").lower() in (
    "true",
    "1",
    "yes",
)
TLS_CERT = os.environ.get("AWX_MCP_TLS_CERT") or None
TLS_KEY = os.environ.get("AWX_MCP_TLS_KEY") or None
TLS_KEY_PASSWORD = os.environ.get("AWX_MCP_TLS_KEY_PASSWORD") or None


def resolve_tls_kwargs(transport: str) -> dict[str, str] | None:
    """Resolve uvicorn ``ssl_*`` kwargs for inbound TLS, or ``None`` when off.

    Returns ``None`` when TLS is disabled or when the transport is ``stdio``
    (which has no socket — a warning is logged if TLS was requested anyway).
    Raises ``ValueError`` when TLS is enabled for a network transport but the
    certificate/key are missing or point to non-existent files, so a
    misconfigured server fails fast at startup instead of silently serving
    plain HTTP.
    """
    if not TLS_ENABLE:
        return None
    if transport == "stdio":
        logger.warning(
            "AWX_MCP_TLS_ENABLE is set but transport=stdio has no network "
            "socket — TLS settings are ignored for stdio."
        )
        return None

    cert, key = TLS_CERT, TLS_KEY
    missing = [
        name
        for name, value in (("AWX_MCP_TLS_CERT", cert), ("AWX_MCP_TLS_KEY", key))
        if not value
    ]
    if missing:
        raise ValueError(
            "AWX_MCP_TLS_ENABLE=true requires " + " and ".join(missing) + " to be set."
        )
    # After the missing check both are set; narrow str | None -> str for mypy.
    assert cert is not None and key is not None
    for name, path in (("AWX_MCP_TLS_CERT", cert), ("AWX_MCP_TLS_KEY", key)):
        if not os.path.isfile(path):
            raise ValueError(f"{name} points to a file that does not exist: {path}")

    kwargs: dict[str, str] = {"ssl_certfile": cert, "ssl_keyfile": key}
    if TLS_KEY_PASSWORD:
        kwargs["ssl_keyfile_password"] = TLS_KEY_PASSWORD
    return kwargs


def get_request_header(name: str) -> str | None:
    """Return an inbound request header (case-insensitive), or ``None``.

    Reads the current request's Starlette headers via the FastMCP request
    context. Returns ``None`` outside a request (stdio, or before/after a
    request) and never raises — the ``request_context`` property raises
    ``ValueError`` when there is no active request, which ``getattr`` would not
    swallow, so the access is wrapped explicitly.
    """
    try:
        req = mcp.get_context().request_context.request
    except (LookupError, ValueError):
        return None
    if req is None:
        return None
    try:
        return req.headers.get(name)
    except Exception:  # noqa: BLE001 — header access must never fail a tool
        return None


def _request_is_read_only() -> bool:
    """True when the current passthrough request opted into read-only mode.

    Only meaningful in passthrough mode: a caller may send
    ``X-AWX-Read-Only: true`` to restrict their own session to read tools. This
    can only tighten access (never loosen it) and is advisory self-restriction,
    not a security boundary — the real boundary is the caller's AWX token scope.
    """
    if AUTH_MODE != "passthrough":
        return False
    return (get_request_header("x-awx-read-only") or "").lower() in (
        "true",
        "1",
        "yes",
    )


def _read_only_gated(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a write tool so a read-only caller's invocation is rejected.

    In static/local mode this is a no-op (the check short-circuits on
    ``AUTH_MODE``). In passthrough mode a request carrying a truthy
    ``X-AWX-Read-Only`` header is refused with a clear error before the tool
    body runs. Supports sync and async tools.
    """
    message = (
        f"'{func.__name__}' is a write operation and read-only mode is enabled "
        "for this session (X-AWX-Read-Only)."
    )

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if _request_is_read_only():
                raise PermissionError(message)
            return await func(*args, **kwargs)

        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if _request_is_read_only():
            raise PermissionError(message)
        return func(*args, **kwargs)

    return wrapper


def read_tool(func):
    """Register a pure read/GET MCP tool.

    Always registered (read tools are exposed even in read-only mode) and
    annotated with ``readOnlyHint=True``.
    """
    return mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))(
        instrument_tool(func)
    )


def write_tool(*, destructive: bool = False, idempotent: bool = False):
    """Register a write MCP tool unless AWX_MCP_READ_ONLY is enabled.

    When server-global ``READ_ONLY`` is true the tool is left unregistered
    (returned as-is). Otherwise it is registered with annotations describing
    whether the write is destructive and/or idempotent, and wrapped with a
    call-time gate so a passthrough caller who sent ``X-AWX-Read-Only`` is
    refused (the tool stays visible in tools/list but errors on call).
    """

    def deco(func):
        if READ_ONLY:
            return func
        return mcp.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=destructive,
                idempotentHint=idempotent,
            )
        )(instrument_tool(_read_only_gated(func)))

    return deco


def maybe_credential_management_tool(func):
    """Register an MCP tool only when AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true.

    Gates the four credential/user write tools (``create_credential``,
    ``update_credential``, ``create_user``, ``update_user``) that collect
    sensitive data via Form-mode elicitation. By default the flag is unset and
    these tools are not registered, so the shipped server exposes no tool that
    handles sensitive data.

    Sensitive credential/user writes are still writes, so they are also gated by
    ``READ_ONLY``: when read-only mode is enabled they are not registered even
    if credential management is enabled.
    """
    if ENABLE_CREDENTIAL_MANAGEMENT and not READ_ONLY:
        return mcp.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
            )
        )(instrument_tool(_read_only_gated(func)))
    return func


if ENABLE_CREDENTIAL_MANAGEMENT:
    logger.warning(
        "AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true detected. "
        "Credential/user write tools (create_credential, update_credential, "
        "create_user, update_user) are now exposed and use Form-mode elicitation, "
        "which is not spec-compliant for sensitive data per the MCP specification. "
        "Use only in trusted, isolated environments. "
        "See README.md#security for the threat model."
    )

if READ_ONLY:
    logger.warning(
        "AWX_MCP_READ_ONLY=true detected. All write/destructive tools are "
        "unregistered; only read tools are exposed."
    )


def warn_if_exposed(host: str, tls_enabled: bool) -> None:
    """Warn when the network server binds a non-local address without TLS.

    Called from ``__init__.main()`` for ``--serve`` with the effective (post-CLI)
    host, so the warning reflects what the process actually binds. In passthrough
    mode the per-request AWX token travels in a header, so plaintext transport
    exposes credentials — hence the emphasis on TLS.
    """
    if host in ("127.0.0.1", "localhost", "::1"):
        return
    if not tls_enabled:
        logger.warning(
            "Central server bound to %s without in-process TLS. In passthrough "
            "mode the caller's AWX token travels in a request header — enable "
            "AWX_MCP_TLS_ENABLE (or front it with an authenticating TLS reverse "
            "proxy) so tokens are not sent in clear text.",
            host,
        )
    else:
        logger.info("Central server bound to %s (in-process TLS enabled).", host)

# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Server Configuration

FastMCP instance, environment configuration, and logging setup.
"""

import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import urllib3
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .usage import instrument_tool, make_timed_rotating_handler

# Transport configuration
TRANSPORT = os.environ.get("AWX_MCP_TRANSPORT", "stdio").lower()
VALID_TRANSPORTS = ("stdio", "sse", "streamable-http")
if TRANSPORT not in VALID_TRANSPORTS:
    raise ValueError(
        f"Invalid AWX_MCP_TRANSPORT={TRANSPORT!r}. "
        f"Choose one of: {', '.join(VALID_TRANSPORTS)}."
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
# TLS: verification is ON by default (secure). Set ANSIBLE_SSL_VERIFY=false to
# turn it off (a warning is logged). ANSIBLE_CA_BUNDLE may point to a custom CA
# bundle / self-signed certificate to trust instead of disabling verification.
# The resolved requests `verify=` value is assigned to ANSIBLE_SSL_VERIFY below,
# once the logger exists (so the disable/plain-http warnings can be emitted).
_SSL_VERIFY_ENABLED = os.environ.get("ANSIBLE_SSL_VERIFY", "true").lower() in (
    "true",
    "1",
    "yes",
)
ANSIBLE_CA_BUNDLE = os.environ.get("ANSIBLE_CA_BUNDLE") or None
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
if not ANSIBLE_TOKEN and not (ANSIBLE_USERNAME and ANSIBLE_PASSWORD):
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
# ANSIBLE_SSL_VERIFY becomes the value passed to requests' `verify=`:
#   False   -> verification disabled (insecure; warning logged)
#   <path>  -> custom CA bundle (from ANSIBLE_CA_BUNDLE)
#   True    -> system trust store (default)
if not _SSL_VERIFY_ENABLED:
    ANSIBLE_SSL_VERIFY: bool | str = False
    logger.warning(
        "TLS certificate verification is DISABLED (ANSIBLE_SSL_VERIFY=false). "
        "Connections to %s are vulnerable to man-in-the-middle attacks. Enable "
        "verification (and set ANSIBLE_CA_BUNDLE for a private CA) in production.",
        ANSIBLE_BASE_URL,
    )
elif ANSIBLE_CA_BUNDLE:
    if not os.path.isfile(ANSIBLE_CA_BUNDLE):
        raise ValueError(
            f"ANSIBLE_CA_BUNDLE points to a file that does not exist: "
            f"{ANSIBLE_CA_BUNDLE}"
        )
    ANSIBLE_SSL_VERIFY = ANSIBLE_CA_BUNDLE
else:
    ANSIBLE_SSL_VERIFY = True


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
    if SERVER_LOG_FORMAT == "json":
        _server_log_handler.setFormatter(_JsonLogFormatter())
    else:
        _server_log_handler.setFormatter(logging.Formatter(_PLAIN_LOG_FORMAT))
    logging.getLogger().addHandler(_server_log_handler)

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

    missing = [
        name
        for name, value in (
            ("AWX_MCP_TLS_CERT", TLS_CERT),
            ("AWX_MCP_TLS_KEY", TLS_KEY),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "AWX_MCP_TLS_ENABLE=true requires " + " and ".join(missing) + " to be set."
        )
    for name, path in (("AWX_MCP_TLS_CERT", TLS_CERT), ("AWX_MCP_TLS_KEY", TLS_KEY)):
        if not os.path.isfile(path):
            raise ValueError(f"{name} points to a file that does not exist: {path}")

    kwargs: dict[str, str] = {"ssl_certfile": TLS_CERT, "ssl_keyfile": TLS_KEY}
    if TLS_KEY_PASSWORD:
        kwargs["ssl_keyfile_password"] = TLS_KEY_PASSWORD
    return kwargs


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

    When ``READ_ONLY`` is true the tool is left unregistered (returned as-is).
    Otherwise it is registered with annotations describing whether the write is
    destructive and/or idempotent.
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
        )(instrument_tool(func))

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
        )(instrument_tool(func))
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

if TRANSPORT != "stdio" and MCP_HOST not in ("127.0.0.1", "localhost", "::1"):
    logger.warning(
        "AWX_MCP_TRANSPORT=%s is bound to %s:%s and reachable over the network. "
        "The server has no built-in authentication — place it behind an "
        "authenticating TLS reverse proxy and restrict access.",
        TRANSPORT,
        MCP_HOST,
        MCP_PORT,
    )

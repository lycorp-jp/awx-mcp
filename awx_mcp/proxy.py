# SPDX-License-Identifier: Apache-2.0

"""Client proxy mode (``awx-mcp --remote <URL>``).

Runs no AWX-facing server. Instead it exposes a local stdio MCP server to the
user's MCP client and relays every request to a central awx-mcp over
streamable-http, injecting the user's own AWX token as an ``Authorization:
Bearer`` header. This gives the same UX as local mode (an ``awx-mcp`` command
plus an ``ANSIBLE_TOKEN`` env var) while all AWX access is attributed and logged
centrally.

This module deliberately never imports ``awx_mcp.server``: proxy users do not
set ``ANSIBLE_BASE_URL`` (the central server manages AWX access), and importing
``server`` would trigger its startup validation and crash. Outbound TLS settings
come from the shared, server-free :mod:`awx_mcp.tls_config`.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from urllib.parse import urlparse

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent

from .tls_config import resolve_ssl_verify
from .usage import record_proxy_tool_call

logger = logging.getLogger("awx-mcp.proxy")

# Generous read timeout: a relayed tool call blocks until the central server
# finishes its AWX request (which itself allows a long read), so the proxy must
# not time out first.
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


def _truthy(value: str | None) -> bool:
    return (value or "").lower() in ("true", "1", "yes")


async def _run_proxy_async(url: str, token: str, ssl_verify: bool | str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    # A user may restrict their own session to read-only; the central server
    # enforces it per request. This can only tighten access.
    if _truthy(os.environ.get("AWX_MCP_READ_ONLY")):
        headers["X-AWX-Read-Only"] = "true"

    central_host = urlparse(url).hostname or "unknown"
    usage_user = os.environ.get("AWX_MCP_USAGE_USER") or "local"

    async with httpx.AsyncClient(
        headers=headers, verify=ssl_verify, timeout=_HTTP_TIMEOUT
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(read_stream, write_stream) as upstream:
                await upstream.initialize()
                logger.info("Connected to central awx-mcp at %s", url)

                proxy = Server("awx-mcp-proxy")

                @proxy.list_tools()
                async def _list_tools():
                    # A transport failure here raises, which the lowlevel server
                    # converts to a JSON-RPC error (ListToolsResult has no
                    # isError field).
                    result = await upstream.list_tools()
                    return result.tools

                @proxy.call_tool()
                async def _call_tool(name: str, arguments: dict):
                    start = time.monotonic()
                    success = True
                    captured: BaseException | None = None
                    try:
                        result = await upstream.call_tool(name, arguments)
                        success = not bool(result.isError)
                        return result
                    except Exception as exc:  # noqa: BLE001 — degrade, don't crash
                        success = False
                        captured = exc
                        return CallToolResult(
                            content=[
                                TextContent(
                                    type="text",
                                    text=f"central awx-mcp unreachable: {exc}",
                                )
                            ],
                            isError=True,
                        )
                    finally:
                        record_proxy_tool_call(
                            name,
                            start,
                            success,
                            captured,
                            user=usage_user,
                            awx_host=central_host,
                        )

                init_opts = proxy.create_initialization_options()
                async with stdio_server() as (stdio_read, stdio_write):
                    await proxy.run(stdio_read, stdio_write, init_opts)


def run_proxy(url: str) -> None:
    """Run the stdio<->central relay until stdin closes.

    Reads the caller's token from ``ANSIBLE_TOKEN`` (PAT only — username/password
    cannot be forwarded as a Bearer header) and the outbound TLS-verify setting
    from ``ANSIBLE_SSL_VERIFY`` / ``ANSIBLE_CA_BUNDLE`` (verifying the connection
    to the central awx-mcp, not to AWX). Exits non-zero with a clear message on
    a fatal startup/connection error.
    """
    logging.basicConfig(
        level=os.environ.get("ANSIBLE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    token = os.environ.get("ANSIBLE_TOKEN")
    if not token:
        sys.exit(
            "ANSIBLE_TOKEN is required for --remote (proxy) mode. Proxy mode "
            "forwards a personal access token as a Bearer header; "
            "ANSIBLE_USERNAME/PASSWORD are not supported."
        )

    ssl_verify = resolve_ssl_verify(logger, url)

    try:
        anyio.run(_run_proxy_async, url, token, ssl_verify)
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001 — surface a clean fatal error
        logger.error("proxy to %s failed: %s", url, exc)
        sys.exit(f"awx-mcp --remote failed: {exc}")

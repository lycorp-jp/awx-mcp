# SPDX-License-Identifier: Apache-2.0

"""AWX MCP Server - MCP server for Ansible Tower/AWX."""

import argparse

from . import tools  # noqa: F401
from .server import TRANSPORT, VALID_TRANSPORTS, logger, mcp, resolve_tls_kwargs


def main():
    """Entry point for the awx-mcp command.

    Transport defaults come from the AWX_MCP_TRANSPORT / AWX_MCP_HOST /
    AWX_MCP_PORT environment variables and can be overridden by CLI flags.
    """
    parser = argparse.ArgumentParser(
        prog="awx-mcp",
        description="MCP server for Ansible Tower/AWX.",
    )
    parser.add_argument(
        "--transport",
        choices=VALID_TRANSPORTS,
        default=TRANSPORT,
        help="Transport protocol (default: %(default)s, env: AWX_MCP_TRANSPORT).",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host for sse/streamable-http (env: AWX_MCP_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port for sse/streamable-http (env: AWX_MCP_PORT, default 8000).",
    )
    args = parser.parse_args()

    if args.host is not None:
        mcp.settings.host = args.host
    if args.port is not None:
        mcp.settings.port = args.port

    if args.transport == "stdio":
        logger.info("Starting awx-mcp (transport=stdio)")
        mcp.run(transport="stdio")
        return

    # Network transports (sse / streamable-http): optionally serve over TLS.
    # resolve_tls_kwargs validates the cert/key and raises on misconfiguration.
    tls_kwargs = resolve_tls_kwargs(args.transport)
    logger.info(
        "Starting awx-mcp (transport=%s, %s) on %s:%s",
        args.transport,
        "https" if tls_kwargs else "http",
        mcp.settings.host,
        mcp.settings.port,
    )

    if tls_kwargs:
        # FastMCP.run() cannot pass TLS options to uvicorn, so drive uvicorn
        # directly on the transport's ASGI app with the resolved ssl_* kwargs.
        import uvicorn

        app = mcp.sse_app() if args.transport == "sse" else mcp.streamable_http_app()
        uvicorn.run(
            app,
            host=mcp.settings.host,
            port=mcp.settings.port,
            **tls_kwargs,
        )
    else:
        mcp.run(transport=args.transport)

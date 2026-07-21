# SPDX-License-Identifier: Apache-2.0

"""AWX MCP Server - MCP server for Ansible Tower/AWX."""

import argparse
import os
import sys


def main():
    """Entry point for the awx-mcp command.

    Three mutually exclusive running modes, selected by CLI flags:

    * (no flags)         local stdio server using this process's own AWX
                         credentials (``ANSIBLE_TOKEN`` / USERNAME+PASSWORD).
    * ``--remote <URL>`` client proxy: no local server; relays stdio to a
                         central awx-mcp, injecting the caller's ``ANSIBLE_TOKEN``
                         as an ``Authorization: Bearer`` header.
    * ``--serve``        central multi-user server (streamable-http, or sse with
                         ``--sse``) that authenticates each request with the
                         caller's own token (passthrough).

    Heavy imports (``server``/``tools``/``proxy``) are deferred into the branch
    that needs them: proxy mode must not import ``server`` (which requires
    ``ANSIBLE_BASE_URL``, deliberately absent for proxy users).
    """
    parser = argparse.ArgumentParser(
        prog="awx-mcp",
        description="MCP server for Ansible Tower/AWX.",
    )
    parser.add_argument(
        "--remote",
        metavar="URL",
        default=None,
        help=(
            "Client proxy mode: connect to a central awx-mcp at URL instead of "
            "running a local server (env: AWX_MCP_REMOTE_URL)."
        ),
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run the central multi-user server (streamable-http; --sse for sse).",
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="With --serve, serve the sse transport instead of streamable-http.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host for --serve (env: AWX_MCP_HOST, default 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port for --serve (env: AWX_MCP_PORT, default 8000).",
    )
    args = parser.parse_args()

    remote_url = args.remote or os.environ.get("AWX_MCP_REMOTE_URL")

    # --- Mutual exclusivity -------------------------------------------------
    if args.remote and args.serve:
        parser.error("--remote and --serve are mutually exclusive.")
    if remote_url and (args.serve or args.host or args.port is not None or args.sse):
        parser.error(
            "--remote runs no server; it cannot be combined with "
            "--serve/--host/--port/--sse."
        )
    if args.sse and not args.serve:
        parser.error("--sse is only valid together with --serve.")

    # --- Proxy mode ---------------------------------------------------------
    if remote_url:
        # Import only the proxy; never touch server (no ANSIBLE_BASE_URL here).
        from .proxy import run_proxy

        run_proxy(remote_url)
        return

    # --- Server modes: set the internal channels BEFORE importing server ----
    if args.serve:
        os.environ["AWX_MCP_AUTH_MODE"] = "passthrough"
        transport = "sse" if args.sse else "streamable-http"
    else:
        # Guard: a stray AWX_MCP_AUTH_MODE env cannot turn on passthrough
        # without --serve. Modes are flag-decided.
        os.environ["AWX_MCP_AUTH_MODE"] = "static"
        transport = "stdio"
    os.environ["AWX_MCP_EFFECTIVE_TRANSPORT"] = transport

    from . import tools  # noqa: F401 — registers all tools on import
    from .access_log import AccessLogMiddleware
    from .server import logger, mcp, resolve_tls_kwargs, warn_if_exposed

    if transport == "stdio":
        logger.info("Starting awx-mcp (local stdio server)")
        mcp.run(transport="stdio")
        return

    # Central server (--serve). Apply CLI host/port overrides, then optionally
    # serve over TLS. resolve_tls_kwargs validates the cert/key and raises on
    # misconfiguration.
    if args.host is not None:
        mcp.settings.host = args.host
    if args.port is not None:
        mcp.settings.port = args.port

    tls_kwargs = resolve_tls_kwargs(transport)
    warn_if_exposed(mcp.settings.host, tls_enabled=bool(tls_kwargs))
    logger.info(
        "Starting awx-mcp --serve (transport=%s, %s) on %s:%s",
        transport,
        "https" if tls_kwargs else "http",
        mcp.settings.host,
        mcp.settings.port,
    )

    # Drive uvicorn directly (FastMCP.run() cannot pass TLS options or wrap the
    # app in middleware). log_config=None routes uvicorn's own loggers through
    # the root logging config (JSON in --serve, see server.py); access_log=False
    # drops uvicorn's plain-text access lines — the JSON access log
    # (AccessLogMiddleware) is the access record.
    import uvicorn

    app = mcp.sse_app() if transport == "sse" else mcp.streamable_http_app()
    app = AccessLogMiddleware(app)
    uvicorn.run(
        app,
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_config=None,
        access_log=False,
        **(tls_kwargs or {}),
    )


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: Apache-2.0

"""Non-mocked, end-to-end passthrough integration tests.

A real ``awx-mcp --serve`` subprocess is driven by a real MCP SDK client over a
real localhost socket, and a fake AWX (a threaded HTTP server) captures the
``Authorization`` header the server forwards. This is the only layer that proves
the SDK actually delivers the request header to the tool code path — the unit
tests monkeypatch that boundary, so they cannot catch an SDK contract change.

Covers both network transports the ``--serve`` mode exposes: streamable-http
(default) and sse (``--serve --sse``), plus the full stdio proxy relay chain.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import socket
import subprocess
import threading
import time

import anyio
import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with contextlib.suppress(OSError), socket.create_connection((HOST, port), 1):
            return
        time.sleep(0.2)
    raise TimeoutError(f"nothing listening on {HOST}:{port} within {timeout}s")


# --- fake AWX ---------------------------------------------------------------


class _Captured:
    def __init__(self):
        self.auth_headers: list[tuple[str, str | None]] = []


@pytest.fixture(scope="module")
def fake_awx():
    captured = _Captured()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            captured.auth_headers.append((self.path, self.headers.get("Authorization")))
            body = json.dumps(
                {"version": "9.9.9", "ping": "pong", "active_node": "node0"}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence
            pass

    port = _free_port()
    server = http.server.ThreadingHTTPServer((HOST, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{HOST}:{port}", captured
    finally:
        server.shutdown()


def _serve(transport_args: list[str], awx_url: str) -> subprocess.Popen:
    port = _free_port()
    env = os.environ.copy()
    env["ANSIBLE_BASE_URL"] = awx_url
    env["ANSIBLE_SSL_VERIFY"] = "false"
    env.pop("ANSIBLE_TOKEN", None)  # passthrough server needs no credentials
    env.pop("AWX_MCP_USAGE_LOG_FILE", None)
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "--directory",
            REPO,
            "awx-mcp",
            "--serve",
            "--host",
            HOST,
            "--port",
            str(port),
            *transport_args,
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_port(port)
    return proc, port


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(5)


# --- drivers ----------------------------------------------------------------


async def _drive_streamable(url: str, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(headers=headers) as hc:
        async with streamable_http_client(url, http_client=hc) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await s.call_tool("get_ansible_version", {})


async def _drive_sse(url: str, token: str):
    async with sse_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await s.call_tool("get_ansible_version", {})


# --- tests ------------------------------------------------------------------


def test_streamable_http_forwards_caller_token(fake_awx):
    awx_url, captured = fake_awx
    captured.auth_headers.clear()
    proc, port = _serve([], awx_url)
    try:
        result = anyio.run(
            _drive_streamable, f"http://{HOST}:{port}/mcp", "CALLER-TOKEN-A"
        )
    finally:
        _stop(proc)

    assert not result.isError, result.content
    pings = [a for (p, a) in captured.auth_headers if p.startswith("/api/v2/ping")]
    assert pings, "fake AWX never received the ping"
    assert all(a == "Bearer CALLER-TOKEN-A" for a in pings), captured.auth_headers


def test_streamable_http_missing_token_is_rejected(fake_awx):
    awx_url, captured = fake_awx
    proc, port = _serve([], awx_url)
    try:
        result = anyio.run(_drive_streamable, f"http://{HOST}:{port}/mcp", None)
    finally:
        _stop(proc)

    assert result.isError
    text = getattr(result.content[0], "text", "") if result.content else ""
    assert "token is required" in text.lower() or "passthrough" in text.lower()


def test_sse_forwards_caller_token(fake_awx):
    awx_url, captured = fake_awx
    captured.auth_headers.clear()
    proc, port = _serve(["--sse"], awx_url)
    try:
        result = anyio.run(_drive_sse, f"http://{HOST}:{port}/sse", "CALLER-TOKEN-SSE")
    finally:
        _stop(proc)

    assert not result.isError, result.content
    pings = [a for (p, a) in captured.auth_headers if p.startswith("/api/v2/ping")]
    assert pings and all(a == "Bearer CALLER-TOKEN-SSE" for a in pings)


def test_proxy_relay_chain_forwards_token(fake_awx):
    """Full chain: stdio proxy -> streamable-http central -> fake AWX."""
    awx_url, captured = fake_awx
    captured.auth_headers.clear()
    central, port = _serve([], awx_url)
    try:
        central_url = f"http://{HOST}:{port}/mcp"
        env = os.environ.copy()
        env["ANSIBLE_TOKEN"] = "PROXY-USER-TOKEN"
        env["AWX_MCP_REMOTE_URL"] = central_url
        env.pop("ANSIBLE_BASE_URL", None)  # proxy needs no BASE_URL
        params = StdioServerParameters(
            command="uv",
            args=["run", "--directory", REPO, "awx-mcp", "--remote", central_url],
            env=env,
        )

        async def _drive():
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    tools = await s.list_tools()
                    result = await s.call_tool("get_ansible_version", {})
                    return len(tools.tools), result

        count, result = anyio.run(_drive)
    finally:
        _stop(central)

    assert count > 0
    assert not result.isError, result.content
    pings = [a for (p, a) in captured.auth_headers if p.startswith("/api/v2/ping")]
    assert pings and all(a == "Bearer PROXY-USER-TOKEN" for a in pings)

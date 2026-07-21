# SPDX-License-Identifier: Apache-2.0

"""Smoke-test the AWX MCP server across its running modes.

Modes:
  local   local stdio server (static auth from env)
  serve   central server (streamable-http, passthrough auth via a caller token)
  sse     central server (sse transport, passthrough)
  remote  client proxy (stdio) relaying to a central serve instance

Each mode starts the server(s), connects as an MCP client, lists tools, and
calls the read-only ``get_ansible_version`` tool to verify the round trip. In
the passthrough modes the caller's token is sent as an ``Authorization: Bearer``
header (streamable-http/sse) or forwarded by the proxy from ``ANSIBLE_TOKEN``.

Connection settings are read from the environment — the token is NEVER written
to this file. Set ANSIBLE_BASE_URL and ANSIBLE_TOKEN (or ANSIBLE_USERNAME /
ANSIBLE_PASSWORD) before running.

Usage:
    ANSIBLE_BASE_URL=https://awx.example.com/ \\
    ANSIBLE_TOKEN=xxxxx \\
    ANSIBLE_SSL_VERIFY=false \\
    uv run python scripts/mcp_smoke_test.py [local|serve|sse|remote|all]

Exit code 0 = all selected modes passed, 1 = at least one failed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import time

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = "127.0.0.1"
PROBE_TOOL = "get_ansible_version"


def _require_env() -> None:
    if not os.environ.get("ANSIBLE_BASE_URL"):
        sys.exit("ANSIBLE_BASE_URL is required (and ANSIBLE_TOKEN or USER/PASS).")
    has_auth = os.environ.get("ANSIBLE_TOKEN") or (
        os.environ.get("ANSIBLE_USERNAME") and os.environ.get("ANSIBLE_PASSWORD")
    )
    if not has_auth:
        sys.exit("Authentication required: set ANSIBLE_TOKEN or USERNAME+PASSWORD.")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with contextlib.suppress(OSError), socket.create_connection((HOST, port), 1):
            return
        time.sleep(0.3)
    raise TimeoutError(f"server did not open {HOST}:{port} within {timeout}s")


async def _drive(read, write) -> tuple[int, str]:
    """Run an MCP session: initialize, list tools, call the probe tool."""
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool(PROBE_TOOL, {})
        text = ""
        if result.content:
            text = getattr(result.content[0], "text", "") or ""
        return len(tools.tools), text.strip()


def _server_env() -> dict[str, str]:
    return os.environ.copy()


async def test_local() -> tuple[int, str]:
    params = StdioServerParameters(
        command="uv",
        args=["run", "--directory", REPO, "awx-mcp"],
        env=_server_env(),
    )
    async with stdio_client(params) as (read, write):
        return await _drive(read, write)


def _spawn_serve(port: int, sse: bool) -> subprocess.Popen:
    cmd = ["uv", "run", "awx-mcp", "--serve", "--host", HOST, "--port", str(port)]
    if sse:
        cmd.append("--sse")
    return subprocess.Popen(
        cmd,
        cwd=REPO,
        env=_server_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _caller_headers() -> dict[str, str]:
    token = os.environ.get("ANSIBLE_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


async def test_serve() -> tuple[int, str]:
    port = _free_port()
    proc = _spawn_serve(port, sse=False)
    try:
        _wait_port(port)
        url = f"http://{HOST}:{port}/mcp"
        async with httpx.AsyncClient(headers=_caller_headers()) as hc:
            async with streamable_http_client(url, http_client=hc) as (read, write, _):
                return await _drive(read, write)
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(5)


async def test_sse() -> tuple[int, str]:
    port = _free_port()
    proc = _spawn_serve(port, sse=True)
    try:
        _wait_port(port)
        url = f"http://{HOST}:{port}/sse"
        async with sse_client(url, headers=_caller_headers()) as (read, write):
            return await _drive(read, write)
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(5)


async def test_remote() -> tuple[int, str]:
    """Proxy mode: stdio proxy -> central streamable-http serve."""
    port = _free_port()
    central = _spawn_serve(port, sse=False)
    try:
        _wait_port(port)
        url = f"http://{HOST}:{port}/mcp"
        env = _server_env()
        env["AWX_MCP_REMOTE_URL"] = url
        env.pop("ANSIBLE_BASE_URL", None)  # proxy needs none
        params = StdioServerParameters(
            command="uv",
            args=["run", "--directory", REPO, "awx-mcp", "--remote", url],
            env=env,
        )
        async with stdio_client(params) as (read, write):
            return await _drive(read, write)
    finally:
        central.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            central.wait(5)


MODES = {
    "local": test_local,
    "serve": test_serve,
    "sse": test_sse,
    "remote": test_remote,
}


def _summarize(text: str) -> str:
    """Render a one-line summary of a tool's JSON result."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text[:60].replace("\n", " ")
    if isinstance(data, list):
        return f"list[{len(data)}]"
    if isinstance(data, dict):
        for key in ("count", "results"):
            if key in data:
                val = data[key]
                n = val if isinstance(val, int) else len(val)
                return f"{key}={n}"
        return "{" + ", ".join(list(data)[:4]) + "}"
    return str(data)[:60]


async def exercise() -> int:
    """Call every no-required-arg read tool (list_*/get_*) over stdio."""
    params = StdioServerParameters(
        command="uv",
        args=["run", "--directory", REPO, "awx-mcp"],
        env=_server_env(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            targets = []
            for tool in tools:
                if not (tool.name.startswith("list_") or tool.name.startswith("get_")):
                    continue
                required = (tool.inputSchema or {}).get("required") or []
                if not required:
                    targets.append(tool.name)
            targets.sort()
            print(f"Exercising {len(targets)} read tools (of {len(tools)} total):\n")
            failures = 0
            for name in targets:
                try:
                    result = await session.call_tool(name, {})
                    text = ""
                    if result.content:
                        text = getattr(result.content[0], "text", "") or ""
                    if result.isError:
                        failures += 1
                        print(f"[FAIL] {name:32} {text[:70]}")
                    else:
                        print(f"[PASS] {name:32} {_summarize(text)}")
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    print(f"[FAIL] {name:32} {type(exc).__name__}: {exc}")
            passed = len(targets) - failures
            status = "OK" if not failures else "FAILED"
            print(f"\n{status}: {passed}/{len(targets)} read tools passed")
            return 1 if failures else 0


async def main() -> int:
    _require_env()
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    if which == "exercise":
        return await exercise()
    selected = list(MODES) if which == "all" else [which]
    if any(m not in MODES for m in selected):
        sys.exit(f"Unknown mode {which!r}. Choose: local, serve, sse, remote, all.")

    failures = 0
    for name in selected:
        try:
            count, version = await MODES[name]()
            print(f"[PASS] {name:16} tools={count} {PROBE_TOOL} -> {version[:80]}")
        except Exception as exc:  # noqa: BLE001 - smoke test reports all failures
            failures += 1
            print(f"[FAIL] {name:16} {type(exc).__name__}: {exc}")

    print(
        f"\n{'OK' if not failures else 'FAILED'}: "
        f"{len(selected) - failures}/{len(selected)} modes passed"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

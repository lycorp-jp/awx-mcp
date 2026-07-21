# SPDX-License-Identifier: Apache-2.0

"""Unit tests for passthrough auth, per-request token extraction, the per-user
read-only gate, and the CLI mode selection / mutual-exclusivity rules.

These exercise the SDK-independent logic with the request context mocked. The
real ``Authorization`` header reaching the server over a live transport is
covered separately by the non-mocked integration tests.
"""

from __future__ import annotations

import os

os.environ.setdefault("ANSIBLE_BASE_URL", "https://test.example.com/")
os.environ.setdefault("ANSIBLE_TOKEN", "fake-test-token")

import pytest  # noqa: E402

import awx_mcp.client as client  # noqa: E402
import awx_mcp.server as server  # noqa: E402
from awx_mcp.exceptions import AnsibleAuthError  # noqa: E402

# ---------------------------------------------------------------------------
# get_request_token — Bearer parsing + X-AWX-Token fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"authorization": "Bearer abc123"}, "abc123"),
        ({"authorization": "bearer abc123"}, "abc123"),  # scheme case-insensitive
        ({"authorization": "BEARER   spaced"}, "spaced"),
        ({"authorization": "Basic abc123"}, None),  # non-Bearer -> no token here
        ({"x-awx-token": "raw-token"}, "raw-token"),  # fallback header
        ({"authorization": "Token xyz", "x-awx-token": "raw"}, "raw"),  # fallback
        ({}, None),
    ],
)
def test_get_request_token_parsing(monkeypatch, headers, expected):
    monkeypatch.setattr(
        client, "get_request_header", lambda name: headers.get(name.lower())
    )
    assert client.get_request_token() == expected


def test_get_request_header_returns_none_outside_request(monkeypatch):
    """The request_context property raises ValueError outside a request; the
    helper must swallow it and return None (not propagate)."""

    class _Ctx:
        @property
        def request_context(self):
            raise ValueError("Context is not available outside of a request")

    monkeypatch.setattr(server.mcp, "get_context", lambda: _Ctx())
    assert server.get_request_header("authorization") is None


# ---------------------------------------------------------------------------
# get_ansible_client — passthrough branch
# ---------------------------------------------------------------------------


def test_passthrough_uses_request_token_no_mint(monkeypatch):
    monkeypatch.setattr(client, "AUTH_MODE", "passthrough")
    monkeypatch.setattr(client, "get_request_token", lambda: "caller-token-A")
    # Guard: minting must not happen in passthrough.
    monkeypatch.setattr(
        client.AnsibleClient,
        "get_token",
        lambda self: pytest.fail("passthrough must not mint a token"),
    )

    with client.get_ansible_client() as c:
        assert c.token == "caller-token-A"


def test_passthrough_missing_token_raises_401(monkeypatch):
    monkeypatch.setattr(client, "AUTH_MODE", "passthrough")
    monkeypatch.setattr(client, "get_request_token", lambda: None)

    with pytest.raises(AnsibleAuthError) as exc:
        with client.get_ansible_client():
            pass
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# per-user read-only gate (X-AWX-Read-Only)
# ---------------------------------------------------------------------------


def test_read_only_gate_rejects_write_when_header_set(monkeypatch):
    monkeypatch.setattr(server, "AUTH_MODE", "passthrough")
    monkeypatch.setattr(server, "get_request_header", lambda name: "true")

    @server._read_only_gated
    def do_write():
        return "wrote"

    with pytest.raises(PermissionError, match="read-only"):
        do_write()


def test_read_only_gate_allows_write_without_header(monkeypatch):
    monkeypatch.setattr(server, "AUTH_MODE", "passthrough")
    monkeypatch.setattr(server, "get_request_header", lambda name: None)

    @server._read_only_gated
    def do_write():
        return "wrote"

    assert do_write() == "wrote"


def test_read_only_gate_noop_in_static_mode(monkeypatch):
    # In static/local mode the header is irrelevant; the gate never fires even
    # if a stray header is present.
    monkeypatch.setattr(server, "AUTH_MODE", "static")
    monkeypatch.setattr(server, "get_request_header", lambda name: "true")

    @server._read_only_gated
    def do_write():
        return "wrote"

    assert do_write() == "wrote"


def test_read_only_gate_async(monkeypatch):
    import asyncio

    monkeypatch.setattr(server, "AUTH_MODE", "passthrough")
    monkeypatch.setattr(server, "get_request_header", lambda name: "1")

    @server._read_only_gated
    async def do_write_async():
        return "wrote"

    with pytest.raises(PermissionError):
        asyncio.run(do_write_async())


# ---------------------------------------------------------------------------
# CLI mode selection / mutual exclusivity (argparse)
# ---------------------------------------------------------------------------


def _run_main(monkeypatch, argv):
    import awx_mcp

    monkeypatch.setattr("sys.argv", ["awx-mcp", *argv])
    return awx_mcp.main()


def test_cli_remote_and_serve_mutually_exclusive(monkeypatch):
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, ["--remote", "https://c/mcp", "--serve"])


def test_cli_remote_rejects_host_port_sse(monkeypatch):
    for extra in (["--host", "0.0.0.0"], ["--port", "9"], ["--sse"]):
        with pytest.raises(SystemExit):
            _run_main(monkeypatch, ["--remote", "https://c/mcp", *extra])


def test_cli_sse_requires_serve(monkeypatch):
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, ["--sse"])


def test_cli_remote_invokes_proxy(monkeypatch):
    import awx_mcp.proxy as proxy

    called = {}
    monkeypatch.setattr(proxy, "run_proxy", lambda url: called.setdefault("url", url))
    monkeypatch.delenv("AWX_MCP_REMOTE_URL", raising=False)
    _run_main(monkeypatch, ["--remote", "https://central.example/mcp"])
    assert called["url"] == "https://central.example/mcp"


def test_cli_remote_url_from_env(monkeypatch):
    import awx_mcp.proxy as proxy

    called = {}
    monkeypatch.setattr(proxy, "run_proxy", lambda url: called.setdefault("url", url))
    monkeypatch.setenv("AWX_MCP_REMOTE_URL", "https://env-central/mcp")
    _run_main(monkeypatch, [])
    assert called["url"] == "https://env-central/mcp"

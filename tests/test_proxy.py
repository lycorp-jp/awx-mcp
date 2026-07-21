# SPDX-License-Identifier: Apache-2.0

"""Unit / subprocess tests for the client proxy (``awx-mcp --remote``).

The load-bearing invariant is that proxy mode never imports ``awx_mcp.server``
(proxy users have no ``ANSIBLE_BASE_URL``, so a server import would crash at
startup). That is verified in a fresh subprocess because the main test process
imports ``server`` via other modules. The full stdio<->central<->AWX relay is
covered by ``test_integration_passthrough.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

os.environ.setdefault("ANSIBLE_BASE_URL", "https://test.example.com/")
os.environ.setdefault("ANSIBLE_TOKEN", "fake-test-token")

import pytest  # noqa: E402


def _subprocess(script: str, env_extra: dict[str, str]):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": "."}
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script).strip()],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_proxy_import_does_not_load_server():
    """Importing the proxy must not import server (no ANSIBLE_BASE_URL here)."""
    r = _subprocess(
        """
        import sys
        import awx_mcp.proxy  # noqa: F401
        assert "awx_mcp.server" not in sys.modules, "server was imported"
        assert "awx_mcp.tools" not in sys.modules, "tools were imported"
        sys.stdout.write("OK")
        """,
        env_extra={},  # deliberately no ANSIBLE_BASE_URL / ANSIBLE_TOKEN
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "OK"


def test_proxy_local_logging_crash_safety():
    """With usage logging on and no ANSIBLE_BASE_URL, recording a proxy call
    writes a transport=proxy record and never imports server."""
    r = _subprocess(
        """
        import json, sys, time
        import awx_mcp.proxy  # noqa: F401
        from awx_mcp.usage import record_proxy_tool_call
        record_proxy_tool_call(
            "list_inventories", time.monotonic(), True, None,
            user="argon", awx_host="central.example.com",
        )
        assert "awx_mcp.server" not in sys.modules, "server was imported"
        import os
        line = open(os.environ["AWX_MCP_USAGE_LOG_FILE"]).read().splitlines()[-1]
        entry = json.loads(line)
        assert entry["transport"] == "proxy", entry
        assert entry["user"] == "argon"
        sys.stdout.write("OK")
        """,
        env_extra={"AWX_MCP_USAGE_LOG_FILE": "/tmp/awx_mcp_proxy_test_usage.jsonl"},
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "OK"


def test_run_proxy_requires_token(monkeypatch):
    import awx_mcp.proxy as proxy

    monkeypatch.delenv("ANSIBLE_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        proxy.run_proxy("https://central.example/mcp")
    assert "ANSIBLE_TOKEN" in str(exc.value)

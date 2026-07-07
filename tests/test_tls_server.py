# SPDX-License-Identifier: Apache-2.0

"""Tests for inbound TLS resolution (``server.resolve_tls_kwargs``).

TLS env vars are read at ``awx_mcp.server`` import time, so each case runs in a
fresh subprocess. The probe imports server, calls ``resolve_tls_kwargs`` with a
transport (from ``PROBE_TRANSPORT``), and prints the result as JSON; a
``ValueError`` exits non-zero with the message on stderr.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

_PROBE = textwrap.dedent(
    """
    import json, os, sys
    from awx_mcp import server
    try:
        transport = os.environ.get("PROBE_TRANSPORT", "streamable-http")
        r = server.resolve_tls_kwargs(transport)
        sys.stdout.write(json.dumps(r))
    except ValueError as e:
        sys.stderr.write("VALUEERROR:" + str(e))
        sys.exit(3)
    """
).strip()


def _run(env_extra: dict[str, str]):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": "."}
    env["ANSIBLE_BASE_URL"] = "https://awx.example.com/"
    env["ANSIBLE_TOKEN"] = "dummy"
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _cert_key(tmp_path):
    cert = tmp_path / "srv.crt"
    key = tmp_path / "srv.key"
    cert.write_text("-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n")
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    return cert, key


def test_tls_disabled_returns_none():
    r = _run({})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) is None


def test_tls_enabled_returns_ssl_kwargs(tmp_path):
    cert, key = _cert_key(tmp_path)
    r = _run(
        {
            "AWX_MCP_TLS_ENABLE": "true",
            "AWX_MCP_TLS_CERT": str(cert),
            "AWX_MCP_TLS_KEY": str(key),
        }
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}


def test_tls_key_password_included(tmp_path):
    cert, key = _cert_key(tmp_path)
    r = _run(
        {
            "AWX_MCP_TLS_ENABLE": "true",
            "AWX_MCP_TLS_CERT": str(cert),
            "AWX_MCP_TLS_KEY": str(key),
            "AWX_MCP_TLS_KEY_PASSWORD": "s3cret",
        }
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["ssl_keyfile_password"] == "s3cret"


def test_tls_enabled_missing_key_is_fatal(tmp_path):
    cert, _ = _cert_key(tmp_path)
    r = _run({"AWX_MCP_TLS_ENABLE": "true", "AWX_MCP_TLS_CERT": str(cert)})
    assert r.returncode == 3
    assert "AWX_MCP_TLS_KEY" in r.stderr


def test_tls_enabled_nonexistent_cert_is_fatal(tmp_path):
    _, key = _cert_key(tmp_path)
    r = _run(
        {
            "AWX_MCP_TLS_ENABLE": "true",
            "AWX_MCP_TLS_CERT": "/no/such/cert.pem",
            "AWX_MCP_TLS_KEY": str(key),
        }
    )
    assert r.returncode == 3
    assert "does not exist" in r.stderr


def test_tls_ignored_for_stdio(tmp_path):
    cert, key = _cert_key(tmp_path)
    r = _run(
        {
            "PROBE_TRANSPORT": "stdio",
            "AWX_MCP_TLS_ENABLE": "true",
            "AWX_MCP_TLS_CERT": str(cert),
            "AWX_MCP_TLS_KEY": str(key),
        }
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) is None
    assert "stdio" in r.stderr  # warning emitted

# SPDX-License-Identifier: Apache-2.0

"""Tests for TLS/HTTPS configuration resolved at ``awx_mcp.server`` import time.

``server`` reads the TLS env vars at import, so each case runs in a fresh
subprocess. The subprocess prints the resolved ``ANSIBLE_SSL_VERIFY`` /
``ANSIBLE_BASE_URL`` as JSON on stdout; warnings land on stderr (logging).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

_PROBE = textwrap.dedent(
    """
    import json, sys
    from awx_mcp import server
    sys.stdout.write(json.dumps({
        "verify": server.ANSIBLE_SSL_VERIFY,
        "verify_type": type(server.ANSIBLE_SSL_VERIFY).__name__,
        "base_url": server.ANSIBLE_BASE_URL,
    }))
    """
).strip()


def _run(env_extra: dict[str, str]):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": "."}
    env.setdefault("ANSIBLE_BASE_URL", "https://awx.example.com/")
    env["ANSIBLE_TOKEN"] = "dummy"
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_verification_on_by_default():
    r = _run({})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["verify"] is True
    assert out["base_url"].startswith("https://")


def test_disable_verification_emits_warning():
    r = _run({"ANSIBLE_SSL_VERIFY": "false"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["verify"] is False
    assert "DISABLED" in r.stderr and "man-in-the-middle" in r.stderr


def test_custom_ca_bundle_used_as_verify_path(tmp_path):
    ca = tmp_path / "corp-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n")
    r = _run({"ANSIBLE_CA_BUNDLE": str(ca)})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["verify"] == str(ca)
    assert out["verify_type"] == "str"


def test_missing_ca_bundle_is_fatal():
    r = _run({"ANSIBLE_CA_BUNDLE": "/no/such/ca.pem"})
    assert r.returncode != 0
    assert "ANSIBLE_CA_BUNDLE" in r.stderr


def test_disabled_verification_ignores_ca_bundle(tmp_path):
    # When verification is off, the CA bundle is irrelevant (verify=False wins).
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    r = _run({"ANSIBLE_SSL_VERIFY": "false", "ANSIBLE_CA_BUNDLE": str(ca)})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["verify"] is False


def test_http_base_url_warns():
    r = _run({"ANSIBLE_BASE_URL": "http://awx.example.com/"})
    assert r.returncode == 0, r.stderr
    assert "http://" in r.stderr and "unencrypted" in r.stderr


def test_bare_host_upgraded_to_https():
    r = _run({"ANSIBLE_BASE_URL": "awx.example.com"})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["base_url"] == "https://awx.example.com"

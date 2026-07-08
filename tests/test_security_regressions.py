# SPDX-License-Identifier: Apache-2.0

"""Security regression tests.

Targets:
- port/origin validation in ``_validate_url``
- opt-in credential management gating
- Form-mode credential elicitation (see test_elicit_form_mode.py)
"""

import os

os.environ.setdefault("ANSIBLE_BASE_URL", "https://test.example.com/")
os.environ.setdefault("ANSIBLE_TOKEN", "fake-test-token")

import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import textwrap  # noqa: E402

import pytest  # noqa: E402

from awx_mcp.client import AnsibleClient  # noqa: E402

# ---------------------------------------------------------------------------
# port validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url, bad_url",
    [
        ("https://awx.example.com/", "https://awx.example.com:8443/api/v2/"),
        ("https://awx.example.com:8443/", "https://awx.example.com/api/v2/"),
        ("https://awx.example.com:443/", "https://awx.example.com:8443/api/v2/"),
    ],
    ids=["bare-vs-port", "port-vs-bare", "443-vs-8443"],
)
def test_port_validation_rejects_origin_mismatch(base_url, bad_url):
    """Port differences must be rejected as origin mismatch."""
    client = AnsibleClient(base_url=base_url, token="t")
    with pytest.raises(ValueError) as excinfo:
        client._validate_url(bad_url)
    assert "origin mismatch" in str(excinfo.value).lower()


def test_validate_url_accepts_matching_origin():
    client = AnsibleClient(base_url="https://awx.example.com:8443/", token="t")
    out = client._validate_url("https://awx.example.com:8443/api/v2/jobs/")
    assert out == "https://awx.example.com:8443/api/v2/jobs/"


def test_validate_url_rejects_scheme_mismatch():
    client = AnsibleClient(base_url="https://awx.example.com/", token="t")
    with pytest.raises(ValueError):
        client._validate_url("http://awx.example.com/api/v2/")


def test_validate_url_rejects_hostname_mismatch():
    client = AnsibleClient(base_url="https://awx.example.com/", token="t")
    with pytest.raises(ValueError):
        client._validate_url("https://attacker.example.com/api/v2/")


# ---------------------------------------------------------------------------
# opt-in credential management gating
# ---------------------------------------------------------------------------


def _run_subprocess_tool_listing(env_value: str | None) -> list[str]:
    """Import awx_mcp in a fresh subprocess and return the registered tool list."""
    if env_value is None:
        flag_setup = "os.environ.pop('AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT', None)"
    else:
        flag_setup = (
            f"os.environ['AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT'] = {env_value!r}"
        )
    script = textwrap.dedent(
        f"""
        import json, os, sys
        os.environ['ANSIBLE_BASE_URL'] = 'https://x.example.com/'
        os.environ['ANSIBLE_TOKEN'] = 'dummy'
        {flag_setup}
        from awx_mcp import server
        from awx_mcp import tools  # noqa: F401
        names = list(server.mcp._tool_manager._tools.keys())
        sys.stdout.write(json.dumps(names))
        """
    ).strip()
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "."},
    )
    return json.loads(result.stdout)


def test_gated_tools_not_registered_by_default():
    """When AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT is unset, the four
    write tools must not appear in the server's tool registry. Verified
    via subprocess to bypass import-time caching."""
    tools = _run_subprocess_tool_listing(env_value=None)
    for name in (
        "create_credential",
        "update_credential",
        "create_user",
        "update_user",
    ):
        assert name not in tools, f"{name} must not be registered when flag is unset"


def test_gated_tools_registered_when_flag_true():
    """Truthy values register the four tools."""
    tools = _run_subprocess_tool_listing(env_value="true")
    for name in (
        "create_credential",
        "update_credential",
        "create_user",
        "update_user",
    ):
        assert name in tools, f"{name} must be registered when flag is true"


def test_default_tool_count_is_141():
    """Smoke: total tool count for default config (credential + ad-hoc gates off).

    146 total minus 4 credential/user tools minus 1 ad hoc command tool.
    """
    tools = _run_subprocess_tool_listing(env_value=None)
    assert len(tools) == 141


def test_credential_opt_in_tool_count_is_145():
    """Credential management on, ad hoc still gated off: 141 + 4."""
    tools = _run_subprocess_tool_listing(env_value="true")
    assert len(tools) == 145

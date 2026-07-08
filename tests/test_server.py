# SPDX-License-Identifier: Apache-2.0

"""Tests for AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT gating.

These tests run the package import in a fresh subprocess per case because
``awx_mcp.server`` reads the env flag at import time.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

GATED_TOOLS = (
    "create_credential",
    "update_credential",
    "create_user",
    "update_user",
)

# Tools that must always remain available regardless of the flag.
ALWAYS_REGISTERED = (
    "list_credentials",
    "get_credential",
    "delete_credential",
    "copy_credential",
    "list_users",
    "get_user",
    "delete_user",
)


def _list_registered_tools(
    env_value: str | None, ad_hoc_value: str | None = None
) -> list[str]:
    """Import the package in a fresh subprocess and return registered tool names."""
    script = textwrap.dedent(
        """
        import json, os, sys
        os.environ['ANSIBLE_BASE_URL'] = 'https://x.example.com/'
        os.environ['ANSIBLE_TOKEN'] = 'dummy'
        from awx_mcp import server
        from awx_mcp import tools  # noqa: F401  (registers tool modules)
        names = list(server.mcp._tool_manager._tools.keys())
        sys.stdout.write(json.dumps(names))
        """
    ).strip()

    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": ".",
    }
    if env_value is not None:
        env["AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT"] = env_value
    if ad_hoc_value is not None:
        env["AWX_MCP_ENABLE_AD_HOC_COMMAND"] = ad_hoc_value

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return json.loads(result.stdout)


def test_default_unset_does_not_register_gated_tools():
    tools = _list_registered_tools(env_value=None)

    for name in GATED_TOOLS:
        assert name not in tools, f"{name} must be gated by default"
    for name in ALWAYS_REGISTERED:
        assert name in tools, f"{name} must always be registered"


def test_explicit_false_does_not_register_gated_tools():
    tools = _list_registered_tools(env_value="false")

    for name in GATED_TOOLS:
        assert name not in tools


def test_true_registers_all_gated_tools():
    tools = _list_registered_tools(env_value="true")

    for name in GATED_TOOLS:
        assert name in tools, f"{name} must be registered when flag is true"
    for name in ALWAYS_REGISTERED:
        assert name in tools


def test_truthy_aliases_register_gated_tools():
    for value in ("1", "yes", "TRUE", "True"):
        tools = _list_registered_tools(env_value=value)
        for name in GATED_TOOLS:
            assert name in tools, f"{name} should register for env value {value!r}"


def test_default_total_tool_count_is_141():
    # Both sensitive-tool flags off: 146 total - 4 credential/user - 1 ad hoc.
    tools = _list_registered_tools(env_value=None)
    assert len(tools) == 141, (
        f"Expected 141 tools with both gates disabled, got {len(tools)}"
    )


def test_credential_management_enabled_tool_count_is_145():
    # Credential management on, ad hoc still off: 141 + 4.
    tools = _list_registered_tools(env_value="true")
    assert len(tools) == 145, (
        f"Expected 145 tools with credential management enabled, got {len(tools)}"
    )


def test_all_gates_enabled_tool_count_is_146():
    tools = _list_registered_tools(env_value="true", ad_hoc_value="true")
    assert len(tools) == 146, (
        f"Expected 146 tools with all gates enabled, got {len(tools)}"
    )


def test_ad_hoc_command_gated_by_default():
    assert "run_ad_hoc_command" not in _list_registered_tools(env_value=None)


def test_ad_hoc_command_registered_when_enabled():
    tools = _list_registered_tools(env_value=None, ad_hoc_value="true")
    assert "run_ad_hoc_command" in tools
    # get_ad_hoc_command / cancel_ad_hoc_command are not gated by this flag.
    assert "get_ad_hoc_command" in _list_registered_tools(env_value=None)

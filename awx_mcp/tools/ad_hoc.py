# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Ad Hoc Command Tools
"""

import json

from ..client import get_ansible_client
from ..server import maybe_ad_hoc_command_tool, read_tool, write_tool


@maybe_ad_hoc_command_tool
def run_ad_hoc_command(
    inventory_id: int,
    credential_id: int,
    module_name: str,
    module_args: str,
    limit: str = "",
    verbosity: int = 0,
) -> str:
    """Run an AWX ad hoc command.

    Executes a one-off Ansible module on inventory hosts without using a
    playbook or job template. Returns an ad hoc command record with an id for
    tracking via get_ad_hoc_command; for playbook runs, use launch_job.

    Args:
        inventory_id: Target inventory ID (from list_inventories)
        credential_id: Machine credential ID used for host access
            (from list_credentials)
        module_name: Ansible module name (e.g., command, shell, ping, setup, copy)
        module_args: Module arguments (e.g., "uptime" for command)
        limit: Host pattern to scope targets (e.g., "web*", "db-servers")
        verbosity: Verbosity level from 0 to 4 (0 is minimal output)
    """
    if verbosity not in range(5):
        return json.dumps(
            {"status": "error", "message": "Verbosity must be between 0 and 4"}
        )

    with get_ansible_client() as client:
        data = {
            "inventory": inventory_id,
            "credential": credential_id,
            "module_name": module_name,
            "module_args": module_args,
            "verbosity": verbosity,
        }

        if limit:
            data["limit"] = limit

        response = client.request("POST", "/api/v2/ad_hoc_commands/", data=data)
        return json.dumps(response, indent=2)


@read_tool
def get_ad_hoc_command(command_id: int) -> str:
    """Get details for one AWX ad hoc command.

    Use this to monitor state and outcome of a previously launched one-off
    module execution. Pair with run_ad_hoc_command to start the run and read
    returned status fields to decide whether cancellation is needed.

    Args:
        command_id: Ad hoc command ID (from run_ad_hoc_command)
    """
    with get_ansible_client() as client:
        command = client.request("GET", f"/api/v2/ad_hoc_commands/{command_id}/")
        return json.dumps(command, indent=2)


@write_tool(destructive=True)
def cancel_ad_hoc_command(command_id: int) -> str:
    """Cancel a running AWX ad hoc command.

    WARNING: This interrupts active module execution on target hosts and may
    leave partial changes depending on the module. Use get_ad_hoc_command to
    confirm the command is still running before issuing cancellation.

    Args:
        command_id: Ad hoc command ID (from run_ad_hoc_command or get_ad_hoc_command)
    """
    with get_ansible_client() as client:
        response = client.request(
            "POST", f"/api/v2/ad_hoc_commands/{command_id}/cancel/"
        )
        return json.dumps(response, indent=2)

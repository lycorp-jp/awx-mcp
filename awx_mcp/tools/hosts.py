# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Host Management Tools
"""

import json
from typing import Any

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool
from ..utils import validate_json_str


@read_tool
def list_hosts(inventory_id: int = None, limit: int = 20, offset: int = 0) -> str:
    """List AWX hosts.

    Returns individual managed machines and their IDs, optionally scoped to one
    inventory. For logical collections inside an inventory, use list_groups
    instead.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        inventory_id: Optional ID of inventory to filter hosts
            (from list_inventories response)
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}

        if inventory_id is not None:
            endpoint = f"/api/v2/inventories/{inventory_id}/hosts/"
        else:
            endpoint = "/api/v2/hosts/"

        hosts = handle_pagination(client, endpoint, params, with_meta=True)
        return json.dumps(hosts, indent=2)


@read_tool
def get_host(host_id: int) -> str:
    """Get details for one AWX host.

    Use this when you need host variables, inventory linkage, and state before
    updating membership or configuration. For host discovery across inventories,
    use list_hosts first.

    Args:
        host_id: ID of the host (from list_hosts response)
    """
    with get_ansible_client() as client:
        host = client.request("GET", f"/api/v2/hosts/{host_id}/")
        return json.dumps(host, indent=2)


@write_tool()
def create_host(
    name: str,
    inventory_id: int,
    variables: str = "{}",
    description: str = "",
    enabled: bool = True,
) -> str:
    """Create an AWX host in an inventory.

    Use this to add a new machine record to an inventory with optional host
    variables. Returns host_id for follow-up with update_host or
    add_host_to_group; for attaching an existing host to a group, use add_host_to_group.

    Args:
        name: Name or IP address of the host
        inventory_id: ID of the inventory to add the host to
            (from list_inventories response)
        variables: JSON string of host variables
        description: Description of the host
        enabled: Whether the host is included in job runs
    """
    error = validate_json_str(variables, "variables")
    if error:
        return error

    with get_ansible_client() as client:
        data = {
            "name": name,
            "inventory": inventory_id,
            "variables": variables,
            "description": description,
            "enabled": enabled,
        }
        response = client.request("POST", "/api/v2/hosts/", data=data)
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def update_host(
    host_id: int,
    name: str = None,
    variables: str = None,
    description: str = None,
    enabled: bool = None,
) -> str:
    """Update AWX host metadata and variables.

    Use this to modify an existing host's name, description, or variables while
    preserving its inventory and group relationships. For creating a new host,
    use create_host.

    Args:
        host_id: ID of the host (from list_hosts response)
        name: New name for the host
        variables: JSON string of host variables
        description: New description for the host
        enabled: Whether the host is included in job runs
    """
    if variables is not None:
        error = validate_json_str(variables, "variables")
        if error:
            return error

    with get_ansible_client() as client:
        data: dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if variables is not None:
            data["variables"] = variables
        if description is not None:
            data["description"] = description
        if enabled is not None:
            data["enabled"] = enabled

        response = client.request("PATCH", f"/api/v2/hosts/{host_id}/", data=data)
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_host(host_id: int) -> str:
    """Delete an AWX host.

    WARNING: This permanently removes the host record from AWX and detaches it
    from all group memberships. Verify target identity with get_host before
    deletion.

    Args:
        host_id: ID of the host (from list_hosts response)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/hosts/{host_id}/")
        return json.dumps({"status": "success", "message": f"Host {host_id} deleted"})

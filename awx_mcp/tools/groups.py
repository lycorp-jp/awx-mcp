# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Group Management Tools
"""

import json

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool
from ..utils import validate_json_str


@read_tool
def list_groups(inventory_id: int = None, limit: int = 20, offset: int = 0) -> str:
    """List AWX inventory groups.

    Returns logical host groupings within inventories and their group IDs for
    membership operations. For individual machine records, use list_hosts
    instead.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        inventory_id: Optional ID of inventory to filter groups
            (from list_inventories response)
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        if inventory_id is not None:
            endpoint = f"/api/v2/inventories/{inventory_id}/groups/"
        else:
            endpoint = "/api/v2/groups/"
        groups = handle_pagination(client, endpoint, params, with_meta=True)
        return json.dumps(groups, indent=2)


@read_tool
def get_group(group_id: int) -> str:
    """Get details for one AWX group.

    Use this when you need group variables, inventory linkage, and related
    hosts before changing membership or metadata. For broad discovery, use
    list_groups first.

    Args:
        group_id: ID of the group (from list_groups response)
    """
    with get_ansible_client() as client:
        group = client.request("GET", f"/api/v2/groups/{group_id}/")
        return json.dumps(group, indent=2)


@write_tool()
def create_group(
    name: str, inventory_id: int, variables: str = "{}", description: str = ""
) -> str:
    """Create an AWX group inside an inventory.

    Use this to define a logical host collection with shared group variables
    under an inventory. Returns group_id for use with add_host_to_group and
    remove_host_from_group.

    Args:
        name: Name of the group
        inventory_id: ID of the inventory to add the group to
            (from list_inventories response)
        variables: JSON string of group variables
        description: Description of the group
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
        }
        response = client.request("POST", "/api/v2/groups/", data=data)
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def update_group(
    group_id: int, name: str = None, variables: str = None, description: str = None
) -> str:
    """Update AWX group metadata and variables.

    Use this to modify an existing group's name, description, or variables
    without recreating it. For membership changes, use add_host_to_group or
    remove_host_from_group.

    Args:
        group_id: ID of the group (from list_groups response)
        name: New name for the group
        variables: JSON string of group variables
        description: New description for the group
    """
    if variables is not None:
        error = validate_json_str(variables, "variables")
        if error:
            return error

    with get_ansible_client() as client:
        data = {}
        if name is not None:
            data["name"] = name
        if variables is not None:
            data["variables"] = variables
        if description is not None:
            data["description"] = description

        response = client.request("PATCH", f"/api/v2/groups/{group_id}/", data=data)
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_group(group_id: int) -> str:
    """Delete an AWX group.

    WARNING: This permanently deletes the group and its direct group-level
    variable context for host targeting in AWX. Confirm group identity and
    membership impact with get_group before deletion.

    Args:
        group_id: ID of the group (from list_groups response)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/groups/{group_id}/")
        return json.dumps({"status": "success", "message": f"Group {group_id} deleted"})


@write_tool(idempotent=True)
def add_host_to_group(group_id: int, host_id: int) -> str:
    """Associate an existing AWX host with a group.

    Use this to add membership for a host that already exists in the inventory
    hierarchy. To create a new host record, use create_host instead.

    Args:
        group_id: ID of the group (from list_groups response)
        host_id: ID of the host (from list_hosts response)
    """
    with get_ansible_client() as client:
        data = {"id": host_id}
        response = client.request(
            "POST", f"/api/v2/groups/{group_id}/hosts/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def remove_host_from_group(group_id: int, host_id: int) -> str:
    """Remove AWX host membership from a group.

    Use this to disassociate a host from a logical group while keeping the host
    itself in the inventory. To remove the host object entirely, use
    delete_host instead.

    Args:
        group_id: ID of the group (from list_groups response)
        host_id: ID of the host (from list_hosts response)
    """
    with get_ansible_client() as client:
        client.request(
            "POST",
            f"/api/v2/groups/{group_id}/hosts/",
            data={"id": host_id, "disassociate": True},
        )
        return json.dumps(
            {
                "status": "success",
                "message": f"Host {host_id} removed from group {group_id}",
            }
        )

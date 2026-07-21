# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Inventory & Inventory Source Tools

Inventory CRUD, inventory source management, and inventory update monitoring.
"""

import json
from typing import Any

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool
from ..utils import validate_json_str

# =============================================================================
# Inventory Management
# =============================================================================


@read_tool
def list_inventories(limit: int = 20, offset: int = 0) -> str:
    """List AWX inventories.

    Returns inventory IDs, names, and organization links for the top-level
    host/group container in AWX. Use returned inventory_id values with
    get_inventory, list_hosts, list_groups, and create_job_template.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        inventories = handle_pagination(
            client, "/api/v2/inventories/", params, with_meta=True
        )
        return json.dumps(inventories, indent=2)


@read_tool
def get_inventory(inventory_id: int) -> str:
    """Get details for one AWX inventory.

    Use this when you already have an inventory_id and need full metadata,
    variables, and related URLs before making changes. For broad discovery,
    use list_inventories first.

    Args:
        inventory_id: ID of the inventory (from list_inventories response)
    """
    with get_ansible_client() as client:
        inventory = client.request("GET", f"/api/v2/inventories/{inventory_id}/")
        return json.dumps(inventory, indent=2)


@write_tool()
def create_inventory(
    name: str, organization_id: int, description: str = "", variables: str = "{}"
) -> str:
    """Create an AWX inventory in an organization.

    Use this to create a new host/group container under an existing AWX
    organization. Returns the new inventory_id for use with host, group,
    inventory source, and job template tools.

    Args:
        name: Name of the inventory
        organization_id: ID of the organization (from list_organizations response)
        description: Description of the inventory
        variables: JSON string of inventory-level variables
    """
    error = validate_json_str(variables, "variables")
    if error:
        return error

    with get_ansible_client() as client:
        data = {
            "name": name,
            "description": description,
            "organization": organization_id,
            "variables": variables,
        }
        response = client.request("POST", "/api/v2/inventories/", data=data)
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def update_inventory(
    inventory_id: int,
    name: str = None,
    description: str = None,
    variables: str = None,
) -> str:
    """Update AWX inventory metadata.

    Use this to rename or re-describe an existing inventory while keeping its
    hosts, groups, and sources intact. For create-or-delete lifecycle actions,
    use create_inventory or delete_inventory.

    Args:
        inventory_id: ID of the inventory (from list_inventories response)
        name: New name for the inventory
        description: New description for the inventory
        variables: JSON string of inventory-level variables
    """
    if variables is not None:
        error = validate_json_str(variables, "variables")
        if error:
            return error

    with get_ansible_client() as client:
        data: dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if variables is not None:
            data["variables"] = variables

        response = client.request(
            "PATCH", f"/api/v2/inventories/{inventory_id}/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_inventory(inventory_id: int) -> str:
    """Delete an AWX inventory.

    WARNING: This permanently deletes the inventory and its related inventory
    resources in AWX, and cannot be undone. Consider checking dependencies
    with get_inventory, list_hosts, and list_groups before deletion.

    Args:
        inventory_id: ID of the inventory (from list_inventories response)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/inventories/{inventory_id}/")
        return json.dumps(
            {"status": "success", "message": f"Inventory {inventory_id} deleted"}
        )


# =============================================================================
# Inventory Source Management
# =============================================================================


@read_tool
def list_inventory_sources(
    inventory_id: int = None, limit: int = 20, offset: int = 0
) -> str:
    """List AWX inventory sources.

    Use this to inspect dynamic host sync configurations (EC2, GCE, SCM, and
    similar) attached to inventories. For inventory containers themselves, use
    list_inventories instead.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        inventory_id: Optional ID of inventory to filter sources
            (from list_inventories response)
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        if inventory_id is not None:
            endpoint = f"/api/v2/inventories/{inventory_id}/inventory_sources/"
        else:
            endpoint = "/api/v2/inventory_sources/"
        sources = handle_pagination(client, endpoint, params, with_meta=True)
        return json.dumps(sources, indent=2)


@read_tool
def get_inventory_source(source_id: int) -> str:
    """Get details for one AWX inventory source.

    Use this when you need source configuration, credential linkage, and sync
    settings for a specific dynamic inventory source. Pair with
    list_inventory_updates to track run history for this source.

    Args:
        source_id: ID of the inventory source (from list_inventory_sources response)
    """
    with get_ansible_client() as client:
        source = client.request("GET", f"/api/v2/inventory_sources/{source_id}/")
        return json.dumps(source, indent=2)


@write_tool()
def create_inventory_source(
    name: str,
    inventory_id: int,
    source: str,
    source_path: str = "",
    source_vars: str = "{}",
    credential_id: int = None,
    overwrite: bool = False,
    overwrite_vars: bool = False,
    update_on_launch: bool = False,
    description: str = "",
) -> str:
    """Create an AWX inventory source for dynamic host sync.

    Use this to attach cloud/SCM/controller-based discovery to an inventory so
    hosts can be imported or refreshed automatically. Returns source_id for
    sync_inventory_source and list_inventory_updates.

    Args:
        name: Name of the inventory source
        inventory_id: ID of the inventory (from list_inventories response)
        source: Source type (scm, ec2, gce, azure_rm, vmware, satellite6,
            openstack, rhv, controller, file)
        source_path: Path to the source file within the project
        source_vars: JSON/YAML string of source variables
        credential_id: Optional ID of the credential (from list_credentials response)
        overwrite: Whether to overwrite existing hosts/groups on sync
        overwrite_vars: Whether to overwrite existing variables on sync
        update_on_launch: Whether to update on job launch
        description: Description of the inventory source
    """
    looks_like_json = source_vars.strip()[:1] in ("{", "[")
    if looks_like_json:
        error = validate_json_str(source_vars, "source_vars")
        if error:
            return error

    with get_ansible_client() as client:
        data = {
            "name": name,
            "inventory": inventory_id,
            "source": source,
            "source_path": source_path,
            "source_vars": source_vars,
            "overwrite": overwrite,
            "overwrite_vars": overwrite_vars,
            "update_on_launch": update_on_launch,
            "description": description,
        }
        if credential_id is not None:
            data["credential"] = credential_id
        response = client.request("POST", "/api/v2/inventory_sources/", data=data)
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def update_inventory_source(
    source_id: int,
    name: str = None,
    source: str = None,
    source_path: str = None,
    source_vars: str = None,
    credential_id: int = None,
    overwrite: bool = None,
    overwrite_vars: bool = None,
    update_on_launch: bool = None,
    description: str = None,
) -> str:
    """Update an existing AWX inventory source.

    Use this to modify dynamic host sync configuration without recreating
    the source. For source creation, use create_inventory_source instead.

    Args:
        source_id: ID of the inventory source (from list_inventory_sources response)
        name: New name for the inventory source
        source: New source type (scm, ec2, gce, azure_rm, vmware, satellite6,
            openstack, rhv, controller, file)
        source_path: New path to the source file within the project
        source_vars: JSON/YAML string of source variables (JSON validated
            client-side; AWX also accepts YAML)
        credential_id: New credential ID (from list_credentials response)
        overwrite: Whether to overwrite existing hosts/groups on sync
        overwrite_vars: Whether to overwrite existing variables on sync
        update_on_launch: Whether to update on job launch
        description: New description
    """
    if source_vars is not None:
        looks_like_json = source_vars.strip()[:1] in ("{", "[")
        if looks_like_json:
            error = validate_json_str(source_vars, "source_vars")
            if error:
                return error

    with get_ansible_client() as client:
        data: dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if source is not None:
            data["source"] = source
        if source_path is not None:
            data["source_path"] = source_path
        if source_vars is not None:
            data["source_vars"] = source_vars
        if credential_id is not None:
            data["credential"] = credential_id
        if overwrite is not None:
            data["overwrite"] = overwrite
        if overwrite_vars is not None:
            data["overwrite_vars"] = overwrite_vars
        if update_on_launch is not None:
            data["update_on_launch"] = update_on_launch
        if description is not None:
            data["description"] = description

        response = client.request(
            "PATCH", f"/api/v2/inventory_sources/{source_id}/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def sync_inventory_source(source_id: int) -> str:
    """Start an AWX inventory source sync.

    Use this to refresh hosts/groups from a configured inventory source inside
    an inventory. For syncing SCM project content, use sync_project instead.
    Returns an inventory update record you can track with get_inventory_update.

    Args:
        source_id: ID of the inventory source (from list_inventory_sources response)
    """
    with get_ansible_client() as client:
        response = client.request(
            "POST", f"/api/v2/inventory_sources/{source_id}/update/"
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_inventory_source(source_id: int) -> str:
    """Delete an AWX inventory source.

    WARNING: This permanently removes the source configuration and stops future
    dynamic syncs from that source. Existing hosts may remain unless separately
    removed; verify source details with get_inventory_source first.

    Args:
        source_id: ID of the inventory source (from list_inventory_sources response)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/inventory_sources/{source_id}/")
        return json.dumps(
            {"status": "success", "message": f"Inventory source {source_id} deleted"}
        )


# =============================================================================
# Inventory Update Monitoring (NEW)
# =============================================================================


@read_tool
def list_inventory_updates(
    source_id: int, limit: int = 20, offset: int = 0, order_by: str = "-created"
) -> str:
    """List AWX inventory source sync history.

    Newest first by default (order_by="-created"); AWX's own default is
    oldest-first.

    Returns update records for host/group discovery runs tied to an inventory
    source. For SCM repository sync history, use list_project_updates instead.
    Use returned update_id values with get_inventory_update.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        source_id: ID of the inventory source (from list_inventory_sources response)
        limit: Maximum number of results to return
        offset: Number of results to skip
        order_by: Sort field; prefix with "-" for descending
            (e.g. -created, created, -finished, id, status)
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset, "order_by": order_by}
        updates = handle_pagination(
            client,
            f"/api/v2/inventory_sources/{source_id}/inventory_updates/",
            params,
            with_meta=True,
        )
        return json.dumps(updates, indent=2)


@read_tool
def get_inventory_update(update_id: int) -> str:
    """Get details for one AWX inventory update.

    Use this to check status, timestamps, and result metadata for a specific
    inventory sync run. For project SCM sync runs, use get_project_update.

    Args:
        update_id: ID of the inventory update (from list_inventory_updates response)
    """
    with get_ansible_client() as client:
        update = client.request("GET", f"/api/v2/inventory_updates/{update_id}/")
        return json.dumps(update, indent=2)


@write_tool(destructive=True)
def cancel_inventory_update(update_id: int) -> str:
    """Cancel a running AWX inventory update.

    WARNING: Cancels the active inventory source sync and may leave host/group
    discovery incomplete for that run. Use get_inventory_update to confirm the
    final canceled state.

    Args:
        update_id: ID of the inventory update (from list_inventory_updates response)
    """
    with get_ansible_client() as client:
        response = client.request(
            "POST", f"/api/v2/inventory_updates/{update_id}/cancel/"
        )
        return json.dumps(response, indent=2)

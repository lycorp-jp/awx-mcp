# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Organization Management Tools
"""

import json
from typing import Any

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool


@read_tool
def list_organizations(limit: int = 20, offset: int = 0) -> str:
    """List AWX organizations.

    Use this to discover top-level AWX tenants before working with teams,
    users, inventories, or projects that belong to an organization. Returns
    organization records with IDs used by get_organization and create_team.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of organization results to return
        offset: Number of organization results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        envelope = handle_pagination(
            client, "/api/v2/organizations/", params, with_meta=True
        )
        return json.dumps(envelope, indent=2)


@read_tool
def get_organization(organization_id: int) -> str:
    """Get details for one AWX organization.

    Use this when you already have an organization ID and need tenant metadata
    for downstream operations. Returns fields like id, name, and description;
    pair with list_organizations to discover IDs first.

    Args:
        organization_id: ID of the organization (from list_organizations)
    """
    with get_ansible_client() as client:
        organization = client.request(
            "GET", f"/api/v2/organizations/{organization_id}/"
        )
        return json.dumps(organization, indent=2)


@write_tool()
def create_organization(
    name: str,
    description: str = "",
    max_hosts: int = 0,
    default_environment: int = None,
) -> str:
    """Create an AWX organization.

    Organizations are the top-level tenant boundary in AWX and own teams,
    projects, inventories, and credentials. Returns the created organization
    record (including id) for chaining into create_team and create_label.

    Args:
        name: Organization name shown across AWX
        description: Organization description for tenant context
        max_hosts: Maximum number of hosts allowed (0 for unlimited)
        default_environment: Default execution environment ID
            (from list_execution_environments)
    """
    with get_ansible_client() as client:
        data = {"name": name, "description": description, "max_hosts": max_hosts}
        if default_environment is not None:
            data["default_environment"] = default_environment
        response = client.request("POST", "/api/v2/organizations/", data=data)
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def update_organization(
    organization_id: int,
    name: str = None,
    description: str = None,
    max_hosts: int = None,
    default_environment: int = None,
) -> str:
    """Update an AWX organization.

    Use this to rename or revise organization metadata without recreating the
    tenant. Returns the updated organization object; use get_organization to
    verify final state after changes.

    Args:
        organization_id: ID of the organization (from list_organizations)
        name: New organization name
        description: New organization description
        max_hosts: Maximum number of hosts allowed (0 for unlimited)
        default_environment: Default execution environment ID
            (from list_execution_environments)
    """
    with get_ansible_client() as client:
        data: dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if max_hosts is not None:
            data["max_hosts"] = max_hosts
        if default_environment is not None:
            data["default_environment"] = default_environment

        response = client.request(
            "PATCH", f"/api/v2/organizations/{organization_id}/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_organization(organization_id: int) -> str:
    """Delete an AWX organization.

    WARNING: This permanently removes the organization tenant and can impact
    related resources such as teams, projects, inventories, and credentials.
    Use get_organization first to confirm the target.

    Args:
        organization_id: ID of the organization (from list_organizations)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/organizations/{organization_id}/")
        return json.dumps(
            {"status": "success", "message": f"Organization {organization_id} deleted"}
        )

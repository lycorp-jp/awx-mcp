# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Team Management Tools
"""

import json

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool


@read_tool
def list_teams(organization_id: int = None, limit: int = 20, offset: int = 0) -> str:
    """List AWX teams, optionally scoped to an organization.

    Use this to enumerate teams under the organizations -> teams -> users
    hierarchy before assigning RBAC roles. Returns team IDs for get_team,
    update_team, delete_team, and grant_role_to_team.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        organization_id: Optional organization ID to filter teams
            (from list_organizations)
        limit: Maximum number of team results to return
        offset: Number of team results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}

        if organization_id is not None:
            endpoint = f"/api/v2/organizations/{organization_id}/teams/"
        else:
            endpoint = "/api/v2/teams/"

        envelope = handle_pagination(client, endpoint, params, with_meta=True)
        return json.dumps(envelope, indent=2)


@read_tool
def get_team(team_id: int) -> str:
    """Get details for one AWX team.

    Use this when a team ID is known and you need organization linkage or
    metadata before role assignment and membership operations. Pair with
    list_teams to discover IDs and with update_team for changes.

    Args:
        team_id: ID of the team (from list_teams)
    """
    with get_ansible_client() as client:
        team = client.request("GET", f"/api/v2/teams/{team_id}/")
        return json.dumps(team, indent=2)


@write_tool()
def create_team(name: str, organization_id: int, description: str = "") -> str:
    """Create an AWX team within an organization.

    Teams are organization-scoped groups used for RBAC delegation across AWX
    resources. Returns the created team record (including id) for follow-up
    role operations such as grant_role_to_team.

    Args:
        name: Team name (for example, "DevOps" or "Platform")
        organization_id: Parent organization ID (from list_organizations)
        description: Team description and ownership context
    """
    with get_ansible_client() as client:
        data = {
            "name": name,
            "organization": organization_id,
            "description": description,
        }
        response = client.request("POST", "/api/v2/teams/", data=data)
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def update_team(team_id: int, name: str = None, description: str = None) -> str:
    """Update an AWX team.

    Use this to adjust team identity details while preserving existing RBAC
    relationships. Returns the updated team object; use get_team to confirm
    the final values.

    Args:
        team_id: ID of the team (from list_teams)
        name: New team name
        description: New team description
    """
    with get_ansible_client() as client:
        data = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description

        response = client.request("PATCH", f"/api/v2/teams/{team_id}/", data=data)
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_team(team_id: int) -> str:
    """Delete an AWX team.

    WARNING: This permanently removes the team and may revoke team-based RBAC
    access on inventories, projects, templates, and other resources. Use
    get_team first to confirm the target team.

    Args:
        team_id: ID of the team (from list_teams)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/teams/{team_id}/")
        return json.dumps({"status": "success", "message": f"Team {team_id} deleted"})

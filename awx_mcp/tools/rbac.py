# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - RBAC (Role-Based Access Control) Tools

Role listing, role assignment (grant/revoke) for users and teams,
and object role inspection.
"""

import json

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool


@read_tool
def list_roles(limit: int = 20, offset: int = 0) -> str:
    """List all AWX RBAC roles.

    Returns global role definitions available across the AWX system. To find
    roles available for one specific resource instance, use list_object_roles
    instead.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        envelope = handle_pagination(client, "/api/v2/roles/", params, with_meta=True)
        return json.dumps(envelope, indent=2)


@read_tool
def get_role(role_id: int) -> str:
    """Get details for one AWX RBAC role.

    Use this when you need exact role metadata before assigning it to users or
    teams. For discovering candidate roles by scope, use list_roles or
    list_object_roles first.

    Args:
        role_id: ID of the role (from list_roles or list_object_roles response)
    """
    with get_ansible_client() as client:
        role = client.request("GET", f"/api/v2/roles/{role_id}/")
        return json.dumps(role, indent=2)


# =============================================================================
# Role Assignment (NEW)
# =============================================================================


@write_tool(idempotent=True)
def grant_role_to_user(role_id: int, user_id: int) -> str:
    """Grant an AWX role to a user.

    Use this to assign resource or system permissions directly to a user
    account. For team-based permission assignment, use grant_role_to_team
    instead.

    Args:
        role_id: ID of the role to grant (from list_roles or list_object_roles response)
        user_id: ID of the user to receive the role (from list_users response)
    """
    with get_ansible_client() as client:
        data = {"id": user_id}
        response = client.request("POST", f"/api/v2/roles/{role_id}/users/", data=data)
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def revoke_role_from_user(role_id: int, user_id: int) -> str:
    """Revoke an AWX role from a user.

    Use this to remove direct user-level permission grants in AWX. This only
    removes the specified assignment; effective access may still come from team
    roles.

    Args:
        role_id: ID of the role to revoke
            (from list_roles or list_object_roles response)
        user_id: ID of the user to remove the role from (from list_users response)
    """
    with get_ansible_client() as client:
        data = {"id": user_id, "disassociate": True}
        client.request("POST", f"/api/v2/roles/{role_id}/users/", data=data)
        return json.dumps(
            {
                "status": "success",
                "message": f"Role {role_id} revoked from user {user_id}",
            }
        )


@write_tool(idempotent=True)
def grant_role_to_team(role_id: int, team_id: int) -> str:
    """Grant an AWX role to a team.

    Use this to assign permissions to all team members through one RBAC grant.
    For per-user assignment, use grant_role_to_user instead.

    Args:
        role_id: ID of the role to grant (from list_roles or list_object_roles response)
        team_id: ID of the team to receive the role (from list_teams response)
    """
    with get_ansible_client() as client:
        data = {"id": team_id}
        response = client.request("POST", f"/api/v2/roles/{role_id}/teams/", data=data)
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def revoke_role_from_team(role_id: int, team_id: int) -> str:
    """Revoke an AWX role from a team.

    Use this to remove a team-level RBAC assignment in AWX. Members can still
    retain access through other team grants or direct user role assignments.

    Args:
        role_id: ID of the role to revoke
            (from list_roles or list_object_roles response)
        team_id: ID of the team to remove the role from (from list_teams response)
    """
    with get_ansible_client() as client:
        data = {"id": team_id, "disassociate": True}
        client.request("POST", f"/api/v2/roles/{role_id}/teams/", data=data)
        return json.dumps(
            {
                "status": "success",
                "message": f"Role {role_id} revoked from team {team_id}",
            }
        )


@read_tool
def list_object_roles(
    resource_type: str, resource_id: int, limit: int = 20, offset: int = 0
) -> str:
    """List AWX RBAC roles for one specific resource.

    Use this to discover assignable roles scoped to a particular inventory,
    project, template, team, organization, or credential. For global role
    definitions across all resources, use list_roles instead.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        resource_type: Resource type (inventories, projects, job_templates,
            workflow_job_templates, organizations, teams, credentials)
        resource_id: ID of the resource (from the corresponding list_* response)
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    valid_types = [
        "inventories",
        "projects",
        "job_templates",
        "workflow_job_templates",
        "organizations",
        "teams",
        "credentials",
    ]
    if resource_type not in valid_types:
        return json.dumps(
            {
                "status": "error",
                "message": (
                    f"Invalid resource_type. Must be one of: {', '.join(valid_types)}"
                ),
            }
        )

    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        envelope = handle_pagination(
            client,
            f"/api/v2/{resource_type}/{resource_id}/object_roles/",
            params,
            with_meta=True,
        )
        return json.dumps(envelope, indent=2)

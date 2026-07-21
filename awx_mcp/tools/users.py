# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - User Management Tools
"""

import json
from typing import Any

from mcp.server.fastmcp import Context
from pydantic import BaseModel

from ..client import get_ansible_client, handle_pagination
from ..server import maybe_credential_management_tool, read_tool, write_tool


class PasswordInput(BaseModel):
    password: str


@read_tool
def list_users(limit: int = 20, offset: int = 0) -> str:
    """List AWX users.

    Use this to enumerate user accounts before assigning team membership or
    RBAC roles in the organizations -> teams -> users model. Returns user IDs
    for get_user, update_user, delete_user, and grant_role_to_user.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of user results to return
        offset: Number of user results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        envelope = handle_pagination(client, "/api/v2/users/", params, with_meta=True)
        return json.dumps(envelope, indent=2)


@read_tool
def get_user(user_id: int) -> str:
    """Get details for one AWX user.

    Use this when you need account metadata, privilege flags, or identity
    fields for an existing user ID. Pair with list_users to discover IDs and
    with update_user for account changes.

    Args:
        user_id: ID of the user (from list_users)
    """
    with get_ansible_client() as client:
        user = client.request("GET", f"/api/v2/users/{user_id}/")
        return json.dumps(user, indent=2)


@maybe_credential_management_tool
async def create_user(
    username: str,
    ctx: Context,
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    is_superuser: bool = False,
    is_system_auditor: bool = False,
) -> str:
    """Create an AWX user account.

    Use this to onboard a new principal that can receive direct roles or team
    based access. Returns the created user object (including id) for chaining
    into grant_role_to_user and team-related operations.

    The password is collected via Form-mode MCP Elicitation instead of being
    passed as a tool parameter. Per the MCP specification, only URL mode
    guarantees that sensitive data does not transit through the LLM context,
    the MCP client, or intermediate systems; Form mode does not provide this
    guarantee.

    NOTE: This tool is registered only when
    ``AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true``. See README.md#security
    for the threat model.

    Args:
        username: Login username (for example, "jane.doe")
        first_name: User given name
        last_name: User family name
        email: User email address for notifications
        is_superuser: Whether to grant full AWX administrative privileges
        is_system_auditor: Whether to grant read-only system auditor access
    """
    result = await ctx.elicit(
        message=f"Please provide the password for the new user '{username}'.",
        schema=PasswordInput,
    )
    if result.action != "accept":
        return json.dumps(
            {"status": "cancelled", "message": "Password input was declined"}
        )

    with get_ansible_client() as client:
        data = {
            "username": username,
            "password": result.data.password,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "is_superuser": is_superuser,
            "is_system_auditor": is_system_auditor,
        }
        response = client.request("POST", "/api/v2/users/", data=data)
        return json.dumps(response, indent=2)


@maybe_credential_management_tool
async def update_user(
    user_id: int,
    ctx: Context,
    username: str = None,
    update_password: bool = False,
    first_name: str = None,
    last_name: str = None,
    email: str = None,
    is_superuser: bool = None,
    is_system_auditor: bool = None,
) -> str:
    """Update an AWX user account.

    Use this to modify identity, credentials, or privilege flags for an
    existing user without recreating it. Returns the updated user object;
    call get_user to verify the current final state.

    If update_password is True, the new password is collected via Form-mode
    MCP Elicitation instead of being passed as a tool parameter. Per the MCP
    specification, only URL mode guarantees that sensitive data does not
    transit through the LLM context, the MCP client, or intermediate systems;
    Form mode does not provide this guarantee.

    NOTE: This tool is registered only when
    ``AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true``. See README.md#security
    for the threat model.

    Args:
        user_id: ID of the user (from list_users)
        username: New username
        update_password: Whether to update the password (will prompt securely)
        first_name: New first name
        last_name: New last name
        email: New email address
        is_superuser: Whether the user should have superuser privileges
        is_system_auditor: Whether the user should have system auditor privileges
    """
    password = None
    if update_password:
        result = await ctx.elicit(
            message="Please provide the new password for this user.",
            schema=PasswordInput,
        )
        if result.action != "accept":
            return json.dumps(
                {"status": "cancelled", "message": "Password input was declined"}
            )
        password = result.data.password

    with get_ansible_client() as client:
        data: dict[str, Any] = {}
        if username is not None:
            data["username"] = username
        if password is not None:
            data["password"] = password
        if first_name is not None:
            data["first_name"] = first_name
        if last_name is not None:
            data["last_name"] = last_name
        if email is not None:
            data["email"] = email
        if is_superuser is not None:
            data["is_superuser"] = is_superuser
        if is_system_auditor is not None:
            data["is_system_auditor"] = is_system_auditor

        response = client.request("PATCH", f"/api/v2/users/{user_id}/", data=data)
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_user(user_id: int) -> str:
    """Delete an AWX user account.

    WARNING: This permanently removes the user and can break ownership or
    direct role assignments tied to that account. Confirm with get_user before
    deletion and reassign access as needed.

    Args:
        user_id: ID of the user (from list_users)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/users/{user_id}/")
        return json.dumps({"status": "success", "message": f"User {user_id} deleted"})

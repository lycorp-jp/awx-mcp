# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Credential Management Tools
"""

import json

from mcp.server.fastmcp import Context
from pydantic import BaseModel

from ..client import get_ansible_client, handle_pagination
from ..server import maybe_credential_management_tool, read_tool, write_tool
from ..utils import parse_json_str


class CredentialInputs(BaseModel):
    inputs: str


@read_tool
def list_credentials(limit: int = 20, offset: int = 0) -> str:
    """List AWX credentials.

    Returns credential IDs, types, and ownership context used by projects,
    inventory sources, job templates, and execution environments. Use
    credential_id values with get_credential, create_project, and
    create_inventory_source.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        credentials = handle_pagination(
            client, "/api/v2/credentials/", params, with_meta=True
        )
        return json.dumps(credentials, indent=2)


@read_tool
def get_credential(credential_id: int) -> str:
    """Get details for one AWX credential.

    Use this when you need credential metadata and input structure before
    assigning or updating it in other AWX resources. For credential discovery,
    use list_credentials first.

    Args:
        credential_id: ID of the credential (from list_credentials response)
    """
    with get_ansible_client() as client:
        credential = client.request("GET", f"/api/v2/credentials/{credential_id}/")
        return json.dumps(credential, indent=2)


@read_tool
def list_credential_types(limit: int = 20, offset: int = 0) -> str:
    """List AWX credential types.

    Returns available credential schemas such as machine, SCM, cloud, and
    vault types. Use returned credential_type_id values with create_credential.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        credential_types = handle_pagination(
            client, "/api/v2/credential_types/", params, with_meta=True
        )
        return json.dumps(credential_types, indent=2)


@maybe_credential_management_tool
async def create_credential(
    name: str,
    credential_type_id: int,
    organization_id: int,
    ctx: Context,
    description: str = "",
) -> str:
    """Create an AWX credential.

    Use this to register authentication material for machine access, SCM sync,
    cloud inventory sync, or other AWX integrations. Returns credential_id for
    use with project, inventory source, and template association tools.

    Sensitive credential inputs (e.g., username, password, ssh_key) are
    collected via Form-mode MCP Elicitation instead of being passed as tool
    parameters. Per the MCP specification, only URL mode guarantees that
    sensitive data does not transit through the LLM context, the MCP client,
    or intermediate systems; Form mode does not provide this guarantee.

    NOTE: This tool is registered only when
    ``AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true``. See README.md#security
    for the threat model.

    Args:
        name: Name of the credential
        credential_type_id: ID of the credential type
            (from list_credential_types response)
        organization_id: ID of the organization (from list_organizations response)
        description: Description of the credential
    """
    result = await ctx.elicit(
        message=(
            "Please provide the credential inputs as a JSON string "
            '(e.g., {"username": "admin", "password": "secret"}).'
        ),
        schema=CredentialInputs,
    )
    if result.action != "accept":
        return json.dumps(
            {"status": "cancelled", "message": "Credential input was declined"}
        )

    parsed_inputs, error = parse_json_str(result.data.inputs, "inputs")
    if error:
        return error

    with get_ansible_client() as client:
        data = {
            "name": name,
            "credential_type": credential_type_id,
            "organization": organization_id,
            "inputs": parsed_inputs,
            "description": description,
        }

        response = client.request("POST", "/api/v2/credentials/", data=data)
        return json.dumps(response, indent=2)


@maybe_credential_management_tool
async def update_credential(
    credential_id: int,
    ctx: Context,
    name: str = None,
    description: str = None,
    update_inputs: bool = False,
) -> str:
    """Update AWX credential metadata or inputs.

    Use this to rotate secrets or rename an existing credential without
    recreating references. For creating a new credential record, use
    create_credential.

    If update_inputs is True, sensitive credential inputs are collected via
    Form-mode MCP Elicitation instead of being passed as tool parameters.
    Per the MCP specification, only URL mode guarantees that sensitive data
    does not transit through the LLM context, the MCP client, or intermediate
    systems; Form mode does not provide this guarantee.

    NOTE: This tool is registered only when
    ``AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true``. See README.md#security
    for the threat model.

    Args:
        credential_id: ID of the credential (from list_credentials response)
        name: New name for the credential
        description: New description
        update_inputs: Whether to update credential inputs (will prompt securely)
    """
    parsed_inputs = None
    if update_inputs:
        result = await ctx.elicit(
            message=(
                "Please provide the new credential inputs as a JSON string "
                '(e.g., {"username": "admin", "password": "secret"}).'
            ),
            schema=CredentialInputs,
        )
        if result.action != "accept":
            return json.dumps(
                {"status": "cancelled", "message": "Credential input was declined"}
            )
        parsed_inputs, error = parse_json_str(result.data.inputs, "inputs")
        if error:
            return error

    with get_ansible_client() as client:
        data = {}
        if name is not None:
            data["name"] = name
        if parsed_inputs is not None:
            data["inputs"] = parsed_inputs
        if description is not None:
            data["description"] = description

        response = client.request(
            "PATCH", f"/api/v2/credentials/{credential_id}/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_credential(credential_id: int) -> str:
    """Delete an AWX credential.

    WARNING: This permanently removes the credential object and can break
    project syncs, inventory source syncs, or job launches that reference it.
    Check dependencies with get_credential before deletion.

    Args:
        credential_id: ID of the credential (from list_credentials response)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/credentials/{credential_id}/")
        return json.dumps(
            {"status": "success", "message": f"Credential {credential_id} deleted"}
        )


@write_tool()
def copy_credential(credential_id: int, new_name: str) -> str:
    """Copy an AWX credential.

    Use this to clone an existing credential configuration for safe variation
    or staged rotation. Returns a new credential_id you can attach to projects
    or inventory sources.

    Args:
        credential_id: ID of the credential to copy (from list_credentials response)
        new_name: Name for the new copy
    """
    with get_ansible_client() as client:
        data = {"name": new_name}
        response = client.request(
            "POST", f"/api/v2/credentials/{credential_id}/copy/", data=data
        )
        return json.dumps(response, indent=2)

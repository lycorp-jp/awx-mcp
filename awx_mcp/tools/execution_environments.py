# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Execution Environment Tools
"""

import json
from typing import Any

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool


@read_tool
def list_execution_environments(limit: int = 20, offset: int = 0) -> str:
    """List AWX execution environments.

    Use this to discover containerized runtimes available for job execution.
    Returns execution environment IDs and image metadata for chaining into
    get_execution_environment, update_execution_environment, or templates.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of execution environment results to return
        offset: Number of execution environment results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        envs = handle_pagination(
            client, "/api/v2/execution_environments/", params, with_meta=True
        )
        return json.dumps(envs, indent=2)


@read_tool
def get_execution_environment(ee_id: int) -> str:
    """Get details for one AWX execution environment.

    Use this when you need exact runtime image, pull policy, or credential
    linkage for an EE referenced by job templates. Pair with
    list_execution_environments to discover EE IDs.

    Args:
        ee_id: ID of the execution environment (from list_execution_environments)
    """
    with get_ansible_client() as client:
        ee = client.request("GET", f"/api/v2/execution_environments/{ee_id}/")
        return json.dumps(ee, indent=2)


@write_tool()
def create_execution_environment(
    name: str,
    image: str,
    organization_id: int = None,
    credential_id: int = None,
    description: str = "",
    pull: str = None,
) -> str:
    """Create an AWX execution environment.

    Defines a container image runtime used by AWX job templates to run
    playbooks and collections. Returns the created EE object (including id)
    for assignment in template configuration workflows.

    Args:
        name: Execution environment name in AWX
        image: Container image reference (e.g., quay.io/ansible/awx-ee:latest)
        organization_id: Optional owning organization ID (from list_organizations)
        credential_id: Optional registry credential ID (from list_credentials)
        description: Execution environment description
        pull: Image pull policy ("always", "missing", or "never")
    """
    with get_ansible_client() as client:
        data: dict[str, Any] = {
            "name": name,
            "image": image,
            "description": description,
        }
        if organization_id is not None:
            data["organization"] = organization_id
        if credential_id is not None:
            data["credential"] = credential_id
        if pull is not None:
            data["pull"] = pull
        response = client.request("POST", "/api/v2/execution_environments/", data=data)
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def update_execution_environment(
    ee_id: int,
    name: str = None,
    image: str = None,
    credential_id: int = None,
    description: str = None,
    pull: str = None,
) -> str:
    """Update an AWX execution environment.

    Use this to change EE runtime image, pull behavior, or registry credential
    without creating a new EE object. Returns the updated EE record; verify
    effective runtime fields with get_execution_environment.

    Args:
        ee_id: ID of the execution environment (from list_execution_environments)
        name: New execution environment name
        image: New container image reference
        credential_id: New registry credential ID (from list_credentials)
        description: New execution environment description
        pull: Image pull policy ("always", "missing", or "never")
    """
    with get_ansible_client() as client:
        data: dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if image is not None:
            data["image"] = image
        if credential_id is not None:
            data["credential"] = credential_id
        if description is not None:
            data["description"] = description
        if pull is not None:
            data["pull"] = pull
        response = client.request(
            "PATCH", f"/api/v2/execution_environments/{ee_id}/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_execution_environment(ee_id: int) -> str:
    """Delete an AWX execution environment.

    WARNING: This permanently removes the runtime definition and any template
    depending on it may fail until reassigned. Confirm target details with
    get_execution_environment before deletion.

    Args:
        ee_id: ID of the execution environment (from list_execution_environments)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/execution_environments/{ee_id}/")
        return json.dumps(
            {"status": "success", "message": f"Execution environment {ee_id} deleted"}
        )

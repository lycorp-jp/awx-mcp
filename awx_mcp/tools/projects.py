# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Project Management Tools

Project CRUD, SCM sync, playbook listing, and project update monitoring.
"""

import json

from ..client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    get_ansible_client,
    handle_pagination,
)
from ..exceptions import AnsibleHTTPError
from ..server import read_tool, write_tool


@read_tool
def list_projects(limit: int = 100, offset: int = 0) -> str:
    """List AWX projects.

    Returns SCM project records that supply playbooks for job templates and
    workflows. Use returned project_id values with sync_project,
    list_project_playbooks, and create_job_template.

    Args:
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        projects = handle_pagination(client, "/api/v2/projects/", params)
        return json.dumps(projects, indent=2)


@read_tool
def get_project(project_id: int) -> str:
    """Get details for one AWX project.

    Use this when you need SCM settings, organization linkage, and update
    status for a specific project. For project discovery across AWX,
    use list_projects first.

    Args:
        project_id: ID of the project (from list_projects response)
    """
    with get_ansible_client() as client:
        project = client.request("GET", f"/api/v2/projects/{project_id}/")
        return json.dumps(project, indent=2)


@write_tool()
def create_project(
    name: str,
    organization_id: int,
    scm_type: str,
    scm_url: str = None,
    scm_branch: str = None,
    credential_id: int = None,
    description: str = "",
) -> str:
    """Create an AWX project for playbook content.

    Use this to register an SCM repository (or manual project) under an AWX
    organization. Returns project_id for sync_project and
    list_project_playbooks.

    Args:
        name: Name of the project
        organization_id: ID of the organization (from list_organizations response)
        scm_type: SCM type (git, hg, svn, manual)
        scm_url: URL for the repository
        scm_branch: Branch/tag/commit to checkout
        credential_id: ID of the credential for SCM access
            (from list_credentials response)
        description: Description of the project
    """
    if scm_type not in ["", "git", "hg", "svn", "manual"]:
        return json.dumps(
            {
                "status": "error",
                "message": "Invalid SCM type. Must be one of: git, hg, svn, manual",
            }
        )

    if scm_type != "manual" and not scm_url:
        return json.dumps(
            {
                "status": "error",
                "message": "SCM URL is required for non-manual SCM types",
            }
        )

    with get_ansible_client() as client:
        data = {
            "name": name,
            "organization": organization_id,
            "scm_type": scm_type,
            "description": description,
        }

        if scm_url is not None:
            data["scm_url"] = scm_url
        if scm_branch is not None:
            data["scm_branch"] = scm_branch
        if credential_id is not None:
            data["credential"] = credential_id

        response = client.request("POST", "/api/v2/projects/", data=data)
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def update_project(
    project_id: int,
    name: str = None,
    scm_type: str = None,
    scm_url: str = None,
    scm_branch: str = None,
    description: str = None,
) -> str:
    """Update AWX project metadata and SCM settings.

    Use this to adjust repository details, branch targeting, or project naming
    for an existing AWX project. For pulling latest SCM content after updates,
    use sync_project.

    Args:
        project_id: ID of the project (from list_projects response)
        name: New name for the project
        scm_type: New SCM type (git, hg, svn, manual)
        scm_url: New URL for the repository
        scm_branch: New branch/tag/commit to checkout
        description: New description
    """
    if scm_type is not None and scm_type not in ["", "git", "hg", "svn", "manual"]:
        return json.dumps(
            {
                "status": "error",
                "message": "Invalid SCM type. Must be one of: git, hg, svn, manual",
            }
        )

    with get_ansible_client() as client:
        data = {}
        if name is not None:
            data["name"] = name
        if scm_type is not None:
            data["scm_type"] = scm_type
        if scm_url is not None:
            data["scm_url"] = scm_url
        if scm_branch is not None:
            data["scm_branch"] = scm_branch
        if description is not None:
            data["description"] = description

        response = client.request("PATCH", f"/api/v2/projects/{project_id}/", data=data)
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_project(project_id: int) -> str:
    """Delete an AWX project.

    WARNING: This permanently removes the project record and its SCM sync
    history reference in AWX. Verify dependencies (such as job templates) and
    confirm target with get_project before deletion.

    Args:
        project_id: ID of the project (from list_projects response)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/projects/{project_id}/")
        return json.dumps(
            {"status": "success", "message": f"Project {project_id} deleted"}
        )


@write_tool(destructive=True)
def sync_project(project_id: int) -> str:
    """Start an AWX project SCM sync.

    Use this to fetch playbook content from a project's SCM source into AWX.
    For dynamic host discovery syncs, use sync_inventory_source instead.
    Returns a project update record trackable via get_project_update.

    Args:
        project_id: ID of the project (from list_projects response)
    """
    with get_ansible_client() as client:
        response = client.request("POST", f"/api/v2/projects/{project_id}/update/")
        return json.dumps(response, indent=2)


# =============================================================================
# Project Playbooks (NEW)
# =============================================================================


@read_tool
def list_project_playbooks(project_id: int) -> str:
    """List AWX playbooks discovered in a project.

    Use this after sync_project to see playbook filenames currently available
    for job template configuration. For SCM sync run history, use
    list_project_updates.

    Args:
        project_id: ID of the project (from list_projects response)
    """
    with get_ansible_client() as client:
        playbooks = client.request("GET", f"/api/v2/projects/{project_id}/playbooks/")
        return json.dumps(playbooks, indent=2)


# =============================================================================
# Project Update Monitoring (NEW)
# =============================================================================


@read_tool
def list_project_updates(
    project_id: int, limit: int = 100, offset: int = 0, order_by: str = "-created"
) -> str:
    """List AWX project SCM sync history.

    Newest first by default (order_by="-created"); AWX's own default is
    oldest-first.

    Returns update records for repository fetch/update runs on a project. For
    inventory source host-discovery sync history, use list_inventory_updates
    instead.

    Args:
        project_id: ID of the project (from list_projects response)
        limit: Maximum number of results to return
        offset: Number of results to skip
        order_by: Sort field; prefix with "-" for descending
            (e.g. -created, created, -finished, id, status)
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset, "order_by": order_by}
        updates = handle_pagination(
            client, f"/api/v2/projects/{project_id}/project_updates/", params
        )
        return json.dumps(updates, indent=2)


@read_tool
def get_project_update(update_id: int) -> str:
    """Get details for one AWX project update.

    Use this to inspect status, timing, and result metadata for an SCM sync
    run. For inventory source sync runs, use get_inventory_update instead.

    Args:
        update_id: ID of the project update (from list_project_updates response)
    """
    with get_ansible_client() as client:
        update = client.request("GET", f"/api/v2/project_updates/{update_id}/")
        return json.dumps(update, indent=2)


@write_tool(destructive=True)
def cancel_project_update(update_id: int) -> str:
    """Cancel a running AWX project update.

    WARNING: Cancels an in-flight SCM sync and may leave project content only
    partially refreshed for that run. Use get_project_update to verify the
    final canceled state.

    Args:
        update_id: ID of the project update (from list_project_updates response)
    """
    with get_ansible_client() as client:
        response = client.request(
            "POST", f"/api/v2/project_updates/{update_id}/cancel/"
        )
        return json.dumps(response, indent=2)


@read_tool
def get_project_update_stdout(update_id: int, format: str = "txt") -> str:
    """Get AWX stdout logs for a project update.

    Use this when sync status alone is not enough and you need detailed SCM
    fetch output for troubleshooting. Pair with get_project_update to locate
    the relevant update_id and status.

    Args:
        update_id: ID of the project update (from list_project_updates response)
        format: Output format (txt, html, json, ansi)
    """
    valid_formats = ["txt", "html", "json", "ansi", "txt_download", "ansi_download"]
    if format not in valid_formats:
        return json.dumps(
            {
                "status": "error",
                "message": (
                    f"Invalid format. Must be one of: {', '.join(valid_formats)}"
                ),
            }
        )

    with get_ansible_client() as client:
        if format != "json":
            url = (
                f"{client.base_url}/api/v2/project_updates/{update_id}"
                f"/stdout/?format={format}"
            )
            client._validate_url(url)
            response = client.session.get(
                url,
                headers=client.get_headers(),
                timeout=(DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT),
            )
            if response.status_code >= 400:
                raise AnsibleHTTPError(
                    f"Ansible API error: {response.status_code}"
                    f" - {response.text[:500]}",
                    status_code=response.status_code,
                )
            return json.dumps({"status": "success", "stdout": response.text})
        else:
            response = client.request(
                "GET", f"/api/v2/project_updates/{update_id}/stdout/?format={format}"
            )
            return json.dumps(response, indent=2)

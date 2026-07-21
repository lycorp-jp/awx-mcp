# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Job Template Tools

Job template CRUD, launch, copy, survey management, and credential association.
"""

import json
from typing import Any

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool
from ..utils import parse_json_str, validate_json_str


@read_tool
def list_job_templates(limit: int = 20, offset: int = 0) -> str:
    """List AWX job templates for single playbook executions.

    Use this to discover templates that launch one playbook run via launch_job.
    For multi-step orchestration definitions, use list_workflow_templates instead.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        templates = handle_pagination(
            client, "/api/v2/job_templates/", params, with_meta=True
        )
        return json.dumps(templates, indent=2)


@read_tool
def get_job_template(template_id: int) -> str:
    """Get details for a specific AWX job template.

    Use this to inspect playbook, inventory, and launch configuration before launch_job.
    For workflow template details, use get_workflow_template instead.

    Args:
        template_id: ID of the job template (from list_job_templates)
    """
    with get_ansible_client() as client:
        template = client.request("GET", f"/api/v2/job_templates/{template_id}/")
        return json.dumps(template, indent=2)


@write_tool()
def create_job_template(
    name: str,
    inventory_id: int,
    project_id: int,
    playbook: str,
    description: str = "",
    extra_vars: str = "{}",
    job_type: str = "run",
    verbosity: int = 0,
    limit: str = "",
    forks: int = 0,
    become_enabled: bool = False,
    diff_mode: bool = False,
    allow_simultaneous: bool = False,
    timeout: int = 0,
) -> str:
    """Create an AWX job template for single playbook execution.

    Use this when you need a reusable playbook run definition tied to inventory
    and project. AWX 24.x uses multi-credential attachment; connect credentials
    with associate_credential_with_template after creation.
    For multi-step orchestration definitions, use create_workflow_template instead.

    Args:
        name: Name of the job template
        inventory_id: ID of the inventory (from list_inventories)
        project_id: ID of the project (from list_projects)
        playbook: Name of the playbook (e.g., "playbook.yml")
        description: Description of the job template
        extra_vars: JSON string of extra variables
        job_type: Job type — "run" for normal execution, "check" for dry-run
        verbosity: Output verbosity level (0=Normal, 1=Verbose, 2=More, 3=Debug,
            4=Connection, 5=WinRM)
        limit: Host limit pattern (e.g., "webservers" or "host1,host2")
        forks: Number of parallel processes (0 uses AWX default)
        become_enabled: Whether to enable privilege escalation
        diff_mode: Whether to show changes in files
        allow_simultaneous: Whether to allow simultaneous runs
        timeout: Job timeout in seconds (0 for no timeout)
    """
    if job_type not in ("run", "check"):
        return json.dumps(
            {"status": "error", "message": "Invalid job_type. Must be 'run' or 'check'"}
        )
    if verbosity not in range(6):
        return json.dumps(
            {"status": "error", "message": "Invalid verbosity. Must be 0-5"}
        )

    error = validate_json_str(extra_vars, "extra_vars")
    if error:
        return error

    with get_ansible_client() as client:
        data = {
            "name": name,
            "inventory": inventory_id,
            "project": project_id,
            "playbook": playbook,
            "description": description,
            "extra_vars": extra_vars,
            "job_type": job_type,
            "verbosity": verbosity,
            "limit": limit,
            "forks": forks,
            "become_enabled": become_enabled,
            "diff_mode": diff_mode,
            "allow_simultaneous": allow_simultaneous,
            "timeout": timeout,
        }

        response = client.request("POST", "/api/v2/job_templates/", data=data)
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def update_job_template(
    template_id: int,
    name: str = None,
    inventory_id: int = None,
    project_id: int = None,
    playbook: str = None,
    description: str = None,
    extra_vars: str = None,
    job_type: str = None,
    verbosity: int = None,
    limit: str = None,
    forks: int = None,
    become_enabled: bool = None,
    diff_mode: bool = None,
    allow_simultaneous: bool = None,
    timeout: int = None,
) -> str:
    """Update an existing AWX job template.

    Use this to modify launch defaults, playbook binding, or inventory for
    future launch_job calls.
    For workflow orchestration template updates, use update_workflow_template instead.

    Args:
        template_id: ID of the job template
            (from list_job_templates or get_job_template)
        name: New name for the job template
        inventory_id: New inventory ID (from list_inventories)
        project_id: New project ID (from list_projects)
        playbook: New playbook name
        description: New description
        extra_vars: JSON string of extra variables
        job_type: Job type — "run" or "check"
        verbosity: Output verbosity level (0-5)
        limit: Host limit pattern
        forks: Number of parallel processes (0 uses AWX default)
        become_enabled: Whether to enable privilege escalation
        diff_mode: Whether to show changes in files
        allow_simultaneous: Whether to allow simultaneous runs
        timeout: Job timeout in seconds (0 for no timeout)
    """
    if job_type is not None and job_type not in ("run", "check"):
        return json.dumps(
            {"status": "error", "message": "Invalid job_type. Must be 'run' or 'check'"}
        )
    if verbosity is not None and verbosity not in range(6):
        return json.dumps(
            {"status": "error", "message": "Invalid verbosity. Must be 0-5"}
        )
    if extra_vars is not None:
        error = validate_json_str(extra_vars, "extra_vars")
        if error:
            return error

    with get_ansible_client() as client:
        data: dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if inventory_id is not None:
            data["inventory"] = inventory_id
        if project_id is not None:
            data["project"] = project_id
        if playbook is not None:
            data["playbook"] = playbook
        if description is not None:
            data["description"] = description
        if extra_vars is not None:
            data["extra_vars"] = extra_vars
        if job_type is not None:
            data["job_type"] = job_type
        if verbosity is not None:
            data["verbosity"] = verbosity
        if limit is not None:
            data["limit"] = limit
        if forks is not None:
            data["forks"] = forks
        if become_enabled is not None:
            data["become_enabled"] = become_enabled
        if diff_mode is not None:
            data["diff_mode"] = diff_mode
        if allow_simultaneous is not None:
            data["allow_simultaneous"] = allow_simultaneous
        if timeout is not None:
            data["timeout"] = timeout

        response = client.request(
            "PATCH", f"/api/v2/job_templates/{template_id}/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_job_template(template_id: int) -> str:
    """Delete an AWX job template.

    WARNING: This permanently removes the job template and it cannot be used
    for future launch_job calls.
    This action cannot be undone.
    For removing workflow orchestration templates, use delete_workflow_template.

    Args:
        template_id: ID of the job template (from list_job_templates)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/job_templates/{template_id}/")
        return json.dumps(
            {"status": "success", "message": f"Job template {template_id} deleted"}
        )


@write_tool(destructive=True)
def launch_job(
    template_id: int,
    extra_vars: str = None,
    inventory: int = None,
    credential: int = None,
    limit: str = None,
    job_tags: str = None,
    skip_tags: str = None,
    verbosity: int = None,
    scm_branch: str = None,
    diff_mode: bool = None,
) -> str:
    """Launch a single AWX playbook execution from a job template.

    Starts an asynchronous run and returns a job object with a job_id for tracking.
    Track progress with get_job and fetch logs with get_job_stdout.
    For multi-step orchestration, use launch_workflow; for maintenance tasks,
    use launch_system_job.

    Args:
        template_id: ID of the job template
            (from list_job_templates or get_job_template)
        extra_vars: JSON string of extra variables to override the template's variables
        inventory: Override inventory ID (from list_inventories)
        credential: Override credential ID (from list_credentials)
        limit: Host limit pattern (e.g., "webservers" or "host1,host2")
        job_tags: Comma-separated tags to run (e.g., "setup,deploy")
        skip_tags: Comma-separated tags to skip (e.g., "cleanup")
        verbosity: Override verbosity level (0-5)
        scm_branch: Override SCM branch for the project
        diff_mode: Override diff mode setting
    """
    if extra_vars is not None:
        error = validate_json_str(extra_vars, "extra_vars")
        if error:
            return error

    with get_ansible_client() as client:
        data: dict[str, Any] = {}
        if extra_vars is not None:
            data["extra_vars"] = extra_vars
        if inventory is not None:
            data["inventory"] = inventory
        if credential is not None:
            data["credentials"] = [credential]
        if limit is not None:
            data["limit"] = limit
        if job_tags is not None:
            data["job_tags"] = job_tags
        if skip_tags is not None:
            data["skip_tags"] = skip_tags
        if verbosity is not None:
            data["verbosity"] = verbosity
        if scm_branch is not None:
            data["scm_branch"] = scm_branch
        if diff_mode is not None:
            data["diff_mode"] = diff_mode

        response = client.request(
            "POST", f"/api/v2/job_templates/{template_id}/launch/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool()
def copy_job_template(template_id: int, new_name: str) -> str:
    """Create a copy of an AWX job template.

    Use this to clone a template as a starting point for a new single-playbook
    execution path.
    Returns the new template object for follow-up with update_job_template or
    launch_job.

    Args:
        template_id: ID of the job template to copy (from list_job_templates)
        new_name: Name for the new copy
    """
    with get_ansible_client() as client:
        data = {"name": new_name}
        response = client.request(
            "POST", f"/api/v2/job_templates/{template_id}/copy/", data=data
        )
        return json.dumps(response, indent=2)


# =============================================================================
# Survey Management
# =============================================================================


@read_tool
def get_job_template_survey(template_id: int) -> str:
    """Get the survey spec for an AWX job template.

    Use this to inspect prompt questions required at launch_job time.
    For workflow template surveys, use get_workflow_template_survey.

    Args:
        template_id: ID of the job template
            (from list_job_templates or get_job_template)
    """
    with get_ansible_client() as client:
        survey = client.request(
            "GET", f"/api/v2/job_templates/{template_id}/survey_spec/"
        )
        return json.dumps(survey, indent=2)


@write_tool(idempotent=True)
def set_job_template_survey(template_id: int, survey_spec: str) -> str:
    """Set the survey spec for an AWX job template.

    Use this to define or replace launch-time prompts for launch_job.
    Validate current survey first with get_job_template_survey when updating
    existing questions.

    Args:
        template_id: ID of the job template
            (from list_job_templates or get_job_template)
        survey_spec: JSON string of the survey spec
            (must include name, description, spec array)
    """
    spec, error = parse_json_str(survey_spec, "survey_spec")
    if error:
        return error

    with get_ansible_client() as client:
        response = client.request(
            "POST", f"/api/v2/job_templates/{template_id}/survey_spec/", data=spec
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_job_template_survey(template_id: int) -> str:
    """Delete the survey spec from an AWX job template.

    WARNING: This permanently removes launch-time survey prompts from the template.
    This action cannot be undone.
    For workflow template surveys, use delete_workflow_template_survey instead.

    Args:
        template_id: ID of the job template
            (from list_job_templates or get_job_template)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/job_templates/{template_id}/survey_spec/")
        return json.dumps(
            {
                "status": "success",
                "message": f"Survey deleted from job template {template_id}",
            }
        )


# =============================================================================
# Credential Association (NEW)
# =============================================================================


@read_tool
def list_template_credentials(
    template_id: int, limit: int = 20, offset: int = 0
) -> str:
    """List credentials attached to an AWX job template.

    Use this to verify credential associations that launch_job will use at runtime.
    Returns credential IDs for disassociate_credential_from_template when
    cleanup is needed.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        template_id: ID of the job template
            (from list_job_templates or get_job_template)
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        credentials = handle_pagination(
            client,
            f"/api/v2/job_templates/{template_id}/credentials/",
            params,
            with_meta=True,
        )
        return json.dumps(credentials, indent=2)


@write_tool(idempotent=True)
def associate_credential_with_template(template_id: int, credential_id: int) -> str:
    """Associate a credential with an AWX job template.

    Use this after create_job_template or update_job_template to attach runtime
    auth material.
    Confirm current links with list_template_credentials before and after changes.

    Args:
        template_id: ID of the job template
            (from list_job_templates or get_job_template)
        credential_id: ID of the credential to associate (from list_credentials)
    """
    with get_ansible_client() as client:
        data = {"id": credential_id}
        response = client.request(
            "POST", f"/api/v2/job_templates/{template_id}/credentials/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def disassociate_credential_from_template(template_id: int, credential_id: int) -> str:
    """Disassociate a credential from an AWX job template.

    Use this to remove a credential from future launch_job executions without
    deleting the credential object.
    Check existing links first with list_template_credentials.

    Args:
        template_id: ID of the job template
            (from list_job_templates or get_job_template)
        credential_id: ID of the credential to disassociate
            (from list_template_credentials)
    """
    with get_ansible_client() as client:
        data = {"id": credential_id, "disassociate": True}
        client.request(
            "POST", f"/api/v2/job_templates/{template_id}/credentials/", data=data
        )
        return json.dumps(
            {
                "status": "success",
                "message": (
                    f"Credential {credential_id} disassociated from"
                    f" template {template_id}"
                ),
            }
        )

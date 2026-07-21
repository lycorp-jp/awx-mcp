# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Workflow Template & Node Tools

Workflow job template CRUD, survey management, copy, launch, and node management.
"""

import json
from typing import Any

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool
from ..utils import parse_json_str, validate_json_str

# =============================================================================
# Workflow Template Management
# =============================================================================


@read_tool
def list_workflow_templates(limit: int = 20, offset: int = 0) -> str:
    """List AWX workflow templates for multi-step orchestration.

    Use this to discover orchestration definitions that chain multiple nodes
    and conditions.
    For single-playbook execution templates, use list_job_templates instead.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        envelope = handle_pagination(
            client, "/api/v2/workflow_job_templates/", params, with_meta=True
        )
        return json.dumps(envelope, indent=2)


@read_tool
def get_workflow_template(template_id: int) -> str:
    """Get details for a specific AWX workflow template.

    Use this to inspect orchestration settings and readiness before launch_workflow.
    For single-playbook template details, use get_job_template instead.

    Args:
        template_id: ID of the workflow template (from list_workflow_templates)
    """
    with get_ansible_client() as client:
        template = client.request(
            "GET", f"/api/v2/workflow_job_templates/{template_id}/"
        )
        return json.dumps(template, indent=2)


@write_tool()
def create_workflow_template(
    name: str,
    organization_id: int,
    description: str = "",
    extra_vars: str = "{}",
    survey_enabled: bool = False,
    allow_simultaneous: bool = False,
    inventory: int = None,
    limit: str = None,
    scm_branch: str = None,
) -> str:
    """Create an AWX workflow template for multi-step orchestration.

    Use this when you need conditional branching or chained job execution
    across multiple nodes.
    For single-playbook template creation, use create_job_template instead.

    Args:
        name: Name of the workflow template
        organization_id: ID of the organization (from list_organizations)
        description: Description of the workflow template
        extra_vars: JSON string of extra variables
        survey_enabled: Whether to enable survey
        allow_simultaneous: Whether to allow simultaneous runs
        inventory: Default inventory ID for nodes that prompt on launch
            (from list_inventories)
        limit: Default host limit pattern for nodes that prompt on launch
        scm_branch: Default SCM branch for nodes that prompt on launch
    """
    error = validate_json_str(extra_vars, "extra_vars")
    if error:
        return error

    with get_ansible_client() as client:
        data = {
            "name": name,
            "organization": organization_id,
            "description": description,
            "extra_vars": extra_vars,
            "survey_enabled": survey_enabled,
            "allow_simultaneous": allow_simultaneous,
        }
        if inventory is not None:
            data["inventory"] = inventory
        if limit is not None:
            data["limit"] = limit
        if scm_branch is not None:
            data["scm_branch"] = scm_branch
        response = client.request("POST", "/api/v2/workflow_job_templates/", data=data)
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def update_workflow_template(
    template_id: int,
    name: str = None,
    description: str = None,
    extra_vars: str = None,
    survey_enabled: bool = None,
    allow_simultaneous: bool = None,
    inventory: int = None,
    limit: str = None,
    scm_branch: str = None,
) -> str:
    """Update an existing AWX workflow template.

    Use this to change orchestration-level defaults used by future launch_workflow runs.
    For single-playbook template updates, use update_job_template instead.

    Args:
        template_id: ID of the workflow template
            (from list_workflow_templates or get_workflow_template)
        name: New name for the workflow template
        description: New description
        extra_vars: JSON string of extra variables
        survey_enabled: Whether to enable survey
        allow_simultaneous: Whether to allow simultaneous runs
        inventory: Default inventory ID for nodes that prompt on launch
            (from list_inventories)
        limit: Default host limit pattern for nodes that prompt on launch
        scm_branch: Default SCM branch for nodes that prompt on launch
    """
    if extra_vars is not None:
        error = validate_json_str(extra_vars, "extra_vars")
        if error:
            return error

    with get_ansible_client() as client:
        data: dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if extra_vars is not None:
            data["extra_vars"] = extra_vars
        if survey_enabled is not None:
            data["survey_enabled"] = survey_enabled
        if allow_simultaneous is not None:
            data["allow_simultaneous"] = allow_simultaneous
        if inventory is not None:
            data["inventory"] = inventory
        if limit is not None:
            data["limit"] = limit
        if scm_branch is not None:
            data["scm_branch"] = scm_branch

        response = client.request(
            "PATCH", f"/api/v2/workflow_job_templates/{template_id}/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_workflow_template(template_id: int) -> str:
    """Delete an AWX workflow template.

    WARNING: This permanently removes the orchestration definition and it
    cannot be launched again.
    This action cannot be undone.
    For single-playbook templates, use delete_job_template instead.

    Args:
        template_id: ID of the workflow template (from list_workflow_templates)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/workflow_job_templates/{template_id}/")
        return json.dumps(
            {"status": "success", "message": f"Workflow template {template_id} deleted"}
        )


@write_tool(destructive=True)
def launch_workflow(
    template_id: int,
    extra_vars: str = None,
    inventory: int = None,
    limit: str = None,
    scm_branch: str = None,
) -> str:
    """Launch an AWX multi-step workflow from a workflow template.

    Starts an asynchronous orchestration run and returns a workflow job object
    with a job_id for tracking.
    Track execution with get_workflow_job and inspect node outcomes with
    list_workflow_job_nodes.
    For single-playbook execution, use launch_job instead.

    Args:
        template_id: ID of the workflow template
            (from list_workflow_templates or get_workflow_template)
        extra_vars: JSON string of extra variables to override the template's variables
        inventory: Override inventory ID (from list_inventories)
        limit: Override host limit pattern
        scm_branch: Override SCM branch for projects in the workflow
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
        if limit is not None:
            data["limit"] = limit
        if scm_branch is not None:
            data["scm_branch"] = scm_branch

        response = client.request(
            "POST", f"/api/v2/workflow_job_templates/{template_id}/launch/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool()
def copy_workflow_template(template_id: int, new_name: str) -> str:
    """Create a copy of an AWX workflow template.

    Use this to clone an orchestration definition before modifying nodes or links.
    Returns the new template object for follow-up with update_workflow_template
    or list_workflow_template_nodes.

    Args:
        template_id: ID of the workflow template to copy (from list_workflow_templates)
        new_name: Name for the new copy
    """
    with get_ansible_client() as client:
        data = {"name": new_name}
        response = client.request(
            "POST", f"/api/v2/workflow_job_templates/{template_id}/copy/", data=data
        )
        return json.dumps(response, indent=2)


# =============================================================================
# Workflow Template Survey
# =============================================================================


@read_tool
def get_workflow_template_survey(template_id: int) -> str:
    """Get the survey spec for an AWX workflow template.

    Use this to inspect launch prompts collected before launch_workflow runs.
    For job-template surveys, use get_job_template_survey instead.

    Args:
        template_id: ID of the workflow job template
            (from list_workflow_templates or get_workflow_template)
    """
    with get_ansible_client() as client:
        survey = client.request(
            "GET", f"/api/v2/workflow_job_templates/{template_id}/survey_spec/"
        )
        return json.dumps(survey, indent=2)


@write_tool(idempotent=True)
def set_workflow_template_survey(template_id: int, survey_spec: str) -> str:
    """Set the survey spec for an AWX workflow template.

    Use this to define or replace launch-time prompts for launch_workflow.
    Review existing prompts with get_workflow_template_survey before updating.

    Args:
        template_id: ID of the workflow job template
            (from list_workflow_templates or get_workflow_template)
        survey_spec: JSON string of the survey spec
            (must include name, description, spec array)
    """
    spec, error = parse_json_str(survey_spec, "survey_spec")
    if error:
        return error

    with get_ansible_client() as client:
        response = client.request(
            "POST",
            f"/api/v2/workflow_job_templates/{template_id}/survey_spec/",
            data=spec,
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_workflow_template_survey(template_id: int) -> str:
    """Delete the survey spec from an AWX workflow template.

    WARNING: This permanently removes launch-time workflow prompts.
    This action cannot be undone.
    For job-template survey deletion, use delete_job_template_survey instead.

    Args:
        template_id: ID of the workflow job template
            (from list_workflow_templates or get_workflow_template)
    """
    with get_ansible_client() as client:
        client.request(
            "DELETE", f"/api/v2/workflow_job_templates/{template_id}/survey_spec/"
        )
        return json.dumps(
            {
                "status": "success",
                "message": f"Survey deleted from workflow template {template_id}",
            }
        )


# =============================================================================
# Workflow Job Template Nodes
# =============================================================================


@read_tool
def list_workflow_template_nodes(
    template_id: int, limit: int = 20, offset: int = 0
) -> str:
    """List nodes defined in an AWX workflow template.

    Use this to inspect orchestration topology, including node IDs needed for
    link management.
    For executed-node outcomes from a running workflow, use
    list_workflow_job_nodes instead.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        template_id: ID of the workflow template
            (from list_workflow_templates or get_workflow_template)
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        envelope = handle_pagination(
            client,
            f"/api/v2/workflow_job_templates/{template_id}/workflow_nodes/",
            params,
            with_meta=True,
        )
        return json.dumps(envelope, indent=2)


@read_tool
def get_workflow_template_node(node_id: int) -> str:
    """Get details for a specific AWX workflow template node.

    Use this to inspect node configuration, linked templates, and identifier metadata.
    For runtime execution data of nodes, use list_workflow_job_nodes instead.

    Args:
        node_id: ID of the workflow template node (from list_workflow_template_nodes)
    """
    with get_ansible_client() as client:
        node = client.request("GET", f"/api/v2/workflow_job_template_nodes/{node_id}/")
        return json.dumps(node, indent=2)


@write_tool()
def create_workflow_template_node(
    workflow_template_id: int,
    unified_job_template_id: int,
    identifier: str = None,
    all_parents_must_converge: bool = False,
    extra_data: str = "{}",
) -> str:
    """Create a node in an AWX workflow template.

    Use this to add a job, project update, approval, or other unified template
    step to orchestration.
    Returns the new node object and node_id for linking with
    add_workflow_node_success_link, add_workflow_node_failure_link, or
    add_workflow_node_always_link.

    Args:
        workflow_template_id: ID of the workflow template
            (from list_workflow_templates or get_workflow_template)
        unified_job_template_id: ID of the job template or other template to
            run at this node (from list_job_templates, list_workflow_templates,
            list_inventory_sources, etc.)
        identifier: Optional unique identifier for this node
        all_parents_must_converge: Whether all parent nodes must succeed before
            this node runs
        extra_data: JSON string of extra variables for this node
    """
    parsed_extra, error = parse_json_str(extra_data, "extra_data")
    if error:
        return error

    with get_ansible_client() as client:
        data = {
            "workflow_job_template": workflow_template_id,
            "unified_job_template": unified_job_template_id,
            "all_parents_must_converge": all_parents_must_converge,
            "extra_data": parsed_extra,
        }
        if identifier is not None:
            data["identifier"] = identifier
        response = client.request(
            "POST", "/api/v2/workflow_job_template_nodes/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_workflow_template_node(node_id: int) -> str:
    """Delete an AWX workflow template node.

    WARNING: This permanently removes the node and its link relationships from
    the orchestration graph.
    This action cannot be undone.
    Re-list topology with list_workflow_template_nodes before deleting dependent links.

    Args:
        node_id: ID of the workflow template node (from list_workflow_template_nodes)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/workflow_job_template_nodes/{node_id}/")
        return json.dumps(
            {
                "status": "success",
                "message": f"Workflow template node {node_id} deleted",
            }
        )


@write_tool(idempotent=True)
def add_workflow_node_success_link(node_id: int, target_node_id: int) -> str:
    """Add an AWX workflow success edge between template nodes.

    Use this to run the target node only when the source node succeeds.
    For failure-only or always-run paths, use add_workflow_node_failure_link or
    add_workflow_node_always_link.

    Args:
        node_id: ID of the source workflow template node
            (from list_workflow_template_nodes)
        target_node_id: ID of the target workflow template node to run on
            success (from list_workflow_template_nodes)
    """
    with get_ansible_client() as client:
        data = {"id": target_node_id}
        response = client.request(
            "POST",
            f"/api/v2/workflow_job_template_nodes/{node_id}/success_nodes/",
            data=data,
        )
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def add_workflow_node_failure_link(node_id: int, target_node_id: int) -> str:
    """Add an AWX workflow failure edge between template nodes.

    Use this to run the target node only when the source node fails.
    For success-only or always-run paths, use add_workflow_node_success_link or
    add_workflow_node_always_link.

    Args:
        node_id: ID of the source workflow template node
            (from list_workflow_template_nodes)
        target_node_id: ID of the target workflow template node to run on
            failure (from list_workflow_template_nodes)
    """
    with get_ansible_client() as client:
        data = {"id": target_node_id}
        response = client.request(
            "POST",
            f"/api/v2/workflow_job_template_nodes/{node_id}/failure_nodes/",
            data=data,
        )
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def add_workflow_node_always_link(node_id: int, target_node_id: int) -> str:
    """Add an AWX workflow always-run edge between template nodes.

    Use this to run the target node regardless of source-node success or failure.
    For conditional routing, use add_workflow_node_success_link or
    add_workflow_node_failure_link instead.

    Args:
        node_id: ID of the source workflow template node
            (from list_workflow_template_nodes)
        target_node_id: ID of the target workflow template node to always run
            (from list_workflow_template_nodes)
    """
    with get_ansible_client() as client:
        data = {"id": target_node_id}
        response = client.request(
            "POST",
            f"/api/v2/workflow_job_template_nodes/{node_id}/always_nodes/",
            data=data,
        )
        return json.dumps(response, indent=2)

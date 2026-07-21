# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - System Tools

Activity stream, system information, metrics, and system job management.
"""

import json
from typing import Any

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool
from ..utils import validate_json_str

# =============================================================================
# Activity Stream
# =============================================================================

# Map an activity-stream object_type (singular AWX model name) to its REST
# collection path. AWX exposes a per-resource audit trail at
# ``/api/v2/<collection>/<id>/activity_stream/`` which is the only reliable way
# to scope the stream to one object — the global ``/api/v2/activity_stream/``
# endpoint rejects ``object1__*`` lookups with HTTP 400.
_ACTIVITY_STREAM_PATHS = {
    "job_template": "job_templates",
    "workflow_job_template": "workflow_job_templates",
    "job": "jobs",
    "workflow_job": "workflow_jobs",
    "inventory": "inventories",
    "inventory_source": "inventory_sources",
    "host": "hosts",
    "group": "groups",
    "project": "projects",
    "credential": "credentials",
    "organization": "organizations",
    "user": "users",
    "team": "teams",
    "execution_environment": "execution_environments",
    "schedule": "schedules",
    "notification_template": "notification_templates",
    "label": "labels",
}


@read_tool
def list_activity_stream(
    limit: int = 100, offset: int = 0, object_type: str = None, object_id: int = None
) -> str:
    """List AWX activity stream audit entries.

    Use this to trace who changed resources and when across the AWX control plane.
    Filter by object_type and object_id when investigating a specific entity,
    then drill into that entity with its get_* tool.

    Scoping behavior:
        - object_type + object_id: returns the per-resource audit trail via
          /api/v2/<collection>/<id>/activity_stream/ (precise, recommended).
        - object_type only: filters the global stream by object1 model name.
        - neither: returns the unfiltered global stream.

    Args:
        limit: Maximum number of results to return
        offset: Number of results to skip
        object_type: Object type singular model name (e.g., job_template,
            inventory, project, user). See get_* tools for the matching entity.
        object_id: Specific object ID to scope the trail to (requires object_type)
    """
    with get_ansible_client() as client:
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        # Precise per-resource trail: route to the resource sub-endpoint.
        if object_type is not None and object_id is not None:
            collection = _ACTIVITY_STREAM_PATHS.get(object_type)
            if collection is not None:
                endpoint = f"/api/v2/{collection}/{object_id}/activity_stream/"
                activities = handle_pagination(client, endpoint, params)
                return json.dumps(activities, indent=2)
            # Unknown type: fall back to a best-effort global filter by type.
            params["object1"] = object_type

        # Type-only filter on the global stream (object1 is the model name).
        elif object_type is not None:
            params["object1"] = object_type

        activities = handle_pagination(client, "/api/v2/activity_stream/", params)
        return json.dumps(activities, indent=2)


# =============================================================================
# System Information
# =============================================================================


@read_tool
def get_config() -> str:
    """Get AWX instance configuration metadata.

    Use this for controller-level settings and feature flags that affect tool behavior.
    Pair with get_ansible_version and get_dashboard_stats for environment diagnostics.
    """
    with get_ansible_client() as client:
        config = client.request("GET", "/api/v2/config/")
        return json.dumps(config, indent=2)


@read_tool
def get_ansible_version() -> str:
    """Get AWX/Ansible controller version and health information.

    Use this to verify controller version compatibility before automation changes.
    Pair with get_config for settings context and get_dashboard_stats for a
    high-level operational snapshot.
    """
    with get_ansible_client() as client:
        info = client.request("GET", "/api/v2/ping/")
        return json.dumps(info, indent=2)


@read_tool
def get_dashboard_stats() -> str:
    """Get AWX dashboard summary statistics.

    Use this for a quick operational snapshot of jobs, inventories, and recent activity.
    For raw maintenance execution history, use list_system_jobs.
    """
    with get_ansible_client() as client:
        stats = client.request("GET", "/api/v2/dashboard/")
        return json.dumps(stats, indent=2)


# =============================================================================
# System Job Templates & System Jobs (NEW)
# =============================================================================


@read_tool
def list_system_job_templates(limit: int = 100, offset: int = 0) -> str:
    """List AWX system job templates for maintenance operations.

    Use this to discover runnable maintenance tasks such as cleanup and
    analytics collection.
    For playbook execution templates, use list_job_templates.
    Returns template IDs for launch_system_job.

    Args:
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        templates = handle_pagination(client, "/api/v2/system_job_templates/", params)
        return json.dumps(templates, indent=2)


@read_tool
def get_system_job_template(template_id: int) -> str:
    """Get details for a specific AWX system job template.

    Use this to inspect available maintenance actions and required launch inputs.
    For playbook-based template details, use get_job_template instead.

    Args:
        template_id: ID of the system job template (from list_system_job_templates)
    """
    with get_ansible_client() as client:
        template = client.request("GET", f"/api/v2/system_job_templates/{template_id}/")
        return json.dumps(template, indent=2)


@write_tool(destructive=True)
def launch_system_job(template_id: int, extra_vars: str = None) -> str:
    """Launch an AWX maintenance system job from a system job template.

    Starts asynchronous controller maintenance work and returns a system job
    object with a job_id for tracking.
    Track status with get_system_job.
    For playbook execution, use launch_job; for orchestration workflows,
    use launch_workflow.

    Args:
        template_id: ID of the system job template
            (from list_system_job_templates or get_system_job_template)
        extra_vars: JSON string of extra variables (e.g., {"days": 90})
    """
    if extra_vars is not None:
        error = validate_json_str(extra_vars, "extra_vars")
        if error:
            return error

    with get_ansible_client() as client:
        data = {}
        if extra_vars is not None:
            data["extra_vars"] = extra_vars
        response = client.request(
            "POST", f"/api/v2/system_job_templates/{template_id}/launch/", data=data
        )
        return json.dumps(response, indent=2)


@read_tool
def list_system_jobs(
    limit: int = 100, offset: int = 0, order_by: str = "-created"
) -> str:
    """List AWX system job executions.

    Newest first by default (order_by="-created"); AWX's own default is
    oldest-first.

    Use this for maintenance-task run history such as cleanup and analytics jobs.
    For regular playbook execution runs, use list_jobs.
    For multi-step orchestration runs, use list_workflow_jobs.

    Args:
        limit: Maximum number of results to return
        offset: Number of results to skip
        order_by: Sort field; prefix with "-" for descending
            (e.g. -created, created, -finished, id, status)
    """
    with get_ansible_client() as client:
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "order_by": order_by,
        }
        jobs = handle_pagination(client, "/api/v2/system_jobs/", params)
        return json.dumps(jobs, indent=2)


@read_tool
def get_system_job(job_id: int) -> str:
    """Get details for a specific AWX maintenance system job run.

    Use this after launch_system_job to track state, result, and timing of
    maintenance work.
    For regular playbook jobs, use get_job.
    For workflow orchestration runs, use get_workflow_job.

    Args:
        job_id: ID of the system job
            (from list_system_jobs or launch_system_job response)
    """
    with get_ansible_client() as client:
        job = client.request("GET", f"/api/v2/system_jobs/{job_id}/")
        return json.dumps(job, indent=2)

# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Workflow Job & Approval Tools

Workflow job monitoring, approval management, and approval templates.
"""

import json
from typing import Any

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool

# =============================================================================
# Workflow Jobs
# =============================================================================


@read_tool
def list_workflow_jobs(
    status: str = None,
    limit: int = 20,
    offset: int = 0,
    order_by: str = "-created",
) -> str:
    """List AWX workflow orchestration runs, optionally filtered by status.

    Newest first by default (order_by="-created"); AWX's own default is
    oldest-first.

    Use this for multi-step executions launched from workflow templates.
    For single playbook runs, use list_jobs instead.
    For AWX maintenance executions, use list_system_jobs instead.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        status: Filter by job status
            (pending, waiting, running, successful, failed, canceled)
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
        if status is not None:
            params["status"] = status

        envelope = handle_pagination(
            client, "/api/v2/workflow_jobs/", params, with_meta=True
        )
        return json.dumps(envelope, indent=2)


@read_tool
def get_workflow_job(job_id: int) -> str:
    """Get details for a single AWX workflow orchestration run.

    Use this after launch_workflow or relaunch_workflow_job to track run status
    and node progress.
    For single playbook job details, use get_job.
    For AWX maintenance run details, use get_system_job.

    Args:
        job_id: ID of the workflow job (from list_workflow_jobs,
            launch_workflow, or relaunch_workflow_job response)
    """
    with get_ansible_client() as client:
        job = client.request("GET", f"/api/v2/workflow_jobs/{job_id}/")
        return json.dumps(job, indent=2)


@write_tool(destructive=True)
def cancel_workflow_job(job_id: int) -> str:
    """Cancel a running AWX workflow orchestration run.

    WARNING: This stops the workflow immediately and prevents remaining nodes
    from running.
    Use get_workflow_job to confirm final status and list_workflow_job_nodes
    to inspect partial execution.
    For single playbook cancellation, use cancel_job instead.

    Args:
        job_id: ID of the workflow job
            (from list_workflow_jobs or launch_workflow response)
    """
    with get_ansible_client() as client:
        response = client.request("POST", f"/api/v2/workflow_jobs/{job_id}/cancel/")
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def relaunch_workflow_job(job_id: int) -> str:
    """Relaunch a completed or failed AWX workflow orchestration run.

    Use this to rerun the same workflow definition and generate a new workflow
    job record.
    Returns a new workflow job_id for tracking with get_workflow_job.
    For rerunning a single playbook execution, use relaunch_job instead.

    Args:
        job_id: ID of the workflow job to relaunch
            (from list_workflow_jobs or get_workflow_job)
    """
    with get_ansible_client() as client:
        response = client.request("POST", f"/api/v2/workflow_jobs/{job_id}/relaunch/")
        return json.dumps(response, indent=2)


@read_tool
def list_workflow_job_nodes(job_id: int, limit: int = 20, offset: int = 0) -> str:
    """List node outcomes for an executed AWX workflow job.

    Use this to inspect which workflow nodes ran, failed, or were skipped in
    a specific run.
    Pair with get_workflow_job for overall status and list_workflow_approvals
    for pending approvals.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        job_id: ID of the workflow job
            (from list_workflow_jobs or launch_workflow response)
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        envelope = handle_pagination(
            client,
            f"/api/v2/workflow_jobs/{job_id}/workflow_nodes/",
            params,
            with_meta=True,
        )
        return json.dumps(envelope, indent=2)


# =============================================================================
# Workflow Approvals
# =============================================================================


@read_tool
def list_workflow_approvals(
    status: str = None, limit: int = 20, offset: int = 0
) -> str:
    """List AWX workflow approval requests, optionally filtered by status.

    Use this to find pending manual approval gates created by workflow approval nodes.
    Returns approval_id values for follow-up with get_workflow_approval,
    approve_workflow, or deny_workflow.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        status: Filter by status (pending, successful, failed)
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        envelope = handle_pagination(
            client, "/api/v2/workflow_approvals/", params, with_meta=True
        )
        return json.dumps(envelope, indent=2)


@read_tool
def get_workflow_approval(approval_id: int) -> str:
    """Get details for a specific AWX workflow approval request.

    Use this to inspect approval state, timing, and related workflow context
    before making a decision.
    For listing many requests first, use list_workflow_approvals.

    Args:
        approval_id: ID of the workflow approval (from list_workflow_approvals)
    """
    with get_ansible_client() as client:
        approval = client.request("GET", f"/api/v2/workflow_approvals/{approval_id}/")
        return json.dumps(approval, indent=2)


@write_tool()
def approve_workflow(approval_id: int) -> str:
    """Approve a pending AWX workflow approval request.

    Use this to unblock a workflow paused at an approval node so downstream
    nodes can continue.
    Check request details with get_workflow_approval before approving.

    Args:
        approval_id: ID of the workflow approval
            (from list_workflow_approvals or get_workflow_approval)
    """
    with get_ansible_client() as client:
        response = client.request(
            "POST", f"/api/v2/workflow_approvals/{approval_id}/approve/"
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def deny_workflow(approval_id: int) -> str:
    """Deny a pending AWX workflow approval request.

    Use this to explicitly reject a paused approval gate and stop that workflow
    path.
    Check request details with get_workflow_approval before denying.

    Args:
        approval_id: ID of the workflow approval
            (from list_workflow_approvals or get_workflow_approval)
    """
    with get_ansible_client() as client:
        response = client.request(
            "POST", f"/api/v2/workflow_approvals/{approval_id}/deny/"
        )
        return json.dumps(response, indent=2)


# =============================================================================
# Workflow Approval Templates (NEW)
# =============================================================================


@read_tool
def list_workflow_approval_templates(
    workflow_template_id: int = None, limit: int = 20, offset: int = 0
) -> str:
    """List AWX workflow approval template nodes.

    Fetches all workflow nodes and filters client-side for approval-type nodes
    because AWX does not provide a dedicated list endpoint for approval templates.
    Use this to discover approval points defined in workflow templates before
    launch_workflow.
    For runtime approval requests in active executions, use
    list_workflow_approvals instead.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    total number of approval nodes found (already an exact count, since the
    full node set is fetched and filtered before offset/limit is applied); if
    offset + returned < count, call again with offset=offset+returned to page
    through.

    Args:
        workflow_template_id: Optional workflow job template ID to filter nodes
            (from list_workflow_templates)
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        if workflow_template_id is not None:
            endpoint = (
                f"/api/v2/workflow_job_templates/{workflow_template_id}/workflow_nodes/"
            )
        else:
            endpoint = "/api/v2/workflow_job_template_nodes/"
        # Fetch ALL nodes, then filter for approval nodes, THEN apply
        # limit/offset. Applying limit before filtering (the previous behavior)
        # would only scan the first `limit` raw nodes and miss approval nodes
        # beyond that window, returning an incomplete (often empty) list on
        # large AWX instances.
        all_nodes = handle_pagination(client, endpoint)
        # Surface a pagination-budget timeout instead of silently returning a
        # partial (and therefore wrong) approval list.
        if (
            len(all_nodes) == 1
            and isinstance(all_nodes[0], dict)
            and all_nodes[0].get("error") == "pagination_timeout"
        ):
            return json.dumps(all_nodes[0], indent=2)
        # AWX versions differ on which field identifies approval nodes in summary_fields
        approval_nodes = []
        for node in all_nodes:
            ujt = node.get("summary_fields", {}).get("unified_job_template", {})
            if (
                ujt.get("unified_job_type") == "workflow_approval"
                or ujt.get("type") == "workflow_approval_template"
            ):
                approval_nodes.append(node)
        window = (
            approval_nodes[offset : offset + limit]
            if limit
            else approval_nodes[offset:]
        )
        # count is exact (not a server-reported total) because approval_nodes
        # is already the complete filtered collection at this point.
        envelope = {
            "count": len(approval_nodes),
            "returned": len(window),
            "offset": offset,
            "results": window,
        }
        return json.dumps(envelope, indent=2)


@read_tool
def get_workflow_approval_template(node_id: int) -> str:
    """Get a workflow job template node by ID (may or may not be an approval node).

    Returns the full node object from /api/v2/workflow_job_template_nodes/.
    Despite the tool name, this returns a workflow template node — use it to
    inspect node configuration including approval-node prompts and timeout.
    For runtime approval instances during execution, use get_workflow_approval
    instead.

    Args:
        node_id: ID of the workflow job template node
            (from list_workflow_approval_templates or list_workflow_template_nodes)
    """
    with get_ansible_client() as client:
        node = client.request("GET", f"/api/v2/workflow_job_template_nodes/{node_id}/")
        return json.dumps(node, indent=2)

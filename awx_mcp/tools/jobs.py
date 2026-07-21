# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Job Management Tools
"""

import json
from typing import Any

from ..client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    get_ansible_client,
    handle_pagination,
)
from ..exceptions import AnsibleHTTPError
from ..server import read_tool, write_tool


@read_tool
def list_jobs(
    status: str = None,
    limit: int = 20,
    offset: int = 0,
    order_by: str = "-created",
) -> str:
    """List AWX regular playbook jobs, optionally filtered by status.

    Newest first by default (order_by="-created") — without an explicit
    ordering AWX returns jobs oldest-first, which is rarely what "recent
    jobs" means.

    Use this for single playbook executions launched from job templates.
    For multi-step orchestration runs, use list_workflow_jobs instead.
    For AWX maintenance runs, use list_system_jobs instead.

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

        jobs = handle_pagination(client, "/api/v2/jobs/", params, with_meta=True)
        return json.dumps(jobs, indent=2)


@read_tool
def get_job(job_id: int) -> str:
    """Get details for a single AWX playbook execution job.

    Use this after launch_job or relaunch_job to track state, timings, and
    related resources.
    For workflow run details, use get_workflow_job instead.
    For system maintenance run details, use get_system_job instead.

    Args:
        job_id: ID of the job (from list_jobs, launch_job, or relaunch_job response)
    """
    with get_ansible_client() as client:
        job = client.request("GET", f"/api/v2/jobs/{job_id}/")
        return json.dumps(job, indent=2)


@write_tool(destructive=True)
def cancel_job(job_id: int) -> str:
    """Cancel a running AWX regular playbook job.

    WARNING: This stops an in-flight execution and may leave target systems
    partially changed.
    Use get_job to confirm the terminal status after cancellation.
    For workflow-run cancellation, use cancel_workflow_job instead.

    Args:
        job_id: ID of the job (from list_jobs or launch_job response)
    """
    with get_ansible_client() as client:
        response = client.request("POST", f"/api/v2/jobs/{job_id}/cancel/")
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def relaunch_job(job_id: int) -> str:
    """Relaunch a completed or failed AWX regular playbook job.

    Use this to rerun one playbook execution with the same template context.
    Returns a new job object with a new job_id for tracking via get_job.
    For multi-step workflow reruns, use relaunch_workflow_job instead.

    Args:
        job_id: ID of the job to relaunch (from list_jobs or get_job)
    """
    with get_ansible_client() as client:
        response = client.request("POST", f"/api/v2/jobs/{job_id}/relaunch/")
        return json.dumps(response, indent=2)


@read_tool
def get_job_events(job_id: int, limit: int = 20, offset: int = 0) -> str:
    """Get event stream records for a specific AWX regular job.

    Use this for task-level execution history when status alone is not enough.
    Pair with get_job_stdout for readable logs and get_job_host_summaries for
    per-host rollups.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        job_id: ID of the job (from list_jobs or launch_job response)
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        events = handle_pagination(
            client, f"/api/v2/jobs/{job_id}/job_events/", params, with_meta=True
        )
        return json.dumps(events, indent=2)


@read_tool
def get_job_stdout(job_id: int, format: str = "txt") -> str:
    """Get stdout logs for a specific AWX regular playbook job.

    Use this to read rendered execution output after launch_job, relaunch_job,
    or get_job.
    Returns stdout content in the requested format for troubleshooting or reporting.
    For structured event-level detail, use get_job_events.

    Args:
        job_id: ID of the job (from list_jobs or launch_job response)
        format: Output format (txt, html, json, ansi, txt_download, ansi_download)
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
            url = f"{client.base_url}/api/v2/jobs/{job_id}/stdout/?format={format}"
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
                "GET", f"/api/v2/jobs/{job_id}/stdout/?format={format}"
            )
            return json.dumps(response, indent=2)


@read_tool
def get_job_host_summaries(job_id: int, limit: int = 20, offset: int = 0) -> str:
    """Get AWX per-host result summaries for a regular playbook job.

    Use this to quickly identify which hosts succeeded, changed, or failed in one run.
    Pair with get_job_events for task-level detail and get_job_stdout for full logs.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        job_id: ID of the job (from list_jobs or launch_job response)
        limit: Maximum number of results to return
        offset: Number of results to skip
    """
    with get_ansible_client() as client:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        summaries = handle_pagination(
            client,
            f"/api/v2/jobs/{job_id}/job_host_summaries/",
            params,
            with_meta=True,
        )
        return json.dumps(summaries, indent=2)

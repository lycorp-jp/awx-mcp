# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Schedule Management Tools
"""

import json

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool
from ..utils import parse_json_str


@read_tool
def list_schedules(
    unified_job_template_id: int = None, limit: int = 20, offset: int = 0
) -> str:
    """List AWX schedules, optionally filtered by template.

    Use this to discover recurring iCal triggers attached to job templates or
    workflow templates. Returns schedule IDs and linked unified_job_template
    values for follow-up with get_schedule, update_schedule, or delete_schedule.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        unified_job_template_id: Optional template ID to filter schedules
            (from list_job_templates or list_workflow_templates)
        limit: Maximum number of schedule results to return
        offset: Number of schedule results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}

        if unified_job_template_id is not None:
            params["unified_job_template"] = unified_job_template_id

        envelope = handle_pagination(
            client, "/api/v2/schedules/", params, with_meta=True
        )
        return json.dumps(envelope, indent=2)


@read_tool
def get_schedule(schedule_id: int) -> str:
    """Get details for one AWX schedule.

    Use this when you need the exact RRULE, enabled state, or launch linkage
    for a specific recurring trigger. Pair with list_schedules to discover IDs
    and with update_schedule to revise recurrence.

    Args:
        schedule_id: ID of the schedule (from list_schedules)
    """
    with get_ansible_client() as client:
        schedule = client.request("GET", f"/api/v2/schedules/{schedule_id}/")
        return json.dumps(schedule, indent=2)


@write_tool()
def create_schedule(
    name: str,
    unified_job_template_id: int,
    rrule: str,
    description: str = "",
    extra_data: str = "{}",
) -> str:
    """Create an AWX schedule for recurring launches.

    Attaches an iCal RRULE-based trigger to a job template or workflow
    template so AWX launches it automatically on schedule. Returns the created
    schedule record with id for future updates or deletion.

    Args:
        name: Schedule name shown in AWX
        unified_job_template_id: Template ID to trigger
            (from list_job_templates or list_workflow_templates)
        rrule: iCal recurrence rule
            (e.g., "DTSTART:20231001T120000Z RRULE:FREQ=DAILY;INTERVAL=1")
        description: Schedule description and intent
        extra_data: JSON string of extra vars passed at each scheduled launch
    """
    parsed_extra, error = parse_json_str(extra_data, "extra_data")
    if error:
        return error

    with get_ansible_client() as client:
        data = {
            "name": name,
            "unified_job_template": unified_job_template_id,
            "rrule": rrule,
            "description": description,
            "extra_data": parsed_extra,
        }

        response = client.request("POST", "/api/v2/schedules/", data=data)
        return json.dumps(response, indent=2)


@write_tool(idempotent=True)
def update_schedule(
    schedule_id: int,
    name: str = None,
    rrule: str = None,
    description: str = None,
    extra_data: str = None,
) -> str:
    """Update an AWX schedule.

    Use this to change recurrence, metadata, or scheduled extra vars without
    recreating the trigger. Returns the updated schedule object; use
    get_schedule to verify the final RRULE and configuration.

    Args:
        schedule_id: ID of the schedule (from list_schedules)
        name: New schedule name
        rrule: New iCal recurrence rule
        description: New schedule description
        extra_data: JSON string of updated extra vars for launches
    """
    parsed_extra = None
    if extra_data is not None:
        parsed_extra, error = parse_json_str(extra_data, "extra_data")
        if error:
            return error

    with get_ansible_client() as client:
        data = {}
        if name is not None:
            data["name"] = name
        if rrule is not None:
            data["rrule"] = rrule
        if description is not None:
            data["description"] = description
        if parsed_extra is not None:
            data["extra_data"] = parsed_extra

        response = client.request(
            "PATCH", f"/api/v2/schedules/{schedule_id}/", data=data
        )
        return json.dumps(response, indent=2)


@write_tool(destructive=True)
def delete_schedule(schedule_id: int) -> str:
    """Delete an AWX schedule.

    WARNING: This permanently removes the recurring trigger and future
    automatic launches will stop. Confirm the schedule target with
    get_schedule before deleting.

    Args:
        schedule_id: ID of the schedule (from list_schedules)
    """
    with get_ansible_client() as client:
        client.request("DELETE", f"/api/v2/schedules/{schedule_id}/")
        return json.dumps(
            {"status": "success", "message": f"Schedule {schedule_id} deleted"}
        )

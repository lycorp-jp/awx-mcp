# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Notification Template Tools
"""

import json

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool


@read_tool
def list_notification_templates(limit: int = 20, offset: int = 0) -> str:
    """List AWX notification templates.

    Use this to discover alert integrations (email, Slack, webhook, and more)
    available for job and workflow event notifications. Returns template IDs
    for get_notification_template and test_notification_template.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of notification template results to return
        offset: Number of notification template results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        envelope = handle_pagination(
            client, "/api/v2/notification_templates/", params, with_meta=True
        )
        return json.dumps(envelope, indent=2)


@read_tool
def get_notification_template(template_id: int) -> str:
    """Get details for one AWX notification template.

    Use this when you need backend type and configuration metadata for a
    specific notifier before testing or attaching it to resources. Pair with
    list_notification_templates to discover template IDs.

    Args:
        template_id: ID of the notification template (from list_notification_templates)
    """
    with get_ansible_client() as client:
        template = client.request(
            "GET", f"/api/v2/notification_templates/{template_id}/"
        )
        return json.dumps(template, indent=2)


@write_tool()
def test_notification_template(template_id: int) -> str:
    """Send a test event through an AWX notification template.

    Use this to validate notifier connectivity and credentials before relying
    on event-driven alerts from jobs or workflows. Returns the test result
    payload for troubleshooting delivery behavior.

    Args:
        template_id: ID of the notification template (from list_notification_templates)
    """
    with get_ansible_client() as client:
        response = client.request(
            "POST", f"/api/v2/notification_templates/{template_id}/test/"
        )
        return json.dumps(response, indent=2)

# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Label Tools
"""

import json
from typing import Any

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool, write_tool


@read_tool
def list_labels(
    label_name: str = None,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """List AWX labels.

    Use this to discover reusable tags applied across AWX resources for
    grouping and filtering. Returns label IDs and organization linkage for
    follow-up resource association workflows.

    Label name searches are performed server-side using AWX's case-insensitive
    partial-name filter. Prefer this over paging through the whole collection
    when resolving a name to an ID.

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        label_name: Optional full or partial label name, matched
            case-insensitively.
        limit: Maximum number of label results to return
        offset: Number of label results to skip
    """
    with get_ansible_client() as client:
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        if label_name and label_name.strip():
            params["name__icontains"] = label_name.strip()

        labels = handle_pagination(client, "/api/v2/labels/", params, with_meta=True)
        return json.dumps(labels, indent=2)


@write_tool()
def create_label(name: str, organization_id: int) -> str:
    """Create an AWX label in an organization.

    Use this to define a tag that can be attached to resources such as job
    templates and inventories for organization-level categorization. Returns
    the created label object, including id and organization.

    Args:
        name: Label name (for example, "production" or "pci")
        organization_id: Owning organization ID (from list_organizations)
    """
    with get_ansible_client() as client:
        data = {"name": name, "organization": organization_id}
        response = client.request("POST", "/api/v2/labels/", data=data)
        return json.dumps(response, indent=2)

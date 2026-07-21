# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - Instance & Instance Group Tools
"""

import json

from ..client import get_ansible_client, handle_pagination
from ..server import read_tool

# The system /api/v2/instances/ and /api/v2/instance_groups/ collections require
# system-auditor/admin RBAC; non-privileged tokens get an empty list (HTTP 200,
# count 0) rather than 403. The read-only /api/v2/ping/ endpoint, however,
# exposes the same cluster topology to any authenticated user. When the
# privileged collection comes back empty we fall back to ping so callers still
# get node/group visibility instead of a misleading empty result.
_PING_FALLBACK_NOTE = (
    "Primary {endpoint} returned no rows (token likely lacks system-auditor "
    "RBAC). Falling back to read-only cluster topology from /api/v2/ping/."
)


def _ping_topology(client):
    """Return the /api/v2/ping/ payload, or {} on any error (best-effort)."""
    try:
        return client.request("GET", "/api/v2/ping/")
    except Exception:  # noqa: BLE001 — fallback is best-effort
        return {}


@read_tool
def list_instances(limit: int = 20, offset: int = 0) -> str:
    """List AWX cluster instances.

    Use this for control-plane visibility into AWX nodes that execute and
    coordinate jobs. Returns instance IDs and capacity-related fields for
    deeper inspection with get_instance.

    If the privileged /api/v2/instances/ collection is empty (insufficient
    RBAC), this falls back to read-only node topology from /api/v2/ping/
    (returned in its own {"results", "_source", "_note"} shape rather than
    the envelope below).

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of instance results to return
        offset: Number of instance results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        instances = handle_pagination(
            client, "/api/v2/instances/", params, with_meta=True
        )
        if not instances["results"]:
            topology = _ping_topology(client).get("instances", [])
            if topology:
                return json.dumps(
                    {
                        "results": topology,
                        "_source": "/api/v2/ping/",
                        "_note": _PING_FALLBACK_NOTE.format(
                            endpoint="/api/v2/instances/"
                        ),
                    },
                    indent=2,
                )
        return json.dumps(instances, indent=2)


@read_tool
def get_instance(instance_id: int) -> str:
    """Get details for one AWX cluster instance.

    Use this when a specific node needs health or capacity review for job
    routing decisions. Pair with list_instances to discover instance IDs and
    with list_instance_groups to understand placement context.

    Args:
        instance_id: ID of the instance (from list_instances)
    """
    with get_ansible_client() as client:
        instance = client.request("GET", f"/api/v2/instances/{instance_id}/")
        return json.dumps(instance, indent=2)


@read_tool
def list_instance_groups(limit: int = 20, offset: int = 0) -> str:
    """List AWX instance groups.

    Use this to inspect how AWX nodes are grouped for execution capacity and
    job routing. Returns instance group IDs and names for follow-up via
    get_instance_group and scheduling diagnostics.

    If the privileged /api/v2/instance_groups/ collection is empty (insufficient
    RBAC), this falls back to read-only group topology from /api/v2/ping/
    (returned in its own {"results", "_source", "_note"} shape rather than
    the envelope below).

    Returns a JSON envelope {count, returned, offset, results}. count is the
    server-side total; if offset + returned < count, call again with
    offset=offset+returned to page through.

    Args:
        limit: Maximum number of instance group results to return
        offset: Number of instance group results to skip
    """
    with get_ansible_client() as client:
        params = {"limit": limit, "offset": offset}
        groups = handle_pagination(
            client, "/api/v2/instance_groups/", params, with_meta=True
        )
        if not groups["results"]:
            topology = _ping_topology(client).get("instance_groups", [])
            if topology:
                return json.dumps(
                    {
                        "results": topology,
                        "_source": "/api/v2/ping/",
                        "_note": _PING_FALLBACK_NOTE.format(
                            endpoint="/api/v2/instance_groups/"
                        ),
                    },
                    indent=2,
                )
        return json.dumps(groups, indent=2)


@read_tool
def get_instance_group(group_id: int) -> str:
    """Get details for one AWX instance group.

    Use this to review a group's capacity policy and associated instances when
    analyzing job placement behavior. Pair with list_instance_groups to find
    target IDs first.

    Args:
        group_id: ID of the instance group (from list_instance_groups)
    """
    with get_ansible_client() as client:
        group = client.request("GET", f"/api/v2/instance_groups/{group_id}/")
        return json.dumps(group, indent=2)

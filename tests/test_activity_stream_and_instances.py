# SPDX-License-Identifier: Apache-2.0

"""Regression tests for two AWX REST quirks surfaced by live testing:

1. ``list_activity_stream`` must scope a per-resource trail through the
   ``/api/v2/<collection>/<id>/activity_stream/`` sub-endpoint. The old
   ``object1__content_type__model`` global filter returned HTTP 400
   ("No related model for field content_type.").
2. ``list_instances`` / ``list_instance_groups`` fall back to the read-only
   ``/api/v2/ping/`` topology when the privileged collection is empty (the
   common case for non system-auditor tokens).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from awx_mcp.tools import instances as instances_mod
from awx_mcp.tools import system as system_mod
from tests.conftest import fake_client_factory


def _paginated(rows):
    """Shape a single-page AWX list response."""
    return {"count": len(rows), "next": None, "previous": None, "results": rows}


# ---------------------------------------------------------------------------
# list_activity_stream
# ---------------------------------------------------------------------------


def test_activity_stream_object_type_and_id_uses_resource_subendpoint():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1}])

    with patch.object(system_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = system_mod.list_activity_stream(
            object_type="job_template", object_id=4745
        )

    endpoint = api.request.call_args.args[1]
    assert endpoint == "/api/v2/job_templates/4745/activity_stream/"
    # No broken object1__* lookups in the query params.
    sent_params = api.request.call_args.kwargs.get("params") or {}
    assert not any(k.startswith("object1__") for k in sent_params)
    data = json.loads(out)
    assert data["results"] == [{"id": 1}]
    assert data["count"] == 1
    assert data["returned"] == 1
    assert data["offset"] == 0


def test_activity_stream_inventory_pluralizes_collection_path():
    api = MagicMock()
    api.request.return_value = _paginated([])

    with patch.object(system_mod, "get_ansible_client", new=fake_client_factory(api)):
        system_mod.list_activity_stream(object_type="inventory", object_id=1218)

    assert api.request.call_args.args[1] == "/api/v2/inventories/1218/activity_stream/"


def test_activity_stream_type_only_filters_global_by_object1():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 7}])

    with patch.object(system_mod, "get_ansible_client", new=fake_client_factory(api)):
        system_mod.list_activity_stream(object_type="job_template")

    assert api.request.call_args.args[1] == "/api/v2/activity_stream/"
    sent_params = api.request.call_args.kwargs.get("params") or {}
    assert sent_params.get("object1") == "job_template"


def test_activity_stream_unknown_type_with_id_falls_back_to_global_filter():
    api = MagicMock()
    api.request.return_value = _paginated([])

    with patch.object(system_mod, "get_ansible_client", new=fake_client_factory(api)):
        system_mod.list_activity_stream(object_type="widget", object_id=5)

    # Unknown type must not build a bogus /api/v2/widgets/5/... path.
    assert api.request.call_args.args[1] == "/api/v2/activity_stream/"
    sent_params = api.request.call_args.kwargs.get("params") or {}
    assert sent_params.get("object1") == "widget"


def test_activity_stream_no_filter_hits_global_endpoint():
    api = MagicMock()
    api.request.return_value = _paginated([])

    with patch.object(system_mod, "get_ansible_client", new=fake_client_factory(api)):
        system_mod.list_activity_stream()

    assert api.request.call_args.args[1] == "/api/v2/activity_stream/"
    sent_params = api.request.call_args.kwargs.get("params") or {}
    assert "object1" not in sent_params


# ---------------------------------------------------------------------------
# list_instances / list_instance_groups ping fallback
# ---------------------------------------------------------------------------

_PING = {
    "instances": [{"node": "awx-task-1", "node_type": "control", "capacity": 480}],
    "instance_groups": [{"name": "controlplane", "capacity": 2878, "instances": []}],
}


def _route(primary_endpoint, primary_rows):
    """request() side_effect: primary endpoint paginates, ping returns _PING."""

    def _side_effect(method, endpoint, *args, **kwargs):
        if endpoint == "/api/v2/ping/":
            return _PING
        if endpoint == primary_endpoint:
            return _paginated(primary_rows)
        raise AssertionError(f"unexpected endpoint {endpoint}")

    return _side_effect


def test_list_instances_falls_back_to_ping_when_empty():
    api = MagicMock()
    api.request.side_effect = _route("/api/v2/instances/", [])

    with patch.object(
        instances_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(instances_mod.list_instances())

    assert out["_source"] == "/api/v2/ping/"
    assert out["results"] == _PING["instances"]
    assert "/api/v2/instances/" in out["_note"]


def test_list_instances_returns_primary_when_present():
    rows = [{"id": 1, "hostname": "node-a"}]
    api = MagicMock()
    api.request.side_effect = _route("/api/v2/instances/", rows)

    with patch.object(
        instances_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(instances_mod.list_instances())

    assert out["results"] == rows
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0
    # ping must NOT be consulted when the primary collection has rows.
    assert all(c.args[1] != "/api/v2/ping/" for c in api.request.call_args_list)


def test_list_instance_groups_falls_back_to_ping_when_empty():
    api = MagicMock()
    api.request.side_effect = _route("/api/v2/instance_groups/", [])

    with patch.object(
        instances_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(instances_mod.list_instance_groups())

    assert out["_source"] == "/api/v2/ping/"
    assert out["results"] == _PING["instance_groups"]


def test_list_instance_groups_returns_primary_when_present():
    rows = [{"id": 2, "name": "default"}]
    api = MagicMock()
    api.request.side_effect = _route("/api/v2/instance_groups/", rows)

    with patch.object(
        instances_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(instances_mod.list_instance_groups())

    assert out["results"] == rows
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0

# SPDX-License-Identifier: Apache-2.0

"""Tests for the server-side name filters on the ``list_*`` tools.

Issue #30: resolving a resource by name was unreliable once the collection
needed pagination, because the target could sit outside the first page. Every
``list_*`` tool that fronts a named collection now accepts a name argument and
pushes it to AWX as a case-insensitive partial-match filter, so a name resolves
in one call regardless of collection size.

These tests pin three things per tool:

* the tool's name argument reaches AWX as the right ``*__icontains`` query param
* surrounding whitespace is stripped
* omitted / ``None`` / blank-only values add no filter at all (so the unfiltered
  listing behaviour is untouched)

``list_instances`` and ``list_instance_groups`` get extra coverage: they fall
back to ``/api/v2/ping/`` when the privileged collection is empty, and that
fallback must not fire for a filtered query — ``/api/v2/ping/`` cannot apply the
filter, so falling back would answer a filtered query with unfiltered rows.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from awx_mcp.tools import credentials as credentials_mod
from awx_mcp.tools import execution_environments as ee_mod
from awx_mcp.tools import groups as groups_mod
from awx_mcp.tools import hosts as hosts_mod
from awx_mcp.tools import instances as instances_mod
from awx_mcp.tools import inventories as inventories_mod
from awx_mcp.tools import job_templates as job_templates_mod
from awx_mcp.tools import labels as labels_mod
from awx_mcp.tools import notifications as notifications_mod
from awx_mcp.tools import organizations as organizations_mod
from awx_mcp.tools import projects as projects_mod
from awx_mcp.tools import rbac as rbac_mod
from awx_mcp.tools import system as system_mod
from awx_mcp.tools import teams as teams_mod
from awx_mcp.tools import users as users_mod
from awx_mcp.tools import workflow_templates as workflow_templates_mod
from tests.conftest import fake_client_factory


def _paginated(rows, count=None):
    """Shape a single-page AWX list response (count overridable)."""
    return {
        "count": len(rows) if count is None else count,
        "next": None,
        "previous": None,
        "results": rows,
    }


# (module, tool name, tool's name argument, AWX query param it maps to)
#
# Most collections key on ``name``. Instance keys on ``hostname`` and User on
# ``username``, so those two map to a different AWX field.
NAME_FILTERS = [
    (hosts_mod, "list_hosts", "hostname", "name__icontains"),
    (job_templates_mod, "list_job_templates", "template_name", "name__icontains"),
    (projects_mod, "list_projects", "project_name", "name__icontains"),
    (inventories_mod, "list_inventories", "inventory_name", "name__icontains"),
    (inventories_mod, "list_inventory_sources", "source_name", "name__icontains"),
    (
        workflow_templates_mod,
        "list_workflow_templates",
        "template_name",
        "name__icontains",
    ),
    (credentials_mod, "list_credentials", "credential_name", "name__icontains"),
    (credentials_mod, "list_credential_types", "type_name", "name__icontains"),
    (organizations_mod, "list_organizations", "organization_name", "name__icontains"),
    (teams_mod, "list_teams", "team_name", "name__icontains"),
    (labels_mod, "list_labels", "label_name", "name__icontains"),
    (groups_mod, "list_groups", "group_name", "name__icontains"),
    (
        ee_mod,
        "list_execution_environments",
        "environment_name",
        "name__icontains",
    ),
    (
        notifications_mod,
        "list_notification_templates",
        "template_name",
        "name__icontains",
    ),
    (instances_mod, "list_instances", "hostname", "hostname__icontains"),
    (instances_mod, "list_instance_groups", "group_name", "name__icontains"),
    (system_mod, "list_system_job_templates", "template_name", "name__icontains"),
    (users_mod, "list_users", "username", "username__icontains"),
]

_IDS = [f"{mod.__name__.rsplit('.', 1)[-1]}.{tool}" for mod, tool, _, _ in NAME_FILTERS]


def _call(module, tool_name, **kwargs):
    """Invoke a list tool against a mocked client, returning (envelope, params).

    ``params`` is the query dict handed to the final ``client.request`` call, so
    assertions can check what actually reached AWX rather than what the tool
    intended to send. One non-empty row keeps the instances/instance-groups ping
    fallback out of the way for the shared cases.
    """
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "name": "row"}])

    with patch.object(module, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(getattr(module, tool_name)(**kwargs))

    return out, api.request.call_args.kwargs["params"]


@pytest.mark.parametrize(("module", "tool", "arg", "param"), NAME_FILTERS, ids=_IDS)
def test_name_argument_becomes_server_side_icontains_filter(module, tool, arg, param):
    _, params = _call(module, tool, **{arg: "deploy"})

    assert params[param] == "deploy"


@pytest.mark.parametrize(("module", "tool", "arg", "param"), NAME_FILTERS, ids=_IDS)
def test_name_argument_is_stripped(module, tool, arg, param):
    _, params = _call(module, tool, **{arg: "  deploy  "})

    assert params[param] == "deploy"


@pytest.mark.parametrize(("module", "tool", "arg", "param"), NAME_FILTERS, ids=_IDS)
def test_name_argument_omitted_sends_no_filter(module, tool, arg, param):
    _, params = _call(module, tool)

    assert param not in params


@pytest.mark.parametrize(("module", "tool", "arg", "param"), NAME_FILTERS, ids=_IDS)
@pytest.mark.parametrize("blank", [None, "", "   "], ids=["none", "empty", "spaces"])
def test_blank_name_argument_sends_no_filter(module, tool, arg, param, blank):
    # A blank name must behave exactly like an omitted one: AWX treats
    # name__icontains="" as "match everything", so sending it is harmless but
    # noisy — and sending it for a user who typed nothing is misleading.
    _, params = _call(module, tool, **{arg: blank})

    assert param not in params


@pytest.mark.parametrize(("module", "tool", "arg", "param"), NAME_FILTERS, ids=_IDS)
def test_filtered_call_keeps_envelope_shape(module, tool, arg, param):
    out, _ = _call(module, tool, **{arg: "deploy"})

    assert set(out) == {"count", "returned", "offset", "results"}
    assert out["returned"] == len(out["results"])


def test_name_filter_does_not_disturb_existing_scope_filters():
    # list_groups and list_inventory_sources scope by inventory via the URL, not
    # a query param. Adding a name filter must not move them off that path.
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1}])

    with patch.object(groups_mod, "get_ansible_client", new=fake_client_factory(api)):
        json.loads(groups_mod.list_groups(inventory_id=7, group_name="web"))

    assert api.request.call_args.args[1] == "/api/v2/inventories/7/groups/"
    assert api.request.call_args.kwargs["params"]["name__icontains"] == "web"


def test_list_roles_has_no_name_filter():
    # Deliberate omission, not an oversight: AWX exposes Role.name as a
    # serializer property derived from role_field rather than a database column,
    # so name__icontains is not a valid filter on /api/v2/roles/. Adding one
    # would hand callers a 400 instead of a lookup.
    with pytest.raises(TypeError):
        rbac_mod.list_roles(role_name="admin")


# ---------------------------------------------------------------------------
# instances / instance_groups: the ping fallback must respect the filter
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


@pytest.mark.parametrize(
    ("tool", "arg", "endpoint"),
    [
        ("list_instances", "hostname", "/api/v2/instances/"),
        ("list_instance_groups", "group_name", "/api/v2/instance_groups/"),
    ],
)
def test_filtered_empty_result_does_not_fall_back_to_ping(tool, arg, endpoint):
    # A filtered query that matches nothing means "no such resource", which is a
    # real answer. /api/v2/ping/ cannot apply the filter, so falling back here
    # would replace an accurate empty result with the whole unfiltered topology.
    api = MagicMock()
    api.request.side_effect = _route(endpoint, [])

    with patch.object(
        instances_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(getattr(instances_mod, tool)(**{arg: "no-such-node"}))

    assert set(out) == {"count", "returned", "offset", "results"}
    assert out["results"] == []
    assert all(c.args[1] != "/api/v2/ping/" for c in api.request.call_args_list)


@pytest.mark.parametrize(
    ("tool", "arg", "endpoint", "topology_key"),
    [
        ("list_instances", "hostname", "/api/v2/instances/", "instances"),
        (
            "list_instance_groups",
            "group_name",
            "/api/v2/instance_groups/",
            "instance_groups",
        ),
    ],
)
@pytest.mark.parametrize("blank", [None, "", "   "], ids=["none", "empty", "spaces"])
def test_unfiltered_empty_result_still_falls_back_to_ping(
    tool, arg, endpoint, topology_key, blank
):
    # Regression guard for the RBAC fallback: a blank name argument counts as
    # unfiltered, so the ping fallback must behave exactly as it did before the
    # filter existed.
    api = MagicMock()
    api.request.side_effect = _route(endpoint, [])

    with patch.object(
        instances_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(getattr(instances_mod, tool)(**{arg: blank}))

    assert out["_source"] == "/api/v2/ping/"
    assert out["results"] == _PING[topology_key]

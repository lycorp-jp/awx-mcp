# SPDX-License-Identifier: Apache-2.0

"""Behavior tests for the inventory-adjacent AWX MCP tool modules.

Covers hosts, groups, schedules, execution_environments, and labels: HTTP
method/endpoint/body shape for every tool, non-None field filtering on
partial updates, host<->group association endpoints, and the
validation-error branches that short-circuit before any HTTP call.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from awx_mcp.tools import execution_environments as ee_mod
from awx_mcp.tools import groups as groups_mod
from awx_mcp.tools import hosts as hosts_mod
from awx_mcp.tools import labels as labels_mod
from awx_mcp.tools import schedules as schedules_mod
from tests.conftest import fake_client_factory


def _paginated(rows):
    """Shape a single-page AWX list response."""
    return {"count": len(rows), "next": None, "previous": None, "results": rows}


# ---------------------------------------------------------------------------
# hosts
# ---------------------------------------------------------------------------


def test_list_hosts_global_endpoint_and_page_size():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "name": "h1"}])

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(hosts_mod.list_hosts())

    assert api.request.call_args.args == ("GET", "/api/v2/hosts/")
    assert api.request.call_args.kwargs["params"] == {"page_size": 20}
    assert out["results"] == [{"id": 1, "name": "h1"}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0


def test_list_hosts_scoped_to_inventory():
    api = MagicMock()
    api.request.return_value = _paginated([])

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        hosts_mod.list_hosts(inventory_id=7, limit=10, offset=0)

    assert api.request.call_args.args == ("GET", "/api/v2/inventories/7/hosts/")
    assert api.request.call_args.kwargs["params"] == {"page_size": 10}


def test_get_host():
    api = MagicMock()
    api.request.return_value = {"id": 5, "name": "h5"}

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(hosts_mod.get_host(5))

    api.request.assert_called_once_with("GET", "/api/v2/hosts/5/")
    assert out == {"id": 5, "name": "h5"}


def test_create_host_happy_path():
    api = MagicMock()
    api.request.return_value = {"id": 1, "name": "new-host"}

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(
            hosts_mod.create_host(
                name="new-host",
                inventory_id=3,
                variables='{"a": 1}',
                description="desc",
                enabled=False,
            )
        )

    api.request.assert_called_once_with(
        "POST",
        "/api/v2/hosts/",
        data={
            "name": "new-host",
            "inventory": 3,
            "variables": '{"a": 1}',
            "description": "desc",
            "enabled": False,
        },
    )
    assert out == {"id": 1, "name": "new-host"}


def test_create_host_invalid_variables_json_short_circuits():
    api = MagicMock()

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(
            hosts_mod.create_host(name="x", inventory_id=1, variables="not-json{{")
        )

    assert out["status"] == "error"
    api.request.assert_not_called()


def test_update_host_filters_none_fields():
    api = MagicMock()
    api.request.return_value = {"id": 5, "name": "renamed"}

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(hosts_mod.update_host(host_id=5, name="renamed"))

    api.request.assert_called_once_with(
        "PATCH", "/api/v2/hosts/5/", data={"name": "renamed"}
    )
    assert out == {"id": 5, "name": "renamed"}


def test_update_host_enabled_false_is_sent_not_dropped():
    """``enabled=False`` must survive the ``is not None`` filter."""
    api = MagicMock()
    api.request.return_value = {"id": 5, "enabled": False}

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        hosts_mod.update_host(host_id=5, enabled=False)

    assert api.request.call_args.kwargs["data"] == {"enabled": False}


def test_update_host_invalid_variables_json_short_circuits():
    api = MagicMock()

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(hosts_mod.update_host(host_id=5, variables="bad-json"))

    assert out["status"] == "error"
    api.request.assert_not_called()


def test_delete_host():
    api = MagicMock()

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(hosts_mod.delete_host(9))

    api.request.assert_called_once_with("DELETE", "/api/v2/hosts/9/")
    assert out == {"status": "success", "message": "Host 9 deleted"}


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------


def test_list_groups_global_endpoint():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "name": "g1"}])

    with patch.object(groups_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(groups_mod.list_groups())

    assert api.request.call_args.args == ("GET", "/api/v2/groups/")
    assert api.request.call_args.kwargs["params"] == {"page_size": 20}
    assert out["results"] == [{"id": 1, "name": "g1"}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0


def test_list_groups_scoped_to_inventory():
    api = MagicMock()
    api.request.return_value = _paginated([])

    with patch.object(groups_mod, "get_ansible_client", new=fake_client_factory(api)):
        groups_mod.list_groups(inventory_id=4)

    assert api.request.call_args.args == ("GET", "/api/v2/inventories/4/groups/")


def test_get_group():
    api = MagicMock()
    api.request.return_value = {"id": 2, "name": "g2"}

    with patch.object(groups_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(groups_mod.get_group(2))

    api.request.assert_called_once_with("GET", "/api/v2/groups/2/")
    assert out == {"id": 2, "name": "g2"}


def test_create_group_happy_path():
    api = MagicMock()
    api.request.return_value = {"id": 1, "name": "new-group"}

    with patch.object(groups_mod, "get_ansible_client", new=fake_client_factory(api)):
        json.loads(
            groups_mod.create_group(
                name="new-group",
                inventory_id=3,
                variables='{"k": "v"}',
                description="desc",
            )
        )

    api.request.assert_called_once_with(
        "POST",
        "/api/v2/groups/",
        data={
            "name": "new-group",
            "inventory": 3,
            "variables": '{"k": "v"}',
            "description": "desc",
        },
    )


def test_create_group_invalid_variables_json_short_circuits():
    api = MagicMock()

    with patch.object(groups_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(
            groups_mod.create_group(name="x", inventory_id=1, variables="{bad")
        )

    assert out["status"] == "error"
    api.request.assert_not_called()


def test_update_group_filters_none_fields():
    api = MagicMock()
    api.request.return_value = {"id": 2, "description": "updated"}

    with patch.object(groups_mod, "get_ansible_client", new=fake_client_factory(api)):
        groups_mod.update_group(group_id=2, description="updated")

    api.request.assert_called_once_with(
        "PATCH", "/api/v2/groups/2/", data={"description": "updated"}
    )


def test_update_group_invalid_variables_json_short_circuits():
    api = MagicMock()

    with patch.object(groups_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(groups_mod.update_group(group_id=2, variables="{bad"))

    assert out["status"] == "error"
    api.request.assert_not_called()


def test_delete_group():
    api = MagicMock()

    with patch.object(groups_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(groups_mod.delete_group(6))

    api.request.assert_called_once_with("DELETE", "/api/v2/groups/6/")
    assert out == {"status": "success", "message": "Group 6 deleted"}


def test_add_host_to_group_association_endpoint():
    api = MagicMock()
    api.request.return_value = {}

    with patch.object(groups_mod, "get_ansible_client", new=fake_client_factory(api)):
        groups_mod.add_host_to_group(group_id=10, host_id=20)

    api.request.assert_called_once_with(
        "POST", "/api/v2/groups/10/hosts/", data={"id": 20}
    )


def test_remove_host_from_group_disassociate_flag():
    api = MagicMock()
    api.request.return_value = {}

    with patch.object(groups_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(groups_mod.remove_host_from_group(group_id=10, host_id=20))

    api.request.assert_called_once_with(
        "POST",
        "/api/v2/groups/10/hosts/",
        data={"id": 20, "disassociate": True},
    )
    assert out == {
        "status": "success",
        "message": "Host 20 removed from group 10",
    }


# ---------------------------------------------------------------------------
# schedules
# ---------------------------------------------------------------------------


def test_list_schedules_no_filter():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1}])

    with patch.object(
        schedules_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(schedules_mod.list_schedules())

    assert api.request.call_args.args == ("GET", "/api/v2/schedules/")
    assert api.request.call_args.kwargs["params"] == {"page_size": 20}
    assert out["results"] == [{"id": 1}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0


def test_list_schedules_filtered_by_template():
    api = MagicMock()
    api.request.return_value = _paginated([])

    with patch.object(
        schedules_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        schedules_mod.list_schedules(unified_job_template_id=42)

    assert api.request.call_args.kwargs["params"] == {
        "unified_job_template": 42,
        "page_size": 20,
    }


def test_get_schedule():
    api = MagicMock()
    api.request.return_value = {"id": 3, "rrule": "FREQ=DAILY"}

    with patch.object(
        schedules_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(schedules_mod.get_schedule(3))

    api.request.assert_called_once_with("GET", "/api/v2/schedules/3/")
    assert out == {"id": 3, "rrule": "FREQ=DAILY"}


def test_create_schedule_happy_path_parses_extra_data():
    api = MagicMock()
    api.request.return_value = {"id": 1, "name": "nightly"}

    with patch.object(
        schedules_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        json.loads(
            schedules_mod.create_schedule(
                name="nightly",
                unified_job_template_id=7,
                rrule="DTSTART:20231001T120000Z RRULE:FREQ=DAILY;INTERVAL=1",
                description="nightly run",
                extra_data='{"verbose": true}',
            )
        )

    api.request.assert_called_once_with(
        "POST",
        "/api/v2/schedules/",
        data={
            "name": "nightly",
            "unified_job_template": 7,
            "rrule": "DTSTART:20231001T120000Z RRULE:FREQ=DAILY;INTERVAL=1",
            "description": "nightly run",
            "extra_data": {"verbose": True},
        },
    )


def test_create_schedule_invalid_extra_data_short_circuits():
    api = MagicMock()

    with patch.object(
        schedules_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(
            schedules_mod.create_schedule(
                name="x",
                unified_job_template_id=1,
                rrule="FREQ=DAILY",
                extra_data="not-json",
            )
        )

    assert out["status"] == "error"
    api.request.assert_not_called()


def test_update_schedule_filters_none_fields_and_parses_extra_data():
    api = MagicMock()
    api.request.return_value = {"id": 3, "rrule": "FREQ=WEEKLY"}

    with patch.object(
        schedules_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        schedules_mod.update_schedule(
            schedule_id=3, rrule="FREQ=WEEKLY", extra_data='{"x": 1}'
        )

    api.request.assert_called_once_with(
        "PATCH",
        "/api/v2/schedules/3/",
        data={"rrule": "FREQ=WEEKLY", "extra_data": {"x": 1}},
    )


def test_update_schedule_invalid_extra_data_short_circuits():
    api = MagicMock()

    with patch.object(
        schedules_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(
            schedules_mod.update_schedule(schedule_id=3, extra_data="{not-json")
        )

    assert out["status"] == "error"
    api.request.assert_not_called()


def test_delete_schedule():
    api = MagicMock()

    with patch.object(
        schedules_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(schedules_mod.delete_schedule(11))

    api.request.assert_called_once_with("DELETE", "/api/v2/schedules/11/")
    assert out == {"status": "success", "message": "Schedule 11 deleted"}


# ---------------------------------------------------------------------------
# execution_environments
# ---------------------------------------------------------------------------


def test_list_execution_environments():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "name": "ee1"}])

    with patch.object(ee_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(ee_mod.list_execution_environments())

    assert api.request.call_args.args == ("GET", "/api/v2/execution_environments/")
    assert api.request.call_args.kwargs["params"] == {"page_size": 20}
    assert out["results"] == [{"id": 1, "name": "ee1"}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0


def test_get_execution_environment():
    api = MagicMock()
    api.request.return_value = {"id": 4, "image": "quay.io/x:latest"}

    with patch.object(ee_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(ee_mod.get_execution_environment(4))

    api.request.assert_called_once_with("GET", "/api/v2/execution_environments/4/")
    assert out == {"id": 4, "image": "quay.io/x:latest"}


def test_create_execution_environment_minimal_omits_optional_fields():
    api = MagicMock()
    api.request.return_value = {"id": 1, "name": "ee-new"}

    with patch.object(ee_mod, "get_ansible_client", new=fake_client_factory(api)):
        ee_mod.create_execution_environment(name="ee-new", image="quay.io/x:latest")

    api.request.assert_called_once_with(
        "POST",
        "/api/v2/execution_environments/",
        data={
            "name": "ee-new",
            "image": "quay.io/x:latest",
            "description": "",
        },
    )


def test_create_execution_environment_with_all_optional_fields():
    api = MagicMock()
    api.request.return_value = {"id": 1, "name": "ee-new"}

    with patch.object(ee_mod, "get_ansible_client", new=fake_client_factory(api)):
        ee_mod.create_execution_environment(
            name="ee-new",
            image="quay.io/x:latest",
            organization_id=9,
            credential_id=8,
            description="desc",
            pull="always",
        )

    api.request.assert_called_once_with(
        "POST",
        "/api/v2/execution_environments/",
        data={
            "name": "ee-new",
            "image": "quay.io/x:latest",
            "description": "desc",
            "organization": 9,
            "credential": 8,
            "pull": "always",
        },
    )


def test_update_execution_environment_filters_none_fields():
    api = MagicMock()
    api.request.return_value = {"id": 4, "pull": "missing"}

    with patch.object(ee_mod, "get_ansible_client", new=fake_client_factory(api)):
        ee_mod.update_execution_environment(ee_id=4, pull="missing")

    api.request.assert_called_once_with(
        "PATCH",
        "/api/v2/execution_environments/4/",
        data={"pull": "missing"},
    )


def test_delete_execution_environment():
    api = MagicMock()

    with patch.object(ee_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(ee_mod.delete_execution_environment(4))

    api.request.assert_called_once_with("DELETE", "/api/v2/execution_environments/4/")
    assert out == {
        "status": "success",
        "message": "Execution environment 4 deleted",
    }


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------


def test_list_labels():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "name": "prod"}])

    with patch.object(labels_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(labels_mod.list_labels())

    assert api.request.call_args.args == ("GET", "/api/v2/labels/")
    assert api.request.call_args.kwargs["params"] == {"page_size": 20}
    assert out["results"] == [{"id": 1, "name": "prod"}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0


def test_create_label():
    api = MagicMock()
    api.request.return_value = {"id": 1, "name": "prod", "organization": 5}

    with patch.object(labels_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(labels_mod.create_label(name="prod", organization_id=5))

    api.request.assert_called_once_with(
        "POST", "/api/v2/labels/", data={"name": "prod", "organization": 5}
    )
    assert out == {"id": 1, "name": "prod", "organization": 5}

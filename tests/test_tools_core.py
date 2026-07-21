# SPDX-License-Identifier: Apache-2.0

"""Behavior tests for the core AWX MCP tool modules.

Covers HTTP method/endpoint/body construction and validation branches for:
- awx_mcp.tools.job_templates
- awx_mcp.tools.workflow_templates
- awx_mcp.tools.inventories
- awx_mcp.tools.projects
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from awx_mcp.tools import inventories as inventories_mod
from awx_mcp.tools import job_templates as jt_mod
from awx_mcp.tools import projects as projects_mod
from awx_mcp.tools import workflow_templates as wf_mod
from tests.conftest import fake_client_factory


def _paginated(rows):
    return {"count": len(rows), "next": None, "previous": None, "results": rows}


# ---------------------------------------------------------------------------
# job_templates
# ---------------------------------------------------------------------------


def test_list_job_templates_uses_pagination_params(fake_client):
    # offset=5 with page_size=10 lands on page 1, skipping the first 5 rows
    # of that page's results.
    rows = [{"id": i} for i in range(6)]
    fake_client.request.return_value = _paginated(rows)
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.list_job_templates(limit=10, offset=5)

    assert fake_client.request.call_args.args == ("GET", "/api/v2/job_templates/")
    assert fake_client.request.call_args.kwargs["params"] == {
        "page_size": 10,
        "page": 1,
    }
    data = json.loads(out)
    assert data["results"] == [{"id": 5}]
    assert data["count"] == 6  # server-side total from the mocked AWX count
    assert data["offset"] == 5  # requested offset echoed back
    assert data["returned"] == 1


def test_get_job_template(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jt_mod.get_job_template(42)

    assert fake_client.request.call_args.args == ("GET", "/api/v2/job_templates/42/")


def test_create_job_template_invalid_job_type_short_circuits(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.create_job_template(
            name="n", inventory_id=1, project_id=1, playbook="p.yml", job_type="bogus"
        )

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_create_job_template_invalid_verbosity_short_circuits(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.create_job_template(
            name="n", inventory_id=1, project_id=1, playbook="p.yml", verbosity=99
        )

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_create_job_template_invalid_extra_vars_short_circuits(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.create_job_template(
            name="n",
            inventory_id=1,
            project_id=1,
            playbook="p.yml",
            extra_vars="not-json{{",
        )

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_create_job_template_happy_path_builds_full_body(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jt_mod.create_job_template(
            name="n",
            inventory_id=1,
            project_id=2,
            playbook="p.yml",
            description="d",
            extra_vars='{"a": 1}',
            job_type="check",
            verbosity=3,
            limit="web",
            forks=4,
            become_enabled=True,
            diff_mode=True,
            allow_simultaneous=True,
            timeout=60,
        )

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("POST", "/api/v2/job_templates/")
    body = fake_client.request.call_args.kwargs["data"]
    assert body == {
        "name": "n",
        "inventory": 1,
        "project": 2,
        "playbook": "p.yml",
        "description": "d",
        "extra_vars": '{"a": 1}',
        "job_type": "check",
        "verbosity": 3,
        "limit": "web",
        "forks": 4,
        "become_enabled": True,
        "diff_mode": True,
        "allow_simultaneous": True,
        "timeout": 60,
    }


def test_update_job_template_only_sends_provided_fields(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jt_mod.update_job_template(template_id=7, name="new-name", forks=2)

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("PATCH", "/api/v2/job_templates/7/")
    body = fake_client.request.call_args.kwargs["data"]
    assert body == {"name": "new-name", "forks": 2}


def test_update_job_template_invalid_job_type_short_circuits(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.update_job_template(template_id=7, job_type="bogus")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_update_job_template_invalid_verbosity_short_circuits(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.update_job_template(template_id=7, verbosity=-1)

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_update_job_template_invalid_extra_vars_short_circuits(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.update_job_template(template_id=7, extra_vars="{bad json")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_delete_job_template(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.delete_job_template(9)

    assert fake_client.request.call_args.args == ("DELETE", "/api/v2/job_templates/9/")
    assert json.loads(out)["status"] == "success"


def test_launch_job_invalid_extra_vars_short_circuits(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.launch_job(template_id=1, extra_vars="not-json")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_launch_job_credential_becomes_credentials_list(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jt_mod.launch_job(template_id=1, credential=55, limit="web", verbosity=2)

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("POST", "/api/v2/job_templates/1/launch/")
    body = fake_client.request.call_args.kwargs["data"]
    assert body == {"credentials": [55], "limit": "web", "verbosity": 2}


def test_launch_job_omits_unset_optional_fields(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jt_mod.launch_job(template_id=1)

    body = fake_client.request.call_args.kwargs["data"]
    assert body == {}


def test_copy_job_template(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jt_mod.copy_job_template(template_id=3, new_name="clone")

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("POST", "/api/v2/job_templates/3/copy/")
    assert fake_client.request.call_args.kwargs["data"] == {"name": "clone"}


def test_get_job_template_survey(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jt_mod.get_job_template_survey(3)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/job_templates/3/survey_spec/",
    )


def test_set_job_template_survey_invalid_json_short_circuits(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.set_job_template_survey(3, "not-json")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_set_job_template_survey_posts_parsed_spec(fake_client):
    spec = {"name": "s", "description": "d", "spec": []}
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jt_mod.set_job_template_survey(3, json.dumps(spec))

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("POST", "/api/v2/job_templates/3/survey_spec/")
    assert fake_client.request.call_args.kwargs["data"] == spec


def test_delete_job_template_survey(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.delete_job_template_survey(3)

    assert fake_client.request.call_args.args == (
        "DELETE",
        "/api/v2/job_templates/3/survey_spec/",
    )
    assert json.loads(out)["status"] == "success"


def test_list_template_credentials(fake_client):
    fake_client.request.return_value = _paginated([{"id": 1}])
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jt_mod.list_template_credentials(template_id=3, limit=50, offset=0)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/job_templates/3/credentials/",
    )


def test_associate_credential_with_template_posts_id(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jt_mod.associate_credential_with_template(template_id=3, credential_id=10)

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("POST", "/api/v2/job_templates/3/credentials/")
    assert fake_client.request.call_args.kwargs["data"] == {"id": 10}


def test_disassociate_credential_from_template_posts_disassociate_flag(fake_client):
    with patch.object(
        jt_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = jt_mod.disassociate_credential_from_template(
            template_id=3, credential_id=10
        )

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("POST", "/api/v2/job_templates/3/credentials/")
    assert fake_client.request.call_args.kwargs["data"] == {
        "id": 10,
        "disassociate": True,
    }
    assert json.loads(out)["status"] == "success"


# ---------------------------------------------------------------------------
# workflow_templates
# ---------------------------------------------------------------------------


def test_list_workflow_templates(fake_client):
    fake_client.request.return_value = _paginated([{"id": 1}])
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.list_workflow_templates(limit=20, offset=0)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/workflow_job_templates/",
    )


def test_get_workflow_template(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.get_workflow_template(5)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/workflow_job_templates/5/",
    )


def test_create_workflow_template_invalid_extra_vars_short_circuits(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = wf_mod.create_workflow_template(
            name="n", organization_id=1, extra_vars="bad{{"
        )

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_create_workflow_template_omits_none_optional_fields(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.create_workflow_template(name="n", organization_id=1)

    body = fake_client.request.call_args.kwargs["data"]
    assert body == {
        "name": "n",
        "organization": 1,
        "description": "",
        "extra_vars": "{}",
        "survey_enabled": False,
        "allow_simultaneous": False,
    }
    assert "inventory" not in body
    assert "limit" not in body
    assert "scm_branch" not in body


def test_create_workflow_template_includes_optional_fields_when_set(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.create_workflow_template(
            name="n",
            organization_id=1,
            inventory=3,
            limit="web",
            scm_branch="main",
        )

    body = fake_client.request.call_args.kwargs["data"]
    assert body["inventory"] == 3
    assert body["limit"] == "web"
    assert body["scm_branch"] == "main"


def test_update_workflow_template_only_sends_provided_fields(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.update_workflow_template(template_id=8, name="renamed")

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("PATCH", "/api/v2/workflow_job_templates/8/")
    assert fake_client.request.call_args.kwargs["data"] == {"name": "renamed"}


def test_update_workflow_template_invalid_extra_vars_short_circuits(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = wf_mod.update_workflow_template(template_id=8, extra_vars="bad{{")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_delete_workflow_template(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = wf_mod.delete_workflow_template(8)

    assert fake_client.request.call_args.args == (
        "DELETE",
        "/api/v2/workflow_job_templates/8/",
    )
    assert json.loads(out)["status"] == "success"


def test_launch_workflow_invalid_extra_vars_short_circuits(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = wf_mod.launch_workflow(template_id=1, extra_vars="bad{{")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_launch_workflow_builds_body_from_set_fields(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.launch_workflow(template_id=1, inventory=2, limit="web")

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == (
        "POST",
        "/api/v2/workflow_job_templates/1/launch/",
    )
    assert fake_client.request.call_args.kwargs["data"] == {
        "inventory": 2,
        "limit": "web",
    }


def test_copy_workflow_template(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.copy_workflow_template(template_id=1, new_name="clone")

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("POST", "/api/v2/workflow_job_templates/1/copy/")
    assert fake_client.request.call_args.kwargs["data"] == {"name": "clone"}


def test_get_workflow_template_survey(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.get_workflow_template_survey(1)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/workflow_job_templates/1/survey_spec/",
    )


def test_set_workflow_template_survey_invalid_json_short_circuits(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = wf_mod.set_workflow_template_survey(1, "not-json")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_set_workflow_template_survey_posts_parsed_spec(fake_client):
    spec = {"name": "s", "description": "d", "spec": []}
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.set_workflow_template_survey(1, json.dumps(spec))

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == (
        "POST",
        "/api/v2/workflow_job_templates/1/survey_spec/",
    )
    assert fake_client.request.call_args.kwargs["data"] == spec


def test_delete_workflow_template_survey(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = wf_mod.delete_workflow_template_survey(1)

    assert fake_client.request.call_args.args == (
        "DELETE",
        "/api/v2/workflow_job_templates/1/survey_spec/",
    )
    assert json.loads(out)["status"] == "success"


def test_list_workflow_template_nodes(fake_client):
    fake_client.request.return_value = _paginated([{"id": 1}])
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.list_workflow_template_nodes(template_id=1, limit=10, offset=0)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/workflow_job_templates/1/workflow_nodes/",
    )


def test_get_workflow_template_node(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.get_workflow_template_node(node_id=4)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/workflow_job_template_nodes/4/",
    )


def test_create_workflow_template_node_invalid_extra_data_short_circuits(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = wf_mod.create_workflow_template_node(
            workflow_template_id=1, unified_job_template_id=2, extra_data="bad{{"
        )

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_create_workflow_template_node_builds_body_with_identifier(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.create_workflow_template_node(
            workflow_template_id=1,
            unified_job_template_id=2,
            identifier="step-1",
            all_parents_must_converge=True,
            extra_data='{"a": 1}',
        )

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("POST", "/api/v2/workflow_job_template_nodes/")
    body = fake_client.request.call_args.kwargs["data"]
    assert body == {
        "workflow_job_template": 1,
        "unified_job_template": 2,
        "all_parents_must_converge": True,
        "extra_data": {"a": 1},
        "identifier": "step-1",
    }


def test_create_workflow_template_node_omits_identifier_when_unset(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.create_workflow_template_node(
            workflow_template_id=1, unified_job_template_id=2
        )

    body = fake_client.request.call_args.kwargs["data"]
    assert "identifier" not in body


def test_delete_workflow_template_node(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = wf_mod.delete_workflow_template_node(node_id=4)

    assert fake_client.request.call_args.args == (
        "DELETE",
        "/api/v2/workflow_job_template_nodes/4/",
    )
    assert json.loads(out)["status"] == "success"


def test_add_workflow_node_success_link(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.add_workflow_node_success_link(node_id=1, target_node_id=2)

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == (
        "POST",
        "/api/v2/workflow_job_template_nodes/1/success_nodes/",
    )
    assert fake_client.request.call_args.kwargs["data"] == {"id": 2}


def test_add_workflow_node_failure_link(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.add_workflow_node_failure_link(node_id=1, target_node_id=3)

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == (
        "POST",
        "/api/v2/workflow_job_template_nodes/1/failure_nodes/",
    )
    assert fake_client.request.call_args.kwargs["data"] == {"id": 3}


def test_add_workflow_node_always_link(fake_client):
    with patch.object(
        wf_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        wf_mod.add_workflow_node_always_link(node_id=1, target_node_id=4)

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == (
        "POST",
        "/api/v2/workflow_job_template_nodes/1/always_nodes/",
    )
    assert fake_client.request.call_args.kwargs["data"] == {"id": 4}


# ---------------------------------------------------------------------------
# inventories
# ---------------------------------------------------------------------------


def test_list_inventories(fake_client):
    fake_client.request.return_value = _paginated([{"id": 1}])
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.list_inventories(limit=10, offset=0)

    assert fake_client.request.call_args.args == ("GET", "/api/v2/inventories/")


def test_get_inventory(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.get_inventory(3)

    assert fake_client.request.call_args.args == ("GET", "/api/v2/inventories/3/")


def test_create_inventory_invalid_variables_short_circuits(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = inventories_mod.create_inventory(
            name="n", organization_id=1, variables="bad{{"
        )

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_create_inventory_builds_body(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.create_inventory(
            name="n", organization_id=1, description="d", variables='{"a": 1}'
        )

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("POST", "/api/v2/inventories/")
    assert fake_client.request.call_args.kwargs["data"] == {
        "name": "n",
        "description": "d",
        "organization": 1,
        "variables": '{"a": 1}',
    }


def test_update_inventory_only_sends_provided_fields(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.update_inventory(inventory_id=3, name="renamed")

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("PATCH", "/api/v2/inventories/3/")
    assert fake_client.request.call_args.kwargs["data"] == {"name": "renamed"}


def test_update_inventory_invalid_variables_short_circuits(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = inventories_mod.update_inventory(inventory_id=3, variables="bad{{")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_delete_inventory(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = inventories_mod.delete_inventory(3)

    assert fake_client.request.call_args.args == ("DELETE", "/api/v2/inventories/3/")
    assert json.loads(out)["status"] == "success"


def test_list_inventory_sources_scoped_to_inventory(fake_client):
    fake_client.request.return_value = _paginated([])
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.list_inventory_sources(inventory_id=3, limit=10, offset=0)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/inventories/3/inventory_sources/",
    )


def test_list_inventory_sources_global_when_no_inventory_id(fake_client):
    fake_client.request.return_value = _paginated([])
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.list_inventory_sources(limit=10, offset=0)

    assert fake_client.request.call_args.args == ("GET", "/api/v2/inventory_sources/")


def test_get_inventory_source(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.get_inventory_source(9)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/inventory_sources/9/",
    )


def test_create_inventory_source_invalid_json_source_vars_short_circuits(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = inventories_mod.create_inventory_source(
            name="n", inventory_id=1, source="ec2", source_vars="{bad"
        )

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_create_inventory_source_non_json_source_vars_skips_validation(fake_client):
    # YAML-shaped source_vars ("[1] doesn't start with { or [") should NOT be
    # rejected — only JSON-looking payloads are validated.
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = inventories_mod.create_inventory_source(
            name="n", inventory_id=1, source="scm", source_vars="key: value"
        )

    body = fake_client.request.call_args.kwargs["data"]
    assert body["source_vars"] == "key: value"
    assert json.loads(out) == fake_client.request.return_value


def test_create_inventory_source_includes_credential_when_set(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.create_inventory_source(
            name="n", inventory_id=1, source="ec2", credential_id=7
        )

    body = fake_client.request.call_args.kwargs["data"]
    assert body["credential"] == 7


def test_create_inventory_source_omits_credential_when_unset(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.create_inventory_source(name="n", inventory_id=1, source="ec2")

    body = fake_client.request.call_args.kwargs["data"]
    assert "credential" not in body


def test_update_inventory_source_only_sends_provided_fields(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.update_inventory_source(source_id=9, name="renamed")

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("PATCH", "/api/v2/inventory_sources/9/")
    assert fake_client.request.call_args.kwargs["data"] == {"name": "renamed"}


def test_update_inventory_source_invalid_json_source_vars_short_circuits(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = inventories_mod.update_inventory_source(source_id=9, source_vars="{bad")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_sync_inventory_source(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.sync_inventory_source(9)

    assert fake_client.request.call_args.args == (
        "POST",
        "/api/v2/inventory_sources/9/update/",
    )


def test_delete_inventory_source(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = inventories_mod.delete_inventory_source(9)

    assert fake_client.request.call_args.args == (
        "DELETE",
        "/api/v2/inventory_sources/9/",
    )
    assert json.loads(out)["status"] == "success"


def test_list_inventory_updates(fake_client):
    fake_client.request.return_value = _paginated([])
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.list_inventory_updates(source_id=9, limit=10, offset=0)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/inventory_sources/9/inventory_updates/",
    )


def test_get_inventory_update(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.get_inventory_update(15)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/inventory_updates/15/",
    )


def test_cancel_inventory_update(fake_client):
    with patch.object(
        inventories_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        inventories_mod.cancel_inventory_update(15)

    assert fake_client.request.call_args.args == (
        "POST",
        "/api/v2/inventory_updates/15/cancel/",
    )


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


def test_list_projects(fake_client):
    fake_client.request.return_value = _paginated([{"id": 1}])
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        projects_mod.list_projects(limit=10, offset=0)

    assert fake_client.request.call_args.args == ("GET", "/api/v2/projects/")


def test_get_project(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        projects_mod.get_project(4)

    assert fake_client.request.call_args.args == ("GET", "/api/v2/projects/4/")


def test_create_project_invalid_scm_type_short_circuits(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = projects_mod.create_project(name="n", organization_id=1, scm_type="bogus")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_create_project_missing_scm_url_for_non_manual_short_circuits(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = projects_mod.create_project(name="n", organization_id=1, scm_type="git")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_create_project_manual_scm_type_does_not_require_url(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        projects_mod.create_project(name="n", organization_id=1, scm_type="manual")

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("POST", "/api/v2/projects/")
    body = fake_client.request.call_args.kwargs["data"]
    assert body["scm_type"] == "manual"
    assert "scm_url" not in body


def test_create_project_builds_full_body_with_git(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        projects_mod.create_project(
            name="n",
            organization_id=1,
            scm_type="git",
            scm_url="https://example.com/repo.git",
            scm_branch="main",
            credential_id=8,
            description="d",
        )

    body = fake_client.request.call_args.kwargs["data"]
    assert body == {
        "name": "n",
        "organization": 1,
        "scm_type": "git",
        "description": "d",
        "scm_url": "https://example.com/repo.git",
        "scm_branch": "main",
        "credential": 8,
    }


def test_update_project_invalid_scm_type_short_circuits(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = projects_mod.update_project(project_id=4, scm_type="bogus")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_update_project_only_sends_provided_fields(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        projects_mod.update_project(project_id=4, name="renamed")

    method, endpoint = fake_client.request.call_args.args
    assert (method, endpoint) == ("PATCH", "/api/v2/projects/4/")
    assert fake_client.request.call_args.kwargs["data"] == {"name": "renamed"}


def test_delete_project(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = projects_mod.delete_project(4)

    assert fake_client.request.call_args.args == ("DELETE", "/api/v2/projects/4/")
    assert json.loads(out)["status"] == "success"


def test_sync_project(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        projects_mod.sync_project(4)

    assert fake_client.request.call_args.args == ("POST", "/api/v2/projects/4/update/")


def test_list_project_playbooks(fake_client):
    fake_client.request.return_value = ["site.yml"]
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = projects_mod.list_project_playbooks(4)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/projects/4/playbooks/",
    )
    assert json.loads(out) == ["site.yml"]


def test_list_project_updates(fake_client):
    fake_client.request.return_value = _paginated([])
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        projects_mod.list_project_updates(project_id=4, limit=10, offset=0)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/projects/4/project_updates/",
    )


def test_get_project_update(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        projects_mod.get_project_update(11)

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/project_updates/11/",
    )


def test_cancel_project_update(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        projects_mod.cancel_project_update(11)

    assert fake_client.request.call_args.args == (
        "POST",
        "/api/v2/project_updates/11/cancel/",
    )


def test_get_project_update_stdout_invalid_format_short_circuits(fake_client):
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = projects_mod.get_project_update_stdout(update_id=11, format="bogus")

    assert json.loads(out)["status"] == "error"
    fake_client.request.assert_not_called()


def test_get_project_update_stdout_json_format_uses_client_request(fake_client):
    fake_client.request.return_value = {"content": "log text"}
    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = projects_mod.get_project_update_stdout(update_id=11, format="json")

    assert fake_client.request.call_args.args == (
        "GET",
        "/api/v2/project_updates/11/stdout/?format=json",
    )
    assert json.loads(out) == {"content": "log text"}


def test_get_project_update_stdout_txt_format_uses_raw_session_get(fake_client):
    fake_client.base_url = "https://awx.example.com"
    fake_client.get_headers.return_value = {"Authorization": "Bearer x"}
    fake_response = MagicMock(status_code=200, text="PLAY [all] ***")
    fake_client.session.get.return_value = fake_response

    with patch.object(
        projects_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        out = projects_mod.get_project_update_stdout(update_id=11, format="txt")

    fake_client._validate_url.assert_called_once()
    called_url = fake_client._validate_url.call_args.args[0]
    assert called_url == (
        "https://awx.example.com/api/v2/project_updates/11/stdout/?format=txt"
    )
    fake_client.session.get.assert_called_once()
    assert json.loads(out) == {"status": "success", "stdout": "PLAY [all] ***"}
    fake_client.request.assert_not_called()


# ---------------------------------------------------------------------------
# execution-history ordering (regression: "recent N" returned oldest-first)
# ---------------------------------------------------------------------------


def test_list_jobs_defaults_to_newest_first(fake_client):
    from awx_mcp.tools import jobs as jobs_mod

    fake_client.request.return_value = _paginated([{"id": 1}])
    with patch.object(
        jobs_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jobs_mod.list_jobs(limit=10)
    params = (
        fake_client.request.call_args.kwargs.get("params")
        or fake_client.request.call_args.args[-1]
    )
    assert params["order_by"] == "-created"


def test_list_jobs_order_by_override(fake_client):
    from awx_mcp.tools import jobs as jobs_mod

    fake_client.request.return_value = _paginated([{"id": 1}])
    with patch.object(
        jobs_mod, "get_ansible_client", new=fake_client_factory(fake_client)
    ):
        jobs_mod.list_jobs(order_by="id")
    params = (
        fake_client.request.call_args.kwargs.get("params")
        or fake_client.request.call_args.args[-1]
    )
    assert params["order_by"] == "id"


def test_history_list_tools_default_newest_first(fake_client):
    from awx_mcp.tools import system as system_mod
    from awx_mcp.tools import workflow_jobs as wfj_mod

    cases = [
        (wfj_mod, lambda: wfj_mod.list_workflow_jobs()),
        (system_mod, lambda: system_mod.list_system_jobs()),
        (inventories_mod, lambda: inventories_mod.list_inventory_updates(1)),
        (projects_mod, lambda: projects_mod.list_project_updates(1)),
    ]
    for mod, call in cases:
        fake_client.request.return_value = _paginated([{"id": 1}])
        with patch.object(
            mod, "get_ansible_client", new=fake_client_factory(fake_client)
        ):
            call()
        params = (
            fake_client.request.call_args.kwargs.get("params")
            or fake_client.request.call_args.args[-1]
        )
        assert params["order_by"] == "-created", mod.__name__

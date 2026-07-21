# SPDX-License-Identifier: Apache-2.0

"""Behavior tests for organization, team, RBAC, notification-template, and
workflow-job/approval MCP tools.

Focuses on the endpoint/method/body shape sent to ``client.request`` (via the
``fake_client_factory`` context-manager stand-in for ``get_ansible_client``)
rather than AWX's actual response contents.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from awx_mcp.tools import notifications as notifications_mod
from awx_mcp.tools import organizations as organizations_mod
from awx_mcp.tools import rbac as rbac_mod
from awx_mcp.tools import teams as teams_mod
from awx_mcp.tools import workflow_jobs as workflow_jobs_mod
from tests.conftest import fake_client_factory


def _paginated(rows):
    """Shape a single-page AWX list response."""
    return {"count": len(rows), "next": None, "previous": None, "results": rows}


# ---------------------------------------------------------------------------
# organizations.py
# ---------------------------------------------------------------------------


def test_list_organizations_sends_limit_offset():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "name": "Default"}])

    with patch.object(
        organizations_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(organizations_mod.list_organizations(limit=10, offset=0))

    assert out["results"] == [{"id": 1, "name": "Default"}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0
    endpoint = api.request.call_args.args[1]
    assert endpoint == "/api/v2/organizations/"


def test_get_organization_uses_detail_endpoint():
    api = MagicMock()
    api.request.return_value = {"id": 7, "name": "Org7"}

    with patch.object(
        organizations_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(organizations_mod.get_organization(organization_id=7))

    assert out == {"id": 7, "name": "Org7"}
    api.request.assert_called_once_with("GET", "/api/v2/organizations/7/")


def test_create_organization_omits_default_environment_when_none():
    api = MagicMock()
    api.request.return_value = {"id": 1, "name": "NewOrg"}

    with patch.object(
        organizations_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        organizations_mod.create_organization(name="NewOrg", description="d")

    method, endpoint = api.request.call_args.args
    body = api.request.call_args.kwargs["data"]
    assert method == "POST"
    assert endpoint == "/api/v2/organizations/"
    assert body == {"name": "NewOrg", "description": "d", "max_hosts": 0}
    assert "default_environment" not in body


def test_create_organization_includes_default_environment_when_set():
    api = MagicMock()
    api.request.return_value = {"id": 1, "name": "NewOrg"}

    with patch.object(
        organizations_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        organizations_mod.create_organization(
            name="NewOrg", max_hosts=50, default_environment=3
        )

    body = api.request.call_args.kwargs["data"]
    assert body == {
        "name": "NewOrg",
        "description": "",
        "max_hosts": 50,
        "default_environment": 3,
    }


def test_update_organization_only_sends_provided_fields():
    api = MagicMock()
    api.request.return_value = {"id": 4, "name": "Renamed"}

    with patch.object(
        organizations_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        organizations_mod.update_organization(organization_id=4, name="Renamed")

    method, endpoint = api.request.call_args.args
    body = api.request.call_args.kwargs["data"]
    assert method == "PATCH"
    assert endpoint == "/api/v2/organizations/4/"
    assert body == {"name": "Renamed"}


def test_update_organization_with_all_fields():
    api = MagicMock()
    api.request.return_value = {"id": 4}

    with patch.object(
        organizations_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        organizations_mod.update_organization(
            organization_id=4,
            name="N",
            description="D",
            max_hosts=99,
            default_environment=2,
        )

    body = api.request.call_args.kwargs["data"]
    assert body == {
        "name": "N",
        "description": "D",
        "max_hosts": 99,
        "default_environment": 2,
    }


def test_delete_organization_returns_success_envelope():
    api = MagicMock()
    api.request.return_value = None

    with patch.object(
        organizations_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(organizations_mod.delete_organization(organization_id=9))

    api.request.assert_called_once_with("DELETE", "/api/v2/organizations/9/")
    assert out == {"status": "success", "message": "Organization 9 deleted"}


# ---------------------------------------------------------------------------
# teams.py
# ---------------------------------------------------------------------------


def test_list_teams_without_organization_hits_global_endpoint():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "name": "Team1"}])

    with patch.object(teams_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(teams_mod.list_teams())

    assert out["results"] == [{"id": 1, "name": "Team1"}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0
    assert api.request.call_args.args[1] == "/api/v2/teams/"


def test_list_teams_scoped_to_organization():
    api = MagicMock()
    api.request.return_value = _paginated([])

    with patch.object(teams_mod, "get_ansible_client", new=fake_client_factory(api)):
        teams_mod.list_teams(organization_id=3)

    assert api.request.call_args.args[1] == "/api/v2/organizations/3/teams/"


def test_get_team_uses_detail_endpoint():
    api = MagicMock()
    api.request.return_value = {"id": 5, "name": "Team5"}

    with patch.object(teams_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(teams_mod.get_team(team_id=5))

    assert out == {"id": 5, "name": "Team5"}
    api.request.assert_called_once_with("GET", "/api/v2/teams/5/")


def test_create_team_sends_organization_and_description():
    api = MagicMock()
    api.request.return_value = {"id": 1, "name": "Platform"}

    with patch.object(teams_mod, "get_ansible_client", new=fake_client_factory(api)):
        teams_mod.create_team(name="Platform", organization_id=2, description="ops")

    method, endpoint = api.request.call_args.args
    body = api.request.call_args.kwargs["data"]
    assert method == "POST"
    assert endpoint == "/api/v2/teams/"
    assert body == {"name": "Platform", "organization": 2, "description": "ops"}


def test_update_team_only_sends_provided_fields():
    api = MagicMock()
    api.request.return_value = {"id": 1}

    with patch.object(teams_mod, "get_ansible_client", new=fake_client_factory(api)):
        teams_mod.update_team(team_id=1, description="new desc")

    method, endpoint = api.request.call_args.args
    body = api.request.call_args.kwargs["data"]
    assert method == "PATCH"
    assert endpoint == "/api/v2/teams/1/"
    assert body == {"description": "new desc"}


def test_delete_team_returns_success_envelope():
    api = MagicMock()
    api.request.return_value = None

    with patch.object(teams_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(teams_mod.delete_team(team_id=8))

    api.request.assert_called_once_with("DELETE", "/api/v2/teams/8/")
    assert out == {"status": "success", "message": "Team 8 deleted"}


# ---------------------------------------------------------------------------
# rbac.py
# ---------------------------------------------------------------------------


def test_list_roles_paginates_global_endpoint():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "name": "Admin"}])

    with patch.object(rbac_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(rbac_mod.list_roles(limit=20, offset=0))

    assert out["results"] == [{"id": 1, "name": "Admin"}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0
    assert api.request.call_args.args[1] == "/api/v2/roles/"


def test_get_role_uses_detail_endpoint():
    api = MagicMock()
    api.request.return_value = {"id": 3, "name": "Execute"}

    with patch.object(rbac_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(rbac_mod.get_role(role_id=3))

    assert out == {"id": 3, "name": "Execute"}
    api.request.assert_called_once_with("GET", "/api/v2/roles/3/")


def test_grant_role_to_user_posts_associate_body():
    api = MagicMock()
    api.request.return_value = {"status": "ok"}

    with patch.object(rbac_mod, "get_ansible_client", new=fake_client_factory(api)):
        rbac_mod.grant_role_to_user(role_id=10, user_id=20)

    method, endpoint = api.request.call_args.args
    body = api.request.call_args.kwargs["data"]
    assert method == "POST"
    assert endpoint == "/api/v2/roles/10/users/"
    assert body == {"id": 20}
    assert "disassociate" not in body


def test_revoke_role_from_user_sets_disassociate_flag():
    api = MagicMock()
    api.request.return_value = None

    with patch.object(rbac_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(rbac_mod.revoke_role_from_user(role_id=10, user_id=20))

    method, endpoint = api.request.call_args.args
    body = api.request.call_args.kwargs["data"]
    assert method == "POST"
    assert endpoint == "/api/v2/roles/10/users/"
    assert body == {"id": 20, "disassociate": True}
    assert out == {
        "status": "success",
        "message": "Role 10 revoked from user 20",
    }


def test_grant_role_to_team_posts_associate_body():
    api = MagicMock()
    api.request.return_value = {"status": "ok"}

    with patch.object(rbac_mod, "get_ansible_client", new=fake_client_factory(api)):
        rbac_mod.grant_role_to_team(role_id=11, team_id=30)

    method, endpoint = api.request.call_args.args
    body = api.request.call_args.kwargs["data"]
    assert method == "POST"
    assert endpoint == "/api/v2/roles/11/teams/"
    assert body == {"id": 30}
    assert "disassociate" not in body


def test_revoke_role_from_team_sets_disassociate_flag():
    api = MagicMock()
    api.request.return_value = None

    with patch.object(rbac_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(rbac_mod.revoke_role_from_team(role_id=11, team_id=30))

    method, endpoint = api.request.call_args.args
    body = api.request.call_args.kwargs["data"]
    assert method == "POST"
    assert endpoint == "/api/v2/roles/11/teams/"
    assert body == {"id": 30, "disassociate": True}
    assert out == {
        "status": "success",
        "message": "Role 11 revoked from team 30",
    }


def test_list_object_roles_valid_type_hits_scoped_endpoint():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "name": "Admin"}])

    with patch.object(rbac_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(
            rbac_mod.list_object_roles(resource_type="inventories", resource_id=42)
        )

    assert out["results"] == [{"id": 1, "name": "Admin"}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0
    assert api.request.call_args.args[1] == "/api/v2/inventories/42/object_roles/"


def test_list_object_roles_invalid_type_returns_error_without_calling_client():
    api = MagicMock()
    api.request.side_effect = AssertionError("client should not be called")

    with patch.object(rbac_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(
            rbac_mod.list_object_roles(resource_type="widgets", resource_id=1)
        )

    assert out["status"] == "error"
    assert "Invalid resource_type" in out["message"]
    api.request.assert_not_called()


# ---------------------------------------------------------------------------
# notifications.py
# ---------------------------------------------------------------------------


def test_list_notification_templates_paginates():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "name": "Slack"}])

    with patch.object(
        notifications_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(notifications_mod.list_notification_templates())

    assert out["results"] == [{"id": 1, "name": "Slack"}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0
    assert api.request.call_args.args[1] == "/api/v2/notification_templates/"


def test_get_notification_template_uses_detail_endpoint():
    api = MagicMock()
    api.request.return_value = {"id": 2, "name": "Email"}

    with patch.object(
        notifications_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(notifications_mod.get_notification_template(template_id=2))

    assert out == {"id": 2, "name": "Email"}
    api.request.assert_called_once_with("GET", "/api/v2/notification_templates/2/")


def test_test_notification_template_posts_to_test_endpoint():
    api = MagicMock()
    api.request.return_value = {"notification": 55}

    with patch.object(
        notifications_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(notifications_mod.test_notification_template(template_id=2))

    assert out == {"notification": 55}
    api.request.assert_called_once_with(
        "POST", "/api/v2/notification_templates/2/test/"
    )


# ---------------------------------------------------------------------------
# workflow_jobs.py — list / get / approve / deny only
# ---------------------------------------------------------------------------


def test_list_workflow_jobs_without_status_omits_filter():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "status": "successful"}])

    with patch.object(
        workflow_jobs_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(workflow_jobs_mod.list_workflow_jobs())

    assert out["results"] == [{"id": 1, "status": "successful"}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0
    assert api.request.call_args.args[1] == "/api/v2/workflow_jobs/"
    sent_params = api.request.call_args.kwargs.get("params") or {}
    assert "status" not in sent_params


def test_list_workflow_jobs_with_status_filters():
    api = MagicMock()
    api.request.return_value = _paginated([])

    with patch.object(
        workflow_jobs_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        workflow_jobs_mod.list_workflow_jobs(status="failed")

    sent_params = api.request.call_args.kwargs.get("params") or {}
    assert sent_params.get("status") == "failed"


def test_get_workflow_job_uses_detail_endpoint():
    api = MagicMock()
    api.request.return_value = {"id": 9, "status": "running"}

    with patch.object(
        workflow_jobs_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(workflow_jobs_mod.get_workflow_job(job_id=9))

    assert out == {"id": 9, "status": "running"}
    api.request.assert_called_once_with("GET", "/api/v2/workflow_jobs/9/")


def test_list_workflow_job_nodes_scopes_to_job():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "job": 9}])

    with patch.object(
        workflow_jobs_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(workflow_jobs_mod.list_workflow_job_nodes(job_id=9))

    assert out["results"] == [{"id": 1, "job": 9}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0
    assert api.request.call_args.args[1] == "/api/v2/workflow_jobs/9/workflow_nodes/"


def test_list_workflow_approvals_without_status_omits_filter():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1, "status": "pending"}])

    with patch.object(
        workflow_jobs_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(workflow_jobs_mod.list_workflow_approvals())

    assert out["results"] == [{"id": 1, "status": "pending"}]
    assert out["count"] == 1
    assert out["returned"] == 1
    assert out["offset"] == 0
    assert api.request.call_args.args[1] == "/api/v2/workflow_approvals/"
    sent_params = api.request.call_args.kwargs.get("params") or {}
    assert "status" not in sent_params


def test_list_workflow_approvals_with_status_filters():
    api = MagicMock()
    api.request.return_value = _paginated([])

    with patch.object(
        workflow_jobs_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        workflow_jobs_mod.list_workflow_approvals(status="pending")

    sent_params = api.request.call_args.kwargs.get("params") or {}
    assert sent_params.get("status") == "pending"


def test_get_workflow_approval_uses_detail_endpoint():
    api = MagicMock()
    api.request.return_value = {"id": 4, "status": "pending"}

    with patch.object(
        workflow_jobs_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(workflow_jobs_mod.get_workflow_approval(approval_id=4))

    assert out == {"id": 4, "status": "pending"}
    api.request.assert_called_once_with("GET", "/api/v2/workflow_approvals/4/")


def test_approve_workflow_posts_to_approve_endpoint():
    api = MagicMock()
    api.request.return_value = {"id": 4, "status": "successful"}

    with patch.object(
        workflow_jobs_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(workflow_jobs_mod.approve_workflow(approval_id=4))

    assert out == {"id": 4, "status": "successful"}
    api.request.assert_called_once_with("POST", "/api/v2/workflow_approvals/4/approve/")


def test_deny_workflow_posts_to_deny_endpoint():
    api = MagicMock()
    api.request.return_value = {"id": 4, "status": "failed"}

    with patch.object(
        workflow_jobs_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(workflow_jobs_mod.deny_workflow(approval_id=4))

    assert out == {"id": 4, "status": "failed"}
    api.request.assert_called_once_with("POST", "/api/v2/workflow_approvals/4/deny/")

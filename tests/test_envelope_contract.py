# SPDX-License-Identifier: Apache-2.0

"""Tool-level tests for the list envelope contract.

Where tests/test_pagination_meta.py exercises ``handle_pagination(...,
with_meta=True)`` in isolation, these confirm the *tools* expose that envelope
end-to-end: the exact key set, the new default ``limit`` of 20, and the
``offset`` echo. The workflow-approval-template case is included because it
computes ``count`` client-side (after filtering) rather than trusting AWX's
server-reported total.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from awx_mcp.tools import hosts as hosts_mod
from awx_mcp.tools import workflow_jobs as workflow_jobs_mod
from tests.conftest import fake_client_factory


def _paginated(rows, count=None):
    """Shape a single-page AWX list response (count overridable)."""
    return {
        "count": len(rows) if count is None else count,
        "next": None,
        "previous": None,
        "results": rows,
    }


def test_list_tool_returns_exact_envelope_keys():
    api = MagicMock()
    api.request.return_value = _paginated([{"id": 1}, {"id": 2}])

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(hosts_mod.list_hosts())

    assert set(out) == {"count", "returned", "offset", "results"}
    assert out["returned"] == len(out["results"])


def test_default_limit_is_20():
    # 25 rows on one page: the default limit must cap the page_size at 20 and
    # the returned slice at 20 (count still reflects the server total).
    rows = [{"id": i} for i in range(25)]
    api = MagicMock()
    api.request.return_value = _paginated(rows, count=25)

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(hosts_mod.list_hosts())

    assert api.request.call_args.kwargs["params"]["page_size"] == 20
    assert out["returned"] == 20
    assert len(out["results"]) == 20
    assert out["count"] == 25


def test_offset_is_echoed_through_tool_call():
    rows = [{"id": i} for i in range(10)]
    api = MagicMock()
    api.request.return_value = _paginated(rows, count=10)

    with patch.object(hosts_mod, "get_ansible_client", new=fake_client_factory(api)):
        out = json.loads(hosts_mod.list_hosts(offset=7))

    assert out["offset"] == 7
    # offset 7 skips the first 7 rows of the single page, leaving 3.
    assert out["returned"] == 3
    assert out["results"] == rows[7:]


def test_approval_templates_count_is_client_side_after_filter():
    # Two approval nodes among three raw nodes: count must reflect the filtered
    # approval set (2), not the raw node total (3).
    nodes = [
        {
            "id": 1,
            "summary_fields": {
                "unified_job_template": {"unified_job_type": "workflow_approval"}
            },
        },
        {
            "id": 2,
            "summary_fields": {"unified_job_template": {"unified_job_type": "job"}},
        },
        {
            "id": 3,
            "summary_fields": {
                "unified_job_template": {"type": "workflow_approval_template"}
            },
        },
    ]
    api = MagicMock()
    api.request.return_value = _paginated(nodes, count=3)

    with patch.object(
        workflow_jobs_mod, "get_ansible_client", new=fake_client_factory(api)
    ):
        out = json.loads(workflow_jobs_mod.list_workflow_approval_templates())

    assert set(out) == {"count", "returned", "offset", "results"}
    assert out["count"] == 2  # filtered approval nodes, not the raw total of 3
    assert out["returned"] == 2
    assert [n["id"] for n in out["results"]] == [1, 3]


def test_approval_templates_timeout_keeps_envelope_shape():
    # When the full-node scan hits the pagination budget, the tool must still
    # answer in the envelope shape: partial raw nodes are approval-filtered,
    # count is None (server total unknowable), and the timeout error fields
    # ride along instead of replacing the envelope.
    partial_nodes = [
        {
            "id": 1,
            "summary_fields": {
                "unified_job_template": {"unified_job_type": "workflow_approval"}
            },
        },
        {
            "id": 2,
            "summary_fields": {"unified_job_template": {"unified_job_type": "job"}},
        },
    ]
    timeout_page = [
        {
            "error": "pagination_timeout",
            "partial": True,
            "pages_fetched": 4,
            "results": partial_nodes,
            "budget_seconds": 180,
        }
    ]

    api = MagicMock()
    with (
        patch.object(
            workflow_jobs_mod, "get_ansible_client", new=fake_client_factory(api)
        ),
        patch.object(workflow_jobs_mod, "handle_pagination", return_value=timeout_page),
    ):
        out = json.loads(workflow_jobs_mod.list_workflow_approval_templates())

    assert out["error"] == "pagination_timeout"
    assert out["partial"] is True
    assert out["pages_fetched"] == 4
    assert out["budget_seconds"] == 180
    assert out["count"] is None  # scan incomplete: total approvals unknowable
    assert out["offset"] == 0
    assert out["returned"] == 1
    assert [n["id"] for n in out["results"]] == [1]  # approval-filtered partials

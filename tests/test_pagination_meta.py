# SPDX-License-Identifier: Apache-2.0

"""Tests for ``handle_pagination(..., with_meta=True)`` — the opt-in metadata
envelope that tells callers how many items exist server-side and how to page.

The ``with_meta=False`` default is exercised by tests/test_timeout_retry.py; the
final regression test here just confirms the two modes stay independent.
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("ANSIBLE_BASE_URL", "https://test.example.com/")
os.environ.setdefault("ANSIBLE_TOKEN", "test-token")

from awx_mcp.client import handle_pagination  # noqa: E402 — env before import


def test_with_meta_single_page():
    client = MagicMock()
    client.request.return_value = {
        "count": 2,
        "results": [{"id": 1}, {"id": 2}],
        "next": None,
    }
    result = handle_pagination(client, "/api/v2/items/", {}, with_meta=True)
    assert result == {
        "count": 2,
        "returned": 2,
        "offset": 0,
        "results": [{"id": 1}, {"id": 2}],
    }


def test_with_meta_multi_page_limit_spans_pages():
    """count reflects the server total; returned is capped at the limit."""
    client = MagicMock()
    client.request.side_effect = [
        {
            "count": 5,
            "results": [{"id": 1}, {"id": 2}],
            "next": "/api/v2/items/?page=2",
        },
        {
            "count": 5,
            "results": [{"id": 3}, {"id": 4}],
            "next": "/api/v2/items/?page=3",
        },
    ]
    result = handle_pagination(client, "/api/v2/items/", {"limit": 3}, with_meta=True)
    assert result["count"] == 5
    assert result["returned"] == 3
    assert result["offset"] == 0
    assert result["results"] == [{"id": 1}, {"id": 2}, {"id": 3}]


def test_with_meta_offset_echoed():
    client = MagicMock()
    client.request.return_value = {
        "count": 100,
        "results": [{"id": 11}, {"id": 12}],
        "next": None,
    }
    result = handle_pagination(
        client, "/api/v2/items/", {"limit": 2, "offset": 10}, with_meta=True
    )
    assert result["offset"] == 10
    assert result["count"] == 100
    assert result["returned"] == 2


def test_with_meta_zero_limit_no_http():
    client = MagicMock()
    result = handle_pagination(client, "/api/v2/items/", {"limit": 0}, with_meta=True)
    assert result == {"count": None, "returned": 0, "offset": 0, "results": []}
    client.request.assert_not_called()


def test_with_meta_non_paginated_dict_response():
    client = MagicMock()
    client.request.return_value = {"id": 42, "name": "solo"}
    result = handle_pagination(client, "/api/v2/config/", {}, with_meta=True)
    assert result == {
        "count": None,
        "returned": 1,
        "offset": 0,
        "results": [{"id": 42, "name": "solo"}],
    }


def test_with_meta_timeout_partial_envelope(monkeypatch):
    """Cumulative-budget exhaustion folds into the meta envelope with the
    error/partial/pages_fetched keys plus count/returned/offset."""
    import awx_mcp.client as client_mod

    times = iter([0.0, 1.0, 1000.0, 2000.0])

    def fake_time():
        return next(times)

    monkeypatch.setattr(client_mod.time, "monotonic", fake_time)

    client = MagicMock()
    client.request.side_effect = [
        {"count": 9, "results": [{"id": 1}], "next": "/api/v2/items/?page=2"},
        {"count": 9, "results": [{"id": 2}], "next": "/api/v2/items/?page=3"},
    ]
    result = handle_pagination(client, "/api/v2/items/", {}, with_meta=True)
    assert result["error"] == "pagination_timeout"
    assert result["partial"] is True
    assert result["pages_fetched"] >= 1
    assert result["count"] == 9
    assert result["offset"] == 0
    assert result["returned"] == len(result["results"])


def test_with_meta_false_regression_returns_plain_list():
    """Sanity: the default mode still yields a bare list, not an envelope."""
    client = MagicMock()
    client.request.return_value = {
        "count": 2,
        "results": [{"id": 1}, {"id": 2}],
        "next": None,
    }
    result = handle_pagination(client, "/api/v2/items/", {})
    assert result == [{"id": 1}, {"id": 2}]

# SPDX-License-Identifier: Apache-2.0

"""Tests for awx_mcp.client request plumbing and secret masking.

Covers:
- ``_validate_url``: same-origin acceptance, default-port normalization
  (``https://h`` and ``https://h:443`` are the same origin), cross-origin
  rejection.
- ``_resolve_url``/``request``: a subpath base URL (``https://h/awx``) keeps its
  path prefix when joined with an ``/api/...`` endpoint.
- ``request_text``: returns the full body untruncated for non-JSON, where
  ``request`` would truncate to 1000 chars.
- Error masking: a 4xx body echoing a token / Bearer header / password is masked
  both in the raised exception message and in the diagnostic log record.
"""

import logging
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ANSIBLE_BASE_URL", "https://test.example.com/")
os.environ.setdefault("ANSIBLE_TOKEN", "fake-test-token")

from awx_mcp.client import AnsibleClient  # noqa: E402 — env before import
from awx_mcp.exceptions import AnsibleValidationError  # noqa: E402


def make_response(
    status_code=200,
    text='{"ok": true}',
    json_value=None,
    json_error=None,
    content_type="application/json",
):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.headers = {"Content-Type": content_type}
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = (
            json_value if json_value is not None else {"ok": True}
        )
    return response


# --------------------------------------------------------------------------- #
# _validate_url
# --------------------------------------------------------------------------- #
@patch("awx_mcp.client.requests.Session")
def test_validate_url_accepts_same_origin(mock_session_cls):
    mock_session_cls.return_value = MagicMock()
    client = AnsibleClient(base_url="https://h", token="t")

    url = "https://h/api/v2/jobs/"
    assert client._validate_url(url) == url


@patch("awx_mcp.client.requests.Session")
def test_validate_url_normalizes_default_port(mock_session_cls):
    """https://h and https://h:443 are the same origin (443 is the https default),
    so a next-link that spells out the port must not be rejected."""
    mock_session_cls.return_value = MagicMock()

    client_no_port = AnsibleClient(base_url="https://h", token="t")
    assert client_no_port._validate_url("https://h:443/api/v2/x")

    client_with_port = AnsibleClient(base_url="https://h:443", token="t")
    assert client_with_port._validate_url("https://h/api/v2/x")


@patch("awx_mcp.client.requests.Session")
def test_validate_url_rejects_cross_origin(mock_session_cls):
    mock_session_cls.return_value = MagicMock()
    client = AnsibleClient(base_url="https://h", token="t")

    with pytest.raises(ValueError, match="origin mismatch"):
        client._validate_url("https://evil.example.com/api/v2/x")


@patch("awx_mcp.client.requests.Session")
def test_validate_url_rejects_scheme_mismatch(mock_session_cls):
    mock_session_cls.return_value = MagicMock()
    client = AnsibleClient(base_url="https://h", token="t")

    with pytest.raises(ValueError, match="origin mismatch"):
        client._validate_url("http://h/api/v2/x")


# --------------------------------------------------------------------------- #
# subpath base URL resolution
# --------------------------------------------------------------------------- #
@patch("awx_mcp.client.requests.Session")
def test_request_preserves_subpath_prefix_in_resolved_url(mock_session_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session
    mock_session.request.return_value = make_response(200, json_value={"ok": True})
    client = AnsibleClient(base_url="https://h/awx", token="t")

    client.request("GET", "/api/v2/jobs/")

    assert mock_session.request.call_args.kwargs["url"] == "https://h/awx/api/v2/jobs/"


# --------------------------------------------------------------------------- #
# request / request_text bodies
# --------------------------------------------------------------------------- #
@patch("awx_mcp.client.requests.Session")
def test_request_returns_parsed_json(mock_session_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session
    mock_session.request.return_value = make_response(200, json_value={"id": 7})
    client = AnsibleClient(base_url="https://h", token="t")

    assert client.request("GET", "/api/v2/jobs/7/") == {"id": 7}


@patch("awx_mcp.client.requests.Session")
def test_request_text_returns_full_untruncated_body(mock_session_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session
    long_body = "A" * 5000
    mock_session.request.return_value = make_response(
        200, text=long_body, content_type="text/plain"
    )
    client = AnsibleClient(base_url="https://h", token="t")

    result = client.request_text("GET", "/api/v2/metrics/")

    assert result == long_body
    assert len(result) == 5000


@patch("awx_mcp.client.requests.Session")
def test_request_truncates_non_json_body_to_1000_chars(mock_session_cls):
    """Contrast with request_text: the JSON-oriented request() truncates a
    non-JSON fallback body to 1000 chars."""
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session
    long_body = "B" * 5000
    mock_session.request.return_value = make_response(
        200,
        text=long_body,
        json_error=__import__("json").JSONDecodeError("no", "x", 0),
        content_type="text/plain",
    )
    client = AnsibleClient(base_url="https://h", token="t")

    result = client.request("GET", "/api/v2/metrics/")

    assert result["status"] == "success"
    assert len(result["text"]) == 1000


# --------------------------------------------------------------------------- #
# secret masking on error
# --------------------------------------------------------------------------- #
@patch("awx_mcp.client.requests.Session")
def test_error_body_secrets_masked_in_exception_and_log(mock_session_cls, caplog):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session
    secret_body = "denied: token=SEKRET Bearer abc123def password=p4ss"
    mock_session.request.return_value = make_response(400, text=secret_body)
    client = AnsibleClient(base_url="https://h", token="t")

    with caplog.at_level(logging.ERROR, logger="ansible-mcp"):
        with pytest.raises(AnsibleValidationError) as ei:
            client.request("GET", "/api/v2/jobs/")

    msg = str(ei.value)
    # Secrets must not leak into the exception surfaced to the MCP client.
    assert "SEKRET" not in msg
    assert "abc123def" not in msg
    assert "p4ss" not in msg
    assert "***" in msg

    # ...nor into the diagnostic log record.
    assert "SEKRET" not in caplog.text
    assert "abc123def" not in caplog.text
    assert "p4ss" not in caplog.text
    assert "***" in caplog.text

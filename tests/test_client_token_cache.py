# SPDX-License-Identifier: Apache-2.0

"""Tests for awx_mcp.client token acquisition and the get_ansible_client cache.

Covers the two previously untested entry points:
- ``get_ansible_client()`` context manager: static-token path, username/password
  mint-and-cache path, cached-token reuse with ping preflight, and cache refresh
  when a cached token no longer validates.
- ``AnsibleClient.get_token()``: CSRF fetch -> login -> token creation happy path,
  the read/write scope selection, and each authentication error branch.

All HTTP is mocked; no real network access. Module globals imported into
``awx_mcp.client`` (ANSIBLE_TOKEN, ANSIBLE_USERNAME/PASSWORD, READ_ONLY,
_cached_token, _cached_token_id, AnsibleClient) are patched via monkeypatch so
each test is hermetic and restores state afterwards.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ANSIBLE_BASE_URL", "https://test.example.com/")
os.environ.setdefault("ANSIBLE_TOKEN", "fake-test-token")

import awx_mcp.client as client_mod  # noqa: E402 — env must be set before import
from awx_mcp.client import AnsibleClient  # noqa: E402
from awx_mcp.exceptions import AnsibleAPIError, AnsibleAuthError  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def preserve_atexit_targets():
    """Snapshot/restore the module-level atexit revoke list.

    ``get_token()`` appends ``(base_url, token, token_id)`` on success; restoring
    keeps tests from leaking targets into one another (or into a real atexit run).
    """
    saved = list(client_mod._atexit_revoke_targets)
    try:
        yield
    finally:
        client_mod._atexit_revoke_targets[:] = saved


class FakeClient:
    """Stand-in for AnsibleClient used to observe get_ansible_client orchestration.

    Mirrors the real client's context-manager contract: ``__enter__`` mints a
    token via ``get_token()`` when none was supplied but credentials are present
    (exactly what the production ``__enter__`` does).
    """

    instances: list["FakeClient"] = []
    ping_fails: bool = False

    def __init__(self, base_url, username=None, password=None, token=None):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.token = token
        self._token_id = None
        self.get_token_calls = 0
        self.requests: list[tuple[str, str]] = []
        self.entered = False
        self.exited = False
        FakeClient.instances.append(self)

    def __enter__(self):
        if not self.token and self.username and self.password:
            self.get_token()
        self.entered = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exited = True
        return False

    def get_token(self):
        self.get_token_calls += 1
        self.token = "minted-token"
        self._token_id = 123
        return self.token

    def request(self, method, endpoint, params=None, data=None):
        self.requests.append((method, endpoint))
        if FakeClient.ping_fails:
            raise AnsibleAPIError("cached token expired", status_code=401)
        return {"status": "ok"}


@pytest.fixture
def fake_client_class(monkeypatch):
    """Install FakeClient in place of AnsibleClient and reset cache globals."""
    FakeClient.instances = []
    FakeClient.ping_fails = False
    monkeypatch.setattr(client_mod, "AnsibleClient", FakeClient)
    monkeypatch.setattr(client_mod, "ANSIBLE_BASE_URL", "https://awx.example.com")
    monkeypatch.setattr(client_mod, "_cached_token", None)
    monkeypatch.setattr(client_mod, "_cached_token_id", None)
    return FakeClient


# --------------------------------------------------------------------------- #
# get_ansible_client() — static token path
# --------------------------------------------------------------------------- #
def test_get_ansible_client_static_token_uses_env_token_without_minting(
    fake_client_class, monkeypatch
):
    monkeypatch.setattr(client_mod, "ANSIBLE_TOKEN", "env-static-token")
    monkeypatch.setattr(client_mod, "ANSIBLE_USERNAME", "u")
    monkeypatch.setattr(client_mod, "ANSIBLE_PASSWORD", "p")

    with client_mod.get_ansible_client() as client:
        assert client.token == "env-static-token"

    assert len(fake_client_class.instances) == 1
    only = fake_client_class.instances[0]
    assert only.get_token_calls == 0
    assert only.requests == []  # static path does no ping preflight
    assert only.exited is True


# --------------------------------------------------------------------------- #
# get_ansible_client() — username/password mint + cache reuse
# --------------------------------------------------------------------------- #
def test_get_ansible_client_mints_then_reuses_cached_token(
    fake_client_class, monkeypatch
):
    monkeypatch.setattr(client_mod, "ANSIBLE_TOKEN", None)
    monkeypatch.setattr(client_mod, "ANSIBLE_USERNAME", "u")
    monkeypatch.setattr(client_mod, "ANSIBLE_PASSWORD", "p")

    # First call: no cached token -> mint via get_token().
    with client_mod.get_ansible_client() as first:
        assert first.token == "minted-token"
    assert first.get_token_calls == 1
    assert client_mod._cached_token == "minted-token"

    # Second call: cached token present -> reused, no new mint.
    with client_mod.get_ansible_client() as second:
        assert second.token == "minted-token"
    assert second.get_token_calls == 0
    assert first is not second


def test_get_ansible_client_cached_token_does_ping_preflight(
    fake_client_class, monkeypatch
):
    """ACTUAL behavior: the cached-token path validates with a /api/v2/ping/ GET
    before yielding. (This documents that the ping preflight is still present.)"""
    monkeypatch.setattr(client_mod, "ANSIBLE_TOKEN", None)
    monkeypatch.setattr(client_mod, "ANSIBLE_USERNAME", "u")
    monkeypatch.setattr(client_mod, "ANSIBLE_PASSWORD", "p")
    monkeypatch.setattr(client_mod, "_cached_token", "pre-cached-token")

    with client_mod.get_ansible_client() as client:
        assert client.token == "pre-cached-token"

    assert client.get_token_calls == 0
    assert ("GET", "/api/v2/ping/") in client.requests


def test_get_ansible_client_refreshes_when_cached_token_invalid(
    fake_client_class, monkeypatch
):
    """Cached token fails its ping preflight -> cache invalidated and a fresh
    token minted for the yielded client."""
    monkeypatch.setattr(client_mod, "ANSIBLE_TOKEN", None)
    monkeypatch.setattr(client_mod, "ANSIBLE_USERNAME", "u")
    monkeypatch.setattr(client_mod, "ANSIBLE_PASSWORD", "p")
    monkeypatch.setattr(client_mod, "_cached_token", "stale-token")
    fake_client_class.ping_fails = True

    with client_mod.get_ansible_client() as client:
        assert client.token == "minted-token"

    # First instance is the stale cached client (ping failed); a later instance
    # minted the replacement token.
    stale = fake_client_class.instances[0]
    assert stale.token == "stale-token"
    assert stale.exited is True
    assert any(inst.get_token_calls == 1 for inst in fake_client_class.instances)
    assert client_mod._cached_token == "minted-token"


# --------------------------------------------------------------------------- #
# AnsibleClient.get_token() — happy path and scope
# --------------------------------------------------------------------------- #
def _mock_login_session(mock_session_cls, *, csrf_cookie="csrf-abc"):
    """Wire a mocked Session whose login page returns a CSRF cookie."""
    session = MagicMock()
    mock_session_cls.return_value = session
    login_page = MagicMock()
    login_page.cookies = {"csrftoken": csrf_cookie} if csrf_cookie else {}
    login_page.text = ""
    session.get.return_value = login_page
    session.cookies = {"csrftoken": csrf_cookie} if csrf_cookie else {}
    return session


@patch("awx_mcp.client.requests.Session")
def test_get_token_success_returns_and_stores_token(
    mock_session_cls, preserve_atexit_targets
):
    session = _mock_login_session(mock_session_cls)
    login_resp = MagicMock(status_code=302)
    token_resp = MagicMock(status_code=201)
    token_resp.json.return_value = {"token": "the-token", "id": 55}
    session.post.side_effect = [login_resp, token_resp]

    client = AnsibleClient(
        base_url="https://awx.example.com", username="u", password="p"
    )
    result = client.get_token()

    assert result == "the-token"
    assert client.token == "the-token"
    assert client._token_id == 55
    # token_id registered for atexit revocation
    assert (
        "https://awx.example.com",
        "the-token",
        55,
    ) in client_mod._atexit_revoke_targets


@patch("awx_mcp.client.requests.Session")
def test_get_token_uses_csrf_from_html_when_no_cookie(
    mock_session_cls, preserve_atexit_targets
):
    session = _mock_login_session(mock_session_cls, csrf_cookie=None)
    session.get.return_value.text = (
        '<input name="csrfmiddlewaretoken" value="html-csrf-token">'
    )
    login_resp = MagicMock(status_code=302)
    token_resp = MagicMock(status_code=201)
    token_resp.json.return_value = {"token": "tok", "id": 1}
    session.post.side_effect = [login_resp, token_resp]

    client = AnsibleClient(
        base_url="https://awx.example.com", username="u", password="p"
    )
    result = client.get_token()

    assert result == "tok"
    # The CSRF token scraped from the HTML is sent as the login header.
    login_headers = session.post.call_args_list[0].kwargs["headers"]
    assert login_headers["X-CSRFToken"] == "html-csrf-token"


@patch("awx_mcp.client.requests.Session")
def test_get_token_scope_is_write_by_default(
    mock_session_cls, preserve_atexit_targets, monkeypatch
):
    monkeypatch.setattr(client_mod, "READ_ONLY", False)
    session = _mock_login_session(mock_session_cls)
    token_resp = MagicMock(status_code=201)
    token_resp.json.return_value = {"token": "t", "id": 2}
    session.post.side_effect = [MagicMock(status_code=302), token_resp]

    client = AnsibleClient(
        base_url="https://awx.example.com", username="u", password="p"
    )
    client.get_token()

    token_body = session.post.call_args_list[1].kwargs["json"]
    assert token_body["scope"] == "write"


@patch("awx_mcp.client.requests.Session")
def test_get_token_scope_is_read_when_read_only(
    mock_session_cls, preserve_atexit_targets, monkeypatch
):
    monkeypatch.setattr(client_mod, "READ_ONLY", True)
    session = _mock_login_session(mock_session_cls)
    token_resp = MagicMock(status_code=201)
    token_resp.json.return_value = {"token": "t", "id": 3}
    session.post.side_effect = [MagicMock(status_code=302), token_resp]

    client = AnsibleClient(
        base_url="https://awx.example.com", username="u", password="p"
    )
    client.get_token()

    token_body = session.post.call_args_list[1].kwargs["json"]
    assert token_body["scope"] == "read"


# --------------------------------------------------------------------------- #
# AnsibleClient.get_token() — error branches
# --------------------------------------------------------------------------- #
@patch("awx_mcp.client.requests.Session")
def test_get_token_missing_csrf_raises_auth_error(mock_session_cls):
    _mock_login_session(mock_session_cls, csrf_cookie=None)  # no cookie, empty html

    client = AnsibleClient(
        base_url="https://awx.example.com", username="u", password="p"
    )
    with pytest.raises(AnsibleAuthError) as ei:
        client.get_token()
    assert "CSRF" in str(ei.value)


@patch("awx_mcp.client.requests.Session")
def test_get_token_login_4xx_raises_auth_error(mock_session_cls):
    session = _mock_login_session(mock_session_cls)
    login_resp = MagicMock(status_code=401)
    login_resp.text = "invalid credentials"
    session.post.side_effect = [login_resp]

    client = AnsibleClient(
        base_url="https://awx.example.com", username="u", password="bad"
    )
    with pytest.raises(AnsibleAuthError) as ei:
        client.get_token()
    assert ei.value.status_code == 401


@patch("awx_mcp.client.requests.Session")
def test_get_token_token_creation_non_201_raises_auth_error(mock_session_cls):
    session = _mock_login_session(mock_session_cls)
    login_resp = MagicMock(status_code=302)
    token_resp = MagicMock(status_code=403)
    token_resp.text = "forbidden"
    session.post.side_effect = [login_resp, token_resp]

    client = AnsibleClient(
        base_url="https://awx.example.com", username="u", password="p"
    )
    with pytest.raises(AnsibleAuthError) as ei:
        client.get_token()
    assert ei.value.status_code == 403

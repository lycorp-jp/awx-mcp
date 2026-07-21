# SPDX-License-Identifier: Apache-2.0

"""Tests for the thread-local shared requests.Session reuse in awx_mcp.client.

The static/cached-token paths of ``get_ansible_client()`` inject a per-thread
shared ``requests.Session`` (post-construction) so connection-pool keep-alive is
reused across tool calls instead of paying a TLS handshake every call. These
tests fix that contract:

- same thread -> identical session object, verify + retry adapter configured;
- ``AnsibleClient.__exit__`` does not close a session it does not own;
- different threads get different sessions (per-thread pool isolation);
- a directly-constructed ``AnsibleClient`` still owns and closes its session.

The static-token path does no network I/O (no ping preflight, no minting), so a
real ``requests.Session`` can be created without touching AWX.
"""

import os
import threading
from unittest.mock import MagicMock, patch

import pytest
from requests.adapters import HTTPAdapter

os.environ.setdefault("ANSIBLE_BASE_URL", "https://test.example.com/")
os.environ.setdefault("ANSIBLE_TOKEN", "fake-test-token")

import awx_mcp.client as client_mod  # noqa: E402 — env must be set before import
from awx_mcp.client import AnsibleClient  # noqa: E402


@pytest.fixture
def preserve_atexit_targets():
    """Snapshot/restore the module-level atexit revoke list."""
    saved = list(client_mod._atexit_revoke_targets)
    try:
        yield
    finally:
        client_mod._atexit_revoke_targets[:] = saved


def _mint_client(mock_session_cls, *, token, token_id):
    """Drive AnsibleClient.get_token() with a mocked login/token session."""
    session = MagicMock()
    mock_session_cls.return_value = session
    login_page = MagicMock()
    login_page.cookies = {"csrftoken": "csrf"}
    login_page.text = ""
    session.get.return_value = login_page
    session.cookies = {"csrftoken": "csrf"}
    token_resp = MagicMock(status_code=201)
    token_resp.json.return_value = {"token": token, "id": token_id}
    session.post.side_effect = [MagicMock(status_code=302), token_resp]
    client = AnsibleClient(
        base_url="https://awx.example.com", username="u", password="p"
    )
    client.get_token()
    return client


@pytest.fixture
def reset_thread_local_session():
    """Clear this thread's cached shared session before and after the test."""
    if hasattr(client_mod._thread_local, "session"):
        del client_mod._thread_local.session
    try:
        yield
    finally:
        if hasattr(client_mod._thread_local, "session"):
            del client_mod._thread_local.session


@pytest.fixture
def static_token_env(monkeypatch):
    """Force the static-token path of get_ansible_client() (no network)."""
    monkeypatch.setattr(client_mod, "AUTH_MODE", "static")
    monkeypatch.setattr(client_mod, "ANSIBLE_TOKEN", "static-tok")
    monkeypatch.setattr(client_mod, "ANSIBLE_BASE_URL", "https://awx.example.com")
    return None


# --------------------------------------------------------------------------- #
# same-thread reuse
# --------------------------------------------------------------------------- #
def test_static_path_reuses_same_session_within_thread(
    static_token_env, reset_thread_local_session
):
    with client_mod.get_ansible_client() as first:
        first_session = first.session
    with client_mod.get_ansible_client() as second:
        second_session = second.session

    assert first_session is second_session
    assert id(first_session) == id(second_session)


def test_shared_session_configures_verify_and_retry_adapters(
    static_token_env, reset_thread_local_session
):
    with client_mod.get_ansible_client() as client:
        session = client.session

    assert session.verify == client_mod.ANSIBLE_SSL_VERIFY
    for scheme in ("http://", "https://"):
        adapter = session.adapters[scheme]
        assert isinstance(adapter, HTTPAdapter)
        assert adapter.max_retries.total == 3


def test_static_path_client_does_not_own_shared_session(
    static_token_env, reset_thread_local_session
):
    with client_mod.get_ansible_client() as client:
        assert client._owns_session is False


# --------------------------------------------------------------------------- #
# __exit__ ownership guard
# --------------------------------------------------------------------------- #
def test_exit_does_not_close_when_not_owner():
    client = AnsibleClient(base_url="https://awx.example.com", token="t")
    client.session = MagicMock()
    client._owns_session = False

    client.__exit__(None, None, None)

    client.session.close.assert_not_called()


def test_exit_closes_when_owner():
    client = AnsibleClient(base_url="https://awx.example.com", token="t")
    client.session = MagicMock()
    # _owns_session defaults to True from __init__

    client.__exit__(None, None, None)

    client.session.close.assert_called_once()


def test_direct_client_owns_its_session_by_default():
    client = AnsibleClient(base_url="https://awx.example.com", token="t")
    assert client._owns_session is True


# --------------------------------------------------------------------------- #
# per-thread isolation
# --------------------------------------------------------------------------- #
def test_different_threads_get_different_sessions(static_token_env):
    results: dict[int, int] = {}

    def worker(key: int) -> None:
        # Ensure a fresh thread-local session for this worker thread.
        if hasattr(client_mod._thread_local, "session"):
            del client_mod._thread_local.session
        with client_mod.get_ansible_client() as client:
            results[key] = id(client.session)

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results[1] != results[2]


# --------------------------------------------------------------------------- #
# atexit revoke-target supersede on re-mint
# --------------------------------------------------------------------------- #
@patch("awx_mcp.client.requests.Session")
def test_re_mint_supersedes_prior_revoke_target_for_same_base_url(
    mock_session_cls, preserve_atexit_targets
):
    client_mod._atexit_revoke_targets[:] = []

    _mint_client(mock_session_cls, token="tok-1", token_id=1)
    _mint_client(mock_session_cls, token="tok-2", token_id=2)

    same_base = [
        t
        for t in client_mod._atexit_revoke_targets
        if t[0] == "https://awx.example.com"
    ]
    assert same_base == [("https://awx.example.com", "tok-2", 2)]


@patch("awx_mcp.client.requests.Session")
def test_re_mint_keeps_targets_for_other_base_urls(
    mock_session_cls, preserve_atexit_targets
):
    client_mod._atexit_revoke_targets[:] = [("https://other.example.com", "keep", 9)]

    _mint_client(mock_session_cls, token="tok-1", token_id=1)

    assert ("https://other.example.com", "keep", 9) in client_mod._atexit_revoke_targets
    assert ("https://awx.example.com", "tok-1", 1) in client_mod._atexit_revoke_targets

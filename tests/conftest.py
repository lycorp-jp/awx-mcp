# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for the awx-mcp test suite.

The credential-management env flag is read at import time by ``awx_mcp.server``,
so this fixture sets it via ``os.environ`` BEFORE awx_mcp is imported by any
test module that needs the gated tools.
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("ANSIBLE_BASE_URL", "https://test.example.com/")
os.environ.setdefault("ANSIBLE_TOKEN", "fake-test-token")


@dataclass
class _FakeElicitData:
    inputs: str | None = None
    password: str | None = None


@dataclass
class _FakeElicitResult:
    action: str  # "accept" | "decline" | "cancel"
    data: _FakeElicitData | None = None


class FakeContext:
    """Duck-typed stand-in for ``mcp.server.mcpserver.Context``.

    Only implements the ``elicit`` async method. Production code reads
    ``result.action`` and ``result.data.<field>``; nothing else.
    """

    def __init__(self, scripted: _FakeElicitResult):
        self._scripted = scripted

    async def elicit(self, message: str, schema):  # noqa: ANN001 — duck-typed
        return self._scripted


def fake_client_factory(api_mock: MagicMock):
    """Build a context-manager replacement for ``get_ansible_client``.

    The replacement returns a context manager that yields ``api_mock`` (so tests
    can assert on ``client.request(...)`` calls). Used with
    ``patch("awx_mcp.tools.<module>.get_ansible_client", new=...)``.
    """

    @contextmanager
    def _factory():
        yield api_mock

    return _factory


@pytest.fixture
def fake_client():
    """Yield a fresh MagicMock for ``client.request``."""
    api = MagicMock()
    api.request.return_value = {"id": 99, "name": "ok"}
    return api


@pytest.fixture
def fake_ctx_accept():
    return FakeContext(
        _FakeElicitResult(
            "accept",
            _FakeElicitData(inputs='{"username":"u","password":"p"}'),
        )
    )


@pytest.fixture
def fake_ctx_decline():
    return FakeContext(_FakeElicitResult("decline", None))


@pytest.fixture
def fake_ctx_cancel():
    return FakeContext(_FakeElicitResult("cancel", None))


@pytest.fixture
def fake_ctx_invalid_json():
    return FakeContext(
        _FakeElicitResult("accept", _FakeElicitData(inputs="not-json{{"))
    )


@pytest.fixture
def fake_ctx_password_accept():
    return FakeContext(_FakeElicitResult("accept", _FakeElicitData(password="s3cret")))

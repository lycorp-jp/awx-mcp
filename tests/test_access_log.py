# SPDX-License-Identifier: Apache-2.0

"""Tests for the JSON HTTP access log (awx_mcp.access_log)."""

import json

import anyio
import pytest

from awx_mcp import access_log
from awx_mcp.access_log import AccessLogMiddleware, access_log_enabled


@pytest.fixture(autouse=True)
def _fresh_sink(monkeypatch, tmp_path):
    """Point the access log at a per-test file and reset the cached logger."""
    path = tmp_path / "access.jsonl"
    monkeypatch.setenv("AWX_MCP_ACCESS_LOG_FILE", str(path))
    access_log._reset_for_tests()
    yield path
    access_log._reset_for_tests()


def _http_scope(**overrides):
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "client": ("10.0.0.5", 51234),
        "headers": [
            (b"user-agent", b"pytest-client/1.0"),
            (b"authorization", b"Bearer secret-token"),
        ],
    }
    scope.update(overrides)
    return scope


def _run(middleware, scope):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    anyio.run(middleware, scope, receive, send)
    return sent


def _read_entries(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _ok_app(status=200):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


def test_records_basic_request(_fresh_sink):
    mw = AccessLogMiddleware(_ok_app(202))
    _run(mw, _http_scope())

    entries = _read_entries(_fresh_sink)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["client_ip"] == "10.0.0.5"
    assert entry["method"] == "POST"
    assert entry["path"] == "/mcp"
    assert entry["status"] == 202
    assert entry["user_agent"] == "pytest-client/1.0"
    assert entry["has_auth"] is True
    assert isinstance(entry["latency_ms"], int)
    assert "@timestamp" in entry
    # The token itself must never be logged.
    assert "secret-token" not in _fresh_sink.read_text()


def test_x_forwarded_for_wins_over_peer(_fresh_sink):
    scope = _http_scope(headers=[(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1")])
    _run(AccessLogMiddleware(_ok_app()), scope)

    entry = _read_entries(_fresh_sink)[0]
    assert entry["client_ip"] == "203.0.113.9"
    assert entry["has_auth"] is False
    assert entry["user_agent"] is None


def test_x_awx_token_counts_as_auth(_fresh_sink):
    scope = _http_scope(headers=[(b"x-awx-token", b"tok")])
    _run(AccessLogMiddleware(_ok_app()), scope)

    assert _read_entries(_fresh_sink)[0]["has_auth"] is True


def test_crash_before_response_logs_null_status(_fresh_sink):
    async def crashing_app(scope, receive, send):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _run(AccessLogMiddleware(crashing_app), _http_scope())

    entry = _read_entries(_fresh_sink)[0]
    assert entry["status"] is None


def test_non_http_scope_passthrough(_fresh_sink):
    seen = {}

    async def app(scope, receive, send):
        seen["scope"] = scope["type"]

    _run(AccessLogMiddleware(app), {"type": "lifespan"})

    assert seen["scope"] == "lifespan"
    assert not _fresh_sink.exists() or _fresh_sink.read_text() == ""


def test_disabled_without_env(monkeypatch, tmp_path, _fresh_sink):
    monkeypatch.delenv("AWX_MCP_ACCESS_LOG_FILE")
    access_log._reset_for_tests()
    assert not access_log_enabled()

    _run(AccessLogMiddleware(_ok_app()), _http_scope())
    assert not _fresh_sink.exists() or _fresh_sink.read_text() == ""


def test_response_passes_through_unchanged(_fresh_sink):
    sent = _run(AccessLogMiddleware(_ok_app(200)), _http_scope())
    assert sent[0] == {"type": "http.response.start", "status": 200, "headers": []}
    assert sent[1] == {"type": "http.response.body", "body": b"ok"}


def test_serve_mode_mirrors_to_stdout(monkeypatch, _fresh_sink, capsys):
    monkeypatch.setenv("AWX_MCP_EFFECTIVE_TRANSPORT", "streamable-http")
    access_log._reset_for_tests()

    _run(AccessLogMiddleware(_ok_app(201)), _http_scope())

    entry = json.loads(capsys.readouterr().out.strip())
    assert entry["status"] == 201
    assert entry["path"] == "/mcp"
    # File sink still written alongside stdout.
    assert _read_entries(_fresh_sink)[0]["status"] == 201


def test_serve_mode_stdout_only_without_file(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("AWX_MCP_ACCESS_LOG_FILE", raising=False)
    monkeypatch.setenv("AWX_MCP_EFFECTIVE_TRANSPORT", "sse")
    access_log._reset_for_tests()

    assert access_log_enabled()
    _run(AccessLogMiddleware(_ok_app()), _http_scope())

    entry = json.loads(capsys.readouterr().out.strip())
    assert entry["method"] == "POST"
    access_log._reset_for_tests()


def test_stdio_mode_never_writes_stdout(monkeypatch, _fresh_sink, capsys):
    monkeypatch.setenv("AWX_MCP_EFFECTIVE_TRANSPORT", "stdio")
    access_log._reset_for_tests()

    _run(AccessLogMiddleware(_ok_app()), _http_scope())

    assert capsys.readouterr().out == ""
    assert _read_entries(_fresh_sink)[0]["status"] == 200

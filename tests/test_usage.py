# SPDX-License-Identifier: Apache-2.0

"""Tests for the file-based usage instrumentation (awx_mcp.usage).

``awx_mcp.usage`` reads ``AWX_MCP_USAGE_LOG_FILE`` at import time, so tests set
the env var and ``importlib.reload`` the module to pick it up, mirroring the
env-at-import pattern already used by the server tests. The server diagnostic
log file is covered via a fresh subprocess (server.py also reads its env at
import time).
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import textwrap

os.environ.setdefault("ANSIBLE_BASE_URL", "https://test.example.com/")
os.environ.setdefault("ANSIBLE_TOKEN", "fake-test-token")

import pytest  # noqa: E402

import awx_mcp.usage as usage  # noqa: E402

REQUIRED_FIELDS = {
    "@timestamp",
    "type",
    "user",
    "tool",
    "trace_id",
    "server_version",
    "success",
    "latency_ms",
    "transport",
    "awx_host",
}


def _reload_usage(monkeypatch, **env: str | None):
    """Reload awx_mcp.usage with the given env vars applied."""
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    importlib.reload(usage)
    return usage


def _read_lines(path):
    return [line for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# disabled (env unset) -> pass-through, no file
# ---------------------------------------------------------------------------


def test_disabled_is_passthrough_and_creates_no_file(monkeypatch, tmp_path):
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=None)

    assert mod._is_enabled() is False
    assert mod._get_usage_logger() is None

    calls: list[int] = []

    @mod.instrument_tool
    def my_tool(x):
        calls.append(x)
        return x * 2

    assert my_tool(21) == 42
    assert calls == [21]
    # Nothing under tmp_path should have been created.
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# enabled -> one JSON line per call with the required fields
# ---------------------------------------------------------------------------


def test_enabled_appends_one_valid_json_line(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    # Pin ANSIBLE_BASE_URL so awx_host is deterministic regardless of the
    # ambient value (CI sets its own ANSIBLE_BASE_URL for the job).
    mod = _reload_usage(
        monkeypatch,
        AWX_MCP_USAGE_LOG_FILE=str(log_file),
        ANSIBLE_BASE_URL="https://test.example.com/",
    )
    monkeypatch.setattr(mod, "resolve_user_identifier", lambda: "tester")

    @mod.instrument_tool
    def my_tool():
        return "ok"

    assert my_tool() == "ok"

    lines = _read_lines(log_file)
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert REQUIRED_FIELDS <= set(entry)
    assert entry["tool"] == "my_tool"
    assert entry["user"] == "tester"
    assert entry["success"] is True
    assert entry["transport"] == "stdio"
    assert entry["awx_host"] == "test.example.com"
    assert "error" not in entry
    assert isinstance(entry["latency_ms"], int)


def test_each_call_appends_a_line(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))
    monkeypatch.setattr(mod, "resolve_user_identifier", lambda: "tester")

    @mod.instrument_tool
    def my_tool():
        return 1

    my_tool()
    my_tool()
    my_tool()

    assert len(_read_lines(log_file)) == 3


# ---------------------------------------------------------------------------
# tool call parameters -> logged as `params`, secrets redacted
# ---------------------------------------------------------------------------


def test_tool_params_are_logged(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))
    monkeypatch.setattr(mod, "resolve_user_identifier", lambda: "tester")

    @mod.instrument_tool
    def my_tool(inventory_id=None, name=None):
        return "ok"

    my_tool(inventory_id=5, name="web-01")

    entry = json.loads(_read_lines(log_file)[0])
    assert entry["params"] == {"inventory_id": 5, "name": "web-01"}


def test_no_params_omits_field(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))
    monkeypatch.setattr(mod, "resolve_user_identifier", lambda: "tester")

    @mod.instrument_tool
    def my_tool():
        return "ok"

    my_tool()

    entry = json.loads(_read_lines(log_file)[0])
    assert "params" not in entry


def test_sensitive_params_are_redacted(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))
    monkeypatch.setattr(mod, "resolve_user_identifier", lambda: "tester")

    @mod.instrument_tool
    def create_credential(name=None, password=None, inputs=None, extra=None):
        return "ok"

    create_credential(
        name="db",
        password="s3cret",
        inputs={"ssh_key_data": "PRIVATE"},
        extra="token=abc123",
    )

    entry = json.loads(_read_lines(log_file)[0])
    params = entry["params"]
    # Sensitive-named keys are fully redacted.
    assert params["password"] == "***"
    assert params["inputs"] == "***"
    # Non-sensitive keys keep their value, but inline secrets are masked.
    assert params["name"] == "db"
    assert params["extra"] == "token=***"


def test_freeform_var_blob_is_redacted(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))
    monkeypatch.setattr(mod, "resolve_user_identifier", lambda: "tester")

    @mod.instrument_tool
    def launch_job(template_id=None, extra_vars=None):
        return "ok"

    launch_job(template_id=5, extra_vars='{"api_key": "SECRET", "region": "us"}')

    params = json.loads(_read_lines(log_file)[0])["params"]
    assert params["template_id"] == 5
    # extra_vars is a free-form blob — redacted wholesale, secret never logged.
    assert params["extra_vars"] == "***"


def test_nested_secret_keys_are_redacted(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))
    monkeypatch.setattr(mod, "resolve_user_identifier", lambda: "tester")

    @mod.instrument_tool
    def configure(payload=None, blob=None):
        return "ok"

    configure(
        payload={"host": "h", "nested": {"api_token": "SECRET", "port": 22}},
        blob='{"ssh_private_key": "SECRET", "user": "root"}',
    )

    params = json.loads(_read_lines(log_file)[0])["params"]
    # dict value: benign keys kept, nested secret-named key redacted.
    assert params["payload"]["host"] == "h"
    assert params["payload"]["nested"]["port"] == 22
    assert params["payload"]["nested"]["api_token"] == "***"
    # JSON string value: parsed and nested secret redacted, benign key kept.
    assert params["blob"]["user"] == "root"
    assert params["blob"]["ssh_private_key"] == "***"
    assert "SECRET" not in _read_lines(log_file)[0]


# ---------------------------------------------------------------------------
# failure path -> success=false + error{type,message}, exception propagates
# ---------------------------------------------------------------------------


def test_failed_call_records_error_and_propagates(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))
    monkeypatch.setattr(mod, "resolve_user_identifier", lambda: "tester")

    @mod.instrument_tool
    def failing_tool():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        failing_tool()

    entry = json.loads(_read_lines(log_file)[0])
    assert entry["success"] is False
    assert entry["error"]["type"] == "ValueError"
    assert entry["error"]["message"] == "boom"


def test_error_message_is_secret_masked(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))
    monkeypatch.setattr(mod, "resolve_user_identifier", lambda: "tester")

    @mod.instrument_tool
    def leaky_tool():
        raise RuntimeError("auth failed: Authorization: Bearer supersecret123")

    with pytest.raises(RuntimeError):
        leaky_tool()

    message = json.loads(_read_lines(log_file)[0])["error"]["message"]
    assert "supersecret123" not in message
    assert "Bearer ***" in message


# ---------------------------------------------------------------------------
# fire-and-forget: logging-internal failure must not affect the tool
# ---------------------------------------------------------------------------


def test_logging_failure_does_not_break_tool(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))

    def _boom(**kwargs):
        raise RuntimeError("payload build exploded")

    monkeypatch.setattr(mod, "build_payload", _boom)

    @mod.instrument_tool
    def my_tool():
        return "still-fine"

    # Tool return value is unaffected despite the instrumentation blowing up.
    assert my_tool() == "still-fine"
    assert _read_lines(log_file) == []


def test_logging_failure_preserves_original_exception(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))

    def _boom(**kwargs):
        raise RuntimeError("instrumentation error, not the tool error")

    monkeypatch.setattr(mod, "build_payload", _boom)

    @mod.instrument_tool
    def failing_tool():
        raise KeyError("original")

    # The tool's own exception propagates, not the instrumentation's.
    with pytest.raises(KeyError, match="original"):
        failing_tool()


# ---------------------------------------------------------------------------
# async tools
# ---------------------------------------------------------------------------


def test_async_tool_is_instrumented(monkeypatch, tmp_path):
    import asyncio

    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))
    monkeypatch.setattr(mod, "resolve_user_identifier", lambda: "tester")

    @mod.instrument_tool
    async def my_async_tool():
        return "async-ok"

    assert asyncio.run(my_async_tool()) == "async-ok"

    entry = json.loads(_read_lines(log_file)[0])
    assert entry["tool"] == "my_async_tool"
    assert entry["success"] is True


# ---------------------------------------------------------------------------
# user resolution failure -> "unknown", tool call unaffected
# ---------------------------------------------------------------------------


def test_user_resolution_failure_falls_back_to_unknown(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))

    def _raise():
        raise RuntimeError("me lookup failed")

    # Force the /api/v2/me/ lookup to fail; resolve_user_identifier must swallow.
    monkeypatch.setattr("awx_mcp.client.get_ansible_client", _raise)

    @mod.instrument_tool
    def my_tool():
        return "ok"

    assert my_tool() == "ok"

    entries = [json.loads(line) for line in _read_lines(log_file)]
    # Two entries: the internal /api/v2/me/ attempt (failed) and the tool call.
    me_entry = next(e for e in entries if e["type"] == "internal_api")
    tool_entry = next(e for e in entries if e["type"] == "tool")

    assert me_entry["tool"] == "me"
    assert me_entry["method"] == "GET"
    assert me_entry["endpoint"] == "/api/v2/me/"
    assert me_entry["success"] is False

    # The failed user lookup must not affect the tool call.
    assert tool_entry["user"] == "unknown"
    assert tool_entry["success"] is True


# ---------------------------------------------------------------------------
# /api/v2/me/ lookup is itself recorded as an internal_api entry
# ---------------------------------------------------------------------------


def test_me_lookup_recorded_as_internal_api(monkeypatch, tmp_path):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, endpoint):
            assert endpoint == "/api/v2/me/"
            return {"results": [{"username": "alice"}]}

    monkeypatch.setattr("awx_mcp.client.get_ansible_client", lambda: _FakeClient())

    @mod.instrument_tool
    def my_tool():
        return "ok"

    assert my_tool() == "ok"

    entries = [json.loads(line) for line in _read_lines(log_file)]
    me_entry = next(e for e in entries if e["type"] == "internal_api")
    tool_entry = next(e for e in entries if e["type"] == "tool")

    assert me_entry["tool"] == "me"
    assert me_entry["method"] == "GET"
    assert me_entry["endpoint"] == "/api/v2/me/"
    assert me_entry["success"] is True
    assert me_entry["user"] == "alice"
    # The /me/ call is resolved once and attributed to the tool call too.
    assert tool_entry["user"] == "alice"
    assert tool_entry["type"] == "tool"


# ---------------------------------------------------------------------------
# stdout must never receive log output (stdio protocol protection)
# ---------------------------------------------------------------------------


def test_usage_logging_writes_nothing_to_stdout(monkeypatch, tmp_path, capsys):
    log_file = tmp_path / "usage.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))
    monkeypatch.setattr(mod, "resolve_user_identifier", lambda: "tester")

    @mod.instrument_tool
    def my_tool():
        return "ok"

    my_tool()

    captured = capsys.readouterr()
    assert captured.out == ""


# ---------------------------------------------------------------------------
# rotation / backup count helpers
# ---------------------------------------------------------------------------


def test_backup_count_defaults_and_override(monkeypatch):
    mod = _reload_usage(monkeypatch, AWX_MCP_LOG_BACKUP_COUNT=None)
    assert mod.log_backup_count() == 7

    monkeypatch.setenv("AWX_MCP_LOG_BACKUP_COUNT", "3")
    assert mod.log_backup_count() == 3

    monkeypatch.setenv("AWX_MCP_LOG_BACKUP_COUNT", "not-an-int")
    assert mod.log_backup_count() == 7


def test_handler_uses_midnight_utc_rotation(monkeypatch, tmp_path):
    mod = _reload_usage(monkeypatch, AWX_MCP_LOG_BACKUP_COUNT="5")
    handler = mod.make_timed_rotating_handler(str(tmp_path / "x.log"))
    try:
        assert handler.when == "MIDNIGHT"
        assert handler.utc is True
        assert handler.backupCount == 5
    finally:
        handler.close()


# ---------------------------------------------------------------------------
# server diagnostic log file (subprocess: server.py reads env at import)
# ---------------------------------------------------------------------------


def _run_server_log_subprocess(tmp_path, log_format: str):
    log_file = tmp_path / "server.log"
    script = textwrap.dedent(
        f"""
        import os, sys
        os.environ["ANSIBLE_BASE_URL"] = "https://awx.example.com/"
        os.environ["ANSIBLE_TOKEN"] = "dummy"
        os.environ["AWX_MCP_SERVER_LOG_FILE"] = {str(log_file)!r}
        os.environ["AWX_MCP_SERVER_LOG_FORMAT"] = {log_format!r}
        from awx_mcp import server
        server.logger.warning("diagnostic-probe-message")
        sys.stdout.write("STDOUT_SENTINEL")
        """
    ).strip()
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "."},
    )
    return log_file, result


def test_server_log_file_json_format(tmp_path):
    log_file, result = _run_server_log_subprocess(tmp_path, "json")

    lines = _read_lines(log_file)
    assert lines, "server log file should contain at least one line"
    probe = None
    for line in lines:
        entry = json.loads(line)  # every line must be valid JSON
        assert {"@timestamp", "type", "level", "logger", "message"} <= set(entry)
        assert entry["type"] == "diagnostic"
        if entry["message"] == "diagnostic-probe-message":
            probe = entry
    assert probe is not None
    assert probe["level"] == "WARNING"
    assert probe["logger"] == "ansible-mcp"

    # stdout carries only the protocol sentinel, never log output.
    assert result.stdout == "STDOUT_SENTINEL"
    assert "diagnostic-probe-message" not in result.stdout


def test_server_log_file_plain_format(tmp_path):
    log_file, result = _run_server_log_subprocess(tmp_path, "plain")

    text = log_file.read_text()
    assert " - ansible-mcp - WARNING - diagnostic-probe-message" in text
    # Plain format is not JSON.
    with pytest.raises(json.JSONDecodeError):
        json.loads(text.splitlines()[0])

    assert result.stdout == "STDOUT_SENTINEL"
    assert "diagnostic-probe-message" not in result.stdout


# ---------------------------------------------------------------------------
# passthrough: per-token user cache + attribution (no raw token in the log)
# ---------------------------------------------------------------------------


def test_passthrough_per_token_user_cache(monkeypatch, tmp_path):
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(tmp_path / "u.jsonl"))
    monkeypatch.setattr("awx_mcp.server.AUTH_MODE", "passthrough")

    current = {"t": "tokA"}
    monkeypatch.setattr("awx_mcp.client.get_request_token", lambda: current["t"])

    names = {"tokA": "alice", "tokB": "bob"}
    looked_up: list[str] = []

    def fake_lookup(token=None):
        looked_up.append(token)
        return names[token]

    monkeypatch.setattr(mod, "_lookup_me_username", fake_lookup)

    # Same token twice -> one /api/v2/me/ lookup, cached thereafter.
    assert mod.resolve_user_identifier() == "alice"
    assert mod.resolve_user_identifier() == "alice"
    # Different token -> a distinct identity, its own lookup.
    current["t"] = "tokB"
    assert mod.resolve_user_identifier() == "bob"

    assert looked_up == ["tokA", "tokB"]


def test_passthrough_no_token_is_unknown(monkeypatch, tmp_path):
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(tmp_path / "u.jsonl"))
    monkeypatch.setattr("awx_mcp.server.AUTH_MODE", "passthrough")
    monkeypatch.setattr("awx_mcp.client.get_request_token", lambda: None)
    monkeypatch.setattr(
        mod, "_lookup_me_username", lambda token=None: pytest.fail("no lookup")
    )
    assert mod.resolve_user_identifier() == "unknown"


def test_passthrough_token_cache_bounded(monkeypatch, tmp_path):
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(tmp_path / "u.jsonl"))
    monkeypatch.setattr("awx_mcp.server.AUTH_MODE", "passthrough")
    monkeypatch.setattr(mod, "_MAX_TOKEN_CACHE", 2)
    monkeypatch.setattr(mod, "_lookup_me_username", lambda token=None: f"u-{token}")

    for tok in ("t1", "t2", "t3", "t4"):
        monkeypatch.setattr("awx_mcp.client.get_request_token", lambda t=tok: t)
        mod.resolve_user_identifier()

    # The cache never grows without bound (cleared when it reaches the cap).
    assert len(mod._user_cache_by_token) <= mod._MAX_TOKEN_CACHE


def test_passthrough_log_line_has_no_raw_token(monkeypatch, tmp_path):
    log_file = tmp_path / "u.jsonl"
    mod = _reload_usage(
        monkeypatch,
        AWX_MCP_USAGE_LOG_FILE=str(log_file),
        AWX_MCP_AUTH_MODE="passthrough",
    )
    monkeypatch.setattr("awx_mcp.server.AUTH_MODE", "passthrough")
    secret = "super-secret-token-XYZ"
    monkeypatch.setattr("awx_mcp.client.get_request_token", lambda: secret)
    monkeypatch.setattr(mod, "_lookup_me_username", lambda token=None: "alice")

    @mod.instrument_tool
    def my_tool():
        return "ok"

    my_tool()

    text = log_file.read_text()
    assert secret not in text  # neither raw token
    import hashlib

    assert hashlib.sha256(secret.encode()).hexdigest()[:16] not in text  # nor its hash
    entry = json.loads(_read_lines(log_file)[-1])
    assert entry["user"] == "alice"
    assert entry["auth_mode"] == "passthrough"


# ---------------------------------------------------------------------------
# proxy local recorder: transport="proxy", supplied user/awx_host, no server import
# ---------------------------------------------------------------------------


def test_record_proxy_tool_call_writes_proxy_record(monkeypatch, tmp_path):
    log_file = tmp_path / "u.jsonl"
    mod = _reload_usage(monkeypatch, AWX_MCP_USAGE_LOG_FILE=str(log_file))

    import time as _time

    mod.record_proxy_tool_call(
        "list_inventories",
        _time.monotonic(),
        True,
        None,
        user="argon",
        awx_host="central.example.com",
    )

    entry = json.loads(_read_lines(log_file)[-1])
    assert entry["tool"] == "list_inventories"
    assert entry["transport"] == "proxy"
    assert entry["user"] == "argon"
    assert entry["awx_host"] == "central.example.com"
    assert entry["success"] is True


def test_server_log_file_unset_creates_no_file(tmp_path):
    """Env unset -> no file created and stdout stays clean (existing behaviour)."""
    marker = tmp_path / "should-not-exist.log"
    script = textwrap.dedent(
        f"""
        import os, sys
        os.environ["ANSIBLE_BASE_URL"] = "https://awx.example.com/"
        os.environ["ANSIBLE_TOKEN"] = "dummy"
        os.environ.pop("AWX_MCP_SERVER_LOG_FILE", None)
        from awx_mcp import server
        server.logger.warning("diagnostic-probe-message")
        assert not os.path.exists({str(marker)!r})
        sys.stdout.write("STDOUT_SENTINEL")
        """
    ).strip()
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "."},
    )
    assert result.stdout == "STDOUT_SENTINEL"
    assert not marker.exists()

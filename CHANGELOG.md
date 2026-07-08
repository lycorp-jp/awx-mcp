# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com),
and this project adheres to [Semantic Versioning](https://semver.org).

## [Unreleased]

### Security
- The four credential/user write tools (`create_credential`, `update_credential`,
  `create_user`, `update_user`) are now opt-in via
  `AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true` (default: `false`). Combined
  with the `run_ad_hoc_command` gating below, the default deployment
  registers 141 of 146 tools and exposes no tool that handles sensitive data.
- When the flag is enabled, the server logs a stderr warning noting that the
  gated tools use Form-mode elicitation, which is not spec-compliant for
  sensitive data per the MCP specification. See [SECURITY.md](SECURITY.md).
- `AWX_MCP_READ_ONLY=true` exposes only read tools (`list_*`/`get_*`); all
  write/destructive tools are unregistered at startup.
- TLS certificate verification (`ANSIBLE_SSL_VERIFY`) is now **on by default**
  (previously effectively off). Added `ANSIBLE_CA_BUNDLE` to trust a
  private/self-signed CA (PEM) without disabling verification; the server
  fails fast at startup if the path doesn't exist. A bare `ANSIBLE_BASE_URL`
  host with no scheme is upgraded to `https://`; an explicit `http://` URL is
  honored but logs a warning since the API token would be sent unencrypted.
  (#14)
- `run_ad_hoc_command` (fleet-wide ad hoc Ansible execution) is now opt-in via
  `AWX_MCP_ENABLE_AD_HOC_COMMAND=true` (default: `false`); it is unregistered
  by default and the server logs a startup warning when the flag is enabled.
  The default deployment now registers 141 of 146 tools.
- In read-only mode (`AWX_MCP_READ_ONLY=true`), the AWX token minted via
  username/password auth now requests `scope: "read"` instead of `"write"`.
- AWX error response bodies are now secret-masked (bearer tokens, `token=`,
  `password=`) before being surfaced in exceptions or the diagnostic log, not
  just the usage log.

### Added
- Typed exception hierarchy (`AnsibleAPIError`, `AnsibleAuthError`,
  `AnsibleHTTPError`, `AnsibleValidationError`); error envelopes carry an
  `error_type` discriminator.
- HTTP request timeout (`connect=10s`, `read=90s`; override via
  `AWX_HTTP_TIMEOUT_READ`) and retry policy (3 retries, exponential backoff with
  jitter on 429/502/503/504, honors `Retry-After`, safe methods only).
- Cumulative pagination budget that returns a partial-results envelope when
  exceeded; zero-limit guard (`limit=0` returns `[]` without an HTTP call).
- Best-effort token revocation on shutdown (`atexit`) for tokens minted via
  username/password auth.
- Verbose usage logging: `AWX_MCP_USAGE_LOG_FILE` records one JSON Lines
  document per MCP tool call (`@timestamp`, `user`, `tool`, `kind`,
  `trace_id`, `server_version`, `success`, `latency_ms`, `transport`,
  `awx_host`, `error{type,message}` on failure). Each entry carries a `kind`
  of `"tool"` (regular tool calls) or `"internal_api"` (the one-time
  `/api/v2/me/` user-resolution call made at startup, recorded as
  `tool: "GET /api/v2/me/"`), so usage statistics can separate real tool
  usage from that overhead. `AWX_MCP_SERVER_LOG_FILE` /
  `AWX_MCP_SERVER_LOG_FORMAT` / `AWX_MCP_LOG_BACKUP_COUNT` add a mirrored
  server diagnostic log file with daily rotation. (#13)
- Optional in-process inbound TLS for the `sse`/`streamable-http` transports:
  `AWX_MCP_TLS_ENABLE`, `AWX_MCP_TLS_CERT`, `AWX_MCP_TLS_KEY`,
  `AWX_MCP_TLS_KEY_PASSWORD`. Ignored for `stdio` (no network socket; a
  warning is logged). (#15)

### Changed
- `ANSIBLE_BASE_URL` is accepted with or without a trailing slash.
- Concurrent token refresh is serialized with a lock to avoid minting duplicate
  tokens.

### Fixed
- `get_metrics` now returns the full Prometheus text response (was truncated
  to 1000 characters).
- `list_workflow_approval_templates` now applies filters before `limit`/
  `offset` (previously could return incomplete lists).
- Subpath AWX base URLs (e.g. `https://host/awx`) are now preserved end-to-end
  instead of being collapsed to the host root.
- `_validate_url` normalizes default ports so AWX `next` pagination links
  with an explicit `:443` no longer break pagination.
- Fixed a `resolve_tls_kwargs` type annotation (mypy clean).

## [24.6.1]

### Changed
- Version scheme tracks the AWX upstream release
  (<https://github.com/ansible/awx/releases/tag/24.6.1>).
- License changed to Apache License 2.0.

### Added
- SPDX-License-Identifier headers on all source files.
- `CODE_OF_CONDUCT.md`, `DCO.md`, and `CONTRIBUTING.md`.

### Improved
- Tool docstrings enhanced with disambiguation cues, return hints, chaining
  guides, and destructive-operation warnings.

## [1.0.1]

### Fixed
- `create_job_template`: removed the deprecated `credential_id` parameter
  (AWX 24.x uses multi-credential M2M; use
  `associate_credential_with_template()` instead).

### Added
- `create_execution_environment` / `update_execution_environment`: added the
  `pull` parameter (`"always"`, `"missing"`, `"never"`).

## [1.0.0]

### Added
- Initial release: MCP tools for the AWX REST API v2 across 20 domain modules
  (inventories, hosts, groups, projects, job templates, jobs, credentials,
  organizations, teams, users, workflow templates, workflow jobs, schedules,
  execution environments, notifications, labels, RBAC, instances, system, and
  ad hoc commands).
- `AnsibleClient` with token caching and pagination support.
- Configurable SSL verification.
- Token and username/password authentication.

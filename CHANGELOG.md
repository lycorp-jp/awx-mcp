# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com),
and this project adheres to [Semantic Versioning](https://semver.org).

## [Unreleased]

### Added
- Tool-call arguments are recorded in the usage log as a `params` field so you
  can see how each tool was invoked. Secret-named keys (password, token, key,
  `inputs`, …) and inline `token=`/`password=`/`Bearer` values are redacted, and
  each value is truncated to keep log lines bounded.
- Every log record now carries a `type` discriminator (`tool`, `internal_api`,
  `access`, `diagnostic`) so the four record shapes can be separated in a single
  log index. This replaces the usage-only `kind` field. All records also share
  the same `@timestamp` timestamp field (the server diagnostic log previously
  used `timestamp`), so there is one timestamp field across every log line.
- Optional stateless streamable-http mode via `AWX_MCP_STATELESS_HTTP` (default
  off). When enabled, `--serve` keeps no per-session state in-process, so
  multiple replicas can run behind a plain round-robin load balancer without
  sessions 404-ing when a request is routed to a different replica.
- Multi-user central deployment. `awx-mcp --serve` runs one shared server that
  authenticates **each request with the caller's own AWX token** (passthrough),
  sent as `Authorization: Bearer <token>` (or the `X-AWX-Token` fallback header),
  and attributes every tool call to that caller in the usage log. AWX RBAC then
  applies per user, and no shared/static server credential is involved.
- Client proxy mode. `awx-mcp --remote <URL>` runs no local server; it relays
  stdio to a central `--serve` instance, injecting the user's `ANSIBLE_TOKEN`
  as the Bearer header. Same UX as local mode (an `awx-mcp` command plus an env
  token). Optional local usage logging via `AWX_MCP_USAGE_LOG_FILE`
  (`transport: "proxy"`), with the user label from `AWX_MCP_USAGE_USER`.
- Per-user read-only. When the central server is not globally read-only, a user
  may set `AWX_MCP_READ_ONLY=true` in proxy mode; the proxy sends
  `X-AWX-Read-Only: true` and the server rejects that caller's write-tool calls.
  Tighten-only (advisory self-restriction), never loosens server policy.
- Usage records gained `auth_mode`; the `transport` field now reports
  `stdio` / `streamable-http` / `sse` / `proxy`.

### Changed
- Running mode is selected by CLI flags: `awx-mcp` (local stdio),
  `awx-mcp --remote <URL>` (proxy), `awx-mcp --serve [--sse]` (central server).
- Pinned `mcp` to `>=1.26,<2` and declared `httpx` as a direct dependency
  (used by the proxy to inject the caller's token header).

### Removed (BREAKING)
- The `--transport` CLI flag and the `AWX_MCP_TRANSPORT` environment variable.
  The previous "network transport + single static server token" server mode is
  gone (it authenticated every user as one shared identity). **Migration:** run
  `awx-mcp --serve` (add `--sse` for the sse transport) — note the auth model
  changed: each user now supplies their own token. Setting `AWX_MCP_TRANSPORT`
  now fails fast with a message pointing to `--serve`. The sse transport itself
  is retained, reachable via `awx-mcp --serve --sse`.

### Security
- The four credential/user write tools (`create_credential`, `update_credential`,
  `create_user`, `update_user`) are now opt-in via
  `AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true` (default: `false`). The default
  deployment registers 141 of 145 tools and exposes no tool that handles
  sensitive data.
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
  document per MCP tool call (`@timestamp`, `type`, `user`, `tool`, `params`,
  `trace_id`, `server_version`, `success`, `latency_ms`, `transport`,
  `awx_host`, `error{type,message}` on failure). Each entry carries a `type`
  of `"tool"` (regular tool calls) or `"internal_api"` (the one-time
  `/api/v2/me/` user-resolution call made at startup, recorded as
  `tool: "me"` with the HTTP verb and path in separate `method`/`endpoint`
  fields), so usage statistics can separate real tool usage from that overhead. `AWX_MCP_SERVER_LOG_FILE` /
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

### Removed
- Removed the `get_metrics` tool. Prometheus `/metrics/` exposition is a
  scraper-oriented ~53KB payload that overflows the MCP result budget and
  duplicates `get_dashboard_stats` / `get_ansible_version` for the useful
  counts. Tool count: 146 → 145.

### Fixed
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

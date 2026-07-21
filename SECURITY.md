# Security Policy

## Reporting Vulnerabilities

Please report security vulnerabilities via GitHub Security Advisories:
<https://github.com/lycorp-jp/awx-mcp/security/advisories/new>

Do not open a public issue for security vulnerabilities.

## Threat Model

In the default **local** mode the server runs on the operator's host with a
static AWX token supplied via environment variable. The trust boundary sits
between three components:

```
MCP client (LLM) <-> MCP server (this process) <-> AWX REST API
```

The server itself is trusted; the LLM is untrusted input. AWX enforces its
own RBAC on every API call using the configured token.

### Central multi-user mode (`--serve` + `--remote`)

`awx-mcp --serve` runs one shared server for many users. It holds **no** AWX
credential of its own; each request must carry the caller's own AWX token in an
`Authorization: Bearer <token>` (or `X-AWX-Token`) header, and every AWX call is
made with that per-caller token. AWX RBAC therefore applies per user, and the
usage log attributes each tool call to the caller's AWX account.

Because the caller's token travels in a request header, the transport must be
protected: enable in-process TLS (`AWX_MCP_TLS_ENABLE`) or front the server with
an authenticating TLS reverse proxy. A non-local bind without TLS logs a warning
at startup. Users typically connect with `awx-mcp --remote <URL>`, a stdio proxy
that injects their `ANSIBLE_TOKEN` as the header (username/password auth is not
supported in proxy mode — only a personal access token can be forwarded).

Per-user read-only (`AWX_MCP_READ_ONLY=true` on the client, sent as
`X-AWX-Read-Only`) is **advisory self-restriction**, not a security boundary: it
can only tighten a caller's own access. The real boundaries are the caller's AWX
token scope/RBAC and a server-global `AWX_MCP_READ_ONLY` (which unregisters write
tools entirely and cannot be re-enabled by a user).

Enabling `AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true` together with `--serve` is
discouraged: the gated tools collect sensitive data via Form-mode elicitation
(see below), which is worse in a shared network deployment than on a local
stdio process.

All 4 credential/user write tools (`create_credential`, `update_credential`,
`create_user`, `update_user`) are gated behind
`AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true` (default: off). The default
deployment registers 141 of 145 tools and exposes no tool that handles
sensitive data.

## Sensitive Data

When `AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true` is set, the 4 gated tools
collect sensitive inputs (passwords, credential fields) via **Form-mode
elicitation**.

Form-mode elicitation is not spec-compliant for sensitive data per the MCP
specification. Only URL-mode elicitation guarantees that sensitive data does
not transit through the LLM context, MCP client, or other intermediate
systems. Form mode does not provide this guarantee — sensitive responses may
still be exposed through client-side logging, transcript persistence, or
other intermediate systems.

This is the residual risk operators accept when enabling the flag. Use only
in trusted, isolated environments and document the acceptance in your
runbooks.

## Fleet-Wide Command Execution

`run_ad_hoc_command` runs an ad hoc Ansible command against some or all hosts
in an AWX inventory — a normal, default-registered write tool, not an opt-in
one. Because inventories can span an operator's entire managed fleet, this
tool is effectively remote code execution across every reachable host — the
same blast radius as any ad hoc AWX job. It is subject to `AWX_MCP_READ_ONLY`
like other write/destructive tools: set `AWX_MCP_READ_ONLY=true` to unregister
it along with all other write tools. AWX's own RBAC still applies to the
configured token.

## Security Hardening History

Notable security-relevant changes, newest first. See the
[CHANGELOG](CHANGELOG.md) and
[release notes](https://github.com/lycorp-jp/awx-mcp/releases) for full detail.

- Added central multi-user passthrough mode (`awx-mcp --serve`): per-request
  caller tokens, no shared static server credential, per-user usage attribution,
  and a per-user advisory read-only header. Removed the previous static-token
  network server mode (`--transport`/`AWX_MCP_TRANSPORT`), which authenticated
  all users as one identity.
- In read-only mode (`AWX_MCP_READ_ONLY=true`), the AWX token minted via
  username/password auth now requests `scope: "read"` instead of `"write"`.
- AWX error response bodies are now secret-masked (bearer tokens, `token=`,
  `password=`) before being surfaced in exceptions or the diagnostic log, not
  just the usage log.
- Added optional in-process inbound TLS for the `sse`/`streamable-http`
  transports (`AWX_MCP_TLS_ENABLE`, `AWX_MCP_TLS_CERT`, `AWX_MCP_TLS_KEY`,
  `AWX_MCP_TLS_KEY_PASSWORD`).
- TLS certificate verification (`ANSIBLE_SSL_VERIFY`) is now on by default;
  added `ANSIBLE_CA_BUNDLE` to trust a private/self-signed CA without
  disabling verification. A bare `ANSIBLE_BASE_URL` host is upgraded to
  `https://`; an explicit `http://` URL logs a warning.
- Corrected Form-mode elicitation security claims — clarified that Form mode is
  not spec-compliant for sensitive data.
- Gated the 4 credential/user write tools behind an opt-in env flag
  (`AWX_MCP_ENABLE_CREDENTIAL_MANAGEMENT=true`); they are not registered by default.
- Documented the residual risk of Form-mode elicitation for sensitive inputs and
  updated the threat model accordingly.

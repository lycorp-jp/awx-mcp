# Security Policy

## Reporting Vulnerabilities

Please report security vulnerabilities via GitHub Security Advisories:
<https://github.com/lycorp-jp/awx-mcp/security/advisories/new>

Do not open a public issue for security vulnerabilities.

## Threat Model

This server runs on the operator's host with a static AWX token supplied via
environment variable. The trust boundary sits between three components:

```
MCP client (LLM) <-> MCP server (this process) <-> AWX REST API
```

The server itself is trusted; the LLM is untrusted input. AWX enforces its
own RBAC on every API call using the configured token.

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

#!/usr/bin/env python3
"""Doc parity guard.

Enforces three invariants across the docs so they cannot silently drift:

1. SECURITY.md exists and every README links to it.
2. The advertised tool counts ("<total> tools", "<default> default", and
   "<default> of <total>") agree across the live doc surfaces — README.md,
   README.ko.md, README.ja.md, SECURITY.md, and pyproject.toml.
3. Every user-facing `AWX_MCP_*` / `ANSIBLE_*` env var read via
   ``os.environ.get(...)`` in awx_mcp/*.py appears in the env tables of all
   three READMEs, and no README documents a symbol the code doesn't read
   (a "ghost" symbol).

CHANGELOG.md is intentionally excluded from the count check: it is a historical
log and legitimately references older counts.
"""

import glob
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

READMES = ["README.md", "README.ko.md", "README.ja.md"]
SECURITY_FILE = "SECURITY.md"
# Files whose tool counts must agree. CHANGELOG.md is excluded (historical log).
COUNT_FILES = [*READMES, SECURITY_FILE, "pyproject.toml"]

# Only the two *structured* count claims are enforced; bare "<n> tools" in prose
# (e.g. "the 4 opt-in tools") is too ambiguous to parity-check.
# "146 tools (142 default + 4 opt-in)"  -> (total, default, opt_in)
COMPOSITE_RE = re.compile(r"(\d+)\s+tools?\s*\((\d+)\s+default\s*\+\s*(\d+)\s+opt-in\)")
# "142 of 146"  -> (default, total)
OF_RE = re.compile(r"(\d+)\s+of\s+(\d+)\b")

# Symbols read via os.environ.get("AWX_MCP_*"/"ANSIBLE_*") that are internal
# implementation channels, not something a user is meant to set directly —
# excluded from the "must appear in a README" requirement.
#   - AWX_MCP_AUTH_MODE / AWX_MCP_EFFECTIVE_TRANSPORT: written by the process
#     itself (in awx_mcp/__init__.py) to pass the CLI-selected mode/transport
#     down to server.py/usage.py; there is no supported use case for a user
#     setting these from outside the process.
#   - AWX_MCP_TRANSPORT: a removed flag (see server.py); only read so the
#     server can fail fast with a migration error, not to be set going forward.
INTERNAL_ENV_SYMBOLS = {
    "AWX_MCP_AUTH_MODE",
    "AWX_MCP_EFFECTIVE_TRANSPORT",
    "AWX_MCP_TRANSPORT",
}

ENV_SYMBOL_RE = re.compile(r'os\.environ\.get\(\s*"((?:AWX_MCP|ANSIBLE)_[A-Z0-9_]*)"')

errors = []


def _read(rel_path):
    path = os.path.join(REPO_ROOT, rel_path)
    if not os.path.isfile(path):
        errors.append(f"MISSING: {rel_path} does not exist")
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


# 1. SECURITY.md exists + every README links to it.
if _read(SECURITY_FILE) is None:
    pass
for readme in READMES:
    content = _read(readme)
    if content is not None and "security.md" not in content.lower():
        errors.append(f"MISSING LINK: {readme} does not reference SECURITY.md")

# 2. Tool-count parity.
totals = {}  # value -> [files]
defaults = {}  # value -> [files]


def _record(bucket, value, rel_path):
    bucket.setdefault(value, [])
    if rel_path not in bucket[value]:
        bucket[value].append(rel_path)


for rel_path in COUNT_FILES:
    content = _read(rel_path)
    if content is None:
        continue
    for m in COMPOSITE_RE.finditer(content):
        _record(totals, int(m.group(1)), rel_path)
        _record(defaults, int(m.group(2)), rel_path)
    for m in OF_RE.finditer(content):
        _record(defaults, int(m.group(1)), rel_path)
        _record(totals, int(m.group(2)), rel_path)

if len(totals) > 1:
    errors.append(f"TOOL COUNT MISMATCH (total): {dict(totals)}")
if len(defaults) > 1:
    errors.append(f"TOOL COUNT MISMATCH (default): {dict(defaults)}")
if totals and defaults:
    total = next(iter(totals))
    default = next(iter(defaults))
    if default >= total:
        errors.append(
            f"TOOL COUNT INVALID: default ({default}) must be < total ({total})"
        )

# 3. Env-var parity: every user-facing symbol read by the code must be
# documented in all three READMEs; every symbol documented in a README must
# actually be read by the code (no ghost rows).
code_symbols = set()
for path in sorted(glob.glob(os.path.join(REPO_ROOT, "awx_mcp", "*.py"))):
    with open(path, encoding="utf-8") as f:
        code_symbols.update(ENV_SYMBOL_RE.findall(f.read()))

user_facing_symbols = code_symbols - INTERNAL_ENV_SYMBOLS

for readme in READMES:
    content = _read(readme)
    if content is None:
        continue
    readme_symbols = set(re.findall(r"`((?:AWX_MCP|ANSIBLE)_[A-Z0-9_]*)`", content))

    missing = sorted(user_facing_symbols - readme_symbols)
    if missing:
        errors.append(f"ENV VAR MISSING FROM README: {readme} is missing {missing}")

    ghosts = sorted(readme_symbols - code_symbols)
    if ghosts:
        errors.append(
            f"ENV VAR GHOST IN README: {readme} documents {ghosts} "
            "which the code never reads"
        )

if errors:
    for err in errors:
        print(err, file=sys.stderr)
    sys.exit(1)

print("OK: doc parity verified")

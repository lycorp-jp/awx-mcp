# SPDX-License-Identifier: Apache-2.0

import json
import re

# --- Secret masking -----------------------------------------------------------
# Shared by the usage instrumentation and the API client so that bearer tokens /
# token=... / password=... never reach a log sink, the usage JSONL, an exception
# message propagated to the MCP client, or the diagnostic log.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Bearer\s+\S+", re.IGNORECASE), "Bearer ***"),
    (re.compile(r"(token[\"']?\s*[:=]\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(password[\"']?\s*[:=]\s*)\S+", re.IGNORECASE), r"\1***"),
]


def mask_secrets(text: str) -> str:
    """Redact bearer tokens, token=... and password=... occurrences from text."""
    if not text:
        return text
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def validate_json_str(value: str, param_name: str) -> str | None:
    """Validate a JSON string parameter.

    Returns MCP error response if invalid, None if valid.
    """
    try:
        json.loads(value)
        return None
    except json.JSONDecodeError:
        return json.dumps(
            {"status": "error", "message": f"Invalid JSON in {param_name}"}
        )


def parse_json_str(value: str, param_name: str) -> tuple:
    """Parse a JSON string.

    Returns (parsed_data, None) on success, (None, error_response) on failure.
    """
    try:
        return json.loads(value), None
    except json.JSONDecodeError:
        return None, json.dumps(
            {"status": "error", "message": f"Invalid JSON in {param_name}"}
        )

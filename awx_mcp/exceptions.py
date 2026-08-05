# SPDX-License-Identifier: Apache-2.0

"""Typed exception hierarchy for AWX/Tower API errors.

All API errors inherit from AnsibleAPIError. Tools propagate these to the
MCP client; MCPServer surfaces the exception class name in the error envelope's
``error_type`` field, allowing LLM clients to discriminate auth vs. validation
vs. transport failures.
"""


class AnsibleAPIError(Exception):
    """Base class for all AWX/Tower API errors raised by AnsibleClient."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AnsibleAuthError(AnsibleAPIError):
    """Authentication or authorization failure (401, 403, CSRF/login failure)."""


class AnsibleHTTPError(AnsibleAPIError):
    """Non-auth HTTP error response (4xx other than 401/403, all 5xx)."""


class AnsibleValidationError(AnsibleAPIError):
    """400 Bad Request with field-level validation errors from AWX."""

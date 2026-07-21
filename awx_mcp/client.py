# SPDX-License-Identifier: Apache-2.0

"""
Ansible MCP Server - API Client

AnsibleClient class for communicating with the AWX/Tower REST API,
token caching, and pagination handling.
"""

import atexit
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import (
    AnsibleAPIError,
    AnsibleAuthError,
    AnsibleHTTPError,
    AnsibleValidationError,
)
from .server import (
    ANSIBLE_BASE_URL,
    ANSIBLE_PASSWORD,
    ANSIBLE_SSL_VERIFY,
    ANSIBLE_TOKEN,
    ANSIBLE_USERNAME,
    AUTH_MODE,
    READ_ONLY,
    get_request_header,
    logger,
)
from .utils import mask_secrets


def get_request_token() -> str | None:
    """Extract the caller's AWX token from the current request, or ``None``.

    Used only in passthrough (``--serve``) mode. Prefers ``Authorization:
    Bearer <token>`` (scheme match is case-insensitive) and falls back to the
    ``X-AWX-Token`` header for environments where a proxy/gateway rewrites
    ``Authorization``. Returns ``None`` outside a request (never raises).
    """
    auth = get_request_header("authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    fallback = get_request_header("x-awx-token")
    if fallback and fallback.strip():
        return fallback.strip()
    return None


def _default_port(scheme: str) -> int | None:
    """Effective default port for a scheme, so ``https://h`` and ``https://h:443``
    compare equal in origin validation."""
    return {"https": 443, "http": 80}.get(scheme)


DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_READ_TIMEOUT = int(os.environ.get("AWX_HTTP_TIMEOUT_READ", "90"))

_RETRY_STATUS = frozenset({429, 502, 503, 504})
_RETRY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _build_retry_adapter() -> HTTPAdapter:
    """Configure urllib3 Retry: 3 retries, exponential backoff with jitter,
    honor Retry-After, only on safe methods."""
    retry = Retry(
        total=3,
        status_forcelist=list(_RETRY_STATUS),
        allowed_methods=_RETRY_METHODS,
        backoff_factor=0.5,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    return HTTPAdapter(max_retries=retry)


class AnsibleClient:
    """HTTP client for Ansible Tower/AWX REST API."""

    def __init__(
        self,
        base_url: str,
        username: str = None,
        password: str = None,
        token: str = None,
    ):
        # Normalize: accept ANSIBLE_BASE_URL with or without a trailing slash.
        # All endpoints are joined as f"{base_url}/api/...", so a trailing slash
        # would otherwise produce a double slash ("//api/...").
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token: str | None = token
        self._token_id: int | None = None
        self.session = requests.Session()
        self.session.verify = ANSIBLE_SSL_VERIFY
        adapter = _build_retry_adapter()
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def __enter__(self):
        if not self.token and self.username and self.password:
            self.get_token()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.session.close()

    def get_token(self) -> str | None:
        """Authenticate and get token using web session approach."""
        logger.debug("Authenticating with username/password...")

        # Step 1: Get the CSRF token
        login_page = self.session.get(f"{self.base_url}/api/login/")

        # Get CSRF token from cookies
        csrf_token = None
        if "csrftoken" in login_page.cookies:
            csrf_token = login_page.cookies["csrftoken"]
        else:
            # Try to find it in the content
            match = re.search(
                r'name="csrfmiddlewaretoken" value="([^"]+)"', login_page.text
            )
            if match:
                csrf_token = match.group(1)

        if not csrf_token:
            raise AnsibleAuthError("Could not obtain CSRF token")

        # Step 2: Perform login
        headers = {"Referer": f"{self.base_url}/api/login/", "X-CSRFToken": csrf_token}

        login_data = {
            "username": self.username,
            "password": self.password,
            "next": "/api/v2/",
        }

        login_response = self.session.post(
            f"{self.base_url}/api/login/",
            data=login_data,
            headers=headers,
            allow_redirects=False,
        )

        if login_response.status_code >= 400:
            raise AnsibleAuthError(
                f"Login failed: {login_response.status_code} - {login_response.text}",
                status_code=login_response.status_code,
            )

        # Step 3: Generate API token
        token_headers = {
            "Content-Type": "application/json",
            "Referer": f"{self.base_url}/api/v2/",
        }

        # Use the updated CSRF token
        if "csrftoken" in self.session.cookies:
            token_headers["X-CSRFToken"] = self.session.cookies["csrftoken"]

        # Mint a read-scoped token in read-only mode so the token's actual AWX
        # permissions match the tool gating (defense-in-depth if the token
        # leaks), otherwise a write-scoped token for full functionality.
        token_data = {
            "description": "MCP Server Token",
            "application": None,
            "scope": "read" if READ_ONLY else "write",
        }

        token_response = self.session.post(
            f"{self.base_url}/api/v2/tokens/", json=token_data, headers=token_headers
        )

        if token_response.status_code == 201:
            resp_json = token_response.json()
            self.token = resp_json.get("token")
            self._token_id = resp_json.get("id")
            logger.debug("Token obtained successfully (id=%s)", self._token_id)
            if self._token_id is not None:
                _atexit_revoke_targets.append(
                    (self.base_url, self.token, self._token_id)
                )
            return self.token
        else:
            raise AnsibleAuthError(
                f"Token creation failed: {token_response.status_code}"
                f" - {token_response.text}",
                status_code=token_response.status_code,
            )

    def _validate_url(self, url: str) -> str:
        """Validate that URL matches base_url origin (scheme, hostname, port).

        Ports are compared by their effective value, so an AWX ``next`` link that
        spells out the default port (``https://host:443/...``) is not rejected
        against a base URL written without it (``https://host``).
        """
        parsed_base = urlparse(self.base_url)
        parsed_url = urlparse(url)
        base_port = parsed_base.port or _default_port(parsed_base.scheme)
        url_port = parsed_url.port or _default_port(parsed_url.scheme)
        if (
            parsed_url.scheme != parsed_base.scheme
            or parsed_url.hostname != parsed_base.hostname
            or url_port != base_port
        ):
            raise ValueError(
                f"URL origin mismatch: {url} does not match base URL {self.base_url}"
            )
        return url

    def get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _resolve_url(self, endpoint: str) -> str:
        """Resolve an endpoint (relative path or absolute AWX URL) to a full URL.

        Relative paths are appended to ``base_url`` so a base-URL path prefix
        (subpath deployment, e.g. ``https://host/awx``) is preserved — plain
        ``urljoin`` would drop it because the endpoints start with ``/api/``.
        Absolute URLs (e.g. an AWX ``next`` link) are used as-is. All results are
        origin-validated.
        """
        if urlparse(endpoint).scheme:
            return self._validate_url(endpoint)
        if endpoint.startswith("/"):
            return self._validate_url(self.base_url + endpoint)
        return self._validate_url(urljoin(self.base_url + "/", endpoint))

    def _send(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        data: dict | None = None,
    ) -> requests.Response:
        """Send a request and raise typed errors. Secrets are masked in every
        error message (exception + diagnostic log) so a token/password echoed in
        an AWX error body never reaches the MCP client or the log file."""
        url = self._resolve_url(endpoint)
        headers = self.get_headers()

        logger.debug("%s %s", method, url)

        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=data,
                timeout=(DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT),
            )
        except requests.exceptions.Timeout as e:
            raise AnsibleHTTPError(
                f"Ansible API timeout after {DEFAULT_READ_TIMEOUT}s: {method} {url}",
            ) from e
        except requests.exceptions.RequestException as e:
            raise AnsibleHTTPError(
                f"Ansible API request error: {method} {url} — {mask_secrets(str(e))}",
            ) from e

        if response.status_code >= 400:
            error_message = mask_secrets(
                f"Ansible API error: {response.status_code} - {response.text}"
            )
            logger.error(error_message)
            if response.status_code in (401, 403):
                raise AnsibleAuthError(error_message, status_code=response.status_code)
            elif response.status_code == 400:
                raise AnsibleValidationError(error_message, status_code=400)
            else:
                raise AnsibleHTTPError(error_message, status_code=response.status_code)

        return response

    def request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        data: dict | None = None,
    ) -> dict[str, Any]:
        """Make a request to the Ansible API and return the parsed JSON body."""
        response = self._send(method, endpoint, params, data)

        if response.status_code == 204:  # No content
            return {"status": "success"}

        # Handle empty responses
        if not response.text.strip():
            return {"status": "success", "message": "Empty response"}

        # Try to parse as JSON, but handle non-JSON responses gracefully.
        try:
            return response.json()
        except json.JSONDecodeError:
            return {
                "status": "success",
                "content_type": response.headers.get("Content-Type", "unknown"),
                "text": response.text[:1000],
            }


# Token cache for reuse across tool calls
_cached_token = None
_cached_token_id = None

_token_cache_lock = threading.Lock()
_atexit_revoke_targets: list[
    tuple[str, str | None, int]
] = []  # (base_url, token, token_id)


def _revoke_token_at_shutdown(base_url: str, token: str | None, token_id: int) -> None:
    """Best-effort token revocation called by atexit.

    Uses a fresh ``requests.request(...)`` call (NOT a shared Session) because
    the session's connection pool may already be torn down at interpreter
    shutdown. Errors are swallowed; this is best-effort cleanup.

    Race: if a tool call is mid-flight when atexit fires, the in-flight
    call may see 401 on its next page. Acceptable because shutdown is
    terminal.

    Limitation: SIGKILL or OOM-kill bypasses atexit, leaving the token live
    on AWX until its server-side TTL expires. Operators should monitor
    /api/v2/tokens/ periodically.
    """
    if not token_id:
        return
    delete_url = f"{base_url.rstrip('/')}/api/v2/tokens/{token_id}/"
    try:
        resp = requests.request(
            "DELETE",
            delete_url,
            headers={"Authorization": f"Bearer {token}"},
            verify=ANSIBLE_SSL_VERIFY,
            timeout=5,
        )
        logger.debug("Token %s revoke status: %s", token_id, resp.status_code)
    except Exception as e:  # noqa: BLE001 — best-effort cleanup
        logger.debug("Token %s revoke failed (best-effort): %s", token_id, e)


def _atexit_drain() -> None:
    """Iterate over registered revoke targets at process exit."""
    for base_url, token, token_id in _atexit_revoke_targets:
        _revoke_token_at_shutdown(base_url, token, token_id)


atexit.register(_atexit_drain)


@contextmanager
def get_ansible_client():
    """Get an initialized Ansible API client with token reuse."""
    global _cached_token, _cached_token_id

    # Passthrough (--serve) mode: authenticate with the caller's per-request
    # token. No caching, minting, or atexit revocation — the token belongs to
    # the user, not this server, so sharing/revoking it across requests would
    # be wrong.
    if AUTH_MODE == "passthrough":
        token = get_request_token()
        if not token:
            raise AnsibleAuthError(
                "An AWX token is required in passthrough mode. Send it as "
                "'Authorization: Bearer <token>' (or the X-AWX-Token header).",
                status_code=401,
            )
        client = AnsibleClient(base_url=ANSIBLE_BASE_URL, token=token)
        with client:
            yield client
        return

    # If we have a static token from env, always use it
    if ANSIBLE_TOKEN:
        client = AnsibleClient(
            base_url=ANSIBLE_BASE_URL,
            username=ANSIBLE_USERNAME,
            password=ANSIBLE_PASSWORD,
            token=ANSIBLE_TOKEN,
        )
        with client:
            yield client
        return

    # Snapshot the cache under the lock; do NOT hold the lock during HTTP I/O
    with _token_cache_lock:
        cached_token = _cached_token

    if cached_token:
        client = AnsibleClient(
            base_url=ANSIBLE_BASE_URL,
            username=ANSIBLE_USERNAME,
            password=ANSIBLE_PASSWORD,
            token=cached_token,
        )
        try:
            client.__enter__()
            client.request("GET", "/api/v2/ping/")
        except AnsibleAPIError:
            logger.debug("Cached token expired, refreshing...")
            with _token_cache_lock:
                # invalidate only if our snapshot is still the current cache
                if _cached_token == cached_token:
                    _cached_token = None
                    _cached_token_id = None
            client.__exit__(None, None, None)
        else:
            try:
                yield client
            finally:
                client.__exit__(None, None, None)
            return

    # Need to mint a new token. Acquire lock for the mint to serialize concurrent
    # refreshes; double-check inside the lock in case another thread minted.
    with _token_cache_lock:
        if _cached_token:
            existing_token = _cached_token
        else:
            existing_token = None

    if existing_token:
        # Another thread minted while we were trying — re-use it.
        client = AnsibleClient(
            base_url=ANSIBLE_BASE_URL,
            username=ANSIBLE_USERNAME,
            password=ANSIBLE_PASSWORD,
            token=existing_token,
        )
        with client:
            yield client
        return

    # Fall through: this thread mints. Use the lock to serialize the mint
    # itself (only one thread should call get_token() at a time to avoid AWX
    # creating duplicate tokens).
    with _token_cache_lock:
        # final check
        if _cached_token:
            client = AnsibleClient(
                base_url=ANSIBLE_BASE_URL,
                username=ANSIBLE_USERNAME,
                password=ANSIBLE_PASSWORD,
                token=_cached_token,
            )
            mint = False
        else:
            client = AnsibleClient(
                base_url=ANSIBLE_BASE_URL,
                username=ANSIBLE_USERNAME,
                password=ANSIBLE_PASSWORD,
            )
            mint = True

        if mint:
            # authenticates via get_token() — mutates self.token + self._token_id
            client.__enter__()
            _cached_token = client.token
            _cached_token_id = client._token_id

    if mint:
        try:
            yield client
        finally:
            client.__exit__(None, None, None)
    else:
        with client:
            yield client


def handle_pagination(
    client: AnsibleClient, endpoint: str, params: dict | None = None
) -> list[dict[str, Any]]:
    """Handle paginated results from Ansible API.

    Translates tool-level 'limit'/'offset' into AWX-native 'page_size'/'page'.
    Respects the 'limit' parameter: stops collecting once the limit is reached.

    Cumulative budget = DEFAULT_READ_TIMEOUT * 2 seconds. If exceeded, returns
    a partial-results envelope: ``[{"error": "pagination_timeout", "partial": True,
    "pages_fetched": N, "results": [...]}]``.
    """
    if params is None:
        params = {}

    # Extract custom pagination params (not native AWX API params)
    max_results = params.pop("limit", None)
    offset = params.pop("offset", 0)

    # Zero-limit guard: caller asked for nothing, skip HTTP entirely.
    if max_results is not None and max_results <= 0:
        return []

    # Convert to AWX-native pagination params
    page_size = min(max_results, 200) if max_results else 200
    params["page_size"] = page_size

    if offset > 0:
        params["page"] = (offset // page_size) + 1
        skip_in_page = offset % page_size
    else:
        skip_in_page = 0

    results: list[dict[str, Any]] = []
    next_url: str | None = endpoint
    first_page = True
    pages_fetched = 0

    budget_seconds = DEFAULT_READ_TIMEOUT * 2
    started_at = time.monotonic()

    while next_url:
        if time.monotonic() - started_at > budget_seconds:
            return [
                {
                    "error": "pagination_timeout",
                    "partial": True,
                    "pages_fetched": pages_fetched,
                    "results": results,
                    "budget_seconds": budget_seconds,
                }
            ]

        response = client.request("GET", next_url, params=params)
        pages_fetched += 1
        if "results" in response:
            page_results = response["results"]
            # Skip partial offset within the first page
            if first_page and skip_in_page > 0:
                page_results = page_results[skip_in_page:]
                first_page = False
            results.extend(page_results)
        else:
            return [response]

        # If a limit was specified, stop once we have enough results
        if max_results is not None and len(results) >= max_results:
            results = results[:max_results]
            break

        next_url = response.get("next")
        if next_url:
            params = None

    return results

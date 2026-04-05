<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Guesty Auth & Config Flow

**Feature**: 001-auth-config-flow
**Date**: 2025-07-18

## R-001: Guesty OAuth 2.0 Token Endpoint

**Context**: The Guesty Open API uses OAuth 2.0 Client Credentials
grant for machine-to-machine authentication (FR-005).

**Decision**: Use the Guesty OAuth 2.0 token endpoint at
`https://open-api.guesty.com/oauth2/token` with `client_credentials`
grant type.

**Rationale**: This is the documented Guesty Open API authentication
mechanism. The rentalsync-bridge project successfully uses this same
endpoint and grant type in production. The token endpoint returns an
`access_token` with a 24-hour lifetime and `open-api` scope.

**Alternatives considered**:

- API key authentication: Not supported by Guesty Open API; only
  OAuth 2.0 Client Credentials is available for server-to-server
  access.
- OAuth 2.0 Authorization Code flow: Not applicable; Guesty's
  integration API uses client credentials, not user-delegated
  authorization.

**Token endpoint request**:

```http
POST https://open-api.guesty.com/oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&scope=open-api
&client_id=<ID>
&client_secret=<SECRET>
```

**Token endpoint response**:

```json
{
  "token_type": "Bearer",
  "access_token": "<jwt-token>",
  "expires_in": 86400,
  "scope": "open-api"
}
```

**Key constraints**:

- Token lifetime: 86400 seconds (24 hours)
- Rate limit: 5 token requests per 24-hour rolling window per
  `client_id`
- Exceeding the rate limit results in account lockout until the
  window resets

---

## R-002: Token Persistence Strategy

**Context**: Home Assistant restarts frequently (updates, config
changes, hardware events). The integration must preserve
authentication state across restarts to avoid exhausting the
5-request-per-24h token limit (FR-007, FR-008, SC-002).

**Decision**: Persist token data in `config_entry.data` using
Home Assistant's built-in config entry storage mechanism. Store the
access token, expiry timestamp, and token request counter with
window start time.

**Rationale**: HA's `config_entry` storage is the standard mechanism
for persisting integration state across restarts. It is
JSON-serializable, survives HA restarts and updates, and is
accessible in both `async_setup_entry` and `config_flow`. This
approach avoids external files or databases.

**Alternatives considered**:

- `hass.data` only (in-memory): Lost on restart; would require
  re-authentication every time HA restarts, exhausting the 5-request
  limit quickly.
- File-based storage (`homeassistant.helpers.storage.Store`): More
  complex API with separate file management. `config_entry.data` is
  simpler for the small amount of data needed (token + counter).
- SQLite or other database: Excessive for storing a single token and
  counter; adds unnecessary dependency complexity.

**Implementation detail**:

```python
# Stored in config_entry.data alongside credentials:
{
    "client_id": "...",
    "client_secret": "...",
    "cached_token": {
        "access_token": "...",
        "token_type": "Bearer",
        "expires_in": 86400,
        "scope": "open-api",
        "issued_at": "2025-07-18T12:00:00+00:00",
    },
    "token_request_count": 1,
    "token_window_start": "2025-07-18T12:00:00+00:00"
}
```

On startup, `HATokenStorage.load_token()` reads from
`config_entry.data`. On token acquisition,
`HATokenStorage.save_token()` updates `config_entry.data` via
`hass.config_entries.async_update_entry()`.

---

## R-003: HTTP Client Library Choice

**Context**: The `api/` package needs an async HTTP client for
communicating with the Guesty API (Constitution §II, §VIII).

**Decision**: Use `httpx.AsyncClient` with dependency injection.

**Rationale**: `httpx` is already used by the rentalsync-bridge
project for the Guesty API client. It provides a modern, fully async
HTTP client with first-class `asyncio` support. Using the same
library ensures interface compatibility when the `api/` package is
eventually extracted into a shared library. `httpx` also supports
HTTP/2, connection pooling, and has excellent timeout control.

**Alternatives considered**:

- `aiohttp`: The default in many HA integrations. However,
  rentalsync-bridge uses `httpx`, and consistency between the two
  projects is important for the planned library extraction. `aiohttp`
  has a different API surface that would complicate future merging.
- `requests` (sync): Violates Constitution §VIII (async mandatory).
  Would block the HA event loop.

**Dependency injection pattern**:

```python
class GuestyApiClient:
    def __init__(
        self,
        token_manager: GuestyTokenManager,
        http_client: httpx.AsyncClient,
        base_url: str = GUESTY_BASE_URL,
    ) -> None:
        self._token_manager = token_manager
        self._http = http_client
        self._base_url = base_url
```

The HA integration creates and owns the `httpx.AsyncClient` lifetime
in `async_setup_entry` and closes it in `async_unload_entry`.

---

## R-004: Concurrent Token Refresh Strategy

**Context**: Multiple concurrent API requests may discover an expired
token simultaneously. Only one authentication request should be made,
with all waiters receiving the new token (FR-011).

**Decision**: Use `asyncio.Lock` with double-checked locking pattern
(identical to rentalsync-bridge's `GuestyTokenManager`).

**Rationale**: The double-checked locking pattern is a well-known
concurrency pattern. The first check (without lock) avoids contention
in the common case (valid cached token). The lock serializes only
actual token acquisition. The second check inside the lock prevents
redundant requests when multiple coroutines race to acquire the lock.

**Alternatives considered**:

- Single lock (always acquire): Adds unnecessary contention when the
  token is valid; every token access would serialize.
- `asyncio.Event` or `asyncio.Condition`: More complex API without
  clear benefit over Lock + double-check for this use case.
- No locking (allow duplicate requests): Wastes rate-limited token
  requests; could exhaust the 5-per-24h limit.

**Pattern**:

```python
async def get_token(self) -> str:
    if self._is_cache_valid():
        return self._cached_token
    async with self._lock:
        if self._is_cache_valid():
            return self._cached_token
        await self._check_rate_limit()
        token = await self._request_token()
        self._update_cache(token)
        await self._storage.save_token(token)
        return token.access_token
```

---

## R-005: Rate Limit Handling Strategy

**Context**: Guesty enforces three tiers of API rate limits (15/s,
120/min, 5000/hr) and returns HTTP 429 with optional Retry-After
header (FR-013, FR-014, FR-015).

**Decision**: Implement exponential backoff with Retry-After header
respect, matching rentalsync-bridge's `_request()` pattern: 3 max
retries, 1s initial backoff doubling to 4s, capped at 30s.

**Rationale**: This is a proven pattern from rentalsync-bridge that
correctly handles Guesty's rate limiting behavior. Exponential backoff
prevents thundering herd effects while Retry-After respect ensures
compliance with server-specified wait times.

**Alternatives considered**:

- Fixed delay retry: Suboptimal; doesn't adapt to varying rate limit
  durations.
- Token bucket / leaky bucket (proactive): Adds complexity for the
  MVP scope. The reactive approach (handle 429 responses) is
  sufficient and proven. Proactive rate limiting can be added in a
  future feature.
- No retry (fail immediately on 429): Poor user experience; transient
  rate limits would surface as errors.

**Backoff parameters** (matching rentalsync-bridge):

| Parameter | Value |
| --------- | ----- |
| Max retries | 3 (4 total attempts) |
| Initial backoff | 1.0 seconds |
| Backoff multiplier | 2x |
| Max backoff | 30.0 seconds |
| Retry-After | Overrides calculated backoff |

---

## R-006: TokenStorage Protocol Design

**Context**: The `api/` package must have zero `homeassistant.*`
imports but needs to persist tokens and rate limit counters across
restarts (library-shim architecture requirement).

**Decision**: Define a `typing.Protocol` class `TokenStorage` in the
`api/` package. The HA integration provides a concrete implementation
(`HATokenStorage`) backed by `config_entry.data`.

**Rationale**: `typing.Protocol` provides structural subtyping
(duck typing with type checker support) without requiring
inheritance. The `api/` package defines the interface; consumers
(HA integration, future CLI tools, etc.) implement it. This cleanly
separates concerns without coupling the API library to any specific
storage backend.

**Alternatives considered**:

- `abc.ABC` with `abstractmethod`: Requires the implementation to
  inherit from the base class, creating a tighter coupling. Protocol
  allows any class with matching methods to satisfy the interface.
- Callback functions (instead of protocol): Less discoverable, harder
  to type-check, and doesn't group related operations together.
- No abstraction (pass HA objects directly): Violates the zero-HA-
  imports rule for the `api/` package.

**Protocol definition**:

```python
class TokenStorage(Protocol):
    async def load_token(self) -> CachedToken | None: ...
    async def save_token(self, token: CachedToken) -> None: ...
    async def load_request_count(
        self,
    ) -> tuple[int, datetime | None]: ...
    async def save_request_count(
        self, count: int, window_start: datetime
    ) -> None: ...
```

---

## R-007: Exception Hierarchy Design

**Context**: The API client must translate errors into typed
exceptions with actionable context (Constitution §II, FR-016).

**Decision**: Mirror the rentalsync-bridge exception hierarchy with
Guesty-specific naming: `GuestyApiError` (base) →
`GuestyAuthError`, `GuestyRateLimitError`,
`GuestyConnectionError`, `GuestyResponseError`.

**Rationale**: Consistent hierarchy with rentalsync-bridge simplifies
the future library extraction. Each exception type maps to a specific
failure category that the HA integration can translate into
user-facing error messages. `GuestyResponseError` is added (beyond
rentalsync-bridge's hierarchy) to handle unexpected response formats
(spec edge case: "unexpected response format").

**Alternatives considered**:

- Reuse `PMS*Error` names from rentalsync-bridge: Would create
  naming confusion when both exist. Guesty-specific names are clearer
  for the standalone package.
- Single exception class with error codes: Less Pythonic; makes
  `except` clauses verbose and error-prone.
- HA-specific exceptions only: Would couple the `api/` package to HA.

**Hierarchy**:

```text
GuestyApiError (base)
├── GuestyAuthError         # Invalid creds, expired token, 401/403
├── GuestyRateLimitError    # HTTP 429, retry_after: float | None
├── GuestyConnectionError   # Network failures, timeouts
└── GuestyResponseError     # Unexpected response format, missing fields
```

---

## R-008: Minimum Home Assistant Version

**Context**: The integration needs a minimum HA version for
`manifest.json` and to ensure API compatibility (FR-019).

**Decision**: Target Home Assistant 2026.2.0 as the minimum version.

**Rationale**: The project's mypy configuration already targets
`homeassistant>=2026.2.0` for type stubs, confirming this as the
baseline. HA 2026.2.0 provides all required APIs: `ConfigFlow` with
reauth support, `config_entries.async_update_entry()`,
`async_get_clientsession()`, and `ConfigEntryNotReady`. No newer
APIs are needed for this MVP feature.

**Alternatives considered**:

- Older version (e.g., 2024.x): Would require compatibility shims
  and reduce access to modern HA APIs. The mypy stubs already
  assume 2026.2.0+.
- Latest version only: Unnecessarily restrictive; the APIs needed
  have been stable for several releases.

---

## R-009: Config Flow Reauth Pattern

**Context**: FR-021 requires a re-authentication flow that allows
credential updates without removing the integration (FR-021).

**Decision**: Implement `async_step_reauth` and
`async_step_reauth_confirm` in the `ConfigFlow` class, following the
standard HA reauth pattern.

**Rationale**: Home Assistant provides a built-in reauth mechanism
via `ConfigEntry.async_start_reauth()`. The integration triggers
reauth when the API client detects persistent authentication failures
(after token refresh also fails). The reauth flow pre-fills the
client_id and allows the user to update the client_secret.

**Alternatives considered**:

- Remove and re-add integration: Poor UX; loses any associated
  entities and automations.
- Options flow for credential editing: Non-standard; HA convention
  reserves options flow for non-credential settings.

**Flow**:

1. API client raises `GuestyAuthError` persistently
2. Integration calls `config_entry.async_start_reauth()`
3. HA shows reauth notification to user
4. User enters updated credentials in reauth form
5. Integration validates new credentials
6. On success: updates `config_entry.data`, clears token cache

---

## R-010: DTO Design for Token Data

**Context**: The API package needs data structures for token
caching and rate limit tracking that are serializable and immutable
(Constitution §II).

**Decision**: Use frozen `dataclasses` for `CachedToken` (access
token, issued time, expires-in seconds, scope) with computed
properties for expiry checking.

**Rationale**: Frozen dataclasses match the rentalsync-bridge
pattern (`PMSListing`, `PMSReservation`, etc.) and provide
immutability guarantees. Keeping `issued_at` + `expires_in` (rather
than just `expires_at`) preserves the original token response data
for debugging and allows flexible expiry buffer calculations.

**Alternatives considered**:

- Mutable dataclasses: Risk of accidental mutation; frozen is safer
  for shared data.
- Named tuples: Less discoverable; no default values or methods.
- TypedDict: No runtime validation or methods; less ergonomic.
- Pydantic models: Adds an unnecessary dependency for the small
  number of DTOs needed.

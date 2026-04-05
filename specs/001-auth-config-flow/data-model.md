<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Guesty Auth & Config Flow

**Feature**: 001-auth-config-flow
**Date**: 2025-07-18

## Entities

### CachedToken

Represents an OAuth 2.0 access token returned by the Guesty token
endpoint. Immutable after creation.

**Location**: `custom_components/guesty/api/models.py`

| Field | Type | Description |
| ----- | ---- | ----------- |
| `access_token` | `str` | The Bearer token value |
| `token_type` | `str` | Token type (always "Bearer") |
| `expires_in` | `int` | Token lifetime in seconds (typically 86400) |
| `scope` | `str` | OAuth scope (typically "open-api") |
| `issued_at` | `datetime` | UTC timestamp when token was acquired |

**Computed properties**:

- `expires_at -> datetime`: `issued_at + timedelta(seconds=expires_in)`
- `is_expired(buffer_seconds: int = 0) -> bool`:
  `utcnow() >= expires_at - timedelta(seconds=buffer_seconds)`

**Validation rules**:

- `access_token` must be non-empty
- `expires_in` must be positive
- `issued_at` must be timezone-aware (UTC)

**Serialization** (for config_entry persistence):

```python
def to_dict(self) -> dict[str, Any]:
    return {
        "access_token": self.access_token,
        "token_type": self.token_type,
        "expires_in": self.expires_in,
        "scope": self.scope,
        "issued_at": self.issued_at.isoformat(),
    }

@classmethod
def from_dict(cls, data: dict[str, Any]) -> CachedToken:
    return cls(
        access_token=data["access_token"],
        token_type=data["token_type"],
        expires_in=data["expires_in"],
        scope=data["scope"],
        issued_at=datetime.fromisoformat(data["issued_at"]),
    )
```

---

### TokenStorage (Protocol)

Abstract interface for token persistence. The `api/` package defines
this protocol; the HA integration provides the concrete
implementation.

**Location**: `custom_components/guesty/api/models.py`

```python
class TokenStorage(Protocol):
    """Protocol for persisting token and rate limit data."""

    async def load_token(self) -> CachedToken | None:
        """Load a previously persisted token.

        Returns None if no token is stored or if stored data is
        corrupted/invalid.
        """
        ...

    async def save_token(self, token: CachedToken) -> None:
        """Persist a token for later retrieval."""
        ...

    async def load_request_count(
        self,
    ) -> tuple[int, datetime | None]:
        """Load the token request counter and window start time.

        Returns (count, window_start). If no counter is stored,
        returns (0, None).
        """
        ...

    async def save_request_count(
        self, count: int, window_start: datetime
    ) -> None:
        """Persist the token request counter and window start."""
        ...
```

---

### HATokenStorage

Home Assistant implementation of `TokenStorage` backed by
`config_entry.data`.

**Location**: `custom_components/guesty/__init__.py`

| Dependency | Type | Description |
| ---------- | ---- | ----------- |
| `hass` | `HomeAssistant` | HA instance for config entry updates |
| `config_entry` | `ConfigEntry` | The integration's config entry |

**Storage layout in `config_entry.data`**:

```python
{
    # User-provided (set during config flow)
    "client_id": str,
    "client_secret": str,

    # Token cache (set by HATokenStorage)
    "cached_token": {
        "access_token": str,
        "token_type": str,
        "expires_in": int,
        "scope": str,
        "issued_at": str,  # ISO 8601
    } | None,

    # Rate limit tracking (set by HATokenStorage)
    "token_request_count": int,
    "token_window_start": str | None,  # ISO 8601
}
```

**Methods**:

- `load_token()`: Reads `config_entry.data["cached_token"]`,
  deserializes via `CachedToken.from_dict()`. Returns `None` if key
  is missing or data is corrupted (logs warning, does not raise).
- `save_token()`: Serializes token via `to_dict()`, updates
  `config_entry.data` via
  `hass.config_entries.async_update_entry()`.
- `load_request_count()`: Reads `token_request_count` and
  `token_window_start` from `config_entry.data`.
- `save_request_count()`: Updates count and window start in
  `config_entry.data`.

---

### GuestyTokenManager

Manages OAuth 2.0 token lifecycle: acquisition, caching, refresh,
and rate limit enforcement.

**Location**: `custom_components/guesty/api/auth.py`

| Field | Type | Description |
| ----- | ---- | ----------- |
| `_client_id` | `str` | Guesty API client ID |
| `_client_secret` | `str` | Client secret |
| `_http` | `httpx.AsyncClient` | HTTP client |
| `_storage` | `TokenStorage` | Persistence backend |
| `_cached_token` | `CachedToken \| None` | In-memory token cache |
| `_lock` | `asyncio.Lock` | Concurrent access guard |
| `_refresh_buffer` | `int` | Seconds before expiry to refresh (default 300) |

**State transitions**:

```text
[No Token] --get_token()--> [Requesting] --success--> [Cached/Valid]
[Cached/Valid] --expired--> [Requesting] --success--> [Cached/Valid]
[Cached/Valid] --invalidate()--> [No Token]
[Requesting] --auth error--> raises GuestyAuthError
[Requesting] --rate limit--> raises GuestyRateLimitError
[Requesting] --network error--> raises GuestyConnectionError
```

**Rate limit tracking**:

| Request count | Behavior |
| ------------- | -------- |
| 1-3 | Silent, allowed |
| 4 | Warning logged, allowed |
| 5 | Warning logged, allowed (last permitted) |
| 6+ | Raises `GuestyRateLimitError` with `reset_at` |

The 24-hour window starts from the first token request and resets
when the window elapses. Counter is persisted via `TokenStorage` to
survive restarts.

---

### GuestyApiClient

HTTP client for authenticated Guesty API requests with retry logic.

**Location**: `custom_components/guesty/api/client.py`

| Field | Type | Description |
| ----- | ---- | ----------- |
| `_token_manager` | `GuestyTokenManager` | Token provider |
| `_http` | `httpx.AsyncClient` | Injected HTTP client |
| `_base_url` | `str` | Guesty API base URL |

**Retry behavior on HTTP 429**:

| Attempt | Backoff delay | Notes |
| ------- | ------------- | ----- |
| 1 (initial) | 0 | First attempt |
| 2 (retry 1) | 1.0s or Retry-After | Prefer header |
| 3 (retry 2) | 2.0s or Retry-After | Exponential |
| 4 (retry 3) | 4.0s or Retry-After | Final attempt |
| Exhausted | N/A | GuestyRateLimitError |

All delays are capped at 30 seconds maximum.

---

### Integration Configuration (config_entry.data)

Represents a single Guesty account connection as stored by Home
Assistant.

| Field | Type | Source |
| ----- | ---- | ------ |
| `client_id` | `str` | Config flow |
| `client_secret` | `str` | Config flow |
| `cached_token` | `dict` or `None` | HATokenStorage |
| `token_request_count` | `int` | HATokenStorage |
| `token_window_start` | `str` or `None` | HATokenStorage |

Field descriptions:

- `client_id`: Guesty API client ID
- `client_secret`: Guesty API client secret
- `cached_token`: Serialized `CachedToken`
- `token_request_count`: Requests in current 24h window
- `token_window_start`: ISO 8601 window start time

**Unique ID**: `client_id` (prevents duplicate configurations of the
same Guesty account per FR-004).

---

## Relationships

```text
ConfigEntry.data
  ├── contains → client_id, client_secret
  ├── contains → CachedToken (serialized)
  └── contains → token_request_count, window_start

HATokenStorage
  ├── implements → TokenStorage (protocol)
  ├── reads/writes → ConfigEntry.data
  └── used by → GuestyTokenManager

GuestyTokenManager
  ├── uses → TokenStorage (for persistence)
  ├── uses → httpx.AsyncClient (for HTTP POST to token endpoint)
  ├── manages → CachedToken (in-memory cache)
  └── used by → GuestyApiClient

GuestyApiClient
  ├── uses → GuestyTokenManager (for authentication)
  ├── uses → httpx.AsyncClient (for API requests)
  └── raises → GuestyApiError hierarchy
```

## Exception Hierarchy

```text
GuestyApiError
│   Base exception for all Guesty API errors.
│   Attributes: message (str)
│
├── GuestyAuthError
│   Authentication failures: invalid credentials, expired token
│   after refresh attempt, 401/403 responses.
│
├── GuestyRateLimitError
│   Rate limit exceeded: HTTP 429 after max retries, or token
│   request limit (5/24h) exceeded.
│   Attributes: retry_after (float | None), reset_at (datetime | None)
│
├── GuestyConnectionError
│   Network-level failures: DNS resolution, TCP connection,
│   TLS handshake, timeouts.
│
└── GuestyResponseError
    Unexpected response format: missing expected fields,
    unparsable JSON, unexpected content type.
```

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Guesty Auth & Config Flow

**Feature**: 001-auth-config-flow
**Date**: 2025-07-18

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- Git with pre-commit hooks installed

## Project Setup

```bash
# Clone and enter the repository
cd /path/to/guesty

# Install dependencies (once pyproject.toml exists)
uv sync

# Install pre-commit hooks
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
```

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -x -q

# Run API package tests only
uv run pytest tests/api/ -x -q

# Run config flow tests only
uv run pytest tests/test_config_flow.py -x -q

# Run with coverage
uv run pytest tests/ --cov=custom_components/guesty --cov-report=term-missing
```

## Running Linting

```bash
# Ruff linting and formatting
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/

# Type checking
uv run mypy custom_components/ tests/

# Docstring coverage
uv run interrogate -v custom_components/

# Run all pre-commit hooks
prek
```

## Architecture Overview

```text
custom_components/guesty/
├── api/                 ← Library-extractable (zero HA imports)
│   ├── __init__.py      # Public exports
│   ├── auth.py          # GuestyTokenManager
│   ├── client.py        # GuestyApiClient
│   ├── const.py         # API constants
│   ├── exceptions.py    # Exception hierarchy
│   └── models.py        # DTOs + TokenStorage protocol
├── __init__.py          # HA setup (HATokenStorage, entry lifecycle)
├── config_flow.py       # HA config flow
├── const.py             # HA constants (DOMAIN, CONF_* keys)
├── manifest.json        # Integration metadata
└── strings.json         # Localized UI strings
```

### Key Design Rule

The `api/` package has **zero** `homeassistant.*` imports. It
communicates with HA only through:

1. **`TokenStorage` protocol** — HA implements this to persist tokens
2. **`httpx.AsyncClient`** — HA creates and injects the HTTP client
3. **Constructor parameters** — Credentials and config passed as
   plain Python types

This ensures the `api/` package can be extracted into a standalone
`guesty-api` Python library in the future.

## Development Workflow

This project follows strict TDD (Red-Green-Refactor):

1. **Write a failing test** that defines the desired behavior
2. **Implement minimum code** to make the test pass
3. **Refactor** while keeping all tests green
4. **Run linting**: `uv run ruff check custom_components/ tests/`
5. **Stage and commit** with sign-off:

   ```bash
   git add <files>
   git commit -s -m "Type(scope): description

   Body explaining what and why.

   Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
   ```

6. If pre-commit fails, fix issues, `git add`, and retry (no reset)

## Key Patterns

### Token Manager Usage

```python
from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.models import TokenStorage

# In async_setup_entry:
storage = HATokenStorage(hass, config_entry)
token_manager = GuestyTokenManager(
    client_id=config_entry.data["client_id"],
    client_secret=config_entry.data["client_secret"],
    http_client=http_client,
    storage=storage,
)
# get_token() handles caching, refresh, and rate limits transparently
token = await token_manager.get_token()
```

### API Client Usage

```python
from custom_components.guesty.api.client import GuestyApiClient

client = GuestyApiClient(
    token_manager=token_manager,
    http_client=http_client,
)
# test_connection() validates credentials and API access
await client.test_connection()
```

### Error Handling

```python
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyRateLimitError,
)

try:
    await client.test_connection()
except GuestyAuthError:
    errors["base"] = "invalid_auth"
except GuestyConnectionError:
    errors["base"] = "cannot_connect"
except GuestyRateLimitError:
    errors["base"] = "rate_limited"
```

## Testing Patterns

### Mocking HTTP with respx

```python
import respx
from httpx import Response

@respx.mock
async def test_token_acquisition():
    respx.post("https://open-api.guesty.com/oauth2/token").mock(
        return_value=Response(200, json={
            "token_type": "Bearer",
            "access_token": "test-token",
            "expires_in": 86400,
            "scope": "open-api",
        })
    )
    token = await token_manager.get_token()
    assert token == "test-token"
```

### Fake TokenStorage for API Tests

```python
class FakeTokenStorage:
    """In-memory TokenStorage for tests."""

    def __init__(self):
        self._token = None
        self._count = 0
        self._window = None

    async def load_token(self):
        return self._token

    async def save_token(self, token):
        self._token = token

    async def load_request_count(self):
        return (self._count, self._window)

    async def save_request_count(self, count, window_start):
        self._count = count
        self._window = window_start
```

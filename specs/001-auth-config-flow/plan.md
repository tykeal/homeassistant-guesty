<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Guesty Auth & Config Flow (MVP)

**Branch**: `001-auth-config-flow` | **Date**: 2025-07-18 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`/specs/001-auth-config-flow/spec.md`

## Summary

Implement the foundational authentication and configuration flow for the
Guesty Home Assistant custom component integration. This feature
establishes OAuth 2.0 Client Credentials authentication against the
Guesty Open API, persistent token caching across HA restarts, proactive
and reactive token refresh, rate limit enforcement (5 token
requests/24h, API tier limits), and a standard HA config flow for
credential entry and validation.

The implementation uses a **library-shim architecture**: a
HA-independent `api/` sub-package (zero `homeassistant.*` imports)
containing the HTTP client, token manager, DTOs, and exception
hierarchy. This package is designed for future extraction into a shared
`guesty-api` Python library. The HA-specific integration shell
(`__init__.py`, `config_flow.py`, `const.py`) bridges the `api/`
package to Home Assistant via a `TokenStorage` protocol implementation
backed by HA config entry storage.

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: httpx (async HTTP), homeassistant (>=2026.2.0)
**Storage**: HA config entry storage (`hass.data`, `config_entry.data`)
for token persistence
**Testing**: pytest with pytest-homeassistant-custom-component, respx
(httpx mocking)
**Target Platform**: Home Assistant custom component (HACS-installable)
**Project Type**: HA custom integration with extractable API library
**Performance Goals**: Non-blocking async operations; zero measurable
overhead on HA event loop
**Constraints**: 5 token requests/24h per client ID; API rate limits
15/s, 120/min, 5000/hr; token lifetime 24h
**Scale/Scope**: Single integration, 1 config flow, 1 API client, ~15
source files, ~20 test modules

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1
design.*

- **I. Code Quality & Testing**: ✅ PASS — TDD enforced
  within each phase; 100% docstring coverage; full type
  annotations.
- **II. API Client Design**: ✅ PASS — Clean `api/`
  abstraction; OAuth transparent; rate limiting built in;
  mockable via DI.
- **III. Atomic Commit Discipline**: ✅ PASS — Each phase
  broken into atomic commits; task updates separate.
- **IV. Licensing & Attribution**: ✅ PASS — SPDX headers
  on all files; REUSE.toml covers JSON/brand assets.
- **V. Pre-Commit Integrity**: ✅ PASS — All hooks run;
  no bypass.
- **VI. Agent Co-Authorship & DCO**: ✅ PASS —
  `git commit -s` with Co-authored-by trailer.
- **VII. UX Consistency**: ✅ PASS — Standard HA config
  flow; localized strings; reauth flow.
- **VIII. Performance Requirements**: ✅ PASS — Full
  async; no event loop blocking; rate limit awareness.
- **IX. Phased Development**: ✅ PASS — 4 phases; unit
  TDD in each; integration tests in Phase 4.
- **X. Security & Credentials**: ✅ PASS — Tokens in HA
  storage only; log sanitization; no secrets in VCS.

**Gate result**: ✅ ALL PASS — proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/001-auth-config-flow/
├── plan.md              # This file
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: entity/data model
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/           # Phase 1: API contracts
│   └── guesty-oauth.md  # OAuth token endpoint contract
└── tasks.md             # Phase 2: task breakdown (separate command)
```

### Source Code (repository root)

```text
custom_components/guesty/
├── __init__.py          # HA integration setup
├── config_flow.py       # HA config flow
├── const.py             # HA-level constants
├── strings.json         # Localized UI strings
├── manifest.json        # Integration metadata
└── api/                 # LIBRARY-EXTRACTABLE SHIM
    ├── __init__.py       # Public API exports
    ├── client.py         # GuestyApiClient
    ├── auth.py           # GuestyTokenManager
    ├── models.py         # DTOs + TokenStorage protocol
    ├── exceptions.py     # Exception hierarchy
    └── const.py          # API constants

tests/
├── conftest.py              # Shared fixtures (mock HTTP, fake HA)
├── api/                     # Unit tests for api/ package
│   ├── __init__.py
│   ├── test_auth.py         # Token manager tests
│   ├── test_client.py       # HTTP client tests
│   ├── test_exceptions.py   # Exception hierarchy tests
│   └── test_models.py       # DTO tests
├── test_config_flow.py      # Config flow unit tests
├── test_init.py             # Integration setup/teardown tests
└── test_token_persistence.py # Cross-restart token persistence tests
```

**Structure Decision**: Home Assistant custom component layout with a
clean `api/` sub-package designed for future library extraction. The
`api/` package has zero `homeassistant.*` imports and uses
`typing.Protocol` for storage abstraction. Tests mirror the source
structure with separate directories for API-layer and HA-layer tests.

## Phase Overview

### Phase 1: API Foundation (`api/` package)

Build the library-extractable API client layer with zero HA
dependencies.

**Deliverables**:

- `api/exceptions.py` — Exception hierarchy:
  `GuestyApiError` → `GuestyAuthError`, `GuestyRateLimitError`,
  `GuestyConnectionError`, `GuestyResponseError`
- `api/const.py` — API constants: token URL, base URL, default
  timeouts, rate limit thresholds, backoff parameters
- `api/models.py` — Frozen dataclasses: `CachedToken`,
  `TokenRequestRecord`; `TokenStorage` protocol
- `api/auth.py` — `GuestyTokenManager`: token acquisition via OAuth
  2.0 Client Credentials, in-memory + persistent caching via
  `TokenStorage` protocol, 5-per-24h rate tracking, `asyncio.Lock`
  double-checked locking for concurrent access, proactive refresh
  (configurable buffer, default 5 min)
- `api/client.py` — `GuestyApiClient`: wraps `httpx.AsyncClient`
  (injected), authenticated requests via token manager, exponential
  backoff with jitter on 429 (respects Retry-After), configurable
  timeouts, `test_connection()` method
- `api/__init__.py` — Public API surface exports
- Unit tests for all of the above (TDD: tests written first)

**Key design decisions**:

- `httpx.AsyncClient` passed via dependency injection (same as
  rentalsync-bridge)
- `TokenStorage` is a `typing.Protocol` so the `api/` package never
  imports HA
- Double-checked locking pattern (check → lock → re-check) prevents
  thundering herd on token refresh
- Rate limit counter persisted via `TokenStorage` to survive restarts
- Exponential backoff: 1s → 2s → 4s, max 30s, max 3 retries
  (matching rentalsync-bridge)

### Phase 2: HA Integration Shell

Wire the `api/` package into Home Assistant's component lifecycle.

**Deliverables**:

- `manifest.json` — domain `guesty`, `config_flow: true`,
  `iot_class: cloud_polling`, HA version `2026.2.0`,
  requirements: `httpx`
- `const.py` — `DOMAIN`, `CONF_CLIENT_ID`, `CONF_CLIENT_SECRET`,
  `PLATFORMS` (empty for MVP)
- `strings.json` — Localized strings for config flow steps, field
  labels, error messages, abort reasons
- `__init__.py` — `HATokenStorage` (implements `TokenStorage` using
  `config_entry.data`/`hass.data`); `async_setup_entry` (creates
  `httpx.AsyncClient`, `GuestyTokenManager`, `GuestyApiClient`,
  stores in `hass.data`); `async_unload_entry` (closes client,
  cleans `hass.data`)
- Unit tests for setup/teardown lifecycle

### Phase 3: Config Flow

Implement the user-facing configuration experience.

**Deliverables**:

- `config_flow.py` — `GuestyConfigFlow(ConfigFlow, domain=DOMAIN)`:
  - `async_step_user`: credential entry form (client_id,
    client_secret), validates by acquiring a token
  - `async_step_reauth`: re-authentication flow (FR-021)
  - Duplicate detection via `unique_id` from `client_id` (FR-004)
  - Error mapping: `GuestyAuthError` → `invalid_auth`,
    `GuestyConnectionError` → `cannot_connect`,
    `GuestyRateLimitError` → `rate_limited`
- Unit tests for all config flow paths (valid, invalid, duplicate,
  reauth, network error, rate limit)

### Phase 4: Testing & Integration

Comprehensive test coverage and cross-phase validation.

**Deliverables**:

- Integration tests for token persistence across simulated restarts
- Integration tests for config flow → setup entry → client
  operational flow
- Edge case tests: clock drift, rapid restarts, concurrent refresh,
  malformed responses
- All tests use mocked HTTP via `respx` (no live Guesty API)
- Verify SC-002 (10 restarts without exhausting token limit)
- Verify SC-008 (no credentials in logs at any level)

## Complexity Tracking

> No constitution violations identified. The library-shim architecture
> adds structural complexity but is justified by the future library
> extraction requirement and matches the established rentalsync-bridge
> patterns.

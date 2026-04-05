<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Guesty Auth & Config Flow (MVP)

**Input**: Design documents from `/specs/001-auth-config-flow/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅,
data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Tests**: TDD is mandatory per project constitution. Every unit of
production code MUST be preceded by a failing test. Tests are written
first in each phase, then implementation makes them pass.

**Organization**: Tasks are grouped by user story to enable independent
implementation and testing of each story. The plan's architectural
phases (API Foundation → HA Shell → Config Flow → Testing) are mapped
to user story phases where each story progressively enhances the
codebase.

## Format: `ID [P] [Story] Description`

- **ID**: Plain task identifier (e.g., `T001`, `T002`)
- **[P]** *(optional)*: Can run in parallel (different files, no
  dependencies on incomplete tasks)
- **[Story]** *(optional)*: Which user story this task belongs to
  (e.g., `[US1]`, `[US2]`). Present only in user story phases;
  omitted for Setup, Foundational, and Polish phases.
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/guesty/` (HA custom component)
- **API layer**: `custom_components/guesty/api/` (library-extractable,
  zero HA imports)
- **Tests**: `tests/` (HA-level), `tests/api/` (API layer unit tests)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create project directory structure and package scaffolding

- [X] T001 Create directory structure with `__init__.py` packages for
  `custom_components/guesty/`, `custom_components/guesty/api/`,
  `tests/`, and `tests/api/` per plan.md project structure

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Exception hierarchy, API constants, DTOs, and
TokenStorage protocol — required by ALL user stories

**⚠️ CRITICAL**: No user story work can begin until this phase is
complete

### Tests for Foundational

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [X] T002 [P] Write exception hierarchy tests in
  `tests/api/test_exceptions.py`: verify `GuestyApiError` base class,
  `GuestyAuthError`, `GuestyRateLimitError` (with `retry_after` and
  `reset_at` attrs), `GuestyConnectionError`, `GuestyResponseError`
  inheritance chain and string representation
- [X] T003 [P] Write `CachedToken` frozen dataclass and
  `TokenStorage` protocol tests in `tests/api/test_models.py`: creation
  with valid data, frozen immutability, `expires_at` computation,
  `is_expired` with and without buffer, `to_dict`/`from_dict`
  round-trip serialization, validation (empty token raises, negative
  expiry raises, naive datetime raises)

### Implementation for Foundational

- [X] T004 [P] Implement exception hierarchy in
  `custom_components/guesty/api/exceptions.py`: `GuestyApiError` base
  with `message` attr; `GuestyAuthError`; `GuestyRateLimitError` with
  `retry_after: float | None` and `reset_at: datetime | None`;
  `GuestyConnectionError`; `GuestyResponseError` — all with SPDX
  header, docstrings, type annotations
- [X] T005 [P] Implement API constants in
  `custom_components/guesty/api/const.py`: `TOKEN_URL`
  (`https://open-api.guesty.com/oauth2/token`), `BASE_URL`
  (`https://open-api.guesty.com/v1`), `DEFAULT_TIMEOUT` (30s),
  `DEFAULT_REFRESH_BUFFER` (300s), `MAX_TOKEN_REQUESTS_PER_WINDOW` (5),
  `TOKEN_WINDOW_SECONDS` (86400), `MAX_RETRIES` (3),
  `INITIAL_BACKOFF` (1.0), `BACKOFF_MULTIPLIER` (2.0),
  `MAX_BACKOFF` (30.0), `GRANT_TYPE` (`client_credentials`),
  `SCOPE` (`open-api`)
- [X] T006 [P] Implement `CachedToken` frozen dataclass and
  `TokenStorage` protocol in
  `custom_components/guesty/api/models.py`: `CachedToken` with
  `access_token`, `token_type`, `expires_in`, `scope`, `issued_at`
  fields; `expires_at` and `is_expired(buffer_seconds)` computed
  properties; `to_dict()`/`from_dict()` serialization; `TokenStorage`
  protocol with `load_token`, `save_token`, `load_request_count`,
  `save_request_count` async methods per data-model.md
- [X] T007 Create public API surface exports in
  `custom_components/guesty/api/__init__.py`: export all exception
  classes, `CachedToken`, `TokenStorage`, and constants needed by
  consumers
- [X] T008 Create shared test fixtures in `tests/conftest.py`:
  `FakeTokenStorage` in-memory implementation of `TokenStorage`
  protocol, mock token response factory, common test constants
  (fake client ID/secret, token URL), `httpx.AsyncClient` fixture
  with `respx` mocking

**Checkpoint**: Foundation ready — user story implementation can now
begin

---

## Phase 3: User Story 1 — Initial Integration Setup (P1) 🎯 MVP

**Goal**: Users can add the Guesty integration via HA UI, enter
credentials (client ID + secret), have them validated against the
Guesty token endpoint, and establish a working connection. Duplicate
accounts are detected and prevented.

**Independent Test**: Add the integration through the HA config flow
UI with valid and invalid credentials. Verify success creates a config
entry and failure shows actionable error messages.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [X] T009 [P] [US1] Write token acquisition unit tests in
  `tests/api/test_auth.py`: successful token acquisition via
  `get_token()`, cached token reuse on second call, `GuestyAuthError`
  on 401 invalid credentials, `GuestyConnectionError` on network
  failure/timeout, `GuestyResponseError` on malformed token response
  (missing fields, invalid JSON) — all using `respx` to mock HTTP
  and `FakeTokenStorage`
- [X] T010 [P] [US1] Write API client `test_connection` unit tests in
  `tests/api/test_client.py`: successful connection test (GET
  `/listings?limit=1&fields=_id`), `GuestyAuthError` propagation from
  token manager, `GuestyConnectionError` on network failure,
  authenticated request includes `Authorization: Bearer` header —
  using `respx` mocking
- [X] T011 [P] [US1] Write config flow `step_user` tests in
  `tests/test_config_flow.py`: successful credential entry creates
  config entry, invalid credentials show `invalid_auth` error,
  connection failure shows `cannot_connect` error, duplicate
  `client_id` triggers `already_configured` abort, form fields
  include `client_id` and `client_secret` — using
  `pytest-homeassistant-custom-component` fixtures
- [X] T012 [P] [US1] Write `async_setup_entry` and
  `async_unload_entry` tests in `tests/test_init.py`: setup creates
  `httpx.AsyncClient`, `GuestyTokenManager`, and `GuestyApiClient`
  in `hass.data[DOMAIN]`; unload closes HTTP client and removes
  `hass.data` entry; setup failure raises `ConfigEntryNotReady` —
  using HA test fixtures

### Implementation for User Story 1

- [X] T013 [US1] Implement `GuestyTokenManager` basic token
  acquisition in `custom_components/guesty/api/auth.py`: constructor
  accepting `client_id`, `client_secret`, `http_client`
  (`httpx.AsyncClient`), and `storage` (`TokenStorage`);
  `get_token()` method that POSTs to token endpoint with
  `client_credentials` grant type and `open-api` scope; in-memory
  `CachedToken` caching; maps HTTP 401 to `GuestyAuthError`, network
  errors to `GuestyConnectionError`, malformed responses to
  `GuestyResponseError`
- [X] T014 [US1] Implement `GuestyApiClient` with `test_connection`
  in `custom_components/guesty/api/client.py`: constructor accepting
  `token_manager` (`GuestyTokenManager`), `http_client`
  (`httpx.AsyncClient`), and optional `base_url`; `test_connection()`
  that acquires token and GETs `/listings?limit=1&fields=_id`;
  `_request()` helper that adds `Authorization: Bearer` header;
  propagates all `GuestyApiError` subtypes
- [X] T015 [US1] Update public exports in
  `custom_components/guesty/api/__init__.py` to include
  `GuestyTokenManager` and `GuestyApiClient`
- [X] T016 [P] [US1] Create integration manifest in
  `custom_components/guesty/manifest.json`: domain `guesty`,
  `config_flow: true`, `iot_class: cloud_polling`, HA version
  `2026.2.0`, requirements `httpx`, codeowners, documentation URL
- [X] T017 [P] [US1] Create HA-level constants in
  `custom_components/guesty/const.py`: `DOMAIN = "guesty"`,
  `CONF_CLIENT_ID`, `CONF_CLIENT_SECRET`, `PLATFORMS: list[Platform]`
  (empty for MVP)
- [X] T018 [P] [US1] Create localized UI strings in
  `custom_components/guesty/strings.json`: config flow step `user`
  title and field descriptions (`client_id`, `client_secret`), error
  messages (`invalid_auth`, `cannot_connect`, `unknown`,
  `rate_limited`), abort reasons (`already_configured`)
- [X] T019 [US1] Implement stub `HATokenStorage` and entry lifecycle
  in `custom_components/guesty/__init__.py`: `HATokenStorage` class
  with stub `load_token`/`save_token`/`load_request_count`/
  `save_request_count` (return defaults); `async_setup_entry` creates
  `httpx.AsyncClient`, `GuestyTokenManager`, `GuestyApiClient`,
  stores in `hass.data[DOMAIN][entry.entry_id]`, calls
  `test_connection()` (raises `ConfigEntryNotReady` on failure);
  `async_unload_entry` closes HTTP client, removes `hass.data` entry
- [X] T020 [US1] Implement config flow `step_user` in
  `custom_components/guesty/config_flow.py`:
  `GuestyConfigFlow(ConfigFlow, domain=DOMAIN)` with
  `async_step_user` showing credential form, validating via temporary
  `GuestyTokenManager`/`GuestyApiClient`, mapping
  `GuestyAuthError` → `invalid_auth`,
  `GuestyConnectionError` → `cannot_connect`,
  `GuestyRateLimitError` → `rate_limited`; sets `unique_id` from
  `client_id` and calls `_abort_if_unique_id_configured()` for
  duplicate detection

**Checkpoint**: User Story 1 is fully functional — users can add the
Guesty integration via config flow with credential validation and
duplicate detection

---

## Phase 4: User Story 2 — Token Persistence Across Restarts (P2)

**Goal**: Authentication tokens survive HA restarts. On startup, the
integration reuses a persisted token if still valid, avoiding
unnecessary token requests and preserving the 5-per-24h budget.

**Independent Test**: Configure the integration, simulate an HA
restart, verify the integration resumes using the stored token without
making a new authentication request.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [X] T021 [P] [US2] Write `HATokenStorage` persistence tests in
  `tests/test_init.py`: `save_token` persists `CachedToken` to
  `config_entry.data["cached_token"]` via
  `async_update_entry`; `load_token` deserializes stored token;
  `load_token` returns `None` for missing/corrupted data without
  raising; `save_request_count`/`load_request_count` round-trip
  correctly
- [X] T022 [US2] Write cross-restart token persistence tests in
  `tests/test_token_persistence.py`: setup entry with persisted
  valid token reuses it (no HTTP POST to token endpoint); setup
  entry with expired persisted token acquires new one; setup entry
  with missing token data acquires new one gracefully; token
  acquired during operation is persisted for next restart

### Implementation for User Story 2

- [X] T023 [US2] Implement full `HATokenStorage` load/save methods
  in `custom_components/guesty/__init__.py`: `load_token` reads
  `config_entry.data["cached_token"]`, deserializes via
  `CachedToken.from_dict()`, returns `None` on missing/corrupted
  data with warning log; `save_token` serializes via `to_dict()`,
  updates `config_entry.data` via
  `hass.config_entries.async_update_entry()`; `load_request_count`
  reads `token_request_count` and `token_window_start`;
  `save_request_count` persists count and window start
- [X] T024 [US2] Integrate startup token loading in
  `async_setup_entry` in `custom_components/guesty/__init__.py`:
  after creating `GuestyTokenManager`, call storage `load_token()`
  and seed the token manager's in-memory cache if token is valid;
  add `GuestyTokenManager.seed_token(token: CachedToken)` method
  in `custom_components/guesty/api/auth.py` for startup loading

**Checkpoint**: User Stories 1 AND 2 both work — integration survives
restarts without re-authentication

---

## Phase 5: User Story 3 — Transparent Token Refresh (P2)

**Goal**: Token refresh happens transparently. Proactive refresh
occurs before expiry (configurable 5-min buffer). Reactive refresh
occurs on unexpected 401 responses. Concurrent requests share a single
refresh operation.

**Independent Test**: Simulate token expiry (both proactive near-expiry
and reactive 401 failure) and verify the integration continues
operating without interruption or duplicate token requests.

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [X] T025 [P] [US3] Write proactive refresh and concurrent access
  tests in `tests/api/test_auth.py`: `get_token()` refreshes when
  token is within buffer of expiry; `get_token()` with `asyncio.Lock`
  double-checked locking — only one HTTP POST when 5 concurrent
  callers hit expired token; `invalidate()` clears cached token
  forcing next `get_token()` to re-acquire; refreshed token is saved
  via `TokenStorage.save_token()`
- [X] T026 [P] [US3] Write reactive 401 refresh and request retry
  tests in `tests/api/test_client.py`: `_request()` on HTTP 401
  calls `token_manager.invalidate()`, re-acquires token, retries
  original request once; second consecutive 401 raises
  `GuestyAuthError` (no infinite retry loop); successful retry
  returns the retried response data

### Implementation for User Story 3

- [X] T027 [US3] Add proactive refresh buffer, `asyncio.Lock`
  double-checked locking, and `invalidate()` to
  `GuestyTokenManager` in `custom_components/guesty/api/auth.py`:
  `_refresh_buffer` constructor param (default 300s); `is_expired`
  check uses buffer; `_lock = asyncio.Lock()` with check → lock →
  re-check pattern per R-004; `invalidate()` clears `_cached_token`;
  persist token via `storage.save_token()` after acquisition
- [X] T028 [US3] Add 401 detection, token invalidation, and request
  retry to `GuestyApiClient._request()` in
  `custom_components/guesty/api/client.py`: on HTTP 401 response,
  call `token_manager.invalidate()`, call `token_manager.get_token()`
  for fresh token, retry the original request once; raise
  `GuestyAuthError` if retry also fails with 401

**Checkpoint**: Token refresh is fully transparent — proactive,
reactive, and concurrency-safe

---

## Phase 6: User Story 4 — Graceful Rate Limit Handling (P3)

**Goal**: The integration respects Guesty's 5-token-request-per-24h
limit and API rate limits (15/s, 120/min, 5000/hr). Rate limit
responses trigger exponential backoff with jitter.

**Independent Test**: Simulate rate limit responses (429) and verify
the integration backs off appropriately. Simulate 5+ token requests
and verify the 24h limit is enforced.

### Tests for User Story 4

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [X] T029 [P] [US4] Write 5-per-24h token rate limit tests in
  `tests/api/test_auth.py`: requests 1-3 succeed silently; request
  4 logs warning and succeeds; request 5 logs warning and succeeds
  (last permitted); request 6 raises `GuestyRateLimitError` with
  `reset_at`; window reset after 24h allows new requests; counter
  persisted via `TokenStorage.save_request_count()`; counter loaded
  from storage on construction
- [X] T030 [P] [US4] Write HTTP 429 backoff and Retry-After tests
  in `tests/api/test_client.py`: 429 response triggers retry with
  exponential backoff (1s → 2s → 4s); `Retry-After` header overrides
  calculated backoff; max 3 retries then `GuestyRateLimitError`;
  backoff capped at 30s; jitter applied to delay

### Implementation for User Story 4

- [X] T031 [US4] Add token request counter tracking with 24h rolling
  window to `GuestyTokenManager` in
  `custom_components/guesty/api/auth.py`: `_request_count` and
  `_window_start` fields; `_check_rate_limit()` called before token
  HTTP POST; warnings at count 4 and 5; `GuestyRateLimitError` at
  count 6+; window resets when 24h elapses; counter persisted via
  `storage.save_request_count()` after each token request; counter
  loaded via `storage.load_request_count()` during initialization
- [X] T032 [US4] Add exponential backoff with jitter on HTTP 429 to
  `GuestyApiClient._request()` in
  `custom_components/guesty/api/client.py`: max 3 retries (4 total
  attempts); initial backoff 1.0s, multiplier 2x, capped at 30s;
  `Retry-After` header overrides calculated backoff; randomized
  jitter (±25%) on each delay; exhausted retries raise
  `GuestyRateLimitError` with `retry_after` from last response

**Checkpoint**: Rate limiting fully enforced — both token request
budget and API tier limits

---

## Phase 7: User Story 5 — Clear Error Communication (P3)

**Goal**: Errors are communicated clearly via HA UI. Persistent auth
failures trigger a reauth flow. Sensitive data never appears in logs.

**Independent Test**: Simulate credential revocation and verify the
reauth notification appears. Simulate various failures and verify
appropriate error messages. Check all log output for absence of
tokens/secrets.

### Tests for User Story 5

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [X] T033 [P] [US5] Write reauth flow tests in
  `tests/test_config_flow.py`: `async_step_reauth` shows reauth
  form pre-filled with `client_id`; `async_step_reauth_confirm`
  validates new credentials, updates `config_entry.data`, clears
  token cache on success; reauth with invalid new credentials shows
  `invalid_auth` error; reauth with connection failure shows
  `cannot_connect` error
- [X] T034 [P] [US5] Write reauth trigger and log sanitization tests
  in `tests/test_init.py`: persistent `GuestyAuthError` during
  operation triggers `config_entry.async_start_reauth()`; verify
  log output at all levels (DEBUG through CRITICAL) never contains
  token values or client secrets using `caplog` fixture

### Implementation for User Story 5

- [X] T035 [US5] Add `async_step_reauth` and
  `async_step_reauth_confirm` to config flow in
  `custom_components/guesty/config_flow.py`: reauth entry point
  stores existing `client_id`; reauth confirm form pre-fills
  `client_id`, accepts new `client_secret`; validates new
  credentials via temporary token manager; on success updates
  `config_entry.data` and triggers reload; error mapping matches
  `step_user` pattern
- [X] T036 [P] [US5] Add reauth step strings and abort reasons to
  `custom_components/guesty/strings.json`: reauth step title and
  field descriptions, `reauth_successful` abort reason
- [X] T037 [US5] Add reauth trigger on persistent auth failure and
  log sanitization audit in
  `custom_components/guesty/__init__.py`: catch `GuestyAuthError`
  from operations and call
  `config_entry.async_start_reauth(hass)` (FR-021); review all
  `_LOGGER` calls across all source files to ensure no token values
  or client secrets are logged (FR-017, SC-008)

**Checkpoint**: All user stories independently functional with clear
error communication and secure logging

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Integration tests spanning multiple stories, edge case
coverage, success criteria verification, and documentation validation

- [X] T038 [P] Write end-to-end integration test (config flow → setup
  entry → client operational → unload) in `tests/test_init.py`
- [X] T039 [P] Write edge case tests (clock drift token expiry,
  malformed API responses, unexpected content types) in
  `tests/api/test_auth.py` and `tests/api/test_client.py`
- [X] T040 [P] Write 10-restart scenario test verifying SC-002 (token
  reuse prevents exhausting 5-request limit) in
  `tests/test_token_persistence.py`
- [X] T041 Write SC-008 log sanitization audit across all test modules
  ensuring tokens and credentials never appear in any log level
- [X] T042 Validate `specs/001-auth-config-flow/quickstart.md`
  development workflow steps against implemented code
- [X] T043 Final code review for docstring coverage, type annotation
  completeness, and SPDX header compliance across all source and
  test files

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user
  stories
- **User Stories (Phase 3+)**: All depend on Foundational phase
  completion
  - US1 (P1): Can start after Foundational
  - US2 (P2): Depends on US1 (needs HATokenStorage, setup_entry)
  - US3 (P2): Depends on US2 (needs token persistence for save
    after refresh)
  - US4 (P3): Depends on US3 (extends rate limit into same auth
    logic)
  - US5 (P3): Depends on US1 only (adds reauth to config flow) —
    CAN run in parallel with US2/US3/US4
- **Polish (Phase 8)**: Depends on all user stories being complete

### User Story Dependencies

```text
Foundational ──┬──→ US1 (P1) ──┬──→ US2 (P2) → US3 (P2) → US4 (P3)
               │               │
               │               └──→ US5 (P3)  [parallel with US2-US4]
               │
               └──→ [All stories blocked until Foundational complete]
```

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (TDD)
2. API layer (`api/`) before HA layer (`__init__.py`, `config_flow.py`)
3. Models/DTOs before services
4. Services before endpoints/flows
5. Story complete before moving to next priority (unless parallel)

### Parallel Opportunities

- All Foundational tests (T002, T003) can run in parallel
- All Foundational implementations (T004, T005, T006) can run in
  parallel
- All US1 tests (T009-T012) can run in parallel
- US1 HA metadata files (T016, T017, T018) can run in parallel
- US5 can run in parallel with US2/US3/US4 (independent dependency
  chain from US1)
- All Polish tasks marked [P] (T038-T040) can run in parallel

---

## Parallel Example: User Story 1

```text
# Phase 3 — write all US1 tests in parallel:
Task T009: "Token acquisition tests in tests/api/test_auth.py"
Task T010: "Client test_connection tests in tests/api/test_client.py"
Task T011: "Config flow step_user tests in tests/test_config_flow.py"
Task T012: "Setup/unload entry tests in tests/test_init.py"

# Phase 3 — HA metadata files in parallel (alongside T013-T015):
Task T016: "Create manifest.json"
Task T017: "Create HA const.py"
Task T018: "Create strings.json"
```

## Parallel Example: Foundational Phase

```text
# Write all foundational tests in parallel:
Task T002: "Exception hierarchy tests"
Task T003: "CachedToken and TokenStorage tests"

# Implement all foundational modules in parallel:
Task T004: "Exception hierarchy in api/exceptions.py"
Task T005: "API constants in api/const.py"
Task T006: "CachedToken and TokenStorage in api/models.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Test US1 independently — config flow works
   with valid/invalid credentials, duplicate detection functions
5. Deploy/demo if ready (basic integration setup works)

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 → Test independently → **MVP! Integration can be added**
3. Add US2 → Test independently → **Tokens survive restarts**
4. Add US3 → Test independently → **Token refresh is transparent**
5. Add US4 → Test independently → **Rate limits enforced**
6. Add US5 → Test independently → **Errors communicated clearly**
7. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 → US2 → US3 → US4 (main chain)
   - Developer B: US5 (after US1 completes, parallel with US2-US4)
3. Stories integrate independently; Polish after all complete

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Each user story is independently testable after completion
- TDD mandatory: write failing test → implement → refactor → green
- Commit after each task or logical group (atomic commits per
  AGENTS.md)
- Stop at any checkpoint to validate story independently
- `api/` package has zero `homeassistant.*` imports (library-shim
  architecture)
- All files require SPDX headers, docstrings, and type annotations
- Run pre-commit checks via `prek` (project-local alias) or
  equivalently `uv run pre-commit run --all-files`

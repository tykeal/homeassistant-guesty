<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Automation Actions

**Branch**: `006-automation-actions` | **Date**: 2025-07-26
**Spec**: `specs/006-automation-actions/spec.md`
**Input**: Feature specification from
`/specs/006-automation-actions/spec.md`

## Summary

Implement five write-operation action services that enable Home
Assistant automations to push data back to Guesty. Actions cover
adding reservation notes (SD-001), setting listing status
(SD-002), creating operational tasks (SD-003), managing calendar
availability (SD-004), and updating reservation custom fields
(SD-005). Each action is registered as a domain-level service
under the `guesty` domain using
`hass.services.async_register()`, callable from automations,
scripts, and the developer tools interface.

The implementation extends the established library-shim
architecture: a `GuestyActionsClient` is added to the
HA-independent `api/` sub-package (zero `homeassistant.*`
imports) containing input validation, write-operation methods,
and response models. An HA-side `actions.py` module bridges the
API client to Home Assistant's service infrastructure, translating
exceptions into `ServiceValidationError` for clear automation
feedback. Each action returns a structured `ActionResult` with
success indicator, target identifier, and error details.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant >=2026.4.0, httpx >=0.28
**Storage**: N/A — stateless write operations; no persistent
storage beyond existing Feature 001 token/config entry storage
**Testing**: pytest with pytest-homeassistant-custom-component,
respx (httpx mocking)
**Target Platform**: Home Assistant custom component (HACS)
**Project Type**: HA custom integration with extractable API
library
**Performance Goals**: Action completion in <10s from service
call to Guesty acceptance (SC-001, SC-002); non-blocking async
operations; zero measurable overhead on HA event loop (SC-007)
**Constraints**: API rate limits 15/s, 120/min, 5000/hr;
Guesty eventual consistency means local sensors update on next
poll; all I/O async; `api/` sub-package must have zero HA
imports; existing backoff/retry from Feature 001 handles rate
limits
**Scale/Scope**: 5 action services, ~3 new source files, ~3
new test modules; extends existing `api/` package with actions
client

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after
Phase 1 design.*

- **I. Code Quality & Testing**: ✅ PASS — TDD mandated
  within each phase; 100% docstring coverage; full type
  annotations on all public APIs; mypy and ruff enforced.
- **II. API Client Design**: ✅ PASS —
  `GuestyActionsClient` in `api/actions.py` with zero HA
  imports; reuses existing `GuestyApiClient` for authenticated
  requests; rate limit handling and backoff inherited from
  Feature 001.
- **III. Atomic Commit Discipline**: ✅ PASS — Each phase
  broken into atomic commits; task updates separate from
  code changes.
- **IV. Licensing & Attribution**: ✅ PASS — All new files
  get SPDX headers; REUSE.toml covers JSON assets.
- **V. Pre-Commit Integrity**: ✅ PASS — All hooks run;
  no bypass (`--no-verify` prohibited).
- **VI. Agent Co-Authorship & DCO**: ✅ PASS —
  `git commit -s` with `Co-authored-by` trailer on all
  AI-assisted commits.
- **VII. UX Consistency**: ✅ PASS — Domain-level services
  follow standard HA service call patterns; action parameters
  use explicit IDs matching Guesty identifiers; error messages
  are clear and actionable via `ServiceValidationError`.
- **VIII. Performance Requirements**: ✅ PASS — Full async
  via `GuestyActionsClient`; no event loop blocking; rate
  limit awareness inherited from existing API client; all
  write operations complete within 10 seconds.
- **IX. Phased Development**: ✅ PASS — 4 phases; unit
  TDD in each; integration tests in Phase 4.
- **X. Security & Credentials**: ✅ PASS — No guest PII
  or sensitive data in logs; tokens managed by existing
  infrastructure; input validation before all API calls;
  data validated against schemas before transmission.

**Gate result**: ✅ ALL PASS — proceed to Phase 0.

**Post-Phase 1 re-check**: ✅ ALL PASS — design confirmed
compliant. Actions client maintains zero HA imports. Domain
services use standard HA service call patterns. Input
validation prevents malformed payloads reaching Guesty. No
constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/006-automation-actions/
├── plan.md              # This file
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: entity/data model
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/           # Phase 1: API contracts
│   └── guesty-actions-api.md
└── tasks.md             # Generated later by /tasks
```

### Source Code (repository root)

```text
custom_components/guesty/
├── api/                          # HA-independent API layer
│   ├── __init__.py               # (existing — updated exports)
│   ├── auth.py                   # (existing — unchanged)
│   ├── client.py                 # (existing — unchanged)
│   ├── const.py                  # (modify) add action endpoints
│   ├── exceptions.py             # (modify) add GuestyActionError
│   ├── messaging.py              # (existing — unchanged)
│   ├── models.py                 # (modify) add action models
│   └── actions.py                # (new) GuestyActionsClient
├── __init__.py                   # (modify) register services
├── actions.py                    # (new) HA service handlers
├── config_flow.py                # (existing — unchanged)
├── const.py                      # (existing — unchanged)
├── coordinator.py                # (existing — unchanged)
├── entity.py                     # (existing — unchanged)
├── manifest.json                 # (existing — unchanged)
├── notify.py                     # (existing — unchanged)
├── sensor.py                     # (existing — unchanged)
├── services.yaml                 # (modify) add action services
├── strings.json                  # (modify) add action strings
└── translations/
    └── en.json                   # (modify) add action strings

tests/
├── conftest.py                   # (modify) add action fixtures
├── api/
│   ├── __init__.py               # (existing)
│   ├── test_auth.py              # (existing — unchanged)
│   ├── test_client.py            # (existing — unchanged)
│   ├── test_exceptions.py        # (existing — unchanged)
│   ├── test_messaging.py         # (existing — unchanged)
│   ├── test_models.py            # (existing — extend)
│   └── test_actions.py           # (new) actions client tests
├── test_actions.py               # (new) HA service handler tests
├── test_config_flow.py           # (existing — unchanged)
├── test_coordinator.py           # (existing — unchanged)
├── test_init.py                  # (existing — extend)
├── test_notify.py                # (existing — unchanged)
├── test_sensor.py                # (existing — unchanged)
└── test_token_persistence.py     # (existing — unchanged)
```

**Structure Decision**: Extends the existing Home Assistant custom
component layout. The actions client lives in `api/actions.py`
(zero HA imports) following the `messaging.py` pattern. HA-side
service registration and exception translation lives in
`actions.py` at the integration root. Domain-level services are
registered via `hass.services.async_register()` in `__init__.py`
rather than entity-specific services, since actions target Guesty
resources by explicit ID rather than HA entities.

## Phase Overview

### Phase 1: Actions API Client (`api/` package)

Build the library-extractable write-operation client layer with
zero HA dependencies, extending the existing `api/` package.

**Deliverables**:

- `api/const.py` — Extended with action endpoint paths:
  - `RESERVATIONS_ENDPOINT` (reuse existing)
  - `LISTINGS_ENDPOINT` (reuse existing)
  - `TASKS_ENDPOINT = "/tasks-open-api/tasks"`
  - `CALENDAR_ENDPOINT = "/availability-pricing/api/calendar/listings/{listing_id}"`
  - Validation constants: `MAX_NOTE_LENGTH = 5000`,
    `MAX_TASK_TITLE_LENGTH = 255`,
    `MAX_DESCRIPTION_LENGTH = 5000`,
    `MAX_CUSTOM_FIELD_LENGTH = 5000`,
    `VALID_LISTING_STATUSES = frozenset({"active", "inactive"})`,
    `VALID_CALENDAR_OPS = frozenset({"block", "unblock"})`
- `api/models.py` — Extended with action DTOs:
  - `ActionResult(frozen)`: `success: bool`,
    `target_id: str`, `error: str | None = None`,
    `details: MappingProxyType[str, str] | None = None`
  - Each with `__post_init__` validation
- `api/exceptions.py` — Extended with `GuestyActionError`:
  - Inherits `GuestyApiError`; adds `target_id: str | None`,
    `action_type: str | None` for debugging context
- `api/actions.py` — `GuestyActionsClient` class:
  - Receives `GuestyApiClient` via dependency injection
  - `async add_reservation_note(reservation_id, note_text)`
    → `ActionResult`
  - `async set_listing_status(listing_id, status)`
    → `ActionResult`
  - `async create_task(listing_id, task_title, *,
    description=None, assignee=None)` → `ActionResult`
  - `async set_calendar_availability(listing_id,
    start_date, end_date, operation)` → `ActionResult`
  - `async update_reservation_custom_field(reservation_id,
    custom_field_id, value)` → `ActionResult`
  - Each method validates inputs before API call
  - All methods return `ActionResult` on success or raise
    `GuestyActionError` on failure
- `api/__init__.py` — Updated exports for action types
- Unit tests for all new code (TDD: tests written first)

**Key design decisions**:

- `GuestyActionsClient` follows the `GuestyMessagingClient`
  pattern: zero HA imports, dependency injection of
  `GuestyApiClient`, input validation before API calls
- Each method validates locally (format, length, allowed
  values) before making the API request
- Methods return `ActionResult` rather than raw API
  responses, consistent with `MessageDeliveryResult` pattern
- Calendar availability uses
  `PUT /availability-pricing/api/calendar/listings/{id}`
  with date objects; reservation notes use
  `PUT /reservations/{id}`; listing status uses
  `PUT /listings/{id}`; tasks use `POST /tasks-open-api/tasks`
- Custom field updates use
  `PUT /reservations/{id}` with custom fields payload
- `GuestyActionError` includes `target_id` and
  `action_type` for clear error context in logs

### Phase 2: HA Service Registration

Wire the actions client into Home Assistant's service
infrastructure.

**Deliverables**:

- `actions.py` (integration root) — Service handler module:
  - `async async_setup_actions(hass, entry)` — Registers
    all five services with `hass.services.async_register()`
  - `async async_unload_actions(hass, entry)` — Removes
    service registrations on unload
  - Individual handler functions for each service:
    - `async _handle_add_reservation_note(call)`
    - `async _handle_set_listing_status(call)`
    - `async _handle_create_task(call)`
    - `async _handle_set_calendar_availability(call)`
    - `async _handle_update_custom_field(call)`
  - Each handler extracts parameters from `ServiceCall`,
    delegates to `GuestyActionsClient`, translates
    exceptions to `ServiceValidationError`
  - Returns `ActionResult` as service response data
    (FR-022)
- `services.yaml` — Extended with five service definitions:
  - `add_reservation_note`, `set_listing_status`,
    `create_task`, `set_calendar_availability`,
    `update_reservation_custom_field`
  - Each with required/optional fields and descriptions
- `__init__.py` — Updated to:
  - Create `GuestyActionsClient` in `async_setup_entry`
  - Store in `hass.data[DOMAIN][entry_id]`
  - Call `async_setup_actions()` during setup
  - Call `async_unload_actions()` during unload
- `strings.json` / `translations/en.json` — Updated with
  service action translation keys
- Unit tests for service registration and handlers (TDD)

**Key design decisions**:

- Domain-level services via `hass.services.async_register()`
  rather than entity-specific services, since actions target
  Guesty resources by explicit ID
- Service handlers return response data for automation
  branching (`supports_response=SupportsResponse.OPTIONAL`)
- Exception translation follows `notify.py` pattern:
  `GuestyActionError` → `ServiceValidationError`,
  `GuestyApiError` → `HomeAssistantError`
- Voluptuous schemas validate service call parameters
  before handler execution

### Phase 3: P2/P3 Action Features

Implement the remaining priority actions and comprehensive
validation.

**Deliverables**:

- Integration tests for P1 actions (add reservation note,
  set listing status) — end-to-end through HA service call
  → actions client → mocked Guesty API
- Integration tests for P2 actions (create task, set
  calendar availability) — same pattern
- Integration tests for P3 action (update custom field)
- Edge case tests:
  - Deleted reservation/listing returns "not found"
  - Rate limit → retry → success path
  - Rate limit → max retries → clear failure
  - Concurrent action calls to same target
  - Special characters and unicode in note/description
  - Maximum length boundary validation
  - Calendar block conflicting with reservation
  - Invalid date range (end before start)
  - Token expiry during action → auto-refresh → retry

### Phase 4: Integration Testing & Validation

Comprehensive test coverage and cross-phase validation.

**Deliverables**:

- Full integration tests: HA service call → actions client
  → mocked Guesty API → ActionResult response
- Automation compatibility tests: verify actions work with
  HA automations, scripts, and developer tools (FR-017)
- Service response tests: verify ActionResult structure
  enables automation branching (FR-022)
- Rate limit handling tests: 429 → retry → success;
  429 → max retries → failure (FR-014, SC-004)
- Security tests: verify no sensitive data in logs at any
  level (FR-020, SC-008)
- Error message quality tests: verify actionable messages
  for all failure modes (FR-013, SC-005, SC-006)
- Success criteria validation:
  - SC-001: Note addition <10s
  - SC-002: Status change <10s
  - SC-003: Zero additional config beyond service call
  - SC-004: 100% rate limit handling
  - SC-005: Invalid calls → clear errors <2s
  - SC-006: Failures include target ID and reason
  - SC-007: No HA responsiveness degradation
  - SC-008: No sensitive data in logs
  - SC-009: All testable without live Guesty
  - SC-010: Calendar blocks prevent conflicts

## Complexity Tracking

> No constitution violations identified. The automation
> actions feature adds one new API module (`api/actions.py`),
> one new HA module (`actions.py`), and extends existing files
> (`models.py`, `exceptions.py`, `const.py`, `services.yaml`,
> `__init__.py`). All follow established patterns from
> Features 001 and 005. The `GuestyActionsClient` follows the
> `GuestyMessagingClient` dependency injection pattern. Domain
> services use explicit Guesty IDs rather than HA entity
> targeting, which is the correct pattern for write operations
> that do not map to local entities.

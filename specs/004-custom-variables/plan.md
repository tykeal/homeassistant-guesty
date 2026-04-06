<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Custom Variables

**Branch**: `004-custom-variables` | **Date**: 2025-07-27
**Spec**: `specs/004-custom-variables/spec.md`
**Input**: Feature specification from
`/specs/004-custom-variables/spec.md`

## Summary

Implement a custom field write service for the Guesty Home
Assistant integration that enables property managers to push
data from Home Assistant into Guesty custom fields on listings
and reservations. The service registers as
`guesty.set_custom_field`, accepting a target type (listing or
reservation), target identifier, field identifier, and value.
It returns a structured response on success and raises clear
errors on failure.

A `CustomFieldsDefinitionCoordinator` periodically fetches
account-level custom field definitions from the Guesty
`/custom-fields`
endpoint, caching field metadata (name, identifier, value type,
applicability) for discovery and local type validation. The
`api/` sub-package gains a `custom_fields.py` module containing
`GuestyCustomFieldsClient` with zero HA imports to preserve
library extractability. The client handles definition fetching,
value writing to both listing and reservation (v3) endpoints,
and input validation.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant >=2026.4.0, httpx >=0.28
**Storage**: HA config entry storage (via `hass.config_entries`)
**Testing**: pytest (via `uv run pytest tests/`)
**Target Platform**: Home Assistant custom component (HACS)
**Project Type**: HA integration (custom component)
**Performance Goals**: Custom field updates in <10 seconds from
service call to Guesty acceptance (SC-001); zero measurable HA
event loop degradation (SC-007)
**Constraints**: All I/O async; no blocking calls on event loop;
`api/` sub-package must have zero HA imports; Guesty API rate
limits respected (existing backoff/retry from Feature 001);
reservation endpoints use v3 path (`/reservations-v3/`) per
Guesty migration timeline (FR-018)
**Scale/Scope**: One new HA service; one new coordinator for
field definitions; one new API client module; single custom
field per service call; concurrent updates are independent
(last-write-wins at Guesty)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after
Phase 1 design.*

- **I. Code Quality & Testing**: ✅ PASS — TDD mandated
  within each phase; 100% docstring coverage; full type
  annotations on all public APIs; mypy and ruff enforced.
- **II. API Client Design**: ✅ PASS —
  `GuestyCustomFieldsClient` in `api/custom_fields.py` with
  zero HA imports; reuses existing `GuestyApiClient`
  infrastructure; rate limit handling and backoff inherited
  from Feature 001.
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
- **VII. UX Consistency**: ✅ PASS — Standard HA service
  call pattern; response-capable service with structured
  success/error responses; clear validation messages.
- **VIII. Performance Requirements**: ✅ PASS — Full async
  via `GuestyCustomFieldsClient`; configurable definition
  refresh interval; no event loop blocking.
- **IX. Phased Development**: ✅ PASS — 4 phases; unit TDD
  in each; integration tests in Phase 4.
- **X. Security & Credentials**: ✅ PASS — No custom field
  values in logs (may contain access codes, PII per
  FR-014); tokens managed by existing infrastructure;
  input validation before API calls.

**Gate result**: ✅ ALL PASS — proceed to Phase 0.

**Post-Phase 1 re-check**: ✅ ALL PASS — design confirmed
compliant. Custom fields client maintains zero HA imports.
Service uses standard HA service registration patterns.
Definition coordinator reuses existing coordinator patterns.
No constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/004-custom-variables/
├── plan.md              # This file
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: entity/data model
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/           # Phase 1: API contracts
│   └── guesty-custom-fields-api.md
└── tasks.md             # Generated later by /tasks
```

### Source Code (repository root)

```text
custom_components/guesty/
├── api/                          # HA-independent API layer
│   ├── __init__.py               # (existing — updated exports)
│   ├── auth.py                   # (existing — unchanged)
│   ├── client.py                 # (existing — unchanged)
│   ├── const.py                  # (modify) add custom field consts
│   ├── custom_fields.py          # (new) GuestyCustomFieldsClient
│   ├── exceptions.py             # (modify) add custom field error
│   ├── messaging.py              # (existing — unchanged)
│   └── models.py                 # (modify) add custom field models
├── __init__.py                   # (modify) add coordinator + svc
├── config_flow.py                # (existing — unchanged)
├── const.py                      # (modify) add CF constants
├── coordinator.py                # (modify) add CF definitions coord
├── entity.py                     # (existing — unchanged)
├── manifest.json                 # (existing — unchanged)
├── notify.py                     # (existing — unchanged)
├── sensor.py                     # (existing — unchanged)
├── services.yaml                 # (modify) add set_custom_field
├── strings.json                  # (modify) add CF service strings
└── translations/
    └── en.json                   # (modify) add CF translations

tests/
├── conftest.py                   # (modify) add CF fixtures
├── api/
│   ├── __init__.py               # (existing)
│   ├── test_auth.py              # (existing — unchanged)
│   ├── test_client.py            # (existing — unchanged)
│   ├── test_custom_fields.py     # (new) custom fields client tests
│   ├── test_exceptions.py        # (existing — extend)
│   ├── test_messaging.py         # (existing — unchanged)
│   └── test_models.py            # (existing — extend)
├── test_config_flow.py           # (existing — unchanged)
├── test_coordinator.py           # (existing — extend)
├── test_custom_field_service.py  # (new) service handler tests
├── test_init.py                  # (existing — extend)
├── test_notify.py                # (existing — unchanged)
├── test_sensor.py                # (existing — unchanged)
└── test_token_persistence.py     # (existing — unchanged)
```

**Structure Decision**: Extends the existing HA custom component
layout. Custom fields client logic is added as a new
`api/custom_fields.py` module (zero HA imports) following the
same pattern as `api/messaging.py`. Custom field data models
are added to `api/models.py` alongside existing listing and
reservation models. The `CustomFieldsDefinitionCoordinator`
lives in `coordinator.py` alongside the existing
`ListingsCoordinator`. The HA service handler is registered
in `__init__.py` during `async_setup_entry`.

## Phase Overview

### Phase 1: Custom Fields API Client (`api/` package)

Build the library-extractable custom fields client layer with
zero HA dependencies, extending the existing `api/` package.

**Deliverables**:

- `api/const.py` — Extended with custom field endpoint paths:
  - `CUSTOM_FIELDS_ENDPOINT: str` —
    `/custom-fields` (definitions)
  - `LISTING_CUSTOM_FIELDS_PATH: str` —
    `/listings/{listing_id}/custom-fields` (listing write)
  - `RESERVATION_CUSTOM_FIELDS_PATH: str` —
    `/reservations-v3/{reservation_id}/custom-fields`
    (reservation write, v3 per FR-018)
  - `CUSTOM_FIELD_TYPES: frozenset[str]` —
    `{"text", "number", "boolean"}` (normalized integration
    field types; Guesty API `type: "string"` maps to `text`
    on ingest)
  - `CUSTOM_FIELD_TARGETS: frozenset[str]` —
    `{"listing", "reservation"}`
- `api/models.py` — Extended with custom field DTOs:
  - `GuestyCustomFieldDefinition(frozen=True)` — `field_id`,
    `name`, `field_type` (normalized text/number/boolean
    value), `applicable_to` (frozenset of target types).
    Factory:
    `from_api_dict(data) -> GuestyCustomFieldDefinition | None`
  - `GuestyCustomFieldUpdate(frozen=True)` — `field_id`,
    `value` (str | int | float | bool). Represents a single
    field update request.
  - `GuestyCustomFieldResult(frozen=True)` — `success: bool`,
    `target_type: str`, `target_id: str`, `field_id: str`,
    `error_details: str | None`. Returned from write
    operations.
- `api/exceptions.py` — Extended with
  `GuestyCustomFieldError(GuestyApiError)`: custom-field-
  specific error with `target_type`, `target_id`, and
  `field_id` context attributes.
- `api/custom_fields.py` — `GuestyCustomFieldsClient`:
  - `get_definitions() -> list[GuestyCustomFieldDefinition]`
    — Fetches all account-level custom field definitions from
    `GET /custom-fields`
  - `set_field(target_type, target_id, field_id, value)
    -> GuestyCustomFieldResult` — Writes a single custom
    field value via `PUT /listings/{id}/custom-fields` or
    `PUT /reservations-v3/{id}/custom-fields`
  - `validate_value(value, field_type) -> None` — Validates
    value type against definition; raises ValueError on
    mismatch
  - Receives `GuestyApiClient` via dependency injection
  - Zero HA imports
- `api/__init__.py` — Updated exports for custom field types
- Unit tests for all new code (TDD: tests written first)

**Key design decisions**:

- `GuestyCustomFieldsClient` receives `GuestyApiClient` via
  dependency injection (same DI pattern as
  `GuestyMessagingClient`)
- `set_field()` builds the PUT body as
  `[{"fieldId": field_id, "value": value}]` per Guesty API
- Reservation writes use `/reservations-v3/` path per Guesty
  migration timeline (FR-018)
- `get_definitions()` returns flat list; HA-side coordinator
  handles caching and filtering by applicability
- Validation is local for known types; unknown types or
  multi-type fields are passed through to Guesty for
  server-side validation (edge case from spec)
- All DTOs are frozen dataclasses consistent with existing
  patterns (`CachedToken`, `GuestyListing`, `Conversation`)

### Phase 2: Custom Fields Coordinator & Service

Wire the custom fields client into Home Assistant as a
coordinator for definitions and a registered service for
writes.

**Deliverables**:

- `coordinator.py` — `CustomFieldsDefinitionCoordinator`
  class extending
  `DataUpdateCoordinator[list[GuestyCustomFieldDefinition]]`:
  - Polls `GuestyCustomFieldsClient.get_definitions()`
  - Default refresh interval matches listing coordinator
    (configurable via options flow)
  - Provides `get_field(field_id)` lookup method
  - Provides `get_fields_for_target(target_type)` filtering
- `const.py` — Updated with custom-field-specific constants:
  - `CONF_CF_SCAN_INTERVAL: str`
  - `DEFAULT_CF_SCAN_INTERVAL: int` (15 minutes)
  - `SERVICE_SET_CUSTOM_FIELD: str`
- `__init__.py` — Updated `async_setup_entry`:
  - Create `GuestyCustomFieldsClient` with `api_client`
  - Create `CustomFieldsDefinitionCoordinator`
  - Perform initial definition refresh
  - Register `guesty.set_custom_field` service with
    voluptuous schema and
    `supports_response=SupportsResponse.OPTIONAL`
  - Service handler: validates inputs, checks field exists
    in definitions, validates type, delegates to
    `GuestyCustomFieldsClient.set_field()`, returns
    structured response dict
  - Store coordinator and client in `hass.data`
  - Update `async_unload_entry` for cleanup
- `services.yaml` — Extended with `set_custom_field`
  definition: target_type, target_id, field_id, value fields
- `strings.json` / `translations/en.json` — Updated with
  service translation keys
- Unit tests for coordinator, service handler, options

### Phase 3: Discovery & Validation Features

Implement field definition discovery exposure and enhanced
validation for automation integration.

**Deliverables**:

- Coordinator exposes definitions for discovery (FR-010):
  field name, identifier, type, and applicability available
  to automations and UI
- Enhanced validation integration tests:
  - Field ID not in definitions → clear error listing
    available fields for that target type
  - Type mismatch → clear validation error before API call
  - Missing parameters → clear error identifying missing
    parameter
- Automation compatibility tests:
  - Service call from automation trigger
  - Service call with HA template-rendered values
  - Response data consumed by subsequent automation actions
  - Error handling in automation context

### Phase 4: Integration Testing & Validation

Comprehensive test coverage and cross-phase validation.

**Deliverables**:

- Integration tests: full data flow from service call
  through custom fields client to mocked Guesty response
- Rate limit handling tests: 429 response → retry →
  success path; 429 → max retries → failure path
- Transient failure tests: network errors, temporary Guesty
  outages with retry and backoff (FR-012)
- Concurrent update tests: multiple simultaneous service
  calls (FR-011, edge case: last-write-wins)
- Edge case tests:
  - Multi-type fields passed through to Guesty
  - Unicode and special characters in values
  - Very long string values
  - Custom field definitions change during update
  - No custom fields defined for target type
  - Expired/missing auth triggers transparent refresh
  - Past reservation field update (Guesty decides)
- Security tests: no custom field values in logs at any
  level (FR-014, SC-008)
- Success criteria validation:
  - SC-001: Update in <10 seconds
  - SC-002: 100% valid calls succeed
  - SC-003: Zero additional automation config needed
  - SC-004: 100% rate limits handled
  - SC-005: Invalid calls → clear error in <2 seconds
  - SC-006: Definitions discoverable within 2 refreshes
  - SC-007: No event loop degradation
  - SC-009: All scenarios testable without live Guesty

## Complexity Tracking

> No constitution violations identified. The custom fields
> feature adds one new API module (`api/custom_fields.py`),
> new models to `api/models.py`, a new coordinator to
> `coordinator.py`, and a new service registration in
> `__init__.py`. All follow established patterns from
> Features 001 and 005 (messaging). The
> `GuestyCustomFieldsClient` reuses the existing
> `GuestyApiClient` infrastructure rather than implementing
> separate HTTP logic. The service registration follows
> standard HA patterns with response data support.

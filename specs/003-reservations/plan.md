<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Reservations

**Branch**: `003-reservations` | **Date**: 2025-07-25
**Spec**: `specs/003-reservations/spec.md`
**Input**: Feature specification from
`/specs/003-reservations/spec.md`

## Summary

Fetch reservation and booking data from the Guesty Open API v1
`/reservations` endpoint and expose it on existing listing
devices in Home Assistant. Each listing device gains a
reservation status sensor reflecting the current occupancy
state (no_reservation, awaiting_checkin, checked_in,
checked_out, canceled) with rich attributes for guest
information, check-in/check-out dates, confirmation code,
booking source, and upcoming reservation summaries. Financial
data (total price, balance due, currency) is exposed as
diagnostic-category sensors.

A dedicated `ReservationsCoordinator` provides periodic
polling (default 15 minutes, configurable via options flow)
with date-range filtering (default 30 days past / 365 days
future) and graceful degradation on API errors. The `api/`
sub-package gains a paginated `get_reservations()` method
with zero HA imports to preserve library extractability.
Reservation data models (`GuestyReservation`, `GuestyGuest`,
`GuestyMoney`) live in `api/models.py` as frozen dataclasses.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant >=2026.4.0, httpx >=0.28
**Storage**: HA config entry storage (via `hass.config_entries`)
**Testing**: pytest (via `uv run pytest tests/`)
**Target Platform**: Home Assistant custom component (HACS)
**Project Type**: HA integration (custom component)
**Performance Goals**: Handle 100+ listings with 50+
reservations each without timeout; no measurable HA event loop
degradation (SC-004, SC-008)
**Constraints**: All I/O async; no blocking calls on event
loop; `api/` sub-package must have zero HA imports; Guesty API
rate limits respected (existing backoff/retry from Feature 001);
reservation sensors attach to existing listing devices from
Feature 002 (FR-004)
**Scale/Scope**: Hundreds of reservations across many listings;
4 new sensor descriptions per listing device; separate
coordinator from listings; configurable date range window

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after
Phase 1 design.*

- **I. Code Quality & Testing**: ✅ PASS — TDD mandated
  within each phase; 100% docstring coverage; full type
  annotations on all public APIs; mypy and ruff enforced.
- **II. API Client Design**: ✅ PASS — `get_reservations()`
  in `api/client.py` with zero HA imports; reuses existing
  `GuestyApiClient` infrastructure; pagination, rate limit
  handling, and backoff inherited from Feature 001.
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
- **VII. UX Consistency**: ✅ PASS — Reservation sensors
  attach to existing listing devices; entity naming follows
  HA conventions; sensor descriptions use standard patterns
  from Feature 002.
- **VIII. Performance Requirements**: ✅ PASS — Full async
  via `get_reservations()`; configurable polling interval
  with 5-minute minimum (FR-013); date-range filtering
  limits data volume; coordinator prevents concurrent
  refreshes.
- **IX. Phased Development**: ✅ PASS — 4 phases; unit
  TDD in each; integration tests in Phase 4.
- **X. Security & Credentials**: ✅ PASS — No guest PII
  in logs (phone, email sanitized); tokens managed by
  existing infrastructure; input validation before API
  calls.

**Gate result**: ✅ ALL PASS — proceed to Phase 0.

**Post-Phase 1 re-check**: ✅ ALL PASS — design confirmed
compliant. Reservation models maintain zero HA imports.
Reservation sensors use standard HA entity description
patterns. Financial sensors use diagnostic category.
No constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/003-reservations/
├── plan.md              # This file
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: entity/data model
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/           # Phase 1: API contracts
│   └── guesty-reservations-api.md
└── tasks.md             # Generated later by /tasks
```

### Source Code (repository root)

```text
custom_components/guesty/
├── api/                          # HA-independent API layer
│   ├── __init__.py               # (existing — updated exports)
│   ├── auth.py                   # (existing — unchanged)
│   ├── client.py                 # (modify) add get_reservations()
│   ├── const.py                  # (modify) add reservation consts
│   ├── exceptions.py             # (existing — unchanged)
│   ├── messaging.py              # (existing — unchanged)
│   └── models.py                 # (modify) add reservation models
├── __init__.py                   # (modify) add ReservationsCoord
├── config_flow.py                # (modify) add reservation options
├── const.py                      # (modify) add reservation consts
├── coordinator.py                # (modify) add ReservationsCoord
├── entity.py                     # (existing — unchanged)
├── manifest.json                 # (existing — unchanged)
├── notify.py                     # (existing — unchanged)
├── sensor.py                     # (modify) add reservation sensors
├── strings.json                  # (modify) add reservation keys
└── translations/
    └── en.json                   # (modify) add reservation strings

tests/
├── conftest.py                   # (modify) add reservation fixtures
├── api/
│   ├── __init__.py               # (existing)
│   ├── test_auth.py              # (existing — unchanged)
│   ├── test_client.py            # (existing — extend)
│   ├── test_exceptions.py        # (existing — unchanged)
│   ├── test_messaging.py         # (existing — unchanged)
│   └── test_models.py            # (existing — extend)
├── test_config_flow.py           # (existing — extend)
├── test_coordinator.py           # (existing — extend)
├── test_init.py                  # (existing — extend)
├── test_notify.py                # (existing — unchanged)
├── test_reservation_sensor.py    # (new) reservation sensor tests
├── test_sensor.py                # (existing — unchanged)
└── test_token_persistence.py     # (existing — unchanged)
```

**Structure Decision**: Extends the existing HA custom component
layout. Reservation models are added to `api/models.py` alongside
listing models. The `ReservationsCoordinator` lives in the
existing `coordinator.py` module. Reservation sensor descriptions
and entities are added to `sensor.py`. This keeps the module
count low while maintaining clear separation of concerns.

## Phase Overview

### Phase 1: Reservation API Client (`api/` package)

Build the library-extractable reservation data retrieval layer
with zero HA dependencies, extending the existing `api/` package.

**Deliverables**:

- `api/const.py` — Extended with reservation endpoint path,
  page size, query fields tuple, actionable statuses frozenset,
  and default date range constants
- `api/models.py` — Extended with three new frozen dataclasses:
  `GuestyGuest` (full_name, phone, email, guest_id),
  `GuestyMoney` (total_paid, balance_due, currency),
  `GuestyReservation` (id, listing_id, status, check_in,
  check_out, confirmation_code, localized dates, planned
  arrival/departure, nights_count, guests_count, source,
  note, guest, money). Each with `from_api_dict()` factory.
  `GuestyReservationsResponse` pagination wrapper.
- `api/client.py` — `get_reservations()` method on
  `GuestyApiClient`: paginated fetch with date-range and
  status filters, field selection, `sort=_id`, returns
  `list[GuestyReservation]`
- `api/__init__.py` — Updated exports for reservation types
- Unit tests for all new code (TDD: tests written first)

**Key design decisions**:

- `get_reservations()` accepts `past_days`, `future_days`,
  and `statuses` parameters for flexible querying
- Date boundaries computed at call time using `datetime.now(UTC)`
- Filters are JSON-encoded in the `filters` query parameter
  per Guesty API convention
- Reservations missing required fields (`_id`, `listingId`,
  `status`, `checkIn`, `checkOut`) are skipped with a
  warning log, consistent with listing parsing behavior
- All DTOs are frozen dataclasses consistent with existing
  `CachedToken` and `GuestyListing` patterns
- Guest and money objects are optional nested dataclasses
  to handle missing data gracefully (FR-019)

### Phase 2: Reservations Coordinator

Create the `ReservationsCoordinator` to poll reservation data
and wire it into the integration lifecycle.

**Deliverables**:

- `coordinator.py` — `ReservationsCoordinator` class extending
  `DataUpdateCoordinator[dict[str, list[GuestyReservation]]]`:
  - References `ListingsCoordinator` for known listing IDs
  - Groups fetched reservations by listing ID
  - Filters out reservations for unknown listings (FR-017)
  - Sorts reservations per listing by check-in date
  - Configurable polling interval (default 15 min)
- `const.py` — Updated with reservation-specific option
  constants (`CONF_RESERVATION_SCAN_INTERVAL`,
  `CONF_PAST_DAYS`, `CONF_FUTURE_DAYS`, defaults)
- `__init__.py` — Updated `async_setup_entry` to create
  `ReservationsCoordinator` after `ListingsCoordinator`;
  store in `hass.data`; perform initial refresh; update
  `async_unload_entry` for cleanup; options listener
  updates reservation coordinator interval
- `config_flow.py` — Options flow extended with reservation
  polling interval, past days, and future days settings
- Unit tests for coordinator and updated init/config_flow

### Phase 3: Reservation Sensors

Expose reservation data as sensor entities on listing devices.

**Deliverables**:

- `sensor.py` — Extended with reservation sensor entity
  descriptions and a `GuestyReservationSensor` class:
  - `reservation_status` sensor (no category): state derived
    from priority selection (FR-006); rich attributes for
    guest info, dates, confirmation code, source, upcoming
    reservations
  - `reservation_total` sensor (diagnostic): total paid
  - `reservation_balance` sensor (diagnostic): balance due
  - `reservation_currency` sensor (diagnostic): currency
  - Sensors attach to existing listing devices via
    `device_info` using listing ID
  - `async_setup_entry` creates sensors for each listing
    tracked by the reservations coordinator
- `strings.json` / `translations/en.json` — Updated with
  translation keys for all new reservation sensors
- Unit tests for all sensor entities (TDD: tests first)

### Phase 4: Integration Testing & Validation

Comprehensive test coverage and cross-phase validation.

**Deliverables**:

- Integration tests: full data flow from API mock through
  coordinator to sensor state and attributes
- State transition tests: verify sensor state changes
  fire HA state-change events for automations (FR-015)
- Edge case tests:
  - No reservations for a listing (FR-016)
  - Unknown listing ID in reservation (FR-017)
  - Same-day turnover (FR-018)
  - Missing optional fields (FR-019)
  - Unknown reservation status (FR-025)
  - API failure with data retention (FR-014)
  - Listing deleted while reservations exist
- Performance tests: verify handling of 100+ listings with
  50+ reservations each (SC-004, SC-008)
- Cleanup tests: verify entity and coordinator cleanup on
  integration removal (FR-023)
- Success criteria validation:
  - SC-001: First load under 30 seconds
  - SC-002: Changes reflected within polling interval
  - SC-003: 100% state transitions fire events
  - SC-004: 50+ reservations without delay
  - SC-006: Stale data retained during 1-hour outage
  - SC-009: Zero orphaned entities

## Complexity Tracking

> No constitution violations identified. The reservation
> feature adds new models to `api/models.py`, a new method
> to `api/client.py`, a new coordinator to `coordinator.py`,
> and new sensor descriptions to `sensor.py`. All follow
> established patterns from Features 001 and 002. The
> `ReservationsCoordinator` references `ListingsCoordinator`
> for listing ID validation, which is the only cross-feature
> dependency.

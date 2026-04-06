<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Reservations

**Input**: Design documents from `/specs/003-reservations/`
**Prerequisites**: plan.md (required), spec.md (required for user
stories), research.md, data-model.md, contracts/

**Tests**: TDD is mandatory per project constitution. All test
tasks MUST be completed (Red phase) and verified FAILING before
their corresponding implementation tasks (Green phase). Each phase
becomes an independent PR.

**Organization**: Tasks are grouped into an implementation-phase
breakdown derived from plan.md, but re-organized here for task
sequencing and independently deployable increments. User stories
are mapped to the phase where their functionality is delivered.

## Format: `[ID] [P?] [Story?] Description`

- **\[P]**: Can run in parallel (different files, no deps)
- **\[Story?]**: Optional; used only for tasks in user-story
  phases to indicate which user story (US1–US5)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/guesty/` (HA custom component)
- **API library**: `custom_components/guesty/api/` (zero HA
  imports)
- **Tests**: `tests/` and `tests/api/` (pytest with respx
  mocking)

---

## Phase 1: Foundational — Reservation API Client

**Purpose**: Build the library-extractable reservation data
retrieval layer with zero HA dependencies, extending the existing
`api/` package with reservation models, constants, and a
paginated `get_reservations()` method.

**⚠️ CRITICAL**: No user story work can begin until this phase
is complete. All reservation domain logic lives in `api/`.

### Phase 1 Tests (Red) — Write First, Verify They FAIL

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

<!-- markdownlint-disable MD013 -->

- [x] T001 Write unit tests for `GuestyGuest` frozen dataclass
  (`from_api_dict` with full guest data returns populated
  instance, `None` input returns `None`, empty dict returns
  `None`, partial guest with missing phone/email fields) and
  `GuestyMoney` frozen dataclass (`from_api_dict` with full
  financial data, `None` input returns `None`, empty dict
  returns `None`, partial money with missing fields) in
  `tests/api/test_models.py`
- [x] T002 Write unit tests for `GuestyReservation` frozen
  dataclass (`from_api_dict` with complete API data dict,
  missing `_id` returns `None`, missing `listingId` returns
  `None`, missing `status` returns `None`, unparsable
  `checkIn`/`checkOut` dates return `None`, unknown status
  passed through as-is per FR-025, optional fields default to
  `None`, nested `guest` and `money` objects parsed via their
  `from_api_dict` factories) in `tests/api/test_models.py`
- [x] T003 Write unit tests for `GuestyReservationsResponse`
  (`from_api_dict` parses valid results array via
  `GuestyReservation.from_api_dict`, filters `None` entries
  for invalid reservations, `count`/`limit`/`skip` fields
  preserved from API response) in `tests/api/test_models.py`
- [x] T004 [P] Write unit tests for
  `GuestyApiClient.get_reservations()`: single-page fetch
  (results count < limit stops pagination), multi-page
  pagination (two full pages + partial third page), empty
  account returns empty list, primary date-range request merged
  with secondary `checked_in`-only request per R2,
  de-duplication by reservation ID, actionable status filtering
  via `$contains`, date range boundaries computed from
  `past_days`/`future_days`, `GuestyAuthError` propagation,
  `GuestyConnectionError` propagation, `GuestyResponseError` on
  malformed JSON, reservations with missing required fields
  skipped with warning using respx-mocked HTTP in
  `tests/api/test_client.py`

<!-- markdownlint-enable MD013 -->

### Phase 1 Implementation (Green) — Make Tests Pass

<!-- markdownlint-disable MD013 -->

- [x] T005 [P] Add reservation constants
  (`RESERVATIONS_ENDPOINT`, `RESERVATIONS_PAGE_SIZE = 100`,
  `RESERVATIONS_FIELDS` tuple with all requested field names
  per contracts/guesty-reservations-api.md,
  `ACTIONABLE_STATUSES` frozenset, `DEFAULT_PAST_DAYS = 30`,
  `DEFAULT_FUTURE_DAYS = 365`) to
  `custom_components/guesty/api/const.py`
- [x] T006 Implement `GuestyGuest` frozen dataclass with fields
  (`full_name`, `phone`, `email`, `guest_id` — all
  `str | None`) and `from_api_dict(data: dict | None)` factory
  returning `None` for `None` or empty input in
  `custom_components/guesty/api/models.py`
- [x] T007 Implement `GuestyMoney` frozen dataclass with fields
  (`total_paid`, `balance_due`, `currency`) and
  `from_api_dict(data: dict | None)` factory returning `None`
  for `None` or empty input in
  `custom_components/guesty/api/models.py`
- [x] T008 Implement `GuestyReservation` frozen dataclass with
  all fields per data-model.md (`id`, `listing_id`, `status`,
  `check_in`, `check_out`, `confirmation_code`,
  `check_in_local`, `check_out_local`, `planned_arrival`,
  `planned_departure`, `nights_count`, `guests_count`,
  `source`, `note`, `guest`, `money`) and
  `from_api_dict(data: dict)` factory that parses ISO 8601
  dates, skips records missing required fields with warning,
  and delegates nested guest/money parsing in
  `custom_components/guesty/api/models.py`
- [x] T009 Implement `GuestyReservationsResponse` frozen
  dataclass with fields (`results`, `count`, `limit`, `skip`)
  and `from_api_dict(data: dict)` factory that parses results
  via `GuestyReservation.from_api_dict` filtering `None`
  entries in `custom_components/guesty/api/models.py`
- [x] T010 Implement `async get_reservations(self, *,
  past_days=DEFAULT_PAST_DAYS,
  future_days=DEFAULT_FUTURE_DAYS,
  statuses: frozenset[str] | None = None)` on
  `GuestyApiClient` with dual-request pattern (primary:
  `checkIn` `$between` date range + `status` `$contains`
  filter; secondary: `checked_in`-only without date filter
  per R2), skip-based pagination, explicit `fields`
  parameter, `sort=_id`, de-duplication by reservation ID,
  using existing `_request()` method in
  `custom_components/guesty/api/client.py`
- [x] T011 [P] Export `GuestyGuest`, `GuestyMoney`,
  `GuestyReservation`, `GuestyReservationsResponse` from
  `custom_components/guesty/api/__init__.py`

<!-- markdownlint-enable MD013 -->

**Checkpoint**: All api/ reservation tests pass. The API client
can fetch, parse, filter, and de-duplicate reservations — all
independently of Home Assistant. Zero HA imports in `api/`.

---

## Phase 2: US1 + US2 — Status Sensor + Automations (P1) 🎯 MVP

**Goal**: Wire the reservation API client into a
`ReservationsCoordinator`, expose a reservation status sensor
on each listing device with rich attributes, and provide
configurable polling via options flow. State-change events
enable automations for check-in/check-out/cancellation.

**Independent Test**: Configure the integration with valid
Guesty credentials and verify each listing device shows a
reservation status sensor indicating the current occupancy
state with check-in/check-out dates, guest info, and
upcoming reservation summaries as attributes.

**Why combined**: US1 (reservation status on listings) and US2
(automation triggers on status changes) are inseparable — the
status sensor IS the mechanism for state-change events that
HA automations listen to.

### Phase 2 Fixtures

- [x] T012 [US1] Add reservation test fixtures to
  `tests/conftest.py`: `make_reservation_dict(**overrides)`
  returning a Guesty API reservation dict with sensible
  defaults, `make_guest_dict(**overrides)`,
  `make_money_dict(**overrides)`, `sample_reservation()`
  returning a valid `GuestyReservation` instance, mock
  `ReservationsCoordinator` fixture with sample data dict

### Phase 2 Tests (Red) — Write First, Verify They FAIL

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

<!-- markdownlint-disable MD013 -->

- [x] T013 [US1] Write tests for `ReservationsCoordinator`:
  `_async_update_data` calls
  `api_client.get_reservations()` and returns
  `dict[str, list[GuestyReservation]]` keyed by `listing_id`,
  groups reservations by listing, filters out reservations for
  unknown listing IDs using `ListingsCoordinator.data` keys
  (FR-017) with warning logged, sorts reservations per listing
  by `check_in` date, `update_interval` matches configured
  `CONF_RESERVATION_SCAN_INTERVAL` from `entry.options`,
  raises `UpdateFailed` on `GuestyApiError` subclasses, empty
  reservation list returns empty dict in
  `tests/test_coordinator.py`
- [x] T014 [P] [US1] Write tests for
  `GuestyReservationSensor`: priority selection
  (`checked_in` > confirmed/`awaiting_checkin` >
  `checked_out` > `canceled` > `no_reservation` per FR-006),
  `native_value` returns correct state string,
  `extra_state_attributes` includes all expected fields
  (`reservation_id`, `check_in`, `check_out`,
  `check_in_local`, `check_out_local`, `planned_arrival`,
  `planned_departure`, `guest_name`, `guest_phone`,
  `guest_email`, `confirmation_code`, `guests_count`,
  `nights_count`, `source`, `upcoming_reservations`),
  `unique_id` format is
  `{entry_unique_id}_{listing_id}_reservation_status`,
  `device_info` attaches to existing listing device,
  sensor with no reservations shows `"no_reservation"`,
  `async_setup_entry` creates one status sensor per listing
  in `tests/test_reservation_sensor.py`
- [x] T015 [P] [US2] Write tests for reservation options flow:
  `async_step_init` presents
  `reservation_scan_interval`/`past_days`/`future_days`
  fields with correct defaults (15/30/365), valid values save
  to `entry.options` and create entry, interval below
  `MIN_RESERVATION_SCAN_INTERVAL` (5) raises validation error,
  `past_days` and `future_days` accept positive integers in
  `tests/test_config_flow.py`
- [x] T016 [P] [US1] Write tests for updated
  `async_setup_entry`: `ReservationsCoordinator` created after
  `ListingsCoordinator` and stored in `hass.data`,
  `async_config_entry_first_refresh` called for reservation
  coordinator, sensor platform forwarded,
  `async_unload_entry` cleans up reservation coordinator,
  options update listener reconfigures reservation coordinator
  interval and date range in `tests/test_init.py`

<!-- markdownlint-enable MD013 -->

### Phase 2 Implementation (Green) — Make Tests Pass

<!-- markdownlint-disable MD013 -->

- [x] T017 [P] [US1] Add `CONF_RESERVATION_SCAN_INTERVAL`,
  `CONF_PAST_DAYS`, `CONF_FUTURE_DAYS`,
  `DEFAULT_RESERVATION_SCAN_INTERVAL = 15`,
  `MIN_RESERVATION_SCAN_INTERVAL = 5` to
  `custom_components/guesty/const.py`
- [x] T018 [US1] Implement
  `ReservationsCoordinator(DataUpdateCoordinator[dict[str, list[GuestyReservation]]])`
  with `__init__` accepting `hass`, `entry`, `api_client`,
  and `listings_coordinator`; `_async_update_data` fetching
  via `api_client.get_reservations(past_days=past_days, future_days=future_days)`,
  grouping by `listing_id`, filtering unknown listing IDs via
  `listings_coordinator.data` keys with warning log per
  FR-017, sorting per-listing by `check_in`, raising
  `UpdateFailed` on `GuestyApiError` subclasses in
  `custom_components/guesty/coordinator.py`
- [x] T019 [US2] Extend `GuestyOptionsFlowHandler` with
  `reservation_scan_interval` (default 15, min 5),
  `past_days` (default 30), `future_days` (default 365)
  fields using `vol.Schema` validation in
  `custom_components/guesty/config_flow.py`
- [x] T020 [US1] Update `async_setup_entry` to create
  `ReservationsCoordinator` after `ListingsCoordinator`,
  call `await coordinator.async_config_entry_first_refresh()`,
  store in `hass.data`; update `async_unload_entry` for
  cleanup; update options listener to reconfigure reservation
  coordinator interval and date range parameters in
  `custom_components/guesty/__init__.py`
- [x] T021 [US1] Implement reservation sensor descriptions
  and `GuestyReservationSensor` class: define
  `RESERVATION_SENSOR_DESCRIPTIONS` with
  `reservation_status` entry (no category), implement
  priority selection logic per FR-006
  (`checked_in` → `awaiting_checkin` → `checked_out` →
  `canceled` → `no_reservation`), `native_value` returning
  state string, `extra_state_attributes` building full
  attribute dict (FR-007 through FR-011, FR-020, FR-021,
  FR-022), `upcoming_reservations` list limited to 10
  entries (FR-009), `async_setup_entry` creating one status
  sensor per listing tracked by reservations coordinator in
  `custom_components/guesty/sensor.py`
- [x] T022 [P] [US1] Add reservation sensor translation
  keys (`reservation_status` name) and options flow labels
  (`reservation_scan_interval`, `past_days`, `future_days`
  with descriptions) to
  `custom_components/guesty/strings.json` and
  `custom_components/guesty/translations/en.json`

<!-- markdownlint-enable MD013 -->

**Checkpoint**: Each listing device shows a reservation status
sensor with rich attributes. Status changes fire HA
state-change events for automations. Polling interval and date
range are configurable via options flow. This is the **MVP**.

---

## Phase 3: US3 + US5 — Guest Details + Error Resilience (P2)

**Goal**: Validate guest information exposure on reservation
sensors with edge case coverage for missing contact data.
Verify graceful degradation when the Guesty API is temporarily
unreachable, ensuring stale data retention and automatic
recovery.

**Independent Test (US3)**: Check the attributes of the
reservation sensor and verify guest name, phone, and email are
present for the current reservation; verify missing fields
show as `None` rather than causing errors.

**Independent Test (US5)**: Simulate a Guesty API failure
after initial data load and verify reservation data is retained
with appropriate logging.

### Phase 3 Tests for US3 — Guest Information

<!-- markdownlint-disable MD013 -->

- [x] T023 [US3] Write tests verifying guest information
  attributes: `guest_name`, `guest_phone`, `guest_email`
  present on reservation status sensor for active reservation
  with complete guest data; missing guest phone shows `None`;
  missing guest email shows `None`; no guest object on
  reservation shows all guest attributes as `None`;
  `upcoming_reservations` attribute includes guest names in
  summaries per FR-009; reservation with partial guest data
  (name only) exposes name while phone/email are `None` in
  `tests/test_reservation_sensor.py`

<!-- markdownlint-enable MD013 -->

### Phase 3 Tests for US5 — Error Resilience

<!-- markdownlint-disable MD013 -->

- [x] T024 [P] [US5] Write tests for coordinator error
  handling and stale data retention: `_async_update_data`
  raises `UpdateFailed` on
  `GuestyConnectionError`/`GuestyRateLimitError`/`GuestyResponseError`,
  `DataUpdateCoordinator` retains last-known-good data after
  `UpdateFailed` (FR-014), recovery updates data on next
  successful fetch, warning logged on API failure with error
  context in `tests/test_coordinator.py`
- [x] T025 [P] [US5] Write tests for sensor availability
  during API failures: sensor reports previous state when
  coordinator has stale data, entity availability reflects
  coordinator error state so user is aware of the issue
  (US5 scenario 2), sensors recover to fresh data when API
  becomes reachable again (US5 scenario 3), no automations
  misfire due to missing data during outage in
  `tests/test_reservation_sensor.py`

<!-- markdownlint-enable MD013 -->

**Checkpoint**: Guest contact information is verified accessible
on reservation sensors with graceful handling of missing data.
API outages preserve last-known-good reservation data with
automatic recovery.

---

## Phase 4: US4 — Financial Summary Sensors (P3)

**Goal**: Expose reservation financial data (total price,
balance due, currency) as diagnostic-category sensors on each
listing device, keeping the primary entity list clean while
making financial data accessible to power users.

**Independent Test**: Verify that diagnostic-category sensors
on the listing device display total price, balance due, and
currency for the current reservation. Verify sensors show
unavailable when no financial data exists.

### Phase 4 Tests (Red) — Write First, Verify They FAIL

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

<!-- markdownlint-disable MD013 -->

- [x] T026 [US4] Write tests for financial diagnostic sensors:
  `reservation_total` `native_value` returns
  `money.total_paid`, `reservation_balance` returns
  `money.balance_due`, `reservation_currency` returns
  `money.currency`, `entity_category` is
  `EntityCategory.DIAGNOSTIC` for all three, sensors show
  unavailable when no financial data present rather than
  displaying misleading zero values (FR-019, US4 scenario 2),
  `unique_id` format includes sensor key, `device_info`
  attaches to listing device in
  `tests/test_reservation_sensor.py`

<!-- markdownlint-enable MD013 -->

### Phase 4 Implementation (Green) — Make Tests Pass

<!-- markdownlint-disable MD013 -->

- [x] T027 [US4] Add `reservation_total`,
  `reservation_balance`, `reservation_currency` sensor
  descriptions with
  `entity_category=EntityCategory.DIAGNOSTIC` and
  appropriate `value_fn` callables to
  `RESERVATION_FINANCIAL_DESCRIPTIONS`; update
  `async_setup_entry` to create financial sensors per listing
  in `custom_components/guesty/sensor.py`
- [x] T028 [P] [US4] Add translations for
  `reservation_total` ("Reservation total"),
  `reservation_balance` ("Reservation balance"),
  `reservation_currency` ("Reservation currency") to
  `custom_components/guesty/strings.json` and
  `custom_components/guesty/translations/en.json`

<!-- markdownlint-enable MD013 -->

**Checkpoint**: Financial summary data is available as
diagnostic sensors on listing devices. Sensors show unavailable
for reservations without financial data. Primary entity list
remains clean.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Edge case coverage, state transition validation,
integration testing, and quality gates across all phases.

<!-- markdownlint-disable MD013 -->

- [x] T029 Write edge case tests: no reservations for a
  listing shows `"no_reservation"` (FR-016), reservation with
  unknown listing ID skipped with warning logged (FR-017),
  same-day turnover uses chronologically nearest reservation
  for primary state with additional bookings in
  `upcoming_reservations` (FR-018), missing optional fields
  (guest phone, email, financial data, notes) handled
  gracefully (FR-019), unknown reservation status passed
  through as-is with info log (FR-025), listing deleted
  while reservations exist (sensors become unavailable,
  FR-023) in `tests/test_reservation_sensor.py`
- [x] T030 [P] Write state transition integration tests:
  verify sensor state changes fire HA state-change events
  for automations (FR-015), confirmed → `checked_in`
  transition fires event with listing ID and guest context,
  `checked_in` → `checked_out` transition fires event,
  confirmed → `canceled` transition fires event, verify
  automation trigger context includes expected attributes in
  `tests/test_reservation_sensor.py`
- [x] T031 [P] Write entity cleanup tests: verify all
  reservation entities and coordinator resources removed when
  integration is unloaded (FR-023), verify no orphaned
  entities when reservations reference non-existent listings
  (SC-009) in `tests/test_init.py`
- [x] T032 [P] Verify REUSE compliance on all new and modified
  files (SPDX headers where applicable, otherwise REUSE.toml
  annotations) for `custom_components/guesty/coordinator.py`,
  `custom_components/guesty/sensor.py`,
  `custom_components/guesty/config_flow.py`,
  `custom_components/guesty/translations/en.json`,
  `tests/test_coordinator.py`, and
  `tests/test_reservation_sensor.py`
- [x] T033 [P] Run full linting
  (`uv run ruff check custom_components/ tests/`),
  formatting
  (`uv run ruff format --check custom_components/ tests/`),
  type checking (`uv run mypy custom_components/`), and
  docstring coverage
  (`uv run interrogate custom_components/ -v`) with zero
  errors
- [x] T034 [P] Run complete test suite
  (`uv run pytest tests/ -x -q --cov=custom_components/guesty --cov-report=term-missing`)
  and verify no regressions from Features 001 and 002 tests
- [x] T035 Execute quickstart.md developer workflow validation
  from `specs/003-reservations/quickstart.md`: verify test
  commands, linting commands, and architecture diagram
  accuracy against implemented code

<!-- markdownlint-enable MD013 -->

**Checkpoint**: All edge cases produce correct behavior. State
transitions fire events for automations. Entity cleanup is
verified. Full test suite green with coverage report. No
regressions. REUSE-compliant headers on all files. Feature
complete.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Foundational)**: No dependencies — extends
  existing Feature 001 api/ package
- **Phase 2 (US1 + US2 MVP)**: Depends on Phase 1 — BLOCKED
  until reservation models and `get_reservations()` ready
- **Phase 3 (US3 + US5)**: Depends on Phase 2 — requires
  reservation sensors for attribute and resilience testing
- **Phase 4 (US4)**: Depends on Phase 2 — requires sensor
  infrastructure; Phase 3 recommended but not required
- **Phase 5 (Polish)**: Depends on all preceding phases

### User Story Dependencies

- **US1 (P1)**: Phase 1 foundational then Phase 2
  implementation
- **US2 (P1)**: Co-delivered with US1 in Phase 2 (sensor
  state changes ARE automation triggers)
- **US3 (P2)**: Phase 2 MVP then Phase 3 attribute tests
- **US5 (P2)**: Phase 2 MVP then Phase 3 resilience tests
- **US4 (P3)**: Phase 2 MVP then Phase 4 financial sensors

### Within Each Phase (TDD Order)

1. Fixtures FIRST (shared test infrastructure)
2. Tests MUST be written and verified FAILING before
   implementation
3. Constants before models (models use constants)
4. Models before client (client uses models)
5. Coordinator before sensors (sensors read coordinator)
6. Core implementation before translations
7. Implementation commits separate from task tracking updates

### Parallel Opportunities

**Phase 1 — Tests (Red)**:

- T001–T003 sequential in `tests/api/test_models.py`
- T004 (`tests/api/test_client.py`) parallel with T001–T003

**Phase 1 — Implementation (Green)**:

- T005 (`api/const.py`) parallel with other Phase 1 impl
- T006–T009 sequential in `api/models.py`
- T010 (`api/client.py`) after T005, T006–T009
- T011 (`api/__init__.py`) parallel with T010

**Phase 2 — Tests (Red)**:

- T013 (`test_coordinator.py`) ∥ T014
  (`test_reservation_sensor.py`) ∥ T015
  (`test_config_flow.py`) ∥ T016 (`test_init.py`)

**Phase 2 — Implementation (Green)**:

- T017 (`const.py`) parallel with other Phase 2 impl
- T018 (`coordinator.py`) then T020 (`__init__.py`) then
  T021 (`sensor.py`)
- T019 (`config_flow.py`) parallel with T018
- T022 (`strings.json`/`en.json`) parallel

**Phase 3**:

- T023 (`test_reservation_sensor.py`) sequential
- T024 (`test_coordinator.py`) ∥ T025
  (`test_reservation_sensor.py`)

**Phase 4**:

- T027 (`sensor.py`) then T028 (`strings.json`/`en.json`)

---

## Parallel Example: Phase 1

```text
# Parallel batch 1 — tests (different test files):
T001–T003: Model tests in tests/api/test_models.py (seq)
T004: API client tests in tests/api/test_client.py

# Parallel batch 2 — implementation (different source):
T005: Reservation constants in api/const.py
T006–T009: Models in api/models.py (sequential)

# Sequential chain:
T010: get_reservations() in api/client.py (after T005–T009)
T011: Updated exports in api/__init__.py (parallel w/ T010)
```

## Parallel Example: Phase 2

```text
# Parallel batch 1 — tests (all different files):
T013: Coordinator tests in tests/test_coordinator.py
T014: Sensor tests in tests/test_reservation_sensor.py
T015: Config flow tests in tests/test_config_flow.py
T016: Init tests in tests/test_init.py

# Parallel batch 2 — implementation (different files):
T017: Constants in const.py
T018: Coordinator in coordinator.py
T019: Config flow in config_flow.py

# Sequential chain:
T020: Init setup in __init__.py (after T018)
T021: Sensors in sensor.py (after T018, T020)
T022: Translations in strings.json + en.json (parallel)
```

---

## Implementation Strategy

### MVP First (Phase 1 + Phase 2)

1. Complete Phase 1: Reservation API Client (api/ layer)
2. Complete Phase 2: Coordinator, status sensor, options flow
3. **STOP and VALIDATE**: Each listing device shows
   reservation status with rich attributes; automations can
   trigger on state changes
4. Deploy/demo if ready — reservation status monitoring works

### Incremental Delivery

1. Phase 1 — API client ready (library-extractable)
2. Phase 2 — MVP: Status sensors + automations (US1+US2) ✅
3. Phase 3 — Guest detail + error resilience (US3+US5) ✅
4. Phase 4 — Financial summary sensors (US4) ✅
5. Phase 5 — Quality validation and polish ✅
6. Each phase adds capability without breaking previous phases

### Single Developer Strategy

1. Phase 1 tests (T001–T004) then impl (T005–T011) — PR 1
2. Phase 2 fixtures+tests (T012–T016) then impl
   (T017–T022) — PR 2
3. Phase 3 tests (T023–T025) — PR 3
4. Phase 4 tests (T026) then impl (T027–T028) — PR 4
5. Phase 5 validation (T029–T035) — PR 5

### Suggested MVP Scope

Complete Phase 1 + Phase 2 for minimum viable feature:

- Reservation status sensors on all listing devices
- Priority-based state derivation (FR-006)
- Rich attributes: dates, guest info, upcoming reservations
- Configurable polling interval and date range
- State-change events for HA automations

---

## Notes

- \[P] tasks = different files, no dependencies on incomplete
  tasks
- \[US#] label maps task to its user story for traceability
- US1 and US2 combined in Phase 2 (both P1, deeply coupled)
- TDD is mandatory: write failing tests before implementation
- Each phase is an independent PR with its own test coverage
- Commit after each task or logical group (tests separate from
  implementation)
- Task tracking updates (tasks.md) committed separately from
  code
- All new files require SPDX headers per constitution
- `api/` sub-package MUST have zero HA imports (FR-024)
- Use `git commit -s` with `Co-authored-by` trailer per
  AGENTS.md

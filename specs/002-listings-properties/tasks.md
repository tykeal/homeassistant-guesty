<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD024 -->

# Tasks: Listings/Properties

**Input**: Design documents from `/specs/002-listings-properties/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md,
contracts/guesty-listings-api.md, quickstart.md

**Tests**: TDD is mandatory per constitution. Every unit of
production code MUST be preceded by a failing test.
Red-Green-Refactor cycle is strictly enforced.

**Organization**: Tasks are grouped by user story with US1 and
US2 combined (both P1, deeply coupled via the coordinator). Each
phase becomes its own PR during implementation.

## Format: `T### [P] [US#] Description`

- **`T###`**: Sequential task ID (e.g., `T001`)
- **`[P]`**: Optional — task can run in parallel (different
  files, no deps)
- **`[US#]`**: User story tag from spec.md (`[US1]`–`[US5]`);
  omitted for setup/foundational/polish phases
- Exact file paths included in all descriptions

## Path Conventions

- **Source**: `custom_components/guesty/` (HA custom component)
- **API layer**: `custom_components/guesty/api/` (zero HA imports)
- **Tests**: `tests/` (mirrors source structure, including
  `tests/api/` for API-layer tests)

---

## Phase 1: Foundational — API Layer Extension

**Purpose**: Add listing data models and paginated API method to
the HA-independent API layer. This is the blocking prerequisite
for all user stories.

**⚠️ CRITICAL**: No user story work can begin until this phase is
complete. All models and the `get_listings()` method MUST exist
before coordinator, sensor, or entity work.

### Phase 1 Constants

- [x] T001 Add listing API constants (`LISTINGS_ENDPOINT`, `LISTINGS_PAGE_SIZE = 100`, `LISTINGS_FIELDS` tuple with all requested field names per contracts/guesty-listings-api.md) in custom_components/guesty/api/const.py
- [x] T002 [P] Add options flow constants (`DEFAULT_SCAN_INTERVAL = 15`, `MIN_SCAN_INTERVAL = 5`, `CONF_SCAN_INTERVAL = "scan_interval"`) in custom_components/guesty/const.py

### Phase 1 Tests (write first — must FAIL before implementation)

- [x] T003 Write unit tests for GuestyAddress model: test `from_api_dict` with full address dict, partial address (some fields None), None input returns None, empty dict returns None; test `formatted()` returns full when present, joins non-empty components with comma when full is absent, returns None when all components are empty in tests/api/test_models.py
- [x] T004 Write unit tests for GuestyListing model: test `from_api_dict` with complete API data dict, test status derivation (explicit archive indicator → `"archived"`, `listed=true` + `active=true` → `"active"`, `listed=false` → `"inactive"`, `active=false` → `"inactive"`), test title fallback chain (title → nickname → `"Unknown"`), test missing `_id` returns None, test timezone defaults to `"UTC"`, test tags coerced to tuple, test `custom_fields` values coerced to strings, test bedrooms/bathrooms None when absent in tests/api/test_models.py
- [x] T005 Write unit tests for GuestyListingsResponse model: test `from_api_dict` parses valid listings array, test filtering of None entries (listings with missing `_id`), test count/limit/skip fields preserved from API response in tests/api/test_models.py
- [x] T006 [P] Write unit tests for `GuestyApiClient.get_listings()`: test single-page fetch (results count < limit stops pagination), test multi-page pagination (two full pages + partial third page), test empty account returns empty list, test `GuestyAuthError` propagation, test `GuestyConnectionError` propagation, test `GuestyResponseError` on malformed JSON in tests/api/test_client.py

### Phase 1 Implementation

- [x] T007 Implement GuestyAddress frozen dataclass with fields (full, street, city, state, zipcode, country — all `str | None`), `from_api_dict(data: dict | None) → GuestyAddress | None` class method, and `formatted() → str | None` method per data-model.md in custom_components/guesty/api/models.py
- [x] T008 Implement GuestyListing frozen dataclass with all fields per data-model.md (id, title, nickname, status, address, property\_type, room\_type, bedrooms, bathrooms, timezone, check\_in\_time, check\_out\_time, tags, custom\_fields), `from_api_dict(data: dict) → GuestyListing | None` class method with status mapping that first checks for Guesty's explicit archive indicator field (`"archived"` status), then derives remaining statuses from listed/active booleans, with all default fallbacks in custom_components/guesty/api/models.py
- [x] T009 Implement GuestyListingsResponse frozen dataclass with fields (results, count, limit, skip), `from_api_dict(data: dict) → GuestyListingsResponse` class method that parses results via `GuestyListing.from_api_dict` and filters None entries in custom_components/guesty/api/models.py
- [x] T010 [P] Implement `async get_listings(self) → list[GuestyListing]` on GuestyApiClient with paginated fetch loop (`limit=LISTINGS_PAGE_SIZE`, skip-based offset, explicit `fields=` query parameter per API contract, stop condition: `len(results) < limit`) using existing `_request()` method in custom_components/guesty/api/client.py
- [x] T011 [P] Export GuestyAddress, GuestyListing, and GuestyListingsResponse from custom_components/guesty/api/\_\_init\_\_.py

**Checkpoint**: All API-layer models and `get_listings()` pass
tests. The `api/` package has zero HA imports. Paginated listing
fetch works independently of Home Assistant.

---

## Phase 2: US1 + US2 — Listing Devices, Status Sensor & Coordinator (Priority: P1) 🎯 MVP

**Goal**: Fetch Guesty listings via a DataUpdateCoordinator and
expose each listing as a Home Assistant device with a status
sensor. Provide configurable periodic refresh via options flow.

**Independent Test**: Configure the integration, verify each
Guesty listing appears as a device with a status sensor showing
active/inactive/archived. Change the refresh interval via
options flow and verify subsequent refreshes use the new
interval.

**Why combined**: US1 (listing devices and status sensor) and
US2 (periodic data refresh) are inseparable — the
`DataUpdateCoordinator` IS the refresh mechanism, and listing
devices cannot exist without it fetching data.

### Phase 2 Fixtures

- [x] T012 [US1] Add listing test fixtures and factory helpers to tests/conftest.py: `make_listing_dict(**overrides)` returning a Guesty API listing dict with sensible defaults, `make_listings_page_response(listings, count, limit, skip)` returning an API page response dict, `sample_listing()` returning a valid GuestyListing instance, mock coordinator fixture returning a mocked ListingsCoordinator with sample data dict

### Phase 2 Tests (write first — must FAIL before implementation)

- [x] T013 [US1] Write tests for ListingsCoordinator: test `_async_update_data` calls `api_client.get_listings()` and returns `dict[str, GuestyListing]` keyed by `listing.id`, test `update_interval` matches configured `scan_interval` from `entry.options`, test coordinator raises `UpdateFailed` wrapping API exceptions, test empty listing list returns empty dict in tests/test_coordinator.py
- [x] T014 [P] [US1] Write tests for GuestyListingSensor and GuestyEntity base: test `device_info` returns `identifiers={(DOMAIN, listing_id)}`, `name=listing.title`, `manufacturer="Guesty"`, `model=listing.property_type or "Listing"`; test status sensor `native_value` returns `listing.status`; test `unique_id` format is `{entry_unique_id}_{listing_id}_status`; test `entity_category` is None for status sensor in tests/test_sensor.py
- [x] T015 [P] [US2] Write tests for OptionsFlowHandler: test `async_step_init` presents `scan_interval` field with `DEFAULT_SCAN_INTERVAL` default, test valid interval saves to `entry.options` and creates entry, test interval below `MIN_SCAN_INTERVAL` raises validation error, test `async_get_options_flow` on config flow class returns handler in tests/test_config_flow.py
- [x] T016 [P] [US1] Write tests for updated `async_setup_entry`: test coordinator created and stored in `hass.data[DOMAIN][entry_id]`, test sensor platform forwarded, test `async_config_entry_first_refresh` called; test `async_unload_entry` unloads platforms and removes entry from `hass.data`; test options update listener reconfigures coordinator interval in tests/test_init.py
- [x] T016a [P] [US2] Write tests for new-listing discovery in sensor platform: test that when coordinator data gains a new listing ID after refresh, new sensor entities are created via `async_add_entities` without requiring integration reload in tests/test_sensor.py

### Phase 2 Implementation

- [x] T017 [US1] Implement `ListingsCoordinator(DataUpdateCoordinator[dict[str, GuestyListing]])` with `__init__` accepting hass, entry, and api\_client; `_async_update_data` fetching via `api_client.get_listings()`, converting list to dict keyed by `listing.id`, raising `UpdateFailed` on `GuestyApiError` subclasses; `update_interval` from `entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)` in custom_components/guesty/coordinator.py
- [x] T018 [P] [US1] Implement `GuestyEntity(CoordinatorEntity[dict[str, GuestyListing]])` base class storing `listing_id` in `__init__`, providing `device_info` property returning `DeviceInfo(identifiers={(DOMAIN, listing_id)}, name=listing.title, manufacturer="Guesty", model=listing.property_type or "Listing")` with listing looked up from `coordinator.data` in custom_components/guesty/entity.py
- [x] T019 [US1] Define `GuestyListingSensorEntityDescription(SensorEntityDescription)` dataclass adding `value_fn: Callable[[GuestyListing], StateType]`; create `LISTING_SENSOR_DESCRIPTIONS` tuple with status description (`key="status"`, `translation_key="listing_status"`, `entity_category=None`, `value_fn=lambda l: l.status`); implement `GuestyListingSensor(GuestyEntity, SensorEntity)` with `native_value` dispatching via `entity_description.value_fn`; implement `async_setup_entry` creating one sensor entity per listing per description via `async_add_entities` in custom_components/guesty/sensor.py
- [x] T020 [US2] Add `GuestyOptionsFlowHandler(OptionsFlow)` with `async_step_init` presenting `vol.Schema({vol.Required(CONF_SCAN_INTERVAL, default=...): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL))})` and saving to `entry.options`; wire it to GuestyConfigFlow via `async_get_options_flow(config_entry)` returning `GuestyOptionsFlowHandler(config_entry)` in custom_components/guesty/config_flow.py
- [x] T021 [US1] Update PLATFORMS list to `[Platform.SENSOR]` in custom_components/guesty/const.py
- [x] T022 [US1] Update `async_setup_entry` to create ListingsCoordinator with api\_client from entry data, call `await coordinator.async_config_entry_first_refresh()`, store coordinator in `hass.data` under the entry, forward PLATFORMS; update `async_unload_entry` to unload platforms then clean up; add `entry.async_on_unload(entry.add_update_listener(...))` with listener that updates `coordinator.update_interval` when options are changed through the options flow in custom_components/guesty/\_\_init\_\_.py
- [x] T022a [US2] Add new-listing discovery to sensor platform: register a coordinator listener in `async_setup_entry` (sensor.py) that compares current `coordinator.data` keys against previously known listing IDs on each update, and calls `async_add_entities` for any newly discovered listings so devices appear at runtime without requiring integration reload in custom_components/guesty/sensor.py
- [x] T023 [US1] Add translations in custom_components/guesty/strings.json using the nested JSON object structure: under `entity` → `sensor` → `listing_status` → `name` set `"Listing status"`, and under `options` → `step` → `init` → `data` → `scan_interval` and `options` → `step` → `init` → `data_description` → `scan_interval` add the options flow label and description
- [x] T024 [P] [US1] Create custom_components/guesty/translations/en.json for the English locale, mirroring the same nested `entity` and `options` object structure from strings.json

**Checkpoint**: Integration creates one device per Guesty listing
with a status sensor. Data refreshes automatically at configurable
interval. Options flow allows adjusting refresh interval. This is
the **MVP** — usable for property status monitoring and
automations.

---

## Phase 3: User Story 3 — Rich Property Detail Sensors (Priority: P2)

**Goal**: Expose detailed property information (name, nickname,
address, property type, room type, bedrooms, bathrooms, timezone,
check-in time, check-out time) as diagnostic sensors on each
listing device.

**Independent Test**: Verify each listing device exposes 11
detail sensors with correct values matching Guesty data and
diagnostic entity category. Verify sensors for missing optional
fields show `None` state rather than causing errors.

### Phase 3 Tests (write first — must FAIL before implementation)

- [x] T025 [US3] Write tests for all 11 property detail sensors: verify `native_value` for name (`listing.title`), nickname (`listing.nickname`), address (`listing.address.formatted()`), property\_type, room\_type, bedrooms, bathrooms, timezone, check\_in\_time, check\_out\_time; test `entity_category` is `EntityCategory.DIAGNOSTIC` for all; test None returned for optional fields when missing in tests/test_sensor.py

### Phase 3 Implementation

- [x] T026 [US3] Add 11 `GuestyListingSensorEntityDescription` entries to `LISTING_SENSOR_DESCRIPTIONS` tuple: name (`key="name"`, `translation_key="listing_name"`), nickname, address (`value_fn` calls `listing.address.formatted()` with None guard), property\_type, room\_type, bedrooms, bathrooms, timezone, check\_in\_time, check\_out\_time — all with `entity_category=EntityCategory.DIAGNOSTIC` and appropriate `value_fn` callables per data-model.md sensor mapping in custom_components/guesty/sensor.py
- [x] T027 [P] [US3] Add translations for all 11 detail sensors in custom_components/guesty/strings.json and custom_components/guesty/translations/en.json using the nested JSON structure: under `entity` → `sensor`, add `listing_name`, `listing_nickname`, `listing_address`, `listing_property_type`, `listing_room_type`, `listing_bedrooms`, `listing_bathrooms`, `listing_timezone`, `listing_check_in_time`, `listing_check_out_time` each with a `name` key

**Checkpoint**: Each listing device now exposes 12 sensors (1
status + 11 detail). Detail sensors use diagnostic entity
category to keep the main entity list clean. All optional-field
edge cases handled gracefully.

---

## Phase 4: US5 — Graceful Degradation & Disappeared Listings (Priority: P2)

**Goal**: Track listings that disappear from API responses and
mark their entities as unavailable. Validate coordinator error
handling with clear logging and last-known-good-data retention.

**Independent Test**: Mock API returning fewer listings than
previous fetch; verify disappeared listing entities become
unavailable while remaining entities stay available. Mock API
errors; verify existing sensors retain last-known values and
recover automatically on next successful refresh.

### Phase 4 Tests (write first — must FAIL before implementation)

- [ ] T028 [US5] Write tests for coordinator error handling and disappeared listing tracking: test `_async_update_data` raises `UpdateFailed` on `GuestyConnectionError` and `GuestyResponseError`, test `DataUpdateCoordinator` retains last-known-good data after `UpdateFailed`, test recovery updates data on next successful fetch, test `disappeared_listing_ids` populated when listing present in previous fetch but absent in current, test `disappeared_listing_ids` cleared when listing reappears, test warning logged for each disappeared listing ID in tests/test_coordinator.py
- [ ] T029 [P] [US5] Write tests for sensor entity availability: test `available` property returns True when `listing_id` present in `coordinator.data`, test `available` returns False when `listing_id` in `coordinator.disappeared_listing_ids`, test `available` returns False when `listing_id` absent from `coordinator.data` entirely, test entity regains availability when listing reappears in subsequent fetch in tests/test_sensor.py

### Phase 4 Implementation

- [ ] T030 [US5] Add `disappeared_listing_ids: set[str]` attribute to ListingsCoordinator initialized as empty set, update `_async_update_data` to compare current fetch listing IDs against previous `coordinator.data` keys, populate `disappeared_listing_ids` with IDs present previously but absent now, clear IDs that reappear, log warning per disappeared listing with listing ID context in custom_components/guesty/coordinator.py
- [ ] T031 [US5] Override `available` property on GuestyListingSensor to return False when `self._listing_id` is in `self.coordinator.disappeared_listing_ids` or `self._listing_id` is not in `self.coordinator.data`; return True otherwise (combining with parent `CoordinatorEntity.available` check) in custom_components/guesty/sensor.py

**Checkpoint**: Disappeared listing entities show as unavailable.
API errors preserve last-known-good data with automatic recovery.
Logging clearly identifies disappeared listings and error
conditions.

---

## Phase 5: US4 — Tags and Custom Fields (Priority: P3)

**Goal**: Expose listing tags as a comma-separated sensor and
each Guesty custom field as a dynamic diagnostic sensor per
listing device.

**Independent Test**: Add tags and custom fields to test listing
data; verify tags sensor shows comma-separated string of tags;
verify each custom field creates a separate diagnostic sensor
with slugified key. Verify empty tags and no custom fields are
handled gracefully (empty string and no extra sensors
respectively).

### Phase 5 Tests (write first — must FAIL before implementation)

- [ ] T032 [US4] Write tests for tags sensor (`native_value` returns `", ".join(listing.tags)` as a comma-separated string per data-model.md, empty tags tuple returns empty string) and dynamic custom field sensors (one sensor per custom field per listing, `unique_id` includes `custom_{slugified_name}`, value is the custom field string value, `entity_category` is `EntityCategory.DIAGNOSTIC`, no custom field sensors created when `listing.custom_fields` is empty dict) in tests/test_sensor.py

### Phase 5 Implementation

- [ ] T033 [US4] Add tags `GuestyListingSensorEntityDescription` to `LISTING_SENSOR_DESCRIPTIONS` (`key="tags"`, `translation_key="listing_tags"`, `entity_category=EntityCategory.DIAGNOSTIC`, `value_fn` returning `", ".join(listing.tags)` per data-model.md sensor mapping) in custom_components/guesty/sensor.py
- [ ] T034 [US4] Implement dynamic custom field sensor creation in `async_setup_entry`: after creating static description sensors, iterate each listing's `custom_fields` dict entries, create a `GuestyListingSensorEntityDescription` per field with `key=f"custom_{slugify(field_name)}"`, `translation_key="listing_custom_field"`, `entity_category=EntityCategory.DIAGNOSTIC`, and `value_fn` extracting that specific field value; add resulting entities via `async_add_entities` in custom_components/guesty/sensor.py
- [ ] T035 [P] [US4] Add translations for `listing_tags` ("Tags") and `listing_custom_field` ("Custom field") sensors in custom_components/guesty/strings.json and custom_components/guesty/translations/en.json using the nested JSON structure under `entity` → `sensor`

**Checkpoint**: Listing devices now expose tags sensor and
per-field custom field sensors. All dynamic sensors use
diagnostic entity category. Empty tags and absent custom fields
handled gracefully.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, REUSE compliance, and quality
gates across all phases

- [ ] T036 Verify REUSE compliance on all new and modified files (SPDX headers where applicable, otherwise REUSE.toml annotations) for custom_components/guesty/coordinator.py, custom_components/guesty/entity.py, custom_components/guesty/sensor.py, custom_components/guesty/translations/en.json, tests/test_coordinator.py, and tests/test_sensor.py per REUSE.toml and constitution
- [ ] T037 [P] Run full linting (`uv run ruff check custom_components/ tests/`), formatting (`uv run ruff format --check custom_components/ tests/`), type checking (`uv run mypy custom_components/`), and docstring coverage (`uv run interrogate custom_components/ -v`) with zero errors
- [ ] T038 [P] Run complete test suite (`uv run pytest tests/ -x -q --cov=custom_components/guesty --cov-report=term-missing`) and verify no regressions from Feature 001 tests
- [ ] T039 Execute quickstart.md developer workflow validation from specs/002-listings-properties/quickstart.md: verify test commands, linting commands, and architecture diagram accuracy against implemented code

**Checkpoint**: All quality gates pass. Full test suite green
with coverage report. No regressions. REUSE-compliant headers on
all files. Quickstart documentation validated.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Foundational)**: No dependencies — starts
  immediately
- **Phase 2 (US1+US2)**: Depends on Phase 1 — needs data models
  and `get_listings()` method
- **Phase 3 (US3)**: Depends on Phase 2 — extends sensor
  descriptions in `sensor.py`
- **Phase 4 (US5)**: Depends on Phase 2 — extends coordinator
  and sensor behavior
- **Phase 5 (US4)**: Depends on Phase 2 — extends sensor
  platform with dynamic creation; recommended after Phase 3
  and Phase 4 to minimize merge conflicts in `sensor.py`
- **Phase 6 (Polish)**: Depends on all preceding phases

### User Story Dependencies

- **US1 + US2 (Phase 2)**: Start after Phase 1 — no
  dependencies on other stories
- **US3 (Phase 3)**: Start after Phase 2 — adds sensor
  descriptions independently
- **US5 (Phase 4)**: Start after Phase 2 — adds coordinator
  and sensor resilience independently
- **US4 (Phase 5)**: Start after Phase 2 — adds dynamic
  sensors independently; complete after Phase 3/4 to avoid
  `sensor.py` merge conflicts

### Within Each Phase

1. Constants and fixtures FIRST (no tests needed for values)
2. Tests MUST be written and FAIL before implementation (TDD)
3. Models before services (`api/models.py` before
   `api/client.py`)
4. Infrastructure before consumers (`coordinator.py` before
   `sensor.py`)
5. Core implementation before translations
6. Phase complete before starting next priority

### Parallel Opportunities Per Phase

- **Phase 1**: T001 ∥ T002; T003–T005 ∥ T006;
  T007–T009 ∥ T010 ∥ T011
- **Phase 2**: T013 ∥ T014 ∥ T015 ∥ T016 (all different
  test files); T017 ∥ T018; T023 ∥ T024
- **Phase 3**: T026 ∥ T027
- **Phase 4**: T028 ∥ T029; T030 ∥ T031
- **Phase 5**: T033 then T034 (same file, sequential);
  T034 ∥ T035

---

## Parallel Example: Phase 1

```text
# Parallel batch 1 — constants (different files):
T001: Listing API constants in api/const.py
T002: Options flow constants in const.py

# Parallel batch 2 — tests (different test files):
T003–T005: Model tests in tests/api/test_models.py (sequential)
T006: API client tests in tests/api/test_client.py

# Parallel batch 3 — implementation (different source files):
T007–T009: Models in api/models.py (sequential)
T010: get_listings() in api/client.py
T011: Exports in api/__init__.py
```

## Parallel Example: Phase 2

```text
# Parallel batch 1 — tests (all different files):
T013: Coordinator tests in tests/test_coordinator.py
T014: Sensor tests in tests/test_sensor.py
T015: Config flow tests in tests/test_config_flow.py
T016: Init tests in tests/test_init.py

# Parallel batch 2 — implementation (different source files):
T017: Coordinator in coordinator.py
T018: Entity base in entity.py
```

---

## Implementation Strategy

### MVP First (Phase 1 + Phase 2)

1. Complete Phase 1: API data models and `get_listings()`
2. Complete Phase 2: Coordinator, status sensor, options flow
3. **STOP and VALIDATE**: Integration creates listing devices
   with status sensors; refresh is configurable
4. This is the MVP — deploy and validate with real Guesty data

### Incremental Delivery

1. Phase 1 → API layer extended → PR and review
2. Phase 2 → MVP with status sensors → PR (deploy!)
3. Phase 3 → Rich detail sensors → PR
4. Phase 4 → Error resilience → PR
5. Phase 5 → Tags and custom fields → PR
6. Phase 6 → Quality validation → PR
7. Each phase adds value without breaking previous phases

### Suggested MVP Scope

Complete Phase 1 + Phase 2 for minimum viable feature:

- Listing devices with operational status sensors
- Configurable periodic data refresh (default 15 min)
- Options flow for refresh interval adjustment
- Basic error handling via `UpdateFailed`

---

## Notes

- `[P]` tasks can run in parallel (different files, no deps)
- `[US#]` label maps tasks to user stories for traceability
- US1 and US2 are combined in Phase 2 (both P1, deeply coupled)
- Each phase is independently deployable as a separate PR
- TDD is mandatory — write failing tests before implementation
- All new files need SPDX headers per constitution
- `api/` sub-package MUST have zero HA imports (FR-015)
- Commit after each task or logical group with `-s` sign-off
  and `Co-authored-by` trailer per AGENTS.md
- Stop at any checkpoint to validate the phase independently

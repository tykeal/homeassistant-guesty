<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Custom Variables

**Input**: Design documents from `/specs/004-custom-variables/`
**Prerequisites**: plan.md (required), spec.md (required for
user stories), research.md, data-model.md, contracts/

**Tests**: TDD is mandatory per project constitution. All test
tasks MUST be completed (Red phase) and verified FAILING before
their corresponding implementation tasks (Green phase). Each
phase becomes an independent PR.

**Organization**: Tasks are grouped by implementation phase
aligned with plan.md. User stories are mapped to the phase
where their functionality is delivered. US1 and US2 are
combined (both P1, share the same service and API client).
Phase boundaries align with independently deployable
increments.

## Format: `[ID] [P?] [Story?] Description`

- **\[P]**: Can run in parallel (different files, no deps)
- **\[Story?]**: Optional; used only for tasks in user-story
  phases to indicate which user story (US1–US5)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/guesty/` (HA custom
  component)
- **API library**: `custom_components/guesty/api/` (zero HA
  imports)
- **Tests**: `tests/` and `tests/api/` (pytest with respx
  mocking)

---

## Phase 1: Foundational — Custom Fields API Client

**Purpose**: Build the library-extractable custom fields client
in the `api/` package with zero HA dependencies. Extends
existing Feature 001 API client infrastructure with custom
field definitions fetching, value writing, input validation,
and DTOs.

**⚠️ CRITICAL**: No user story work can begin until this phase
is complete. All custom field domain logic lives in `api/`.

### Phase 1 Tests (Red) — Write First, Verify They FAIL

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

<!-- markdownlint-disable MD013 -->

- [ ] T001 [P] Write unit tests for `GuestyCustomFieldDefinition` frozen dataclass: `from_api_dict` with full API data dict returns populated instance with `field_id`, `name`, `field_type`, `applicable_to`; `from_api_dict` maps Guesty `type: "string"` to integration `field_type: "text"`; unknown Guesty type values preserved as-is in `field_type`; missing `id` returns `None`; missing `name` returns `None`; missing `type` returns `None`; missing `objectType` defaults `applicable_to` to `frozenset()`; `objectType: "both"` produces `frozenset({"listing", "reservation"})` in `tests/api/test_models.py`
- [ ] T002 [P] Write unit tests for `GuestyCustomFieldUpdate` frozen dataclass: construction with `field_id` and string value, `field_id` and int value, `field_id` and float value, `field_id` and bool value; `to_api_dict` returns `{"fieldId": field_id, "value": value}` in `tests/api/test_models.py`
- [ ] T003 [P] Write unit tests for `GuestyCustomFieldResult` frozen dataclass: success result with all fields populated, failure result with `error_details`, `success=False` when error present in `tests/api/test_models.py`
- [ ] T004 [P] Write unit tests for `GuestyCustomFieldError`: construction with message only, construction with `target_type`/`target_id`/`field_id` context attributes, inherits from `GuestyApiError`, attributes accessible after construction in `tests/api/test_exceptions.py`
- [ ] T005 Write unit tests for `GuestyCustomFieldsClient.get_definitions`: successful fetch returns list of `GuestyCustomFieldDefinition` parsed from API response array, empty array returns empty list, definitions with missing required fields are skipped (filtered `None`), API error propagation from `GuestyApiClient` using respx-mocked HTTP in `tests/api/test_custom_fields.py`
- [ ] T006 Write unit tests for `GuestyCustomFieldsClient.set_field`: listing target builds PUT request to `/listings/{listing_id}/custom-fields` with body `[{"fieldId": ..., "value": ...}]` and returns `GuestyCustomFieldResult(success=True)`, reservation target builds PUT to `/reservations-v3/{reservation_id}/custom-fields` and parses v3 envelope response, 400 Bad Request raises `GuestyCustomFieldError` with context, 404 Not Found raises `GuestyCustomFieldError`, API error propagation using respx-mocked HTTP in `tests/api/test_custom_fields.py`
- [ ] T007 Write unit tests for `GuestyCustomFieldsClient.validate_value`: text field type accepts string rejects int/float/bool, number field type accepts int and float rejects string and bool (bool rejected before numeric check since `bool` subclasses `int`), boolean field type accepts bool rejects string/int/float, unknown field type skips validation (no error raised), type mismatch raises `GuestyCustomFieldError` in `tests/api/test_custom_fields.py`

<!-- markdownlint-enable MD013 -->

### Phase 1 Implementation (Green) — Make Tests Pass

<!-- markdownlint-disable MD013 -->

- [ ] T008 [P] Add custom field endpoint constants (`CUSTOM_FIELDS_ENDPOINT = "/custom-fields"`, `LISTING_CUSTOM_FIELDS_PATH = "/listings/{listing_id}/custom-fields"`, `RESERVATION_CUSTOM_FIELDS_PATH = "/reservations-v3/{reservation_id}/custom-fields"`, `CUSTOM_FIELD_TYPES = frozenset({"text", "number", "boolean"})`, `CUSTOM_FIELD_TARGETS = frozenset({"listing", "reservation"})`) to `custom_components/guesty/api/const.py`
- [ ] T009 [P] Implement `GuestyCustomFieldError` subclassing `GuestyApiError` with `target_type` (`str | None`), `target_id` (`str | None`), and `field_id` (`str | None`) context attributes in `custom_components/guesty/api/exceptions.py`
- [ ] T010 Implement `GuestyCustomFieldDefinition` frozen dataclass with fields (`field_id`, `name`, `field_type`, `applicable_to: frozenset[str]`) and `from_api_dict(data: dict) -> GuestyCustomFieldDefinition | None` factory that maps Guesty `type: "string"` to `"text"`, preserves unknown types as-is, defaults `applicable_to` to `frozenset()` when `objectType` missing, maps `objectType: "both"` to `frozenset({"listing", "reservation"})`, returns `None` for missing `id`/`name`/`type` in `custom_components/guesty/api/models.py`
- [ ] T011 Implement `GuestyCustomFieldUpdate` frozen dataclass with fields (`field_id: str`, `value: str | int | float | bool`) and `to_api_dict()` method returning `{"fieldId": field_id, "value": value}` in `custom_components/guesty/api/models.py`
- [ ] T012 Implement `GuestyCustomFieldResult` frozen dataclass with fields (`success: bool`, `target_type: str`, `target_id: str`, `field_id: str`, `error_details: str | None = None`) in `custom_components/guesty/api/models.py`
- [ ] T013 Implement `GuestyCustomFieldsClient` class with constructor accepting `GuestyApiClient`, `async get_definitions() -> list[GuestyCustomFieldDefinition]` calling GET `/custom-fields` and parsing via `from_api_dict` with `None` filtering, `async set_field(target_type, target_id, field_id, value) -> GuestyCustomFieldResult` building PUT body `[{"fieldId": ..., "value": ...}]` to listing or reservation-v3 endpoint with response parsing (flat array for listings, v3 envelope for reservations), and `validate_value(value, field_type) -> None` with `type(value) in (int, float)` for number check (rejecting bool before numeric) in `custom_components/guesty/api/custom_fields.py`
- [ ] T014 Update public API exports to include `GuestyCustomFieldsClient`, `GuestyCustomFieldError`, `GuestyCustomFieldDefinition`, `GuestyCustomFieldUpdate`, and `GuestyCustomFieldResult` in `custom_components/guesty/api/__init__.py`

<!-- markdownlint-enable MD013 -->

**Checkpoint**: All api/ custom fields tests pass. The custom
fields client can fetch definitions, write values to listings
and reservations, and validate input types — all independently
of Home Assistant. Zero HA imports in `api/`.

---

## Phase 2: US1 + US2 — Set Custom Fields (P1) 🎯 MVP

**Goal**: Wire the custom fields client into Home Assistant as
a `CustomFieldsDefinitionCoordinator` for definitions and a
registered `guesty.set_custom_field` service for writes to
both listings and reservations.

**Independent Test**: Call `guesty.set_custom_field` with a
valid listing/reservation target, field ID, and value. Verify
the API receives the correct PUT request and the service
returns a structured success response.

**Why combined**: US1 (listing custom fields) and US2
(reservation custom fields) share the same service handler,
coordinator, and API client. They differ only in the target
type parameter and endpoint path, both handled by
`GuestyCustomFieldsClient.set_field()`.

### Phase 2 Fixtures

<!-- markdownlint-disable MD013 -->

- [ ] T015 [US1] Add custom field test fixtures to `tests/conftest.py`: `make_custom_field_definition_dict(**overrides)` returning a Guesty API custom field definition dict with sensible defaults, `sample_custom_field_definitions()` returning a list of `GuestyCustomFieldDefinition` instances (listing field, reservation field, both-target field), mock `CustomFieldsDefinitionCoordinator` fixture with sample data, mock `GuestyCustomFieldsClient` fixture

<!-- markdownlint-enable MD013 -->

### Phase 2 Tests (Red) — Write First, Verify They FAIL

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

<!-- markdownlint-disable MD013 -->

- [ ] T016 [US1] Write tests for `CustomFieldsDefinitionCoordinator`: `_async_update_data` calls `GuestyCustomFieldsClient.get_definitions()` and returns `list[GuestyCustomFieldDefinition]`, `get_field(field_id)` returns matching definition or `None`, `get_fields_for_target("listing")` filters to listing-applicable fields, `get_fields_for_target("reservation")` filters to reservation-applicable fields, `update_interval` matches configured `CONF_CF_SCAN_INTERVAL` from `entry.options` (default 15 min), raises `UpdateFailed` on `GuestyApiError`, empty definitions returns empty list in `tests/test_coordinator.py`
- [ ] T017 [US1] Write tests for `set_custom_field` service handler: successful listing update returns `{"target_type": "listing", "target_id": ..., "field_id": ..., "result": "success"}`, successful reservation update returns structured response with `target_type: "reservation"`, field ID not in definitions raises `HomeAssistantError` listing available fields for target type, type mismatch (text field with int value) raises `HomeAssistantError` with validation message, `GuestyCustomFieldError` from client maps to `HomeAssistantError`, missing entry data raises `HomeAssistantError` in `tests/test_custom_field_service.py`
- [ ] T018 [P] [US1] Write tests for `async_setup_entry` custom field integration: creates `GuestyCustomFieldsClient` and stores in `hass.data`, creates `CustomFieldsDefinitionCoordinator` and performs initial refresh, registers `guesty.set_custom_field` service with `supports_response=SupportsResponse.OPTIONAL`, `async_unload_entry` cleans up coordinator and service in `tests/test_init.py`
- [ ] T019 [P] [US2] Write tests for reservation-specific custom field behavior: service call with `target_type="reservation"` delegates to `set_field` with reservation target, v3 endpoint path used for reservation writes, reservation not found (404) produces clear error with reservation ID context in `tests/test_custom_field_service.py`

<!-- markdownlint-enable MD013 -->

### Phase 2 Implementation (Green) — Make Tests Pass

<!-- markdownlint-disable MD013 -->

- [ ] T020 [P] [US1] Add custom field HA constants (`SERVICE_SET_CUSTOM_FIELD = "set_custom_field"`, `CONF_CF_SCAN_INTERVAL = "cf_scan_interval"`, `DEFAULT_CF_SCAN_INTERVAL = 15`) to `custom_components/guesty/const.py`
- [ ] T021 [US1] Implement `CustomFieldsDefinitionCoordinator` extending `DataUpdateCoordinator[list[GuestyCustomFieldDefinition]]` with `_async_update_data` calling `GuestyCustomFieldsClient.get_definitions()`, `get_field(field_id) -> GuestyCustomFieldDefinition | None` lookup, `get_fields_for_target(target_type) -> list[GuestyCustomFieldDefinition]` filtering by `applicable_to`, configurable `update_interval` from `entry.options` in `custom_components/guesty/coordinator.py`
- [ ] T022 [US1] Update `async_setup_entry` to create `GuestyCustomFieldsClient(api_client)`, create `CustomFieldsDefinitionCoordinator`, perform initial `async_config_entry_first_refresh`, register `guesty.set_custom_field` service with voluptuous schema (`target_type`, `target_id`, `field_id`, `value`) and `supports_response=SupportsResponse.OPTIONAL`, store coordinator and client in `hass.data`; update `async_unload_entry` for cleanup in `custom_components/guesty/__init__.py`
- [ ] T023 [US1] Implement `_async_handle_set_custom_field` service handler: extract parameters from `ServiceCall.data`, look up field in `CustomFieldsDefinitionCoordinator`, validate value type via `GuestyCustomFieldsClient.validate_value`, call `set_field()`, return structured response dict on success, map `GuestyCustomFieldError` to `HomeAssistantError` with actionable message in `custom_components/guesty/__init__.py`
- [ ] T024 [P] [US1] Add `set_custom_field` service definition with `target_type` (select: listing/reservation), `target_id` (text), `field_id` (text), `value` (object) fields to `custom_components/guesty/services.yaml`
- [ ] T025 [P] [US1] Add custom field service translation keys (service name, description, field descriptions) to `custom_components/guesty/strings.json` and `custom_components/guesty/translations/en.json`

<!-- markdownlint-enable MD013 -->

**Checkpoint**: Users can set custom fields on listings and
reservations via `guesty.set_custom_field` service call. The
coordinator caches field definitions for validation and
discovery. This is the MVP.

---

## Phase 3: US3 + US4 — Automation + Discovery (P2)

**Goal**: Validate automation compatibility and verify field
definition discovery works end-to-end through the full
coordinator to service stack. Core logic was built in Phase 1
(api/) and Phase 2 (coordinator + service); this phase adds
integration coverage and edge case handling.

**Independent Test**: Create an HA automation that calls
`guesty.set_custom_field` on a trigger event and verify the
field is updated. Query the coordinator for available fields
by target type and verify definitions match Guesty account.

### Phase 3 Tests for US3 — Automation Compatibility

<!-- markdownlint-disable MD013 -->

- [ ] T026 [US3] Write automation compatibility tests: HA service call to `guesty.set_custom_field` dispatches update through custom fields client; service call with template-rendered value sends resolved value; service does not block HA event loop; service failure in automation context is logged and does not crash the automation in `tests/test_custom_field_service.py`
- [ ] T027 [US3] Write response data tests: automation calls service with `return_response=True` and receives structured success response for subsequent action consumption; fire-and-forget call (no `return_response`) succeeds without response data in `tests/test_custom_field_service.py`

<!-- markdownlint-enable MD013 -->

### Phase 3 Tests for US4 — Field Discovery

<!-- markdownlint-disable MD013 -->

- [ ] T028 [US4] Write field discovery tests: coordinator exposes definitions with name, identifier, type, and applicability; `get_fields_for_target("listing")` returns only listing-applicable fields; `get_fields_for_target("reservation")` returns only reservation-applicable fields; fields applicable to "both" appear in both target queries; empty definitions returns empty list without error in `tests/test_coordinator.py`
- [ ] T029 [US4] Write definition refresh tests: new field added in Guesty appears after coordinator refresh; deleted field removed after refresh; `update_interval` respects configured `CONF_CF_SCAN_INTERVAL` in `tests/test_coordinator.py`

<!-- markdownlint-enable MD013 -->

### Phase 3 Edge Case Tests

<!-- markdownlint-disable MD013 -->

- [ ] T030 Write edge case tests: field ID not in definitions for target type produces error listing available fields; multi-type field value passed through to Guesty for server-side validation; Unicode and special characters in values passed through without modification; very long string values passed through; concurrent service calls execute independently; definitions change during update — stale field reference surfaced from Guesty rejection; no custom fields defined for target type produces clear error in `tests/test_custom_field_service.py`

<!-- markdownlint-enable MD013 -->

**Checkpoint**: Automations can call the custom field service
without blocking. Field definitions are discoverable by target
type. Edge cases produce actionable error messages.

---

## Phase 4: US5 + Polish — Error Handling & Integration (P3)

**Purpose**: Comprehensive error scenario coverage through the
full stack, security validation, and success criteria
verification.

### Phase 4 Tests for US5 — Error and Rate Limit Handling

<!-- markdownlint-disable MD013 -->

- [ ] T031 [US5] Write rate limit retry integration tests: mock 429 response with `Retry-After` header on first `set_field` call followed by 200 success on retry; verify field eventually updated; verify retry used exponential backoff via `GuestyApiClient` in `tests/test_custom_field_service.py`
- [ ] T032 [US5] Write transient failure retry tests: mock transient network error on initial `set_field` call followed by 200 success on retry; verify field eventually updated; mock persistent failure and verify `GuestyConnectionError` raised after retries exhausted; verify error includes target type, target ID, and field ID context in `tests/test_custom_field_service.py`
- [ ] T033 [US5] Write error detail quality tests: invalid field ID (not in definitions) returns error listing available fields for target type; type mismatch returns error identifying expected vs actual type; missing target (404) returns error with target type and ID; errors logged at appropriate severity (warning for retries, error for final failure) in `tests/test_custom_field_service.py`

<!-- markdownlint-enable MD013 -->

### Phase 4 Cross-Cutting Validation

<!-- markdownlint-disable MD013 -->

- [ ] T034 Write security tests verifying no custom field values (may contain access codes, PII per FR-014), OAuth tokens, or target identifiers appear in log output at any log level (DEBUG through CRITICAL) during successful update, failed update, and retry scenarios using caplog fixture in `tests/test_custom_field_service.py`
- [ ] T035 Write integration tests: full data flow from `guesty.set_custom_field` service call through coordinator validation through `GuestyCustomFieldsClient.set_field()` to mocked Guesty response and back to structured service response, covering both listing and reservation paths in `tests/test_custom_field_service.py`
- [ ] T036 Write success criteria validation tests: SC-001 update completes in <10s (mocked); SC-005 invalid calls produce errors within 2s; SC-006 definitions discoverable within 2 refreshes; SC-007 no event loop blocking; SC-009 all scenarios testable without live Guesty in `tests/test_custom_field_service.py`
- [ ] T037 Run quickstart.md validation: verify all code examples in `specs/004-custom-variables/quickstart.md` compile and execute correctly against mocked API fixtures; verify documented error handling patterns work as described

<!-- markdownlint-enable MD013 -->

**Checkpoint**: All error scenarios produce actionable messages
with target context. Rate limits and transient failures handled
transparently with retry. No sensitive data in logs. All
success criteria validated. Feature complete.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Foundational)**: No dependencies — extends
  existing Feature 001 api/ package
- **Phase 2 (US1 + US2 MVP)**: Depends on Phase 1 — BLOCKED
  until custom fields client ready
- **Phase 3 (US3 + US4)**: Depends on Phase 2 — requires
  coordinator and service for integration tests
- **Phase 4 (US5 + Polish)**: Depends on Phase 2 — Phase 3
  recommended but not strictly required

### User Story Dependencies

- **US1 (P1)**: Phase 1 foundational then Phase 2
  implementation
- **US2 (P1)**: Co-delivered with US1 in Phase 2 (same
  service handler, different target type)
- **US3 (P2)**: Phase 2 MVP then Phase 3 automation tests
- **US4 (P2)**: Phase 2 MVP then Phase 3 discovery tests
- **US5 (P3)**: Phase 2 MVP then Phase 4 error scenario tests

### Within Each Phase (TDD Order)

1. Tests MUST be written and verified FAILING before
   implementation
2. Constants and exceptions before DTOs (DTOs use constants)
3. DTOs before custom fields client (client uses DTOs)
4. Custom fields client before coordinator (coordinator
   delegates to client)
5. Coordinator before service handler (handler uses
   coordinator for validation)
6. Implementation commits separate from task tracking updates

### Parallel Opportunities

**Phase 1 — Tests (Red)**:

- T001, T002, T003 (`test_models.py`) parallel with T004
  (`test_exceptions.py`) — different files
- T005, T006, T007 — sequential in `test_custom_fields.py`

**Phase 1 — Implementation (Green)**:

- T008 (api/const.py) parallel with T009
  (api/exceptions.py) — different files
- T010, T011, T012 (api/models.py) — sequential, same file
- T013 (api/custom\_fields.py) after T008, T009, T010–T012
- T014 (`api/__init__.py`) after T009, T010–T013

**Phase 2**:

- T016 (`test_coordinator.py`) parallel with T017, T019
  (`test_custom_field_service.py`) parallel with T018
  (`test_init.py`) — different files
- T020 (const.py) parallel with T024 (services.yaml)
  parallel with T025 (strings.json + translations)
- T021 (coordinator.py) then T022, T023 (\_\_init\_\_.py)

---

## Parallel Example: Phase 1

```text
# Launch test writing for Phase 1 (Red):
# Parallel group (different files):
Task: "DTO tests in tests/api/test_models.py"       T001-T003
Task: "Exception tests in test_exceptions.py"             T004

# Sequential in test_custom_fields.py:
Task: "get_definitions tests"                             T005
Task: "set_field tests"                                   T006
Task: "validate_value tests"                              T007

# Launch implementation for Phase 1 (Green):
# Parallel pair:
Task: "CF constants in api/const.py"                      T008
Task: "GuestyCustomFieldError in api/exceptions.py"       T009

# Sequential chain:
Task: "CF Definition DTO in api/models.py"                T010
Task: "CF Update DTO in api/models.py"                    T011
Task: "CF Result DTO in api/models.py"                    T012
Task: "GuestyCustomFieldsClient in custom_fields.py"      T013
Task: "Updated exports in api/__init__.py"                T014
```

---

## Implementation Strategy

### MVP First (Phase 1 + Phase 2)

1. Complete Phase 1: Custom Fields API Client (api/ layer)
   — PR 1
2. Complete Phase 2: Coordinator & Service (US1 + US2) —
   PR 2
3. **STOP and VALIDATE**: Set custom field via
   `guesty.set_custom_field`
4. Deploy/demo if ready — basic custom field writes work

### Incremental Delivery

1. Phase 1 — Custom fields client ready
   (library-extractable)
2. Phase 2 — MVP: Set fields from HA (US1 + US2) ✅
3. Phase 3 — Automation + discovery (US3 + US4) ✅
4. Phase 4 — Production-grade error handling (US5) ✅
5. Each phase adds capability without breaking previous
   phases

### Single Developer Strategy

1. Phase 1 tests (T001–T007) then impl (T008–T014) — PR 1
2. Phase 2 tests (T015–T019) then impl (T020–T025) — PR 2
3. Phase 3 tests (T026–T030) — PR 3
4. Phase 4 tests + validation (T031–T037) — PR 4

---

## Notes

- \[P] tasks = different files, no dependencies on
  incomplete tasks
- \[US#] label maps task to its user story for traceability
- TDD is mandatory: write failing tests before
  implementation
- Each phase is an independent PR with its own test coverage
- Commit after each task or logical group (tests separate
  from implementation)
- Task tracking updates (tasks.md) committed separately from
  code
- All new files require SPDX headers per constitution
- Use `git commit -s` with `Co-authored-by` trailer per
  AGENTS.md

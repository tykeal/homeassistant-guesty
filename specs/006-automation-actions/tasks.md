<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Automation Actions

**Input**: Design documents from `/specs/006-automation-actions/`
**Prerequisites**: plan.md (required), spec.md (required for user
stories), research.md, data-model.md, contracts/guesty-actions-api.md,
quickstart.md

**Tests**: TDD is mandated by the implementation plan. Unit tests are
written first within each user story phase and must fail before
implementation begins.

**Organization**: Tasks are grouped by user story to enable independent
implementation and testing of each story. Five user stories map to the
five action services defined in the spec (SD-001 through SD-005).

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story?]**: Optional user story tag for story-specific tasks
  (e.g., US1, US2); shared infrastructure and foundational tasks
  omit it
- Include exact file paths in descriptions

## Path Conventions

- **Custom component**: `custom_components/guesty/`
- **API library**: `custom_components/guesty/api/`
- **Tests**: `tests/` and `tests/api/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Extend existing API package with action-specific constants,
exception class, and result model needed by all user stories.

- [x] T001 [P] Add action endpoint paths and validation constants to
  custom_components/guesty/api/const.py — add TASKS_ENDPOINT,
  CALENDAR_ENDPOINT, MAX_NOTE_LENGTH, MAX_TASK_TITLE_LENGTH,
  MAX_DESCRIPTION_LENGTH, MAX_CUSTOM_FIELD_LENGTH,
  VALID_LISTING_STATUSES, and VALID_CALENDAR_OPS per plan Phase 1
- [x] T002 [P] Add GuestyActionError exception to
  custom_components/guesty/api/exceptions.py — inherit GuestyApiError,
  add target_id and action_type fields per data-model.md
- [x] T003 [P] Add ActionResult frozen dataclass to
  custom_components/guesty/api/models.py — fields success, target_id,
  error with `__post_init__` validation per data-model.md

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the GuestyActionsClient skeleton, HA service
registration framework, service metadata, and shared test fixtures
that all user story phases depend on.

**⚠️ CRITICAL**: No user story work can begin until this phase is
complete.

- [x] T004 Create GuestyActionsClient class skeleton in
  custom_components/guesty/api/actions.py — constructor receives
  GuestyApiClient via dependency injection per messaging.py pattern;
  no action methods yet; include SPDX header and module docstring
- [x] T005 Update exports in
  `custom_components/guesty/api/__init__.py` — add ActionResult,
  GuestyActionError, and GuestyActionsClient to `__all__`
- [x] T006 Create HA service handler module
  custom_components/guesty/actions.py — implement
  async_setup_actions(hass, entry) and async_unload_actions(hass,
  entry) with first-entry/last-entry guard logic, config entry
  resolution helper, and Voluptuous schema framework; no individual
  handlers yet; include SPDX header
- [x] T007 Wire actions client creation and service lifecycle in
  `custom_components/guesty/__init__.py` — create GuestyActionsClient
  in async_setup_entry, store in hass.data, call
  async_setup_actions/async_unload_actions
- [x] T008 [P] Add five action service definitions to
  custom_components/guesty/services.yaml — add_reservation_note,
  set_listing_status, create_task, set_calendar_availability,
  update_reservation_custom_field with required/optional fields per
  spec SD-001 through SD-005
- [x] T009 [P] Add action service strings to
  custom_components/guesty/strings.json — add services section with
  name, description, and field descriptions for all five actions
- [x] T010 [P] Add action service translations to
  custom_components/guesty/translations/en.json — mirror strings.json
  services section for English locale
- [x] T011 Add action test fixtures and helpers to
  tests/conftest.py — add mock_actions_client fixture returning
  AsyncMock of GuestyActionsClient, add make_action_result helper
- [x] T012 [P] Unit tests for ActionResult validation in
  tests/api/test_models.py — test frozen dataclass, target_id
  non-empty enforcement, success/error consistency invariants
- [x] T013 [P] Unit tests for GuestyActionError in
  tests/api/test_exceptions.py — test inheritance from
  GuestyApiError, target_id and action_type fields, string repr
- [x] T014 Unit tests for actions service registration lifecycle in
  tests/test_init.py — test GuestyActionsClient creation, storage in
  hass.data, setup/unload call sequence, multi-entry guard

**Checkpoint**: Foundation ready — user story implementation can now
begin in priority order.

---

## Phase 3: User Story 1 — Update Reservation Notes (P1) 🎯 MVP

**Goal**: Enable automations to append timestamped notes to Guesty
reservations via guesty.add_reservation_note service call (SD-001).

**Independent Test**: Call the action with a valid reservation ID and
note text, verify the note is appended (read-modify-write) without
overwriting existing content, and verify ActionResult is returned.

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [x] T015 [P] [US1] Unit tests for add_reservation_note API method
  in tests/api/test_actions.py — test successful append
  (read-modify-write), empty reservation note field, input validation
  (empty note, note exceeding MAX_NOTE_LENGTH, empty reservation_id),
  API 404 raises GuestyActionError, separator format between notes
- [x] T016 [P] [US1] HA handler tests for
  guesty.add_reservation_note in tests/test_actions.py — test
  Voluptuous schema validation, successful service call returns
  ActionResult dict, GuestyActionError translated to
  HomeAssistantError, GuestyApiError translated to
  HomeAssistantError, service response structure for automation
  branching

### Implementation for User Story 1

- [x] T017 [US1] Implement add_reservation_note method in
  custom_components/guesty/api/actions.py — validate inputs, GET
  current reservation note, append with separator, PUT combined note,
  return ActionResult; raise GuestyActionError on failure
- [x] T018 [US1] Implement _handle_add_reservation_note handler and
  register service in custom_components/guesty/actions.py — define
  Voluptuous schema for reservation_id and note_text, delegate to
  GuestyActionsClient, translate exceptions to HomeAssistantError,
  return ActionResult as response data with
  supports_response=SupportsResponse.OPTIONAL

**Checkpoint**: User Story 1 fully functional — automations can append
notes to reservations.

---

## Phase 4: User Story 2 — Set Listing Status (P1)

**Goal**: Enable automations to activate or deactivate Guesty listings
via guesty.set_listing_status service call (SD-002).

**Independent Test**: Call the action with a valid listing ID and
status "inactive", verify the listing is deactivated in Guesty via
PUT with correct payload, and verify ActionResult is returned.

### Tests for User Story 2 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [x] T019 [P] [US2] Unit tests for set_listing_status API method in
  tests/api/test_actions.py — test activate (active=true, listed=true
  payload), deactivate (active=false payload), input validation
  (invalid status value, empty listing_id), API 404 raises
  GuestyActionError
- [x] T020 [P] [US2] HA handler tests for
  guesty.set_listing_status in tests/test_actions.py — test
  Voluptuous schema validation (status restricted to active/inactive),
  successful service call, exception translation, response structure

### Implementation for User Story 2

- [x] T021 [US2] Implement set_listing_status method in
  custom_components/guesty/api/actions.py — validate listing_id and
  status against VALID_LISTING_STATUSES, PUT correct payload per
  research.md R3 (active: true+listed: true for activate, active:
  false for deactivate), return ActionResult
- [x] T022 [US2] Implement _handle_set_listing_status handler and
  register service in custom_components/guesty/actions.py — define
  Voluptuous schema, delegate to GuestyActionsClient, translate
  exceptions, return response data

**Checkpoint**: User Stories 1 and 2 both functional — automations can
append notes and control listing availability.

---

## Phase 5: User Story 3 — Create Operational Tasks (P2)

**Goal**: Enable automations to create operational tasks in Guesty
(cleaning, maintenance) via guesty.create_task service call (SD-003).

**Independent Test**: Call the action with a listing ID, task title,
and optional description/assignee, verify the task is created in
Guesty via POST to tasks-open-api endpoint, and verify ActionResult
is returned.

### Tests for User Story 3 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [x] T023 [P] [US3] Unit tests for create_task API method in
  tests/api/test_actions.py — test successful creation with all
  fields, creation with required-only fields, input validation (empty
  title, title exceeding MAX_TASK_TITLE_LENGTH, description exceeding
  MAX_DESCRIPTION_LENGTH, empty listing_id), API error handling
- [x] T024 [P] [US3] HA handler tests for guesty.create_task in
  tests/test_actions.py — test Voluptuous schema with required and
  optional fields, successful service call, exception translation,
  response structure

### Implementation for User Story 3

- [x] T025 [US3] Implement create_task method in
  custom_components/guesty/api/actions.py — validate inputs, POST to
  TASKS_ENDPOINT with listingId, title, description, assigneeId
  payload per contract and research.md R4, return ActionResult
- [x] T026 [US3] Implement _handle_create_task handler and register
  service in custom_components/guesty/actions.py — define Voluptuous
  schema with required listing_id/task_title and optional
  description/assignee, delegate and translate exceptions

**Checkpoint**: User Stories 1–3 functional — automations can append
notes, control listings, and create tasks.

---

## Phase 6: User Story 4 — Update Calendar Availability (P2)

**Goal**: Enable automations to block or unblock date ranges on a
listing calendar via guesty.set_calendar_availability service call
(SD-004).

**Independent Test**: Call the action with a listing ID, date range,
and "block" operation, verify the calendar is updated via PUT to the
availability-pricing endpoint, and verify ActionResult is returned.

### Tests for User Story 4 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [x] T027 [P] [US4] Unit tests for set_calendar_availability API
  method in tests/api/test_actions.py — test block (status:
  unavailable payload), unblock (status: available payload), input
  validation (invalid date format, end before start, invalid
  operation, empty listing_id), API 409 conflict handling, date
  boundary cases
- [x] T028 [P] [US4] HA handler tests for
  guesty.set_calendar_availability in tests/test_actions.py — test
  Voluptuous schema for all four required fields, date format
  validation, operation restricted to block/unblock, exception
  translation, response structure

### Implementation for User Story 4

- [x] T029 [US4] Implement set_calendar_availability method in
  custom_components/guesty/api/actions.py — validate inputs including
  date format and range, PUT to CALENDAR_ENDPOINT with dateFrom,
  dateTo, and status (unavailable/available) per research.md R5,
  handle 409 conflict as GuestyActionError, return ActionResult
- [x] T030 [US4] Implement _handle_set_calendar_availability handler
  and register service in custom_components/guesty/actions.py —
  define Voluptuous schema for listing_id, start_date, end_date, and
  operation, delegate and translate exceptions

**Checkpoint**: User Stories 1–4 functional — automations can append
notes, control listings, create tasks, and manage calendar
availability.

---

## Phase 7: User Story 5 — Update Custom Fields (P3)

**Goal**: Enable automations to write data to reservation custom
fields in Guesty via guesty.update_reservation_custom_field service
call (SD-005).

**Independent Test**: Call the action with a reservation ID, custom
field ID, and value, verify the custom field is updated via PUT with
customFields payload, and verify ActionResult is returned.

### Tests for User Story 5 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [x] T031 [P] [US5] Unit tests for update_reservation_custom_field
  API method in tests/api/test_actions.py — test successful update,
  input validation (empty reservation_id, empty custom_field_id,
  empty value, value exceeding MAX_CUSTOM_FIELD_LENGTH), API 404
  raises GuestyActionError, customFields payload format
- [x] T032 [P] [US5] HA handler tests for
  guesty.update_reservation_custom_field in tests/test_actions.py —
  test Voluptuous schema for all three required fields, successful
  service call, exception translation, response structure

### Implementation for User Story 5

- [x] T033 [US5] Implement update_reservation_custom_field method in
  custom_components/guesty/api/actions.py — validate inputs, PUT to
  reservations endpoint with customFields object per research.md R6,
  return ActionResult
- [x] T034 [US5] Implement _handle_update_custom_field handler and
  register service in custom_components/guesty/actions.py — define
  Voluptuous schema for reservation_id, custom_field_id, and value,
  delegate and translate exceptions

**Checkpoint**: All five user stories functional — complete automation
actions suite operational.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Integration tests, edge case coverage, and cross-phase
validation.

- [x] T035 [P] Integration tests for all actions end-to-end in
  tests/test_actions.py — test full path from HA service call through
  actions client to mocked Guesty API and back to ActionResult
  response for each of the five services; verify automation
  compatibility (FR-017) and response structure (FR-022)
- [x] T036 [P] Edge case and error handling tests in
  tests/api/test_actions.py — test rate limit retry path (429 →
  backoff → success), max retries exhausted path, concurrent action
  calls, special characters and unicode in note/description, maximum
  length boundary values, token expiry during action → auto-refresh →
  retry, deleted reservation/listing returns not found error
- [x] T037 Run quickstart.md validation scenarios per
  specs/006-automation-actions/quickstart.md — execute test commands,
  linter commands, and verify architecture matches documented
  structure

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS
  all user stories
- **User Stories (Phases 3–7)**: All depend on Foundational phase
  completion
  - Stories can proceed sequentially in priority order
    (P1 → P1 → P2 → P2 → P3)
  - US1 and US2 share no code dependencies and could proceed in
    parallel if staffed
  - US3–US5 similarly independent once Phase 2 is complete
- **Polish (Phase 8)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2 — no dependencies on other
  stories
- **US2 (P1)**: Can start after Phase 2 — no dependencies on other
  stories; independent of US1
- **US3 (P2)**: Can start after Phase 2 — no dependencies on US1 or
  US2
- **US4 (P2)**: Can start after Phase 2 — no dependencies on other
  stories
- **US5 (P3)**: Can start after Phase 2 — no dependencies on other
  stories

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (TDD)
2. API client method before HA service handler
3. Story complete and tests passing before moving to next priority

### Parallel Opportunities

- All Phase 1 tasks marked [P] can run in parallel (three different
  files)
- Phase 2 tasks T008, T009, T010 can run in parallel (YAML/JSON
  metadata)
- Phase 2 tasks T012, T013 can run in parallel (different test files)
- Within each user story: API tests and HA handler tests marked [P]
  can run in parallel (different test files)
- Different user stories can be worked on in parallel by different
  team members once Phase 2 is complete

---

## Parallel Example: User Story 1

```bash
# Launch both test files in parallel (TDD: write tests first):
Task: T015 "Unit tests for add_reservation_note API method
  in tests/api/test_actions.py"
Task: T016 "HA handler tests for guesty.add_reservation_note
  in tests/test_actions.py"

# Then implement sequentially:
Task: T017 "Implement add_reservation_note in api/actions.py"
Task: T018 "Implement _handle_add_reservation_note in actions.py"
```

## Parallel Example: Phase 1

```bash
# All three setup tasks target different files:
Task: T001 "Add action constants to api/const.py"
Task: T002 "Add GuestyActionError to api/exceptions.py"
Task: T003 "Add ActionResult to api/models.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (3 tasks)
2. Complete Phase 2: Foundational (11 tasks, BLOCKS all stories)
3. Complete Phase 3: User Story 1 — Update Reservation Notes
4. **STOP and VALIDATE**: Test US1 independently — call
   guesty.add_reservation_note, verify note appended, verify
   ActionResult response
5. Deploy/demo if ready — automations can write notes to
   reservations

### Incremental Delivery

1. Setup + Foundational → framework ready
2. Add US1 (P1) → test independently → reservation note capability
3. Add US2 (P1) → test independently → listing status control
4. Add US3 (P2) → test independently → task creation
5. Add US4 (P2) → test independently → calendar management
6. Add US5 (P3) → test independently → custom field updates
7. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 (reservation notes) + US2 (listing status)
   - Developer B: US3 (tasks) + US4 (calendar)
   - Developer C: US5 (custom fields)
3. Stories complete and integrate independently

---

## Summary

| Metric | Value |
| ------ | ----- |
| Total tasks | 37 |
| Setup tasks | 3 |
| Foundational tasks | 11 |
| US1 tasks (P1) | 4 |
| US2 tasks (P1) | 4 |
| US3 tasks (P2) | 4 |
| US4 tasks (P2) | 4 |
| US5 tasks (P3) | 4 |
| Polish tasks | 3 |
| Parallel opportunities | 12 tasks marked [P] |
| Suggested MVP scope | US1 — Update Reservation Notes |
| New source files | 2 (see list below) |
| Modified source files | 8 (see list below) |
| New test files | 2 (see list below) |
| Modified test files | 4 (see list below) |

**New source files**:
`custom_components/guesty/api/actions.py`,
`custom_components/guesty/actions.py`

**Modified source files**:
`custom_components/guesty/api/const.py`,
`custom_components/guesty/api/exceptions.py`,
`custom_components/guesty/api/models.py`,
`custom_components/guesty/api/__init__.py`,
`custom_components/guesty/__init__.py`,
`custom_components/guesty/services.yaml`,
`custom_components/guesty/strings.json`,
`custom_components/guesty/translations/en.json`

**New test files**: `tests/api/test_actions.py`,
`tests/test_actions.py`

**Modified test files**: `tests/conftest.py`,
`tests/api/test_models.py`, `tests/api/test_exceptions.py`,
`tests/test_init.py`

---

## Notes

- [P] tasks = different files, no dependencies
- [Story?] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- TDD: Write tests first, verify they fail, then implement
- Commit after each task or logical group
- Task list updates are separate commits from code changes
- Stop at any checkpoint to validate story independently
- All five actions follow the same architecture: Voluptuous schema
  → HA handler → GuestyActionsClient method → GuestyApiClient
  request → Guesty API

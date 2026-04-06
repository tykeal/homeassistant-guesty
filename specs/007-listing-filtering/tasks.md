<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Listing Filtering

**Input**: Design documents from `/specs/007-listing-filtering/`
**Prerequisites**: plan.md (required), spec.md (required for user stories),
research.md, data-model.md, contracts/options-flow.md

**Tests**: Included — 100% test coverage required per constitution and user
request. TDD Red-Green-Refactor enforced (Constitution I).

**Organization**: Tasks are grouped by user story to enable independent
implementation and testing. US3 (Backward-Compatible Upgrade) is co-P1 with US1
and is integrated into Phases 2 and 3 rather than a standalone phase. Each phase
is designed as a separate PR.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete
  tasks)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/guesty/` at repository root
- **Tests**: `tests/` at repository root
- **API package**: `custom_components/guesty/api/` — NO CHANGES
  (library-extractable, zero HA imports)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add new constants, test helpers, and translation strings needed by
all subsequent phases.

- [x] T001 Add CONF_SELECTED_LISTINGS = "selected_listings" and CONF_TAG_FILTER
      = "tag_filter" constants to custom_components/guesty/const.py
- [x] T002 [P] Add multi-listing test data factories (make_listing_dict variants
      with distinct IDs, titles, addresses, and tags) and multi-listing fixtures
      to tests/conftest.py
- [x] T003 [P] Add new options flow step strings (init with tag_filter,
      select_listings with selected_listings, intervals with existing fields)
      and error strings (cannot_connect, invalid_auth, rate_limited,
      no_listings_selected, no_listings_match_tags) to
      custom_components/guesty/strings.json
- [x] T004 [P] Add matching translation strings for all new options flow steps
      and errors to custom_components/guesty/translations/en.json

---

## Phase 2: Foundational (Coordinator Filtering + Backward Compatibility)

**Purpose**: Core coordinator filtering logic that MUST be complete before ANY
user story can be implemented. Includes backward compatibility verification (US3
constraint).

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Tests

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation
> (Constitution I)**

- [x] T005 Write test: ListingsCoordinator._async_update_data returns only
      listings whose IDs are in CONF_SELECTED_LISTINGS when option is set in
      tests/test_coordinator.py
- [x] T006 Write test: ListingsCoordinator._async_update_data returns all
      listings when CONF_SELECTED_LISTINGS is absent from entry.options
      (backward compatible default) in tests/test_coordinator.py
- [x] T007 Write test: ListingsCoordinator silently ignores listing IDs in
      CONF_SELECTED_LISTINGS not present in the API response in
      tests/test_coordinator.py
- [x] T008 Write test: empty result set returned when CONF_SELECTED_LISTINGS
      contains only IDs not in API response in tests/test_coordinator.py

### Implementation

- [x] T009 Implement selected_listings filter in
      ListingsCoordinator._async_update_data: read CONF_SELECTED_LISTINGS from
      config_entry.options, when not None filter returned dict to only include
      matching IDs, when None return all listings unchanged in
      custom_components/guesty/coordinator.py

**Checkpoint**: Coordinator filtering works. `None` (absent) = all listings
(backward compatible). Explicit list = only those listings.
ReservationsCoordinator cascading filter already works via existing
`listings_coordinator.data.keys()` check — no code change needed.

---

## Phase 3: US1 + US3 — Listing Selection Options Flow (P1) 🎯 MVP

**Goal**: Users can open the options flow, see a 3-step wizard (tag filter →
listing selector → polling intervals), select which Guesty listings to track by
name and address, and save. Existing installations with no filter configuration
continue tracking all listings with zero intervention.

**Independent Test**: Open options flow, verify 3-step navigation works
end-to-end, verify listings displayed with title + formatted address, save a
selection, verify selected_listings persisted in config entry options. For
backward compatibility: verify flow preselects all listings when no prior filter
exists.

### Tests for US1 + US3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation
> (Constitution I)**

- [x] T010 [US1] Write test: async\_step\_init fetches listings
      via `hass.data[DOMAIN][entry_id]["api_client"]`
      `.get_listings()` and transitions to select\_listings
      step on submit in tests/test\_config\_flow.py
- [x] T011 [US1] Write test: async_step_init returns cannot_connect error and
      re-displays form when API client raises GuestyApiError in
      tests/test_config_flow.py
- [x] T012 [US1] Write test: async_step_select_listings builds SelectSelector
      options with label format "{title} — {formatted_address}" for each listing
      in tests/test_config_flow.py
- [x] T013 [US3] Write test: async_step_select_listings preselects all available
      listing IDs as default when CONF_SELECTED_LISTINGS is absent from
      entry.options in tests/test_config_flow.py
- [x] T014 [US1] Write test: async_step_select_listings persists user-selected
      listing IDs via async_create_entry after completing all steps in
      tests/test_config_flow.py
- [x] T015 [US1] Write test: async_step_select_listings returns
      no_listings_selected error when user submits empty selection in
      tests/test_config_flow.py
- [x] T016 [US1] Write test: async_step_intervals shows current scan_interval,
      reservation_scan_interval, past_days, future_days defaults from
      entry.options and saves on submit in tests/test_config_flow.py
- [x] T017 [US1] Write test: complete 3-step flow (init → select_listings →
      intervals) produces merged options dict containing tag_filter,
      selected_listings, scan_interval, reservation_scan_interval, past_days,
      and future_days in tests/test_config_flow.py
- [x] T018 [P] [US3] Write test: async_setup_entry succeeds and all coordinators
      refresh without error when entry.options contains no
      CONF_SELECTED_LISTINGS or CONF_TAG_FILTER keys in tests/test_init.py

### Implementation for US1 + US3

- [x] T019 [US1] Implement async_step_init: show optional tag_filter field using
      TextSelector(TextSelectorConfig(multiple=True)), fetch all listings from
      API client on submit, store in self._available_listings instance variable,
      handle GuestyApiError with cannot_connect/invalid_auth/rate_limited errors
      in custom_components/guesty/config_flow.py
- [x] T020 [US1] Implement async_step_select_listings: build SelectOptionDict
      list (value=listing.id, label="{title} — {address.formatted() or 'No
      address'}"), show SelectSelector(SelectSelectorConfig(options=...,
      multiple=True, mode=SelectSelectorMode.LIST)), default to current
      selection or all IDs, validate non-empty selection in
      custom_components/guesty/config_flow.py
- [x] T021 [US1] Refactor existing async_step_init interval logic into new
      async_step_intervals: preserve vol.Schema with
      scan_interval/reservation_scan_interval/past_days/future_days fields using
      vol.Range validators, merge all step data (tag_filter + selected_listings
      + intervals) in final async_create_entry call in
      custom_components/guesty/config_flow.py
- [x] T022 [US1] Add flow handler instance variables (self._tag_filter,
      self._selected_listings, self._available_listings) to carry state between
      steps and add new imports (SelectSelector, SelectSelectorConfig,
      SelectSelectorMode, SelectOptionDict, TextSelector, TextSelectorConfig) in
      custom_components/guesty/config_flow.py

**Checkpoint**: At this point, the full 3-step options flow is functional. Users
can select listings, existing installs are backward compatible. Tag filter field
exists but filtering behavior is added in Phase 4.

---

## Phase 4: User Story 2 — Tag-Based Pre-Filtering (Priority: P2)

**Goal**: Users managing large accounts (50–100+ listings) can enter Guesty tags
to narrow the listing selector to only matching properties before making their
selection. Multiple tags use OR logic.

**Independent Test**: Enter one or more tag values in init step, verify
select_listings step only shows listings whose Guesty tags include at least one
specified tag. Verify empty tags show all listings.

### Tests for US2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation
> (Constitution I)**

- [x] T023 [US2] Write test: async_step_select_listings shows only listings
      matching a single tag from init step tag_filter input in
      tests/test_config_flow.py
- [x] T024 [US2] Write test: async_step_select_listings shows all listings when
      tag_filter from init step is empty in tests/test_config_flow.py
- [x] T025 [US2] Write test: multiple tags in tag_filter use OR logic — listing
      shown if any of its tags match any filter tag in tests/test_config_flow.py
- [x] T026 [US2] Write test: async_step_select_listings returns
      no_listings_match_tags error when tag filter matches zero listings in
      tests/test_config_flow.py
- [x] T027 [US2] Write test: default selection in select_listings intersects
      previous CONF_SELECTED_LISTINGS with tag-filtered available listing IDs in
      tests/test_config_flow.py

### Implementation for US2

- [x] T028 [US2] Implement _filter_listings_by_tags helper function: accept
      list[GuestyListing] and list[str] tags, return filtered list using OR
      logic via set intersection on listing.tags, return all listings when tags
      is empty in custom_components/guesty/config_flow.py
- [x] T029 [US2] Integrate _filter_listings_by_tags into
      async_step_select_listings: apply tag filter from self._tag_filter to
      self._available_listings before building SelectSelector options, show
      no_listings_match_tags error on empty result in
      custom_components/guesty/config_flow.py

**Checkpoint**: Tag pre-filtering works. Users with 80+ listings can narrow to a
region's 12 listings in seconds using tags.

---

## Phase 5: US4 — Clean Removal of Deselected Entities (P2)

**Goal**: When listings are deselected, their HA devices and all associated
sensors (listing status, reservations, financials, custom fields) are cleanly
removed from the device and entity registries. Re-selecting recreates them on
next refresh.

**Independent Test**: Deselect a listing with known associated sensors, save
options, verify device removed via
`dev_registry.async_update_device(remove_config_entry_id=...)`, verify no
orphaned entities. Re-select and verify fresh entities created.

### Tests for US4

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation
> (Constitution I)**

- [x] T030 [US4] Write test: _async_options_updated removes device via
      dev_registry.async_update_device(device.id,
      remove_config_entry_id=entry.entry_id) when listing deselected in
      tests/test_init.py
- [x] T031 [US4] Write test: _async_options_updated removes multiple devices
      when multiple listings are deselected simultaneously in tests/test_init.py
- [x] T032 [US4] Write test: _async_options_updated does not remove devices for
      listings that remain in selected_listings in tests/test_init.py
- [x] T033 [US4] Write test: _async_options_updated handles transition from None
      (no prior filter, all tracked) to explicit selected_listings without
      removing still-selected devices in tests/test_init.py
- [x] T034 [P] [US4] Write test: entity platform recreates device and sensors
      when previously deselected listing is re-added to selected_listings and
      coordinator refreshes in tests/test_sensor.py

### Implementation for US4

- [x] T035 [US4] Implement `_remove_deselected_devices` helper: use
      dr.async_get(hass) and dr.async_entries_for_config_entry
      to iterate devices, extract listing IDs from
      device.identifiers tuples where identifier\[0\] == DOMAIN,
      call async_update_device(remove_config_entry_id=)
      for devices with listing IDs not in selected set in
      custom_components/guesty/\_\_init\_\_.py
- [x] T036 [US4] Integrate `_remove_deselected_devices` into
      `_async_options_updated`: detect selected_listings
      presence in updated options and call cleanup helper,
      add dr import to
      custom_components/guesty/\_\_init\_\_.py

**Checkpoint**: Entity lifecycle is complete. Deselect → device and all sensors
removed. Re-select → fresh entities on next refresh. No orphaned entities.

---

## Phase 6: User Story 5 — Immediate Refresh After Filter Changes (Priority: P3)

**Goal**: After saving changed filter settings, listings and reservations
coordinators refresh immediately (within seconds) rather than waiting for the
next scheduled poll interval (up to 15 minutes). No unnecessary refresh when
settings are unchanged.

**Independent Test**: Modify listing filter, save, verify
async_request_refresh() called on both coordinators. Save with no changes,
verify no refresh triggered.

### Tests for US5

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation
> (Constitution I)**

- [ ] T037 [US5] Write test: _async_options_updated calls async_request_refresh
      on both listings coordinator and reservations coordinator when
      selected_listings changes in tests/test_init.py
- [ ] T038 [US5] Write test: _async_options_updated does not call
      async_request_refresh when options saved with identical selected_listings
      value in tests/test_init.py
- [ ] T039 [US5] Write test: async_request_refresh is called after
      _remove_deselected_devices completes (correct ordering) in
      tests/test_init.py

### Implementation for US5

- [ ] T040 [US5] Implement conditional refresh in
      `_async_options_updated`: store previous
      selected\_listings in `hass.data[DOMAIN][entry_id]`,
      compare with new value on update, call
      `coordinator.async_request_refresh()` and
      `reservations_coordinator.async_request_refresh()`
      only when changed in
      custom_components/guesty/\_\_init\_\_.py

**Checkpoint**: Filter changes reflected immediately. Users see updated entity
list within seconds of saving. No unnecessary API calls when settings unchanged
(FR-014).

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Verify all quality standards, run full validation suite, confirm
100% coverage.

- [ ] T041 [P] Verify 100% docstring coverage with uv run interrogate
      custom_components/guesty/ on all modified and new files
- [ ] T042 [P] Verify mypy type checking passes with uv run mypy
      custom_components/guesty/ with no errors
- [ ] T043 [P] Verify ruff linting and formatting passes with uv run ruff check
      and uv run ruff format --check on custom_components/ and tests/
- [ ] T044 Run full test suite with uv run pytest tests/
      --cov=custom_components/guesty --cov-report=term-missing to confirm 100%
      branch coverage
- [ ] T045 Run all pre-commit hooks with pre-commit run --all-files to verify
      full compliance
- [ ] T046 Validate quickstart.md development scenarios: run test commands,
      linting commands, and verify commit conventions

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (constants) — BLOCKS all user
  stories
- **US1+US3 (Phase 3)**: Depends on Phase 2 (coordinator filtering must work)
- **US2 (Phase 4)**: Depends on Phase 3 (options flow must exist)
- **US4 (Phase 5)**: Depends on Phase 3 (filter config must be persisted)
- **US5 (Phase 6)**: Depends on Phase 5 (refresh after cleanup, correct
  ordering)
- **Polish (Phase 7)**: Depends on all prior phases

### User Story Dependencies

- **US1+US3 (P1)**: Can start after Phase 2 — delivers core MVP listing
  selection
- **US2 (P2)**: Requires Phase 3 — extends options flow with tag filtering
- **US4 (P2)**: Requires Phase 3 — needs filter config to detect deselections;
  can run in parallel with US2
- **US5 (P3)**: Requires Phase 5 — refresh must happen after device cleanup

### Within Each User Story

- Tests MUST be written FIRST and verified to FAIL before implementation
  (Constitution I: Red-Green-Refactor)
- Implementation follows: imports/helpers → core logic → integration → error
  handling
- Each story is independently testable at its checkpoint
- Commit after each task or logical group (Constitution III: atomic commits)

### Parallel Opportunities

**Phase 1** (all different files):

- T002, T003, T004 can all run in parallel after T001

**Phase 3** (tests across files):

- T010–T017 (test_config_flow.py) can run in parallel with T018 (test_init.py)

**Phase 4 + Phase 5** (independent stories):

- Phase 4 (US2, modifies config_flow.py) and Phase 5 (US4, modifies `__init__.py`)
  can run in parallel since they touch different files

**Phase 7** (independent tools):

- T041, T042, T043 can run in parallel (different linting tools)

---

## Parallel Example: Phase 3

```text
# Launch all options flow tests together (same file, sequential within):
Task T010: "test init fetches listings" in tests/test_config_flow.py
Task T011: "test init handles API error" in tests/test_config_flow.py
Task T012: "test select_listings displays labels" in tests/test_config_flow.py
...
Task T017: "test full 3-step flow" in tests/test_config_flow.py

# IN PARALLEL with (different file):
Task T018: "test setup with no filter keys" in tests/test_init.py
```

## Parallel Example: Phase 4 + Phase 5

```text
# These phases can run in parallel (different source files):
Phase 4 tasks: modify custom_components/guesty/config_flow.py + tests/test_config_flow.py
Phase 5 tasks: modify custom_components/guesty/__init__.py + tests/test_init.py
```

---

## Implementation Strategy

### MVP First (Phase 1 + 2 + 3 Only)

1. Complete Phase 1: Setup (constants, fixtures, strings)
2. Complete Phase 2: Foundational (coordinator filtering + backward compat)
3. Complete Phase 3: US1+US3 (multi-step options flow)
4. **STOP and VALIDATE**: Test listing selection end-to-end
5. Deploy if ready — users can select listings, backward compatible, all
   existing tests pass

### Incremental Delivery

1. Phase 1 + 2 → Foundation ready (PR #1, PR #2)
2. - Phase 3 → Listing selection works → **MVP** (PR #3)
3. - Phase 4 → Tag filtering for large accounts (PR #4)
4. - Phase 5 → Clean entity lifecycle (PR #5)
5. - Phase 6 → Immediate feedback on changes (PR #6)
6. - Phase 7 → Polish and full verification (PR #7)

Each phase adds value without breaking previous phases.

### Key Files Modified Per Phase

| Phase | Files Modified |
| ----- | -------------- |
| 1 | const.py, conftest.py, strings.json, translations/en.json |
| 2 | coordinator.py, test_coordinator.py |
| 3 | config_flow.py, test_config_flow.py, test_init.py |
| 4 | config_flow.py, test_config_flow.py |
| 5 | \_\_init\_\_.py, test_init.py, test_sensor.py |
| 6 | \_\_init\_\_.py, test_init.py |
| 7 | No source changes — verification only |

---

## Notes

- **[P]** tasks = different files, no dependencies on incomplete tasks
- **[Story]** label maps task to specific user story for traceability
- US3 (backward compat) is integrated into US1 tests/implementation, not a
  standalone phase
- **api/ package**: NO CHANGES — all filtering is in the HA integration layer
  (coordinator, config flow, init)
- **Each phase = separate PR** with all pre-commit hooks passing
- **TDD**: Red → Green → Refactor for every implementation task (Constitution I)
- **Commits**: `-s` for DCO sign-off, `Co-authored-by` for AI-assisted
  (Constitution VI)
- **SPDX**: Headers on all new/modified files (Constitution IV)
- **HA helpers used**: SelectSelector, SelectSelectorConfig, SelectSelectorMode,
  SelectOptionDict, TextSelector, TextSelectorConfig, device_registry (dr),
  DataUpdateCoordinator.async_request_refresh

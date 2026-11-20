<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: V3 Listing-Local Reservation Times

**Input**: `spec.md`, `plan.md`, `research.md`, `data-model.md`,
`contracts/reservation-sensor.md`, and `quickstart.md` in
`specs/011-v3-listing-local-times/`. Tracking: #282.

**Scope**: Later implementation only; this list authorizes no live Guesty
queries or writes, release publication, or tags. The v1.0.0 hold remains.
Use fixture clients. Unit-level red-green-refactor is mandatory: write and
run each slice's tests to demonstrate failure before implementing that
slice, then refactor and run its checkpoint green. Do not commit failing
intermediate states. Keep task-list updates in separate commits from code.
Paths below are relative to the repository root.

**Format**: `[P]` marks tasks that can run concurrently in different files
after their prerequisites; `[USn]` identifies the specification story.
Tasks sharing a file or depending on an incomplete task are not parallel.
All three P1 stories are needed for safe release; US1 alone is a development
validation slice, not a production-ready partial rollout.

## Phase 1: Setup

- [ ] T001 Prepare the locked development environment with `uv sync
  --all-groups` using `pyproject.toml` and `uv.lock`; run the focused baseline
  commands in `specs/011-v3-listing-local-times/quickstart.md` without a live
  account.
- [ ] T002 Record existing source and fixture boundaries for
  `GuestyReservation.from_v3_api_dict`, `ListingsCoordinator`,
  `ReservationsCoordinator`, `ReservationIdentityGuard`, and
  `GuestyReservationSensor` in `specs/011-v3-listing-local-times/tasks.md`
  implementation notes without changing approved policies.

## Phase 2: Foundational Source and Provenance

**Purpose**: Preserve source identity and raw shape before timing is resolved.
The red tests and green implementation in this phase precede all stories.

- [ ] T003 Add failing source-provenance tests in `tests/api/test_models.py` for
  V3 versus legacy records, raw `stay` count and positions including malformed
  entries, and Feature 010 eligibility before filtering; keep legacy endpoint
  rejection unchanged.
- [ ] T004 Add failing mixed V3/legacy-fallback source tests in
  `tests/test_reservations_parity.py` proving that a resolved legacy fallback
  record is not reinterpreted as V3 when both sources occur in one refresh.
- [ ] T005 Implement explicit source and original raw count/position provenance
  in `custom_components/guesty/api/models.py` and preserve it through
  `custom_components/guesty/api/reservations_v3.py` and
  `custom_components/guesty/api/reservations_fallback.py`; retain existing
  identity and Feature 010 eligibility.
- [ ] T006 Run the T003–T004 tests plus existing Feature 008/010 and legacy
  model tests in `tests/api/test_models.py`,
  `tests/api/test_reservations_v3.py`, and `tests/test_reservations_parity.py`;
  refactor with all tests green.

## Phase 3: User Story 1 — Scheduled Local Stay Times (P1)

**Goal**: Derive V3 exact endpoints from each room's own date, default
wall time, and explicit zone, without reinterpreting legacy records.

**Independent test**: For a single exact room, fixture-driven winter,
summer and transition-spanning stays publish the expected local dates and
UTC instants, independent of raw suffixes; equivalent legacy output remains
unchanged. Run the tests named below without a Guesty account.

### Slice A: Listing inputs and pure conversion

- [ ] T007 [P] [US1] Add failing tests in `tests/api/test_models.py` for
  explicit versus missing/blank timezone provenance, explicit UTC, valid and
  malformed localized dates/defaults, offset-bearing defaults, independent
  endpoint inputs, and unchanged legacy parsing.
- [ ] T008 [P] [US1] Add failing pure-conversion tests in
  `tests/api/test_timing.py` for New York winter 16:00 → 21:00 UTC, summer 16:00
  → 20:00 UTC, endpoint-specific offsets across a DST transition, an unambiguous
  time, both fold candidates, and a spring gap.
- [ ] T009 [P] [US1] Add failing tests in `tests/test_coordinator.py` proving
  the existing paginated listing response retains an unselected exact room for
  timing while selected `data` and `known_ids` remain unchanged and no extra
  Guesty requests occur.
- [ ] T010 [US1] Preserve the listing model's compatibility timezone default
  while recording whether Guesty supplied a nonempty timezone in
  `custom_components/guesty/api/models.py`; retain valid raw default time
  fields.
- [ ] T011 [US1] Implement a typed, HA-independent resolver in
  `custom_components/guesty/api/timing.py` using prepared zone objects,
  per-endpoint dates and wall times, and UTC round trips; accept exactly one
  distinct instant and return unknown for invalid inputs, folds, and gaps.
- [ ] T012 [US1] Retain an exact-ID lookup of all records already returned by
  `get_listings()` in `custom_components/guesty/coordinator.py`, without
  expanding selected listings, attribution, pagination, fields, or request
  count.
- [ ] T013 [US1] Load/cache uncached `ZoneInfo` objects off the Home Assistant
  event loop in `custom_components/guesty/coordinator.py` and pass prepared
  zones to `custom_components/guesty/api/timing.py`; invalid zones remain
  unknown.
- [ ] T014 [US1] Run T007–T009 and `tests/api/test_timing.py` green; verify the
  resolver in `custom_components/guesty/api/timing.py` has no HA or network
  dependency.

### Slice B: Single-room end-to-end resolution

- [ ] T015 [P] [US1] Add failing V3 model tests in `tests/api/test_models.py`
  showing that raw `checkIn`/`checkOut` suffixes and
  `plannedArrival`/`plannedDeparture` never supply exact instants, while
  localized dates and planned attributes are preserved. Add a failing
  known-to-unknown candidate reload test in `tests/test_identity.py` proving
  stale endpoints are not reused or expired as current evidence.
- [ ] T016 [P] [US1] Add failing end-to-end fixture tests in
  `tests/test_reservation_sensor.py` for standard and daylight dates,
  independent endpoint offsets, retained planned attributes, and unchanged
  legacy selected/upcoming output. In `tests/test_coordinator.py` and
  `tests/api/test_reservations_fallback.py`, test missing room settings and
  known-to-unknown refresh: no crash, guessed instant, stale fallback lookup,
  or identity/write-target change.
- [ ] T017 [US1] Preserve V3 dates and per-endpoint optional exact timing
  without changing strict legacy parsing in
  `custom_components/guesty/api/models.py`; never derive exact values from raw
  V3 endpoint suffixes. In `custom_components/guesty/identity.py`, preserve
  unknown current candidates across reload without stale interval eligibility
  or time-based eviction; retain old hint compatibility.
- [ ] T018 [US1] Before activating optional endpoints, make window, projection,
  ordering, status and candidate handling null-safe in
  `custom_components/guesty/coordinator.py`, suppress unknown fallback in
  `custom_components/guesty/api/reservations_fallback.py`, and publish
  null/date/certainty and source status safely in
  `custom_components/guesty/sensor.py`; preserve identity and legacy behavior.
- [ ] T019 [US1] Activate V3 resolution only after T017–T018 in
  `custom_components/guesty/coordinator.py`, using each segment's exact-room
  lookup; retain `ReservationsCoordinator.data`, sensor keys, planned
  attributes, and the unchanged legacy and fallback source paths.
- [ ] T020 [US1] Run the US1 model, timing, coordinator, sensor and parity tests
  in `tests/api/test_models.py`, `tests/api/test_timing.py`,
  `tests/test_coordinator.py`, `tests/test_reservation_sensor.py`, and
  `tests/test_reservations_parity.py`, plus `tests/test_identity.py` and
  `tests/api/test_reservations_fallback.py`; checkpoint green before US2.

T017–T019 are one green atomic implementation slice: optional V3 endpoints
must not reach consumers until their minimum null-safe and stale-evidence
guards are in place. US3 expands the edge-case matrices and capacity policy;
it must not be used to defer these initial safety guards.

## Phase 4: User Story 2 — Moved Stays on the Right Listings (P1)

**Goal**: Preserve Feature 008 attribution and per-segment timing, with only
the owner-approved same-zone handoff adjustment.

**Independent test**: A fixture with two rooms, distinct settings and a
same-date move projects only to the existing eligible listings; qualifying
handoff uses the prior exact checkout, while every failed predicate uses
the destination's own schedule or unknown.

### Slice A: Attribution and raw adjacency

- [ ] T021 [P] [US2] Add failing tests in `tests/test_coordinator.py` for two
  tracked rooms with different zones/defaults, unselected exact child metadata
  already fetched, missing child metadata, tracked parent attribution, and no
  parent-setting substitution or added requests.
- [ ] T022 [P] [US2] Add failing tests in `tests/api/test_models.py` for
  original positions across malformed/filtered stay entries and preservation of
  per-segment localized dates and listing identities.
- [ ] T023 [US2] Update V3 segment projection in
  `custom_components/guesty/coordinator.py` to resolve exact room settings
  separately from `_attribute_segments`, preserve raw order, and leave existing
  listing attribution and distinct intervals unchanged.
- [ ] T024 [US2] Run T021–T022 and existing Feature 008 projection tests in
  `tests/test_coordinator.py` and `tests/api/test_models.py`; checkpoint green.

### Slice B: Narrow handoff

- [ ] T025 [US2] Add failing handoff tests in `tests/test_coordinator.py` for
  adjacent same-date same-explicit-zone rooms with valid settings, prior exact
  checkout and strictly later destination checkout; assert exact destination
  start, unchanged final checkout, no buffer and unchanged attribution.
- [ ] T026 [US2] Extend `tests/test_coordinator.py` with failing non-handoff
  cases for different dates/zones, missing or invalid room settings, implicit
  zone, unknown prior checkout, filtered raw gap, nonadjacency, and destination
  checkout not strictly later; assert own schedule or unknown.
- [ ] T027 [US2] Implement only the qualifying adjacent raw-position handoff in
  `custom_components/guesty/coordinator.py`; never infer a missing segment,
  extend the final checkout, or change identity or attribution.
- [ ] T028 [US2] Run moved-stay and handoff tests in `tests/test_coordinator.py`
  plus US1 tests in `tests/api/test_timing.py` and
  `tests/test_reservation_sensor.py`; checkpoint green before US3.

## Phase 5: User Story 3 — Visible Uncertainty and Identity Safety (P1)

**Goal**: Carry independent unknown endpoints through windowing, status,
publication, fallback and durable identity evidence without stale instants.

**Independent test**: A previously timed V3 reservation becomes partially
or wholly unknown, retains available dates, appears with null endpoints and
correct status/window/order, and neither initiates stale fallback nor
changes identity or write routing after persistence reload.

### Slice A: Feature 010 and per-endpoint uncertainty

- [ ] T029 [P] [US3] Add failing tests in `tests/api/test_models.py` for all
  absent/null endpoint combinations in exactly one raw segment with a valid
  chronological top-level interval, segment-then-reservation localized-date
  precedence, and existing top-level date-only fallback when both localized
  dates are absent.
- [ ] T030 [P] [US3] Add failing tests in `tests/api/test_reservations_v3.py`
  for malformed-present endpoints, multiple raw segments with one survivor,
  invalid or reversed top-level intervals, missing listing identity, and raw
  suffixes never yielding exact fallback timing.
- [ ] T031 [US3] Implement Feature 010's original raw eligibility and date
  precedence, with independent optional V3 endpoints, in
  `custom_components/guesty/api/models.py`; never fill one endpoint from the
  other or broaden attribution.
- [ ] T032 [US3] Run T029–T030 and Feature 010 regression tests in
  `tests/api/test_models.py` and `tests/api/test_reservations_v3.py`; checkpoint
  green.

### Slice B: Windows, ordering, temporal state and sensor contract

- [ ] T033 [P] [US3] Add failing tests in `tests/test_coordinator.py` for known
  instant windows; inclusive local-date lower/upper boundaries; per-segment zone
  overlap; missing date or explicit zone retaining the already-bounded V3
  result; and no stale previous-refresh instant.
- [ ] T034 [P] [US3] Add failing sensor contract tests in
  `tests/test_reservation_sensor.py` for each independently null endpoint,
  current local dates, `timing_status`, unknown projected state and separate
  `reservation_status`, selected and upcoming summaries, lifecycle selection
  priority, and unchanged planned attributes.
- [ ] T035 [US3] Extend `tests/test_coordinator.py` with failing known-first
  exact-instant ordering, unknown localized-date then reservation-ID ordering,
  deterministic absent-date handling, and unknown multi-segment temporal status
  without treating it as current/upcoming/past.
- [ ] T036 [US3] Complete the edge-case handling for `_within_window`, `_project`,
  `_select_segment`, `_segment_status`, and ordering in
  `custom_components/guesty/coordinator.py`, retaining known interval behavior
  and using inclusive date bounds only with provable dates and explicit valid
  exact-room zone.
- [ ] T037 [US3] Complete selected and upcoming nullable exact attributes,
  current dates, certainty and separate source status in
  `custom_components/guesty/sensor.py` per
  `specs/011-v3-listing-local-times/contracts/reservation-sensor.md`, preserving
  known lifecycle selection priority.
- [ ] T038 [US3] Run T033–T035 and the full sensor/coordinator regressions in
  `tests/test_coordinator.py` and `tests/test_reservation_sensor.py`; checkpoint
  green.

### Slice C: Identity evidence, fallback and persistence

- [ ] T039 [P] [US3] Add failing persistence tests in `tests/test_identity.py`
  for old two-string `CandidateHint` loading, current unknown overwriting stale
  known timing, unknown reload, deterministic old-record migration, and non-stay
  observation ordering at bounded capacity.
- [ ] T040 [P] [US3] Add failing tests in
  `tests/api/test_reservations_fallback.py` for unknown current timing
  suppressing time-windowed lookup and stale expiry, verified intervals
  retaining existing window/expiry behavior, and unchanged confirmed-absence and
  legacy fallback routing.
- [ ] T041 [P] [US3] Add failing mixed-refresh and write-guard tests in
  `tests/test_reservations_parity.py` for incomplete raw stay sequences,
  same-path conflict abort, cross-path disagreement blocking writes, and
  verified identity retaining the same permitted/refused target ID despite
  unknown timing.
- [ ] T042 [US3] Complete `CandidateHint` serialization and
  `ReservationIdentityGuard` retention/capacity in
  `custom_components/guesty/identity.py`: old hints remain readable, current
  unknown has no usable stale interval, and unknown timing alone neither expires
  nor displaces evidence; use non-stay observation data and deterministic
  migration ties.
- [ ] T043 [US3] Complete `_record_identity` and `_fallback_candidates` in
  `custom_components/guesty/coordinator.py` to mark a candidate known only when
  its required full raw sequence and endpoints are verified; preserve identity
  maps, source status and existing confirmed-absence rules.
- [ ] T044 [US3] Complete unknown-candidate gating in
  `custom_components/guesty/api/reservations_fallback.py` without changing
  verified legacy lookup windows, source routing, confirmation-code checks or
  write guard outcomes.
- [ ] T045 [US3] Run identity, fallback, mixed-source and write-safety tests in
  `tests/test_identity.py`, `tests/api/test_reservations_fallback.py`, and
  `tests/test_reservations_parity.py`; checkpoint green.

### Slice D: Privacy and end-to-end regression

- [ ] T046 [P] [US3] Add failing diagnostics tests in
  `tests/test_privacy_logging.py` for missing/invalid settings and raw V3 timing
  evidence, ensuring guest names, contacts and confirmation codes never appear
  in time-related logs.
- [ ] T047 [P] [US3] Add a fixture-driven refresh-to-reload regression in
  `tests/test_coordinator.py` covering known → partially unknown → known timing,
  selected/upcoming attributes, date windows, fallback suppression, unchanged
  identity and zero extra requests.
- [ ] T048 [US3] Add only privacy-safe timing diagnostics in
  `custom_components/guesty/api/models.py` and
  `custom_components/guesty/coordinator.py`; raw V3 values, if logged for
  evidence, must be DEBUG-only and contain no guest data or confirmation codes.
- [ ] T049 [US3] Run the T046–T047 tests and the focused fixture suite in
  `specs/011-v3-listing-local-times/quickstart.md`; checkpoint all three stories
  green.

## Phase 6: Polish and Cross-Cutting Gates

- [ ] T050 Author a `fixes` release note in
  `releasenotes/notes/v3-listing-local-times-282.yaml` describing V3 local
  scheduling, unknown/date-only behavior, unchanged legacy behavior and evidence
  limits; do not claim live verification or publish a release.
- [ ] T051 Run full fixture tests with `uv run pytest tests/`, lint with `uv run
  ruff check custom_components/ tests/ scripts/`, and type-check with `uv run
  --group releasenotes mypy custom_components/ tests/ scripts/` from
  `specs/011-v3-listing-local-times/quickstart.md`.
- [ ] T052 Run `uv run pre-commit run --all-files` and confirm interrogate,
  REUSE and all configured hooks succeed for `custom_components/guesty/`,
  `tests/`, and `releasenotes/notes/v3-listing-local-times-282.yaml`; fix
  failures without bypass.
- [ ] T053 Verify all CI checks are green before any permitted manual
  validation, and review `specs/011-v3-listing-local-times/quickstart.md` gates
  for unchanged requests, identity, attribution and write routing; no live
  Guesty operations.

## Dependencies and Execution Order

```text
T001 → T002 → T003/T004 → T005 → T006
  → US1: T007/T008/T009 → T010–T014 → T015/T016
         → T017/T018 (nullable guards) → T019 (activation) → T020
  → US2: T021/T022 → T023–T024 → T025–T028
  → US3: T029/T030 → T031–T032 → T033/T034 → T035–T038
         → T039/T040/T041 → T042–T045 → T046/T047 → T048–T049
  → T050 → T051–T053
```

The stories are ordered because they share the model and coordinator and
US2 depends on US1's resolver, while US3 integrates both into publication
and persisted evidence. A story's independent test can be run at its
checkpoint; it is not a claim that the stories are independently shippable.
For each slice, its red tests precede its production changes. Later
integration tests may wait for their stated prerequisites, but unit TDD
cannot be deferred. Each checkpoint requires green tests and CI before
proceeding or manual validation.

## Parallel Examples

- **US1**: After T006, T007, T008 and T009 touch different test files and
  can be written concurrently; after T014, T015 and T016 can run together.
- **US2**: After T020, T021 and T022 can run together; handoff tests T025
  and T026 share a file and must be sequenced.
- **US3**: After T028, T029 and T030 can run together; after T038, T039,
  T040 and T041 can run together; after T045, T046 and T047 can run together.

## Implementation Strategy and Analysis Handoff

Start with US1 as the MVP **validation** slice, then deliver US2 and US3
before considering the feature safe for release. Keep tests and production
changes in coherent, green atomic commits, and task tracking in separate
commits. This stage does not perform the formal cross-artifact analysis.

For that later analysis, retain these factual uncertainties: #282 reports
bridge evidence, not a raw response from this integration; actual child-room
coverage in already-fetched listings and V3 planned-time semantics remain
unverified. Reconcile the specification's protection of unknown identity
evidence with bounded non-stay recency ordering in the plan without treating
unknown timing alone as an eviction reason. Do not silently change either
operator policy or the existing verified-candidate expiry rules.

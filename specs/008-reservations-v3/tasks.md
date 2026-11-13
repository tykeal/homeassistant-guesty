<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Reservations v3 API Migration

**Input**: Design documents from `/specs/008-reservations-v3/`
**Prerequisites**: plan.md (required), spec.md (required for user
stories), research.md, data-model.md, contracts/
**Tracking Issue**: [#223][issue-223] | **Resolves**: [#204][issue-204]
**Hard deadline**: Guesty retires the legacy Open API reservation
resources on **2027-03-01**.

**Tests**: TDD is mandatory per constitution principle I. Every test
task MUST be written and verified FAILING (Red) before its
corresponding implementation task (Green). The repository currently
has **883 passing tests**; no phase may reduce that count or the
docstring coverage.

**Fixtures**: there are **no live Guesty payloads and no live Guesty
access**. Reservation fixtures in this repository are Python dict
builders, not JSON files (`tests/conftest.py:535-660`,
`tests/api/test_client.py:834-909`). Every v3 fixture added by this
feature MUST be a synthetic dict builder in the same style (NFR-008).

**Organization**: tasks follow the eight implementation phases of
`plan.md` rather than one phase per user story, matching the precedent
set by `specs/003-reservations/tasks.md`. User stories are labelled on
the tasks that deliver them. Three extra phases bracket the plan's
eight: Phase 0 carries blocking decisions, Phase 9 carries the
pre-stable-release live-verification gates, and Phase 10 carries the
quality gates.

## Format: `[ID] [P?] [Story?] Description`

- **\[P]**: can run in parallel (different files, no dependencies on
  an incomplete task)
- **\[US#]**: the user story the task delivers (US1–US7); omitted for
  gate, foundational and polish tasks
- **\[GATE]**: a release gate, not a code change
- Exact file paths appear in every task description

## Path Conventions

- **Source**: `custom_components/guesty/` (HA custom component)
- **API library**: `custom_components/guesty/api/` (zero HA imports,
  NFR-007)
- **Tests**: `tests/` and `tests/api/` (pytest, respx,
  pytest-homeassistant-custom-component)
- **Specs**: `specs/008-reservations-v3/`

## Commit convention for this file (MANDATORY)

`AGENTS.md` requires atomic commits and states that **task-list
updates are a separate commit from the work they track**. When a task
below is completed:

1. commit the code and its tests as one commit, e.g.
   `Feat(api): Add v3 reservation search source`;
2. commit the checkbox change in **this file alone** as a second
   commit, e.g. `Docs(tasks): Mark T037 complete`;
3. both commits ride in the **same pull request**.

Never bundle a checkbox tick into a code commit, even though both are
"documentation" by some readings. Use `git commit -s` with a
`Co-authored-by` trailer on every commit.

## Deferred contradiction — carried forward, NOT resolved here

`plan.md` § "Complexity Tracking" records that the identity guard's
**one-time legacy bootstrap sweep calls a legacy resource while the v3
path is active**. FR-001, SC-001 and FR-034 are written as absolutes
(no deprecated resource on the v3 path; no additional request for the
guard), so the plan contradicts the specification as written.

This is **deliberately deferred to the analyze stage** for a
specification amendment. It is **not** resolved by this task list.
T001 records it and T002 is the amendment; every bootstrap-sweep task
(T067, T073, T074) is **blocked on T002 landing**. Do not implement a
bootstrap sweep before the amendment exists.

Similarly, **listing-id filter chunking is designed but deliberately
not built**. FR-015 forbids a server-side listing filter, and chunking
is an OQ-008 contingency only (plan.md, research.md R6,
`contracts/guesty-reservations-v3-search.md`). **No task in this list
builds chunking**, and none may be added without first amending
FR-015.

---

## Phase 0: Gates and Decisions (blocking)

**Purpose**: settle the two recorded open items and the one documented
discrepancy before any code depends on them. Nothing here is a code
change; each task produces a recorded decision.

**Rollback**: none needed — no production code is touched.

- [ ] T001 Record the bootstrap-sweep contradiction as an
  analyze-stage finding: `plan.md` § "Identity guard" and
  § "Complexity Tracking" require one legacy page sequence at the
  first v3 setup of an upgrading entry, which contradicts FR-001,
  SC-001 and FR-034 as written in `specs/008-reservations-v3/spec.md`.
  Produce the finding in the analyze stage's output; do **not** edit
  `spec.md` in this task. Blocks T002.
- [ ] T002 Land the specification amendment arising from T001 in
  `specs/008-reservations-v3/spec.md`: scope FR-001 and SC-001 to
  steady-state **refreshes** and FR-034 to **per-refresh** cost, and
  record the bootstrap sweep as a bounded, one-time, pre-publication
  exception. Depends on T001. **Blocks T067, T073 and T074** — no
  bootstrap-sweep code may be written until this amendment is merged.
- [ ] T003 Decide OQ-009, the delivery mechanism for the FR-046
  financial-sensor deprecation notice, and record the decision and
  rationale in `specs/008-reservations-v3/research.md` as a new
  decision section during the analyze stage. **Recommendation: ship
  both — a Home Assistant repairs issue (`is_fixable=False`, severity
  `warning`, translation key `financial_sensors_deprecated`) *and* one
  `WARNING` log line per setup.** Rationale: current Home Assistant
  best practice for a breaking integration change is
  `homeassistant.helpers.issue_registry.async_create_issue`, because
  Settings → Repairs is the only in-product surface a non-technical
  user actually sees; a log line alone is reliably missed by exactly
  the dashboard-only user R-010 describes. The costs the spec names —
  a new platform surface and new translated strings — are small here
  (`repairs` needs no new entities, only `strings.json` keys the
  repository already maintains) and are deleted as a unit with the
  legacy path under FR-048, so the surface is explicitly temporary.
  The log line is retained alongside it because the repairs issue is
  dismissible and because `FR-046` requires a logged warning
  regardless. **Blocks T087, T094 and T095**; also informs T075.
- [ ] T004 Record for the analyze stage a **new, minor discrepancy**
  found while writing this task list: `plan.md` § "Diagnostics
  (FR-042)" specifies DEBUG log lines only, while
  `specs/008-reservations-v3/quickstart.md` § "Reading diagnostics"
  specifies a `reservations` block in the **config-entry diagnostics
  download**, and the repository ships no `diagnostics.py` today.
  FR-042 itself only requires a debug log, so this is an additive
  gap rather than a conflict. This task list implements both (T052,
  T091, T096); record the decision so the surface is deliberate.

**Checkpoint**: the bootstrap-sweep amendment is merged, the OQ-009
mechanism is decided and recorded, and the diagnostics surface is
agreed. Phases 1–5, 7 and 8 may proceed without T002; Phase 6's
bootstrap tasks may not.

---

## Phase 1: Toggle and Legacy Isolation (US6)

**Goal**: introduce the `reservations_api` option, extract today's
reservation retrieval into a verbatim legacy source behind a strategy
seam, and make both read and write paths flip together. The default is
`legacy` for Phases 1–7 and is flipped to `v3` in Phase 8 (T092), so
no intermediate phase can ship a half-migrated default.

**Independent Test**: toggle the option on an installation and assert
which endpoint family is called, with no entity-registry change.

**Rollback**: a pure refactor plus a new option; revert the phase's
commits. No user-visible behaviour changes while the default is
`legacy`.

### Phase 1 Tests (Red) — write first, verify they FAIL

- [ ] T005 [US6] Write tests for the `reservations_api` option in the
  options flow: the option is offered with exactly the values `v3` and
  `legacy`, an entry created before this feature resolves without a
  migration step, and an unparsable stored value resolves to `v3` with
  a WARNING, in `tests/test_config_flow.py` (FR-036, FR-038,
  contracts/options-flow-reservations-api.md).
- [ ] T006 [P] [US6] Write tests asserting the config-entry update
  listener reloads the entry when **only** `reservations_api` changes,
  covering today's early return when listing selection and polling
  intervals are unchanged (`__init__.py:723-770`), in
  `tests/test_init.py` (FR-038).
- [ ] T007 [P] [US6] Write tests for reservation **source delegation**:
  `GuestyApiClient.get_reservations()` dispatches to the legacy source
  when `reservations_api="legacy"`, the legacy source issues exactly
  the requests the previous release issued (two paginated sequences,
  `api/client.py:277-287`), and every existing legacy reservation test
  still passes against the extracted module, in
  `tests/api/test_client.py` (FR-040).
- [ ] T008 [P] [US6] Write tests for legacy-path deprecation
  signalling: a single startup WARNING naming the 2027-03-01
  retirement per setup, and a retirement-shaped failure (HTTP 404 or
  410 on `/v1/reservations/{id}`) re-raised with a message instructing
  the user to set the option back to `v3`, in `tests/test_init.py` and
  `tests/api/test_actions.py` (FR-039).
- [ ] T009 [P] [US6] Write tests for `ReservationFetchResult`: that it
  carries `reservations`, `pages`, `status_counts` and
  `segment_histogram`, and that the **legacy** source returns it and
  populates the counters it can, in `tests/api/test_models.py`
  (FR-042, data-model.md §1.6). Scope this task to the result type and
  the legacy source only — the v3 source does not exist until Phase 3,
  so the assertion that the v3 source returns the same type is
  deliberately deferred to T034/T039 and must **not** be written here,
  or the Phase 1 green checkpoint becomes unreachable.

### Phase 1 Implementation (Green)

- [ ] T010 [P] [US6] Add `CONF_RESERVATIONS_API`,
  `RESERVATIONS_API_V3`, `RESERVATIONS_API_LEGACY`,
  `DEFAULT_RESERVATIONS_API = RESERVATIONS_API_LEGACY` (flipped to v3
  by T092) and `LEGACY_RESERVATIONS_RETIREMENT_DATE = "2027-03-01"` to
  `custom_components/guesty/const.py` (FR-036, FR-039).
- [ ] T011 [US6] Add the frozen dataclass `ReservationFetchResult` and
  the `ReservationSource` protocol
  (`fetch(past_days, future_days, statuses) -> ReservationFetchResult`)
  to `custom_components/guesty/api/models.py`, with zero HA imports
  (FR-042, NFR-007). Depends on T009.
- [ ] T012 [US6] Create `custom_components/guesty/api/reservations_legacy.py`
  holding today's `get_reservations()` body
  (`api/client.py:227-390`, `api/client.py:621-648`) moved
  **verbatim** behind the `ReservationSource` protocol, so FR-040's
  behavioural identity holds by construction. Depends on T011.
- [ ] T013 [US6] Add a `reservations_api: Literal["v3", "legacy"]`
  constructor argument to `GuestyApiClient` and make
  `get_reservations()` delegate to the selected source in
  `custom_components/guesty/api/client.py`; an unrecognized value
  resolves to `v3` with a WARNING. Depends on T012.
- [ ] T014 [US6] Make `GuestyActionsClient` read the same setting from
  the client it already holds so reads and writes flip together, and
  add the retirement-shaped (404/410) legacy error message, in
  `custom_components/guesty/api/actions.py` (FR-036, FR-039). Depends
  on T013.
- [ ] T015 [US6] Add the `reservations_api` selector to the existing
  options flow in `custom_components/guesty/config_flow.py` with two
  values and the current default (FR-036, FR-038).
- [ ] T016 [P] [US6] Add the option label, description and both value
  labels to `custom_components/guesty/strings.json` and
  `custom_components/guesty/translations/en.json`.
- [ ] T017 [US6] Pass the option from `entry.options` into the client
  at setup and extend the update listener to track `reservations_api`
  alongside `_prev_selected` and `_prev_intervals`, reloading the
  entry when it changes, in `custom_components/guesty/__init__.py`
  (FR-038). Depends on T013, T015.
- [ ] T018 [P] [US6] Export `ReservationFetchResult`,
  `ReservationSource` and the legacy source from
  `custom_components/guesty/api/__init__.py`.
- [ ] T019 [US6] Log the FR-039 startup deprecation WARNING naming
  2027-03-01 exactly once per setup when the legacy path is active, in
  `custom_components/guesty/__init__.py`. Depends on T017.

**Checkpoint**: the option exists, both paths are selectable, the
legacy path is byte-identical to the previous release, and every
pre-existing reservation test is green.

---

## Phase 2: v3 Models and Mapping (US1)

**Goal**: parse a v3 reservation into the existing
`GuestyReservation` shape, add `GuestyStaySegment`, and map the v3
status vocabulary at parse time so the sensor layer never sees a new
state value.

**Depends on**: Phase 1 (the fetch-result type and the source seam).

**Rollback**: the v3 parser is unreachable until Phase 3 wires a
source to it; revert the phase's commits.

- [ ] T020 Add synthetic v3 dict fixture builders
  `make_v3_segment(**overrides)`, `make_v3_reservation(reservation_id,
  *, status, segments, guest_id, **overrides)` and
  `make_v3_search_page(results, *, skip, limit, has_more)` to
  `tests/conftest.py`, alongside the existing dict builders
  (`tests/conftest.py:535-660`). `reservationId` values MUST be
  sortable and unique because the page guard requires strictly
  ascending ids. No JSON fixture files; no live payloads exist
  (NFR-008).

### Phase 2 Tests (Red) — write first, verify they FAIL

- [ ] T021 [US1] Write tests for `GuestyStaySegment.from_api_dict`:
  full segment parses, `parentListingId` optional, a segment missing
  `listingId`, `checkIn` or `checkOut` is dropped with a debug log,
  and localized dates and `guestsCount` are preserved, in
  `tests/api/test_models.py` (data-model.md §1.1, FR-009, FR-012).
- [ ] T022 [P] [US1] Write tests for the v3 reservation parse:
  `reservationId` → `id`, `listing_id` populated from `stay[0]`,
  `money` always `None`, `stay` populated and chronological,
  `nights_count` derived from the localized dates when absent,
  `planned_arrival`/`planned_departure` publish `None` when the
  payload omits them (OQ-003), `customFields` absent rather than
  empty, a reservation with no usable segment returns `None` with a
  warning, and the status map (`completed` → `checked_out`,
  `canceled`/`cancelled` → `canceled`, `alteration` preserved
  verbatim at parse time for the coordinator to drop, unknown values
  passed through with an INFO log) in `tests/api/test_models.py`
  (FR-022 to FR-026, data-model.md §1.3, §1.4).
- [ ] T023 [P] [US1] Write tests for the FR-003a status **filter**
  translation: `checked_out` additionally requests `completed`, every
  other configured status requests itself, the configured set in
  `api/const.py:116-124` is unchanged, and the output is sorted and
  comma-joined, in `tests/api/test_models.py` (FR-003a, data-model.md
  §1.5).

### Phase 2 Implementation (Green)

- [ ] T024 [P] [US1] Add the v3 constants to
  `custom_components/guesty/api/const.py`:
  `RESERVATIONS_V3_SEARCH_ENDPOINT`, `RESERVATIONS_V3_ENDPOINT`,
  `RESERVATION_NOTES_PATH`, `RESERVATION_STATUS_PATH`,
  `MAX_V3_RESERVATION_PAGES = 200`, `V3_DATE_PAD_DAYS = 1`,
  `V3_STATUS_MAP`, `V3_STATUS_FILTER_MAP` and
  `HOLD_STATUSES = frozenset({"alteration"})`. Do **not** add
  `MAX_LISTING_IDS_PER_SEARCH`: FR-015 forbids a listing filter and
  chunking is not built (data-model.md §6).
- [ ] T025 [US1] Implement the `GuestyStaySegment` frozen dataclass
  and its `from_api_dict` factory in
  `custom_components/guesty/api/models.py` (data-model.md §1.1).
  Depends on T021.
- [ ] T026 [US1] Add `stay: tuple[GuestyStaySegment, ...] = ()` to
  `GuestyReservation` and implement the v3 parse factory, including
  the `nights_count` derivation and the `money is None` invariant, in
  `custom_components/guesty/api/models.py` (FR-022, data-model.md
  §1.2, §1.3). Depends on T025.
- [ ] T027 [US1] Implement the parse-time status map and the FR-003a
  status-filter translation helper in
  `custom_components/guesty/api/models.py`, driven by the T024
  constants (FR-023 to FR-026, FR-003a). Depends on T024, T026.
- [ ] T028 [P] [US1] Export `GuestyStaySegment` from
  `custom_components/guesty/api/__init__.py`.

**Checkpoint**: a v3 payload parses into today's model shape with
today's status vocabulary, with zero HA imports in `api/`.

---

## Phase 3: v3 Search Source (US1, US4)

**Goal**: build `V3ReservationSource` — request construction, paging,
the nine-check page guard, the HTTP 400 error and the diagnostics
counters. This is where [#204][issue-204] is actually fixed on the
request side.

**Depends on**: Phases 1 and 2.

**Rollback**: the option stays `legacy`; the v3 source is never
selected.

### Phase 3 Tests (Red) — write first, verify they FAIL

- [ ] T029 [US4] Write tests pinning the **exact outgoing query** for
  a known configuration: `filter[checkOut][gte]`,
  `filter[checkIn][lte]`, the one-day pad on each side,
  `filter[status][in]` translated/sorted/comma-joined, `sort=_id`,
  `limit=100`, `skip` stepping by 100, and the **absence** of any
  `fields` projection and of `filter[listingId]` in any form, in a new
  `tests/api/test_reservations_v3.py` using respx (FR-002, FR-003,
  FR-007, FR-015, contracts/guesty-reservations-v3-search.md).
- [ ] T030 [P] [US1] Write tests for paging and the **nine-check page
  guard**, one failing case each: missing `results`/`pagination` or a
  non-boolean `hasMore`; `pagination.skip` mismatch; `len(results) >
  limit`; `hasMore` true on an empty page; `hasMore` true on a short
  page; a missing or non-string `reservationId`; a duplicate
  `reservationId` across pages; a non-ascending `reservationId`; and
  `hasMore` still true after `MAX_V3_RESERVATION_PAGES`. Each MUST
  raise, publish nothing and leave last-known-good data intact, in
  `tests/api/test_reservations_v3.py` (FR-005, FR-006, NFR-005).
- [ ] T031 [P] [US1] Write tests asserting an HTTP 400 raises
  `GuestyFilterRejectedError` carrying the endpoint, status and a
  truncated body excerpt, that it is surfaced to the user through
  `UpdateFailed` with the detail intact, and that the request is
  **never** retried with filters removed, in
  `tests/api/test_reservations_v3.py` and `tests/test_coordinator.py`
  (FR-008, US4 scenario 3).
- [ ] T032 [P] [US4] Write frozen-clock window tests: a configured
  30-past/365-future window excludes out-of-window fixtures present in
  the fake backend, and a stay whose check-in is 200 days in the past
  and which is still in progress **is** returned by the overlap
  filter, in `tests/api/test_reservations_v3.py` (FR-002, FR-004,
  SC-004, SC-005). These close [#204][issue-204] on the request side.
- [ ] T033 [P] [US1] Write a test asserting that **no request targets
  `/v1/reservations` or any other resource the migration guide marks
  deprecated** while the v3 path is active, over a full refresh, in
  `tests/api/test_reservations_v3.py` (FR-001, SC-001). Note in the
  test docstring that the one-time identity bootstrap sweep (T073) is
  the single qualified exception, pending the T002 amendment.
- [ ] T034 [P] [US1] Write tests asserting the diagnostics counters on
  `ReservationFetchResult`: pages fetched, raw (pre-mapping) status
  histogram, and the stay-segment-count histogram, in
  `tests/api/test_reservations_v3.py` (FR-042).
- [ ] T035 [P] [US1] Write tests for `GuestyFilterRejectedError`:
  derives from `GuestyResponseError`, carries endpoint, status and a
  truncated excerpt, and truncates a long body, in
  `tests/api/test_exceptions.py` (FR-008, data-model.md §7).

### Phase 3 Implementation (Green)

- [ ] T036 [US1] Implement `GuestyFilterRejectedError` in
  `custom_components/guesty/api/exceptions.py` (data-model.md §7).
  Depends on T035.
- [ ] T037 [US1] Implement request construction in a new
  `custom_components/guesty/api/reservations_v3.py`: the padded
  overlap filter, the translated status filter, `sort=_id`,
  `limit=100`, `skip` paging, and an explicit refusal to emit a
  `fields` or `filter[listingId]` parameter (FR-001 to FR-004,
  FR-007, FR-015). Depends on T024, T027, T029.
- [ ] T038 [US1] Implement paging and the nine-check page guard in
  `custom_components/guesty/api/reservations_v3.py`, raising on any
  failed check so the coordinator retains last-known-good data
  (FR-005, FR-006). Depends on T037.
- [ ] T039 [US1] Implement result mapping and the diagnostics counters
  in `custom_components/guesty/api/reservations_v3.py`, returning a
  `ReservationFetchResult`; raise `GuestyFilterRejectedError` on HTTP
  400 with no unfiltered retry (FR-008, FR-042). Depends on T036,
  T038.
- [ ] T040 [US1] Select `V3ReservationSource` in
  `custom_components/guesty/api/client.py` when
  `reservations_api="v3"`, and convert `GuestyFilterRejectedError` to
  `UpdateFailed` with the rejection detail preserved in
  `custom_components/guesty/coordinator.py` (FR-008). Depends on T039.

**Checkpoint**: the v3 search is callable, pages deterministically,
fails loudly on any inconsistency, and emits counters — all against
synthetic dict fixtures.

---

## Phase 4: Attribution, Projection, Segment State and Trim (US1)

**Goal**: derive listing attribution from `stay[]`, emit per-listing
projections so `sensor.py` is untouched, make state segment-aware, and
add the **new** instant-based client-side window trim on both paths.

**Depends on**: Phases 2 and 3.

**Rollback**: the option stays `legacy`; the trim is the one
deliberate legacy-path improvement and only ever narrows what is
published, so it cannot resurrect a dropped reservation.

### Phase 4 Tests (Red) — write first, verify they FAIL

- [ ] T041 [US1] Write attribution tests: a single-segment
  reservation; a two-segment reservation attributed to **both**
  tracked listings; a segment resolving only through
  `parentListingId`; a partly-unknown reservation published with its
  unknown segment logged at debug; a wholly-unknown reservation
  skipped with a warning; and a reservation with no `stay` at all
  skipped with a warning, in `tests/test_coordinator.py` (FR-009,
  FR-010, FR-012, FR-013, SC-006).
- [ ] T042 [P] [US1] Write per-listing projection tests: a
  single-segment reservation projects to itself field for field; a
  multi-segment reservation substitutes that listing's segment dates,
  localized dates, `guests_count` and derived `nights_count`; `id`,
  `confirmation_code`, `source`, `guest` and `custom_fields` are
  always the reservation's own; `stay` is empty on a projection; and
  an **A → B → A** stay keeps disjoint intervals and selects the
  interval containing the refresh instant, else the earliest future
  interval, else the latest past one, in `tests/test_coordinator.py`
  (FR-011, data-model.md §3.1).
- [ ] T043 [P] [US1] Write frozen-clock segment-aware state tests
  (FR-011a, SC-012): before a mid-stay move the reservation is
  `checked_in` on A and `awaiting_checkin` on B without displacing B's
  current occupant; after the move it is `checked_out` on A and
  `checked_in` on B at the same refresh; and the same reservation when
  `canceled` reports `canceled` on both listings and never
  `awaiting_checkin`. Assert `_STATUS_PRIORITY` in
  `custom_components/guesty/sensor.py` is **unchanged**, in
  `tests/test_coordinator.py` and `tests/test_reservation_sensor.py`.
- [ ] T044 [P] [US1] Write frozen-clock trim tests (FR-002a): padded
  boundary results are dropped, an in-progress stay is **never**
  dropped regardless of how far in the past it began, and the trim
  applies on **both** the legacy and v3 paths, in
  `tests/test_coordinator.py`.
- [ ] T045 [P] [US1] Write tests asserting an `alteration` reservation
  is dropped by the coordinator with a debug log and a diagnostics
  counter, is never selected as a sensor state, and never displaces a
  published reservation for the same listing, in
  `tests/test_coordinator.py` and `tests/test_reservation_sensor.py`
  (FR-024).
- [ ] T046 [P] [US1] Write a test asserting a listing excluded by
  Feature 007 listing filtering publishes no reservations, and that
  the reservation layer performs **no** filtering of its own, in
  `tests/test_coordinator.py` (FR-014).
- [ ] T047 [P] [US1] Write tests asserting the reservation status
  sensor's attribute **key set and names** are unchanged when fed
  projections — reservation id, UTC and localized check-in/check-out,
  planned arrival/departure, guest name/phone/email, confirmation
  code, guest count, nights count, source, listing id,
  `upcoming_reservations` and custom fields — and that the state
  vocabulary is exactly `no_reservation`, `awaiting_checkin`,
  `checked_in`, `checked_out`, `canceled` plus pass-through, in
  `tests/test_reservation_sensor.py` (FR-025, NFR-003, NFR-004).

### Phase 4 Implementation (Green)

- [ ] T048 [US1] Implement segment attribution in
  `custom_components/guesty/coordinator.py`: attribute to
  `segment.listing_id` when known, else `segment.parent_listing_id`
  when known, else ignore with a debug log; publish a reservation with
  at least one attributable segment; skip with a warning when none
  resolves or no segments exist; intersect with
  `listings_coordinator.data` so Feature 007 continues to decide
  tracking; and drop `alteration` reservations with a debug log
  (FR-009, FR-010, FR-012, FR-013, FR-014, FR-024). Depends on T041,
  T045, T046.
- [ ] T049 [US1] Implement the per-listing projection in
  `custom_components/guesty/coordinator.py`, keeping segments in the
  same listing as disjoint intervals and selecting the interval per
  data-model.md §3.1, so `ReservationsCoordinator.data` keeps its type
  `dict[str, list[GuestyReservation]]` (FR-011, NFR-003, NFR-004).
  Depends on T048.
- [ ] T050 [US1] Implement segment-aware status derivation in
  `custom_components/guesty/coordinator.py`, applied **only** when the
  reservation-level status is `confirmed` or `checked_in` and the
  reservation has more than one segment; every other status is carried
  unchanged to every listing it touches (FR-011a). Do not modify
  `_STATUS_PRIORITY` in `sensor.py`. Depends on T049.
- [ ] T051 [US1] Implement the instant-based window trim
  (`keep ⇔ check_out > now − past_days and check_in < now +
  future_days`) in `custom_components/guesty/coordinator.py`, applied
  on **both** paths after mapping and before publication (FR-002a,
  FR-004). Depends on T049.
- [ ] T052 [US1] Emit the FR-042 per-refresh DEBUG diagnostics in
  `custom_components/guesty/coordinator.py` with **no personal data**:
  pages per resource, reservation count by raw status, the
  stay-segment histogram, attributed/partially-attributed/skipped
  counts, `alteration` drops and trim drops (FR-021, FR-042, NFR-009).
  Depends on T048, T051.

**Checkpoint**: multi-segment reservations appear correctly on every
tracked listing with per-listing dates and states; the configured
window is now genuinely authoritative on both paths.

---

## Phase 5: Guest Lookup and Cache (US2)

**Goal**: re-source `guest_name`, `guest_phone` and `guest_email` from
the `/guests` family behind a bounded, deduplicated, TTL cache, within
the NFR-001 request budget and without ever logging guest data.

**Depends on**: Phases 3 and 4 (lookups are issued only for
reservations attributed to a tracked listing).

**Rollback**: the v3 path only; the legacy path still embeds guests.

### Phase 5 Tests (Red) — write first, verify they FAIL

- [ ] T053 [US2] Write `GuestCache` unit tests in a new
  `tests/api/test_guest_cache.py`: a hit within
  `GUEST_CACHE_TTL_SECONDS` issues no request; an expired hit refetches
  and replaces; a 404 stores a negative entry honouring
  `GUEST_CACHE_MISS_TTL_SECONDS` and does not refetch within it;
  `GUEST_CACHE_MAX_ENTRIES` evicts LRU rather than growing;
  `MAX_GUEST_LOOKUPS_PER_REFRESH` defers the remainder oldest-first
  rather than bursting; `GUEST_LOOKUP_CONCURRENCY` bounds in-flight
  requests; one lookup per distinct guest per refresh; and a changed
  `guest_id` never republishes the previous guest's values (FR-018,
  FR-019, NFR-002, NFR-006, SC-010).
- [ ] T054 [P] [US2] Write guest-lookup contract tests in
  `tests/api/test_guest_cache.py`: the exact path
  `/v1/guests-crud/{guestId}` with `fields=fullName phone email`; a
  200 maps into the existing `GuestyGuest` dataclass with `guest_id`
  set from the requested id; a 404 publishes `None` guest attributes
  while still publishing the reservation; and any other failure,
  transport error or post-retry 429 **retains the previous value**,
  logs without PII and never fails the refresh (FR-016, FR-020,
  NFR-005, SC-009).
- [ ] T055 [P] [US2] Write a test asserting lookups are issued
  **only** for reservations attributed to a tracked listing, so
  untracked reservations returned by the completeness-preserving
  search cost nothing beyond their page, and none is issued for a
  reservation with no `guestId`, in `tests/test_coordinator.py`
  (NFR-001).
- [ ] T056 [P] [US2] Write request-budget tests counting transport
  calls in `tests/api/test_reservations_v3.py`: with a **warm** cache
  and no reservation changes the v3 path issues no more requests than
  the legacy path for the same fixture account; with a **cold** cache
  a fixture of ten reservations for ten distinct guests issues exactly
  the search pages plus ten, asserted as a pinned number (NFR-001,
  SC-003).
- [ ] T057 [P] [US2] Write a privacy test in a new
  `tests/test_privacy_logging.py` asserting that **no** guest name,
  phone number, email address or confirmation code appears in captured
  log records at DEBUG across a full v3 refresh and a guest-lookup
  failure (FR-021, NFR-009). The identity-guard abort scenario is
  **not** part of this task: the abort path does not exist until T070
  in Phase 6, and Phases 5 and 6 may run independently. T066a extends
  this same file with the identity-abort privacy scenario.
- [ ] T058 [P] [US2] Write tests asserting the sensor publishes
  `guest_name`, `guest_phone` and `guest_email` under their existing
  attribute names from the cache, that the same guest appearing on
  several reservations in one refresh is looked up at most once, and
  that a stale-but-cached guest keeps publishing during an outage, in
  `tests/test_reservation_sensor.py` (FR-017, NFR-004, SC-009).

### Phase 5 Implementation (Green)

- [ ] T059 [US2] Implement `GuestCacheEntry` and `GuestCache` in a new
  `custom_components/guesty/api/guest_cache.py` with zero HA imports:
  keyed on `guest_id` only so FR-019 holds structurally; TTL, miss
  TTL, LRU bound, per-refresh dedup, burst cap and bounded
  concurrency; failures retain the previous value; nothing but the
  guest id and counts is ever logged (FR-016, FR-018 to FR-021,
  NFR-002, NFR-005 to NFR-007, NFR-009). Depends on T053, T054.
- [ ] T060 [P] [US2] Add `GUESTS_CRUD_PATH`, `GUEST_FIELDS`,
  `GUEST_CACHE_TTL_SECONDS = 86400`,
  `GUEST_CACHE_MISS_TTL_SECONDS = 3600`,
  `GUEST_CACHE_MAX_ENTRIES = 512`,
  `MAX_GUEST_LOOKUPS_PER_REFRESH = 50` and
  `GUEST_LOOKUP_CONCURRENCY = 4` to
  `custom_components/guesty/api/const.py` (data-model.md §2.2, §6).
- [ ] T061 [US2] Wire the cache onto the API client instance and
  populate guest attributes for reservations attributed to tracked
  listings, in `custom_components/guesty/api/client.py` and
  `custom_components/guesty/coordinator.py`; add the guest-cache
  diagnostics counters (hits, lookups, failures, deferrals) to the
  FR-042 output (FR-016, FR-017, FR-042, NFR-001). Depends on T059,
  T060.
- [ ] T062 [P] [US2] Export `GuestCache` from
  `custom_components/guesty/api/__init__.py`.

**Checkpoint**: guest attributes are preserved under today's names,
one lookup per distinct guest per refresh, warm-cache budget no worse
than legacy, and no guest data in any log.

---

## Phase 6: Identity Guard (US5)

**Goal**: make a wrong-identity write impossible. A persisted
confirmation-code baseline, a per-code comparison that catches the
swap case, fail-closed writes at **all three** write entry points, and
the FR-035 match log.

**Depends on**: Phases 3 and 4. The bootstrap tasks additionally
depend on **T002** (the spec amendment) and must not be started
before it lands.

**Rollback**: the v3 path only. If the guard proves too strict on a
real account, the user sets `reservations_api=legacy` and reloads.

### Phase 6 Tests (Red) — write first, verify they FAIL

- [ ] T063 [US5] Write tests for the persisted baseline in a new
  `tests/test_identity.py`: a `Store` round-trip at
  `guesty.{entry_id}.reservations_identity` version 1 carrying
  `path`, `continuity`, `updated`, `codes` and `bootstrap_codes`;
  survival across a Home Assistant restart and a config-entry reload;
  the 5,000-code cap evicting earliest check-out first; and the
  baseline being established from whichever path produced the last
  successful refresh (FR-031a, FR-031b, SC-013, data-model.md §4).
- [ ] T064 [P] [US5] Write guard comparison tests in
  `tests/test_identity.py`: an exact match continues normally; a
  changed code→id mapping aborts the refresh, retains
  last-known-good data, performs no write and logs **one** actionable
  error naming both identifiers and the rollback option; the
  **swap** case (baseline `A→id1`, `B→id2`; refresh `A→id2`,
  `B→id1`, both ids still present) also aborts; a reservation with no
  confirmation code is published but is not a write target; and the
  comparison costs **zero** additional API requests (FR-031, FR-032,
  FR-033, FR-034, SC-007).
- [ ] T065 [P] [US5] Write fail-closed write tests covering **all
  three** entry points — `add_reservation_note`
  (`custom_components/guesty/actions.py:183-220`), the status action
  (`custom_components/guesty/actions.py:342`) and the separate
  `guesty.set_custom_field` handler's `target_type="reservation"`
  branch (`custom_components/guesty/__init__.py:395-401`) — asserting
  each conjunct of the write predicate independently: no usable
  baseline; `continuity == "unconfirmed"` despite a baseline existing;
  the last refresh aborted as ambiguous; and a target with no
  confirmation code. Each MUST abort with an explanatory error and
  send **no request**, and the predicate MUST be re-evaluated from the
  `Store` after a reload. In `tests/test_actions.py` and
  `tests/test_custom_field_service.py` (FR-031c, FR-032, FR-033,
  SC-013).
- [ ] T066 [P] [US5] Write a test asserting the first successful v3
  refresh logs at INFO how many previously known reservation
  identifiers matched, and that the count appears in diagnostics
  without exposing any confirmation code value, in
  `tests/test_identity.py` (FR-021, FR-035, FR-042).
- [ ] T066a [P] [US5] Extend `tests/test_privacy_logging.py` (created
  at T057) with the identity-guard abort scenario: assert that an
  aborted refresh emits no guest name, phone number, email address or
  confirmation code at DEBUG. Deferred here from T057 because the
  abort path does not exist until T070 (FR-021, NFR-009).
- [ ] T067 [US5] **BLOCKED on T002.** Write bootstrap-sweep tests in
  `tests/test_identity.py`: a fresh install (no baseline, no
  reservation entities) is `not_applicable` and permits writes; an
  upgrade (no baseline, reservation entities present) performs one
  legacy sweep and stores it as `bootstrap_codes` with
  `path: "legacy"`; an **empty or disjoint** comparison leaves
  continuity `unconfirmed`, retains the bootstrap evidence and keeps
  writes blocked; a **partial overlap** retires only the codes it
  covered and still catches a later conflict on an uncovered code
  across a reload; an unavailable, rate-limited or erroring sweep
  leaves continuity `unconfirmed` with writes refused and a notice
  raised; and a disagreeing bootstrap aborts exactly as FR-032
  requires. Also assert the sweep runs **exactly once** for an
  upgrading entry and **zero** times otherwise.

### Phase 6 Implementation (Green)

- [ ] T068 [US5] Implement the `Store`-backed baseline in a new
  `custom_components/guesty/identity.py`: schema version 1, the
  fields and size bound of data-model.md §4, and read-once-per-setup
  into the coordinator. Use `Store` rather than `entry.data` so a
  per-refresh write does not fire the update listener and reload the
  entry every 15 minutes (FR-031a, FR-031b). Depends on T063.
- [ ] T069 [US5] Implement the per-code comparison, including the
  swap case, in `custom_components/guesty/identity.py`, costing zero
  additional requests (FR-031, FR-032, FR-034). Depends on T068.
- [ ] T070 [US5] Integrate the check into
  `custom_components/guesty/coordinator.py` before publication:
  abort via `UpdateFailed`, retain last-known-good data, publish
  nothing, block writes and log one error naming both identifiers and
  the rollback instruction — never a confirmation code value (FR-021,
  FR-032, SC-007). Depends on T069.
- [ ] T071 [US5] Enforce the four-conjunct write predicate in
  `custom_components/guesty/actions.py` for `add_reservation_note` and
  the status action, refusing with an explanatory `GuestyActionError`
  and sending no request. Keep the guard in the HA service layer so
  `api/` retains zero HA imports (FR-031c, FR-033, NFR-007). Depends
  on T069.
- [ ] T072 [US5] Enforce the same predicate on the
  `target_type="reservation"` branch of the `guesty.set_custom_field`
  handler in `custom_components/guesty/__init__.py`; listing writes
  and the HTTP resource itself are unchanged (FR-030, FR-031c).
  Depends on T071.
- [ ] T073 [US5] **BLOCKED on T002.** Implement the one-time legacy
  bootstrap sweep for an upgrading entry in
  `custom_components/guesty/identity.py` and
  `custom_components/guesty/__init__.py`: one legacy page sequence,
  before any v3 data is published, once per entry and never again,
  stored as `bootstrap_codes` with `path: "legacy"`. Continuity
  becomes `verified` only when the comparison is **informative** — at
  least one shared confirmation code and no conflict. Depends on
  T068, T067.
- [ ] T074 [US5] **BLOCKED on T002.** Implement bootstrap retention
  and **individual** retirement in
  `custom_components/guesty/identity.py`: a successful refresh
  replaces `codes` but never `bootstrap_codes`; a covered,
  non-conflicting code is retired one at a time; an uncovered code is
  retained; reaching `verified` does not clear the remainder; and an
  unchecked entry is retired only under the safe policy that its
  reservation checked out more than `past_days` ago. Depends on T073.
- [ ] T075 [US5] Surface an "identity continuity unconfirmed" notice
  through the mechanism chosen in T003 when the bootstrap is
  unavailable or uninformative, explaining that writes are refused and
  how to proceed, in `custom_components/guesty/__init__.py` plus
  `strings.json` and `translations/en.json` (FR-031c). Depends on
  T003, T073.
- [ ] T076 [US5] Log the FR-035 identity match count at INFO once per
  path activation and add it, plus an
  "ambiguous identity aborted the last refresh" flag, to the FR-042
  diagnostics, in `custom_components/guesty/coordinator.py` (FR-035,
  FR-042). Depends on T070.

**Checkpoint**: an ambiguous-identity fixture produces zero writes,
zero published changes and exactly one actionable error; writes are
refused until continuity is established; the baseline survives a
reload.

---

## Phase 7: v3 Write Actions (US3)

**Goal**: move `add_reservation_note` and the reservation-status
action onto the dedicated v3 resources, preserving today's append
behaviour, validation and error shape.

**Depends on**: Phases 1 and 6 (every write passes the guard first).

**Rollback**: the option stays `legacy`; both actions revert to the
previous release's exact requests.

### Phase 7 Tests (Red) — write first, verify they FAIL

- [ ] T077 [US3] Write note-action tests in `tests/api/test_actions.py`
  using respx: the read targets
  `GET /v1/reservations-v3?reservationIds={id}` (the coordinator's
  cached copy is **not** used as the read source); the write targets
  `PUT /v1/reservations-v3/{id}/notes`; the body echoes **every**
  writable note field captured by the read, so a multi-category
  fixture carrying both `notes.other` and `notes.guest` survives the
  write; the existing text is preserved and appended to with the
  unchanged `NOTE_SEPARATOR` (`api/const.py:190-191`); and a non-2xx
  **read** raises without attempting a write (FR-027, FR-029, US3
  scenario 1).
- [ ] T078 [P] [US3] Write status-action tests asserting
  `PUT /v1/reservations-v3/{id}/status` with body
  `{"status": "checked_in"}` or `{"status": "checked_out"}`, accepted
  values unchanged from `VALID_RESERVATION_STATUSES`
  (`api/const.py:185-188`), and validation unchanged
  (`api/actions.py:441-470`), in `tests/api/test_actions.py` (FR-028).
- [ ] T079 [P] [US3] Write rejection tests asserting a non-2xx write
  raises `GuestyActionError` naming the resource and the rejection
  detail with a truncated body, and is **never** reported as a
  success, for both actions, in `tests/api/test_actions.py` (FR-029,
  US3 scenario 3).
- [ ] T080 [P] [US3] Write tests asserting that with
  `reservations_api="legacy"` both actions still target
  `/v1/reservations/{id}` exactly as the previous release did, in
  `tests/api/test_actions.py` (FR-040).
- [ ] T081 [P] [US3] Write regression tests asserting reservation
  custom-field reads and writes still use
  `/reservations-v3/{id}/custom-fields` unchanged
  (`api/const.py:142-143`, `api/custom_fields.py:116-141`,
  `api/custom_fields.py:200-260`), in `tests/api/test_custom_fields.py`
  (FR-030).

### Phase 7 Implementation (Green)

- [ ] T082 [US3] Replace the legacy note **read** with
  `GET /v1/reservations-v3?reservationIds={id}`, capturing the whole
  `notes` object rather than only `other`, in
  `custom_components/guesty/api/actions.py` (FR-027). Depends on T077.
- [ ] T083 [US3] Replace the legacy note **write** with
  `PUT /v1/reservations-v3/{id}/notes`, sending every captured note
  field back with `other` appended to using the unchanged separator,
  and keeping today's validation (`api/actions.py:418-440`) and error
  construction (`api/actions.py:389-414`), in
  `custom_components/guesty/api/actions.py` (FR-027, FR-029). Depends
  on T082.
- [ ] T084 [US3] Replace the legacy status write with
  `PUT /v1/reservations-v3/{id}/status`, preserving accepted values
  and validation, in `custom_components/guesty/api/actions.py`
  (FR-028, FR-029). Depends on T078.

**Checkpoint**: both write actions target the dedicated v3 resources,
preserve existing note text, fail loudly on rejection, and are gated
by the identity guard.

---

## Phase 8: Default Flip, Deprecation and Docs (US1, US6, US7)

**Goal**: make `v3` the default, land the financial-sensor
deprecation cleanly, expose the diagnostics surface, and document the
breaking change. This is the phase that makes the migration
user-visible.

**Depends on**: Phases 1–7, plus **T003** for the notice mechanism.

**Rollback**: a user sets `reservations_api` to `legacy` and reloads;
retrieval and both write actions return to the previous release's
behaviour, entities are untouched, and the three financial sensors
resume reporting values.

### Phase 8 Tests (Red) — write first, verify they FAIL

- [ ] T085 [US6] Write tests asserting the default is `v3` for both a
  fresh installation and an upgraded entry that stores no value, with
  no migration step and no reconfiguration prompt, in
  `tests/test_config_flow.py` and `tests/test_init.py` (FR-037).
- [ ] T086 [P] [US7] Write financial-sensor deprecation tests over a
  fixture whose **legacy** payload carries `money` values:
  `reservation_total`, `reservation_balance` and
  `reservation_currency` report those values on the legacy path and
  report `unavailable` on the v3 path; they never publish a value
  carried over from an earlier legacy refresh, a cached value or a
  placeholder; and their entity ids, unique ids, device assignment and
  entity category are identical on both paths, in
  `tests/test_reservation_sensor.py` (FR-044, FR-045, FR-049, SC-014).
- [ ] T087 [P] [US7] Write deprecation-notice tests for the mechanism
  chosen in T003: the notice is surfaced **exactly once** per config
  entry per Home Assistant start when the v3 path is active **and** at
  least one of the three entities already exists in the entity
  registry; it is **not** raised when none exists; it names the three
  sensors, the reason and the FR-048 removal timeline; and a WARNING
  is logged in the same case, in `tests/test_init.py` (FR-046,
  SC-014). Depends on T003.
- [ ] T088 [P] [US7] Write a test asserting **no request is made to
  any guest-folio or payments resource on either path**, over a full
  refresh, in `tests/test_coordinator.py` (FR-043, SC-014).
- [ ] T089 [P] [US6] Write a test asserting that toggling
  `reservations_api` and reloading an installation whose entities are
  all already registered produces **zero** entity-registry changes,
  and that the one permitted change is the first-time registration of
  the three financial sensors on an installation that has never run
  the legacy path and is then switched to it, in `tests/test_init.py`
  (FR-038, FR-045, NFR-003, SC-008).
- [ ] T090 [P] [US1] Write the legacy/v3 **differential** test in a
  new `tests/test_reservations_parity.py`: one shared fixture account
  expressed in both legacy and v3 shapes produces identical entity
  ids, states and attribute key sets on both paths. List the three
  financial sensors and any unverified-attribute exception
  **explicitly** in the test rather than tolerating them with a loose
  comparison; their entity ids are still compared (SC-002, NFR-003,
  NFR-004, FR-049).
- [ ] T091 [P] Write tests for the config-entry diagnostics download
  in a new `tests/test_diagnostics.py`: the `reservations` block
  carries the active path, pages fetched, reservations received,
  trimmed and skipped, the raw status histogram, the segment-count
  histogram, unknown-listing counts, guest lookups, cache hits and
  failures, and the identity-mismatch flag — and carries **no** guest
  details and **no** confirmation codes (FR-021, FR-042, NFR-009).
  Depends on T004.

### Phase 8 Implementation (Green)

- [ ] T092 [US6] Flip `DEFAULT_RESERVATIONS_API` to
  `RESERVATIONS_API_V3` in `custom_components/guesty/const.py`
  (FR-037). Depends on T085 and on Phases 1–7 being green.
- [ ] T093 [US7] Ensure the three financial sensors are **not created**
  on the v3 path and publish no carried-over, cached or placeholder
  value, while entity-registry entries created by earlier releases are
  **retained** so Home Assistant reports them `unavailable`, in
  `custom_components/guesty/sensor.py`. No other entity or attribute
  may regress, and the absence of financial data must not fail, delay
  or degrade a refresh (FR-044, FR-045, FR-049). Depends on T086.
- [ ] T094 [US7] Implement the FR-046 deprecation notice using the
  T003 mechanism in `custom_components/guesty/__init__.py`: if the
  repairs issue is adopted, raise it with
  `homeassistant.helpers.issue_registry.async_create_issue` and add
  the translated strings to `strings.json` and
  `translations/en.json`. A non-fixable issue needs **no**
  `manifest.json` change and **no** `repairs.py` module; add
  `custom_components/guesty/repairs.py` only if a real repair *flow*
  is ever required. Raise at most once per config entry per start,
  only when the v3 path is active and at least one of the three
  entities exists, naming the three sensors, the reason, the rollback
  option and the removal timeline, with a matching WARNING log line.
  Depends on T003, T087.
- [ ] T095 [US7] Add the notice's translated strings to
  `custom_components/guesty/strings.json` and
  `custom_components/guesty/translations/en.json` under
  `financial_sensors_deprecated`, so they are removable as a unit
  under FR-048. Depends on T094.
- [ ] T096 Implement the config-entry diagnostics platform in a new
  `custom_components/guesty/diagnostics.py`, exposing the
  `reservations` block of quickstart.md § "Reading diagnostics" with
  no guest details and no confirmation codes (FR-042). Depends on
  T091.
- [ ] T097 [US7] Document the migration in `README.md`: the
  `reservations_api` option and its values, the 2027-03-01 retirement,
  the identity guard's fail-closed write behaviour, and a
  **Breaking changes** section naming `total_paid`, `balance_due` and
  `currency`, stating that no replacement data source is provided and
  that the legacy rollback itself expires on 2027-03-01 (FR-047,
  NFR-004).
- [ ] T098 [US7] Prepare the release-notes entry: apply the
  `breaking-change` label to the implementation pull request so
  release-drafter files it under "💥 Breaking Change 💥"
  (`.github/release-drafter.yml`), and write the draft note body
  naming the three deprecated sensors, the v3 migration, the
  `reservations_api` rollback and the FR-048 removal timeline
  (FR-047, FR-048).

**Checkpoint**: new and upgraded installations default to v3; the
financial sensors are unavailable rather than stale and the user has
been told why; entity ids, states and attribute keys are provably
identical across both paths.

---

## Phase 9: Pre-Stable-Release Live-Verification Gates

**Purpose**: FR-041 and SC-011. **Every v3 behavioural claim in the
specification and plan is documentation-derived and UNVERIFIED, and no
live Guesty access exists for this project.** None of these items can
be confirmed during implementation. They are **release gates**: the
feature may merge and ship as pre-release without them, but it MUST
NOT be declared stable until each is worked through against a live
Guesty account.

**Depends on**: Phase 8 (the FR-042 diagnostics are how most items are
checked from a user's debug log without a test account).

**Rollback**: any failed item is resolved by the recorded fallback in
`research.md`, or by instructing affected users to set
`reservations_api=legacy` while a fix is prepared.

- [ ] T099 [GATE] Verify the **identity and continuity** items of
  `specs/008-reservations-v3/research.md` § "Pre-release
  live-verification checklist": legacy `_id` equals v3
  `reservationId`; the confirmation code is stable and unique enough;
  and custom-field ids and values are unaffected. **Acceptance
  requires a non-zero overlap of confirmation codes covering the same
  stays on both paths** — two successful refreshes with no overlap
  confirm nothing (FR-041, SC-011).
- [ ] T100 [GATE] Verify the **search resource** items: the endpoint
  and bracketed filters; that `filter[checkOut][gte]` and
  `filter[checkIn][lte]` can be combined and are ANDed (R3 — adopt the
  recorded two-query fallback if not); that date filters compare
  listing-localized dates so the one-day pad is required and
  sufficient; that an unsupported filter returns HTTP 400; that no
  projection is supported and `status` is always present; that a page
  is never short while `hasMore` is true and `sort=_id` is strictly
  ascending; that `filter[status][in]` accepts `completed`; and that a
  large account stays within the 200-page cap and the rate budget
  (OQ-005, OQ-008, FR-041).
- [ ] T101 [GATE] Verify the **result shape** items: a non-empty
  chronological `stay[]` with `listingId`, optional
  `parentListingId`, UTC and localized dates and a guest count; no
  top-level `listingId`/`unitId`/`unitTypeId`/`mtl` and no
  `lastUpdatedAt`; `guestId`, `confirmationCode`, `source`, `notes`
  and `customFields` present with `customFields` absent when unset;
  the documented status set; and `nightsCount` absent. **If
  `plannedArrival`/`plannedDeparture` are absent (OQ-003), their loss
  is a breaking change that NFR-004 requires to be declared in the
  specification and release notes, not absorbed** — raise it as a
  follow-up rather than closing this gate silently (FR-041, NFR-004).
- [ ] T102 [GATE] Verify the **guest** items: `GET /v1/guests-crud/{id}`
  with `fields=fullName phone email` returns values equivalent to
  today's embedded guest object, and a deleted or unknown guest id
  returns a not-found response rather than a refresh-failing error. If
  rejected, swap to the recorded `GET /v1/guests/{id}` fallback; the
  cache and every budget rule are unchanged by the swap (OQ-004,
  FR-041).
- [ ] T103 [GATE] Verify the **write resource** items by deliberate
  action invocations on a real account: the notes resource accepts the
  content and body shape assumed (`notes.other`); whether it replaces
  or merges the note set (OQ-002, which decides whether the
  read-before-write can ever be dropped);
  `GET /v1/reservations-v3?reservationIds={id}` returns the current
  note text; and the status resource accepts `checked_in` and
  `checked_out` (FR-041).
- [ ] T104 [GATE] Confirm and **document** whether a group booking is
  one reservation with segments across listings or several
  reservations under a group id (OQ-006). Either is already handled;
  only the answer needs recording.
- [ ] T105 [GATE] Record that the **financials** checklist item is
  **not applicable**: OQ-001 was decided in favour of deprecation, so
  the guest-folio assumption is void and nothing about it needs
  verifying (FR-043, research.md R9). This gate exists only so that no
  specification assumption is silently dropped.
- [ ] T106 [GATE] Record every T099–T105 outcome in
  `specs/008-reservations-v3/research.md`. For **every refuted
  assumption**, land the corrective fix or documented fallback and
  then **re-run the affected live check until it passes**; an open
  follow-up issue alone does **not** close this gate. Only once every
  check in T099–T105 has passed, or has been recorded as not
  applicable, may the feature be declared stable (FR-041, SC-011).
  Follow-up issues may be opened for non-blocking observations only.

**Checkpoint**: every assumption the implementation relies on has been
exercised against a live Guesty account and passed, or has been
recorded as not applicable. No relied-upon assumption may remain
unverified or refuted at this point. Only now may the feature be
called stable; if any check still fails, the feature stays pre-release
and Phase 9 repeats.

---

## Phase 10: Quality Gates and Polish

**Purpose**: the repository-wide gates that every phase must satisfy
before its pull request merges. Run these per phase, not only once at
the end.

- [ ] T107 [P] Run `uv run ruff check custom_components/ tests/` and
  `uv run ruff format --check custom_components/ tests/` with zero
  errors.
- [ ] T108 [P] Run `uv run mypy custom_components/ tests/` and
  `uv run interrogate custom_components/guesty/ -v` with zero errors
  and 100% docstring coverage, and assert that
  `custom_components/guesty/api/` contains **zero** `homeassistant`
  imports (NFR-007).
- [ ] T109 Run the full suite with coverage —
  `uv run pytest tests/ -x -q --cov=custom_components/guesty
  --cov-report=term-missing` — and verify no regression against the
  883-test baseline or its coverage, including every Feature 001–007
  test.
- [ ] T110 [P] Verify REUSE compliance for every new file —
  `custom_components/guesty/api/reservations_legacy.py`,
  `api/reservations_v3.py`, `api/guest_cache.py`,
  `custom_components/guesty/identity.py`,
  `custom_components/guesty/diagnostics.py`,
  `custom_components/guesty/repairs.py` (only if a repair flow is
  adopted),
  `tests/api/test_reservations_v3.py`, `tests/api/test_guest_cache.py`,
  `tests/test_identity.py`, `tests/test_reservations_parity.py`,
  `tests/test_privacy_logging.py` and `tests/test_diagnostics.py` —
  with SPDX headers, or `REUSE.toml` annotations where a header is not
  possible.
- [ ] T111 Run `prek run --all-files` (`/home/tykeal/.local/bin/prek`)
  with every hook green. Never use `--no-verify` or `--no-gpg-sign`.
- [ ] T112 [P] Execute the `specs/008-reservations-v3/quickstart.md`
  developer workflow: verify every test and lint command runs, that
  the "Where the code lives" table matches the implemented layout, and
  that the fixture-builder signatures match T020.
- [ ] T113 Re-verify the traceability table below against the
  implemented code: every FR, NFR and SC maps to at least one
  completed task or a recorded reason, and close
  [#204][issue-204] from the implementation pull request (FR-002,
  FR-003).

**Checkpoint**: lint, types, docstrings, REUSE and the full suite are
green; the feature is complete except for the Phase 9 release gates.

---

## Dependencies & Execution Order

### Phase dependencies

| Phase | Depends on | Notes |
| --- | --- | --- |
| 0 Gates | — | T002 blocks T067/T073/T074; T003 blocks T087/T094/T095 |
| 1 Toggle | — | pure refactor plus a new option |
| 2 Models | 1 | needs `ReservationFetchResult` |
| 3 Search | 1, 2 | needs the v3 parse and constants |
| 4 Attribution | 2, 3 | needs `stay[]` and a fetch result |
| 5 Guest cache | 3, 4 | lookups only for attributed listings |
| 6 Identity | 3, 4 | bootstrap tasks also need T002 |
| 7 Writes | 1, 6 | every write passes the guard |
| 8 Flip & docs | 1–7, T003 | makes the migration user-visible |
| 9 Live gates | 8 | release gates; not merge blockers |
| 10 Quality | per phase | run on every phase's PR |

The graph is acyclic: 0 → 1 → 2 → 3 → 4 → {5, 6} → 7 → 8 → 9, with
Phase 10 run against each phase.

### User story coverage

| Story | Priority | Delivered by |
| --- | --- | --- |
| US1 reservations keep working | P1 | Phases 2, 3, 4 (+T090) |
| US2 guest details without embedding | P1 | Phase 5 |
| US3 note and status actions | P1 | Phase 7 |
| US4 filters actually apply | P2 | Phase 3 (T029, T032) |
| US5 no wrong-identity write | P1 | Phase 6 |
| US6 rollback without losing a release | P2 | Phases 1, 8 |
| US7 financial sensors deprecate cleanly | P2 | Phase 8 |

### Blocking relationships to watch

- **T002 blocks T067, T073, T074.** No bootstrap-sweep code before the
  specification amendment lands. Phase 6 can otherwise be completed in
  full: the guard, the persisted baseline and fail-closed writes do
  not depend on the sweep.
- **T003 blocks T087, T094, T095 and informs T075.** Both the
  financial-sensor notice and the identity-continuity notice use the
  mechanism OQ-009 settles.
- **T092 must be the last functional change of Phase 8.** Flipping the
  default before Phases 2–7 are green would ship a half-migrated
  default.
- **Phase 9 blocks the *stable* declaration, not the merge.** Treat
  the shipping release as pre-release until T106.

### Within each phase (TDD order)

1. Fixtures first (shared test infrastructure).
2. Tests written and verified **failing** before implementation.
3. Constants before models; models before sources; sources before the
   coordinator; the coordinator before sensors.
4. Core implementation before translations and documentation.
5. Implementation commits separate from the `Docs(tasks):` checkbox
   commit.

### Parallel opportunities

- **Phase 1 tests**: T006, T007, T008 and T009 are in four different
  files and run in parallel; T005 is sequential in
  `tests/test_config_flow.py`.
- **Phase 2**: T022 and T023 are parallel with T021 only if they are
  written as separate test classes; T024 (`api/const.py`) is parallel
  with all model work.
- **Phase 3 tests**: T030–T035 are parallel with T029; T035 is in
  `tests/api/test_exceptions.py` and fully independent.
- **Phase 4 tests**: T042–T047 are parallel with T041; implementation
  T048 → T049 → T050/T051 → T052 is a sequential chain in
  `coordinator.py`.
- **Phase 5 tests**: T054–T058 are parallel with T053; T060
  (`api/const.py`) is parallel with T059.
- **Phase 6 tests**: T064–T066a are parallel with T063; T067 waits on
  T002.
- **Phase 7 tests**: T078–T081 are parallel with T077.
- **Phase 8 tests**: T086–T091 are parallel with T085.
- **Phase 10**: T107, T108, T110 and T112 are parallel; T109 and T111
  are sequential.

---

## Requirement Traceability

Every functional requirement, non-functional requirement and success
criterion in `spec.md` maps to at least one task below. **No
requirement is unmapped.**

### Functional requirements

| Requirement | Tasks |
| --- | --- |
| FR-001 | T033, T037, T040 (+T002 for the bootstrap exception) |
| FR-002 | T029, T032, T037, T113 |
| FR-002a | T044, T051 |
| FR-003 | T029, T037, T113 |
| FR-003a | T023, T027, T037 |
| FR-004 | T032, T037, T044 |
| FR-005 | T030, T038 |
| FR-006 | T030, T038 |
| FR-007 | T029, T037 |
| FR-008 | T031, T035, T036, T039, T040 |
| FR-009 | T021, T041, T048 |
| FR-010 | T041, T048 |
| FR-011 | T042, T049 |
| FR-011a | T043, T050 |
| FR-012 | T021, T041, T048 |
| FR-013 | T041, T048 |
| FR-014 | T046, T048 |
| FR-015 | T029, T037 |
| FR-016 | T054, T059, T061 |
| FR-017 | T058, T061 |
| FR-018 | T053, T059 |
| FR-019 | T053, T059 |
| FR-020 | T054, T059 |
| FR-021 | T052, T057, T059, T066, T066a, T070, T091 |
| FR-022 | T022, T026 |
| FR-023 | T022, T027 |
| FR-024 | T022, T045, T048 |
| FR-025 | T022, T047 |
| FR-026 | T022, T027 |
| FR-027 | T077, T082, T083 |
| FR-028 | T078, T084 |
| FR-029 | T079, T083, T084 |
| FR-030 | T072, T081 (verified unchanged; no code change) |
| FR-031 | T064, T069, T070 |
| FR-031a | T063, T068 |
| FR-031b | T063, T068, T073 |
| FR-031c | T065, T071, T072, T075 |
| FR-032 | T064, T067, T069, T070 |
| FR-033 | T064, T065, T071 |
| FR-034 | T064, T069 (+T002 for the bootstrap exception) |
| FR-035 | T066, T076 |
| FR-036 | T005, T010, T013, T014, T015 |
| FR-037 | T085, T092 |
| FR-038 | T005, T006, T015, T017, T089 |
| FR-039 | T008, T014, T019 |
| FR-040 | T007, T012, T080 |
| FR-041 | T099–T106 |
| FR-042 | T009, T034, T052, T061, T076, T091, T096 |
| FR-043 | T088 (a prohibition; verified by test, no code) |
| FR-044 | T086, T093 |
| FR-045 | T086, T089, T093 |
| FR-046 | T003, T087, T094, T095 |
| FR-047 | T097, T098 |
| FR-048 | T094, T097, T098 (see the note below) |
| FR-049 | T086, T090, T093 |

**FR-048 note**: this feature implements only the *first half* of the
removal timeline — the three sensors become unavailable on the v3 path
now (T093) and the notice states the removal date (T094, T097, T098).
The **removal itself** — deleting the entity creation code, the
registry entries, the strings and the notice together with the legacy
path — is explicitly out of scope here and is a follow-up release on
or after **2027-03-01** (spec.md § "Out of Scope", FR-040). Open that
follow-up issue during T106.

### Non-functional requirements

| Requirement | Tasks |
| --- | --- |
| NFR-001 request volume | T055, T056, T059, T061 |
| NFR-002 rate limiting | T053, T059 |
| NFR-003 entity compatibility | T047, T089, T090 |
| NFR-004 attribute compatibility | T047, T058, T090, T097, T101 |
| NFR-005 graceful degradation | T030, T040, T054 |
| NFR-006 event loop | T053, T059, T108 |
| NFR-007 library isolation | T011, T059, T071, T108 |
| NFR-008 testability | T020, T056 |
| NFR-009 privacy | T052, T057, T059, T066a, T091 |

### Success criteria

| Criterion | Tasks |
| --- | --- |
| SC-001 no deprecated resource called | T033 (+T002) |
| SC-002 differential entity/state/attribute parity | T090 |
| SC-003 request budget, warm and cold | T056 |
| SC-004 out-of-window excluded (#204) | T032, T044 |
| SC-005 200-day in-progress stay found | T032 |
| SC-006 two-listing reservation on both | T041, T042 |
| SC-007 ambiguous identity, zero writes | T064, T070 |
| SC-008 zero registry change across toggle | T089 |
| SC-009 guest outage leaves data intact | T054, T058 |
| SC-010 one lookup per guest per refresh | T053, T056 |
| SC-011 every assumption on the checklist | T099–T106 |
| SC-012 mid-stay move states | T043 |
| SC-013 no baseline, writes refused | T063, T065 |
| SC-014 financial deprecation behaviour | T086, T087, T088 |

### Requirements that need no implementation task

- **FR-030** (custom fields unchanged) and **FR-043** (no guest-folio
  integration) are *prohibitions*. They are satisfied by not writing
  code, and are held by regression tests T081 and T088 respectively.
- **FR-048**'s removal half is a post-2027-03-01 follow-up, as noted
  above.
- **OQ-007 and OQ-008** (listing-filter length and the coverage-for-
  budget trade) generate **no task**: FR-015 forbids a server-side
  listing filter and chunking is explicitly not built. Adopting either
  requires amending FR-015 first.

---

## Implementation Strategy

### Suggested pull-request breakdown

1. **PR 1** — Phase 1 (T005–T019). Toggle and legacy isolation.
2. **PR 2** — Phase 2 (T020–T028). v3 models and mapping.
3. **PR 3** — Phase 3 (T029–T040). v3 search source.
4. **PR 4** — Phase 4 (T041–T052). Attribution, projection, trim.
5. **PR 5** — Phase 5 (T053–T062). Guest cache.
6. **PR 6** — Phase 6 (T063–T066a, T068–T072, T076). Identity
   guard.
7. **PR 7** — Phase 6 bootstrap (T067, T073–T075), **after** the T002
   amendment has merged.
8. **PR 8** — Phase 7 (T077–T084). v3 write actions.
9. **PR 9** — Phase 8 (T085–T098). Default flip, deprecation, docs.
   Labelled `breaking-change`; closes [#223][issue-223] and
   [#204][issue-204].

Phase 0 (T001–T004) is analyze-stage work and precedes PR 1 except for
T002, which need only precede PR 7. Phase 9 (T099–T106) is tracked as
release gates on the milestone, not as a pull request. Phase 10
(T107–T113) runs on every pull request above.

### Incremental delivery

No release is cut between phases, and the default stays `legacy` until
T092. Each phase is nonetheless independently reviewable and leaves
the suite green, satisfying constitution principle IX.

### Rollback summary

| Phase | Rollback |
| --- | --- |
| 1 | Revert; a pure refactor with no behaviour change |
| 2 | Unused until Phase 3; revert |
| 3 | The option stays `legacy` |
| 4 | The option stays `legacy`; the trim only narrows |
| 5 | v3 path only |
| 6 | v3 path only; set `reservations_api=legacy` |
| 7 | The option stays `legacy` |
| 8 | Set `reservations_api=legacy` and reload |
| 9 | Apply the recorded `research.md` fallback, or advise `legacy` |

After release, the user-facing rollback is a single option change plus
a reload. FR-039's startup warning and retirement-shaped error message
keep that state from becoming permanent by accident — and the rollback
itself expires on **2027-03-01**.

---

## Notes

- \[P] tasks touch different files and depend on no incomplete task.
- \[US#] maps a task to its user story; \[GATE] marks a release gate.
- TDD is mandatory: write the failing test first, every time.
- Each phase is an independent pull request with its own coverage.
- **Checkbox updates in this file are a separate `Docs(tasks):`
  commit in the same pull request** (`AGENTS.md`, constitution III).
- All new files require SPDX headers; check `REUSE.toml`.
- `custom_components/guesty/api/` MUST keep zero Home Assistant
  imports (NFR-007).
- Use `git commit -s` with a `Co-authored-by` trailer; never
  `--no-verify` and never `--no-gpg-sign`.
- Markdown must pass the repository's markdownlint hook at 80 columns.

[issue-204]: https://github.com/tykeal/homeassistant-guesty/issues/204
[issue-223]: https://github.com/tykeal/homeassistant-guesty/issues/223

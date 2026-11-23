<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- Keep each task and its requirement references together. -->
<!-- markdownlint-disable MD013 -->

# Tasks: Reservation Identity Write-Miss Refresh

**Input**: Design documents in
`specs/012-reservation-write-miss/` (spec.md, plan.md, research.md,
data-model.md, contracts/guesty-reservation-write-miss.md, quickstart.md)

**Testing**: Tests are required by the specification and project constitution.
For every production-code task, first add the listed tests for each new or
changed behavior and observe a behavioral failure for the intended reason
before implementing the minimum change. Existing-behavior protections are
baseline controls: run and record them before the corresponding code change,
and keep them green afterward. References to failing tests in the tasks below
apply to new or changed behavior, not to baseline-green controls.
Do not treat retained coordinator data or an API method's `None` return as
proof that a refresh succeeded.

**Organization**: Tasks follow the shared coordinator prerequisite and then
the independently testable user stories from spec.md. IDs are in dependency
order. Every task remains unchecked until its work and required verification
are complete.

**Phase gates**: At each phase boundary, complete the phase checkpoint and
confirm that all CI tests pass before beginning the next phase. Report and
resolve any failed or blocked CI test rather than advancing. This gate is
in addition to the final validation in T019.

## Phase 1: Setup

**Purpose**: Use the existing Home Assistant integration, test suites, and
locked dependencies. No project initialization or new dependency is needed.

**Checkpoint**: Confirm the existing project and locked dependencies are
available and all CI tests pass before beginning the coordinator lifecycle.

## Phase 2: Foundational — Coordinator Refresh Attempt Lifecycle

**Purpose**: Establish a coordinator-owned, testable definition of a real
refresh attempt before any service relies on a cache-miss recovery result.

- [ ] T001 Add failing tests in `tests/test_coordinator.py` using the real Home Assistant `DataUpdateCoordinator` and debouncer behavior: cover `async_request_refresh()` returning before a debounced update completes, `async_refresh()` waiting on the same lock and otherwise starting a second update, and an update already in flight at miss detection qualifying only when it completes afterward. Assert generation, completion outcome, skip/failure/cancellation, `last_update_success`, and retained last-known-good data independently (FR-003, FR-004, FR-006, SC-003, SC-004).
- [ ] T002 Implement coordinator-owned attempt generation and completion tracking in `custom_components/guesty/coordinator.py`. Serialize with the existing HA debouncer lock and normal coordinator update path; join a qualifying active attempt, otherwise run at most one update. Record outcomes only after HA updates success state; bind attempts to the live entry/coordinator and configured source. Do not fetch directly, infer freshness from data, or fall back to request-only refresh behavior (FR-003, FR-004, FR-005, FR-006, FR-013).

**Checkpoint**: Coordinator attempt tests pass against real HA refresh/debounce semantics; a completed attempt is distinguishable from a skipped or failed one.

## Phase 3: Safe Recovery Across All Three User Stories

The three stories form one phase because a recovered write must not be
enabled until post-wait authorization, dispatch locking and recovery-only
one-shot transport are all in place. Develop and behaviorally red-test those
prerequisites before enabling service dispatch; use independently testable
units without relying on a disabled service as the reason for a red test.
The intermediate story milestones below are not phase checkpoints. Complete
all tasks and the final phase checkpoint before advancing.

### User Story 1 — Recover From a Missing Identity (Priority: P1)

**Goal**: A valid, otherwise eligible reservation miss can use one accepted
configured-source refresh for note, status, or reservation custom-field writes.

**Independent Test**: Exercise all three public write services from a usable
baseline with a missing target. A safe target found by the refresh reaches its
actual write; a failed refresh or still-missing/code-less target gets a
specific failure and no write.

### Tests for User Story 1

- [ ] T003 [P] [US1] Add failing service-level tests in `tests/test_actions.py` for note and status writes: distinguish a valid absent identifier and a currently published code-less target from invalid input and a cache hit. Before implementing recovery, verify invalid ID or action input, missing/unusable/unpersisted baseline, unconfirmed continuity, ambiguity, and known unroutable targets cause no refresh and no write. Assert success uses the refreshed target, while refresh failure, stale retained data, target still absent, or target still code-less produces an actionable error and zero writes. Assert no second refresh after any failed or incomplete attempt (FR-001–FR-007, FR-015–FR-016; US1-S1–S3; SC-001–SC-004).
- [ ] T004 [P] [US1] Add failing service-level tests in `tests/test_custom_field_service.py` for reservation custom-field misses with absent and code-less targets, successful recovery, failed/stale refreshes, and missing/code-less post-refresh results. Before implementing recovery, verify invalid target/field/value and every independent baseline, storage, continuity, ambiguity and known-route refusal cause no refresh and no write. Assert listing custom-field writes never enter recovery and each reservation attempt performs no more than one coordinated refresh (FR-001–FR-007, FR-016; US1-S1–S3, US3-S3; SC-001, SC-003, SC-006).
- [ ] T005 [US1] Implement a shared typed miss classification and the one-refresh recovery flow in `custom_components/guesty/actions.py` and `custom_components/guesty/__init__.py`. Use it for all three reservation writes on the existing configured `ReservationsCoordinator`; validate inputs and independent refusals first; distinguish cache hit, absent target, and present code-less target; await/join one attempt; and report refresh failure/staleness separately from a target still absent or code-less. Do not refresh listings or other operations (FR-001–FR-007, FR-013, FR-015–FR-016; US1-S1–S3, US3-S3).

**Intermediate milestone**: Tests cover all three write types, failed refreshes,
cache hits and listing writes. Do not enable recovered dispatch until the
remaining story safeguards in this phase are implemented and tested.

### User Story 2 — Preserve Identity and Routing Safety (Priority: P1)

**Goal**: Refresh recovery never bypasses persisted identity, continuity,
ambiguity, source provenance, or route availability protections.

**Independent Test**: Vary baseline persistence, continuity, ambiguity,
identity evidence, and provenance before and after a wait. Every refusal sends
no write; allowed writes use the latest accepted route.

### Tests for User Story 2

- [ ] T006 [P] [US2] Add failing regression tests in `tests/test_actions.py` and `tests/test_coordinator.py` for missing/unusable/unpersisted baseline, unconfirmed continuity, divergence, same-source conflict, duplicate-code ambiguity, bootstrap/cross-path evidence, known unroutable targets, changed source configuration, entry reload, and a later failed or ambiguous refresh superseding an earlier success. Verify refusals known at preflight do not trigger recovery and refusals detected later prevent a write; accepted refreshes preserve the configured source, window, filters, guest lookup policy, pagination, and bounded legacy fallback behavior without an identifier-specific request (FR-002, FR-005–FR-010, FR-012–FR-013; US2-S1–S5; SC-002, SC-004).
- [ ] T007 [P] [US2] Add failing tests in `tests/test_custom_field_service.py` and `tests/test_privacy_logging.py` for equivalent reservation custom-field preflight and guard refusals, first-failed versus previously served fallback lookup, fallback route disabled/retired, and a mocked error response containing a sentinel PIN/custom-field value. Assert the sentinel, confirmation codes, guest data, and credentials are absent from service errors, logs, and diagnostics (FR-002, FR-007, FR-009–FR-012, FR-015; US2-S1–S5; SC-002, SC-005, SC-007).

  T006 must exercise changes to each source-configuration component defined
  in the refresh attempt data model. T007 must also assert privacy for
  successful recovery logs and diagnostics, not only failure paths.

  T006 must test supported Home Assistant option updates A→B→A, including
  back-to-back updates before update listeners run, for the source, window,
  and selected-listing values. Assert that recovery refuses after any options
  replacement or reload even if effective values match again; token-only
  data updates must not themselves cause refusal. Include real HA semantics.
  Add cache-hit controls and recovery tests proving fetched current identity
  evidence is treated identically for selected and unselected listings and
  locally trimmed results; absence of publication is not a new refusal.

- [ ] T008 [US2] Implement preflight and post-refresh authorization in `custom_components/guesty/actions.py` and `custom_components/guesty/__init__.py`. Re-evaluate the existing identity guard, persisted baseline and unstored state, continuity, ambiguity, current target/code evidence, and known route before recovery and again after it. Preserve `identity.py` baseline/comparison semantics; never create, promote, clear, or bypass a baseline. Keep failure categories actionable and sanitize recovery diagnostics rather than forwarding raw response details (FR-002, FR-006–FR-012, FR-015; US2-S1–S5).
- [ ] T009 [US2] Add failing tests in `tests/test_actions.py` and `tests/api/test_actions.py` that pause the note prerequisite read, then invalidate a guard before dispatch or attempt a concurrent coordinator refresh after route selection. Assert that refresh waits through the write, note read precedes write, failed read sends no write, action cancellation propagates, and routing/authorization is rechecked after every awaited read and refresh. Cover stale entry mappings, live-entry lookup, coordinator identity and source changes before dispatch (FR-007–FR-008, FR-014–FR-015; US2-S4; US3-S2; SC-004–SC-005).
- [ ] T010 [US2] Implement final recovery-write authorization in `custom_components/guesty/actions.py` and `custom_components/guesty/api/actions.py`: acquire `_debounced_refresh.async_lock()` without reentering it and hold it from final authorization through the actual PUT, including the note GET; recheck after the note GET and select the current route immediately before the PUT. Verify the live entry mapping, coordinator identity and source; reject reload, source change, failed/ambiguous superseding refresh, missing/code-less target, or any failed guard. Do not convert action cancellation into a service success (FR-007–FR-008, FR-014–FR-015; US2-S4; SC-004–SC-005).

**Intermediate milestone**: Tests and prerequisites preserve feature 008
identity, persistence, continuity, ambiguity, fallback and route decisions,
including after waits. Recovered dispatch remains disabled until all required
dispatch and transport safeguards are present.

### User Story 3 — Bound Recovery Work and Report Outcomes (Priority: P2)

**Goal**: Overlapping misses share eligible work, and recovery writes do not
repeat an externally visible change while ordinary cache-hit behavior remains
unchanged.

**Independent Test**: Overlap eligible actions with an in-flight coordinator
refresh and with one another; count refreshes and actual writes. Exercise
transient write failures for recovered and cache-hit calls separately.

### Tests for User Story 3

- [ ] T011 Add failing service-integration tests in `tests/test_actions.py` and `tests/test_coordinator.py` for two eligible misses sharing one in-flight refresh, including a refresh that began before either miss but completes after miss detection. Cancel one waiter and verify its cancellation propagates without cancelling the shared attempt or causing the other waiter to refresh again; separately cancel the attempt and verify neither waiter writes. Exercise per-action target and guard isolation beyond the foundational coordinator tests: a shared failure/omission does not start another refresh, and each action attempt is capped at one refresh (FR-003–FR-004, FR-016; US3-S1; SC-003).
- [ ] T012 Implement per-attempt joining and shared completion handling in `custom_components/guesty/coordinator.py` and `custom_components/guesty/actions.py`. Preserve the same-entry/source boundary and one-refresh limit while allowing an already-running, same-generation update to qualify when it completes after detection. Sharing refresh work must not share target authorization (FR-003–FR-004, FR-016; US3-S1; SC-003).
- [ ] T013 [P] Add failing transport tests in `tests/api/test_actions.py` for recovered v3 and legacy note/status PUTs under retryable authentication, rate-limit, connection, and transient-server failures. Assert at most one actual PUT, the note prerequisite GET retains its existing retry policy, and ordinary calls retain their current retry behavior (FR-014; US3-S2; SC-006).
- [ ] T014 [P] Add failing transport tests in `tests/api/test_custom_fields.py` for recovered v3 and legacy reservation custom-field PUTs under retryable failures. Assert one actual write only for recovery, while default/cache-hit custom-field requests retain existing retry behavior and listing writes remain outside recovery (FR-001, FR-014; US3-S2–S3; SC-006).
- [ ] T015 Add failing integration tests in `tests/test_actions.py` and `tests/test_custom_field_service.py` proving recovery's one-shot mode reaches each of the three actual reservation writes through both v3 and legacy routes selected after the wait, with no retry against either route; prove cache-hit writes do not opt into one-shot mode and listing writes do not refresh (FR-001, FR-014; US2-S4, US3-S2–S3; SC-005–SC-006).
- [ ] T016 Implement recovery-only single-attempt plumbing in `custom_components/guesty/actions.py`, `custom_components/guesty/api/actions.py`, `custom_components/guesty/api/custom_fields.py`, and `custom_components/guesty/__init__.py`. Apply the mode to the actual note/status/custom-field PUT for both v3 and legacy routes; keep note prerequisite reads and reservation refresh transport policy unchanged; leave cache-hit and listing retries unchanged (FR-001, FR-014; US3-S2–S3; SC-006).

**Checkpoint**: Concurrent eligible calls share one qualifying refresh, every recovered action issues at most one actual write, and cache-hit retry behavior is unchanged.

## Phase 4: Documentation and Cross-Cutting Validation

**Purpose**: Document the bounded user-visible behavior and validate the
implementation without implying live Guesty verification.

- [ ] T017 [P] Add an accurate Reno fix note in `releasenotes/notes/reservation-write-miss-293-20261123.yaml`. State that an eligible reservation write miss may trigger one configured-source refresh and still fails closed if identity or routing evidence is insufficient; do not claim this explains issue #223 or that Guesty's live behavior was verified (FR-015–FR-016; SC-007).
- [ ] T018 [P] Update the reservation-write guidance in `README.md` to explain eligible cache-miss recovery, the unchanged fail-closed identity/provenance rules, and the absence of an eventual-indexing guarantee. Do not imply writes are retried or that listing writes refresh (FR-001–FR-016; US1-S1–S3, US2-S1–S5, US3-S1–S3; SC-001–SC-007).
- [ ] T019 Run focused suites from `specs/012-reservation-write-miss/quickstart.md`, then `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/`, `uv run pre-commit run --all-files`, and `uv run --group releasenotes reno lint`. Run coordinator compatibility tests in the locked HA 2026.10.4 environment and an isolated compatible HA 2026.4.0 environment without changing `uv.lock`; record actual versions and results, or report a blocked gate if the latter cannot be constructed. Do not perform or claim live Guesty verification (FR-003–FR-006, FR-013–FR-016; SC-001–SC-007).

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Uses the existing project; no setup changes are
  required.
- **Foundational (Phase 2)**: T001's new lifecycle behavior tests must fail
  for the intended reason before T002; its existing HA behavior controls may
  pass on baseline. The coordinator attempt lifecycle blocks all write stories.
- **Safe recovery (Phase 3)**: T003–T016 retain their listed scenarios.
  Test each new or changed behavior before its code, and build the guard,
  dispatch-lock and single-attempt prerequisites before T005 enables any
  recovered write. T008, T010, T012, and T016 must be implemented and
  behaviorally verified before T005 enables recovered dispatch; any earlier
  T005 scaffolding must not dispatch recovered writes. T003 and T004 precede
  T005; T006 and T007 precede T008;
  T009 precedes T010; T011 precedes T012; and T013–T015 precede T016.
  Tests for dispatch and transport must reach their intended behavioral
  assertions without requiring an unsafe recovered service to be enabled.
  Complete and validate all three stories together at the phase checkpoint.
- **Documentation and validation (Phase 4)**: T017 and T018 may proceed in
  parallel after behavior is settled. T019 follows implementation, tests,
  and documentation.

### User Story Dependencies

- **US1 (P1)** depends on the coordinator lifecycle in Phase 2.
- **US2 (P1)** tests the recovery flow and its safety prerequisites within
  the same phase, before recovered dispatch is enabled.
- **US3 (P2)** tests shared refresh and one-shot transport in the same phase
  without changing ordinary cache-hit behavior.

### Traceability

Scenario shorthand: `US1-S1..S3`, `US2-S1..S5`, and `US3-S1..S3` refer to
the numbered acceptance scenarios in each story in `spec.md`.

| Requirement or outcome | Tasks |
| --- | --- |
| FR-001; US1-S1, US3-S3 | T003–T005, T014–T016 |
| FR-002; US2-S1 | T003–T008 |
| FR-003–FR-004; US3-S1 | T001–T002, T011–T012 |
| FR-005; US2-S3, US2-S5 | T006, T008 |
| FR-006; US1-S2–S3 | T001–T005, T008 |
| FR-007–FR-008; US2-S4 | T006–T010 |
| FR-009–FR-010; US2-S1, US2-S3 | T006–T008 |
| FR-011; US2-S5 | T006–T008, T013–T016 |
| FR-012–FR-013; US2-S3 | T002, T005–T006, T008 |
| FR-014; US3-S2 | T009, T013–T016 |
| FR-015; US1-S1–S3 | T003–T010, T017–T019 |
| FR-016; US3-S1 | T003–T005, T011–T012, T018 |
| SC-001 | T003–T005, T013–T016, T019 |
| SC-002 | T006–T008, T019 |
| SC-003–SC-004 | T001–T012, T019 |
| SC-005 | T006–T010, T015, T019 |
| SC-006 | T004, T013–T016, T019 |
| SC-007 | T007–T008, T017–T019 |

### Parallel Opportunities

- **US1**: T003 (`tests/test_actions.py`) and T004
  (`tests/test_custom_field_service.py`) are separate service test suites;
  both precede T005.
- **US2**: T006 (action/coordinator tests) and T007 (custom-field/privacy
  tests) touch separate test modules and precede T008.
- **US3**: T013 (`tests/api/test_actions.py`) and T014
  (`tests/api/test_custom_fields.py`) are separate transport suites; T015
  covers service-to-transport propagation before T016.
- **Docs**: T017 and T018 modify separate documentation files.

### Implementation Strategy

1. Complete T001–T002 and validate attempt completion using real HA
   coordination semantics.
2. Develop the three stories as one phase, with behavioral red tests before
   each unit and baseline-green controls for unchanged protections. Build
   and test identity, dispatch-lock, and one-shot transport prerequisites
   before enabling any recovered write.
3. Validate all three stories together at the phase checkpoint, including
   shared refresh and unchanged cache-hit retry behavior.
4. Add accurate operator documentation and release notes, then run all
   required validations without live Guesty calls.

## Task Status and Commit Discipline

- All tasks above are intentionally unchecked at generation time.
- Do not mark a task complete until its implementation or documentation and
  its specified tests/validation are actually complete.
- Commit task-status changes separately from implementation changes; never
  combine `tasks.md` status updates with the code or documentation they
  track.

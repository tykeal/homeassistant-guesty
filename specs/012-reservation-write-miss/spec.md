<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Reservation Identity Write-Miss Refresh

**Feature Branch**: `012-reservation-write-miss-spec1`
**Created**: 2026-11-23
**Status**: Draft
**Tracking Issue**: [#293](https://github.com/tykeal/homeassistant-guesty/issues/293)

An operator reported two reservation custom-field write refusals followed by
a successful PIN write in [#223](https://github.com/tykeal/homeassistant-guesty/issues/223).
The timing of refreshes, the cause of the refusals, and the total number of
identities in the account are not established. This feature addresses a
specific cache-miss case without claiming to explain that report.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recover from a missing identity (Priority: P1)

As a property manager running a reservation automation, I want one fresh
reservation refresh when a valid write target is absent from the identity
cache, so a newly observed, safely identified reservation can be written
without waiting for a scheduled poll.

**Why this priority**: A safe write can otherwise be refused solely because
the current reservation identity data has not yet included the target.

**Independent Test**: Start with a usable baseline and a missing target;
invoke each reservation write type while a fresh configured-source refresh
finds that target with valid identity evidence. Verify the action uses the
current safe route and reports the actual write outcome.

**Acceptance Scenarios**:

1. **Given** a usable baseline and permitted continuity, no other known
   refusal, and a valid target absent from the current identity cache,
   **When** a fresh coordinated refresh completes successfully and finds
   the target with a confirmation code and all guards still pass, **Then**
   the requested reservation note, status, or custom-field write proceeds
   once through the route justified by current provenance.
2. **Given** the same eligible miss, **When** the fresh refresh completes
   successfully without the target, **Then** the action fails explicitly
   without a write or an additional refresh.
3. **Given** an eligible miss, **When** the refresh fails, is ambiguous, or
   does not provide an acceptable fresh completed result, **Then** the
   action fails explicitly without a write and retains the last-known-good
   reservation data as required by feature 008.

---

### User Story 2 - Preserve identity and routing safety (Priority: P1)

As a property manager, I want every reservation write to retain the existing
identity and provenance protections, so a recovery attempt never writes to
the wrong stay or uses an unvalidated route.

**Why this priority**: A mistaken reservation write can affect access to a
guest's accommodation.

**Independent Test**: Independently vary baseline, continuity, ambiguity,
confirmation code, and target provenance before and during a miss refresh;
verify each unsafe state refuses the action without sending a write.

**Acceptance Scenarios**:

1. **Given** a missing or unpersisted baseline, unconfirmed continuity, an
   ambiguous latest refresh, or another independently known refusal,
   **When** a reservation action is invoked, **Then** it is refused
   without a miss-triggered refresh or a write.
2. **Given** a target missing from the current identity cache, whether its
   reservation is absent or has no confirmation code, **When** the one
   allowed refresh still leaves it missing or code-less, **Then** the
   action is refused. A code-less reservation remains readable.
3. **Given** an eligible miss, **When** a refresh detects a same-source
   identity conflict, duplicate-code ambiguity, or disqualifying cross-path
   evidence, **Then** the existing feature 008 comparison and continuity
   rules apply and no unsafe write is sent.
4. **Given** the target becomes available while the action waits, **When**
   its current provenance changes to the other reservation source before
   the write, **Then** all guards are checked again and the route reflects
   the most recently completed accepted refresh, not the earlier route.
5. **Given** a fallback-sourced target, **When** it is only retained and
   has never been served successfully in this session, or the fallback
   write route is disabled or retired, **Then** the action refuses with
   the applicable explanation and sends no write to either source.

---

### User Story 3 - Bound recovery work and report outcomes (Priority: P2)

As an operator, I want simultaneous misses to share appropriate refresh
work and failures to be clear, so automations do not multiply traffic or
silently report a write that did not occur.

**Why this priority**: Recovery should not bypass established request bounds
or create duplicate reservation changes.

**Independent Test**: Invoke overlapping miss actions, count coordinated
refreshes and writes, and exercise unsuccessful and stale refresh results.

**Acceptance Scenarios**:

1. **Given** two eligible misses in the same configuration entry overlap
   an eligible in-progress refresh, **When** both can use its completed
   result, **Then** they share that refresh and each independently
   rechecks its own target and safeguards.
2. **Given** a write was actually attempted after recovery, **When** the
   write is rejected or fails, **Then** the failure is reported and the
   write is not automatically retried, including against another source.
3. **Given** an action targets a listing or a reservation action is already
   refused for a reason other than the identity-cache miss, **When** the
   action runs, **Then** it causes no miss-triggered refresh.

### Edge Cases

- An identifier is absent or invalid: refuse before any refresh; a missing
  identity-cache entry for a valid identifier is a distinct condition.
- An in-progress refresh may serve a miss only if it completes successfully
  after the miss is detected and its result is accepted for the same
  configuration entry and source configuration. A previously completed
  refresh, a throttled or skipped refresh, or a stale result is not fresh.
- If the entry reloads, its configured source changes, or the qualifying
  result is superseded by a failed or ambiguous refresh before the write,
  fail closed; do not use the earlier result as authorization.
- A newer accepted successful refresh before the write supplies the current
  identity and provenance; a target removed, made code-less, or made
  unroutable by that refresh is refused.
- Multiple simultaneous misses can request different targets; sharing a
  refresh does not imply either target will be present or safe.
- A supplementary guest lookup or bounded fallback lookup may fail without
  failing the overall refresh under feature 008. A completed refresh is not
  a guarantee that a particular target is present or writable.
- A failed re-check after a fallback was successfully served in this session
  retains its existing validated served record; a first failed fallback
  lookup does not create one.
- A target may remain absent from current identity evidence because it is
  outside the configured fetch filters or unavailable from the source. The
  action fails; no later indexing or eventual success is promised. A fetched,
  coded target need not be published: selected-listing attribution or the
  local date trim alone does not add a reservation-write restriction. The
  existing identity and provenance guards still decide whether it is writable.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST apply this recovery only to reservation note,
  reservation status, and reservation custom-field write actions with a
  valid supplied reservation identifier whose identity is absent from the
  current identity cache. Listing writes and other operations are excluded.
- **FR-002**: Before triggering recovery, the system MUST evaluate all
  independently determinable input and safety refusals. An invalid or
  missing target identifier, missing or unpersisted usable baseline,
  unconfirmed continuity, ambiguous latest refresh, invalid action input,
  or known unroutable target MUST be refused without
  a miss-triggered refresh. A valid identifier absent from the cache is not
  itself an invalid identifier. A missing target and a present target
  without a confirmation code are distinct states, but either can lack
  current identity evidence and qualify for recovery.
- **FR-003**: For an eligible miss, the system MUST request or join at most
  one coordinated reservation refresh for that action attempt and wait for
  its result. A qualifying refresh MUST complete successfully after the miss
  is detected and publish an accepted result for the same configuration
  entry and source configuration. A refresh already in progress at miss
  detection qualifies if it meets those conditions; an earlier completed,
  skipped, throttled, failed, aborted, or stale result does not.
- **FR-004**: Overlapping eligible misses in the same configuration entry
  MUST share an in-progress qualifying refresh when feasible. Each action
  MUST independently evaluate the result and MUST NOT request a second
  refresh within that attempt, including if the shared refresh fails or
  omits its target. Cancelling one waiting action MUST propagate cancellation
  to that action without cancelling a shared coordinator-owned refresh needed
  by another waiter. If the refresh itself is cancelled, no waiter may
  use it as successful authorization.
- **FR-005**: The refresh MUST use the entry's configured reservation source
  and existing window, status, listing and other applicable selection rules.
  Existing pagination, guest lookup and legacy fallback policies, including
  their cache, eligibility, caps and re-check intervals, remain in force.
  Recovery MUST NOT add targeted per-identifier lookups, speculative sweeps,
  or changes to those policies.
- **FR-006**: Once the refresh finishes, the system MUST fail the action
  explicitly without a write if the refresh failed, was ambiguous, was
  skipped or stale, did not complete successfully, or did not yield an
  accepted result. Retained last-known-good data MUST NOT be treated as a
  successful result of the failed refresh.
- **FR-007**: After a qualifying refresh and before any reservation write,
  the system MUST re-evaluate the current target's presence and confirmation
  code, persisted baseline, continuity, latest refresh ambiguity, action
  validation, and all other applicable existing identity and routing guards.
  A missing or code-less target, or any failed guard, MUST produce an
  explanatory failure and no write. A successful refresh alone does not
  establish permission to write.
- **FR-008**: Immediately before the write, the system MUST choose the route
  from current accepted provenance, not from action input or a route saved
  before waiting. If a later accepted refresh changes provenance, its current
  result controls routing; if the entry reloads, the source configuration
  changes, or the latest refresh fails or becomes ambiguous before the
  write, the action MUST fail closed instead of relying on the earlier
  qualifying result.
  Any Home Assistant options-object replacement during the attempt MUST
  invalidate recovery, including unrelated changes and A→B→A updates;
  token-only entry-data updates MUST NOT themselves invalidate recovery.
  This conservative witness does not add a listing authorization boundary.
- **FR-009**: Feature 008 identity baseline creation, storage, reload and
  persistence failure behavior MUST remain unchanged. An empty, absent or
  unpersisted baseline MUST NOT authorize a write. Recovery MUST NOT
  recreate, clear, bypass or promote the baseline merely to make a write
  possible.
- **FR-010**: Feature 008 cross-path evidence, continuity and ambiguity
  rules MUST remain unchanged: same-source conflicts and duplicate-code
  collisions abort as ambiguous; cross-path disagreement and bootstrap
  conflicts retain their existing distinct treatment; fallback-only
  agreement MUST NOT establish cross-path continuity. An unconfirmed
  continuity state MUST block writes even when a baseline exists.
- **FR-011**: Feature 008 provenance routing remains in force: a v3-sourced
  target uses the v3 route; a fallback-sourced target uses the legacy route
  only if a successful fallback lookup has validated it in the current
  session and that route is available. Retained-only targets never served
  in the session and targets whose legacy route is disabled or retired MUST
  be refused, not sent to either route. A previously served target remains
  routable after a failed re-check under existing rules.
- **FR-012**: Confirmation codes remain identity cross-checks, never
  reservation lookup or write keys. Reservation identifiers MUST retain
  their existing source-validation requirements. Recovery MUST NOT create
  an alternative route or weaken feature 008 FR-031c or FR-033.
- **FR-013**: The per-refresh identity comparison MUST continue to use only
  data already fetched by that refresh, with zero extra comparison requests.
  This specification supersedes **only** feature 008 FR-034's prohibition
  on a write miss initiating one coordinated refresh; it does not add a new
  legacy exception or expand the one-time bootstrap allowance.
- **FR-014**: An action that performed or shared a miss-triggered refresh
  MUST issue at most one actual reservation write, never automatically
  retry it, and never retry it against another source. Existing behavior for
  actions without a miss-triggered refresh remains unchanged. Existing note
  read-before-write and failure handling remain in force; a failed
  prerequisite read produces no write.
- **FR-015**: User-visible failures MUST distinguish an unsuccessful or
  stale refresh, a still-missing identity, and a failed identity or routing
  guard, and provide an actionable explanation without exposing PINs,
  confirmation codes, guest personal data or credentials in logs or
  diagnostics. For a v3 search HTTP 400, report the fixed search endpoint,
  status and guidance to check search settings or contact support, without
  exposing the untrusted body or inferring the rejected filter. The same
  privacy rule applies when an ordinary refresh is joined by recovery.
  Distinct safe local identity and routing errors, other actionable
  categories, authentication handling and cancellation remain unchanged.
- **FR-016**: Recovery MUST NOT impose a new arbitrary cooldown or require
  a particular age of cached data. A later separately invoked action may
  independently qualify for one refresh; no promise of eventual upstream
  indexing or eventual write success is made.

### Key Entities

- **Reservation identity**: A reservation identifier and confirmation-code
  evidence, together with source provenance and current write eligibility.
- **Identity baseline**: Persisted prior identity and cross-path evidence
  governing continuity and conflict detection.
- **Coordinated refresh**: One completed reservation data update for a
  configuration entry, potentially shared by overlapping actions and
  subject to existing source and fallback policies.
- **Write attempt**: One user- or automation-invoked reservation action,
  with at most one qualifying recovery refresh and, after such a refresh,
  at most one actual write.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For each of the three reservation write types, a qualifying
  miss followed by a successful refresh that finds a safe target and any
  successful prerequisite read results in exactly one appropriately routed
  write and reports its actual outcome.
- **SC-002**: In all tested missing-baseline, unconfirmed-continuity,
  ambiguous, code-less, missing-after-refresh and unroutable-target cases,
  zero reservation writes occur and the caller receives an explanatory
  failure.
- **SC-003**: Each miss action requests or joins no more than one coordinated
  refresh, whether it succeeds or fails; two overlapping eligible actions
  able to use the same in-progress refresh consume one shared refresh and
  make independent safety decisions.
- **SC-004**: Across tested refresh failures, stale or skipped results,
  source changes, and later guard failures, 100% of actions fail without a
  write and without treating retained data as a fresh result.
- **SC-005**: Across tested normal, fallback-served, retained-only, disabled
  fallback and changed-provenance cases, 100% of permitted writes use the
  current valid route and 100% of unsafe routes send zero writes.
- **SC-006**: Across tested listing writes and non-miss refusals, zero
  miss-triggered refreshes occur; across tested actual write failures
  after a recovery refresh, zero automatic write retries occur.
- **SC-007**: All tested failure messages identify the applicable failure
  category and a next step, while exposing no PIN, confirmation code,
  guest personal data or credentials in logs or diagnostics.

## Assumptions

- The existing reservation coordinator and feature 008 guard provide the
  refresh, persistence, continuity, ambiguity and provenance semantics
  described in [feature 008](../008-reservations-v3/spec.md), its
  [identity data model](../008-reservations-v3/data-model.md), and its
  [reservation write contract](../008-reservations-v3/contracts/guesty-reservation-writes-v3.md).
- Feature 008's legacy fallback and guest lookup bounds, and its unresolved
  live verification of vendor behavior, remain applicable. This feature
  introduces no guarantee that a reservation will appear in a later refresh.
- A qualifying in-progress refresh can be shared even if it began before
  the miss, provided it completes and is accepted after detection under the
  unchanged entry and source configuration.
- No new action, setting, user-visible entity, periodic polling policy,
  speculative lookup, or change to listing-write behavior is in scope.

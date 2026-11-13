<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Specification Analysis Report: Reservations v3 API Migration

**Feature**: `008-reservations-v3` | **Date**: 2026-11-13
**Stage**: analyze (spec → plan → tasks → **analyze** → implement)
**Artifacts analysed**: `spec.md`, `plan.md`, `tasks.md`, `research.md`,
`data-model.md`, `quickstart.md`, `contracts/*` (4 files),
`checklists/requirements.md`, `.specify/memory/constitution.md`
**Tracking issue**: [#223][issue-223]

This report is the T001 deliverable and the record of the corrective
amendments landed alongside it. Unlike a bare `/speckit.analyze` run,
this pass was explicitly authorised to amend the artifacts, because new
information arrived after `spec.md`, `plan.md` and `tasks.md` were
merged and left them materially incomplete.

## 1. New intelligence that post-dates the merged artifacts

An external user, **the external user**, contacted Guesty Support and posted
the written reply on [#223][issue-223]. None of the merged artifacts
account for it. The load-bearing points:

1. Support tested **one** reservation: the legacy `_id` and the v3 `_id`
   matched, but they explicitly **could not confirm** that this holds
   for every reservation.
2. Always take the reservation `_id` from a **v3 response** when calling
   v3 endpoints. A channel confirmation code, `originId` or any other
   legacy or external reference returns "Reservation not found" on v3
   endpoints such as notes.
3. Run a v3 `GET` with the same id before
   `PUT /v1/reservations-v3/{reservationId}/notes`.
4. **If the v3 `GET` returns not-found, the reservation may simply not
   be in v3 yet**, because Guesty's own migration is still running.
   Support advised retrieving it with
   `GET /v1/reservations/{reservationId}` in that case.
5. Support said legacy retires "at the end of 2026", which **conflicts**
   with Guesty's formal 2026-11-09 announcement of **2027-03-01**.

Point 4 is the one that changes the design. Every merged artifact
assumed absence from the v3 result meant the reservation was cancelled
or genuinely gone — `research.md` R5 states it outright ("a listing
whose reservations vanish simply reports `no_reservation` on the next
refresh"). Shipping on that assumption would have let a reservation
still being backfilled by Guesty disappear from calendars and sensors,
and a door-lock automation would have failed to write a code for a
guest who was in fact arriving. That failure is silent, which is the
class of failure the constitution's rationale for principle I singles
out.

## 2. Owner decisions applied (authoritative, not re-opened)

1. **Hybrid fallback (new scope)**: the default stays `v3`, and a
   reservation not found in v3 falls back to the legacy resource.
2. **Retirement date**: proceed on **2027-03-01**; record the "end of
   2026" remark as a discrepancy and a risk.
3. **Identity guard retained**; `confirmationCode` is a cross-check
   only, **never** a lookup key.
4. **Live verification is available but deferred**: the external user
   has offered account access; the concrete test plan is written once
   an implementation exists.
5. **Financial sensors**: deprecation confirmed.

## 3. Findings

Severity: **CRITICAL** (blocks implementation or violates a constitution
MUST), **MAJOR** (conflicting, unverifiable or uncovered requirement),
**MINOR** (wording, numbering or stale cross-reference).

### A1 — CRITICAL — inconsistency

*Location*: `spec.md` FR-001, SC-001, FR-034; `plan.md` § Identity
guard and § Complexity Tracking; `tasks.md` T001, T002.

The one-time legacy bootstrap sweep calls a legacy resource while the v3
path is active. FR-001, SC-001 and FR-034 are written as absolutes, so
the plan contradicts the spec as written. Carried forward from the tasks
stage, unresolved there.

*Resolution*: FR-001, SC-001 and FR-034 restated with an **exhaustive**
list of permitted legacy calls (§4).

### A2 — CRITICAL — invalidated by new intelligence

*Location*: `spec.md` § Edge Cases; `plan.md` § Pagination;
`research.md` R5.

All three encode "absent from the v3 result ⇒ gone", which Support's
point 4 refutes. `research.md` R5 explicitly declines the sibling
project's confirmed-absence machinery on that basis.

*Resolution*: new FR-050 to FR-057; `plan.md` § Legacy not-found
fallback; `research.md` R13, which rewrites the R5 conclusion.

### A3 — CRITICAL — coverage gap

*Location*: `spec.md`, `plan.md`, `tasks.md`.

Owner decision 1 (hybrid fallback) had **zero** requirement, design and
task coverage.

*Resolution*: FR-050 to FR-057, SC-015 to SC-018, User Story 8, risk
R-012, `plan.md` § Legacy not-found fallback, and tasks T114 to T126.

### A4 — MAJOR — underspecification

*Location*: `plan.md` § Write actions;
`contracts/guesty-reservation-writes-v3.md` §1.1.

The note action's read-before-write — which matches Support's point 3 —
is specified to raise on a non-2xx read. Under Support's point 4 a 404
there may mean "not yet migrated" rather than "no such reservation", and
the already-migrated custom-field write has the same exposure with **no**
legacy equivalent in scope. The merged artifacts do not distinguish the
two cases.

*Resolution*: FR-055 makes a write against a fallback-sourced or
v3-absent reservation **refuse loudly** with a cause-naming error, and
the writes contract defines the read-404 semantics. Whether writes
should additionally route to the legacy write resources is a **scope
change** and is raised as **OQ-010 for the owner**, not decided here.

### A5 — MAJOR — inconsistency

*Location*: `spec.md` § Context and Problem Statement, § Risks and
Mitigations.

Support's "end of 2026" conflicts with the formal 2027-03-01 date, and
nothing recorded the conflict.

*Resolution*: risk **R-011** added and the discrepancy recorded in the
new § Guesty Support guidance; the date itself is unchanged (owner
decision 2).

### A6 — MAJOR — invalidated by new intelligence

*Location*: `spec.md` § Verification status and FR-041; `plan.md`
Summary ¶4; `research.md` header and checklist; `quickstart.md`
§ Prerequisites.

Four artifacts assert that live verification is impossible. It is now
available but deferred.

*Resolution*: the posture is restated as **available, deferred to
pre-release** in all four; FR-041 reworded; `research.md` gains R14 and
a checklist preamble covering Support's two procedural rules.

### A7 — MAJOR — underspecification

*Location*: the design as a whole.

Support's point 2 — never use a confirmation code, an `originId` or an
external reference as a v3 lookup key — had no requirement. **Audit
result: no artifact ever used `confirmationCode` as a lookup key.** It
appears only as a sensor attribute and as the identity baseline's map
key, both of which are cross-checks. The gap is a missing guard-rail,
not an existing defect.

*Resolution*: **FR-031e** states the identifier-provenance rule and
makes it testable; task T121 asserts it; the writes contract states that
the confirmation code is never used to derive a path parameter.

### A8 — MAJOR — inconsistency

*Location*: `data-model.md` §6 versus `tasks.md` T024.

`data-model.md` lists `MAX_LISTING_IDS_PER_SEARCH = 50` as a constant to
add; T024 explicitly forbids adding it.

*Resolution*: `data-model.md` corrected — the value is recorded as a
deferred OQ-008 design parameter, not a constant to add.

### A9 — MAJOR — inconsistency (C2 from T004)

*Location*: `plan.md` § Diagnostics versus `quickstart.md` § Reading
diagnostics.

The plan specifies DEBUG logs only; the quickstart describes a
`reservations` block in the config-entry diagnostics download; no
`diagnostics.py` ships today. FR-042 required only the log, so the
download was additive rather than contradictory.

*Resolution*: FR-042 extended to require **both** surfaces; `plan.md`
§ Diagnostics reconciled and `diagnostics.py` added to the planned
structure. Tasks T091 and T096 already cover both.

### A10 — MAJOR — underspecification

*Location*: `spec.md` OQ-009; `tasks.md` T003, T087, T094, T095.

OQ-009 (the deprecation-notice mechanism) was undecided and blocked
three tasks.

*Resolution*: **decided — both** a non-fixable repairs issue and a
WARNING log, per the T003 recommendation. Recorded in OQ-009, in
`research.md` R15 and in FR-046; the three tasks are unblocked.

### A11 — MAJOR — coverage gap

*Location*: `spec.md` NFR-001.

The request-volume bound predates the fallback and did not account for
its lookups.

*Resolution*: NFR-001 extended to exclude fallback lookups from the
warm-cache comparison and to defer their bound to FR-053; `research.md`
R12 gains the fallback figures.

### A12 — MINOR — inconsistency

*Location*: `spec.md` User Story 6 scenario 4 and FR-039.

Retirement-shaped failures were considered only on the **legacy path**;
the fallback can meet the same failure on the v3 path.

*Resolution*: FR-056 defines a self-disabling fallback on retirement
evidence, with one warning and a still-successful refresh.

### A13 — MINOR — broken reference

*Location*: `contracts/options-flow-reservations-api.md` § Purpose.

Cites "(FR-035)", which is the identity match log; the option is FR-036.

*Resolution*: corrected to FR-036.

### A14 — MINOR — inconsistency

*Location*: `spec.md` § Success Criteria.

SC-011 is listed after SC-014, out of numeric order.

*Resolution*: reordered.

### A15 — MINOR — stale content

*Location*: `checklists/requirements.md`.

Says the spec is "ready for `/speckit.clarify` or `/speckit.plan`" and
records nine open questions.

*Resolution*: updated for the analyze stage, OQ-009's resolution and
OQ-010.

### A16 — MINOR — stale content

*Location*: `quickstart.md` § Prerequisites.

"No Guesty account with v3 access is required — and none is available."

*Resolution*: restated — still not required for development, now
available for pre-release verification.

### Constitution check

No constitution violation was found beyond the already-recorded
principle VII deviation (financial sensors), which the owner has
accepted and which `plan.md` § Complexity Tracking tracks. The fallback
**strengthens** principle I compliance: it removes a silent-data-loss
path.

### Checks that passed

- Every FR, NFR and SC in the merged `spec.md` maps to at least one task
  in `tasks.md`; the traceability tables resolve.
- No duplicate or near-duplicate requirement was found.
- No `TODO`, `TKTK`, `???` or `[NEEDS CLARIFICATION]` placeholder
  remains; `[UNVERIFIED]` markers are deliberate and governed by FR-041.
- All internal anchors used in `spec.md` resolve, including the OQ-001
  and OQ-008 deep links.
- Task phase ordering is acyclic and matches the plan's phases.
- `confirmationCode` is never used as a lookup key anywhere (A7).

## 4. How FR-001, SC-001 and FR-034 were restated

The bootstrap sweep (A1) and the new not-found fallback (A3) are the
**same class** of conflict: a legitimate legacy call while the v3 path
is active. They are resolved **together**, with one restatement that
expresses the real intent — *the legacy path is not used for normal
operation under v3* — rather than two bolted-on exceptions.

- **FR-001** now prohibits the legacy *path* for routine retrieval and
  writes, and enumerates exactly two permitted legacy calls:
  1. the one-time identity bootstrap sweep — one paginated
     `GET /v1/reservations` sequence, once per config entry, at the
     first v3 setup of an **upgrading** entry, before any v3 data is
     published, never repeated (FR-031d);
  2. the not-found fallback —
     `GET /v1/reservations/{reservationId}` for one specific,
     previously known reservation meeting FR-051, at most
     `MAX_LEGACY_FALLBACK_LOOKUPS_PER_REFRESH` per refresh and at most
     once per candidate per `LEGACY_FALLBACK_RECHECK_SECONDS`
     (FR-053).

  Each call must be counted separately in diagnostics, so the permitted
  set is assertable rather than open-ended.
- **SC-001** is now a testable triple: zero deprecated requests in a
  steady-state refresh; at most one bootstrap sequence for an upgrading
  entry's first setup and none for a fresh entry; at most the FR-053 cap
  of by-identifier lookups in a refresh with missing reservations. Any
  other deprecated request fails the test.
- **FR-034** scopes zero-cost to the **per-refresh** comparison and
  names the bootstrap sweep as the only request the guard may ever
  issue, performed outside any refresh.

Everything else remains forbidden: no legacy **search** for routine
retrieval, no legacy **write** on the v3 path, no legacy call of any
other shape. FR-001 says so explicitly, so a test can assert the exact
permitted set rather than an open-ended "except where needed".

## 5. Fallback design summary

Full design in `plan.md` § "Legacy not-found fallback"; data in
`data-model.md` §9.

- **Seam**: a `ReservationFallbackSource` collaborator **inside** the
  `ReservationSource` seam, used only by `V3ReservationSource`. The
  coordinator still receives one `ReservationFetchResult`, so
  attribution, projection, trim and the sensor layer are untouched.
- **Trigger**: a reservation **previously known** from a successful
  refresh, from the identity baseline or from the bootstrap map, whose
  window still overlaps the configured window, and which is **absent**
  from the current v3 result. The fallback is never a speculative sweep:
  it is always a by-id `GET` for a specific known id.
- **Identity**: the fallback record is legacy-sourced. It is recorded
  in a separate `fallback_codes` map, never merged into `codes`, so it
  can never self-confirm continuity. A
  fallback record whose confirmation code maps to a different id than
  the baseline is ambiguous and aborts the refresh exactly as FR-032
  requires.
- **Writes**: a fallback-sourced reservation is **published read-only**.
  Every write targeting it — note, status and the
  `target_type="reservation"` custom-field branch — refuses with an
  error naming the cause. This honours Support's point 1 (never call a
  v3 endpoint with an id that did not come from a v3 response) and keeps
  the failure loud rather than silent (FR-055).
- **Negative caching**: a legacy 404 is **confirmed absence**. The
  candidate is retired, no further lookup is issued, and the reservation
  disappears exactly as it would today — so the fallback cannot mask a
  genuine deletion (FR-052). A legacy success is re-checked at most once
  per `LEGACY_FALLBACK_RECHECK_SECONDS`, so a long-lived backfill gap
  cannot hammer legacy on a 15-minute poll.
- **Budget**: bounded per refresh and per candidate, inside NFR-001's
  stated excess (FR-053).
- **Errors**: a fallback failure that is neither a 404 nor retirement-
  shaped degrades only that reservation — the previous value is retained
  and the refresh still publishes (FR-054). The fallback can never fail
  a refresh.
- **Retirement / legacy unavailable**: a 404 or 410 on the legacy
  resource that is retirement-shaped, rather than reservation-specific,
  self-disables the fallback for the entry's session with one WARNING;
  refreshes continue on v3 alone (FR-056). There is no separate enable
  switch: under `reservations_api=legacy` the legacy path **is** the
  primary path and the fallback is inert.
- **Observability**: fallback-sourced reservations are distinguishable
  in diagnostics and logs only — deliberately **not** as a sensor
  attribute, because adding a v3-only attribute key would break SC-002's
  attribute-key parity assertion (FR-057).

## 6. Metrics

| Metric | Before | After |
| --- | --- | --- |
| Functional requirements | 55 | 66 |
| Non-functional requirements | 9 | 9 (NFR-001 extended) |
| Success criteria | 14 | 18 |
| User stories | 7 | 8 |
| Risks | 10 | 12 |
| Open questions | 9 (1 decided) | 10 (2 decided, 1 new for the owner) |
| Tasks | 114 | 128 |
| Requirements with ≥1 task | 100% | 100% |
| Findings: CRITICAL / MAJOR / MINOR | — | 3 / 8 / 5 |

## 7. Needs an owner decision

- **OQ-010 — should a write fall back to the legacy write resources
  when its target is not yet in v3?** This analysis specifies a loud
  refusal (FR-055), which is within the five recorded decisions. Routing
  writes to `PUT /v1/reservations/{id}` as well would be a **scope
  change** beyond owner decision 1 (which covers retrieval), and would
  need its own identity reasoning, so it is recorded rather than
  decided.

Everything else in this report is either a direct application of the
five owner decisions or a documentation correction.

## 8. Next actions

1. Implementation may now start at Phase 1. The A1 contradiction is
   resolved, so **T067, T073 and T074 are no longer blocked**, and the
   OQ-009 decision unblocks T087, T094 and T095.
2. The fallback tasks T114 to T126 sit in a new Phase 6a and depend on
   the identity guard; they must not be started before Phase 6.
3. The concrete, numbered test plan for the external user is written **once an
   implementation exists**, per owner decision 4, and is posted on
   [#223][issue-223]. It is deliberately not written here.
4. OQ-010 goes to the owner before Phase 7 is implemented.

[issue-223]: https://github.com/tykeal/homeassistant-guesty/issues/223

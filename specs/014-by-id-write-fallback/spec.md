<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: By-ID Reservation Write Fallback

**Feature Branch**: `014-by-id-write-fallback`
**Created**: 2026-11-27
**Status**: Complete — specification only
**Tracking Issue**: [#321](https://github.com/tykeal/homeassistant-guesty/issues/321);
related #223, #293, #314 and #315
**Input**: After the feature 012 write-miss refresh succeeds and the target
is still missing or has no confirmation code, perform exactly one
reservation read by identifier and, only when that single record can be
reliably proven to be the requested, unconflicted, allowed reservation,
authorize the one current v3 write. Never weaken identity, continuity,
routing, or privacy safeguards.

[#223](https://github.com/tykeal/homeassistant-guesty/issues/223) reports a
real same-day reservation whose custom-field write was refused for unknown
identity even after a reload. Every reservation the search returned carried
a confirmation code, so the target was most likely absent from the search
result rather than stale in cache. Feature 012's refresh reruns that same
search and cannot recover such a target. This feature does not claim to fix
issue #223 until the behavior is verified on a live system.

## Clarifications

### Settled Decisions

- Only data that can be reliably proven actionable may authorize a write;
  every uncertain or failed outcome fails closed with no write.
- The by-identifier read happens only after the feature 012 qualifying
  refresh succeeded and every other guard still passes; it is not a
  substitute for that refresh or for any guard.
- Exactly one by-identifier read per action attempt, never retried and
  never directed at another source. Overlapping actions may each perform
  their own read; no new cooldown is introduced.
- An allowed status is one within the entry's configured status selection,
  including that selection's established source equivalents, and is never
  the `alteration` hold status.
- Listing selection or the local publication date window does not by itself
  block a write (#293 decision preserved by feature 013).
- A confirmation code that maps to no reservation in any identity evidence
  is acceptable new evidence. A code that maps to a different reservation in
  any evidence is a conflict and refuses the write.
- Not found, mismatched identifier, malformed or unverifiable record, no
  code, HTTP error, rate limit, or transport failure produces an explicit
  failure with no write and no retry.
- By-identifier evidence authorizes only the single current v3 write. It is
  not published, not added to current writable identities, and not
  persisted to the identity baseline or any other store.

### Derived Decisions and Rationale

- **Only absent or validly code-less targets qualify**: The read applies
  when the qualifying refresh did not return the target, or returned it as
  an accepted record without a confirmation code. A target that the
  qualifying refresh returned but rejected as malformed, unverifiable, or
  partially valid under feature 013 does not qualify, nor does a target
  whose identifier has retained feature 013 conflict-only evidence from any
  earlier rejection, nor a target retired as confirmed absent under feature
  008 FR-052. A rejection or confirmed absence is explicit negative
  evidence; two sources disagreeing about existence is ambiguous, and
  feature 008 forbids masking a genuine cancellation or deletion. The check
  uses the retirement evidence the system currently holds; existing storage
  does not keep retirements durably, and adding such storage would change
  feature 008 and 012 storage behavior outside this narrow scope. As with
  an unknown confirmation code, lost retirement history is not treated as
  proof of existence, but it does not by itself block a record that is
  otherwise independently verified, in an allowed status, and unconflicted.
  Feature
  013 requires correction to arrive through a new accepted refresh; a second
  read of the same source must not override it or promote conflict-only
  evidence.
- **v3 route only**: By-identifier evidence comes from v3 and can only
  justify a v3 write. The fallback applies only when the entry's configured
  reservation source is v3 and the target has no current or retained
  fallback (legacy) provenance in this session. A fallback-sourced target
  keeps feature 008 and 012 routing; this feature never re-routes it, and
  never adds a legacy call.
- **Exactly one matching record**: The response must contain exactly one
  reservation record, and its identifier must exactly match the requested
  identifier. Additional records, duplicate matches, or a non-matching
  record cannot be proven to describe only the target, so they fail closed.
- **Code consistency for the same identifier**: If any identity evidence
  already associates the target identifier with a different confirmation
  code, the identity of that reservation is contradicted and the write is
  refused. Matching prior evidence for the same identifier is consistent,
  not a conflict.
- **Code-less refresh record**: When the accepted refresh record has no
  code and the by-identifier record has one, the by-identifier record must
  agree with the refresh record on identity-relevant facts available in
  both (identifier and the verified listing association). Disagreement
  fails closed. The issue's scoping explicitly includes code-less targets,
  and feature 008 FR-033 remains satisfied because the authorizing
  evidence carries a non-empty code.
- **Note writes reuse the same read**: Because only one by-identifier read
  is permitted, a reservation note write on this path uses the notes from
  the verified by-identifier record as its read-before-write content and
  MUST NOT issue a second read. That record is fresher than any cached copy
  and is the same record whose identity was proven, which satisfies the
  intent of feature 008 FR-027.
- **Single-attempt read**: "No retry" includes automatic retries for rate
  limiting, transient errors, and reactive authentication renewal on this
  request, matching the existing single-attempt recovery write mode. An
  authentication failure on the read fails the action; authentication
  handling for other requests is unchanged.
- **Witness spans the read**: Anything that would invalidate feature 012
  recovery while the read is in flight also invalidates the by-identifier
  evidence. If a newer accepted refresh completes before the write, the
  by-identifier evidence is discarded; a status or custom-field write may
  proceed only if that newer refresh itself supplies the target with a code
  and current v3 provenance and every feature 012 guard passes, without
  another by-identifier read. The action stays v3-only: if that refresh
  gives the target fallback provenance, it fails closed rather than
  switching to a legacy write mid-attempt. A note write fails closed in
  that case too, because its read-before-write content came from the
  discarded record and a fresh note read would be a second by-identifier
  read.
- **Nothing learned is retained**: The record, its code, and its outcome are
  not cached for other actions, not stored as conflict-only evidence, and
  do not make the target a "previously known" legacy fallback candidate.
  This keeps the next accepted refresh authoritative.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Write to a reservation the search omits (Priority: P1)

As a property manager running a reservation automation, I want a write to a
real reservation that the reservation search still omits after recovery to
succeed when the reservation can be proven by its identifier, so a same-day
booking can receive its door code without waiting indefinitely for search
indexing.

**Why this priority**: Feature 012 recovery cannot help when the search
itself does not return the target; access workflows then fail.

**Independent Test**: With a usable persisted baseline and confirmed
continuity, arrange the qualifying refresh to omit the target while a
by-identifier read returns a single valid, allowed, coded record with no
conflicting evidence. Invoke each reservation write type and verify one
read and one v3 write occur, and the actual outcome is reported.

**Acceptance Scenarios**:

1. **Given** an eligible miss and a qualifying refresh that succeeds
   without the target, **When** the one by-identifier read returns exactly
   one well-formed, independently verified record with the requested
   identifier, an allowed status, and a non-empty confirmation code that
   maps to no other reservation, and all guards still pass, **Then** the
   requested note, status, or custom-field write is sent once through v3.
2. **Given** the same conditions except that the qualifying refresh
   returned the target as an accepted v3 record without a code, **When**
   the by-identifier record carries a code and agrees with that record,
   **Then** the write proceeds once through v3.
3. **Given** a valid by-identifier record whose listing is not selected or
   whose stay is outside the local publication window, **When** all other
   conditions pass, **Then** those publication filters alone do not refuse
   the write.
4. **Given** a note write on this path, **When** the write proceeds,
   **Then** it uses the verified record's existing notes as its
   read-before-write content and no second read is issued.
5. **Given** a write that was actually sent on this path, **When** it is
   rejected or fails, **Then** the failure is reported and the write is not
   retried against v3 or any other source.
6. **Given** a successful by-identifier write, **When** reservation data is
   next published, **Then** the target appears only if an accepted refresh
   provides it; the by-identifier record was never published or cached.

---

### User Story 2 - Refuse anything that cannot be proven (Priority: P1)

As a property manager, I want the fallback to refuse every uncertain,
conflicting, or disallowed result, so a direct lookup never writes a door
code or note to the wrong stay.

**Why this priority**: A mistaken reservation write can affect a guest's
access to their accommodation.

**Independent Test**: Vary the by-identifier response (absent, mismatched,
multiple records, malformed, no code, disallowed status, conflicting code,
HTTP error, rate limit, transport failure) and pre-read guard state; verify
zero writes and an explanatory failure in every case.

**Acceptance Scenarios**:

1. **Given** a missing or unpersisted baseline, unconfirmed continuity, an
   ambiguous latest refresh, an invalid identifier or input, or a failed,
   stale, or otherwise non-qualifying feature 012 refresh, **When** the
   action runs, **Then** no by-identifier read and no write occur.
2. **Given** an eligible read, **When** the response has no record, a
   record with a different identifier, more than one record, or a record
   that is malformed, unverifiable, or partially valid under feature 013,
   **Then** the action fails explicitly with no write.
3. **Given** an eligible read, **When** the matching record has no
   confirmation code or a non-string or empty code, **Then** the action
   fails explicitly with no write.
4. **Given** an eligible read, **When** the record's status is outside the
   configured status selection or is `alteration`, **Then** the action is
   refused with no write.
5. **Given** an eligible read, **When** the record's code maps to a
   different reservation in current, baseline, legacy, bootstrap, fallback,
   or conflict-only identity evidence, or the target identifier is already
   associated with a different code, **Then** the write is refused.
6. **Given** an eligible read, **When** the read returns an HTTP error, a
   rate-limit response, an authentication failure, or a transport failure,
   **Then** the action fails explicitly, with no retry of the read and no
   write.
7. **Given** a target that the qualifying refresh returned but rejected
   under feature 013, a target with retained conflict-only evidence for its
   identifier, a target retired as confirmed absent, a target with fallback
   provenance, or an entry
   whose configured source is not v3, **When** the action runs, **Then** no
   by-identifier read occurs and the existing refusal applies.
8. **Given** a by-identifier read in progress, **When** the entry reloads,
   its options or source configuration change, or a newer refresh fails or
   is ambiguous before the write, **Then** the action fails closed without
   a write.
9. **Given** a by-identifier read has succeeded, **When** a newer accepted
   refresh completes before the write, **Then** the by-identifier evidence
   is discarded; a status or custom-field write proceeds through v3 only
   if that refresh supplies the target with a code and current v3
   provenance and all guards pass; a target with fallback provenance, and
   any note write, fails closed; no second by-identifier read occurs.

---

### User Story 3 - Diagnose why a fallback did or did not write (Priority: P2)

As an operator investigating a refused or successful write, I want clear,
privacy-safe outcomes that say whether the reservation was absent from the
search and found by identifier, not found by identifier, or found but
refused, so I can tell a search-indexing gap from a safety refusal.

**Why this priority**: #223 could not be diagnosed from existing messages;
operators need evidence without exposure of guest or access data.

**Independent Test**: Exercise each outcome with synthetic sensitive values
and inspect the action failure, logs, and diagnostics.

**Acceptance Scenarios**:

1. **Given** each fallback outcome, **When** it is reported, **Then** the
   message or log distinguishes "found by identifier" (qualified as absent
   from search or present without a code), "not found by identifier", and
   "found but refused" with a category for the refusal reason and a next
   step.
2. **Given** synthetic confirmation codes, PINs, guest data, credentials, and
   raw response content, **When** any outcome is logged or reported,
   **Then** none of those values appears.
3. **Given** two overlapping actions for the same or different targets,
   **When** both qualify, **Then** each performs at most its own one read
   and makes an independent decision; neither reuses the other's result.

### Edge Cases

- The target may still be absent from search because of upstream indexing,
  filters, or other unknown causes. No cause is inferred, and success of the
  by-identifier read does not imply a later refresh will return it.
- A reservation outside the configured fetch window or listing selection
  but otherwise valid may be written; a disallowed status may not.
- A confirmed or checked-in reservation created after its scheduled check-in
  is not refused for that reason (feature 013 FR-003a).
- `completed` is within the selection when `checked_out` is configured, as
  established by feature 008 FR-003a; `alteration` is never allowed.
- A canceled or other status included in the configured selection is
  treated exactly as the qualifying refresh path would treat it; this
  feature does not widen or narrow write eligibility by status.
- A response envelope that does not prove which record was requested, or
  whose result set cannot be shown complete, is malformed for this purpose.
- A by-identifier record that would establish a duplicate code across two
  identifiers makes the decision ambiguous and fails closed; it does not
  mark the latest refresh ambiguous or change continuity state.
- If conflict-only evidence cannot be checked because it is unavailable or
  its bound was exceeded (feature 013 FR-008c), the write is refused.
- A later separately invoked action for the same target may qualify for its
  own refresh and read; nothing from an earlier read is reused.
- An actual write that times out after sending has an unknown outcome and is
  reported as a failure without retry, as in feature 012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST consider the by-identifier fallback only for
  reservation note, reservation status, and reservation custom-field write
  actions. Listing writes and all other operations are excluded.
- **FR-002**: The fallback MUST be considered only after a feature 012
  qualifying refresh completed successfully and the target is still absent
  from that accepted result or present as an accepted record without a
  confirmation code. Every feature 012 precondition and post-refresh guard
  MUST pass first, except the target's presence and code check and
  provenance-derived route selection, which FR-003 and FR-005 to FR-007
  replace on this path: valid input, usable persisted baseline, confirmed
  continuity, no ambiguity, unchanged entry and options witness, and
  qualifying refresh. Feature 012's refusal of a known unroutable target,
  such as a retained-only or disabled-route fallback target, still applies
  before the read. A target returned but
  rejected by that refresh under feature 013, whose identifier has any
  retained feature 013 conflict-only evidence, or that the system
  currently holds as confirmed absent under feature 008 FR-052, MUST NOT
  qualify. If held negative evidence cannot be checked, the read MUST NOT
  be issued and the action fails closed. This feature adds no new durable
  retirement record and does not change feature 008 or 012 storage;
  retirement evidence no longer held (for example after a reload) is not
  available to this check.
- **FR-003**: The fallback MUST apply only when the entry's configured
  reservation source is v3 and the target has no current, retained, or
  served fallback (legacy) provenance. It MUST NOT add, alter, or re-route
  any legacy call or write.
- **FR-004**: For a qualifying action attempt, the system MUST issue at most
  one reservation read by identifier to the existing v3 reservation read
  used for note read-before-write, for that exact identifier. The read
  MUST be sent once with no automatic retry for any reason, including
  rate limiting, transient errors, and authentication renewal, and MUST NOT
  be followed by any other lookup or source.
- **FR-005**: The by-identifier response MUST authorize a write only when it
  contains exactly one reservation record and that record:
  (a) has an identifier exactly equal to the requested identifier;
  (b) is well formed and independently verified under the feature 013
  rules that would apply to it in an accepted refresh, including FR-008d
  for every raw segment and the facts required by the requested action;
  (c) has a recognized status within the entry's configured status
  selection and its established source equivalents, and not `alteration`;
  (d) carries a non-empty string confirmation code.
  Any other response MUST fail the action explicitly with no write.
- **FR-006**: The system MUST refuse the write if the record's confirmation
  code maps to a different reservation, or the target identifier maps to a
  different confirmation code, in any identity evidence: the qualifying
  refresh's current identities, the persisted baseline, legacy, bootstrap,
  and fallback evidence, and feature 013 conflict-only evidence. If any of
  that evidence cannot be checked, the write MUST be refused. A code with
  no mapping in any evidence is acceptable new evidence.
- **FR-007**: When the qualifying refresh returned the target as an
  accepted record without a code, the by-identifier record MUST agree with
  it on identifier and verified listing association; otherwise the write
  MUST be refused.
- **FR-008**: Immediately before the write, the system MUST re-check every
  feature 012 guard and witness except the target's presence and code check
  and provenance-derived route selection, which on this path are replaced
  by the verified by-identifier evidence (FR-005 to FR-007) and the v3-only
  condition (FR-003); FR-003 MUST itself be re-checked. An entry reload,
  options-object replacement, source change, newer failed or ambiguous
  refresh, or any failed guard MUST fail the action without a write. If a
  newer accepted refresh completes before the write, the by-identifier
  evidence MUST be discarded. A status or custom-field write may then
  proceed through v3 only if that refresh itself supplies the target with a
  code and current v3 provenance and all feature 012 guards pass; if it
  gives the target fallback provenance, or the action is a note write, the
  action MUST fail closed. FR-003 continues to apply, so no legacy write is
  ever issued on this path. No additional by-identifier read may be
  issued.
- **FR-009**: By-identifier evidence MUST authorize only the single current
  v3 write of the invoking action. It MUST NOT be published, added to
  current writable identities, persisted to the identity baseline, stored
  as conflict-only or fallback-candidate evidence, cached for another
  action, or used to change continuity, ambiguity, or routing state. The
  next accepted refresh remains authoritative.
- **FR-010**: An action using the fallback MUST issue at most one actual
  write through the v3 route, MUST NOT automatically retry it, and MUST NOT
  retry against another source. A note write MUST use the notes of the
  verified record as its read-before-write content and MUST NOT issue a
  second read.
- **FR-011**: Overlapping actions MUST each perform their own at-most-one
  read and independent decision. No new cooldown, shared by-identifier
  result, or cached-data age requirement is introduced.
- **FR-012**: Outcomes MUST distinguish "found by identifier" (written or
  write failed), qualified by whether the target was absent from the search
  or present in it without a confirmation code, "not found by identifier"
  (including a mismatched or empty result), and "found but refused" with a
  privacy-safe reason category (malformed, no code, disallowed status,
  identity conflict, guard or witness failure) plus read failures
  (HTTP error, rate limit, authentication, transport), each with an
  actionable explanation. User-visible errors, logs, and diagnostics MUST
  NOT include confirmation codes, PINs, guest personal data, credentials,
  or raw or untrusted response content.
- **FR-013**: Confirmation codes remain identity cross-checks, never lookup
  or write keys; the read and write use only the validated reservation
  identifier. Feature 008 FR-031c and FR-033 remain in force.
- **FR-014**: This specification supersedes the following, **only** on the
  by-identifier path defined here: feature 012 FR-005's prohibition on
  targeted per-identifier lookups; feature 012 FR-013's limitation of the
  identity comparison to refresh data; feature 008 FR-034's statement that
  no guard activity in a write may add a request; feature 012 FR-007's
  refusal of a target still missing or code-less after the refresh, which
  FR-005 to FR-007 replace; feature 012 FR-008's route selection from
  current accepted provenance, which FR-003 replaces with a v3-only route;
  and feature 012 FR-014's separate note read-before-write, which FR-010
  replaces with reuse of the single read. The per-refresh identity
  comparison remains request-free, and no other part of features 008, 012,
  or 013 is changed.

### Compatibility and Evidence

- The v3 reservation read by identifier is the request already used for
  note read-before-write under feature 008 FR-027, which matches the
  requested identifier before using a record. It is not a legacy call, so
  feature 008 FR-001's exhaustive list of legacy exceptions is unchanged.
- The single-attempt request behavior is the same mode feature 008 FR-063
  and feature 012 already require of recovery writes.
- Feature 013 FR-008/008a keep valid unpublished targets writable and
  prohibit rejected or conflict-only evidence from authorizing writes;
  this feature applies those rules to the by-identifier record.
- #223 aggregate observations suggest, but do not prove, that the target
  was absent from the search. Whether the by-identifier read returns such
  reservations on a live account is unverified.

### Key Entities

- **By-identifier evidence**: A single verified v3 record obtained for one
  action attempt, valid only for that attempt's one v3 write.
- **Identity evidence maps**: Current, baseline, legacy, bootstrap,
  fallback, and conflict-only identifier-to-code mappings checked for
  conflicts.
- **Write attempt**: One invoked reservation action with at most one
  qualifying refresh, at most one by-identifier read, and at most one write.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For each of the three reservation write types, a qualifying
  miss with a valid, allowed, unconflicted by-identifier record results in
  exactly one read and exactly one v3 write, and reports the real outcome.
- **SC-002**: Across all tested unsafe cases (pre-read guard failures,
  non-qualifying targets including confirmed-absent and conflict-only
  targets, not found, mismatched, multiple records,
  malformed, no code, disallowed status, conflicts, unavailable evidence,
  read failures, witness changes), zero writes occur and 100% return an
  explanatory failure.
- **SC-003**: Every tested action attempt issues no more than one
  by-identifier read, including when the read fails and for note writes,
  and zero reads are retried.
- **SC-004**: After any tested by-identifier write, zero changes occur to
  published reservations, current writable identities, persisted baseline,
  conflict-only evidence, or fallback candidates.
- **SC-005**: Valid records excluded only by listing selection or the
  local date window are written in 100% of tested otherwise-safe cases.
- **SC-006**: Across all tested outcomes, the three outcome categories,
  and the absent versus code-less qualifier for found records, are
  distinguishable and zero confirmation codes, PINs, guest data,
  credentials, or raw payloads appear in errors, logs, or diagnostics.

## Assumptions

- Feature 012 recovery, feature 008 identity and routing guards, and
  feature 013 validation and conflict-only evidence exist as specified.
- Synthetic data is sufficient for verification; live verification of #223
  is a separate outstanding activity.
- No new action, setting, entity, polling policy, or listing-write change
  is in scope. No plan, tasks, or implementation are included here.

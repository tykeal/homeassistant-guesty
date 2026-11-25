<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Verifiable Reservation Data

**Feature Branch**: `013-reservation-data-validation`
**Created**: 2026-11-25
**Status**: Complete — specification only
**Tracking Issue**: #314; related #297, #293 and #223
**Input**: Publish only reservation data that can be reliably proven
actionable and useful; do not retain bad data merely because it is uncertain.
Preserve genuinely useful history and resolve the interaction with existing
write-safety, migration, and recovery behavior explicitly.

## Clarifications

### Session 2026-11-25

- Verified date-only reservations may be kept for uses that require only
  dates. An unknown exact instant MUST NOT be invented or implied. An
  independently verified timezone or instant may be used, but the model's
  missing-zone default is not authoritative merely because it is `UTC`.
- A reservation whose verified checkout date is today in the verified
  listing-local timezone, but whose exact checkout time is unknown, is a
  date-only, uncertain current-or-upcoming candidate. It may already have
  ended; candidacy MUST NOT imply a verified exact instant or present
  occupancy. The local day requires reliable timezone evidence.
- A well-formed verified reservation is not made unwritable solely because
  selected-listing or date-window filters keep it out of publication. All
  existing identity, continuity, freshness, privacy, and routing safeguards
  still apply. A rejected or unverifiable record MUST NOT authorize a write.
- When a multi-segment stay contains an unverifiable segment, keep only
  independently verified segments. The bad segment MUST NOT be retained,
  published, used to infer information for a good segment, or authorize a
  write.
- If a refresh fails without showing the last-verified data invalid, display
  that data clearly marked stale. A failed refresh does not reverify it or
  weaken write safeguards, and stale display does not retain data shown bad.
- If an individual reservation is malformed but the remaining response is
  complete, independently verifiable, and safe, skip that record and publish
  other independently verified reservations. Skipping proves neither
  deletion nor identity continuity. Fail closed when completeness, batch
  integrity, or safety cannot be established.
- An independently verified reservation-ID-to-confirmation-code mapping
  associated with a rejected reservation may be retained across restarts
  **only** to detect identity conflicts. It must not authorize writes to
  that reservation or appear among current writable identities. Invalid
  fields and malformed payloads are not retained.

### Derived Decisions and Rationale

- **Partially valid stays and writes**: Independently verified segments remain
  usable for their supported outputs, but a reservation with any rejected
  raw segment is not a verified reservation-level write target. Refuse the
  write until all its segments and all applicable write safeguards are
  independently verified. A good segment or identity mapping alone cannot
  prove the whole target actionable; this is the fail-closed reading.
- **Upgrade and recovery**: Revalidate previously stored reservation data
  under these rules before displaying it or using it for writes or fallback.
  Drop anything that cannot be proven valid. Separately verified identity
  evidence for a rejected reservation may remain only as conflict-only
  evidence. Previous acceptance under a fail-open rule is not proof of
  validity. Do not equate dropping data with confirmed deletion.
- **Failure and correction**: A failed refresh may display only last-verified
  data that meets these rules, marked stale; data already shown invalid is
  not restored. A corrected reservation becomes eligible only after a new
  accepted verification and the existing guards succeed. This preserves
  useful data without turning a failed refresh into fresh authorization.
- **Bounded evidence**: Conflict-only evidence is bounded; if it cannot be
  safely retained or checked, affected decisions fail closed. Capacity or
  persistence failure is not proof that no conflict exists.
- **Selection**: Exclude conclusively expired stays before selecting
  current or upcoming candidates, preserve the existing eligible-candidate
  priority, and identify a checkout-day date-only candidate as uncertain.
  This retains the user's date-only choice without inventing occupancy.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Trust reservation information (Priority: P1)

As a property manager, I need reservation information used by my automations
and displays to be verifiable, so an uncertain identity, listing, status, or
time cannot lead to action on the wrong booking.

**Why this priority**: Incorrect booking or listing information can affect
guest access and property operations.

**Independent Test**: Provide a mix of verifiable reservations and
reservations with unrecognized statuses, unreliable identity, listing, or
timing. Verify that only supported, verified information appears in the
applicable outputs, and that unrelated valid records are distinguished from
invalid data under the agreed malformed-record policy.

**Acceptance Scenarios**:

1. **Given** a reservation with an unrecognized source status, **When** a
   refresh processes it, **Then** that reservation is not published by
   passing the unknown status through as though it were supported.
2. **Given** a reservation or listing projection whose identity, listing
   association, or timing needed for a particular use cannot be verified,
   **When** a successful refresh is published, **Then** that unverifiable
   information is not presented as verified or used for that purpose.
3. **Given** a malformed reservation alongside independently verified,
   in-scope reservations in an otherwise complete and safe response,
   **When** the refresh is accepted, **Then** the malformed record is
   skipped and the verified reservations remain available with their own
   verified listings and timing.
4. **Given** a confirmed or checked-in reservation whose applicable
   checkout has conclusively passed and a verified future reservation for
   the same listing, **When** current and upcoming choices are derived,
   **Then** the expired reservation is not labeled current or upcoming and
   does not crowd the future booking out of the upcoming list.
5. **Given** a reservation with verified local dates but no verified exact
   endpoints, **When** a date-only history or other date-appropriate output
   uses it, **Then** its verified dates can remain available while missing
   instants are shown as unknown, never as fabricated midnight or a guessed
   timezone.
6. **Given** a date-only reservation, **When** a decision requires an exact
   instant, **Then** the unknown instant is not used as if it were verified.
7. **Given** a missing listing timezone and independently verified timezone
   or instant evidence, **When** timing is evaluated, **Then** that verified
   evidence may be used; an absent timezone alone does not establish UTC.
8. **Given** a multi-segment stay with an unverifiable middle segment and
   independently verified segments before and after it, **When** the stay
   is processed, **Then** the good segments remain eligible for their own
   outputs, the bad segment is not retained or published, and its values
   do not establish another segment's interval, identity, listing, status,
   or route.
9. **Given** a raw multi-segment stay with only one independently verified
   survivor, **When** the survivor is projected, **Then** it keeps its own
   verified interval and listing; discarding the other segment does not
   make the original stay eligible for a single-raw-segment fallback or
   substitute the reservation-level span.
10. **Given** a stay segment with good dates but an unverified shared
    reservation identity or source status, **When** it is evaluated,
    **Then** that segment is not published as independently verified.
11. **Given** an otherwise eligible confirmed or checked-in reservation
    with a verified checkout date equal to the verified listing-local
    current date but no verified exact checkout time, **When** current or
    upcoming candidates are built, **Then** it is included as a date-only,
    uncertain candidate; its source lifecycle status is not presented as
    proof of current occupancy.
12. **Given** the same date-only reservation and no reliable timezone
    evidence for its listing, **When** the local day cannot be verified,
    **Then** the system does not assume UTC or claim that checkout is today.
    The verified dates remain eligible for outputs that require only those
    dates and do not require a current-local-day comparison.
13. **Given** a date-only checkout before the verified listing-local current
    date, **When** current or upcoming candidates are built, **Then** it is
    not a candidate merely because its source status remains confirmed or
    checked in; it may remain in verified history.
14. **Given** a verified exact checkout instant, **When** that instant is
    reached or passed, **Then** the interval is not treated as current or
    upcoming merely because its source lifecycle status has not advanced.
15. **Given** two listings with independently verified timezones whose
    local dates differ at the same instant, **When** date-only checkout
    candidates are evaluated, **Then** each listing uses its own verified
    local day rather than the other listing's day or a default UTC day.
16. **Given** a reservation created after its scheduled check-in, whose
    identity, listing, recognized `confirmed` or `checked_in` source status,
    and exact interval are independently verified and whose checkout is
    still in the future, **When** reservations are evaluated, **Then** it
    remains eligible for the appropriate current or upcoming output and is
    not discarded merely because it was created late.
17. **Given** a same-day reservation with either eligible source status,
    verified local dates, and no verified exact checkout time, **When** its
    verified listing-local checkout date is today, **Then** it remains a
    date-only uncertain candidate under FR-004a, without inventing exact
    times or current occupancy.
18. **Given** a malformed record whose exclusion leaves page completeness,
    batch integrity, or applicable identity safeguards unverifiable,
    **When** the refresh is processed, **Then** it fails closed rather than
    publishing an unsafe partial result, and eligible last-verified data
    remains visibly stale.
19. **Given** an otherwise safe refresh that skips a malformed record,
    **When** identity and absence evidence are evaluated, **Then** the
    skip alone proves neither that the reservation was deleted nor that
    identity continuity was established.
20. **Given** a single-segment reservation without verified local dates
    and only a source timestamp whose written date has not been proven
    equivalent to the listing-local date, **When** fallback is evaluated,
    **Then** the written date is not treated as verified local timing.
21. **Given** eligible confirmed and checked-in candidates that are not
    conclusively expired, **When** selection applies the existing source
    status priority, **Then** that priority is preserved without presenting
    a date-only candidate as proven occupancy.

### User Story 2 - Preserve useful history and safe writes (Priority: P1)

As a property manager, I need trustworthy past bookings and authorized
reservation writes to remain useful, without turning unpublished or invalid
data into unsafe write authorization.

**Why this priority**: A past stay is not inherently bad data, and an
incorrect write can affect a different guest or a live access workflow.

**Independent Test**: Compare verified past, future, unpublished, and invalid
reservations across history, selection, and write attempts against the
resolved policy, including refresh and migration boundaries.

**Acceptance Scenarios**:

1. **Given** a verified historical reservation inside the configured history
   window, **When** history is requested, **Then** its age alone does not
   make it invalid or remove it from the historical data.
2. **Given** a booking that is valid but outside a selected listing or
   publication date window, **When** its write eligibility is determined,
   **Then** those publication filters alone do not refuse the write, and all
   existing identity and routing safeguards still apply.
3. **Given** a write or recovery attempt, **When** identity continuity,
   freshness, source provenance, or another applicable safety guard fails,
   **Then** no reservation write is dispatched.
4. **Given** a target rejected as unverifiable, including after a recovery
   refresh, **When** a reservation write is requested, **Then** the rejected
   record does not authorize the write and no write is dispatched on its
   authority.
5. **Given** a partially valid stay, **When** any reservation write is
   evaluated, **Then** the write is refused even when a good segment is
   published and its identity mapping is independently verified. The
   rejected segment supplies neither authorization nor routing evidence.
6. **Given** a failed refresh and previously verified data not shown to be
   invalid, **When** reservation information is displayed, **Then** the
   last-verified data remains visible and clearly marked stale, not newly
   verified or current.
7. **Given** a previously valid reservation or segment that a successful
   refresh shows is invalid, **When** that result is accepted, **Then** the
   bad data is removed rather than presented as a stale fallback, while
   unrelated independently verified data remains subject to its own rules.
8. **Given** a failed refresh and no previous verified data, **When**
   reservation information is requested, **Then** no prior reservation
   data or successful verification is invented.
9. **Given** a failed refresh followed by a write attempt or write recovery,
   **When** existing freshness, identity, continuity, or routing checks
   require an accepted fresh result, **Then** stale data and the failed
   refresh do not satisfy those checks or authorize the write.
10. **Given** previously stale data and a later successful refresh, **When**
    the current result is accepted, **Then** verified data is no longer
    labeled stale, and data now shown invalid is not restored.
11. **Given** an independently verified late-created current reservation
    with a `confirmed` or `checked_in` source status, **When** its write
    safeguards are met, **Then** creation after scheduled check-in alone
    does not refuse the write. When its current identity is unavailable or
    another safeguard fails, the write is refused without guessing a target.
12. **Given** a skipped malformed record, **When** a later refresh provides
    independently verified corrected data and all applicable safeguards
    pass, **Then** the corrected reservation can again become eligible for
    the relevant outputs or write checks; the prior skip is not treated as
    confirmed deletion.
13. **Given** a same-day reservation whose write identity remains unavailable
    after a reload, **When** a write is requested, **Then** feature 012's
    existing recovery limits and safety checks apply; if they do not
    establish a safe target, the action is refused and no write is sent.
14. **Given** a rejected reservation with an independently verified
    identity mapping, **When** the integration restarts, **Then** the
    mapping remains available only for conflict detection, does not appear
    among current writable identities, and does not authorize a write to
    that rejected reservation.
15. **Given** retained conflict-only evidence and a later contradictory
    identity observation, **When** the existing identity guard compares
    them, **Then** the applicable existing conflict outcome is enforced,
    no unsafe write proceeds, and confirmation values are not exposed.
16. **Given** a rejected record whose identity mapping cannot be
    independently verified, **When** the record is processed, **Then** no
    conflict-only mapping is created from unverified values and the record
    remains ineligible to authorize writes.
17. **Given** a previously rejected reservation that is later returned as
    independently verified current data, **When** its eligibility is
    evaluated, **Then** any write requires that new accepted data and all
    current guards; conflict-only evidence alone is not promoted into
    current writable identity or used as a source of published attributes.
    An existing confirmation attribute can be supplied by independently
    verified current data under the existing privacy rules.
18. **Given** conflict-only evidence storage is unavailable or reaches a
    bound, **When** required conflict safety cannot be established, **Then**
    affected safety decisions fail closed and the problem is reported
    without exposing confirmation values.
19. **Given** an upgrade with stored reservation data and fallback evidence,
    **When** revalidation is performed, **Then** independently valid data
    remains eligible and data that cannot be proven valid is dropped from
    display, writable identity, and fallback use. A separately verified
    identity mapping for a rejected reservation may remain conflict-only.
20. **Given** an upgrade or reload followed by a failed refresh, **When**
    earlier data has not been verified under these rules, **Then** that data
    is not displayed as a stale verified snapshot or used to authorize a
    write. Previously verified eligible data can still be shown as stale.
21. **Given** a partially valid reservation is later corrected so all raw
    segments are independently verified, **When** an accepted refresh and
    all existing write guards establish the target, **Then** its earlier
    rejected segment alone no longer prevents a write.
22. **Given** an identity was verified for one reservation source, **When**
    the source changes and the target's current provenance has not been
    independently established, **Then** no prior route is reused to write;
    existing source-switch and continuity guards fail closed.
23. **Given** a failed refresh after selected listings, status filters, or
    the publication window change, **When** stale data is shown, **Then**
    only records proven eligible under the current filters are published.
    A valid record excluded solely by those filters is not called invalid or
    refused a write solely for being unpublished.
24. **Given** a stale reservation whose verified checkout is now conclusively
    past, **When** current and upcoming candidates are built, **Then** it is
    excluded from those candidates but remains eligible for history within
    the current historical window.
25. **Given** an otherwise successful refresh and a failed fallback lookup
    or recheck for a previously verified reservation, **When** existing
    fallback rules permit retained values, **Then** only that reservation's
    retained values are shown as stale; unaffected current reservations
    remain fresh. The lookup failure does not prove deletion or identity
    continuity.
26. **Given** a fallback reservation retained after a failed recheck,
    **When** a write is requested, **Then** the existing session-served,
    source-routing, identity, and other guards decide eligibility; a target
    never successfully served in the session remains unroutable.
27. **Given** retained fallback values marked stale after a failed lookup,
    **When** that reservation is independently reverified by an accepted
    source result, **Then** its stale indication clears. On a cold start
    with no retained values, a failed lookup does not invent a reservation.

### User Story 3 - Understand and correct exclusions (Priority: P2)

As a property manager, I need actionable, privacy-safe explanations of
excluded data, so I can fix a configuration problem without exposing guest
information or receiving repeated warnings on every refresh.

**Why this priority**: Silent omissions hide data-quality problems; noisy or
revealing diagnostics create separate operational and privacy risks.

**Independent Test**: Repeat an exclusion across refreshes, correct it, and
reintroduce it; inspect warnings, repair notices, and diagnostics with
synthetic sensitive values.

**Acceptance Scenarios**:

1. **Given** a listing configuration problem that prevents verification,
   **When** affected data is excluded, **Then** the user receives an
   actionable listing-level repair notice without sensitive guest data.
2. **Given** the same unresolved exclusion is observed repeatedly,
   **When** refreshes continue, **Then** warnings do not repeat on every
   refresh; after the condition is resolved and later recurs, it can warn
   again.
3. **Given** a record containing synthetic guest data, confirmation codes,
   access codes, credentials, or raw upstream content, **When** an exclusion
   or verification error is reported, **Then** none of those values appears
   in logs, diagnostics, or user-facing repair messages.

### Edge Cases

- Date-only values may support date-appropriate uses, not exact-time uses.
  Missing wall times and daylight-saving gaps or folds cannot supply an
  exact instant; a verified checkout date of today remains an uncertain
  candidate rather than a claim of occupancy.
- A known local date alone does not establish the listing's current local
  date when its timezone cannot be verified. The date remains eligible for
  uses that do not require a current-local-day comparison; no assumed UTC
  day may decide selection, expiry, or window membership.
- On the same instant, listings in different verified timezones can have
  different local dates. Once the verified local day advances past a
  date-only checkout, that stay is historical rather than current/upcoming.
- An explicit, valid UTC listing timezone is different from the model's UTC
  default for a missing timezone. Neither an upstream UTC representation nor
  the existing fallback is declared correct or incorrect without evidence.
- A stay can move between listings or return to an earlier listing. A bad
  segment must not lend its dates, identity, status, listing, or route to
  another segment. Discarding an intervening segment does not make its
  surviving neighbors consecutive or justify merging their intervals.
- Missing, malformed, duplicated, conflicting, or unrecognized values are
  distinct from valid records excluded only by user-selected listing or date
  filters. A malformed record may be isolated only if the response and
  required safeguards remain verifiable; otherwise the refresh fails.
  Rejected records and partially valid reservations never authorize writes.
- Source or listing configuration changes, an unsuccessful or ambiguous
  refresh, a prior known-good snapshot, restart, corrected source data, and
  legacy-to-v3 fallback must follow the revalidation and routing rules.
  Failed refreshes preserve eligible last-verified data visibly as stale;
  none is represented as a successful current verification. A successful
  refresh showing data invalid is not a failed-refresh stale-data case.
- A failed fallback lookup can leave one reservation stale even when the
  overall refresh succeeds. A deferred recheck alone is not a failed
  lookup; previously stale data remains stale until independently reverified.
- Expired `confirmed` or `checked_in` lifecycle values are a synthetic
  risk described in #297, not a confirmed live Guesty behavior.
- A genuinely current stay may be created after its scheduled check-in.
  Its creation time is not evidence that the stay is invalid, and an
  unknown creation time does not itself justify discarding it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST publish only reservation information whose
  identity, listing association, recognized status, and timing are
  sufficiently verified for the particular output or action. It MUST NOT
  manufacture certainty from an absent, invalid, ambiguous, or stale value.
  Previously verified data may be displayed as stale under FR-012a without
  claiming it was reverified by the failed refresh.
- **FR-002**: The system MUST exclude reservations with unrecognized source
  statuses from reservation publication rather than pass through the raw
  value. Recognized status handling and existing cancellation behavior
  remain unchanged unless an approved decision expressly changes them.
- **FR-003**: The system MUST distinguish invalid or unverifiable data from
  valid data that is merely outside selected listings, configured
  publication windows, or current/upcoming selection.
- **FR-003a**: A reservation or segment MUST NOT be rejected, trimmed, or
  refused a write solely because it was created after its scheduled check-in
  or because its creation time is unavailable. Independently verified,
  in-window late-created reservations with recognized `confirmed` or
  `checked_in` source statuses MUST remain eligible for the appropriate
  outputs; date-only cases follow FR-004a. All applicable verification,
  identity, continuity, freshness, and routing guards continue to apply.
- **FR-003b**: When an individual reservation record is malformed, the
  system MUST exclude that record and publish the other independently
  verified records only if the entire response remains complete, batch
  integrity is established, and applicable safety checks can be satisfied.
  The exclusion MUST be reported under FR-009–011. If any of those
  conditions cannot be established, the refresh MUST fail closed, retain
  eligible last-verified data as stale, and MUST NOT publish an unsafe
  partial result. An invalid segment within an otherwise independently
  verifiable reservation is governed by FR-005a–005c, not automatically
  treated as an invalid reservation-level record.
- **FR-003c**: Excluding a malformed record MUST NOT by itself establish
  deletion, confirmed absence, identity continuity, or permission to write.
  It MUST NOT by itself retire identity or fallback evidence as though the
  target were confirmed absent. Independently established evidence and
  existing safety guards remain necessary. Independently verified identity
  evidence from a rejected record is limited to conflict detection under
  FR-008a.
- **FR-004**: A verified historical reservation MUST remain eligible for
  history within the configured historical window; an expired booking MUST
  NOT be classified as current or upcoming merely because its source status
  remains confirmed or checked in. For a verified exact checkout, the
  interval is no longer current at or after the checkout instant. For a
  date-only checkout, a date earlier than the verified listing-local
  current date establishes that it is no longer current or upcoming.
- **FR-004a**: An otherwise eligible reservation with a verified checkout
  date equal to the verified listing-local current date and no verified
  exact checkout MUST be included as a date-only, uncertain candidate for
  current selection and upcoming summaries before any result limit. It MUST
  NOT be presented as proven currently occupied or assigned a fabricated
  exact instant. If selected or shown in an upcoming summary, its temporal
  uncertainty MUST remain visible; a source lifecycle status MUST be
  distinguished from proof of occupancy. Existing listing, status, window
  and result-limit rules continue to apply.
- **FR-004b**: A checkout date may be compared with the current local day
  only when that day is established using reliable timezone evidence for
  the applicable listing. If the required timezone is not verified, the
  system MUST NOT treat the model's default UTC as authoritative or claim
  the stay is today, past, or future on that basis. Date-appropriate uses
  that do not require that comparison remain eligible under FR-007.
- **FR-004c**: After conclusively expired stays are excluded from current
  and upcoming candidates, the existing eligible-candidate status priority
  and ordering remain in force. A date-only candidate's source status MUST
  NOT be presented as a verified temporal status or proof of occupancy.
  Existing segment-aware priority for a verified current stay versus an
  upcoming segment remains in force.
- **FR-005**: The system MUST verify each published listing association and
  use only that listing's verified segment information. It MUST NOT infer a
  listing or interval from an unrelated segment, a parent, or an overall
  span without evidence that the attribution is valid.
- **FR-005a**: For a partially invalid multi-segment stay, the system MUST
  exclude the unverifiable segment from published data, caches, persisted
  state, and fallback candidate evidence while keeping each independently
  verified segment eligible for its appropriate outputs. The bad segment's
  values MUST NOT establish a good segment's interval, identity, listing,
  status, source provenance, route, or write authorization. A privacy-safe
  exclusion reason or count is not retention of the rejected segment.
- **FR-005b**: A segment is independently verified for an output only when
  all facts required for that output are established without relying on an
  invalid segment: a valid source reservation identifier and association,
  independently recognized source status, the segment's own verified
  listing association and date or interval at the precision needed, and
  independently established supporting relationships where applicable.
  Shared reservation-level identity and status may be used when verified
  independently of the bad segment; their presence alone does not verify a
  segment's listing or timing. Parseable values and another segment's
  success alone are not proof: the source and associations of required
  facts must also be validated, with evidence for their interpretation.
  A publication identity does not itself authorize a write, and feature
  008's distinction between read identity and confirmation-code write
  checks remains in force.
- **FR-005c**: The system MUST assess single-segment fallback eligibility
  using the original raw stay, not the number of segments surviving
  validation. It MUST NOT replace a survivor's dates or status with a
  reservation-level multi-segment span, join disjoint intervals across a
  rejected segment, or treat surviving non-adjacent segments as a verified
  handoff. A supported relationship between independently verified adjacent
  segments may be used only when independently established and does not
  rely on a rejected segment.
- **FR-006**: The system MUST assess the evidence for interpreting localized
  dates and source timestamps, including the feature 010 fallback, before
  using them to establish dates or exact instants. A timestamp's written
  date alone MUST NOT be treated as a proven listing-local date without
  evidence of that equivalence. An explicitly supplied valid UTC timezone
  MUST NOT be rejected solely for being UTC.
- **FR-007**: A missing, invalid, or ambiguous instant MUST NOT be presented
  as a verified exact instant, including when local wall time falls in a
  daylight-saving gap or fold. Verified date-only reservations MUST remain
  eligible for uses requiring only dates, such as date-based history or a
  date-capable calendar output, without inventing precise times. Outputs
  that require an exact instant MUST NOT use an unknown instant as verified.
- **FR-007a**: A timezone or instant supported by independent reliable
  evidence MUST be usable according to its verified precision, whether or
  not the listing supplied an explicit timezone. A missing timezone MUST
  NOT be assumed to mean UTC merely because of a model default. The
  sufficiency of evidence for source timestamp and local-date
  interpretation MUST be documented and tested; absent evidence does not
  establish an exact instant.
- **FR-008**: Existing identity continuity, ambiguity, persisted-baseline,
  freshness, source-provenance, privacy, and write-routing safeguards MUST
  NOT be bypassed. A well-formed verified target MUST NOT be refused solely
  because it is outside selected listings or the local publication window.
  A rejected or unverifiable record MUST NOT authorize a write. No write
  may proceed solely because a refresh completed or because a good segment
  was published. A rejected segment MUST NOT authorize a write or supply
  routing evidence.
- **FR-008a**: The system MUST retain an independently verified
  reservation-ID-to-confirmation-code mapping associated with a rejected
  reservation across restarts when it can be safely stored within the
  bound, **only** for identity-conflict detection. If it cannot be safely
  retained, FR-008c applies.
  The mapping and any minimum verified safety metadata MUST be distinguished
  from current writable identity and MUST NOT appear in the current writable
  identity set. It MUST NOT authorize a rejected reservation's write,
  establish or advance continuity, establish absence, supply write routing,
  or be promoted to current eligibility merely because no conflict was
  found. A later independently accepted record may establish current
  eligibility only through all existing safeguards.
- **FR-008b**: A conflict-only mapping MUST be independently verified under
  applicable identity and source safeguards; parseable values or a rejected
  payload alone are not proof. Retained conflict-only evidence MUST NOT
  include the malformed payload or invalid fields. Confirmation values MUST
  NOT be disclosed from conflict-only evidence in logs, diagnostics, repair
  messages, or user-facing output. An independently verified accepted
  current record may supply its existing confirmation attribute under
  existing privacy rules, even if it matches a retained conflict-only value;
  that attribute MUST NOT be sourced from the conflict-only evidence.
  Existing same-source and cross-source conflict outcomes MUST remain
  distinct and unchanged.
- **FR-008c**: Conflict-only evidence storage MUST be bounded and preserve
  safety across restarts. Capacity, eviction, or persistence failure MUST
  NOT silently make a conflict appear absent or permit a write whose
  identity safety cannot be established. If needed safety cannot be
  established, affected decisions MUST fail closed and report the problem
  without disclosing confirmation values. No arbitrary retention duration
  or numerical capacity is specified here.
- **FR-008d**: A reservation with any rejected raw segment MUST NOT appear
  as a current writable identity or authorize a reservation-level write,
  even if other segments are published and its identity mapping is
  independently verified. It may become a write target only after every
  raw segment has an independently verified listing association and
  date-level interval at minimum, every fact required by the requested
  action is independently verified, and all existing guards succeed.
  Verified date-only segments do not require a fabricated exact instant.
- **FR-009**: An exclusion MUST be observable through a privacy-safe reason.
  A correctable listing-level configuration cause MUST produce an actionable
  repair notice identifying the affected listing safely. Notices MUST cease
  to describe a problem as active once it is verified resolved.
- **FR-010**: Logs, diagnostics, and repair messages for validation and
  recovery MUST NOT include guest personal information, confirmation codes,
  access codes, credentials, or raw upstream payloads. Untrusted values
  MUST NOT be echoed merely to explain a failure.
- **FR-011**: Repeated observation of the same unresolved exclusion MUST
  emit one initial warning, not another warning on each refresh; repeats
  MUST be suppressed or reported at lower severity with the same privacy
  controls. Repeat tracking MUST be bounded and MUST NOT turn capacity
  churn into warning spam. A condition verified resolved and later
  recurring MUST be eligible for a new warning.
- **FR-012**: Migration and recovery MUST follow FR-012a–012h and MUST NOT
  silently weaken identity or routing protection.
- **FR-012a**: If a reservation refresh fails and last-verified data has
  not been shown invalid, the system MUST continue to show that data
  clearly marked stale in user-facing reservation outputs, with an
  observable stale indication for consumers. The failed refresh MUST NOT be
  represented as a successful verification or make stale data satisfy any
  freshness, identity, continuity, or routing guard it would otherwise
  fail. If there is no last-verified data, the system MUST NOT invent it.
  Before publication, stale data MUST still satisfy the current listing,
  status, and date-window filters using verified evidence. A stay whose
  verified checkout has conclusively passed MUST NOT remain current or
  upcoming merely because it was selected before the failure. Data whose
  filter or temporal eligibility cannot be verified MUST NOT be presented
  as eligible for that output. Filtering valid data from publication MUST
  NOT itself make it invalid or alter write eligibility. No staleness
  duration or automatic expiry is specified by this decision.
- **FR-012b**: When an accepted successful refresh shows a previously
  published record or segment is invalid or unverifiable, the system MUST
  remove the affected bad data from publication and retained reservation
  data rather than preserve or restore it as stale. Unaffected independently
  verified segments and records remain subject to their own rules. A
  separately verified conflict-only mapping may be retained under FR-008a;
  that mapping is not the rejected reservation payload or current writable
  identity. A subsequently failed refresh MUST NOT revive the rejected data.
- **FR-012c**: After a later accepted successful refresh, the system MUST
  remove the stale indication from currently verified information while
  continuing to exclude data shown invalid. A refresh attempt alone MUST
  NOT clear the indication.
- **FR-012d**: On upgrade, the system MUST revalidate previously stored
  reservation records, segments, snapshots, timing and fallback evidence
  under these rules before displaying them or using them for writes or
  fallback. Anything that cannot be proven valid MUST be dropped from
  publication, retained reservation data, current writable identities, and
  fallback use. Prior acceptance alone is not proof of verification. An
  independently verified mapping for a rejected reservation may remain
  only as conflict-only evidence under FR-008a–008c.
- **FR-012e**: Upgrade, reload, or source switching MUST NOT treat dropped
  or unverifiable data as confirmed absent, establish continuity, choose
  a write route, or bypass existing baseline and freshness requirements.
  Independently verified identity baselines remain subject to their
  existing safeguards. When required evidence cannot be established or
  stored, affected operations MUST fail closed; an unavailable prior
  record MUST NOT be replaced with a guessed one.
- **FR-012f**: On later correction, the system MUST independently verify
  the current record and applicable provenance before making it available
  or writable again. Existing recovery limits, continuity, freshness,
  routing, and write-dispatch safeguards remain in force; conflict-only
  evidence is not promoted into current writable identity.
- **FR-012g**: When an individual fallback lookup or recheck fails without
  confirmed absence or retirement but the overall refresh succeeds, any
  retained previously verified reservation values permitted by feature 008
  MUST be marked stale for that reservation and its projections. Other
  independently verified records MUST NOT be marked stale solely due to
  that failure. A deferred recheck alone is not a failed lookup, and a
  record already stale after a failure stays stale until independently
  reverified. A cold start without retained values MUST NOT invent them.
  The freshness indication MUST be source-neutral and MUST NOT expose
  fallback provenance through reservation sensor attributes.
- **FR-012h**: A successful accepted source result that independently
  reverifies a fallback reservation MUST clear that reservation's stale
  indication. Per-record staleness MUST NOT by itself weaken or replace
  feature 008 and 012 fallback write guards: a previously served target
  remains routable after a failed recheck only under the existing
  session, provenance, identity, and other safeguards; a retained-only
  target never served in the session remains unroutable.

### Compatibility and Evidence

- Feature 008 FR-026 passes unrecognized statuses through; FR-002 proposes
  to supersede that rule for publication. Feature 011 FR-008/009 retains
  unknown-time records and can retain records when the timezone is absent.
  Date-only uses remain eligible, and checkout-day candidacy is explicitly
  uncertain. Comparing to today's local date requires reliable timezone
  evidence rather than the missing-zone default. Feature 010 and feature
  011 permit a narrow single-segment date fallback; whether its inputs prove
  a local date requires evidence, not an assumption.
- Feature 008 supports per-listing multi-segment projection; feature 010
  identifies risk when invalid raw segments leave only one survivor.
  FR-005a–005c require independent segment verification, preserve original
  raw-stay eligibility, and prohibit deriving a survivor's projection from
  an unrelated reservation-level span. Existing verified parent attribution
  and eligible handoffs remain available only when independently supported.
- Feature 012 and the owner decision in #293 allow a retrieved, coded target
  to pass existing write guards without a selected-listing or local-window
  publication restriction. This approved behavior is preserved for
  well-formed verified targets. Rejected or unverifiable records cannot
  authorize a write. Independently verified evidence associated with a
  rejected record may persist only for conflict detection, not as current
  writable identity. A reservation with a rejected segment is treated as
  ineligible for reservation-level writes until all segments are verified;
  independently verified segments remain eligible for their read outputs.
- Feature 008 FR-006 requires complete, consistent pages and FR-032 requires
  failed refreshes on identity ambiguity. FR-003b–003c allow an isolated
  malformed-record skip only when those integrity and safety conditions
  are independently satisfied; a skip is not confirmed absence or
  continuity evidence.
- Feature 003/008 and #297 intentionally retain configured history. A
  valid past stay can be useful even when it must not be current or upcoming.
- Feature 008 FR-006, FR-008 and FR-032 retain last-known-good data after
  specified refresh failures. This specification preserves visibility of
  last-verified data as explicitly stale after failure, without treating it
  as reverified or bypassing write guards. An accepted refresh that shows
  data invalid must remove it instead of restoring it as stale.
- Feature 008 FR-050a and FR-054 retain eligible fallback values after an
  individual lookup failure even when a refresh succeeds. FR-012g–012h
  add per-reservation staleness without altering the existing distinction
  between a previously served target and a retained-only target. Freshness
  remains source-neutral, preserving FR-057's prohibition on exposing
  fallback provenance through sensor attributes.
- The current implementation compares retrieved identities before listing
  and window publication filtering. The model supplies `UTC` when listing
  timezone is missing but separately records whether it was explicit.
  Feature 010 fallback takes the parsed timestamp's written date; that is
  not proof of the listing-local date. These are source observations, not
  claims about Guesty's live contract.
- The independent #317 refresh-context and #316 shutdown concerns do not
  decide this feature's policy. Their safety requirements may not be
  weakened.
- #223 remains open for outstanding live verification. Evidence may include
  authoritative Guesty documentation and safely obtained representative
  observations; uncertainty must be identified rather than presented as
  proof. This draft does not claim release or new live access.
- The #223 reporter observed on Guesty 1.0.0 a same-day reservation created
  after scheduled check-in whose write was refused for unknown identity even
  after reload. The aggregate observations do not establish whether the
  target was returned, trimmed, or lacked identity evidence. No cause is
  inferred and no claim is made that #315 has resolved this case in a live
  system.

### Key Entities

- **Reservation**: A source booking with an identity, lifecycle status,
  and one or more listing-associated stay intervals.
- **Stay segment and projection**: A listing-specific interval and its
  independently verified representation for that listing.
- **Current identity and provenance**: Current accepted evidence used by
  existing continuity, freshness, and routing safeguards; it is not the
  same as publication and does not alone authorize a write.
- **Conflict-only identity evidence**: An independently verified mapping
  associated with a rejected reservation, retained only to detect identity
  conflicts, not to establish current writability or continuity.
- **Publication scope**: The configured listings, historical/future window,
  and output-specific selection that determine visibility of valid data.
- **Verification issue**: A privacy-safe explanation of a data or
  configuration problem and its current resolution state.
- **Stale snapshot**: Previously verified information displayed after a
  failed refresh, explicitly distinguished from a successful current
  verification and excluding data subsequently shown invalid.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In synthetic examples of unrecognized status, unverified
  identity or listing, and insufficient timing precision, zero values are
  represented as verified or used for an action requiring greater
  certainty. Verified date-only examples remain available for tested
  date-appropriate uses without a fabricated instant.
- **SC-002**: In all verified historical examples within the configured
  history window, age alone causes zero removals; in all conclusively
  expired examples, zero are classified current or upcoming.
- **SC-003**: In a scenario with more than ten conclusively expired
  confirmed or checked-in bookings and at least one valid future confirmed
  booking, the future booking appears within the ten-item upcoming limit.
- **SC-004**: Across synthetic exclusion and recovery examples, zero
  sensitive values or raw payloads appear in logs, diagnostics, or repair
  messages; one unresolved exclusion produces one initial warning and zero
  repeat-refresh warnings, and a resolved-then-recurring exclusion produces
  a new warning.
- **SC-005**: In synthetic valid, invalid, unselected, and historical
  examples, 100% of exclusions and publication-scope differences produce
  the expected privacy-safe diagnostic reason or observable selection
  outcome, without categorizing valid unselected data as invalid.
- **SC-006**: Across synthetic upgrade, reload, source-switch, and correction
  examples, zero unverified stored data is displayed or used for writes or
  fallback; zero failed identity or routing safeguards permit a write.
- **SC-007**: In synthetic write examples, 100% of otherwise guarded valid
  targets are treated identically whether published or excluded solely by
  selected-listing or local date-window filters; zero rejected records
  authorize writes, including after recovery.
- **SC-008**: In synthetic multi-segment examples with invalid first, middle,
  or last segments, 100% of independently verified survivors remain eligible
  for their own outputs; zero rejected segment values are retained,
  published, used to infer a survivor's details, or used to authorize or
  route a write. No raw multi-segment example receives a single-segment
  fallback merely because filtering leaves one survivor.
- **SC-009**: In synthetic checkout-day examples, 100% of otherwise eligible
  date-only stays with a verified local checkout date of today are included
  as uncertain candidates without a claimed instant or occupancy. In
  examples with no verified local day, zero use the default UTC timezone to
  assert a date-based temporal classification. In examples whose verified
  local date is after checkout, zero are current or upcoming candidates.
- **SC-010**: In synthetic failure and recovery examples, 100% of displayed
  last-verified snapshots are marked stale after a failed refresh, zero
  failures are treated as successful reverification or bypass a write
  guard, and zero records or segments shown invalid by a later accepted
  refresh are restored from a stale snapshot.
- **SC-011**: In synthetic complete, safe responses containing one malformed
  record, 100% of independently verified records remain eligible and the
  malformed record is excluded. In examples with unverifiable completeness,
  batch integrity, or applicable safety, zero unsafe partial results are
  published; zero skips alone establish deletion or identity continuity.
- **SC-012**: In synthetic late-created and same-day examples covering both
  `confirmed` and `checked_in` statuses and exact and date-only precision,
  zero otherwise verified in-window reservations are excluded solely due
  to creation time. Every write lacking independently verified identity or
  another required safeguard is refused; no failed write is reported as
  successful.
- **SC-013**: Across synthetic rejection, restart, correction, and storage
  bound examples, 100% of independently verified conflict-only mappings
  remain conflict-detectable when safely retained; zero appear as current
  writable identities or authorize a rejected reservation's write. No
  malformed payload, invalid field, or conflict-only confirmation value is
  exposed, and safety failures never bypass conflict or continuity safeguards.
- **SC-014**: In synthetic partially valid multi-segment examples, zero
  reservation-level writes proceed while any raw segment is rejected;
  independently verified segments remain eligible for their read outputs.
  Corrected cases become writable only after all guards independently pass.
- **SC-015**: In synthetic failed-refresh and fallback-recheck examples,
  100% of shown retained values are marked stale at the affected scope;
  zero records outside current publication filters or conclusively expired
  stays are shown as current/upcoming. Unaffected verified records remain
  fresh, and fallback write outcomes match the existing routing safeguards.

## Assumptions

- The existing configured historical and future windows and supported
  lifecycle vocabulary remain in scope; changing their defaults is not
  implied.
- The integration's existing reservation-facing outputs are in scope;
  calendar consumers are covered where they exist, not as a requirement to
  add a new calendar entity.
- Tests and evidence can use synthetic data. Live Guesty verification is a
  separate outstanding activity under #223 and is not available here.
- No release, live operation, implementation, plan, or task generation is
  included in this specification-only work.

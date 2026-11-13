<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Reservations v3 API Migration

**Feature Branch**: `008-reservations-v3`
**Created**: 2026-11-13
**Status**: Draft
**Tracking Issue**: [#223][issue-223]
**Supersedes behaviour from**: Feature 003 (Reservations)
**Input**: User description: "Migrate reservation retrieval and reservation
write actions from Guesty's legacy Open API reservation resources to the
Reservations v3 resources before Guesty retires the legacy endpoints on
2027-03-01, preserving today's entities, states and attributes for existing
users, and fixing the silently-ignored reservation filters reported in
[#204][issue-204]."

## Context and Problem Statement

Guesty has published a [reservation migration guide][guesty-migration] and a
[changelog entry][guesty-changelog] announcing that the legacy reservation
resources under `/reservations` **retire on 1 March 2027**. After that date,
calls to them stop working.

This integration today retrieves reservations through the legacy curated
search `GET /v1/reservations` (`custom_components/guesty/api/const.py:88-89`)
and performs its two reservation write actions through the catch-all
`PUT /v1/reservations/{id}` (`custom_components/guesty/api/actions.py:56-137`).
Both are retiring. If nothing changes, every reservation sensor, every
reservation-driven automation, and the note and status actions stop working on
2027-03-01.

Three further problems make this more than a mechanical endpoint swap:

1. **Related data is no longer embedded.** The vendor guide states: "Guest,
   financial, and payment data are no longer embedded in the response. You
   fetch them separately, so some flows that were one call may now require two
   or three requests." Today's guest name, phone, email and the three
   financial sensors are all sourced from fields that v3 does not return.
2. **One endpoint per operation.** The guide states that the single
   `PUT /v1/reservations/{id}` that accepted any field "is replaced by a
   dedicated endpoint for each operation — dates, notes, status, and so on."
   The integration's read-modify-write note action has no v3 equivalent in its
   current shape.
3. **Listing assignment changed shape.** v3 is documented to carry no
   top-level `listingId`; assignment is derived from a `stay[]` segment list.
   The coordinator's current "drop reservations whose listing is unknown"
   behaviour (`custom_components/guesty/coordinator.py:234-267`) needs an
   explicit rule under that model.

Issue [#204][issue-204] ("Reservation date and status filters are silently
ignored") is folded into this work. Its root cause is that the integration
sends top-level `checkIn`/`checkOut` parameters and a comma-joined `status`
parameter, which the legacy API accepts and ignores. v3 uses bracketed filters
and returns **HTTP 400** for a filter it does not support, so the class of
defect that produced #204 becomes loud rather than silent.

### Verification status of v3 behaviour

**No live Guesty v3 API access is available to this project.** Every statement
in this specification about v3 request shapes, response fields, filter
semantics, status values and error behaviour is **documentation-derived**:
taken from the vendor migration guide, the vendor reference pages linked from
it, issue [#223][issue-223], and the sibling project
`tykeal/homeassistant-rentalsync-bridge` specification
`specs/004-guesty-reservations-v3/`.

That sibling specification states in its own research notes that no live
Guesty API calls were ever made during its development. It is therefore
corroborating documentation, **not** evidence. Nothing in this specification
may be treated as verified. Every such claim is recorded in
[Assumptions](#assumptions) and governed by FR-041.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reservations keep working past the cutoff (Priority: P1)

As a property manager who already runs this integration, I want my reservation
sensors to keep reporting the same states and attributes after Guesty retires
the legacy reservation endpoints, so that the automations I have built against
them continue to run without my having to rewrite anything.

**Why this priority**: This is the entire reason for the feature. Without it,
every reservation entity in every installation goes stale on 2027-03-01 and
automations that control physical devices (door locks, thermostats) misfire.

**Independent Test**: Can be fully tested by pointing the integration at a
recorded v3 response set and verifying that the reservation status sensor for
each listing reports the same entity id, state and attribute names as the
legacy path produces for an equivalent legacy response set.

**Acceptance Scenarios**:

1. **Given** an installation whose reservation entities were created on the
   legacy path, **When** the integration is upgraded and the v3 path is
   active, **Then** every reservation entity keeps its existing entity id and
   unique id, and no entity is removed, renamed or re-created, with the single
   declared exception of the three deprecated financial sensors, which become
   unavailable rather than being removed (User Story 7, FR-043 to FR-049).
2. **Given** a listing with a confirmed future reservation, **When** the v3
   path refreshes, **Then** the reservation status sensor reports
   `awaiting_checkin` exactly as the legacy path did.
3. **Given** a listing with a guest currently in residence, **When** the v3
   path refreshes, **Then** the sensor reports `checked_in` and the check-in,
   check-out, localized date, confirmation code, guest count, night count and
   source attributes are all present with their existing attribute names.
4. **Given** an automation that triggers on a transition to `checked_in`,
   **When** the underlying reservation transitions under the v3 path, **Then**
   the automation fires exactly as it did on the legacy path.

---

### User Story 2 - Guest details without embedding (Priority: P1)

As a property manager, I want the guest name, phone number and email address
to keep appearing on the reservation sensor, so that my guest-messaging and
door-code automations, which read those attributes, keep working.

**Why this priority**: The guest attributes are consumed by Feature 005
(notify) and by user automations. v3 carries only a guest identifier, so
without an explicit replacement these attributes silently disappear — a
breaking change for every existing user.

**Independent Test**: Can be fully tested by serving a v3 reservation carrying
only a guest identifier plus a separate guest record, and verifying that the
sensor exposes the same guest attribute names and values as the legacy path.

**Acceptance Scenarios**:

1. **Given** a v3 reservation carrying a guest identifier and a guest record
   that carries a name, phone and email, **When** the data refreshes, **Then**
   the reservation sensor exposes the same guest name, phone and email
   attributes as the legacy path did.
2. **Given** the same guest appears on several reservations in one refresh,
   **When** the data refreshes, **Then** that guest is looked up at most once
   for that refresh.
3. **Given** a guest was looked up successfully in an earlier refresh, the
   reservation still references that same guest, and the cache entry is
   within its freshness period, **When** a later refresh occurs, **Then** the
   cached guest details are reused rather than refetched.
4. **Given** a guest lookup fails, **When** the data refreshes, **Then** the
   previously known guest details for that same guest identifier are retained,
   the reservation itself is still published, and the failure is logged.
5. **Given** a reservation's guest identifier has changed since the cached
   lookup, **When** the data refreshes, **Then** the cached details for the
   previous guest are **not** published for the new guest.
6. **Given** a cached guest entry has passed its freshness period, **When** a
   refresh occurs, **Then** that guest is looked up again, so a name, phone
   or email edited directly in Guesty is eventually picked up.

---

### User Story 3 - Note and status actions keep working (Priority: P1)

As a property manager, I want the "add reservation note" and "set reservation
status" actions to keep working after the legacy update endpoint retires, so
that my cleaning and check-in workflows keep writing back to Guesty.

**Why this priority**: These are write paths. A silent failure here means an
operator believes a note or a status change reached Guesty when it did not.

**Independent Test**: Can be fully tested by invoking each action against a
recorded v3 endpoint and asserting the request targets the dedicated v3
resource and that a rejection surfaces as an actionable error.

**Acceptance Scenarios**:

1. **Given** a reservation, **When** the add-note action is invoked, **Then**
   the integration writes through the dedicated v3 notes resource rather than
   the retiring catch-all update, and the existing note text is preserved
   alongside the appended text.
2. **Given** a reservation, **When** the set-status action is invoked with
   `checked_in` or `checked_out`, **Then** the integration writes through the
   dedicated v3 status resource.
3. **Given** the v3 endpoint rejects the write, **When** the action is
   invoked, **Then** the action raises an error naming the endpoint and the
   rejection reason rather than reporting success.
4. **Given** the identity guard (User Story 5) cannot confirm the
   reservation's identity, **When** a write action is invoked for that
   reservation, **Then** the action aborts without sending any write.

---

### User Story 4 - Date and status filters actually apply (Priority: P2)

As a property manager, I want the configured past/future day window and the
status filter to genuinely narrow what the integration fetches, so that a
large account does not page through years of irrelevant history on every
refresh.

**Why this priority**: This is issue [#204][issue-204]. It is a correctness
and request-volume defect today. Fixing it on the legacy path would be
short-lived work; v3 is where it gets fixed once.

**Independent Test**: Can be fully tested by asserting the exact outgoing
query for a known configuration and by asserting that an out-of-window
reservation present in the fake backend is not returned.

**Acceptance Scenarios**:

1. **Given** a configured window of 30 past days and 365 future days, **When**
   a refresh occurs, **Then** the outgoing request carries date filters in the
   form v3 documents, and reservations outside the window are not returned.
2. **Given** a configured status filter, **When** a refresh occurs, **Then**
   the outgoing request carries a status filter in the form v3 documents.
3. **Given** the backend rejects a filter with HTTP 400, **When** a refresh
   occurs, **Then** the refresh fails loudly with the rejection detail,
   previously known reservation data is retained, and the integration does
   **not** silently fall back to an unfiltered fetch.

---

### User Story 5 - A wrong-identity write can never happen (Priority: P1)

As a property manager whose door locks are driven by a reservation custom
field, I want the integration to refuse to act rather than act on a
reservation whose identity it cannot confirm, so that a lock code is never
written to the wrong guest's stay.

**Why this priority**: The integration writes custom fields keyed by
reservation identifier, and at least one real user publishes door-lock codes
into such a field. It is **unverified** whether a legacy reservation
identifier equals the v3 reservation identifier for the same stay. A mismatch
that went undetected would write a stranger's door code onto a live
reservation. Refusing to act is always the safe outcome.

**Independent Test**: Can be fully tested by serving a v3 reservation whose
confirmation code matches a previously seen reservation under a *different*
identifier, and asserting that the integration aborts without writing.

**Acceptance Scenarios**:

1. **Given** a reservation seen previously under the legacy path, **When** the
   v3 path returns a reservation with the same confirmation code and the same
   identifier, **Then** processing continues normally and the match is
   recorded in diagnostics.
2. **Given** a v3 reservation whose confirmation code maps to a different
   reservation identifier than the baseline records, **When** the refresh
   runs, **Then** the integration treats the identity as ambiguous, aborts the
   refresh, retains last-known-good data, performs no write, and logs an error
   that names both identifiers and tells the user how to roll back.
3. **Given** two baseline entries whose confirmation codes map to each
   other's identifiers in the v3 result, so that both previous identifiers are
   still present, **When** the refresh runs, **Then** the integration still
   treats the identity as ambiguous and aborts.
4. **Given** a reservation with no confirmation code, **When** the refresh
   runs, **Then** the reservation is published read-only and any write action
   targeting it aborts with an explanatory error.
5. **Given** an installation upgrading from a release that never recorded an
   identity baseline, **When** the first v3 refresh runs, **Then** the
   reservations are published, the baseline is recorded from that refresh,
   and any write action attempted before a baseline exists aborts with an
   explanatory error rather than proceeding on an empty comparison.
6. **Given** a recorded baseline, **When** Home Assistant restarts or the
   config entry reloads, **Then** the baseline is still available and writes
   remain permitted without waiting for a fresh legacy-path refresh.

---

### User Story 6 - Rollback without losing a release (Priority: P2)

As a property manager, I want a configuration option that puts the integration
back on the legacy reservation path if the v3 path misbehaves on my account,
so that I am not stuck waiting for a fix.

**Why this priority**: The v3 path cannot be validated against a live account
before release. A rollback switch converts an unverifiable migration from a
one-way risk into a recoverable one. It is P2 rather than P1 only because it
has no value if User Story 1 does not work.

**Independent Test**: Can be fully tested by toggling the option and asserting
which endpoint family the integration calls, with no entity churn across the
toggle.

**Acceptance Scenarios**:

1. **Given** a fresh or upgraded installation with no explicit choice,
   **When** the integration starts, **Then** the v3 reservation path is
   active.
2. **Given** the option is set to the legacy path, **When** the integration
   starts, **Then** reservation retrieval and reservation write actions use
   the legacy endpoints exactly as the previous release did.
3. **Given** the option is changed, **When** the integration reloads, **Then**
   reservation entity ids and unique ids are unchanged and no entity is
   removed or re-created; the three deprecated financial sensors, where they
   are already registered, keep their registry entries in both directions and
   only change between available (legacy path) and unavailable (v3 path),
   and where they have never been registered they may be registered for the
   first time on a switch to the legacy path (FR-045).
4. **Given** the legacy path is active and Guesty has retired it, **When** a
   refresh fails with a retirement-shaped error, **Then** the error message
   tells the user to switch the option back to v3.
5. **Given** the legacy path is active, **When** the integration starts,
   **Then** a deprecation warning naming the 2027-03-01 retirement date is
   logged.

---

### User Story 7 - Financial sensors deprecate cleanly (Priority: P2)

As a property manager who has `total_paid`, `balance_due` or `currency` on a
dashboard card or in an automation, I want to be told clearly that those
sensors are going away and to see them go unavailable rather than keep
reporting a number that is no longer being updated, so that I can remove or
replace them deliberately instead of acting on a stale figure.

**Why this priority**: The owner has decided (OQ-001) that reservation
financial data is not retrieved under v3. That is a user-visible breaking
change under constitution principle VII, so it must be communicated and must
fail visibly. It is P2 rather than P1 because no reservation state or
automation trigger depends on it, but a silently wrong monetary value would be
worse than any other outcome in this feature.

**Independent Test**: Can be fully tested by toggling the reservation API path
over a fixture whose legacy payload carries `money` values, and asserting that
the three sensors report values on the legacy path, report `unavailable` on
the v3 path, keep their entity ids across both, and that a deprecation notice
is surfaced exactly once on the v3 path.

**Acceptance Scenarios**:

1. **Given** an installation whose financial sensors exist from a legacy-path
   release, **When** the v3 path becomes active, **Then** all three sensors
   report `unavailable` and never report a value carried over from an earlier
   legacy refresh.
2. **Given** the v3 path is active, **When** a refresh occurs, **Then** no
   request is made to any guest-folio resource.
3. **Given** the v3 path is active and at least one of the three entities
   exists, **When** the integration starts, **Then** a deprecation notice
   naming the three sensors, the reason and the removal timeline is surfaced
   to the user once, and a deprecation warning is logged.
4. **Given** the option is set back to the legacy path, **When** the
   integration reloads, **Then** all three sensors report values again from
   the embedded legacy `money` object, under their existing entity ids.
5. **Given** the v3 path is active, **When** a refresh occurs, **Then** every
   other reservation entity, attribute and entity id is unaffected and the
   reservation refresh succeeds without any financial data.

---

### Edge Cases

- **A reservation whose stay spans more than one listing (mid-stay move).**
  v3 newly supports this. The reservation is attributed to every known listing
  that appears in its stay segments, so it is visible on each of them, the
  segment dates for that listing are used for that listing's attributes, and
  the per-listing state is derived from that segment rather than from the
  reservation-level status (FR-011a). A guest who has moved out of listing A
  into listing B is `checked_out` on A and `checked_in` on B, not
  `checked_in` on both.
- **A reservation whose stay segments are all for unknown listings.** It is
  skipped with a warning, matching today's behaviour (Feature 003, FR-017).
- **A canceled reservation that spans more than one listing.** The temporal
  per-listing rules of FR-011a do not apply: it reports `canceled` on every
  listing it touches, never `awaiting_checkin` on a future segment.
- **A reservation whose stay segments are partly known and partly unknown.**
  The known segments are published; the unknown segments are logged at debug
  level and ignored. The reservation is not dropped wholesale.
- **A listing excluded by Feature 007 listing filtering.** Reservations for
  that listing are not published, exactly as today — the filtering decision
  continues to be made on the listing side.
- **A reservation returned with the new `alteration` status.** This represents
  a proposed, not-yet-published change. It is never selected as the sensor
  state and never displaces a published reservation.
- **A reservation returned with the new `completed` status.** It is treated as
  equivalent to `checked_out` for sensor state purposes.
- **A long stay that began before the configured past window.** It must still
  be found, exactly as today's unbounded `checked_in` second query ensures.
- **A guest lookup that returns no record (deleted guest).** Guest attributes
  are omitted; the reservation is still published.
- **The guest lookup resource is rate limited.** Guest details fall back to
  the cached values and the refresh still publishes reservations.
- **A group reservation.** v3 newly supports these. Any segment referencing a
  known listing is published under that listing; no group-level entity is
  created.
- **A reservation returned with no stay segments at all.** It is skipped with
  a warning rather than published with no listing.
- **A page that claims more results while returning fewer than requested.**
  The refresh fails rather than silently truncating, because v3 provides no
  total count to cross-check against.
- **An unrecognized status value.** Passed through as-is and logged, matching
  today's behaviour (Feature 003, FR-025).
- **A dashboard card or template bound to a financial sensor under the v3
  path.** The sensor is `unavailable`, so the card shows it as unavailable
  and a template referring to it degrades the same way it already does when
  Guesty omits the value. It never shows a stale monetary figure.
- **An installation that has never run the legacy path.** The three financial
  sensors are never created at all and no deprecation notice is raised,
  because there is nothing the user can have bound an automation to. If such
  an installation is later switched to the legacy path, they are registered
  for the first time at that point (FR-045).

## Requirements *(mandatory)*

### Functional Requirements

#### Retrieval

- **FR-001**: System MUST retrieve reservations from the Guesty Reservations
  v3 curated search resource (`GET /reservations-v3/search`) when the v3 path
  is active, and MUST NOT call any resource the vendor migration guide lists
  as deprecated.
- **FR-002**: System MUST send the configured past/future day window as v3
  date filters in the documented bracketed form, and MUST verify by test that
  reservations outside the window are not returned. This resolves
  [#204][issue-204].
- **FR-002a**: Because the v3 date filters are documented to compare
  listing-localized dates while the configured window is an instant-based
  range, system MUST pad the server-side window so that it is a superset of
  the configured window, and MUST then apply a **new** instant-based
  client-side trim so that padded boundary results are not published. No such
  trim exists today — neither `get_reservations()` nor
  `ReservationsCoordinator` checks the date window, which is part of why
  [#204][issue-204] had no client-side compensation. The trim MUST NOT discard
  a stay that is in progress at refresh time (FR-004).
- **FR-003**: System MUST send the configured status set as a v3 status
  filter in the documented bracketed form. This resolves the status half of
  [#204][issue-204].
- **FR-003a**: When translating the configured status set into a v3 status
  filter, system MUST request each configured status together with its v3
  equivalents. In particular, requesting `checked_out` MUST also request
  `completed`, because the default configured set is `confirmed`,
  `checked_in`, `checked_out`, `canceled` (`api/const.py:116-124`) and a
  server-side filter would otherwise exclude completed reservations before
  FR-023 could map them. This translation MUST NOT change the configured
  status set the user sees, nor the sensor state vocabulary.
- **FR-004**: System MUST preserve the *intent* of today's two-query merge —
  the date-and-status-filtered query plus the unbounded `checked_in` query
  that catches long stays whose check-in predates the window
  (`api/client.py:277-287`). The v3 implementation MUST guarantee that a stay
  in progress at refresh time is retrieved regardless of how far in the past
  it began, and MUST NOT rely on an unbounded sweep of account history to do
  so.
- **FR-005**: System MUST page the v3 search using the documented
  `skip`/`limit` paging with a maximum page size of 100, terminating on the
  documented `pagination.hasMore` indicator.
- **FR-006**: Because v3 returns no total result count, system MUST validate
  each page before accepting a refresh and MUST fail the refresh — retaining
  last-known-good data — rather than publish a partial result when a page is
  inconsistent (for example, a page claiming more results while returning
  fewer than the requested limit, a repeated identifier, or a page count
  exceeding a defined cap).
- **FR-007**: System MUST NOT send a field projection on the v3 search, which
  does not support one.
- **FR-008**: System MUST treat an HTTP 400 from the v3 search as a hard
  failure that surfaces the rejection detail to the user, and MUST NOT retry
  the request with filters removed.

#### Listing attribution

- **FR-009**: System MUST derive a reservation's listing assignment from its
  `stay[]` segments rather than from a top-level listing field, which v3 does
  not provide.
- **FR-010**: System MUST attribute a reservation to **every** known listing
  that appears in its stay segments, so that a mid-stay move is visible on
  both the origin and the destination listing.
- **FR-011**: For a listing attributed through a stay segment, system MUST use
  that segment's check-in, check-out, localized dates and guest count for that
  listing's sensor attributes, rather than the reservation-level span, when
  the reservation has more than one segment.
- **FR-011a**: Reservation selection and sensor state derivation MUST be
  **segment-aware** for a multi-segment reservation. The reservation-level
  status alone MUST NOT be used, because today's selection and state logic
  (`sensor.py:305-366`) keys on status only, which would mark both the origin
  and the not-yet-occupied destination listing as `checked_in` during a
  mid-stay move, and would let the future segment compete with the
  destination listing's actual current occupant. For each listing, the state
  MUST be derived from the segment that applies to that listing at the
  current time:
  - before the segment's check-in, the reservation counts as upcoming for
    that listing (`awaiting_checkin`), never as `checked_in`;
  - between the segment's check-in and check-out, a reservation-level
    `checked_in` status counts as `checked_in` for that listing;
  - after the segment's check-out, the reservation counts as past for that
    listing (`checked_out`), even while the reservation as a whole is still
    `checked_in` in another listing;
  - a reservation that is only upcoming for a listing MUST NOT outrank that
    listing's genuinely current occupant.
  A single-segment reservation MUST behave exactly as it does today.
  These temporal rules apply **only** to reservations whose reservation-level
  status is `confirmed` or `checked_in`. A `canceled`, `checked_out`,
  `completed`, `alteration` or unrecognized status MUST keep the
  status-derived behaviour of FR-023 to FR-026 for every listing it touches,
  so that a canceled multi-segment reservation can never appear as
  `awaiting_checkin` on a future segment's listing.
- **FR-012**: System MUST recognize the documented parent/sub-unit
  relationship carried on a stay segment, so that a reservation assigned to a
  sub-unit or to an unassigned parent unit type is still attributed to the
  listing the user tracks.
- **FR-013**: System MUST continue to skip, with a warning, any reservation
  that cannot be attributed to a known listing, preserving Feature 003 FR-017
  and the existing coordinator behaviour (`coordinator.py:234-267`). Partial
  attribution MUST NOT cause a drop: a reservation with at least one known
  segment MUST be published.
- **FR-014**: Listing selection and tag filtering from Feature 007 MUST
  continue to be applied on the listing side and MUST NOT be re-implemented in
  the reservation search. A reservation for a filtered-out listing MUST NOT be
  published.
- **FR-015**: The v3 listing filter is documented to match only the **first**
  stay segment. A reservation that begins in an untracked listing and moves
  into a tracked one is therefore excluded by the server before client-side
  attribution can see it, so neither attribution (FR-010) nor diagnostics
  (FR-042) can recover or even name the missing stay. System MUST therefore
  **NOT** use a server-side listing filter as part of the primary retrieval
  path. Retrieval MUST be completeness-preserving over the configured
  window and status set, with client-side attribution over all segments
  deciding which listings each reservation belongs to. Adopting a listing
  filter as a request-budget optimisation, and thereby accepting the coverage
  gap, is deferred to
  [OQ-008](#oq-008-may-a-server-side-listing-filter-trade-coverage-for-request-budget)
  and requires an explicit recorded decision.

#### Guest details

- **FR-016**: System MUST source guest full name, phone number and email from
  the Guesty `/guests` resource family, keyed by the guest identifier carried
  on the v3 reservation.
- **FR-017**: System MUST expose those guest values under the **same sensor
  attribute names** the legacy path uses today, so that existing automations
  and templates are unaffected.
- **FR-018**: System MUST cache guest lookups. A given guest MUST be looked up
  at most once per refresh. A successful lookup MUST be reusable across
  refreshes while the reservation still references that same guest identifier
  **and** the cache entry is within a bounded freshness period. The freshness
  period MUST be finite, so that a name, phone or email edited directly in
  Guesty is eventually picked up; v3 provides no `lastUpdatedAt` to detect
  such an edit, so time is the only available trigger. An entry that has
  expired MUST be refreshed on the next refresh that needs it, subject to
  FR-020 fallback on failure.
- **FR-019**: System MUST NOT publish cached guest details under a different
  guest identifier than the one they were fetched for.
- **FR-020**: A failed or rate-limited guest lookup MUST NOT fail the
  reservation refresh. Last-known guest values for the same guest identifier
  MUST be retained, and the failure MUST be logged.
- **FR-021**: Guest personal data MUST NOT appear in logs at **any** level,
  including debug, consistent with constitution principle X. Diagnostics added
  for this feature (FR-042) MUST be expressible without it, so that enabling
  debug logging to confirm a v3 assumption can never expose guest details.

#### Status handling

- **FR-022**: System MUST handle the documented v3 status values, including
  the newly documented `alteration` and `completed`.
- **FR-023**: `completed` MUST be treated as equivalent to `checked_out` for
  reservation status sensor state selection, preserving today's state
  vocabulary.
- **FR-024**: `alteration` represents a proposed change and MUST NOT be
  selected as a sensor state nor displace a published reservation for the same
  listing.
- **FR-025**: The reservation status sensor state vocabulary MUST remain
  exactly `no_reservation`, `awaiting_checkin`, `checked_in`, `checked_out`,
  `canceled`, plus pass-through of unrecognized values, preserving Feature 003
  FR-005, FR-006 and FR-025 and the existing selection priority
  (`sensor.py:305-366`).
- **FR-026**: Unrecognized status values MUST continue to be passed through
  as-is with an informational log rather than causing an error.

#### Write actions

- **FR-027**: The add-reservation-note action MUST write through the dedicated
  v3 notes resource (`PUT /reservations-v3/{reservationId}/notes`) instead of
  the retiring catch-all update, while preserving its current user-visible
  behaviour of appending to any existing note text.
- **FR-028**: The set-reservation-status action MUST write through the
  dedicated v3 status resource
  (`PUT /reservations-v3/{reservationId}/status`), preserving today's accepted
  values (`checked_in`, `checked_out`, `api/const.py:185-188`).
- **FR-029**: A rejected write MUST raise an actionable error naming the
  resource and the rejection detail. A write MUST NOT be reported as
  successful on a non-success response.
- **FR-030**: Reservation custom-field reads and writes MUST continue to use
  `/reservations-v3/{id}/custom-fields` unchanged; they are already migrated
  and out of scope for change.

#### Identity guard

- **FR-031**: System MUST maintain an identity baseline mapping confirmation
  code to reservation identifier, and before publishing a refresh or
  performing any reservation write MUST check each reservation's confirmation
  code against that baseline.
- **FR-031a**: The identity baseline MUST be persisted in config entry
  storage and MUST survive a Home Assistant restart and a config entry
  reload. The current reservation coordinator is created fresh on every setup
  (`__init__.py:283-289`) and persists nothing, so an in-memory baseline would
  be empty on every start and the guard would compare against nothing.
- **FR-031b**: The baseline MUST be established from whichever path produced
  the last successful refresh, so that an installation upgrading from a
  legacy-only release records its legacy identities on first use and v3
  identities thereafter.
- **FR-031c**: Until a baseline exists, system MUST **fail closed** for
  writes: reservations are still published, but any reservation write action
  MUST abort with an explanatory error rather than proceed on an empty
  comparison. An empty baseline MUST NOT be read as "no conflict found".
- **FR-032**: If any confirmation code's mapping to a reservation identifier
  differs from the baseline, system MUST treat the identity as ambiguous,
  MUST abort the refresh, MUST retain last-known-good data, MUST perform no
  write, and MUST log an error naming both identifiers and the rollback
  option. This applies to **every** changed mapping, not only to one whose
  previous identifier has disappeared from the result: a wholesale re-key that
  swaps two codes between two still-present identifiers (baseline `A→id1`,
  `B→id2`; result `A→id2`, `B→id1`) is exactly the failure this guard exists
  to catch, and must fail closed.
- **FR-033**: A reservation with no confirmation code MUST be publishable but
  MUST NOT be a valid target for a write action; a write targeting it MUST
  abort with an explanatory error.
- **FR-034**: The identity guard MUST cost no additional API requests.
- **FR-035**: On the first successful v3 refresh, system MUST log at
  informational level how many previously known reservation identifiers were
  matched, so a user can confirm identity continuity on their own account.

#### Rollout toggle

- **FR-036**: System MUST expose a configuration option selecting the
  reservation API path, with exactly two values: the v3 path and the legacy
  path.
- **FR-037**: The option's default MUST be the v3 path, for both new and
  upgraded installations, so that an unattended installation is compliant
  before 2027-03-01.
- **FR-038**: The option MUST be changeable through the existing options flow
  and MUST take effect on reload without removing, renaming or re-creating any
  entity.
- **FR-039**: When the legacy path is active, system MUST log a deprecation
  warning at startup naming the 2027-03-01 retirement date; and when a legacy
  call fails in a way consistent with retirement, the error MUST tell the user
  to switch the option to v3.
- **FR-040**: The legacy path MUST remain behaviourally identical to the
  previous release, so that it is a genuine known-good rollback target. The
  legacy option value and its code MUST be removable as a unit after
  2027-03-01.

#### Financial sensor deprecation

The repository owner decided on 2026-11-13 that the reservation financial
sensors are deprecated rather than re-sourced
([OQ-001](#oq-001-financial-sensors--resolved-deprecate)).

- **FR-043**: System MUST NOT integrate `GET /v1/guest-folio/overview`, nor
  any other guest-folio or payments resource, as part of this feature. No
  reservation financial data is retrieved on the v3 path, and no batched
  supplementary folio lookup is implemented.
- **FR-044**: On the **legacy** path, the `total_paid`, `balance_due` and
  `currency` sensors MUST continue to behave exactly as in the previous
  release, sourced from the embedded legacy `money` object, so that the
  rollback target remains genuinely known-good (FR-040).
- **FR-045**: On the **v3** path, system MUST NOT create the three financial
  sensors, and MUST NOT publish for them any value carried over from an
  earlier legacy refresh, any cached value, or any placeholder. Entity
  registry entries created by earlier releases MUST be retained, so the
  entities remain registered under their existing entity ids and unique ids
  and Home Assistant reports them as `unavailable`. For an installation that
  already has those registry entries, toggling the reservation API path in
  either direction MUST therefore cause no entity registry change (NFR-003,
  SC-008). An installation that has never run the legacy path has no such
  entries; if it is later switched to the legacy path, the three sensors are
  registered for the first time, which is an additive registration and not a
  removal, rename or re-creation.
- **FR-046**: When the v3 path is active and at least one of the three
  entities already exists in the entity registry, system MUST surface a
  user-visible deprecation notice that names the three sensors, states that
  Guesty v3 no longer provides the data, points at the reservation API path
  option as the temporary rollback, and states the removal timeline of
  FR-048. The notice MUST be raised at most once per config entry per Home
  Assistant start and MUST NOT be raised on an installation where none of the
  three entities exists. A deprecation warning MUST additionally be logged at
  warning level in that same case. The delivery mechanism — a Home Assistant
  repairs issue, a logged warning alone, or both — is
  [OQ-009](#oq-009-which-deprecation-notice-mechanism); the repository has no
  existing repairs-platform precedent.
- **FR-047**: The deprecation MUST be documented as a breaking change in the
  release notes of the release that ships this feature and in the
  integration's user documentation, naming the three entities, stating that
  no replacement data source is provided, and describing the reservation API
  path option as a temporary rollback that itself expires on 2027-03-01. This
  satisfies the explicit-declaration requirement of NFR-004 and constitution
  principle VII.
- **FR-048**: The removal timeline MUST be: the three sensors are
  **unavailable** on the v3 path from the release that ships this feature;
  they are **removed outright** — entity creation code, entity registry
  entries, strings and the FR-046 notice deleted together — in the same
  release that removes the legacy path under FR-040, which MUST NOT ship
  before 2027-03-01 so that the rollback remains available for as long as the
  legacy endpoints exist.
- **FR-049**: No entity, attribute or entity id other than the three named
  financial sensors may regress as a result of this deprecation. The absence
  of financial data MUST NOT fail, delay or degrade a reservation refresh, and
  every attribute listed in NFR-004 MUST remain unaffected.

#### Verification discipline

- **FR-041**: Every v3 behaviour this feature relies on that cannot be
  verified without live API access MUST be recorded as an explicit assumption
  in this specification, and MUST be confirmed against a live Guesty account
  before the feature is declared stable. The list MUST be reproduced in the
  implementation plan as a pre-release checklist.
- **FR-042**: System MUST emit diagnostics sufficient to confirm each
  assumption from a user's debug log without exposing personal data: at
  minimum, per refresh, the number of pages fetched per resource, the
  reservation count by status, a histogram of stay-segment counts, and the
  identity-match count from FR-035.

### Non-Functional Requirements

- **NFR-001 (Request volume)**: In **warm-cache steady state** — guest cache
  populated and within its freshness period, no reservation changes — requests
  per refresh under the v3 path MUST NOT exceed the legacy path's count for an
  equivalent account. In **cold-cache or cache-expiry** refreshes, the v3
  path MAY exceed the legacy count, but the excess MUST be separately
  bounded: it MUST be at most one lookup per distinct guest needing a refresh,
  these lookups MUST be spread or batched so that a single refresh cannot
  exhaust the rate budget, and the bound MUST be stated in the plan.
  Because FR-015 forbids a server-side listing filter, **search** pagination
  necessarily scales with the account-wide result set for the configured
  window and status set, not with the number of tracked listings; that is the
  accepted cost of completeness and is the subject of OQ-008. The bound that
  does apply per tracked listing is on **supplementary** lookups: guest
  requests MUST be issued only for reservations attributed to a tracked
  listing, so that untracked reservations returned by the search cost nothing
  beyond the page they arrive on. No folio lookups exist, because FR-043
  forbids them.
- **NFR-002 (Rate limiting)**: The additional guest lookups MUST go through
  the existing rate-limit-aware client with backoff, and MUST NOT be able to
  exhaust the account's rate budget on a 15-minute poll
  (`coordinator.py:175-198`).
- **NFR-003 (Entity compatibility)**: Entity ids, unique ids, device
  assignment, entity categories and state vocabularies MUST be unchanged. No
  entity may be removed, renamed or re-created by this migration. The three
  deprecated financial sensors are the one declared exception and are
  nonetheless held to the weaker form of this rule: their entity ids, unique
  ids, device assignment and entity category are unchanged and their registry
  entries are retained; only their availability changes (FR-045, FR-048).
- **NFR-004 (Attribute compatibility)**: Every attribute the reservation
  status sensor exposes today (`sensor.py:370-504`) MUST keep its name and
  meaning: reservation identifier, UTC check-in/check-out, localized
  check-in/check-out dates, planned arrival, planned departure, guest name,
  guest phone, guest email, confirmation code, guest count, nights count,
  source, listing identifier, the upcoming/active reservation summary list,
  and reservation custom fields. Any attribute that cannot be preserved MUST
  be called out as a breaking change in this specification and in release
  notes, not dropped silently. The loss of the `total_paid`, `balance_due`
  and `currency` sensors on the v3 path is exactly such a declared breaking
  change and is specified by FR-043 to FR-049.
- **NFR-005 (Graceful degradation)**: A failure in the *supplementary* guest
  lookup MUST degrade only the affected attributes, never the reservation
  refresh as a whole. A failure in the *primary* reservation search MUST
  retain last-known-good data (Feature 003 FR-014).
- **NFR-006 (Event loop)**: All added I/O MUST be async and MUST NOT block the
  Home Assistant event loop (constitution principle VIII).
- **NFR-007 (Library isolation)**: All v3 request construction, response
  validation and mapping MUST live in the library layer with zero Home
  Assistant imports (Feature 003 FR-024).
- **NFR-008 (Testability)**: The v3 path MUST be fully testable without a live
  Guesty connection, using the repository's existing dictionary-fixture
  pattern (`tests/conftest.py:535-660`).
- **NFR-009 (Privacy)**: Guest names, phone numbers, email addresses and
  confirmation codes MUST NOT be logged at any level, including debug.

### Key Entities

- **Reservation (v3)**: A booking identified by a reservation identifier,
  carrying a status, a confirmation code, a booking source, a guest
  identifier, reservation-level check-in/check-out instants and localized
  dates, notes, custom field values, and an ordered list of stay segments. It
  no longer carries an embedded guest, an embedded financial summary, a
  top-level listing identifier, or a last-updated timestamp.
- **Stay Segment**: One occupancy interval of a reservation in one listing.
  Carries a listing identifier, an optional parent listing identifier, UTC
  check-in/check-out, localized dates, and a guest count. A reservation has at
  least one; more than one indicates a mid-stay move or a group booking.
- **Guest**: A person record fetched separately by guest identifier, supplying
  the full name, phone and email the sensor exposes today. Cached.
- **Reservation Financial Summary**: Total paid, balance due and currency. No
  longer part of the reservation and, by the owner's decision recorded in
  [OQ-001](#oq-001-financial-sensors--resolved-deprecate), not re-sourced.
  The three sensors fed by it are deprecated (FR-043 to FR-049).
- **Reservation API Path Setting**: The rollout toggle (FR-036 to FR-040).
- **Identity Record**: The previously known mapping from confirmation code to
  reservation identifier used by the identity guard (FR-031 to FR-035).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After the migration, zero requests go to any resource the vendor
  migration guide marks as deprecated, verified by a test that fails if a
  deprecated path is requested on the v3 path.
- **SC-002**: For an equivalent account, every reservation entity's id, state
  and attribute key set is identical between the legacy path and the v3 path,
  verified by a differential test over a shared fixture set. The three
  deprecated financial sensors are excluded from the state comparison and
  asserted separately by SC-014; their entity ids are still compared.
- **SC-003**: For the same fixture account with a **warm guest cache** and no
  reservation changes, requests per refresh on the v3 path are less than or
  equal to the legacy path's, asserted by a request-counting test that pins
  the cache state. A separate test pins the **cold-cache** budget: a fixture
  of ten reservations for ten distinct uncached guests performs at most the
  search requests plus one lookup per distinct guest, and that number is
  asserted explicitly rather than compared to the legacy path.
- **SC-004**: A configured 30-day-past / 365-day-future window demonstrably
  excludes out-of-window reservations present in the fake backend, closing
  [#204][issue-204].
- **SC-005**: A stay in progress whose check-in is 200 days in the past is
  still reported as `checked_in`.
- **SC-006**: A reservation with stay segments in two tracked listings appears
  on both listings' sensors with each listing's own segment dates.
- **SC-007**: An ambiguous-identity fixture causes zero writes and zero
  published changes, and produces exactly one actionable error.
- **SC-008**: Toggling the reservation API path setting and reloading an
  otherwise unchanged installation whose entities are all already registered
  produces zero entity registry changes. The only registry change this toggle
  may cause is the first-time registration of the three financial sensors on
  an installation that has never run the legacy path and is then switched to
  it (FR-045). Normal first-time setup and the discovery of new listings or
  custom fields are unaffected and continue to register entities as today.
- **SC-009**: A guest-lookup outage lasting one full refresh leaves every
  reservation published and every previously known guest attribute intact.
- **SC-010**: Each distinct guest is fetched at most once per refresh; a
  steady-state refresh with no reservation changes and a guest cache within
  its freshness period performs zero guest lookups; and a refresh taken after
  the freshness period has elapsed refetches the affected guests.
- **SC-012**: A multi-segment reservation that has checked out of listing A
  and into listing B reports `checked_out` on A and `checked_in` on B at the
  same refresh, and before the move reports `checked_in` on A and
  `awaiting_checkin` on B without displacing B's current occupant. The same
  reservation, when canceled, reports `canceled` on both listings.
- **SC-013**: An installation with no recorded identity baseline publishes
  reservations normally and rejects every reservation write with an
  explanatory error; after one successful refresh the baseline exists,
  survives a reload, and writes are permitted.
- **SC-014**: Over a fixture whose legacy payload carries `money` values, the
  `total_paid`, `balance_due` and `currency` sensors report those values on
  the legacy path and report `unavailable` on the v3 path, with identical
  entity ids on both and, for an installation where they are already
  registered, zero entity registry changes across the toggle; no request is
  made to any guest-folio resource on either path; and the deprecation notice
  of FR-046 is raised exactly once when the v3 path starts with those
  entities present and not at all when they are absent.
- **SC-011**: Every assumption in this specification appears as an item in the
  plan's pre-release live-verification checklist (FR-041).

## Assumptions

Items marked **[UNVERIFIED]** are documentation-derived and have **not** been
confirmed against a live Guesty account. FR-041 requires each of them to be
confirmed before this feature is declared stable.

### About identity and continuity

- **[UNVERIFIED]** A legacy reservation `_id` equals the v3 `reservationId`
  for the same stay. The identity guard (FR-031 to FR-035) exists precisely
  because this is unverified.
- **[UNVERIFIED]** The confirmation code is stable across the legacy and v3
  APIs and is unique enough among an account's active reservations to serve as
  the identity cross-check.
- **[UNVERIFIED]** Reservation custom-field identifiers and values returned by
  the already-migrated custom-fields resource are unaffected by this change.

### About the search resource

- **[UNVERIFIED]** The curated search is `GET /reservations-v3/search`, takes
  bracketed filters (for example `filter[status][in]`, `filter[checkIn][lte]`,
  `filter[checkOut][gte]`, `filter[listingId][in]`), supports `sort`, `skip`
  and `limit` with a maximum limit of 100, and returns
  `{results, pagination:{skip, limit, hasMore}}` with no total count.
- **[UNVERIFIED]** Date filters compare listing-localized `YYYY-MM-DD` values,
  which means a single account-wide window needs a timezone pad to remain a
  superset of an instant-based window.
- **[UNVERIFIED]** An unsupported filter, operator or sort returns HTTP 400
  rather than being ignored.
- **[UNVERIFIED]** No field projection is supported and `status` is always
  present on a result.
- **[UNVERIFIED]** A page is never shorter than the requested limit while
  `hasMore` is true, and sorting by identifier is strictly ascending. FR-006
  treats a violation as a failure, so if this is wrong the integration fails
  loudly rather than silently truncating.
- **Confirmed by the vendor guide**: `lastUpdatedAt` filtering is not
  supported, and filtering by guest name, phone or email is not carried over.
  Neither is used by this integration today, so neither is a loss here.

### About the result shape

- **[UNVERIFIED]** Every result carries a non-empty, chronological `stay[]`,
  each segment carrying a listing identifier equal to `unitId ?? unitTypeId`,
  an optional `parentListingId` for multi-unit listings, UTC
  check-in/check-out, localized dates and a guest count.
- **[UNVERIFIED]** There is no top-level `listingId`, `unitId`, `unitTypeId`
  or `mtl` field, and no `lastUpdatedAt`.
- **[UNVERIFIED]** The result carries `guestId`, `confirmationCode`, `source`,
  `notes` and `customFields`, and `customFields` is absent rather than empty
  when no value is set.
- **[UNVERIFIED]** The status value set includes `confirmed`, `checked_in`,
  `checked_out`, `completed`, `canceled`, `inquiry`, `reserved` and
  `alteration`.
- **[UNVERIFIED]** `plannedArrival` and `plannedDeparture`, which the sensor
  exposes today, are available in v3 in some form. If they are not, their loss
  is a breaking change under NFR-004 and must be surfaced as such.
- **[UNVERIFIED]** `nightsCount` is not returned and must be derived from the
  localized dates.

### About the listing filter

- **[UNVERIFIED]** The listing filter matches only the **first** stay segment,
  which is why FR-015 forbids it on the primary retrieval path and defers any
  trade of coverage for request budget to OQ-008.
- **[UNVERIFIED]** A comma-joined list of listing identifiers is accepted, and
  a request carrying a realistic number of them is not rejected for URL
  length. This matters only if OQ-008 adopts a listing filter.

### About guests

- **[UNVERIFIED]** A guest record is retrievable by guest identifier from the
  `/guests` resource family and supplies a full name, phone and email
  equivalent to today's embedded guest object.
- **[UNVERIFIED]** A deleted or unknown guest identifier returns a not-found
  response rather than an error that would fail the refresh.

### About write resources

- **[UNVERIFIED]** `PUT /reservations-v3/{reservationId}/notes` accepts the
  note content this integration writes, and its request body shape.
- **[UNVERIFIED]** Whether that notes resource replaces the full note set, so
  that preserving existing note text still requires reading the current value
  first.
- **[UNVERIFIED]** `PUT /reservations-v3/{reservationId}/status` accepts
  `checked_in` and `checked_out` as this integration uses them today.

### About financials

- Not applicable. `GET /v1/guest-folio/overview` is not integrated (FR-043),
  so nothing about its batching limit, field mapping or error behaviour needs
  verifying. The former assumption about it is recorded in
  [OQ-001](#oq-001-financial-sensors--resolved-deprecate) as part of the
  rejected Option A.

### About the environment

- Features 002 (listings) and 007 (listing filtering) remain the source of
  truth for which listings exist and which are tracked. Reservations continue
  to attach to listing devices.
- Authentication, token refresh and rate-limit handling from Feature 001 are
  reused unchanged; v3 resources sit behind the same OAuth credentials.
- Polling remains the refresh mechanism. The `reservation.updated.v2` webhook
  the vendor guide recommends for change ordering is out of scope here.
- The existing default window (30 days past, 365 days future) and poll
  interval (15 minutes, minimum 5) remain the defaults.

## Risks and Mitigations

- **R-001 — Identifier re-keying between APIs silently rewrites every
  reservation.**
  *Impact*: catastrophic. Custom-field writes are keyed by reservation
  identifier and at least one user publishes door-lock codes through them. A
  wrong-id write gives a stranger a working door code.
  *Mitigation*: the identity guard (FR-031 to FR-035) aborts rather than acts;
  FR-034 keeps it free; FR-035 makes continuity observable on the first
  refresh; the rollout toggle (FR-036) provides escape.

- **R-002 — The whole migration is built on unverified documentation.**
  *Impact*: high. A wrong assumption about the response shape could mean no
  reservations are published at all.
  *Mitigation*: FR-041 and SC-011 force every assumption into a pre-release
  checklist; FR-006 and FR-008 make deviations fail loudly rather than
  silently; FR-042 diagnostics let a user's debug log confirm or refute each
  assumption; the toggle gives an immediate rollback.

- **R-003 — Added per-reservation lookups multiply request volume.**
  *Impact*: medium. Guest lookups could turn one request per refresh into
  dozens, risking rate limiting on a 15-minute poll.
  *Mitigation*: NFR-001 caps v3 volume at the legacy count; FR-018 mandates
  caching and per-refresh deduplication; FR-043 removes the folio lookup from
  the design entirely, so guest lookups are the only added traffic; SC-003 and
  SC-010 assert the budget in tests.

- **R-004 — Silent attribute loss breaks user automations.**
  *Impact*: medium-high. Attributes such as guest phone feed real automations
  and dashboards; losing them without notice looks like a Home Assistant bug
  to the user.
  *Mitigation*: NFR-004 requires every at-risk attribute to be preserved or
  explicitly declared a breaking change; SC-002 asserts attribute-key parity.
  The financial sensors are the one declared loss, and FR-043 to FR-049 make
  it loud: the sensors go `unavailable` rather than stale, a deprecation
  notice and release-note entry are required, and SC-014 asserts the
  behaviour.

- **R-005 — The first-segment-only listing filter hides reservations.**
  *Impact*: medium. A stay that begins in an untracked listing and moves into
  a tracked one is excluded by the server before client-side attribution can
  see it, so it cannot be recovered or even named in diagnostics.
  *Mitigation*: FR-015 forbids a server-side listing filter on the primary
  retrieval path, so the gap does not exist by default. Trading coverage for
  request budget is deferred to OQ-008 and requires an explicit recorded
  decision, not a silent optimisation.

- **R-006 — The legacy rollback path rots.**
  *Impact*: medium. A rollback target that is never exercised is not a
  rollback target.
  *Mitigation*: FR-040 requires behavioural identity with the previous
  release and keeps the existing legacy tests green against it.

- **R-007 — Users never move off the legacy path and are broken on
  2027-03-01.**
  *Impact*: medium.
  *Mitigation*: FR-037 defaults to v3, so the toggle is opt-in rollback, not
  opt-in migration; FR-039 warns at startup and gives a retirement-specific
  error message.

- **R-008 — Timezone handling at the window edges.**
  *Impact*: low-medium. Localized date filters against an instant-based window
  can clip a reservation at the boundary, and padding the filter to avoid that
  would publish results outside the configured window.
  *Mitigation*: FR-002a requires both halves — a padded server-side window and
  a **new** instant-based client-side trim. Neither exists today; the absence
  of a trim is part of why #204 produced no visible compensation.

- **R-009 — Mid-stay moves and group reservations create duplicate or
  confusing entities.**
  *Impact*: low-medium.
  *Mitigation*: FR-010 and FR-011 define per-listing attribution explicitly;
  no new entity type is created (see [Out of Scope](#out-of-scope)).

- **R-010 — The financial deprecation surprises a user mid-season.**
  *Impact*: medium. Three sensors that work today stop reporting after an
  upgrade, and a user who only reads the dashboard may not connect that to a
  release.
  *Mitigation*: FR-045 makes the sensors unavailable rather than silently
  stale, so the failure is visible rather than wrong; FR-046 surfaces a
  notice in Home Assistant and logs a warning; FR-047 requires release-note
  and documentation treatment; FR-044 keeps the legacy path working as a
  temporary rollback until FR-048's removal release.

## Out of Scope

- Webhook-based updates, including `reservation.updated.v2`. Polling remains
  the refresh mechanism.
- Reservation creation, quotes, group booking creation, owner reservations,
  approve/decline, pre-approve, relocate, mid-stay creation, cancellation
  requests, date changes, source changes, travel information, confirmation
  code changes, booking-date changes and guest-breakdown changes. This feature
  migrates only the two write operations the integration performs today
  (notes, status).
- Reservation comments, reservation logs, reservation reports and CSV/email
  report resources.
- Payments (`GET /payments`). It supports only one reservation per request,
  which is incompatible with this integration's request budget, and no current
  sensor needs it.
- Guest folio resources in their entirety — overview, invoice items and
  settings snapshots. The folio overview is no longer under consideration;
  the financial sensors are deprecated instead (FR-043, OQ-001).
- New entities for group reservations or for individual stay segments. Group
  reservations and mid-stay moves are **in scope for correct attribution**
  (FR-010, FR-011) but **out of scope as a new entity model**.
- Reservation custom fields, which are already migrated and unchanged
  (FR-030).
- Listings, calendar, custom variables and notify features, which use
  resources that are not retiring.
- Any change to the default window, poll interval or status filter defaults.
- Removal of the legacy path. That is a follow-up on or after 2027-03-01
  (FR-040, FR-048).

## Open Questions

### OQ-001: Financial sensors — RESOLVED: deprecate

**Status: DECIDED on 2026-11-13 by the repository owner — Option B
(deprecate). This reverses the recommendation previously recorded here, which
was Option A. No further decision is required and the plan stage may proceed.**

v3 does not embed `money.totalPaid`, `money.balanceDue` or `money.currency`.
Today those feed three diagnostic sensors (`sensor.py:150-184`,
`sensor.py:740-830`), which already go unavailable when the values are
missing.

**Decision**: the three sensors are **deprecated**.
`GET /v1/guest-folio/overview` is **not** integrated, no supplementary
financial lookup is implemented, and on the v3 path the sensors are not
created and therefore report `unavailable`. The legacy path keeps them
working as a temporary rollback until it is removed. The behaviour, the
user-facing notice, the release-note treatment and the removal timeline are
specified by FR-043 to FR-049, with User Story 7, SC-014 and R-010 covering
acceptance and risk.

**Alternative considered and not taken — Option A, retain via
`GET /v1/guest-folio/overview`.** Recorded so the reasoning is not lost:

- *For*: no breaking change; dashboards and automations reading the three
  sensors keep working; NFR-003 and NFR-004 satisfied with no caveat; the
  resource batches up to 200 reservations per request, so a typical
  installation adds roughly one extra request per refresh.
- *Against*: a new resource dependency whose field mapping to today's three
  values is **[UNVERIFIED]**; a new failure mode on every refresh; additional
  rate-limit exposure; a batching and partial-failure path to design and
  test; and, decisively, the folio's notion of "total paid" may not equal the
  legacy reservation's, which would change sensor *values* without changing
  names. A financial sensor that is confidently wrong is worse than one that
  is unavailable.

**Rationale for the decision**: the Option A safety valve — "fall back to
Option B if live verification shows the values are not equivalent" — could
never be exercised, because FR-041 records that no live Guesty access exists
for this project, so equivalence could not be established before release.
Shipping an unverifiable monetary value under an existing sensor name is a
worse breach of trust than withdrawing the sensor. Option B also removes a
whole resource, failure mode and request-budget argument from a migration
that is already entirely documentation-derived and deadline-bound.

Constitution principle VII is satisfied not by preserving the sensors but by
the alternative it allows: the change is explicitly versioned with a
migration path — declared here, announced in-product (FR-046) and in release
notes (FR-047), reversible through the rollout toggle (FR-044), and given a
removal date tied to the vendor retirement (FR-048).

### OQ-002: Does the v3 notes resource replace or append?

If `PUT /reservations-v3/{reservationId}/notes` replaces the full note set,
the current append behaviour still needs a read before the write, and the read
resource must be identified. If it merges, the read can be dropped. This
changes the request cost of the note action by one request. **[UNVERIFIED]**

### OQ-003: Are `plannedArrival` and `plannedDeparture` available in v3?

Both are exposed as sensor attributes today. If v3 does not carry them, their
loss is a breaking change that NFR-004 requires to be declared explicitly
rather than absorbed. **[UNVERIFIED]**

### OQ-004: Which `/guests` resource, and with what response shape?

The vendor guide says "the `/guests` endpoints" without naming one for a
single-guest read. The sibling bridge project chose a guests-CRUD read by
identifier with a field projection, also unverified. The exact resource,
whether it supports a projection, and its not-found behaviour all need
confirmation. **[UNVERIFIED]**

### OQ-005: How should the two-query intent be expressed in v3 filters?

FR-004 requires in-progress long stays to be found without an unbounded
account sweep. A check-out-based lower bound combined with a check-in-based
upper bound appears to express this in one query, but it is **[UNVERIFIED]**
that both filters can be combined, and **[UNVERIFIED]** whether they are
ANDed. If they cannot be combined, a second query filtered to `checked_in` is
the fallback. The plan stage must choose.

### OQ-006: Does a group reservation share one reservation identifier?

If a group booking is one reservation with segments across several listings,
FR-010 covers it. If it is several reservations under a group identifier, the
integration will see them as independent reservations, which is also
acceptable but should be confirmed so that the behaviour is documented rather
than discovered. **[UNVERIFIED]**

### OQ-007: Is there a ceiling on the search's filter list length?

FR-015 forbids a listing filter on the primary path, so this question applies
only if OQ-008 is answered in favour of adopting one. A large portfolio could
exceed a URL length limit, requiring chunking. **[UNVERIFIED]**

### OQ-008: May a server-side listing filter trade coverage for request budget?

FR-015 currently forbids one, because the filter is documented to match only
the first stay segment and would silently exclude a stay that begins in an
untracked listing and moves into a tracked one — unrecoverable and
undiagnosable. The cost of forbidding it is that retrieval is scoped by the
configured window and status set rather than by the tracked listings, so a
user tracking three listings of a large portfolio pages more than they
strictly need.

If the request budget proves unacceptable in practice, the owner may choose to
accept the coverage gap in exchange, in which case FR-015 must be amended to
record that choice and the gap must be stated in user-facing documentation.
Until then, completeness wins. Whether the resulting page count is acceptable
for a large account is itself **[UNVERIFIED]**.

### OQ-009: Which deprecation-notice mechanism?

FR-046 requires a user-visible deprecation notice for the three financial
sensors, but does not fix its delivery mechanism, because this repository has
no precedent to follow: the integration ships no `repairs` platform and no
issue translations today. The plan stage must choose between a Home Assistant
repairs issue (visible in Settings → Repairs, dismissible, but new platform
surface and new translated strings to add and later remove under FR-048) and
a logged warning alone (zero new surface, but easily missed by a user who
never reads the log). This question exists only because Option B was chosen
in OQ-001; it did not arise under Option A.

## Dependencies

- **Feature 001 (Auth & Config Flow)**: OAuth credentials, token refresh and
  the rate-limit-aware client are reused unchanged.
- **Feature 002 (Listings & Properties)**: listing devices remain the parent
  of reservation entities and the source of known listing identifiers.
- **Feature 003 (Reservations)**: this feature re-sources the data behind
  Feature 003's entities. Feature 003's functional requirements remain in
  force except where this specification supersedes them.
- **Feature 004 (Custom Variables)**: reservation custom fields already use v3
  and are unchanged.
- **Feature 005 (Notify Service)** and **Feature 006 (Automation Actions)**:
  consume reservation attributes and the reservation write actions; both are
  protected by NFR-003 and NFR-004.
- **Feature 007 (Listing Filtering)**: continues to decide which listings are
  tracked (FR-014).
- **Issue [#223][issue-223]**: the tracking issue for this migration. Issue
  [#204][issue-204] is resolved by FR-002 and FR-003.

[guesty-migration]: https://open-api-docs.guesty.com/docs/reservation-migration
[guesty-changelog]: https://open-api-docs.guesty.com/changelog/2026-11-05
[issue-204]: https://github.com/tykeal/homeassistant-guesty/issues/204
[issue-223]: https://github.com/tykeal/homeassistant-guesty/issues/223

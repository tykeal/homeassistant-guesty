<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Narrow Stay-Date Fallback

**Feature Branch**: `010-stay-date-fallback`
**Created**: 2026-11-19
**Status**: Draft
**Tracking Issue**: #268 (hypothetical; payload behavior not verified)
**Input**: User description: "For issue #268, allow a narrowly constrained
fallback for absent dates on a single raw stay segment, preserving explicit
dates and existing reservation attribution and safety behavior."

## Clarifications

### Session 2026-11-20

- Q: Should an explicit JSON `null` for either segment date qualify as
  absent, or must both keys be omitted? → A: Treat each endpoint as absent
  when its key is omitted or its value is JSON `null`; all four combinations
  qualify only when the other eligibility conditions hold.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Publish a single stay with absent segment dates (Priority: P1)

As a property manager, I want a single stay segment whose date endpoints are
absent (omitted or JSON `null`) to use the reservation's valid overall interval
under strict conditions, so that an otherwise attributable reservation is not
discarded solely because its one segment has no dates.

**Why this priority**: This is the sole intended user-visible exception to the
current missing-segment-date rejection rule. Restricting it to a uniquely
identified raw segment and a valid overall interval avoids inventing dates for
ambiguous or malformed stays.

**Independent Test**: Provide reservations with exactly one raw stay segment,
a usable listing identifier, each of the four omitted/JSON-`null`
representations for its two date endpoints, and a valid strictly chronological
top-level interval. Verify that each segment uses that interval and is
attributed using the existing listing rules.

**Acceptance Scenarios**:

1. **Given** exactly one raw segment with a valid listing identifier and both
   `checkIn` and `checkOut` keys omitted, and valid top-level dates with
   check-out later than check-in, **When** the reservation is processed,
   **Then** the segment uses the top-level interval and existing attribution
   behavior is applied.
2. **Given** exactly one raw segment with a valid listing identifier, omitted
   `checkIn`, and `checkOut` set to JSON `null`, and valid top-level dates with
   check-out later than check-in, **When** the reservation is processed,
   **Then** the segment uses the top-level interval and existing attribution
   behavior is applied.
3. **Given** exactly one raw segment with a valid listing identifier,
   `checkIn` set to JSON `null`, and omitted `checkOut`, and valid top-level
   dates with check-out later than check-in, **When** the reservation is
   processed, **Then** the segment uses the top-level interval and existing
   attribution behavior is applied.
4. **Given** exactly one raw segment with a valid listing identifier and both
   `checkIn` and `checkOut` set to JSON `null`, and valid top-level dates with
   check-out later than check-in, **When** the reservation is processed,
   **Then** the segment uses the top-level interval and existing attribution
   behavior is applied.
5. **Given** a single raw segment with explicit valid segment dates, **When**
   the reservation is processed, **Then** those segment dates are preserved
   and are not replaced by the top-level interval.
6. **Given** the segment is attributable to no known listing, **When** the
   fallback interval is available, **Then** the reservation remains subject
   to the existing missing-listing rejection behavior and is not published
   under a listing inferred from the top-level interval.

### User Story 2 - Reject ambiguous or invalid date fallback (Priority: P1)

As a property manager, I want incomplete, malformed, or multi-segment stays
to remain ineligible for date fallback, so that a reservation is never
published with dates inferred from an unrelated or ambiguous interval.

**Why this priority**: Incorrect reservation dates can mislead users and
automation. The exception must not weaken existing validation or attribution
rules.

**Independent Test**: Exercise partial actual-date, empty, malformed or
otherwise invalid present-value, invalid top-level, and
multiple-raw-segment cases and verify none is treated as eligible for fallback.

**Acceptance Scenarios**:

1. **Given** one segment endpoint contains an actual date and the other is
   absent (omitted or JSON `null`), **When** the reservation is processed,
   **Then** no fallback is applied.
2. **Given** either segment endpoint has an empty, malformed, or otherwise
   invalid present value, **When** the reservation is processed, **Then** no
   fallback is applied.
3. **Given** more than one raw stay segment exists, even if filtering leaves
   only one usable segment, **When** the reservation is processed, **Then**
   no fallback is applied.
4. **Given** the top-level interval is missing, malformed, or has check-out
   equal to or earlier than check-in, **When** the reservation is processed,
   **Then** no fallback is applied.
5. **Given** the raw segment lacks a valid listing identifier, **When** the
   reservation is processed, **Then** it is rejected under the existing
   listing validation behavior and no date fallback makes it publishable.
6. **Given** a reservation is unusable because its segments cannot supply an
   eligible interval, **When** it is skipped, **Then** a warning identifies
   the skip without exposing guest personal data or confirmation-code values.
7. **Given** existing reservation identity or write-safety conditions apply,
   **When** the date fallback feature is exercised, **Then** identity
   validation and write routing retain their existing outcomes.

### Edge Cases

- “Exactly one segment” is counted in the raw `stay` collection before any
  segment is filtered or discarded.
- A segment date endpoint is absent when its key is omitted or its value is
  explicit JSON `null`; all four combinations across `checkIn` and `checkOut`
  are eligible for fallback when all other requirements are satisfied.
- A partial actual-date pair means one endpoint has an actual date and the
  other is absent (omitted or JSON `null`); it is not eligible for fallback.
- Empty, malformed, or otherwise invalid present values are not omissions
  and are ineligible for fallback.
- A top-level interval must parse as dates and be strictly chronological.
- A segment with an unknown listing continues through existing attribution
  rules; top-level dates do not supply or override a listing identifier.
- Multi-segment reservations remain subject to the existing segment-aware
  attribution and status rules.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST permit a stay-date fallback only when the raw stay
  collection contains exactly one segment, counted before filtering.
- **FR-002**: For the eligible absent-date case, the sole raw segment MUST
  have a valid listing identifier, and each of its `checkIn` and `checkOut`
  endpoints MUST be absent, meaning the key is omitted or its value is JSON
  `null`. All four combinations of these representations are eligible when
  the other requirements are met.
- **FR-003**: System MUST use the top-level reservation interval for the
  eligible segment only when both top-level dates are valid and check-out is
  strictly later than check-in.
- **FR-004**: System MUST preserve explicit valid segment dates. If one
  endpoint contains an actual date while the other is absent (omitted or JSON
  `null`), or either endpoint has an empty, malformed, or otherwise invalid
  present value, the system MUST NOT use the top-level interval as a fallback.
- **FR-005**: System MUST NOT apply fallback when there is more than one raw
  stay segment, including when filtering would leave only one usable segment.
- **FR-006**: System MUST NOT relax existing listing validation, known-listing
  attribution, partial-attribution, reservation identity, or reservation
  write-safety requirements. The fallback MUST NOT infer a listing from
  top-level reservation data.
- **FR-007**: System MUST retain visible diagnostics for unusable reservations
  and MUST NOT expose guest personal data or confirmation-code values in
  diagnostics for this fallback.
- **FR-008**: Existing requirements for reservation status, segment-aware
  projection, listing filtering, identity checks, and write routing MUST
  remain unchanged except for the narrowly defined missing-date exception.

### Compatibility and Risk Analysis

This specification supersedes the contradictory missing-date rule in
`specs/008-reservations-v3/data-model.md` §1.1 only for the eligible case
defined in FR-001 through FR-003. Existing segment parsing and attribution
remain in force for ineligible cases, including a missing/invalid listing
identifier, a partial actual-date pair, an empty, malformed, or otherwise
invalid present date value, multiple raw segments, or an invalid top-level
interval. In particular, valid segments of multi-segment reservations are not
rejected merely because fallback is ineligible. All other requirements in
Feature 008 remain intact, including FR-009/010/011/011a/013. This does not
amend Feature 008 files.

There is a separate projection risk that this feature does not resolve:
current per-listing projection behavior uses the reservation-level interval
and status when only one usable segment remains. A multi-segment raw stay can
be filtered down to one surviving segment; in that case, the reservation-level
interval/status can be used for the projection, potentially attributing
misleading dates and affecting automations. FR-005 prevents that case from
qualifying for this fallback, but does not change the existing projection
behavior or expand this feature's code scope. That risk requires separate
owner/product consideration.

The source implementation and Feature 008 documents describe current
behavior, not evidence that Guesty has sent a particular issue-related
payload. Issue #268 is treated as hypothetical here; no live payload behavior
or API contract claim is verified by this specification.

### Key Entities

- **Reservation**: The booking record with its overall interval, status,
  identity, and raw stay collection.
- **Stay segment**: One listing-associated interval within a reservation,
  with its own listing identifier and optional check-in/check-out values.
- **Top-level reservation interval**: The reservation-level check-in and
  check-out values considered only as the constrained fallback source.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In the defined eligible single-raw-segment case, 100% of
  verification examples, including all four omitted/JSON-`null` combinations,
  with valid, strictly chronological top-level dates use those dates for the
  segment.
- **SC-002**: Across verification examples for partial actual-date, empty,
  malformed or otherwise invalid present-value, invalid-interval,
  invalid-listing, and multi-raw-segment cases, 0 ineligible examples receive
  the fallback.
- **SC-003**: 100% of examples with valid explicit segment dates retain those
  dates unchanged.
- **SC-004**: Existing identity checks, write routing, and known-listing
  attribution produce the same outcomes as before the exception in all
  unaffected verification examples.
- **SC-005**: Unusable reservations in verification examples produce a
  warning without guest personal data or confirmation-code values.

## Assumptions

- The top-level reservation dates use the existing date interpretation rules;
  this feature adds no new date format or localization policy.
- “Valid listing identifier” means the segment satisfies the integration's
  existing listing-identifier validation; this feature does not broaden or
  redefine that validation.
- This specification describes desired behavior for a hypothetical issue.
  No live Guesty payload was inspected or verified.

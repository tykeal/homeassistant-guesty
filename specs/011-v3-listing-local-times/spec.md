<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: V3 Listing-Local Reservation Times

**Feature Branch**: `011-v3-listing-local-times`
**Created**: 2026-11-20
**Status**: Approved — planning complete; implementation not started
**Tracking Issue**: [#282](https://github.com/tykeal/homeassistant-guesty/issues/282)
**Input**: Correct V3 reservation times that appear to encode
listing-local wall-clock values with a UTC suffix.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Show the listing's scheduled local stay times (Priority: P1)

As a property manager, I want a V3 reservation to show its check-in and
check-out at the scheduled local times for the property, so that Home
Assistant status, reservation attributes, and automations do not act several
hours early or late.

**Why this priority**: A false UTC interpretation can shift an arrival or
departure by the property's UTC offset and can affect time-sensitive
automation.

**Independent Test**: Provide a V3 stay for a listing whose localized dates,
timezone, and configured check-in/check-out defaults are known. Compare the
resulting displayed instants and local dates with those property settings for
both standard-time and daylight-time dates.

**Acceptance Scenarios**:

1. **Given** a V3 stay with localized dates, listing timezone
   `America/New_York`, and listing defaults of 16:00 check-in and 11:00
   check-out, **When** its times are derived, **Then** they represent 16:00
   and 11:00 on the respective property-local dates, regardless of the raw
   V3 `checkIn`/`checkOut` UTC suffix.
2. **Given** a V3 stay spanning a daylight-saving transition, **When** its
   local times are derived, **Then** each endpoint uses the offset applicable
   to its own date and retains the supplied localized calendar date.
3. **Given** the same reservation data through the legacy path, **When** it
   is processed, **Then** its existing time interpretation and published
   outcome remain unchanged.

### User Story 2 - Keep Moved Stays on the Right Listings (Priority: P1)

As a property manager tracking a reservation that moves between rooms or
listings, I want each stay interval to remain associated with its own
property's dates and settings, so that correcting its time does not move or
duplicate a reservation into the wrong listing.

**Why this priority**: Feature 008 already publishes multi-segment stays to
the listings they touch. Applying one property's time or timezone to another
can create misleading per-listing status and attributes.

**Independent Test**: Supply one reservation with consecutive segments for
different tracked listings, including one same-local-date handoff. Verify
that each segment remains attributed only under the existing attribution
rules and that no listing's timezone or default is silently substituted for
another's.

**Acceptance Scenarios**:

1. **Given** segments on two tracked listings with different timezones and
   different configured defaults, **When** their intervals are derived,
   **Then** each segment uses its own localized dates and associated listing
   settings, and existing per-listing projection and attribution are
   preserved.
2. **Given** a same-date room move, **When** the stay is processed, **Then**
   the destination begins at the prior room's checkout only if the segments
   are consecutive, share the same localized handoff date and explicitly
   known timezone, both rooms have valid required settings, and the resulting
   destination interval is valid. Otherwise, it uses its own verified
   schedule or has unknown timing. The adjustment sets the destination's
   start to the exact prior checkout, leaves the reservation's final
   checkout unchanged, and adds no buffer or extension. The reservation
   remains visible only under the existing origin and destination listing
   projections.
3. **Given** the prior room's metadata is missing or untracked, **When** a
   later segment is processed, **Then** no parent or prior-room setting is
   substituted for the exact room's timing metadata, no prior-room time is
   invented, and no segment is attributed to a listing that the existing
   rules do not select.

### User Story 3 - Avoid presenting uncertain times as certain (Priority: P1)

As a property manager, I want missing, invalid, or ambiguous time inputs to
remain visibly uncertain rather than be converted into a plausible but
incorrect instant, so that reservation timing and safety-sensitive
automations are not based on a guess.

**Why this priority**: An incorrect concrete instant can be more harmful than
an explicit unknown. The integration currently uses concrete instants for
window filtering, reservation selection, status, and candidate hints.

**Independent Test**: Exercise missing listing timezone, missing or invalid
listing defaults, invalid localized dates, and DST-ambiguous or nonexistent
local times. Verify that none is silently treated as a verified UTC or local
instant, while known dates remain visible. Verify instant-dependent window,
ordering, status, candidate-hint, identity, and routing behavior without
reusing stale intervals.

**Acceptance Scenarios**:

1. **Given** a listing's timezone is missing, **When** its data is parsed,
   **Then** the current model's default value of `UTC` is not by itself
   treated as evidence that Guesty supplied an explicit, trustworthy
   timezone.
2. **Given** a required default time or localized date is absent or invalid,
   **When** an exact endpoint cannot be derived but its localized date is
   known, **Then** the date remains visible, the endpoint's exact-time value
   is `null`, the reservation's timing status is `unknown`, and no previous
   refresh's instant is shown in its place.
3. **Given** a localized time falls in a DST gap or has two possible
   instants, **When** the interval is processed, **Then** no silent fold or
   gap policy is applied; the endpoint is marked unknown and is not used as
   an instant.
4. **Given** time data becomes unknown after a prior successful refresh,
   **When** the next refresh is processed, **Then** the current record's
   known dates remain visible, each unknown exact-instant attribute is
   `null`, `timing_status` is `unknown`, and no prior interval is presented as
   current.
5. **Given** an unknown-time reservation has known local dates and an
   explicit listing timezone, **When** it is checked against the configured
   time window, **Then** the client uses the inclusive local-date bounds
   instead of an unknown instant. If the timezone is not explicit, the
   already-bounded V3 result remains visible without an instant-based client
   trim.
6. **Given** a listing has both fully timed and unknown-time reservations,
   **When** the reservation list is ordered, **Then** fully timed records
   sort by exact check-in instant, followed by unknown-time records sorted by
   localized check-in date and reservation ID. An unknown multi-segment
   interval has `unknown` temporal status, while the source lifecycle status
   remains separately visible.
7. **Given** a previously timed reservation now has unknown timing,
   **When** candidate hints and identity evidence are refreshed, **Then**
   the stale interval is neither presented nor used for fallback eligibility
   or time-based expiry; the record does not trigger a fallback lookup, and
   its existing identity evidence is not expired or evicted solely due to
   unknown timing.
8. **Given** a reservation has unknown timing but its identity remains
   verified, **When** an existing write is requested, **Then** the current
   identity guard and source-routing rules produce the same permitted or
   refused outcome as before, and the unknown interval does not change the
   target reservation identifier.
9. **Given** an unknown-time reservation conflicts with existing
   same-path identity evidence, **When** the refresh compares identities,
   **Then** the existing conflict outcome still aborts the refresh; a
   cross-path identifier disagreement continues to block writes as before.
10. **Given** Feature 010's sole raw segment has absent or `null` segment
    endpoints and a valid top-level interval, **When** its exact room timing
    is unavailable, **Then** the existing narrow fallback eligibility and
    date-only fallback remains intact, preferring valid segment and then
    reservation localized dates, then the written date of the corresponding
    valid top-level timestamp; its exact-time status is unknown, and no raw
    suffix or prior interval supplies an exact instant.
11. **Given** a V3 reservation has multiple raw stay entries and one middle
    entry is malformed, partial, or cannot be assigned a verified interval,
    **When** its current candidate hint is recorded, **Then** the hint has no
    usable timed interval even if the first and last entries or the parsed
    survivors are valid; every otherwise usable segment keeps its existing
    projection and attribution, and identity and write-guard outcomes are
    unchanged.

## Edge Cases

- The V3 response can include both reservation-level and per-segment
  timestamps and localized dates. Segment-local dates and settings must not
  be replaced with reservation-level values when a stay has multiple
  segments.
- Feature 010 permits a narrow fallback when exactly one raw stay segment
  has both segment endpoints absent or `null` and the reservation-level
  interval is valid. Feature 011 changes the **time interpretation** of that
  eligible V3 fallback: its timestamp suffix does not establish a UTC wall
  time. Its eligibility, raw-segment count, listing attribution, and
  identity/write-safety rules remain unchanged. The eligible segment obtains
  its known calendar dates from the localized dates already present on that
  segment or its reservation. If neither localized date is present, the
  calendar date written in the corresponding valid top-level timestamp is
  the date-only fallback. Extract that date without offset conversion,
  `ZoneInfo`, or deriving an exact time. The top-level pair must still be a
  valid strictly chronological interval for Feature 010 eligibility; an
  invalid or reversed pair supplies no fallback date. Malformed-present
  segment endpoints and all other ineligible shapes remain ineligible.
- A segment's listing may be a tracked sub-unit, an untracked sub-unit with
  a tracked parent, or an unknown listing. Existing Feature 008 attribution
  rules remain authoritative; time correction must not broaden them.
- Two segments can meet on the same localized calendar date, but have
  different listing timezones, defaults, or missing prior-room metadata.
  Only a clear consecutive same-date handoff with valid settings in the same
  explicitly known timezone and a valid resulting interval uses the prior
  room's checkout as the moved-to start. Every other case uses the
  destination's verified schedule or unknown timing. The handoff changes
  only the destination's start to the exact prior checkout; it does not
  change the reservation's final checkout, add a buffer, or extend the
  reservation beyond its existing localized dates.
- Guesty listing records currently provide timezone and default
  check-in/check-out fields to the Listings Coordinator. The model currently
  substitutes `UTC` when timezone is absent. Existing listing results are
  filtered to selected listings, and the requested listing fields do not
  include child-listing timing metadata. Only timing metadata for the exact
  room, available through the existing listing data, is eligible. A parent
  listing MUST NOT substitute for an untracked child's settings, and no new
  request may be added to obtain missing room data.
- `plannedArrival` and `plannedDeparture` are accepted by the current
  reservation model and exposed as separate sensor attributes. Their V3
  presence and semantics remain unverified in Feature 008 OQ-003. Keep these
  attributes separate and do not use either as a timing override while
  Guesty semantics remain unverified.
- Invalid/missing defaults, invalid IANA timezone data, invalid localized
  dates, DST gaps/folds, stale previous values, and unknown current values
  must not be collapsed into one apparent valid instant.
- Time-derived candidate hints must not change reservation identity,
  confirmation-code matching, write routing, or the listing selected for a
  segment. An unknown-time record is not eligible for a time-windowed
  fallback lookup; its prior known hint is not surfaced as current timing,
  used for time-based candidate expiry, or used to evict identity evidence.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: For Reservations V3, the system MUST NOT treat the raw
  `checkIn` or `checkOut` UTC suffix as sufficient evidence that the value is
  a UTC instant for user-facing stay timing. The issue's observed offset and
  its evidentiary limits are recorded under **Evidence and Scope**.
- **FR-002**: When the required inputs are available and valid, the system
  MUST derive each V3 segment's scheduled endpoints from that segment's
  localized calendar dates, the corresponding listing's configured
  check-in/check-out defaults, and that listing's explicit timezone, except
  for a destination start that meets the handoff rule in FR-003.
- **FR-003**: The derived endpoint MUST represent the configured wall-clock
  time on its own localized date, with timezone rules for that date. For a
  qualifying consecutive same-local-date handoff, the destination start MUST
  equal the prior room's exact checkout instant, and MUST be used only when
  the rooms share the same explicitly known timezone and the resulting
  destination interval is valid. Otherwise, use the destination's own
  verified schedule or unknown timing. A segment MUST NOT inherit another
  segment's wall-clock time or timezone outside this rule.
- **FR-004**: The system MUST use listing data already available through the
  existing listing refresh and MUST NOT add Guesty network requests for time
  derivation. It MUST use timing metadata for the exact room only; parent
  listing settings MUST NOT substitute for missing child settings. If
  required setting provenance or exact-room coverage is insufficient, the
  system MUST treat the time as unknown rather than silently use unrelated
  values or the model's implicit `UTC` default.
- **FR-005**: The system MUST preserve existing behavior for the legacy
  reservations path.
- **FR-006**: The system MUST preserve Feature 008 reservation identity,
  confirmation-code cross-checks, candidate identity, write routing, and
  existing listing attribution. Corrected times MUST NOT become reservation
  identifiers or change write destinations.
- **FR-007**: The system MUST preserve Feature 008's per-listing segment
  projection and status rules, except that an unknown exact interval MUST
  NOT be used to calculate a time-dependent per-segment status. In that case,
  the status MUST be reported as unknown while the source lifecycle status
  remains separately visible. This feature MUST NOT introduce a new
  multi-stay entity model, merge distinct listing intervals, or publish a
  segment under a listing not selected by existing attribution rules.
- **FR-008**: The system MUST keep every available localized stay date
  visible when exact timing is unknown, mark the affected endpoint(s) as
  unknown, and MUST NOT present an earlier refresh's instant as current. For
  an unknown endpoint, the existing `check_in` or `check_out` instant
  attribute MUST be `null`; its `check_in_local` or `check_out_local` date
  attribute MUST retain the current known date. Every reservation MUST
  expose `timing_status` as `known` only when both exact endpoints are
  verified, and `unknown` otherwise. Upcoming reservation summaries MUST
  expose the same exact-instant, localized-date, and timing-status values.
- **FR-009**: For an unknown-time reservation with known local dates and an
  explicit listing timezone, the system MUST apply the configured
  past/future window using inclusive local calendar-date bounds: checkout date
  on or after the local current date minus `past_days`, and check-in date on
  or before the local current date plus `future_days`. If the timezone is not
  explicit, the client MUST NOT apply an instant-based trim; it MUST retain
  the record returned by the already-bounded V3 search.
- **FR-010**: An unknown instant MUST NOT be used for ordering by instant,
  segment temporal-status calculation, or any other decision requiring an
  exact instant. Fully timed intervals MUST be ordered by exact check-in
  instant. Incomplete intervals MUST follow fully timed intervals and be
  ordered by known localized check-in date, then reservation ID. A segment
  whose temporal status cannot be calculated MUST report Home Assistant's
  `unknown` state and expose the source lifecycle status separately as
  `reservation_status` both on the selected reservation and in upcoming
  summaries. Existing status priority based on known source lifecycle status
  remains unchanged; a time-dependent per-listing classification MUST NOT
  treat an unknown interval as current, upcoming, or past.
- **FR-011**: A current unknown-time record MUST NOT be eligible for a
  time-windowed legacy fallback lookup. Its stale last-known interval MUST
  NOT be surfaced or used to decide fallback eligibility, window membership,
  ordering, or temporal status. Unknown or stale timing MUST NOT be the
  temporal basis for candidate expiry, bounded candidate-hint capacity, or
  confirmed absence. An observation-recency capacity eviction may remove
  candidate metadata under FR-011b, but it MUST NOT expire or evict identity
  evidence or change identity/write-guard outcomes. Preserve the reservation
  identifier and existing identity protections, but do not use its stale
  hint for a fallback lookup. Once a current verified interval is available,
  the existing candidate-window and expiry rules apply to that verified
  interval.
- **FR-011a**: A timed fallback candidate MUST have a verified interval for
  every entry in the complete raw V3 `stay` array. The implementation MUST
  preserve raw count and order before filtering; it MUST NOT treat parsed
  survivors, only the first and last entries, or a partial projection as the
  complete stay. Every raw entry MUST be interpretable as a segment with a
  valid listing identity and both current exact endpoints verified by the
  Feature 011 timing rules. Each segment MUST have `check_in < check_out`,
  and raw-order intervals MUST be chronological and non-overlapping
  (`previous.check_out <= next.check_in`), after any qualifying handoff
  adjustment. Only then may the overall candidate interval be bounded by
  the first raw segment's verified check-in and the last raw segment's
  verified check-out. Any unusable raw entry, missing or partial interval,
  invalid endpoint, or chronology violation makes the current candidate
  unknown and supplies no timed interval; it does not remove the reservation
  from projection or alter attribution, identity evidence, confirmation-code
  checks, or write routing. The raw V3 timestamps themselves do not establish
  a segment interval; malformed timestamp fields do not invalidate an
  otherwise fully derived interval except where Feature 010's separate
  malformed-present eligibility rule applies.
- **FR-011b**: Candidate storage MUST use deterministic observation recency
  separate from stay timing and identity evidence. A successfully persisted
  refresh observation of a reservation from the primary source or a
  successful by-ID retrieval updates that candidate's recency, whether its
  current interval is known or unknown. A one-time successful bootstrap
  retrieval also records its observation time. Store recency as a UTC
  timestamp, one observation time per successful refresh/bootstrap.
  Retained hints, projections,
  cache-only fallback servings, lookup attempts/failures, and aborted or
  unpersisted refreshes do not create a newer observation. Old two-endpoint
  hints migrate as timing-unknown with no usable interval; they use the
  containing baseline's `updated` value as their migration recency when
  valid, otherwise the fixed UTC sentinel
  `0001-01-01T00:00:00+00:00`, never either stale endpoint.
  Candidate hints are bounded to the existing `MAX_BASELINE_CODES` capacity
  by sorting ascending on `(observed_at, reservation_id)` and retaining the
  newest entries. Thus a tie evicts the lexicographically smallest
  reservation ID first. This candidate-only bound/ordering MUST NOT
  change identity-map retention, expiry, continuity, confirmed-absence, or
  write-guard rules. A confirmed absence removes a candidate only under the
  existing source-confirmation and successful-persistence/acknowledgment
  rules; unknown timing, failed lookup, or missing V3 data is not absence.
- **FR-012**: The system MUST preserve existing reservation identity,
  confirmation-code cross-checks, candidate identity, and write routing. An
  unknown interval MUST NOT change an identifier, mark a reservation absent,
  weaken identity checks, or change a write destination. A write remains
  permitted or refused solely under the existing identity and source-routing
  rules.
- **FR-013**: The system MUST keep `plannedArrival` and `plannedDeparture`
  exposed separately as today and MUST NOT use them as timing overrides
  while their V3 semantics remain unverified.
- **FR-014**: For the eligible one-raw-segment fallback in Feature 010, the
  system MUST retain its existing eligibility and identity/write-safety
  requirements. Feature 011 MUST replace only the V3 endpoint's time
  interpretation; it MUST NOT use that fallback to bypass the requirements
  in Feature 010. For each endpoint, use a valid segment-localized date first
  and a valid reservation-localized date second. If neither is present, use
  the calendar date written in that endpoint's corresponding valid
  top-level ISO timestamp, but only when the complete top-level pair is a
  valid strictly chronological interval and the raw one-segment eligibility
  conditions hold. Read the timestamp's written date component as-is: do not
  convert its offset, construct/use `ZoneInfo`, or infer an exact schedule
  from its time or suffix. An invalid endpoint provides no written-date
  fallback; an invalid or reversed top-level pair makes this fallback
  ineligible. This rule does not alter legacy parsing or publication.
- **FR-015**: The system MUST NOT invent a missing local date or wall-clock
  time, silently choose a DST fold, normalize a nonexistent local time, or
  treat an implicit timezone default as explicit Guesty data.
- **FR-016**: The system MUST NOT emit guest personal data or confirmation
  codes in diagnostics for time derivation. It MUST NOT perform live Guesty
  writes, scrape live account data, or contact external parties as part of
  this feature.
- **FR-017**: The behavior MUST remain compatible with the repository's
  minimum supported Home Assistant and Guesty API requirements and MUST NOT
  add network requests.

### Evidence and Scope

Issue [#282](https://github.com/tykeal/homeassistant-guesty/issues/282)
links to RentalSync Bridge issue
[#212](https://github.com/tykeal/homeassistant-rentalsync-bridge/issues/212).
That report records a live bridge export in `America/New_York` showing
11:00/06:00 local times for listings configured in Guesty at 16:00/11:00.
The report's owner response attributes the five-hour shift to V3 wall-clock
values being treated as UTC. The issue thread does **not** attach a raw
Guesty response, and it does not establish that the Guesty Home Assistant
integration itself was tested against the live account. The observed bridge
output and configuration are strong evidence of the interpretation problem,
not independent proof of every V3 payload shape.

Bridge PR
[#215](https://github.com/tykeal/homeassistant-rentalsync-bridge/pull/215)
is a comparison point: it uses localized stay dates with listing defaults
and timezone rather than relying on the raw suffix. It reports using the
listing data already fetched by that bridge; that fact does not prove that
this integration has complete room-level timing metadata in its current
filtered listing set. Bridge PR
[#216](https://github.com/tykeal/homeassistant-rentalsync-bridge/pull/216)
is a separate comparison point for same-date room moves: it opens the
moved-to interval at the prior room's checkout when the boundary qualifies.
Neither bridge PR is a specification to copy wholesale.

This feature supersedes the earlier Feature 008 assumption that V3
`checkIn`/`checkOut` are authoritative UTC instants for stay timing. It does
not change the V3 reservation search, record identity, API selection,
legacy behavior, Feature 010 eligibility rules, or the existing multi-stay
projection model. Feature 010's use of the reservation-level interval for
its narrowly eligible single raw segment remains in force; this feature
changes how that V3 fallback's time is interpreted. For that fallback,
segment-localized dates take precedence, followed by reservation-localized
dates. If neither localized date is available, the date written in each
corresponding endpoint of Feature 010's already-valid top-level interval is
the date-only fallback; do not convert the offset or treat its time component
or suffix as a verified local schedule or UTC instant.

The owner has confirmed three operator policies for this specification:
same-timezone, consecutive, same-local-date handoffs may start the moved-to
segment at the exact prior checkout only when the derived interval remains
valid; unknown exact timing remains date-visible but is not replaced with
stale timing or used for instant-dependent decisions; and only exact-room
settings already available in listing data may be used, with no parent
substitution or added request. Reservation-level `plannedArrival` and
`plannedDeparture` remain separate attributes and are not timing overrides
while their V3 semantics are unverified.

### Key Entities *(include if data involved)*

- **Reservation**: A booking with a reservation identity, lifecycle status,
  overall interval, optional published timing attributes, and one or more
  listing-associated stay segments.
- **Stay segment**: One reservation interval associated with a listing or
  sub-unit, carrying localized start/end dates and its own guest count.
- **Listing timing settings**: The listing's configured arrival and
  departure wall-clock times and explicit timezone, where available.
- **Time certainty**: Whether a derived interval is known for the current
  refresh or unknown. A prior verified interval may remain internal identity
  evidence but is not current user-facing timing or a current candidate hint.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In all verification cases with valid localized dates, explicit
  timezone, valid listing defaults, and uniquely resolvable endpoints not
  governed by the handoff exception in FR-003, 100% of V3 endpoints represent
  the configured property-local date and wall-clock time across standard-time
  and daylight-time dates.
- **SC-002**: Zero verified V3 cases derive a user-facing time by interpreting
  the raw `checkIn`/`checkOut` suffix as authoritative UTC wall-clock time.
- **SC-003**: In 100% of multi-segment verification cases, existing listing
  attribution and reservation identity are unchanged; no segment is assigned
  to a listing solely because another segment supplied its timing metadata.
- **SC-004**: In 100% of unknown-time cases with known dates, localized dates
  remain visible, unknown exact-instant attributes are `null`, the timing
  status is `unknown`, and no prior interval is presented as current.
- **SC-005**: Across window, ordering, status, and fallback-candidate cases,
  zero decisions requiring an exact instant use an unknown or stale interval;
  timed fallback candidates include every verified raw segment and use only
  the first-to-last verified interval; inclusive date-window behavior and
  candidate-hint expiry match FR-009 and FR-011.
- **SC-006**: In 100% of qualifying same-timezone handoffs, the destination
  begins at the exact prior checkout and its final checkout is unchanged; in
  all non-qualifying cases, the destination uses only its own verified
  schedule or is marked unknown.
- **SC-007**: Zero additional Guesty requests are made for time derivation,
  and missing child timing metadata never causes parent substitution.
- **SC-008**: In all legacy-path and Feature 010 eligibility regression
  cases, the existing path selection, identity, write-routing, and narrow
  fallback rules remain unchanged.
- **SC-009**: Time-related diagnostics disclose no guest personal data or
  confirmation-code values.

## Assumptions

- `checkInDateLocalized` and `checkOutDateLocalized` are the intended V3
  calendar-date inputs when present; the feature adds no claim that a
  localized date is available in every response. The narrow Feature 010
  fallback uses the calendar date written in each corresponding timestamp
  when localized dates are absent, only if its valid chronological top-level
  interval and all raw single-segment eligibility conditions hold.
- A listing's configured default check-in/check-out times are the baseline
  schedule for the exact room. Reservation-level planned-time attributes
  remain informational until Guesty semantics are verified.
- Missing timezone in the current listing model is not equivalent to a
  Guesty-supplied UTC timezone.
- Existing reservation and per-listing sensor keys remain stable. This
  feature adds only the `timing_status` and, when needed for an unknown
  temporal state, `reservation_status` attributes; it does not add
  lock-scheduling semantics to this integration.
- Exact-room settings absent from existing listing data make the affected
  timing unknown; neither parent inheritance nor extra Guesty requests are
  authorized.

## Owner-Confirmed Policies

### Same-date room handoffs

For consecutive stay segments whose localized checkout/check-in dates are
the same, the destination's start MUST use the prior room's exact checkout
only when all of the following hold:

- The segments are consecutive in the reservation's stay sequence and meet
  on the same localized calendar date.
- Both rooms have their required timing settings in the existing listing
  data, and each has an explicit, valid timezone.
- Both segments use the same explicitly known timezone.
- Using the prior checkout as the destination start produces a valid
  destination interval (start strictly before its verified checkout).

If any condition fails, use the destination room's own verified schedule. If
that schedule cannot be verified, the destination timing is unknown. The
handoff uses the exact prior checkout, adds no buffer, does not alter the
reservation's final checkout, and does not extend the overall stay beyond its
existing localized dates. The destination remains attributed only under the
existing Feature 008 rules.

### Unknown exact timing

Keep all available current localized dates visible. For each endpoint whose
exact instant cannot be verified, publish no instant (`null`) and expose
`timing_status: unknown`; never present the previous refresh's interval as
current. Do not use an unknown or stale interval in a decision requiring an
exact instant, including instant-based window trimming, ordering,
segment-temporal status, legacy fallback lookup eligibility, or candidate
expiry. When a date-only window comparison is possible, use inclusive local
calendar-date bounds. If the exact listing timezone is unavailable, do not
perform a client-side instant trim on the already-bounded V3 result.

For time-dependent per-segment status that cannot be computed, expose
Home Assistant's `unknown` state and retain the source lifecycle status
separately. Order fully timed intervals by exact check-in instant; place
incomplete intervals afterward, ordered by known localized check-in date and
then reservation ID. Unknown timing must not itself expire or evict
identity evidence, mark a reservation absent, trigger a time-windowed
fallback lookup, or change identity/write-routing outcomes; existing
bounded-capacity and confirmed-absence safeguards remain unchanged and do
not use unknown or stale timing as their temporal basis.

### Complete timed fallback candidates and candidate recency

A timed fallback candidate represents the interval from the first raw
segment's verified check-in through the last raw segment's verified
check-out, but only after every entry in the complete raw sequence has a
valid listing identity, a verified positive interval, and chronological,
non-overlapping order. A malformed, filtered, partial, missing, or
chronology-invalid entry makes the candidate unknown; it does not remove a
segment from its existing projection or change attribution. Unknown timing
blocks timed fallback only; identity and write-guard evidence remain
independent and unchanged.

Candidate recency records actual persisted source observations, not stay
dates: update it for records returned in a successfully persisted primary
refresh, a successful by-ID response, or a successful bootstrap retrieval.
Store one UTC observation timestamp for each successful refresh/bootstrap.
Do not refresh it for retained or cache-only results, attempts/failures, or
aborted/unpersisted refreshes. Migrate old two-endpoint hints to unknown with
no usable interval; use the baseline `updated` timestamp when valid, else
`0001-01-01T00:00:00+00:00`. Bound candidate hints at
`MAX_BASELINE_CODES` by sorting `(observed_at, reservation_id)` ascending
and retaining the newest entries; equal-recency ties evict the
lexicographically smallest reservation ID first. This candidate-only order
does not alter identity-map
retention or expiry. Confirmed absence still requires the existing source
verdict and persisted acknowledgment.

### Exact-room data and planned-time attributes

Use timing settings for the exact room only, and only when present in the
existing listing data. Do not substitute parent-listing settings for an
untracked or incomplete child's metadata and do not add a Guesty request to
obtain it. If exact-room timing cannot be verified, follow the unknown-time
policy.

Continue exposing `plannedArrival` and `plannedDeparture` separately as
today. Do not use either as a timing override while their V3 semantics remain
unverified.

## Compatibility and Scope Boundaries

- **Feature 008 supersession**: Replace its V3 raw-UTC timing assumption
  only. Keep search behavior, identity guard, write routing, filtering,
  attribution, and per-listing projection unchanged except for the
  owner-confirmed same-date handoff rule.
- **Feature 010 interaction**: Retain the narrow fallback's exact one-raw-
  segment eligibility, null/omitted semantics, chronological top-level
  interval requirement, written-calendar-date fallback, and safety rules.
  Do not broaden it to multiple segments or infer a listing from
  reservation-level data.
- **Room moves**: Do not expand multi-stay semantics into a new entity,
  merge intervals, or change which listing owns a segment. Apply only the
  owner-confirmed same-timezone consecutive handoff rule.
- **Listing coverage**: Existing listing refresh requests timezone and
  default-time fields. The listing model currently maps absent timezone to
  `UTC`, and the coordinator filters results to selected listings. The exact
  room/child metadata coverage is not established by this inspection; only
  exact-room settings in existing listing data are valid, and missing values
  produce unknown timing without parent substitution or added requests.
- **No collateral changes**: No changes to legacy interpretation,
  reservation identifiers, confirmation-code matching, writes, minimum
  supported Home Assistant version, or unrelated features.

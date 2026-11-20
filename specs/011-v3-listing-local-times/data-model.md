<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: V3 Listing-Local Reservation Times

The model remains in-memory except for the existing per-entry reservation
identity baseline and its candidate hints. No entity, device, unique ID, or
write-target identity changes are planned.

## 1. Listing timing metadata

The source is one `GuestyListing` from the already-fetched listings response,
matched by the exact `GuestyStaySegment.listing_id`.

- **Listing identity** (`GuestyListing.id`): must exactly match the segment's
  listing ID.
- **Timezone**: API `timezone`; the current model uses `str` and substitutes
  `"UTC"` when absent. Keep that compatibility value and add
  `timezone_explicit: bool` (or equivalent provenance) from a non-empty
  source string. Require valid `ZoneInfo` data; the model default is not
  evidence.
- **Arrival wall time**: `defaultCheckInTime` → `check_in_time`. Require a
  valid local wall-clock value. Missing, malformed, or offset-bearing input
  makes this endpoint unknown.
- **Departure wall time**: `defaultCheckOutTime` → `check_out_time`. Apply
  the same validation independently.

Parent listing metadata is not related to this entity for timing. Existing
parent listing IDs remain usable only for Feature 008's existing attribution
rule. An exact child record returned by the listing refresh may be used for
time derivation even when the child is not selected for publication; an
absent child record cannot be inferred or fetched separately.

Load/cache `ZoneInfo` data off the Home Assistant event loop; pass prepared
timezone objects to the pure conversion helper. A cache miss or invalid zone
must not block refresh work.

## 2. Raw V3 stay segment

`GuestyStaySegment` represents one raw `stay[]` position when it has enough
listing identity for existing attribution. Preserve raw payload count and
position before filtering so no downstream operation mistakes surviving
records for the original sequence.

- **`listing_id: str`**: existing required listing identifier; never sourced
  from top-level reservation data.
- **`parent_listing_id: str | None`**: existing attribution input only, not a
  timing-settings source.
- **Raw segment count / position**: integer provenance captured before
  filtering; position remains stable within the raw array.
- **`check_in_local`, `check_out_local`**: independently optional localized
  date strings. Preserve supplied strings for attributes; parse as calendar
  dates only when valid for derivation/window checks. Never fill one endpoint
  from the other.
- **`check_in`, `check_out: datetime | None`**: derived exact instants.
  Remain `None` unless current inputs prove one unique instant. Raw V3
  endpoint values are not a derivation source.
- **`guests_count: int | None`**: existing per-segment party count.

The model may retain a small eligibility marker for the narrow Feature 010
case, but that marker must be computed from the raw count and raw missing/null
state before malformed or incomplete segments are filtered. It must not be
reconstructed from the final `stay` tuple.

## 3. Reservation and current timing

`GuestyReservation` continues to carry the source lifecycle status,
reservation ID, confirmation code, guest/custom-field data, planned-time
attributes, localized dates, and V3 stay tuple. Exact endpoints become
optional so a V3 record can remain publishable when timing is uncertain.
Legacy parsing continues to reject missing or malformed required endpoints
and therefore supplies concrete instants as today.

- **`check_in` / `check_out`**: current verified endpoints only; may be
  `None` on V3. Never populated from a previous refresh.
- **`check_in_local` / `check_out_local`**: current localized calendar dates
  when available, even if exact timing is unknown.
- **`timing_status`**: `known` only if both current endpoints are verified;
  otherwise `unknown`.
- **`reservation_status`**: source lifecycle status retained independently
  when a projected temporal status becomes `unknown`.
- **Projected `status`**: existing mapped lifecycle or temporal
  classification. A time-dependent per-listing classification is `unknown`
  when its interval is incomplete.
- **`planned_arrival` / `planned_departure`**: existing independent
  attributes. Never used to derive or repair exact endpoints.
- **`stay`**: keeps raw order/provenance and per-listing date/timing data
  needed for projection and handoff. No new multi-stay entity or merged
  interval.

Each derived endpoint depends only on that endpoint's localized date, the
exact room's matching default wall time, and explicit valid zone data. A
unique wall-time-to-instant conversion is required. A DST fold (more than one
candidate), DST gap (no candidate), malformed date/time/zone, or missing
metadata leaves that endpoint unknown. The interval temporal status is
unknown if either exact endpoint is unknown.

### Same-date handoff

A destination segment may use the prior segment's exact checkout as its
check-in only when:

1. Both segments occupy adjacent positions in the original raw stay array.
2. The prior localized checkout date equals the destination localized
   check-in date.
3. Both exact rooms have valid required timing settings and explicitly
   supplied valid timezone IDs.
4. The explicit timezone IDs are the same and the prior checkout is exact.
5. The destination's resulting start is strictly before its independently
   verified checkout.

Otherwise the destination uses only its own verified schedule or unknown
timing. The final checkout remains the destination's own endpoint. No buffer,
duration inference, or extension is added.

## 4. Per-listing projection and publication

Attribution still uses exact tracked segment ID, or existing parent
attribution when the exact segment listing itself is not tracked. Timing
metadata lookup is separate from attribution and always uses the segment's
exact listing ID. Per-listing projection selects only among segments already
attributed to that listing; it does not merge disjoint intervals.

- **Exact sensor `check_in` / `check_out`**: ISO instants when known;
  `null` for each unknown endpoint.
- **Local date attributes**: preserve each current localized date; otherwise
  `null`.
- **`timing_status`**: `known` when both endpoints exist; otherwise
  `unknown`.
- **Segment temporal status**: use the existing confirmed/checked-in/
  checked-out derivation when possible. Otherwise report `unknown` and expose
  source lifecycle status separately as `reservation_status`.
- **Window**: retain existing exact-instant behavior for known intervals.
  For unknown timing, require known local dates and explicit exact-room zone.
  Include when checkout date is on/after local today minus `past_days` and
  check-in date is on/before local today plus `future_days` (inclusive). If a
  required date or zone is unproven, do not invent an instant or trim by it.
- **List ordering**: exact check-in instant for fully timed records; then
  localized check-in date and reservation ID for unknown records. Never
  compare unknown records as instants.
- **Upcoming summary**: keep existing inclusion and provide the same endpoint
  nullability, dates, `timing_status`, and separate source status.

## 5. Persisted identity and fallback candidate evidence

`IdentityBaseline` remains the existing per-entry persisted identity record.
Candidate timing is separate from identity evidence. A candidate hint carries
an observation-recency value, current timing certainty, and an optional
verified interval. Only the optional interval is supplied to the existing
time-windowed fallback; observation recency is not timing evidence and does
not feed identity-map retention, expiry, continuity, absence, or write
decisions.

The candidate model must distinguish:

- reservation identity/evidence that remains valid under the existing
  identity guard;
- current timing certainty for a reservation returned by V3; and
- an optional verified interval usable for the existing time-windowed
  fallback only while current timing is known; and
- the timestamp of the most recent qualifying source observation, used only
  for deterministic candidate-hint capacity management.

An unknown current V3 timing record persists as unknown and has no usable
interval; it never inherits the prior interval. A candidate is known only
when the complete nonempty raw `stay` array is preserved and every raw entry
has a valid listing identity and a verified current interval. Each interval
must have `check_in < check_out`; in raw order, every next check-in must be
at or after the previous check-out, after any qualifying handoff adjustment.
The overall candidate interval is the first raw segment's check-in through
the last raw segment's check-out. Gaps between non-overlapping segments are
inside this envelope. Any non-object entry, missing listing identity,
unverified endpoint, partial interval, or chronology violation makes the
whole candidate unknown; do not build an envelope from first/last endpoints
or parsable survivors. Raw `checkIn`/`checkOut` values do not verify timing
under Feature 011; a malformed raw timestamp is not independently
disqualifying if the entire interval is otherwise derived from valid
localized dates and room settings, except for Feature 010's
malformed-present eligibility rule. This candidate-only rule does not filter
segments from publication or alter attribution.

Qualifying recency is advanced by a reservation returned in a successfully
persisted primary-source refresh or by a successful by-ID response, even
when that observation has unknown timing. A successful one-time bootstrap
retrieval also records its observation time. Do not advance it for a retained
hint, projection, cache-only fallback serving, lookup attempt/failure, or
aborted/unpersisted refresh. Use the existing refresh observation timestamp
for all reservations observed in that refresh.

Old `{check_in, check_out}` hints deserialize as timing-unknown and expose no
usable interval: their two stay endpoints cannot establish full-sequence
completeness or current timing. Use the containing baseline's valid `updated`
timestamp as the deterministic migration recency; if it is absent or
invalid, use the fixed UTC sentinel
`0001-01-01T00:00:00+00:00`. Never use either endpoint to migrate recency,
timing certainty, fallback eligibility, or candidate capacity.
Bound candidate hints independently at `MAX_BASELINE_CODES` by sorting
ascending on `(observed_at, reservation_id)` and retaining the newest
entries. Equal-recency ties therefore evict the lexicographically smallest
reservation ID first. This capacity rule applies only to candidate
metadata and does not evict, expire, or reorder identity maps. Preserve the
existing identity conflict and write-guard outcomes.

Confirmed absence remains governed only by existing source-response and
successful-persistence/acknowledgment rules. It removes the candidate hint
only after the existing source has confirmed absence and the baseline
records that result; unknown timing, a failed/retired lookup, and omission
from V3 are not confirmed absence.

## 6. Feature 010 single-raw-segment date fallback

Eligibility remains unchanged: exactly one raw segment before filtering,
valid segment listing identity, each raw segment endpoint omitted or JSON
`null`, and a valid strictly chronological top-level interval. Localized
dates prefer valid segment values, then valid reservation values, separately
for check-in and check-out. Where either date is still unavailable, use only
the calendar date written in that endpoint's corresponding valid top-level
timestamp, and only while the whole top-level interval remains valid and
strictly chronological. Read the written calendar date without offset
conversion or `ZoneInfo`; the timestamp's time and suffix never become an
exact schedule. An invalid endpoint provides no fallback date, and an
invalid/reversed top-level pair or any malformed-present segment endpoint
makes the Feature 010 fallback ineligible. This date-only fallback cannot
make a timed candidate verified. Legacy parsing and publication are
unchanged.

## 7. Persistence compatibility and migration

Do not discard or reinterpret the existing identity baseline. Candidate
deserialization accepts old `{check_in, check_out}` entries but migrates
their timing to unknown and their recency from the containing baseline's
`updated` timestamp (or the fixed oldest sentinel); stale endpoints are not
eligible for timed fallback. New serialization carries explicit timing
certainty, an optional interval, and observation recency. A reload test must
demonstrate old-format migration, deterministic candidate ordering, and
persistence of a current unknown marker. No change to reservation IDs,
confirmation-code cross-checks, legacy write routing, identity-map rules, or
the current storage key is planned.

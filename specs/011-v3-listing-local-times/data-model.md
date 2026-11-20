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
`CandidateHint` currently stores `check_in` and `check_out` ISO strings and
feeds both fallback-window eligibility and temporal identity-evidence
retention/capacity decisions.

The candidate model must distinguish:

- reservation identity/evidence that remains valid under the existing
  identity guard;
- current timing certainty for a reservation returned by V3; and
- an optional verified interval usable for the existing time-windowed
  fallback only while current timing is known.

An unknown current V3 timing record must persist as unknown, not inherit a
previous interval as current. A reservation candidate is known only when
the current reservation's full raw stay sequence needed for its overall
interval is preserved and its required exact endpoints are verified; a
filtered or malformed segment cannot make a partial sequence look complete.
An unknown candidate must not initiate fallback or use stale endpoints for
fallback eligibility, expiry, or capacity ordering. It must not itself
expire or displace identity evidence. Existing serialized hints with the old
two-string shape remain readable; a current V3 unknown result updates that
record to unknown. Existing identity conflict and write-guard outcomes
remain unchanged.

For bounded candidate storage, use non-stay observation/recency metadata
rather than a stay endpoint as the capacity ordering key. New records carry
that non-stay observation value; records from the old two-string shape use a
deterministic migration tie-breaker, never their stale stay endpoint.
Confirmed absence remains governed only by existing source response and
persistence rules, not by unknown or stale timing.

## 6. Feature 010 single-raw-segment date fallback

Eligibility remains unchanged: exactly one raw segment before filtering,
valid segment listing identity, each raw segment endpoint omitted or JSON
`null`, and a valid strictly chronological top-level interval. Localized
dates prefer segment values, then reservation values. Where neither is
available, Feature 010's current top-level fallback remains date-only under
its existing date interpretation. Its timestamp time component or suffix
does not become an exact schedule. All malformed-present or multi-raw-entry
cases remain ineligible.

## 7. Persistence compatibility and migration

Do not discard or reinterpret the existing identity baseline. Candidate
deserialization accepts old `{check_in, check_out}` entries and keeps those
entries eligible under existing rules unless the current V3 refresh reports
that reservation with unknown timing. New serialization carries an explicit
timing certainty and does not expose a stale interval as current. A reload
test must demonstrate both old-format compatibility and persistence of a
current unknown marker. No change to reservation IDs, confirmation-code
cross-checks, legacy write routing, or current storage key is planned.

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: V3 Listing-Local Reservation Times

## Decisions

### 1. Treat V3 raw endpoint suffixes as untrusted for stay-time conversion

**Decision**: For V3 stay timing, derive instants from localized stay dates
plus the exact room's default check-in/check-out wall time and explicitly
provided timezone. Do not treat top-level or segment `checkIn`/`checkOut`
suffixes as proof of UTC wall time.

**Rationale**: Issue #282 reports a five-hour local-time shift from a bridge
observation. The approved Feature 011 spec records the evidence and its
limitations and supersedes Feature 008's assumption that the V3 endpoints
are authoritative UTC instants. The current model parses those strings as
aware instants, but this is the behavior being corrected.

**Alternatives considered**: Continue parsing suffixes as UTC; reject the
entire reservation when raw endpoints disagree; use the raw V3 instant only
when listing settings are missing. Each would either preserve the known
failure or represent unverified data as certain.

### 2. Require timezone provenance and exact-room settings

**Decision**: Resolve only with the settings attached to the exact segment
listing ID. A listing model's implicit `"UTC"` fallback is not evidence that
Guesty supplied an explicit timezone. Keep selected listing IDs authoritative
for attribution, but make all records from the existing listings response
available to the timing lookup.

**Rationale**: `GuestyListing.from_api_dict` currently maps an absent
timezone to `"UTC"`; the listings request already asks for timezone and
default arrival/departure times; and `ListingsCoordinator` filters the
returned listing dictionary after fetching it. The specification allows
already-fetched exact-room metadata, forbids parent substitution and
additional requests, and explicitly distinguishes an implicit default from
source evidence.

**Alternatives considered**: Treat the current `"UTC"` model default as
explicit; use parent settings for an untracked child; issue a child-listing
request; infer a timezone from reservation endpoints. All are excluded by
FR-004 and the owner-confirmed policy.

### 3. Treat gaps, folds, invalid dates and missing settings as unknown

**Decision**: Use standard-library `zoneinfo` and `datetime` for local
wall-time conversion. Because constructing a `ZoneInfo` can read timezone
files on a cache miss, load/cache zones outside the Home Assistant event loop
(for example, through its executor). Pass prepared zone objects into the
pure converter. Validate candidates by round-tripping through UTC; accept
only one distinct instant. Zero valid instants (a gap) and two distinct
instants (a fold) produce unknown timing. Validate each endpoint
independently.

**Rationale**: The project targets Python ≥3.14.2. Round-tripping
distinguishes normalized nonexistent local times from valid ones; checking
both folds distinguishes ambiguous times without selecting an undocumented
policy. Loading timezone files off-loop preserves Home Assistant's
non-blocking requirement; the conversion itself is deterministic and
in-memory.

**Alternatives considered**: Silently choose `fold=0` or `fold=1`; normalize
nonexistent times; fall back to UTC; reject the whole reservation instead
of preserving a known date and an independently known endpoint; construct
uncached zones synchronously on the event loop. These conflict with the
approved uncertainty or performance requirements.

### 4. Preserve raw multi-segment provenance before parsing/filtering

**Decision**: Capture raw `stay` count and order before any segment can be
dropped. Retain sufficient positional metadata with usable segment records
to tell whether two parsed records were truly adjacent in the raw payload.
Keep Feature 010 fallback eligibility tied to exactly one raw segment with
both segment endpoint values absent or JSON `null`, valid listing identity,
and a valid top-level interval.

**Rationale**: `GuestyReservation.from_v3_api_dict` currently checks the
Feature 010 single-raw-segment rule before filtering, then `GuestyStaySegment`
drops segments with missing or unparsable endpoint values. A later
coordinator step that sees only the surviving tuple could otherwise mistake
a filtered multi-stay response for a single segment or a consecutive
handoff. Feature 010 explicitly prohibits this count drift.

**Alternatives considered**: Recount parsed segments; infer adjacency from
the surviving tuple; make all malformed entries disappear without
provenance. These alternatives permit invalid fallback or handoff eligibility.

### 5. Keep existing API request and source boundaries

**Decision**: Resolve V3 timing after parsing against the listings DTOs
already returned. Do not change the V3 query, add a listings request, or
change legacy reservation parsing. Keep eligible Feature 010 fallback
date-only when its localized date precedence cannot provide an exact
schedule.

**Rationale**: `LISTINGS_FIELDS` already includes the three relevant
settings; `get_listings()` fetches all pages before the coordinator applies
the selected-listing filter. The V3 search is bounded and source-specific;
the legacy parser and Feature 008 fallback-sourced records remain separate.
Feature 010 controls raw-segment count, listing identity, date eligibility
and write safety; Feature 011 changes only eligible V3 timing interpretation.

**Alternatives considered**: Request missing room settings on demand; change
server filters; allow V3 timing derivation to alter legacy results; expand
Feature 010 eligibility. All violate stated scope or add network behavior.

### 6. Keep current timing and persisted identity evidence separate

**Decision**: Persist an explicit current timing certainty for candidate
hints. A current unknown V3 record must not be submitted as a legacy
time-window candidate, and old known timestamps must not be reused for
window membership, expiry, or capacity ordering. Preserve existing
identity/code evidence, and deserialize the existing two-endpoint candidate
shape compatibly. Apply bounded-capacity ordering using non-stay observation
recency rather than an unknown or stale stay interval.

**Rationale**: `CandidateHint` currently stores two ISO endpoint strings;
`IdentityGuard` uses their checkout values in evidence retention and
capacity ordering; the coordinator reconstructs legacy fallback candidates
from them. FR-011 requires unknown current timing to suppress lookup and
forbids stale intervals as current timing or the temporal basis of expiry,
absence, or capacity behavior. The old serialized records must remain
readable across Home Assistant reload/upgrade.

**Alternatives considered**: Keep the old interval as an implicitly current
candidate; evict evidence when its interval becomes unknown; use the stale
checkout as a sort key; clear the whole identity baseline; change the
reservation identifier. These would either enable unsafe fallback or
weaken existing identity protections.

### 7. Preserve current sensor keys and add explicit uncertainty

**Decision**: Keep existing check-in/out and localized-date attributes,
publish `null` for an unverified exact endpoint, add `timing_status`, and
add `reservation_status` when temporal classification is unknown. Upcoming
summaries carry matching time/date/certainty/lifecycle values. Keep planned
arrival/departure independent.

**Rationale**: The current sensor serializes endpoints using `.isoformat()`
and exposes planned times as separate fields. The approved spec permits
nullable exact instants and requires current dates to remain visible. Additive
certainty fields avoid inventing a time while preserving entity identity and
source lifecycle context.

**Alternatives considered**: Keep a previous interval in the sensor; remove
existing keys; use planned arrival/departure as overrides; collapse
`timing_status` into source lifecycle status. Each conflicts with FR-008,
FR-010 or FR-013.

## Factual uncertainty (not a policy decision)

- #282 references a live RentalSync Bridge observation, but contains no raw
  payload from this integration; it does not prove every V3 response shape.
- Feature 008's V3 assumptions have not been independently live-verified.
  The semantics of V3 `plannedArrival` and `plannedDeparture` remain
  unverified (Feature 008 OQ-003).
- The repository has no live Guesty account to establish whether every
  tracked child room is present in every listings response. If the exact
  room is missing from already-fetched listing data, the accepted outcome is
  unknown timing, not an added query or parent substitution.
- Listing default time strings are currently modeled as raw strings and
  tested from fixtures; implementation must accept only a valid local wall
  time without inventing a format conversion. A malformed or offset-bearing
  value remains unknown.

No unresolved policy clarification remains, and no live account or external
party contact is required for this plan.

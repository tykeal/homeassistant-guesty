<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: V3 Listing-Local Reservation Times

**Branch**: `011-v3-listing-local-times-plan` | **Date**: 2026-11-20 |
**Spec**: [spec.md](./spec.md)
**Input**: `/specs/011-v3-listing-local-times/spec.md`
**Tracking issue**: [#282](https://github.com/tykeal/homeassistant-guesty/issues/282)

## Summary

For V3 reservations only, derive each stay endpoint from that segment's
localized calendar date, its exact room's configured default wall time, and
an explicitly supplied valid IANA timezone. Do not treat raw `checkIn` or
`checkOut` suffixes as verified UTC timing. Keep localized dates when an
endpoint cannot be verified; publish `null` exact instants and unknown
temporal state rather than a stale or guessed time. Preserve legacy parsing,
Feature 008 identity/attribution/write protections, Feature 010's narrow
single-raw-segment eligibility, current reservation selection behavior for
known lifecycle statuses, and the existing V3 request set. A timed fallback
candidate must be verified across every segment in the full raw stay
sequence; its bounding interval is the first verified check-in through the
last verified check-out after full-sequence chronology checks. Candidate
recency is stored separately from timing and identity evidence. Feature 010's
date-only fallback uses the date written in each corresponding valid
top-level timestamp, never offset-converted or used as an exact time.

The bounded implementation surface is the V3 model/timing parse, the
existing listing refresh's already-fetched exact-room metadata, V3 coordinator
filter/projection/order, sensor publication, and candidate-hint persistence
and fallback gating. This does not add lock-scheduling behavior, alter API
selection or search filters, broaden listing attribution, or change
`planned_arrival` / `planned_departure`.

## Technical Context

**Language/Version**: Python ≥3.14.2 (mypy 3.14, ruff `py314`).
**Primary Dependencies**: Home Assistant ≥2026.4.0, `httpx` ≥0.28, stdlib `datetime`/`zoneinfo`.
**Storage**: In-memory models; per-entry Home Assistant `Store` identity baseline.
**Testing**: pytest with pytest-asyncio, respx and Home Assistant test
fixtures; ruff, mypy, interrogate, repository pre-commit hooks, and CI.
**Target Platform**: Home Assistant custom integration (HACS).
**Project Type**: Home Assistant custom integration.
**Performance Goals**: Use data already fetched; add zero Guesty requests.
Load uncached timezone data outside the HA event loop; convert in memory.
**Constraints**: V3-only reinterpretation; exact-room settings only;
preserve raw segment count/order before filtering; no parent-setting
substitution; keep Feature 008 identity, attribution and write routing;
keep Feature 010 raw-count eligibility; unknown timing cannot use prior
instants for publication, trimming, sort, temporal status or fallback; do not
emit guest PII or confirmation codes in timing diagnostics.
**Scale/Scope**: Existing accounts with tens to hundreds of listings and
reservations; unchanged API pagination and polling volume.

## Constitution Check

*Gate: pass before research; re-evaluate after design.*

- **I. Quality and testing — Pass.** Later implementation uses TDD,
  complete docstrings, typing, and the required test gates.
- **II. API client design — Pass.** The V3 timing helper stays in `api/`
  without HA imports; no HTTP behavior changes.
- **III. Atomic commits — Pass.** This plan is a single documentation change;
  implementation remains separate.
- **IV. Licensing — Pass.** New Markdown files carry SPDX headers and retain
  REUSE coverage.
- **V. Pre-commit integrity — Pass.** Run configured hooks without bypass
  before push.
- **VI. Co-authorship and DCO — Pass.** Require `-s` and the Copilot trailer.
- **VII. UX consistency — Pass.** No entity IDs or key renames; nullable
  values and additive certainty fields follow the spec.
- **VIII. Performance — Pass.** Timezone data is loaded off-loop; conversion
  is in memory and no external I/O is added.
- **IX. Phased development — Pass.** Test-first implementation checkpoints
  are defined for the later stage.
- **X. Security and privacy — Pass.** No live calls/writes or guest PII in
  timing diagnostics.

**Gate result**: Pass; no constitutional deviation or complexity exception.

## Project Structure

### Planning artifacts

```text
specs/011-v3-listing-local-times/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    └── reservation-sensor.md
```

`tasks.md` is a later Speckit tasks-stage output and is intentionally not
created here.

### Source and tests for the subsequent implementation stage

```text
custom_components/guesty/
├── api/
│   ├── models.py                  # V3 raw dates, optional derived endpoints,
│   │                              # exact-timezone provenance, raw stay shape
│   ├── timing.py                  # pure date/time + zone resolution seam
│   ├── reservations_v3.py         # retain current search/API behavior
│   └── reservations_fallback.py   # refuse unknown-time window candidates
├── coordinator.py                 # retain unfiltered listing metadata; resolve,
│                                  # window, project, status, ordering, hints
├── identity.py                    # compatible candidate serialization and
│                                  # non-time-based handling of unknown evidence
└── sensor.py                      # null-safe attributes and unknown status

tests/
├── api/test_models.py
├── api/test_reservations_v3.py
├── api/test_reservations_fallback.py
├── test_coordinator.py
├── test_identity.py
├── test_reservation_sensor.py
└── test_reservations_parity.py
```

**Structure decision**: Keep parsing and the pure resolver in `api/` without
Home Assistant imports. Use the existing coordinator as the boundary where
the raw V3 reservation can be joined to exact-room settings already present
in the listings response. Keep selected listing IDs authoritative for
publication/attribution; retaining unselected listing DTOs is metadata
access only. Preserve the existing `ReservationsCoordinator.data` mapping
shape and sensor entity identity.

## Design Boundaries

1. **Keep source and provenance distinct.** The legacy parser continues to
   produce its current concrete intervals. V3 parsing retains localized
   dates and enough raw stay count/order metadata to prevent malformed,
   missing-field, untracked, or otherwise filtered entries from making a
   multi-segment payload appear eligible as a single segment, consecutive
   handoff, or complete timed fallback candidate. Feature 010 eligibility is
   checked against the raw collection before filtering. The top-level V3
   interval may supply only the written calendar date for an otherwise
   eligible Feature 010 endpoint when no localized date is available; it
   never supplies an exact schedule.
2. **Resolve against the exact room's fetched settings.** The listings
   endpoint already requests `timezone`, `defaultCheckInTime`, and
   `defaultCheckOutTime`; `ListingsCoordinator` currently discards
   unselected records after fetching them. Preserve a separate lookup over
   those same returned records while leaving selected `data` and
   `known_ids` unchanged. The listing DTO's `timezone="UTC"` default is not
   provenance: retain an explicit-supplied signal (or equivalent) and
   require an explicit, valid timezone plus valid endpoint default. Never
   substitute a parent listing for a missing exact-room record or setting.
3. **Use a pure, deterministic wall-time resolver.** Parse the supplied
   localized date and configured local wall time independently for each
   endpoint. Load/cache `zoneinfo.ZoneInfo` objects off the event loop
   (executor on cache miss); pass prepared objects to the pure conversion
   helper. Round-trip fold candidates through UTC and accept only one valid
   instant. Zero candidates means a gap, two distinct candidates a fold;
   both yield unknown. Invalid dates, wall times, zones, or provenance also
   yield unknown. Do not normalize, select a fold, or use raw V3 suffixes.
4. **Keep handoff narrow.** Apply a moved-to start adjustment only to
   adjacent raw segment positions whose localized boundary dates are the
   same, whose exact-room metadata and explicit valid timezone are present,
   whose zones are identical, and whose prior checkout and destination
   checkout are verified with destination start strictly before its
   checkout. Set only that destination start to the prior checkout. If any
   predicate fails, use the destination's own verified schedule or unknown;
   never extend the final reservation checkout or infer an intermediate
   segment across a filtered raw entry.
5. **Make uncertainty end-to-end.** Model exact endpoints as optional for
   the V3 path while retaining strict non-null legacy parsing. Compute
   `timing_status` from both current exact endpoints; never retain an old
   instant as the current value. Keep source lifecycle status separate from
   segment temporal classification. Null-safe serialization, status,
   window, and sort code must not compare or format an unknown endpoint as
   an instant.
6. **Preserve window and ordering semantics.** Keep the current V3 server
   query and its bounds unchanged. Exact known intervals keep the existing
   instant-window behavior. For unknown-time records with known dates and
   explicit exact-room zones, use the inclusive date predicate: localized
   checkout date ≥ local today − `past_days`, and localized check-in date ≤
   local today + `future_days`. In a multi-segment record, use each segment's
   own zone and retain the reservation if a segment overlaps its local date
   window. If required date/zone evidence is unavailable, retain the
   already-bounded V3 result without inventing an instant. Sort known records
   by exact
   check-in first, then unknown records by localized check-in date and
   reservation ID, with absent dates ordered deterministically after known
   dates.
7. **Separate current timing, candidate recency, and identity evidence.**
   Evolve durable `CandidateHint` data with current certainty, an optional
   verified interval, and observation recency. A timed V3 candidate requires
   every entry in the complete raw stay array to have a valid listing ID and
   a fully verified interval. Require each interval to be strictly positive
   and raw-order intervals to be chronological and non-overlapping; only
   then bound the candidate from the first check-in through the last
   check-out. Any unusable, partial, filtered, or chronology-invalid entry
   makes the candidate unknown without changing segment publication or
   attribution. Store one UTC observation time per successful
   refresh/bootstrap. Advance recency only for records actually returned by a
   successfully persisted primary-source refresh or successful by-ID
   retrieval, and for successful bootstrap retrievals; do not advance it for
   retained, projected, or cache-only results or failed/aborted attempts.
   Migrate old two-endpoint hints to unknown/no interval and use the enclosing
   baseline update timestamp (or fixed UTC sentinel
   `0001-01-01T00:00:00+00:00`) for deterministic recency. Bound only
   candidate metadata at `MAX_BASELINE_CODES` by sorting
   `(observed_at, reservation_id)` ascending and retaining the newest;
   equal timestamps evict the lexicographically smallest reservation ID
   first.
   This metadata policy must not change identity-map retention, expiry,
   confirmed absence, continuity, or write guards; unknown timing is not
   absence. Fallback-sourced legacy records retain existing source routing
   and write protections.
8. **Keep sensor contract additive.** Maintain `check_in`, `check_out`,
   `check_in_local`, `check_out_local`, `planned_arrival`, and
   `planned_departure` keys. Publish null for unknown exact endpoints,
   retain available localized dates, add `timing_status`, and expose
   `reservation_status` whenever temporal segment status is unknown.
   Upcoming summaries carry the same timing certainty and source lifecycle
   status. Planned-arrival/departure fields remain separate and never feed
   endpoint derivation.

## Focused Test Seams

- **Model and date-time resolver** (`tests/api/test_models.py`): standard
  and summer `America/New_York` offsets; a reservation spanning a DST
  offset change; missing/implicit `UTC`, malformed IANA zones, invalid
  localized dates and defaults; an unambiguous wall time; both sides of a
  DST fold and a gap; independently known/unknown endpoints; raw V3 suffix
  independence; and no substitution from `plannedArrival` /
  `plannedDeparture`.
- **Room metadata and handoff** (`tests/test_coordinator.py`): settings for
  two rooms with different zones/defaults; an unselected exact room present
  in the already-fetched listing result; unknown or absent exact room;
  tracked child versus untracked child/parent metadata; a valid same-date
  adjacent handoff; different-zone, missing-setting, different-date,
  nonconsecutive/raw-gap, and invalid-result non-handoffs. Assert attribution
  and segment order do not change.
- **Feature 010 provenance** (`tests/api/test_models.py` and coordinator
  tests): all omitted/null endpoint combinations for exactly one raw
  segment, malformed-present endpoints, multiple raw segments with only one
  parseable survivor, invalid top-level fallback, and per-endpoint
  segment-then-reservation localized-date precedence. When localized dates
  are absent, assert each eligible endpoint uses only the calendar date
  written in its corresponding valid top-level timestamp; invalid endpoints
  provide no extracted date, and an invalid/reversed top-level pair is
  ineligible. Assert raw suffixes cannot produce an exact instant and this
  date-only fallback cannot qualify a timed candidate.
- **Window/publication** (`tests/test_coordinator.py`,
  `tests/test_reservation_sensor.py`): inclusive lower/upper local-date
  bounds, explicit versus implicit zone, no trim on an unprovable zone,
  exact-time filtering unchanged, no stale interval after a successful
  prior refresh, known-first and unknown-date/reservation-ID ordering,
  unknown multi-segment temporal status, preserved lifecycle priority, and
  selected/upcoming sensor null/date/status attributes.
- **Identity, persistence and source isolation**
  (`tests/test_identity.py`, `tests/api/test_reservations_fallback.py`,
  `tests/test_reservations_parity.py`): old stored two-endpoint hints migrate
  to unknown/no interval and use baseline-update recency or the fixed oldest
  sentinel; current unknown shape survives reload; incomplete middle raw
  entries cannot yield a timed candidate from valid first/last survivors;
  verified complete candidates use the first-to-last envelope; qualifying
  observations, equal-recency ties, and capacity eviction are deterministic
  and affect only candidate metadata; confirmed absence still follows
  existing persisted acknowledgment. Unknown current records do not call
  the legacy fallback or alter identity/write evidence; known-time candidates
  retain existing windows; same-path conflict still aborts and cross-path
  disagreement still blocks writes; writes target the same ID; legacy
  parsing/publication is unchanged; fallback eligibility, source routing and
  write safety remain narrow.
- **Request and privacy regression**: instrument existing fake clients and
  assert zero additional listing/reservation calls for derivation. Diagnostics
  for invalid timing contain no guest name/contact data or confirmation
  code. All tests are fixture-based; no live Guesty query or write.

## Phase Boundaries and Validation

- **Planning (this stage)**: record research, model, contract and quickstart;
  update Copilot context; stop before tasks and production changes.
- **Implementation (later, not authorized by this plan stage)**: use TDD in
  the model/resolver, coordinator/sensor, and identity/fallback boundaries.
  Keep the V3 and legacy behavior checkpoints separate; run focused tests
  after each boundary, then the complete suite and repository quality gates.
- **Completion gate for later implementation**: full pytest suite, ruff,
  mypy, interrogate, REUSE and all pre-commit/CI checks pass; review confirms
  no extra API calls, no stale/guessed timing, unchanged identity/write
  target, and no Feature 008/010 scope expansion.

The plan stage itself adds no production code, tasks file, release note, live
account test, or release/tag action. The existing v1.0.0 hold remains in
force.

## Post-Design Constitution Check

Pass. Design keeps external calls and HA-specific coordination outside the
pure `api/` conversion helper, adds no dependency, preserves stable entity
identity, treats uncertain values explicitly, and names test gates before
implementation. No constitutional deviation is needed.

## Planning Uncertainties

No unresolved policy clarification blocks this plan: the specification
settles exact-room provenance, implicit timezone handling, folds/gaps,
handoff conditions, date-only fallback, identity/write behavior, sensor
fields, and legacy isolation.

Factual uncertainty remains distinct from those decisions: issue #282
describes a bridge observation but contains no raw response from this
integration; the Feature 008 V3 payload assumptions and
`plannedArrival`/`plannedDeparture` semantics are not independently verified;
and this repository has no live Guesty account to establish whether every
selected or child room appears in the returned listing set. Therefore the
implementation must be correct from fields already supplied and fail to
unknown when exact-room provenance is absent; it must not claim live
verification.

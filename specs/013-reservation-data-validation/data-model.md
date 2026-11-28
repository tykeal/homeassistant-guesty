<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Verifiable Reservation Data

This feature adds no Guesty API calls, no new entities and no new
configuration options. It adds:

- a pure validation result model;
- one fetch-result field;
- a separately persisted conflict-only evidence store;
- an identity store minor-version migration;
- a coordinator verified snapshot;
- per-record staleness;
- bounded reporting state.

Names below are the planned code names. Section references `R<n>` point
to [research.md](research.md).

## `ValidationReason` (new, `api/validation.py`)

`ValidationReason` is a `StrEnum` of fixed categories. A reason never
carries a value from the payload.

Record-level reasons, in evaluation order:

- `malformed_status`: `status` is missing, empty or not a string.
- `unrecognized_status`: a `status` string not in `V3_STATUS_MAP`.
- `missing_identifier`: by-ID only; `reservationId` is not a non-empty
  string.
- `no_stay`: `stay` is missing, empty or not a list.
- `no_verified_segment`: no raw segment is verified.
- `partial_stay`: at least one raw segment is rejected. This rejects
  writes only; the verified segments are still published.
- `unverified_instant`: legacy only; `checkIn` or `checkOut` missing,
  unparsable or without an explicit offset.
- `invalid_listing`: legacy only; `listingId` is not a non-empty
  string.
- `invalid_interval`: legacy only; check-in is not strictly before
  check-out.
- `unsupported_action`: the `validate_record` action is not in the fact
  table.

Segment-level reasons:

- `segment_malformed`: the segment is not an object.
- `segment_missing_listing`: `listingId` is not a non-empty string.
- `segment_malformed_parent`: `parentListingId` is present, not `null`
  and not a non-empty string.
- `segment_unverified_dates`: no canonical localized check-in or
  check-out date.
- `segment_invalid_dates`: check-in is not strictly before check-out.

Listing-level reason (reporting only):

- `listing_timezone_unverified`: the timezone is missing or cannot be
  loaded.

The record-level reasons are exclusive: a record reports its first
failing reason in the order above. Segment reasons are counted for each
raw segment.

## `SegmentAssessment` and `RecordAssessment` (new, `api/validation.py`)

Both are frozen dataclasses with no payload references other than the
verified, typed values.

`SegmentAssessment` fields:

- `raw_position: int`: index in the raw `stay[]`.
- `segment: GuestyStaySegment | None`: present only when verified.

- `reason: ValidationReason | None`: present only when rejected.

The assessment layer applies the segment rules to the raw dict, then
builds the verified segment. `GuestyStaySegment.from_api_dict` and
models built directly (for example with `dataclasses.replace`) keep
their current behavior; they are not the validation boundary.
Coordinator-level defensive checks on such models (for example
`_snapshot_listing_ids`) stay in place. The exceptions are
`GuestyReservation.from_v3_api_dict` and the legacy source path: both
are rewritten to build only from an assessment (below and R4/R5), with
no logging and no written-date fallback.

`RecordAssessment` fields:

- `reservation_id: str | None`.
- `status: str | None`: the mapped recognized status.
- `raw_stay_count: int`.
- `segments: tuple[SegmentAssessment, ...]`, in raw order.
- `reason: ValidationReason | None`: the record-level rejection. It is
  never `partial_stay` or `unsupported_action`. Those reasons appear
  only in `validate_record`'s `RecordValidation.reason`; a partial
  record has `reason is None` and is identified by its segments.
- `conflict_code: str | None`: a non-empty string `confirmationCode`
  only when the status is not a recognized hold (R7). Present for any
  record with a verified ID, accepted or rejected.

Derived properties:

- `accepted`: `reason is None` and at least one verified segment.
- `fully_verified`: `accepted` and every segment is verified.
- `partial`: `accepted` and not `fully_verified`.

Validation rules:

- Optional fields with invalid types never reject the record. They
  become `None` or empty in the built model and are never published.
  The optional fields are `guestsCount` (a non-negative `int`),
  `nightsCount`, planned arrival and departure, `source`, `notes`,
  `customFields` and `guestId`.
- A non-string `confirmationCode` stays on the model unchanged, as
  today, so feature 014 classifies it as `code_kind=invalid`, never
  code-less. It gives no conflict-only pair and is never published.
- Top-level localized dates may fill a missing segment date only under
  the full, unchanged feature 010 predicate (research R4 decision 2):
  - exactly one raw segment;
  - that segment is a dict with a listing;
  - the segment has no `checkIn` or `checkOut` of its own;
  - the top-level `checkIn` and `checkOut` parse, with check-out after
    check-in.
- The timestamp written-date fallback does not exist (R4).

## `RecordValidation` (new, `api/validation.py`; feature 014 interface 3)

`RecordValidation` is a frozen dataclass:

- `valid: bool`.
- `reason: ValidationReason | None`: `None` iff `valid`.

It is produced by
`validate_record(raw: Mapping[str, object], action_type: str) ->
RecordValidation`:

- `valid` iff `reservationId` is a non-empty string, the assessment is
  `fully_verified`, and `action_type` is one of `add_reservation_note`,
  `set_reservation_status` or `set_custom_field` (R12).
- A partially valid record returns `partial_stay`.

## `GuestyReservation` (changed)

- Built only from a `RecordAssessment`'s verified parts.
- `stay` holds only verified segments.
- `raw_stay_count` holds the raw length.
- `status` is always a recognized mapped value.
- New field `partial: bool = False` is true when any raw segment was
  rejected. It selects the segment-aware projection and keeps the
  reservation out of identity recording (R3, R11).

`from_v3_api_dict(raw)` keeps its signature for feature 014 and
returns `None` for a rejected record.

## `ReservationFetchResult` (changed; feature 014 interface 1)

- `rejected_ids: tuple[str, ...] = ()`: sorted, de-duplicated,
  batch-verified IDs that are record-rejected or partially valid.
  - For v3, it is a subset of `returned_ids` (014 T011).
  - The legacy source fills it under the same rules (research R5, R6).
    Feature 014 ignores non-v3 sources.
- `rejected_codes: tuple[RejectedCode, ...] = ()`, where
  `RejectedCode` is `NamedTuple(reservation_id: str, code: str)` in
  `api/models.py`: sorted pairs from this refresh's rejected or
  partial records (R7 decisions 1–2), merged with
  `FallbackOutcome.rejected_codes` from `_INVALID` lookups (R8). The
  bounds below are merged the same way. It is a tuple, so shared codes
  cannot collapse. Internal; never logged or in diagnostics.
- `rejected_check_out_bounds: Mapping[str, str]` (empty by default):
  `reservation_id → check_out_bound` for coded rejected records that
  have a retirement bound (research R7 decision 4).
- `validation: ValidationSummary`: counts only (below).
- `fallback_invalid_ids: tuple[str, ...] = ()`: copied from
  `FallbackOutcome.invalid`. `async_record_refresh` pops their hints.
  They are never part of `rejected_ids`.
- `stale_ids: tuple[str, ...] = ()`: fallback-published IDs whose
  latest lookup failed (FR-012g).

`status_counts` keys are recognized mapped statuses plus
`unrecognized` (R2). If feature 014 T011 landed first, `rejected_ids`
already exists and only its computation changes.

### `ValidationSummary`

`ValidationSummary` is a frozen dataclass holding only counts:

- `rejected_records`, `partial_records` and `rejected_segments`;
- `reasons: Mapping[ValidationReason, int]`.

## `ConflictOnlyEvidence` (new, `identity.py`; feature 014 interface 2)

It is owned by `ReservationIdentityGuard` and reached as
`coordinator.identity.conflict_evidence`.

### Persisted payload

The payload is a separate `Store`: version 1, key
`guesty.{entry_id}.reservations_conflict_evidence`.

```json
{
  "entries": [
    {
      "code": "<code>",
      "reservation_id": "<id>",
      "path": "v3",
      "fallback": false,
      "observed_at": "<iso-8601 aware>",
      "check_out_bound": "<YYYY-MM-DD> | null"
    }
  ],
  "saturated": false,
  "generation": 0,
  "covered_past_days": null
}
```

Validation on load:

- Each entry needs:
  - a non-empty string `code` and `reservation_id`;
  - a known `path`;
  - a boolean `fallback`;
  - a parseable, offset-aware `observed_at`;
  - a `check_out_bound` that is `null` or a canonical date.
- `covered_past_days` must be `null` or a non-negative integer.
- Any malformed entry or duplicate `(reservation_id, code)` pair makes
  the store **compromised**. It is never repaired by dropping entries.
- A code may appear with several IDs, and an ID with several codes.
  Every pair is kept.

### State

Only `ready` has `available()` true and applies the normal guards.
Every other state refuses every reservation write.

- `IdentityGuard.async_load` loads both stores before the first
  comparison.
- Precedence, highest first: `compromised`, `saturated`, `unstored`,
  `revalidating`, `ready`. `compromised` and `saturated` are sticky.
- While `compromised` or `saturated`, a refresh still compares the
  current refresh's pairs and every loaded stored pair, but it never
  advances continuity to confirmed, never retires the bootstrap and
  never clears `revalidation_pending` (research R7 decision 7).

- `unloaded`:
  - entered at setup; left when the load completes;
  - refusal `REFUSAL_EVIDENCE_UNAVAILABLE`; DEBUG log only.
- `revalidating`:
  - held while any of these holds: the identity migration is pending
    (R9); no accepted refresh has completed both its step 1 and
    step 2 saves since this load; or the current `past_days` (read
    through a supplier the coordinator passes to the guard) exceeds the
    adopted `covered_past_days` (`null` means no window is covered
    yet);
  - every cause ends only when a successful step 2 follows a
    successful step 1. Step 1 persists `covered_past_days` equal to
    that refresh's `past_days`, but the guard adopts it, clears the
    causes and adopts `_baseline` together, synchronously, right
    before `on_baseline_stored` assigns the routing sets and snapshot.
    A write during the step 2 await therefore still sees
    `revalidating` (research R7 decisions 5, 7 and 8);
  - refusal `REFUSAL_REVALIDATING`; DEBUG log only.
- `ready`:
  - entered only when an accepted refresh has completed both its
    step 1 and step 2 saves and no other state applies (a load alone gives `revalidating`);
    left for any state below.
- `unstored`:
  - entered on a save failure, and held while unsaved pairs are
    pending; left when a save containing every pending pair succeeds;
  - one WARNING; no repair issue.
- `saturated`:
  - entered when a new pair would exceed `MAX_CONFLICT_ONLY_EVIDENCE`;
  - left only when the entry is removed and re-added;
  - raises the repair issue.
- `compromised`:
  - entered when the store is corrupt, unreadable or malformed, or its
    `generation` is below the baseline's `conflict_evidence_generation`
    (a missing store counts as generation 0);
  - the evidence store is never written in this state, so a restart
    loads `compromised` again;
  - a cleanly parsed but lagging store still feeds its pairs to
    `compare`;
  - left only when the entry is removed and re-added;
  - raises the repair issue.

### Methods

- `available() -> bool`: true only in `ready`.
- `codes_for(reservation_id: str) -> frozenset[str]`.
- `ids_for(code: str) -> frozenset[str]`.

Both lookups raise `RuntimeError` unless `available()` is true. This
matches feature 014's `UnavailableConflictEvidence`.

- `state -> str`, `count -> int`, `dated -> int` and `undated -> int`
  (diagnostics).
- `record(pairs: Iterable[RejectedCode], bounds: Mapping[str, str],
  path: str, now: datetime, fallback_ids: Collection[str]) -> bool`:
  pairs whose ID is in `fallback_ids` are stored with `fallback: true`,
  and a stored pair's `fallback` only ever changes to true. It adds new
  pairs and returns false
  on saturation. A pair already stored keeps its entry, but its bound
  moves only later: a later bound replaces it, and a missing bound sets
  it to `null` (research R7 decision 4).
- `retire(*, superseded: Collection[str], absent: Collection[str],
  returned: Collection[str], past_days: int, now: datetime) -> None`:
  applies R7 decision 5.
- `compare_maps() -> Iterable[_StoredMap]`: stored and pending pairs
  (the full in-memory set, research R7 decision 8) for each
  recorded path, as `_StoredMap(source=SOURCE_CONFLICT_ONLY,
  confirms=False, soft=...)`. Conflict checks iterate pairs, so a code
  with several IDs is checked against each one (R7).
- `async_load()`, `async_save()` and `async_remove()` (on entry
  removal).

### Transitions in an accepted refresh

The steps run after compare passes, in the save order of research R7
decision 8:

1. Record the pending set and this refresh's rejected pairs and bounds,
   advance `generation`, and save. On failure, keep them pending.
2. The identity baseline is saved with the advanced
   `conflict_evidence_generation`.
3. Only when both saves succeeded, retire pairs by supersession (IDs
   now fully verified), confirmed absence (`absent_ids`) and the
   verified horizon, and save again.

A failed refresh changes nothing.

## `IdentityBaseline` and the identity `Store` (changed)

- The `Store` is now version 1, minor version 2, through the
  `_IdentityStore` subclass (R9).
- New persisted fields:
  - `revalidation_pending: bool = False`;
  - `conflict_evidence_generation: int = 0`.
- `to_dict` and `from_dict` keep unknown-key tolerance.
- Load-time structural revalidation applies to:
  - `codes`, `legacy_codes`, `bootstrap_codes` and `fallback_codes`;
  - candidate hints (downgrade or drop as described in R9). A payload
    migrated from minor version 1 downgrades **every** `known` hint to
    unknown timing, so no pre-013 interval feeds fallback eligibility,
    `check_outs`, eviction or bootstrap retirement.
- `observe_codes(reservations, conflict_only=())` scans accepted and
  conflict-only pairs together for within-refresh collisions, and
  returns the conflict-only `code → id` map separately.
- `compare(observed, ..., conflict_only: Mapping[str, str])`:
  - Conflict-only mappings join conflict checks only.
  - The coordinator passes `fallback_sourced` plus
    `fallback_invalid_ids` as `fallback_ids`, so an `_INVALID` pair's
    conflicts stay hard, as today.
  - A stored pair with `fallback: true` is never soft and never
    confirms.
  - New compare source `SOURCE_CONFLICT_ONLY` for stored conflict-only
    maps.
- `async_record_refresh(..., rejected_ids: Collection[str],
  fallback_invalid_ids: Collection[str],
  rejected_codes: tuple[RejectedCode, ...],
  rejected_check_out_bounds: Mapping[str, str])`:
  - Hints for `rejected_ids` and `fallback_invalid_ids` are popped
    (not treated as absent).
  - Follows the crash-consistent save order in research R7 decision 8:
    1. evidence save of additions, including the pending set;
    2. identity save with the advanced generation, which clears
       `revalidation_pending` only when step 1 succeeded;
    3. evidence save of retirements, only when steps 1 and 2
       succeeded.
  - Takes a synchronous `on_baseline_stored` callback, called right
    after step 2 succeeds and `_baseline` is adopted, before step 3
    and with no await in between. The coordinator uses it to assign
    the fallback routing sets and the accepted snapshot (research R8).
  - Drives the `ConflictOnlyEvidence` transitions.
- `classify_write_refusal` gains `REFUSAL_EVIDENCE_UNAVAILABLE` and
  `REFUSAL_REVALIDATING`. They are checked after the existing checks
  (baseline unusable, unstored, continuity unconfirmed, ambiguous) and
  **before** the membership check (`missing_current_code`), so they
  never start a 012 recovery refresh or a 014 by-ID read (research
  R18). The refusal is chosen from the reported evidence state, so
  `compromised` or `saturated` always gives
  `REFUSAL_EVIDENCE_UNAVAILABLE`, even while revalidation is pending.

## `ReservationFallbackSource` (changed)

`resolve()` keeps its live in-place cache updates. The coordinator
passes the candidate sourced set to `_check_identity`, and assigns
`fallback_served_ids` and `fallback_sourced_ids` in the
`on_baseline_stored` callback, so the routing sets always match the
stored baseline (research R8).

- `resolve(..., rejected: Collection[str] = ())`, where `rejected` is
  all of `rejected_ids`, partially valid records included:
  - `_absorb` skips those IDs, so they are never retained.
  - Rejected IDs count as returned, so they are not eligible.
  - They are purged from `_served`, `_retained`, `_last_attempt` and
    `_stale`.
  - They are discarded from `_confirmed_absent` and
    `_unreported_absent`, because they are present, and they are never
    confirmed absent.
- A new lookup verdict, `_INVALID`, applies when the legacy by-ID
  document is a dict with the requested `_id` but fails validation
  (research R8):
  - the ID is purged from `_served`, `_retained`, `_last_attempt` and
    `_stale`;
  - it is never confirmed absent;
  - a non-hold string code gives a pair in
    `FallbackOutcome.rejected_codes: tuple[RejectedCode, ...]`, with
    its bound in `FallbackOutcome.rejected_check_out_bounds`;
  - it is added to `FallbackOutcome.observed`, and the branch calls
    `_note_session("observed", id)` explicitly, so feature 014's
    session legacy-observed provenance records it;
  - it is not added to `rejected_ids`.

  `_FAILED` stays limited to transport, HTTP or non-JSON failures and to
  documents that are not a dict or carry another `_id`.
- `_stale: set[str]`:
  - Added when a due lookup fails (`_FAILED`) while the ID is
    published.
  - Removed on an `_OK` lookup, on `_absorb`, on retirement or on
    purge.
- `FallbackOutcome.stale: tuple[str, ...]`.
- `FallbackOutcome.invalid: tuple[str, ...]`: the `_INVALID` IDs of
  this refresh.

## `VerifiedSnapshot` (new, `coordinator.py`)

It is memory-only and distinct from feature 014's
`AcceptedRefreshSnapshot`.

- `reservations: tuple[GuestyReservation, ...]`: accepted records
  before publication filtering, including partially valid ones (for
  reads).
- `stale_ids: frozenset[str]`.
- `accepted_at: datetime`.

Coordinator attributes:

- `_verified_snapshot: VerifiedSnapshot | None`.
- `snapshot_stale: bool`.
- `stale_reservation_ids: frozenset[str]`.
- `stale_display_available: bool` (property): true only while
  `last_update_success` is false, `snapshot_stale` is true and `data`
  holds the re-projection of a `VerifiedSnapshot`.
- `last_verified_at: datetime | None` (property): the snapshot's
  `accepted_at` (the last accepted refresh) while `snapshot_stale` is
  true, otherwise `None`. It never certifies a record that was
  per-record stale in that snapshot.

Behavior:

- An accepted refresh replaces the snapshot and clears
  `snapshot_stale`.
- A failed refresh re-publishes the snapshot through the pure
  publication function and sets `data` and `snapshot_stale` in the
  `_async_update_data` failure path before re-raising, so Home
  Assistant's first-failure listener update already sees them. After a
  failure that followed an already failed refresh, the `_async_refresh`
  override calls `async_update_listeners()` itself, because Home
  Assistant does not notify listeners then (R10).
- `last_update_success` is never overridden, so write guards still see
  the failure.

## Publication output (changed)

Reservation projection dicts gain `stale: bool`. `timing_status` and
`status` follow R4:

- A conclusively expired temporal stay is `checked_out`.
- A verified-timezone checkout-day date-only stay is `unknown` with
  `reservation_status`.
- An unverified-timezone stay that is not conclusively expired is
  `unknown` with `reservation_status`.

Sensor attributes gain a top-level `stale`, a top-level
`last_verified_at` (ISO 8601) and an item-level `stale` in `upcoming`.

## Reservation entity availability (changed, `sensor.py`)

`GuestyReservationSensor.available` and
`GuestyFinancialSensor.available` replace their `super().available`
coordinator-health check with `coordinator.last_update_success or
coordinator.stale_display_available`. Every other existing check
(`data` present, listing tracked, financial field present) is
unchanged. With no snapshot after a failure they are unavailable (R13).

`GuestyFinancialSensor` attributes gain `stale` (true when the snapshot
is stale or its selected reservation is per-record stale) and
`last_verified_at` (the coordinator property; `null` for per-record
staleness alone), beside `listing_id`.

## `ExclusionTracker` (new, `coordinator.py`)

- `_active: dict[tuple[ValidationReason, str], None]`, bounded by
  `MAX_TRACKED_EXCLUSIONS = 256`.
- `_overflowed: bool`.
- `report(keys: Iterable[tuple[ValidationReason, str]]) -> None` runs
  after an accepted refresh only:
  - a new key logs one WARNING;
  - a seen key logs at DEBUG;
  - a vanished key is forgotten;
  - overflow behaves as in R14.

## Repair issues (new)

All three issues are non-fixable.

- `listing_timezone_unverified_{entry_id}_{listing_id}`:
  - severity warning;
  - placeholders `listing_name` and `listing_id`;
  - created when a tracked listing with reservations lacks a verified
    timezone;
  - deleted when the timezone is verified or the listing is untracked.
- `conflict_evidence_near_capacity_{entry_id}`:
  - severity warning;
  - no placeholders;
  - created at 80% of `MAX_CONFLICT_ONLY_EVIDENCE`;
  - deleted below 70%, or on entry removal.
- `conflict_evidence_unavailable_{entry_id}`:
  - severity error;
  - no placeholders;
  - created only in `saturated` or `compromised`;
  - deleted on entry removal.

## Diagnostics (changed)

New block `reservations.validation`:

```json
{
  "rejected_records": 0,
  "partial_records": 0,
  "rejected_segments": 0,
  "reasons": {"segment_missing_listing": 0},
  "rejected_ids": ["..."],
  "rejected_ids_truncated": false,
  "conflict_evidence": {
    "state": "ready",
    "available": true,
    "count": 0,
    "dated": 0,
    "undated": 0,
    "bound": 5000,
    "near_capacity": false
  },
  "stale": {
    "snapshot_stale": false,
    "stale_count": 0,
    "last_verified_at": null
  }
}
```

`rejected_ids` is capped at 100. No codes, statuses, guest data or
payload values ever appear.

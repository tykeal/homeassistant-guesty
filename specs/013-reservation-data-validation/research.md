<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Verifiable Reservation Data

## Scope

The specification is settled. This document records how the live code
behaves today, the decisions the plan takes, and why. No
`NEEDS CLARIFICATION` item remains. R13 records how reservation
entities stay available with stale data despite Home Assistant's
coordinator-entity default.

Governing rule for every decision: publish or act only on data that is
reliably proven actionable and useful; fail closed; never weaken
identity, continuity, routing, or privacy guards.

Sources inspected (first at `0f6343b`, then re-checked at `3734710`,
which includes feature 014 T001–T048 from PR #325):

- `custom_components/guesty/api/models.py`:
  - `GuestyListing.from_api_dict` defaults `timezone` to `UTC` and sets
    `timezone_explicit`;
  - `GuestyStaySegment.from_api_dict`;
  - `GuestyReservation.from_v3_api_dict` and `from_api_dict`;
  - `map_v3_status`, `_parse_iso_datetime`, `_valid_local_date`;
  - `ReservationFetchResult`.
- `custom_components/guesty/api/reservations_v3.py`: `_validate_page`,
  `_validate_identifiers`, `_map_results`, `fetch`.
- `custom_components/guesty/api/reservations_legacy.py` and
  `api/reservations_fallback.py`: `resolve`, `_absorb`, `_lookup`,
  `_publish`, `_parse`.
- `custom_components/guesty/api/timing.py`: `resolve_local_datetime`.
- `custom_components/guesty/api/const.py`: `V3_STATUS_MAP`,
  `HOLD_STATUSES`, `ACTIONABLE_STATUSES`.
- `custom_components/guesty/coordinator.py`:
  - `_async_refresh`, `_async_fetch_reservations`,
    `_resolve_v3_reservations`, `_check_identity`, `_record_identity`;
  - `_within_window`, `_attribute_segments`, `_project`,
    `_select_segment`, `_segment_status`;
  - `_publish_refresh_diagnostics`.
- `custom_components/guesty/identity.py`: `observe_codes`,
  `IdentityBaseline`, `ReservationIdentityGuard`, `_maps`, `compare`,
  `async_record_refresh`, `classify_write_refusal`,
  `_verified_candidate_interval`, `_bounded`.
- `custom_components/guesty/actions.py`: `guard_reservation_write`,
  `select_reservation_write_route`, `execute_reservation_write`.
- `custom_components/guesty/sensor.py`: `GuestyReservationSensor`,
  `_select_reservation`, `_derive_state`, `_build_attributes`,
  `_build_upcoming`.
- `custom_components/guesty/diagnostics.py` and `__init__.py` (the
  existing repair-issue pattern).
- Home Assistant 2026.10.4 `helpers/entity.py`
  (`__async_calculate_state`) and `helpers/storage.py` (`Store`
  migration and version checks).
- Feature 014 `research.md` (R1), `data-model.md` ("013 evidence
  adapter"), the contract ("Required feature 013 interface") and
  `tasks.md` (T011, T019–T020, T049–T055).

## R1 — One pure validator shared by refresh and by-ID reads

**Finding**: Validation is scattered and partly silent:

- `GuestyStaySegment.from_api_dict` drops non-dict and listing-less
  segments, and `from_v3_api_dict` keeps the survivors (FR-005a, FR-008d
  violation).
- `parentListingId` and `guestsCount` are copied unchecked.
- Unknown statuses pass through verbatim (FR-002 violation).

Feature 014 needs the same rules applied to one by-ID record
(`validate_record(raw, action_type)`).

**Decision**: Add a new HA-free module,
`custom_components/guesty/api/validation.py`. It owns the 013 rules as
pure functions over raw v3 dicts:

- `assess_v3_record(raw) -> RecordAssessment` is the single source of
  truth used by `_map_results`.
- `validate_record(raw, action_type) -> RecordValidation` is the
  single-record entry point for feature 014.

`validate_record` calls `assess_v3_record` and then applies the action
fact table (R12). `from_v3_api_dict` is rewritten to build a
`GuestyReservation` only from an assessment's verified parts. It never
re-reads raw segments.

**Rationale**:

- One implementation means the refresh and by-ID paths cannot drift.
- A pure module keeps the constitution's API boundary: no HA imports
  under `api/`.
- It is unit-testable without a coordinator.

**Alternatives considered**:

- **Patch `from_v3_api_dict` in place.** Rejected: it cannot report
  *why* a record or segment was rejected, and feature 014 would need a
  second copy of the rules.
- **Validate in the coordinator.** Rejected: feature 014's validator
  must run in the service layer without a coordinator refresh, and
  validation must happen before any model is built.

## R2 — Status allowlist and FR-002

**Finding**: `map_v3_status` returns an unrecognized raw status
unchanged and logs it at INFO with the reservation ID. This both
publishes unsupported data and echoes an untrusted value (FR-010). A
non-string status reaches `.strip()` and raises, which today fails the
whole refresh. `_map_results` counts *raw* statuses into
`status_counts`, which reaches diagnostics and the DEBUG log.

**Decision**:

- The recognized vocabulary is the key set of `V3_STATUS_MAP`, compared
  after `strip().lower()`. It is unchanged, and `alteration` remains
  recognized as a hold.
- A status that is a string but not in the map rejects the record with
  reason `unrecognized_status`.
- A missing, empty or non-string status rejects the record with reason
  `malformed_status`. The refresh is not failed.
- The value is never logged.
- Replace `map_v3_status` use in the parser with a non-logging
  `recognize_v3_status(raw) -> str | None`.
- `status_counts` is keyed only by mapped recognized statuses, plus one
  `unrecognized` bucket. No raw value is ever a key.
- The legacy source and the legacy by-ID fallback apply the same
  allowlist to `status` through `recognize_v3_status`; any other
  string is `unrecognized_status`. The configured status set is a
  subset of `ACTIONABLE_STATUSES`, and a recognized status outside it
  is valid but unpublished (contract C17). HOLD statuses are handled
  as they are today.

**Rationale**: FR-002 supersedes feature 008 FR-026 for publication.
Rejecting a non-string status instead of crashing the refresh is the
FR-003b record-isolation path. The record's identifier has already
passed batch validation (R5), so it is isolatable.

**Alternatives considered**:

- **Map unknowns to `unknown` and publish them.** Rejected: that
  publishes unverified lifecycle data.
- **Fail the refresh on any unknown status.** Rejected: FR-003b requires
  isolation when the batch is otherwise safe.

## R3 — Segment validation, raw-stay eligibility and FR-008d

**Decision**: Each raw `stay[]` entry is assessed at its raw position.
A segment is **verified** only if all of the following hold:

- it is a dict;
- `listingId` is a non-empty string;
- `parentListingId` is absent, `null` or a non-empty string; any other
  value rejects the segment (this matches feature 014's
  "fail closed on malformed parent IDs");
- it has a verified date-level interval (R4): canonical
  `checkInDateLocalized` and `checkOutDateLocalized`, with check-in
  strictly before check-out.

`guestsCount` that is not a non-negative `int` is not a rejection. The
field becomes `None` (an invalid optional field is never published or
retained).

A reservation is assessed as follows:

- **Record rejected**: it has no verified segment, its status is
  rejected (R2), or `stay` is not a non-empty list.
- **Partially valid**: at least one segment is verified and at least
  one is rejected.
- **Fully verified**: every raw segment is verified.

A partially valid reservation:

- publishes only its verified segments, keeping each segment's own
  dates, listing, guest count and night count;
- has no reservation-level span. The base model's reservation-level
  `check_in`, `check_out`, `check_in_local`, `check_out_local`,
  `nights_count` and `guests_count` are `None` for a partial record, so
  nothing can cover the rejected gap (FR-005b). `listing_id` is the
  first verified segment's listing, a default that projection always
  replaces. `_resolve_v3_reservations` keeps them `None` for partial
  records, and `_within_window` tests each verified segment on its own:
  the record is in the window iff any verified segment is;
- keeps `raw_stay_count` equal to the original length, so the
  single-segment paths (feature 010 date fallback and the coordinator's
  `len(stay) <= 1` projection) are chosen from the **raw** count, never
  the survivor count (FR-005c). A partially valid reservation always
  uses the segment-aware projection, even with one survivor. It never
  uses reservation-level `nightsCount`, `guestsCount` or span;
- allows same-date handoffs only between survivors at adjacent **raw**
  positions (raw position difference of exactly 1), unchanged from
  `_apply_same_date_handoffs`. Survivors either side of a rejected
  segment are never adjacent;
- is listed in `rejected_ids` (FR-008d), so it is not a current
  writable identity and gets no fallback candidate hint (FR-005a).

A rejected segment leaves no trace in the model, caches, persisted
state or candidate hints. Only a reason category and a count survive.

**Rationale**:

- FR-005a–005c and FR-008d require exactly this split between read
  eligibility per segment and write eligibility per reservation.
- `_verified_candidate_interval` already refuses a candidate envelope
  when `len(stay) != raw_stay_count`, so the fallback is already
  consistent. The plan adds an explicit hint drop so that eligibility
  does not depend on that coincidence.

**Alternatives considered**:

- **Reject the whole reservation on any bad segment.** Rejected: it
  contradicts FR-005a, which keeps good segments for reads.
- **Allow zero-night (`in == out`) segments.** Rejected:
  `_verified_candidate_interval` already treats a non-positive interval
  as unverified, and a zero-length interval cannot be placed in a
  current or upcoming classification. It is rejected as
  `segment_invalid_dates`.

## R4 — Dates, timezones and the feature 010 fallback

**Findings**:

- A listing timezone is used for exact instants only when
  `timezone_explicit` is true and `ZoneInfo` loads (`_async_timezone`,
  cached; `None` for an invalid zone).
- `resolve_local_datetime` refuses a DST gap or fold.
- The feature 010 fallback replaces missing localized dates with
  `fallback_check_in.date().isoformat()`. That is the timestamp's
  *written* date, which is not proven to be the listing-local date
  (FR-006, US1-S20).
- `_within_window` returns `True` (keeps the record) whenever the
  timezone is unverified.
- `_segment_status` never expires a date-only checkout. This is the
  #297 and SC-003 crowding risk.

**Decisions**:

1. **Verified timezone**: `timezone_explicit` and a loadable `ZoneInfo`.
   An explicit `UTC` is verified (FR-006). The model default `UTC` never
   is. The predicate is shared by window, expiry and candidacy code.
2. **Date-level interval**: only canonical ISO localized date strings
   (`_valid_local_date`). Raw segment instants are never parsed; that
   is unchanged, and instants are derived later from local dates under
   a verified timezone.
   - The first source is the segment's own `checkInDateLocalized` and
     `checkOutDateLocalized`.
   - Top-level `checkInDateLocalized` and `checkOutDateLocalized` may
     fill a missing segment date only under the **unchanged** feature
     010 eligibility predicate:
     - exactly one raw segment;
     - that segment is a dict with a listing;
     - the segment has no `checkIn` or `checkOut`;
     - the top-level `checkIn` and `checkOut` parse with
       `check_out > check_in`.

     Keeping every condition only narrows acceptance. Tests cover each
     condition failing (the date stays missing, so the segment is
     rejected) and the eligible case.
3. **The timestamp written-date fallback is removed.** A
   single-raw-segment stay with no localized date anywhere has no
   verified date-level interval. Its segment is rejected
   (`segment_unverified_dates`), so the record is rejected. Re-enabling
   it needs live evidence (#223) that the written date equals the
   listing-local date.
4. **Exact instants**: unchanged. They require a verified timezone and a
   resolvable local wall time; a gap or fold leaves the instant unknown.
5. **Local-day comparisons without a verified timezone** use a
   zone-independent bound, never UTC as authority. Every IANA zone has
   an offset between −12:00 and +14:00, so any listing's local date
   lies in `[utc_date − 1, utc_date + 1]`. A comparison is
   **conclusive** only when it holds for every date in that band:
   - expired: `check_out_local < utc_date − 1`;
   - window membership:
     `check_out_local >= utc_date + 1 − past_days` and
     `check_in_local <= utc_date − 1 + future_days`.

   Anything else is inconclusive.
6. **Expiry (FR-004, FR-004b)**: Today only multi-segment v3 stays
   expire, through `_segment_status`. `_project`'s `len(stay) <= 1`
   branch and legacy records are published unchanged, so a past
   `confirmed` single-segment stay stays `confirmed` and the sensor
   ranks it as current or upcoming. Expiry is therefore **new** for
   single-raw-segment v3 projections and for legacy projections. All
   three paths use one shared expiry predicate:
   - An exact checkout at or before `now` is expired.
   - A date-only checkout before the verified local date is expired.
   - With an unverified timezone, the checkout is expired only when it
     is conclusively before every possible local date (decision 5).
   - An expired temporal stay projects as `checked_out` with
     `reservation_status=None`, the same as the multi-segment exact
     case today, and stays eligible for history within the window.
   - Legacy records carry offset-verified instants (R5), so legacy
     expiry uses the exact rule.
7. **Checkout-day candidates (FR-004a)**: with a verified timezone, a
   date-only checkout equal to the verified local date projects as
   status `unknown`, with `reservation_status` set to the source
   lifecycle and `timing_status` `unknown`. That is the existing
   feature 011 uncertainty representation: never an instant, never
   proof of occupancy. Without a verified timezone, a non-conclusively
   expired temporal stay keeps the same uncertain representation and
   no local-day claim is made.
8. **Window (FR-001, FR-003, FR-012a; feature 008 FR-002a)**: with a
   verified timezone, unchanged. Without one, a date-only stay is in
   the window only when membership
   is conclusive under decision 5. An inconclusive stay is not published
   to the window-limited output. It is still a valid record for writes
   (FR-008, R11).
9. **Ordering**: expiry runs during projection, before the sensor
   selects candidates and before the ten-item upcoming limit (FR-004c,
   SC-003). `_select_reservation`'s priority is unchanged.

**Rationale**: The bound is a fact about real timezones, not an assumed
zone. It lets far-past stays expire without claiming any listing's
"today". Decisions 3 and 8 fail closed by dropping uncertain
classifications and keeping verified data for uses that do not need
them.

**Alternatives considered**:

- **Treat an unverified timezone as UTC.** Rejected: FR-004b and
  FR-007a.
- **Keep everything with an unverified timezone (today's behavior).**
  Rejected: expired stays then crowd the upcoming list (SC-003), and
  stale data whose eligibility cannot be verified would be shown
  (FR-012a).
- **Reject records with unverified timezones.** Rejected: FR-007 keeps
  date-only data for date uses.

## R5 — Malformed-record isolation versus batch fail-closed

**Finding**: `_validate_page` and `_validate_identifiers` already fail
the whole refresh on:

- a non-JSON body, or a missing `results` or `pagination`;
- a non-boolean `hasMore`;
- a skip mismatch, an oversize page, or an empty or short page while
  `hasMore`;
- a missing or non-string `reservationId`, a duplicate ID, or
  non-ascending IDs.

Every record that reaches `_map_results` therefore has a verified,
unique string identifier.

**Decision**: A record is **isolatable** exactly when its identifier
passed batch validation. In that case its assessment may reject it
(R2–R4) and the rest of the batch publishes (FR-003b). The refresh still
fails closed when:

- any existing page or identifier check fails (unchanged);
- a non-dict item reaches mapping (it has no verified identifier);
- identity comparison is ambiguous. This includes conflicts involving
  rejected records' mappings, under R7.

Legacy source: legacy pages have no identifier ordering checks. The
legacy anchor is defined as follows:

- Every item must be a dict with a non-empty string `_id`. Otherwise the
  legacy refresh fails closed. Today such an item is skipped, but it
  cannot be isolated.
- De-duplication across the primary and `checked_in` queries stays
  first-wins by `_id`. It runs on the raw items before assessment, so a
  rejected record and an accepted duplicate cannot both survive.
- Other legacy rejections are isolated in the same way, and they emit
  `rejected_ids` and `rejected_codes` pairs under the same rules as v3
  (R6, R7). Today the new legacy rejections (status allowlist and naive
  instants) are accepted records whose codes reach `compare`, so
  dropping their codes would weaken the guard.
- A legacy record is verified only when all of these hold. Otherwise it
  is rejected with the given reason:
  - a recognized status (`malformed_status`, `unrecognized_status`);
  - a non-empty string `listingId` (`invalid_listing`). Today any truthy
    value is accepted;
  - `checkIn` and `checkOut` present, parseable and each with an
    explicit UTC offset (`unverified_instant`, FR-007a). A naive value
    is not treated as UTC;
  - check-in strictly before check-out (`invalid_interval`). Today this
    is not checked.

  Records that today's parser skips (missing listing, status or dates)
  are now rejected records under the same rules, so they also feed
  `rejected_ids` and conflict-only pairs. That is stronger than today,
  where their codes reach no check at all.
- **Identity bootstrap sweep** (`async_bootstrap_identity`) uses the
  same legacy source, so it keeps every code it sees today:
  - its collision scan is `observe_codes(fetched.reservations,
    conflict_only=fetched.rejected_codes)`, so a collision involving a
    rejected record still gives `BOOTSTRAP_AMBIGUOUS`;
  - rejected pairs are recorded as conflict-only evidence on the legacy
    path (never confirming), using the R7 decision 8 order: evidence
    additions, then `async_record_bootstrap`'s identity save with the
    advanced generation;
  - a missing `_id` now fails the sweep closed, which gives
    `BOOTSTRAP_UNAVAILABLE` (writes stay refused, as today).

A skipped record:

- is not "absent": it is excluded from fallback eligibility (R8) and is
  never in `absent_ids`;
- does not retire bootstrap evidence: `_retired_bootstrap` only retires
  codes the refresh *observed without conflict*, and a rejected
  record's mapping is never "observed" for retirement;
- never counts toward continuity matches.

**Rationale**: This reuses the existing batch-integrity anchor rather
than inventing a new completeness test. FR-003c is met by construction.

**Alternatives considered**:

- **Skip records with invalid IDs.** Rejected: an unidentifiable record
  cannot be proven not to be the target of a write or a conflict.

## R6 — Rejected-ID set (feature 014 interface 1)

**Decision**: `ReservationFetchResult.rejected_ids: tuple[str, ...] =
()` holds the sorted, de-duplicated identifiers that the v3 search
returned and that are not fully verified: record-rejected **or**
partially valid (FR-008d). It never contains:

- an **accepted** hold-status record (a recognized status, fully
  verified). A hold that is rejected or partially valid **is**
  included, so the fallback never treats it as missing (R8). Holds
  still never give conflict-only pairs (R7);
- a record that is valid but filtered by listing, status or window
  (valid, unpublished);
- an identifier that failed batch validation (the refresh fails
  instead).

For the v3 source, `rejected_ids ⊆ returned_ids`. A partially valid
reservation can be both published (verified segments) and in
`rejected_ids`. For any write decision, `rejected_ids` takes
precedence.

The legacy source fills `rejected_ids` under the same rules (R5), so
candidate hints and conflict-only evidence are handled consistently.
Feature 014 never consults a non-v3 source (its contract refuses on a
non-v3 source), so this does not affect it. 014 T011's "the legacy
source keeps the defaults" concerns only `returned_ids` and
`fallback_observed`.

Feature 014 T011 has landed (PR #325) with an interim "raw minus
parsed" value. Feature 013 **reuses** the field (same name and type)
and replaces only the computation in `_map_results` with the
assessment-based set.

**Rationale**: This matches feature 014 data-model "`rejected_ids`
(extended model)" and the contract "Required feature 013 interface"
exactly. 013 replaces 014's interim computation, so 014 T051 only
confirms it and needs no adapter logic.

## R7 — Conflict-only evidence (feature 014 interface 2)

**Findings**:

- `observe_codes` treats every non-hold record with a non-empty string
  code as identity evidence.
- Within-refresh collisions abort the refresh.
- `IdentityBaseline.compare` checks stored maps with provenance
  (`_maps`). A same-path stored conflict aborts; a carried-over map is
  "soft" (it refuses writes and publishes).
- `current_ids()` is `codes ∪ fallback_codes`.
- Only accepted records feed `codes`.
- Codes are stored in plain form in the identity `Store` and never in
  logs or diagnostics.

**Decisions**:

1. **Verified mapping** (FR-008b, US2-S16): a rejected record yields a
   conflict-only mapping iff:
   - its identifier passed batch validation;
   - its `confirmationCode` is a non-empty string;
   - its status is not a recognized hold.

   Nothing else from the payload is retained. A non-string or empty code
   yields no mapping.

   **Why this is independent verification**: the identity proof is the
   pair, not the record's lifecycle.
   - The identifier comes from a complete, integrity-checked batch:
     a string, unique and in ascending order on a page whose pagination
     was validated.
   - The code is a string field of that same identified record, served
     by the same authoritative source.

   That is exactly the standard `observe_codes` applies to accepted
   records today. Status takes part only through the hold exclusion,
   which stops a proposed alteration from manufacturing a conflict.

   An unrecognized or malformed status therefore does **not** prevent
   the mapping:
   - Today an unrecognized string status reaches `observe_codes`, and a
     non-string status crashes the refresh, so both already end in
     detection or a failed refresh.
   - Dropping the mapping would weaken the existing guard.
   - The residual risk is that an unknown status is really a new kind
     of hold. That can only cause a fail-closed abort, the same outcome
     as today.
2. **Collision-safe transport**: `ReservationFetchResult.rejected_codes`
   is a sorted `tuple[RejectedCode, ...]`, where `RejectedCode` is a
   `NamedTuple(reservation_id: str, code: str)`. It is not a dict, so
   two IDs that share a code cannot collapse into one entry.
   - `observe_codes` gains a `conflict_only` argument. Accepted and
     conflict-only pairs go through one collision scan: the same code
     on two IDs is a within-refresh collision with its existing outcome
     (ambiguous abort), whether the pair is rejected–rejected or
     rejected–accepted.
   - The conflict-only `code → id` map that results is kept apart from
     `mapping`.
3. **In-refresh use**: the conflict-only map joins comparison **for
   conflict detection only**. Stored-map conflicts keep their existing
   outcomes (FR-008b "unchanged"). Conflict-only pairs never add to
   `matched` or `cross_path_matched`, never enter `codes`, and so never
   establish or advance continuity.
4. **Persisted store**: a new `ConflictOnlyEvidence` is owned by
   `ReservationIdentityGuard` (`guard.conflict_evidence`). It is
   persisted in a **separate** `Store`, key
   `guesty.{entry_id}.reservations_conflict_evidence`, version 1.
   - Entries are a list of unique `(reservation_id, code)` pairs. One
     code may map to several IDs. A soft cross-path conflict does not
     abort, so a later pair can share a code with an earlier one. Both
     are kept, so nothing is overwritten.
   - Each pair holds only:
     - `path`;
     - `fallback`: true when the pair came from a legacy by-ID
       `_INVALID` lookup (R8);
     - `observed_at`;
     - the minimum verified safety metadata FR-008a allows,
       `check_out_bound`.

     `check_out_bound` is a date used **only** for retirement, never
     for publication or any local-day claim. It is the latest of these
     candidates:
     - every raw segment's verified local checkout date, when **all**
       raw segments have a verified date-level interval;
     - `utc_date(checkOut) + 1` from the record's top-level `checkOut`,
       when it parses as an offset-aware instant (v3), or the legacy
       record's offset-aware `checkOut`.

     The `+ 1` covers every positive offset, using the same band
     reasoning as R4 decision 5. An offset-aware ISO instant describes
     itself, so it is reliable as an instant. Only converting its
     written date into a listing-local date is forbidden (R4).

     If neither candidate exists, the bound is `null`. When both exist,
     the later one wins, so retirement never happens earlier than
     either piece of evidence allows.

     The top-level candidate requires an **explicit** offset in the raw
     string (`Z` or `±HH:MM`), checked before parsing.
     `_parse_iso_datetime` treats a naive value as UTC, so it is not
     enough on its own. A naive top-level `checkOut` gives no
     candidate.
   - **Re-observation**: when a stored pair is rejected again, its
     bound only ever moves later. A new bound replaces the old one
     when it is later. A re-observation with no bound sets it to
     `null`, so a pair whose dates are no longer verifiable is never
     horizon-retired on old evidence.
   - It participates in `compare` as a new stored source
     `SOURCE_CONFLICT_ONLY`, with `confirms=False` and `soft` equal to
     "recorded on another path". This is the provenance rule `codes`
     already follows, so the applicable existing outcome is applied
     (US2-S15). A stored pair with `fallback: true` is never soft and
     never confirms, like `SOURCE_FALLBACK` today: it came from the
     legacy resource, so a disagreement can only be a re-key (FR-032,
     FR-055).
   - **Fallback origin in the same refresh**: today a legacy by-ID
     record is published and its ID is in `fallback_sourced`, so the
     live `compare` treats any conflict it is part of as hard. An
     `_INVALID` record is not published, so its ID is added to the
     `fallback_ids` set passed to `compare` and to the collision scan.
     Its conflicts therefore keep that hard outcome.
   - A stored code with several IDs conflicts with any observed ID that
     differs from one of them.
   - It is never part of `current_ids()` or `known_ids()`.
5. **Retirement** happens only on evidence, one pair at a time, in an
   accepted, stored refresh:
   - **Supersession**: the same ID is accepted as fully verified,
     whatever its code. The accepted record is the authority, and the
     compare that read the pair already ran.
   - **Confirmed absence**: legacy confirms the ID absent
     (`absent_ids`).
   - **Horizon (T074 parity)**: the ID was not returned in this refresh
     and its `check_out_bound` is conclusively earlier than the
     `past_days` horizon under every timezone offset:
     `check_out_bound < utc_date(now − past_days) − 1`. Such a stay
     cannot re-enter the window while `past_days` does not grow.
     `past_days` has no maximum and changes without a reload
     (`__init__.py`, `_async_options_updated`), so the store
     persists `covered_past_days`: the window of the last accepted
     refresh whose step 1 save succeeded (decision 8). The guard adopts
     it only when that refresh's step 2 save also succeeds. While the
     current `past_days` exceeds it, the evidence is `revalidating`
     (decision 7). An accepted, stored refresh under the wider window
     re-observes every stay that window returns and records rejected
     ones again. This is the same evidence-based policy
     `_retired_bootstrap` applies to bootstrap codes, and the accepted
     `codes` map keeps no out-of-window entries at all.

     **Pre-existing limit, out of scope**: the live
     `_retired_bootstrap` (feature 010 T074) drops bootstrap pairs for
     good under the current window. A later `past_days` increase cannot
     restore an old legacy-era mapping, because re-observing the stay
     in v3 only adds a fresh `codes` entry. 013 does not change that
     behavior and does not weaken it; it is reported for a follow-up
     issue.

   A pair with `check_out_bound = null` has no verified horizon. It is
   retired only by supersession or confirmed absence. No arbitrary
   duration is used (FR-008c).

   **Residual risk of retirement**: once a pair is retired on the
   horizon, a later reuse of its code by another ID is no longer
   detected against it. That is the same exposure the accepted `codes`
   map has today, because it keeps nothing outside the window.
6. **Bound and growth**: `MAX_CONFLICT_ONLY_EVIDENCE = 5000` pairs, the
   same order as `MAX_BASELINE_CODES`. Nothing is ever evicted. If a new
   pair does not fit, the store becomes **saturated**, a sticky,
   persisted flag.

   Growth comes only from distinct, coded, non-hold rejected records.
   Dated pairs retire on the horizon, so steady-state occupancy is
   about the rejected records in one window plus the undated ones.
   Undated pairs need a record with neither verified segment dates nor
   an offset-aware top-level checkout. They accumulate only while
   Guesty keeps serving such records, which is the very condition the
   warnings report.

   Early warning:
   - Diagnostics expose `count`, `bound` and a `dated` / `undated`
     split.
   - At 80% of the bound (4000 pairs), one WARNING is logged and the
     non-fixable, warning-severity repair issue
     `conflict_evidence_near_capacity_{entry_id}` is raised. It has no
     values.
   - The issue is deleted when occupancy falls below 70% (hysteresis),
     so the operator can act before saturation.
   - Saturation itself remains fail-closed.
7. **Fail-closed states**:
   - `unloaded` while not loaded;
   - `revalidating` while any of these holds:
     - upgrade revalidation is pending (R9);
     - no accepted refresh has completed both its step 1 and step 2
       saves since this load (decision 8, restart);
     - the current `past_days` exceeds the adopted
       `covered_past_days` (decision 5, window expansion).

     Every cause is cleared only when a successful step 2 follows a
     successful step 1, synchronously with the adoption of
     `_baseline` and the `on_baseline_stored` callback (R8). A write
     issued while step 2 is awaiting storage still sees
     `revalidating`, so it can never use the previous current-ID set
     for an ID this refresh rejected;
   - `unstored` after a failed save;
   - `saturated`;
   - `compromised` when the store is corrupt or unreadable, or when its
     persisted `generation` is lower than the identity baseline's
     `conflict_evidence_generation`. This includes a missing store when
     the expected generation is above zero (decision 8).

   In every one of these states:
   - `available()` is false;
   - `classify_write_refusal` refuses every reservation write (R18).

   Only `saturated` and `compromised` raise the non-fixable repair issue
   `conflict_evidence_unavailable_{entry_id}`, with no values, because
   only they need operator action. They clear only when the entry is
   removed and re-added, which deletes both stores and the issue,
   because evidence that was never stored cannot be proven absent
   (FR-008c, US2-S18).

   `unstored` logs one WARNING on entry and clears on the next
   successful save. `unloaded` and `revalidating` are expected,
   transient startup states and log at DEBUG.

   **Loading**: `ReservationIdentityGuard.async_load` loads the identity baseline
   and then the evidence store, in the same call the coordinator
   already awaits before its first comparison (`_check_identity`). No
   comparison therefore runs in `unloaded`.

   **Precedence**: when several conditions hold, the reported state is
   the first of `compromised`, `saturated`, `unstored`, `revalidating`,
   `ready`. `compromised` and `saturated` are sticky: neither an
   accepted refresh nor the end of revalidation leaves them.

   **Refreshes while not ready**: the current refresh's own rejected
   pairs always reach `compare`, and every loaded stored pair and every
   pending (unsaved) pair does too.
   A corrupt or malformed store loads no pairs. A store that parses
   cleanly but lags in generation still feeds its loaded pairs to
   `compare`. A `saturated` store cannot record new ones. In each case
   a conflict could be missed. The
   refresh still publishes its independently verified records, which
   pre-013 behavior already did without any conflict-only evidence.
   While `compromised` or `saturated`, identity state is frozen so
   that missed evidence never becomes confirmation. `unstored` and
   `revalidating` refresh normally, because an accepted refresh is how
   they end:
   - continuity is never advanced to confirmed, and the bootstrap is
     never retired;
   - `revalidation_pending` is never cleared;
   - writes stay refused (R18).

   **No writes while compromised**: the evidence store is never written
   while `compromised`. A corrupt file stays corrupt and a lagging
   generation stays behind the baseline's (which keeps advancing), so a
   restart loads `compromised` again. Overwriting it would let lost
   evidence look absent.

   Only removing and re-adding the entry leaves these states.
8. **Crash-consistent save order**: an in-memory `unstored` flag does
   not survive a restart, and conflict-only evidence accumulates across
   refreshes, so a pair that is never saved might never be seen again.
   Two rules follow:
   - A restart may only ever find an evidence store that holds *at
     least* what the identity baseline expects. Additions are saved
     before the identity baseline, and retirements after it.
   - Pairs and bounds that were not saved are never dropped. They stay
     in an in-memory pending set, merged into every later save attempt,
     and are cleared only when an evidence save that contains them
     succeeds. While the set is not empty, the state is `unstored`.

   The steps of an accepted refresh are:

   1. **Evidence additions**: merge the pending set and this refresh's
      new pairs, and save the evidence store with `generation = N + 1`
      and no retirements. If this save fails, the evidence is
      `unstored` and the pairs stay pending.
   2. **Identity save**: always save the identity baseline with
      `conflict_evidence_generation = N + 1`. Clear
      `revalidation_pending` only when step 1 succeeded. If this save
      fails, the identity guard is `_unstored` (existing behavior) and
      step 3 is skipped.
   3. **Evidence retirements**: only after steps 1 and 2 succeeded,
      apply the retirements and save again with the same generation.
      If this save fails, the state is `unstored` until the next
      successful save. The persisted store is then a superset of what
      is needed, which is safe.

   After a restart:
   - step 1 failed and step 2 succeeded: the store's generation `N`
     is below the expected `N + 1`, so it loads as `compromised`.
     Evidence that was never stored cannot be proven absent (FR-008c,
     US2-S18);
   - step 1 succeeded and step 2 failed: the store is ahead of the
     baseline. It is accepted, because it holds a superset;
   - both failed: nothing changed on disk, so the generations still
     match and the pending pairs are gone. A durable marker cannot help,
     because it would be written to the same failing storage. Instead,
     after **every** load the evidence is `revalidating` (decision 7)
     until the first accepted refresh completes both its step 1 and
     step 2 saves. That
     refresh re-observes and records again every rejected pair Guesty
     still returns, so no write is permitted on the reloaded evidence
     alone. A lost pair whose record Guesty no longer returns cannot be
     rebuilt by any design. That is the same gap as Home Assistant
     being stopped while the record existed, and the old baseline never
     authorized a write during the failure, because the guard was
     `_unstored`.

   A store generation **above** the expected one is accepted. Besides
   step 2 failing, it happens after a rollback, when the older release
   rewrote the identity payload without the key.

   Step 1 runs on **every** accepted refresh, even with no new or
   pending pairs, because the generation it writes must match the one
   step 2 records. Only step 3 is skipped when there is nothing to
   retire. "Step 1 succeeded" means its save completed. While the store
   is `compromised` no step writes it (decision 7), so step 1 never
   succeeds and `revalidation_pending` is never cleared.

- A separate `Store` survives a rollback to a pre-013 release, which
  never reads or writes it. A rollback therefore cannot silently erase
  conflict evidence, whereas a key inside the identity payload would be
  dropped by the older release's next save.
- Supersession keeps feature 014's rule 8 ("any conflict-only evidence
  for the target refuses") from refusing a corrected reservation
  forever (US2-S17, US2-S21).
- Horizon retirement keeps the store bounded in steady state without
  an arbitrary duration.

**Alternatives considered**:

- **Bounded eviction by age or LRU.** Rejected: an eviction can make a
  conflict appear absent (FR-008c).
- **Retire undated pairs when they are no longer returned.** Rejected:
  the v3 search's server-side date filter is not verified evidence for
  a record whose dates failed verification.
- **Refuse only "affected" writes on saturation.** Rejected: the
  affected IDs are exactly the ones whose evidence was not stored, so
  they are unknowable.
- **Persist inside the identity store at version 2.** Rejected because
  of the rollback behavior above.

## R8 — Fallback interaction

**Findings**:

- `ReservationFallbackSource.resolve` treats every identifier not in
  `retrieved` as missing, so it is eligible for a legacy lookup and its
  `_retained` values can be republished.
- A record that v3 returned but 013 rejects would therefore be looked
  up through legacy and possibly served and routed. That would bypass
  FR-008 and FR-012b.

**Decisions**:

- `resolve` gains `rejected: Collection[str]`, which is the whole of
  `rejected_ids`: fully rejected **and** partially valid records.
  - `_absorb` skips those IDs. Instead it purges them from `_served`,
    `_retained` and `_last_attempt`, and discards them from
    `_confirmed_absent` and `_unreported_absent`, because v3 returned
    them and they are present, not absent.
  - They count as present in this refresh, so they are not eligible.
  - The coordinator drops their candidate hints in
    `async_record_refresh` (new `rejected_ids` argument, alongside but
    distinct from `absent_ids`), so they are not eligible later either.
  - A partially valid record's verified segments are published from
    this refresh's v3 result only. Nothing is retained for
    republication, because a reservation that is not fully verified
    may not leave fallback evidence or caches behind (FR-005a). If v3
    stops returning it, nothing about it is published until a fully
    verified record arrives. That fails closed.
- **Fallback state after a failed refresh**: today `resolve()`
  mutates its caches during the fetch, and the coordinator assigns
  `fallback_served_ids`, `fallback_sourced_ids` and
  `last_fallback_counters` before `_check_identity`
  (`coordinator.py`, `_async_update_data`). A failure after that point
  leaves routing from a refresh that was never stored. Under 013:
  - the coordinator computes the candidate served and sourced sets
    (including the sticky retired-generation branch) where it does
    today and passes the candidate sourced set to `_check_identity`;
  - `async_record_refresh` takes a synchronous `on_baseline_stored`
    callback. It calls it right after the step 2 identity save
    succeeds and `_baseline` is adopted (R7 decision 8), with no await
    in between and before the step 3 retirement save. The coordinator's
    callback assigns `fallback_served_ids`, `fallback_sourced_ids` and
    014's `AcceptedRefreshSnapshot`. 013's `VerifiedSnapshot` is not
    storage-gated (R10). The routing sets therefore always match the
    baseline that authorizes writes, including while step 3 is
    suspended. Any failure before that save (an
    identity abort, `_resolve_guests`, projection, cancellation) and a
    failed save keep the routing sets of the last refresh whose
    baseline was stored; a failed save also leaves `_unstored`
    refusing every write. A failure after step 2 (step 3,
    `acknowledge_absent_reservations`, snapshot build) cannot leave
    routing behind the stored baseline;
  - an identity abort additionally calls `mark_ambiguous()` first, so
    `classify_write_refusal` refuses every write at its ambiguity step
    until a successful accepted save clears `_ambiguous`;
  - the fallback caches keep their live in-place updates and are not
    staged. Rolling them back would be harmful: `_last_attempt` must
    advance to keep the feature 008 lookup limits (FR-053, SC-017)
    when failures repeat, and purges, retirement (`_disable()`) and the
    014 session sets only remove routing or add provenance. Additions
    reach routing only through the sets above, which the next stored
    refresh assigns after its own identity check;
  - a failed refresh publishes from the `VerifiedSnapshot` (R10), never
    from the fallback caches;
  - `last_fallback_counters` keeps its live assignment, because the
    counters of an aborted refresh are published on purpose (the
    existing comment at the abort handler).

  Tests assert that after an identity abort, and after a non-identity
  failure following `_check_identity`, the routing sets match the
  stored baseline, that a write issued while the step 3 save is
  suspended uses the new routing, every write in the abort case is refused
  with no request, and repeated failures add no lookups beyond the
  feature 008 limits.
- **Per-record staleness (FR-012g, FR-012h)**: the fallback tracks
  `_stale: set[str]`.
  - An ID is added when a due lookup fails (`_FAILED`) and the record is
    still published from `_served` or `_retained`.
  - An ID is removed when a lookup succeeds (`_OK`), when v3 returns it
    accepted (`_absorb`), and on retirement or purge.
  - A deferred lookup changes nothing, and a stale ID stays stale until
    it is reverified.
  - `FallbackOutcome.stale` and `ReservationFetchResult.stale_ids` carry
    the set to the coordinator.
  - Routing is unchanged: a served target stays routable under the
    existing guards, and a retained-only target stays unroutable.
- Legacy by-ID documents are validated with the same status allowlist
  and explicit-offset instant rule (R2, R5). Two outcomes are kept
  apart, because only one of them is a failed lookup:
  - `_FAILED` (unchanged): transport, HTTP or non-JSON failures, or a
    document that is not a dict or whose `_id` is not the requested ID.
    Nothing was proven about the record, so it keeps its last known
    values and is marked stale.
  - `_INVALID` (new): a dict whose `_id` is the requested ID, but which
    is rejected. The source answered and proved the record not
    verifiable, so stale display would retain data shown bad (FR-012b,
    FR-012g). The ID is purged from `_served`, `_retained`,
    `_last_attempt` and `_stale`, and is never confirmed absent. When
    the record has a non-hold string code, the `RejectedCode` and
    its retirement bound go to `FallbackOutcome.rejected_codes` and
    `rejected_check_out_bounds`. `V3ReservationSource.fetch`, the only
    caller of the fallback, merges them into the fetch result's
    `rejected_codes` and `rejected_check_out_bounds`, the same way it
    copies the fallback's other outputs, so they reach `compare` and the store
    (R7), marked with fallback origin (R7 decision 4). The ID's
    candidate hint is popped. The IDs travel as
    `FallbackOutcome.invalid` and
    `ReservationFetchResult.fallback_invalid_ids` to
    `async_record_refresh`, which pops their hints.

  `_INVALID` IDs are added to `FallbackOutcome.observed`, exactly as
  a successful lookup is today. An `_INVALID` verdict is a successful
  `_lookup` response. The live `_note_session("observed", id)` call
  runs only after `_parse` succeeds, so the `_INVALID` branch must call
  it explicitly before returning (the bounded, overflow-aware path).
  The ID's provenance then persists for the session, so 014 refuses a
  later write as `fallback_provenance` even when the record has no
  code.
  They give no hint and no publication.

  `_INVALID` IDs are **not** added to `ReservationFetchResult
  .rejected_ids`, which stays a statement about the records the v3
  search returned (R6). The purged ID is no longer a current identity,
  so a write to it goes through the unchanged membership path. Feature
  014 then refuses it without a read: the ID is in its legacy-observed
  set (`fallback_provenance`), and a coded ID also has conflict-only
  evidence. The warning is privacy-safe.

## R9 — Upgrade revalidation (FR-012d, FR-012e)

**Finding**: The only persisted reservation data is the identity
`Store` (version 1). It holds code maps, candidate hints, continuity and
flags. Entities are not `RestoreEntity`, and coordinator data, fallback
`_retained` and `_served` are memory-only, so a restart has no
reservation snapshot to revalidate. Home Assistant `Store` raises
`UnsupportedStorageVersionError` when the stored **major** version is
newer than the code's. It loads a newer **minor** version through
`_async_migrate_func`, falling back to the stored data on
`NotImplementedError`.

**Decisions**:

- Bump the identity store to **minor** version 2 (major stays 1), so a
  rollback still loads it.
- A subclass `_IdentityStore._async_migrate_func(1, 1, data)` returns
  `data` with `revalidation_pending: true`.
- On load, every stored structure is revalidated before use:
  - code maps keep only `str → str` entries with non-empty values;
  - when the payload is migrated from minor version 1 (or
    `revalidation_pending` is already set), **every** `known` candidate
    hint is downgraded to an unknown-timing hint. A shape check cannot
    tell a verified interval from one built by the removed written-date
    fallback (R4) or from a naive legacy timestamp read as UTC. An
    unknown-timing hint is not fallback-eligible and contributes no
    `check_outs`, so `_bounded` keeps it last to evict and
    `_retired_bootstrap` retains its bootstrap pair (fail closed). Only a
    refresh under 013 rules re-derives a `known` interval;
  - otherwise (a minor-version-2 payload with the flag clear), a
    `known` hint keeps its interval only when both instants parse as
    offset-aware and `check_in < check_out`, and is downgraded
    otherwise;
  - an unparsable `observed_at` drops the hint.
- While `revalidation_pending`:
  - `classify_write_refusal` returns a new `REFUSAL_REVALIDATING`;
  - `conflict_evidence.available()` is false.
- The first accepted, stored refresh under 013 rules clears the flag.
  That refresh replaces `codes` and `fallback_codes` wholesale with
  re-verified evidence, drops hints for `rejected_ids`, and records
  conflict-only mappings.
- `legacy_codes`, `bootstrap_codes`, `continuity` and `divergent` are
  kept after structural validation. They are identity mappings already
  under the existing safeguards (FR-012e). Dropping them would weaken
  conflict detection.
- Nothing dropped is treated as absent, and no continuity is
  established by revalidation.
- The identity payload records `conflict_evidence_generation` (an
  integer, default 0), which is advanced by the save order in R7
  decision 8. It detects a missing or lagging evidence store.

**Rationale**: The integration's first refresh after setup re-derives
current identity under the new rules. The pending flag ensures nothing
accepted under the old fail-open rules authorizes a write in between
(US2-S19, US2-S20). A rollback to a pre-013 release reads minor version
2 (unknown keys ignored) and writes minor version 1. A re-upgrade
migrates again and revalidates again.

## R10 — Stale display after a failed refresh (FR-012a–012c)

**Findings**:

- On `UpdateFailed` Home Assistant keeps `coordinator.data` and sets
  `last_update_success = False`. `GuestyReservationSensor.available`
  and `GuestyFinancialSensor.available` already return false through
  `CoordinatorEntity.available`, which FR-012a no longer permits while
  eligible last-verified data exists (R13).
- There is no stale marker, and projections in `data` were computed at
  the last success, so a stay can still read `confirmed` after its
  checkout.

**Decisions**:

- After every accepted refresh, the coordinator keeps a
  `VerifiedSnapshot`: the accepted, fully or partially verified
  records before publication filtering, plus the stale IDs from the
  fallback. It replaces the previous snapshot wholesale, which removes
  anything that refresh showed invalid (FR-012b). It is stored once the
  refresh publishes, even when an identity save fails, so an older
  snapshot can never revive a record a later refresh rejected.
- On a failed refresh (any exception leaving `_async_update_data`,
  including identity ambiguity and an `asyncio.CancelledError` that
  Home Assistant treats as a failure), if a snapshot exists, the
  coordinator:
  - re-runs the same pure publication pipeline (R4 window, expiry and
    candidacy; current options, listings and statuses) with the current
    instant;
  - sets `data` to that projection and sets `snapshot_stale = True`
    in the `_async_update_data` failure path, **before** re-raising.
    Every failure Home Assistant's `_async_refresh` handles comes from
    that await, and on a first failure it notifies listeners
    immediately because `last_update_success` changed. Setting the
    stale state afterwards would let entities write an intermediate
    `unavailable` state;
  - calls `async_update_listeners()` from its `_async_refresh` override
    after a failure that followed an already failed refresh, because
    Home Assistant then returns without notifying listeners, so the
    re-projection would otherwise never be written.

  If the re-projection itself raises, the coordinator sets `data` to
  `None` and clears `snapshot_stale` before re-raising the original
  failure, so the entities become unavailable instead of showing an
  outdated projection. With no snapshot, nothing is invented (US2-S8)
  and the reservation entities are unavailable.
- An accepted refresh clears `snapshot_stale` (FR-012c). A refresh
  *attempt* does not.
- Staleness never feeds a guard: write predicates read only identity
  state, the `_write_miss_outcomes` witnesses and the live fallback
  sets. `guard_reservation_write` also reads `coordinator.data`, which
  may hold the stale re-projection, but only to choose between the
  Present and Absent error types on a membership miss; both paths still
  need a new accepted recovery refresh before any write. A failed
  refresh already leaves `last_update_success` false and the identity
  state unchanged. The entity `available` override in R13
  never changes `last_update_success`, which the write guards keep
  reading.

## R11 — Writes for unpublished but valid targets

**Finding**: `codes` comes from every accepted record before listing
and window filtering (`_check_identity` runs before `_within_window` and
`_attribute_segments`). That is the #293 behavior FR-008 preserves.

**Decision**: Unchanged for fully verified records. Rejected and
partially valid records are kept out of `codes` and `fallback_codes`
by passing only fully verified records to `observe_codes` for
recording. A write to such a target meets the existing
`missing_current_code` refusal. Feature 012's single recovery refresh
then re-evaluates it, so a corrected record becomes writable through
existing guards (US2-S21). Feature 014's by-ID path refuses it through
`rejected_ids` and conflict-only evidence.

## R12 — Single-record validator (feature 014 interface 3)

**Decision**: `validate_record(raw: Mapping[str, object], action_type:
str) -> RecordValidation` in `api/validation.py`. It is pure and makes
no logs or requests.

- It runs `assess_v3_record` on one raw v3 record. A record is valid
  only when it is **fully verified**: identifier string, recognized
  status, and every raw segment verified (FR-008d).
- It then applies the action fact table:

  | `action_type` | Additional facts |
  | --- | --- |
  | `add_reservation_note` | none beyond full verification |
  | `set_reservation_status` | none beyond full verification |
  | `set_custom_field` | none beyond full verification |
  | anything else | rejected as `unsupported_action` |

- The result is `RecordValidation(valid: bool, reason:
  ValidationReason | None)`.
  `reason` is a fixed category from `ValidationReason` and is never a
  value. A hold status is valid for validation; holds are handled by
  feature 014's own qualification.
- A non-string `confirmationCode` is neither a rejection nor a
  code. It stays on the model exactly as today (unchanged
  pass-through), so feature 014's snapshot still classifies it as
  `code_kind=invalid` and never as code-less. It gives no conflict-only
  pair, and publication shows no code for it.
- Unlike the batch path, the by-ID identifier has not passed
  `_validate_identifiers`, so `validate_record` itself requires
  `reservationId` to be a non-empty string.

**Rationale**: Feature 014's contract says the validator applies "the
accepted-refresh rules ... including FR-008d for each raw segment and
the facts the action needs". Notes shape and code presence are checked
by feature 014 itself.

## R13 — Stale reservation entities stay available

**Finding**: Home Assistant 2026.10.4 (the locked environment)
`Entity.__async_calculate_state` adds `state_attributes` and
`extra_state_attributes` **only when the entity is available**, and
`CoordinatorEntity.available` returns `coordinator.last_update_success`.
Without an override, every reservation entity would turn unavailable
after an overall failed refresh and hide its stale data. The user chose
"show last verified data clearly marked stale" and rejected "show it as
unavailable until reverified", and FR-012a, US2-S6 and SC-010 now say
so. The behavior is long-standing; the implementation compatibility
gate re-checks it on the declared minimum 2026.4.0.

The live entities that publish reservation data are
`GuestyReservationSensor` and `GuestyFinancialSensor` in `sensor.py`,
both `CoordinatorEntity["ReservationsCoordinator"]`. The integration
registers only `Platform.SENSOR`; there is no calendar entity.

**Decision**:

- `ReservationsCoordinator` exposes `stale_display_available: bool`,
  true only while `last_update_success` is false, `snapshot_stale` is
  true and the re-projected `data` from a `VerifiedSnapshot` is set.
  Both are set before Home Assistant notifies listeners of the failure
  (R10), so an entity never writes an intermediate `unavailable` state.
- Both entities override `available` so the coordinator-health part
  reads `coordinator.last_update_success or
  coordinator.stale_display_available` instead of `super().available`.
  Their other existing checks are unchanged: `data` present, listing
  still tracked, and for the financial sensor a selected reservation
  with the specific money field.
- `last_update_success` itself is never changed, so every write guard
  that reads it (`actions.py` recovery witnesses and
  `accepted_refresh_snapshot`) still refuses on stale data.
- The reservation sensor's attributes carry top-level `stale: true`
  and `last_verified_at`, and item-level `stale` in `upcoming`. The
  financial sensor's attributes carry `stale` and `last_verified_at`
  beside `listing_id`. These values are written because the entity is
  available.
- `last_verified_at` is the time of the last accepted refresh (the
  snapshot's `accepted_at`, ISO 8601) while `snapshot_stale` is true,
  and `null` otherwise. It never certifies a record that was already
  per-record stale (FR-012g) in that snapshot; such a record keeps its
  own `stale` marker. Per-record staleness after a successful refresh
  sets `stale` only, with no timestamp.
- The state is the re-projection at the current instant (R4 expiry,
  window and checkout-day uncertainty), so an expired stay is not
  current and a date-only checkout day stays `unknown`. It is never
  described as reverified, occupancy is never presented as
  live-proven, and the stale marker is its observable qualifier.
- With no snapshot (for example, a failed first refresh), the entities
  stay unavailable; nothing is invented.
- Diagnostics also report the stale block (R15).
- Abusing `capability_attributes` or adding an extra entity is
  unnecessary and is not used.

## R14 — Privacy-safe, non-repeating reporting (FR-009–011)

**Decisions**:

- `ExclusionTracker` (coordinator-owned, memory-only) keys
  `(reason, scope_id)` with `scope_id` equal to a reservation or listing
  ID. Identifiers are permitted in logs and diagnostics today; guest
  data, codes and payload values are not.
- An accepted refresh reports its current exclusion set:
  - a new key logs one WARNING;
  - a known key logs at DEBUG;
  - a key missing from the accepted set is resolved and forgotten, so
    it can warn again (SC-004).
- The keys come from `ReservationFetchResult.exclusions`, which the
  v3 and legacy sources and the by-ID fallback fill with fixed reasons
  and reservation IDs only, so no rejected payload is retained. The
  coordinator adds `(listing_timezone_unverified, listing_id)` keys for
  tracked listings with reservations and no verified timezone, which
  the sources cannot see.
- Failed refreshes do not resolve keys. The conflict-evidence repair
  issues are not tied to refreshes: they follow the evidence state
  after load and on every state change, including failed refreshes,
  so a store whose pairs abort every refresh is still reported.
- The bound is `MAX_TRACKED_EXCLUSIONS = 256`. Over the bound, new keys
  log at DEBUG and one aggregate WARNING ("N further exclusions") is
  emitted per overflow episode. The episode ends when the tracked set
  falls below the bound, so churn cannot spam.
- **Repairs**: one non-fixable issue per tracked listing whose timezone
  is missing or unloadable while that listing has published or
  candidate reservations:
  `listing_timezone_unverified_{entry_id}_{listing_id}`.
  - Placeholders are the listing's title or nickname and its ID; there
    is no guest data.
  - It is deleted once the listing has a verified timezone or is no
    longer tracked (FR-009).
  - The conflict-evidence issue (R7) uses the same pattern.
- The log text never includes a status, code, guest field, note, custom
  field value or raw payload. Reasons are fixed `ValidationReason`
  values.

## R15 — Diagnostics

**Decision**: `reservations.validation` is added to diagnostics:

- `rejected_records`, `partial_records` and `rejected_segments` counts;
- `reasons`: counts by `ValidationReason`;
- `rejected_ids`: sorted, capped at 100;
- `conflict_evidence`: `state`, `available`, `count`, `dated`,
  `undated` and `bound` (no codes or IDs), plus the near-capacity flag;
- `stale`: `snapshot_stale`, `stale_count` and `last_verified_at`
  (ISO 8601 while `snapshot_stale` is true, otherwise `null`).

`refresh.status_counts` uses the sanitized keys (R2). Diagnostics never
contain confirmation codes, and never show which IDs have stored
conflict-only pairs. `rejected_ids` reports only this refresh's
rejections.

## R16 — Merge order with feature 014

Feature 014 T001–T048 merged in PR #325 (`3734710`) while this plan
was written. The live code on `main` now has:

- `api/models.py`: `ReservationFetchResult.returned_ids`,
  `rejected_ids` and `fallback_observed` (T011);
- `api/reservations_v3.py`: `_map_results` computes the interim
  `rejected_ids` as raw minus parsed, and `fetch` copies
  `fallback_observed` (T011);
- `api/reservations_fallback.py`: `_session_observed`,
  `_session_retired` and `_note_session`, with `_note_session
  ("observed", id)` called only after `_parse` succeeds (T013);
- `coordinator.py`: `AcceptedRefreshSnapshot` with
  `rejected_ids: frozenset[str]` (T017);
- `identity.py`: `check_identity_conflict` over the baseline maps;
- `actions.py`: the `ConflictEvidence` protocol, whose
  `validate_record(raw, action_type) -> str` returns `"valid"` or any
  other string naming a rejection, and `UnavailableConflictEvidence`;
- tests `test_rejected_ids_are_raw_minus_parsed`
  (`tests/api/test_reservations_v3.py`) and
  `test_malformed_listing_ids_mark_record_unverifiable`
  (`tests/test_coordinator.py`).

**Decision**:

- 013 reuses the existing fields with their names and types, and
  replaces only the interim `rejected_ids` computation in
  `_map_results`. It never edits 014's port or adapter in
  `actions.py`.
- 013 is now the second to merge. It updates
  `test_rejected_ids_are_raw_minus_parsed` with a documented reason
  (partial records are in `rejected_ids`).
  `test_malformed_listing_ids_mark_record_unverifiable` stays as is:
  it feeds models built directly to the coordinator, and
  `GuestyStaySegment.from_api_dict` and directly built models keep
  their behavior, so its defensive path still holds. 013 adds
  source-level rejection tests instead.
- The `_INVALID` branch in `_lookup` calls `_note_session("observed",
  id)` explicitly, because the live call sits after a successful
  `_parse` (R8).
- 014's `check_identity_conflict` reads only the baseline maps.
  Conflict-only evidence reaches 014 through the port's `codes_for` /
  `ids_for`, so 013 does not change `check_identity_conflict`.
- 013's `VerifiedSnapshot` (R10) is distinct from 014's
  `AcceptedRefreshSnapshot` and must not be merged into it.
- After 013 merges, 014 T049–T051 wire the adapter to the interfaces in
  the contract's "Interfaces provided to feature 014" section.
- 013's `REFUSAL_REVALIDATING` and `REFUSAL_EVIDENCE_UNAVAILABLE`
  fire before the membership check (R18). So once 013 is merged,
  unavailable or revalidating evidence gives a plain
  `GuestyActionError`: no 012 recovery refresh, no by-ID read, and no
  Present or Absent error. 014's own `evidence_unavailable`
  qualification step then stays as a defensive check only. The 014
  contract, plan, research, data-model, quickstart and tasks (T010,
  T011, T051, T052) now say so, and describe `rejected_ids` with this
  feature's definition instead of "raw minus parsed".

## R17 — Agent context

The plan adds no language, framework, dependency or storage technology:
it uses Python 3.14, Home Assistant `Store`, the repairs issue registry,
pytest and `uv`, all already in use. `.github/copilot-instructions.md`
is not changed.

## R18 — Where the new write refusals sit

**Finding**: `classify_write_refusal` checks, in order:

1. baseline missing or unusable;
2. `_unstored`;
3. continuity unconfirmed;
4. `_ambiguous`;
5. membership in `current_ids()` (`missing_current_code`).

`guard_reservation_write` turns only the membership refusal into
`ReservationIdentityAbsentError` or `ReservationIdentityPresentError`.
Those errors start feature 012's recovery refresh and feature 014's
by-ID qualification. Every other refusal raises a plain
`GuestyActionError`.

**Decision**: Insert two refusals after step 4 and **before**
membership:

- `REFUSAL_EVIDENCE_UNAVAILABLE` while conflict-only evidence is not
  `ready` for any reason other than revalidation;
- `REFUSAL_REVALIDATING` while the reported state is `revalidating`.

The refusal follows the evidence state precedence (R7 decision 7):
`REFUSAL_EVIDENCE_UNAVAILABLE` whenever the state is `compromised`,
`saturated`, `unstored` or `unloaded`, and `REFUSAL_REVALIDATING` only
when the reported state is `revalidating`. A compromised store during a
pending revalidation therefore reports the repair issue, never an
endless "revalidating".

All existing refusals keep their order and messages. With this placement
a write refused because the evidence is unusable never triggers a
recovery refresh or a by-ID read. A test asserts that no refresh and no
request happen in each new state.

The refusal messages are fixed strings with no codes.
`REFUSAL_EVIDENCE_UNAVAILABLE` names the repair issue only when the
state is `saturated` or `compromised`.

**Rationale**: Recovery cannot cure unusable evidence: saturation and
compromise need operator action, and `unstored` needs the next
successful save, which the next scheduled refresh attempts.
Revalidation is the one exception that a refresh *can* clear: the
first refresh after setup does that within seconds, and after a
`past_days` increase the next scheduled refresh does.
Refusing without starting a recovery is the simplest fail-closed
behavior. It keeps 012's one-refresh budget for true identity misses.

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Verifiable Reservation Data

**Consumers**:

- reservation sensors and their `upcoming` attributes;
- reservation-derived binary sensors and calendar consumers, where they
  exist;
- diagnostics;
- the repairs dashboard;
- the existing reservation write actions;
- feature 014's by-ID write fallback through the interfaces below.

**Scope**: This contract covers validation, publication, staleness,
conflict-only identity evidence and privacy-safe reporting for reservation
data from the v3 search, the legacy source and the legacy by-ID fallback.
It adds no Guesty requests, configuration options, entities or actions.

## Record handling

- **C1**: A record whose identifier passed batch validation is assessed under
  [data-model.md](../data-model.md) `ValidationReason`.
- **C2**: A **rejected** record:
  - is not published;
  - creates no current identity and no fallback candidate hint;
  - is not eligible for a legacy lookup;
  - is never treated as absent.
- **C3**: A **partially valid** record:
  - publishes only its verified segments;
  - uses raw-stay eligibility and raw-adjacency handoffs;
  - is listed in `rejected_ids`;
  - is not a current writable identity.
- **C4**: A record without a batch-verified identifier, or any batch-integrity
  failure, fails the whole refresh. The previous verified data is
  handled as stale (C10).
- **C5**: Unrecognized, missing or non-string statuses reject the record. The
  value is never logged, published or used as a diagnostics key.
- **C6**: Date-level data comes only from canonical localized dates.
  Top-level localized dates may fill a missing segment date only under
  the unchanged feature 010 predicate:
  - exactly one raw segment, which is a dict with a listing;
  - no segment `checkIn` or `checkOut`;
  - top-level `checkIn` and `checkOut` that parse, with check-out after
    check-in.

  A timestamp's written date is never a local date. A partially valid
  record has no reservation-level span; window and projection use each
  verified segment.
- **C7**: Exact instants require a verified listing timezone (explicit and
  loadable) and an unambiguous local wall time.

## Publication

- **C8**: Expiry runs before candidate selection and before the ten-item
  `upcoming` limit:
  - A conclusively ended temporal stay is `checked_out` and is never
    current or upcoming.
  - A verified-timezone, date-only checkout on the local checkout day
    is an uncertain candidate: status `unknown` with
    `reservation_status`, and no occupancy claim.
  - Without a verified timezone, a stay is expired only when its
    checkout precedes every possible local date. Otherwise it is
    uncertain.
- **C9**: Without a verified timezone, a date-only stay is in the configured
  window only when membership holds for every possible local date.
- **C10**: After an overall failed refresh:
  - the last accepted verified data is re-evaluated at the current
    instant against the current options;
  - it is marked `stale`;
  - writes stay governed only by identity guards;
  - the fallback routing sets always match the stored identity
    baseline, and an identity abort leaves the guard ambiguous, refusing every
    write until an accepted refresh is stored.

  The reservation sensor is unavailable. Its computed attributes carry
  `stale: true`, but Home Assistant does not write attributes for
  unavailable entities, so diagnostics `validation.stale` is the
  observable surface (research R13).
- **C11**: An accepted refresh clears `stale` and removes everything it shows
  invalid.
- **C12**: A fallback-published record whose latest lookup failed has item and
  top-level `stale: true` while the sensor stays available. Routing is
  unchanged. A lookup that answers with the requested record but proves
  it invalid is not a failure: the record is removed at once, is never
  treated as absent, and its code becomes conflict-only evidence.

## Identity and writes

- **C13**: The rejected mappings of the current refresh, together with stored
  conflict-only evidence, reach every existing conflict check with its
  existing outcome:
  - a same-path conflict aborts the refresh as ambiguous;
  - a cross-path conflict is soft.

  These mappings never confirm continuity, never enter current IDs and
  never authorize a write.
- **C14**: Conflict-only evidence is persisted separately as
  `(reservation_id, code)` pairs (`RejectedCode`). A shared code never
  collapses: within one refresh it is a collision with the existing
  outcome, and across refreshes both pairs are kept. The store is
  bounded at 5000 pairs with no eviction. A pair is retired only on
  evidence:
  - the same ID is accepted fully verified;
  - legacy confirms the ID absent;
  - its retirement-only `check_out_bound` lies conclusively before
    the `past_days` horizon under every offset, and the ID was not
    returned in that refresh.

  At 80% occupancy a warning repair issue is raised. Saves follow a
  generation order, so an evidence store that lags its identity
  baseline after a restart loads as compromised.
- **C15**: Every reservation write is refused, with a non-fixable repair issue
  for the saturated and compromised states, while conflict-only
  evidence is in one of these states:
  - unavailable;
  - unstored;
  - saturated;
  - compromised;
  - revalidating.
- **C16**: Writes are refused as revalidating until an accepted refresh
  under these rules is stored: after an upgrade, after every restart,
  and after `past_days` grows beyond the window of the last stored
  refresh. The refusals in C15 and
  C16 come before the current-identity membership check, so they never
  start a feature 012 recovery refresh or a feature 014 by-ID read.
- **C17**: A valid target that is filtered from publication by listing, window
  or status remains writable under the existing guards.

## Reporting

- **C18**: A new exclusion key `(reason, reservation or listing ID)` logs one
  WARNING. Repeats log at DEBUG. A key that resolves and later recurs
  warns again. Overflow logs one aggregate warning.
- **C19**: A missing or invalid listing timezone raises one repair issue per
  listing. The issue is deleted when the timezone is verified or the
  listing is no longer tracked.
- **C20**: Logs, diagnostics and repair issues never contain:
  - guest data;
  - confirmation codes;
  - notes or custom-field values;
  - status strings outside the recognized vocabulary;
  - raw payloads.

## Interfaces provided to feature 014

These satisfy feature 014's "Required feature 013 interface". The names
are fixed. Feature 014 T049–T051 consume them in `actions.py`'s thin
`_conflict_evidence(coordinator)` adapter.

### 1. Rejected IDs

```python
# custom_components/guesty/api/models.py
@dataclass(frozen=True)
class ReservationFetchResult:
    ...
    rejected_ids: tuple[str, ...] = ()
```

- The field holds sorted, de-duplicated, batch-verified v3
  `reservationId` values that are record-rejected **or** partially
  valid (FR-008d).
- It excludes accepted holds, records that are valid but unpublished,
  and anything that failed batch validation (the refresh fails
  instead). A rejected or partially valid hold is included.
- The legacy source fills it under the same rules. Feature 014 never
  consults a non-v3 source.
- For v3, when both are present, `set(rejected_ids) <=
  set(returned_ids)`.
- A partially valid ID may also appear in `reservations` with only its
  verified segments. For writes, `rejected_ids` takes precedence.
- If feature 014 T011 already added the field, feature 013 keeps the
  name and type and replaces only its computation in
  `reservations_v3._map_results`.

### 2. Conflict-only evidence

```python
# custom_components/guesty/identity.py
class ConflictOnlyEvidence:
    def available(self) -> bool: ...
    def codes_for(self, reservation_id: str) -> frozenset[str]: ...
    def ids_for(self, code: str) -> frozenset[str]: ...


# reached as:
coordinator.identity.conflict_evidence  # ConflictOnlyEvidence
```

- `available()` is true only in the `ready` state. It is false while the
  evidence is:
  - unloaded;
  - revalidating;
  - unstored;
  - saturated (FR-008c bound exceeded);
  - compromised.
- `codes_for` and `ids_for` return persisted conflict-only mappings
  only. They never include accepted identity maps, which feature 014
  reads from the baseline and snapshot.
- Both lookups raise `RuntimeError` when `available()` is false.

### 3. Record validator

```python
# custom_components/guesty/api/validation.py
class ValidationReason(StrEnum): ...


@dataclass(frozen=True)
class RecordValidation:
    valid: bool
    reason: ValidationReason | None


def validate_record(
    raw: Mapping[str, object],
    action_type: str,
) -> RecordValidation: ...
```

- The validator is pure: no I/O, no logging and no Home Assistant
  imports.
- It uses the same assessment as the refresh path.
- `valid` requires all of the following:
  - a non-empty string `reservationId`;
  - a recognized status;
  - every raw segment verified (FR-008d);
  - `action_type` in `add_reservation_note`, `set_reservation_status`
    or `set_custom_field`.
- Holds are not rejected here; feature 014 refuses them itself.
- `reason` is a fixed category and never echoes payload values.

### Adapter composition (for feature 014 T051)

```python
class _Evidence013:
    def __init__(self, coordinator) -> None:
        self._evidence = coordinator.identity.conflict_evidence

    def available(self) -> bool:
        return self._evidence.available()

    def codes_for(self, reservation_id: str) -> frozenset[str]:
        return self._evidence.codes_for(reservation_id)

    def ids_for(self, code: str) -> frozenset[str]:
        return self._evidence.ids_for(code)

    def validate_record(self, raw, action_type) -> str:
        result = validate_record(raw, action_type)
        return "valid" if result.valid else str(result.reason)
```

013's `validate_record` returns a `RecordValidation`. The live 014 port
(`ConflictEvidence.validate_record(...) -> str`) expects `"valid"` or
any other string, and refuses anything else as `malformed`. The adapter
converts the result. No `ValidationReason` value is `"valid"`, and an
adapter test asserts the returned string for a valid and a rejected
record. The interfaces need no 014 change.
014's text on behavior while evidence is unavailable does need one
(research R16): the C15 and C16 refusals come first, so no 012 recovery
refresh or by-ID read happens.

## Required contract tests

- **Validator** (`tests/api/test_validation.py`):
  - every `ValidationReason`;
  - fully valid, partial and rejected records;
  - zero-night rejection;
  - a malformed parent;
  - no written-date fallback;
  - invalid optional fields become `None`;
  - the action fact table;
  - a `caplog` sentinel never appears.
- **Batch** (`tests/api/test_reservations_v3.py`):
  - isolated rejection versus a batch failure;
  - `rejected_ids` contents, ordering and exclusions;
  - sanitized `status_counts`;
  - `rejected_codes` pairs only for non-hold rejected or partial
    records with string codes;
  - a code shared by rejected–rejected or rejected–accepted records is a
    within-refresh collision.
- **Legacy and fallback**:
  - status allowlist;
  - a naive instant is rejected;
  - a missing `_id` fails the refresh;
  - rejected IDs are not eligible, purged and not absent;
  - per-record stale transitions.
- **Identity** (`tests/test_identity.py`):
  - rejected mappings reproduce the existing collision and conflict
    outcomes;
  - they never add matches;
  - persistence, retirement (supersession, absence, horizon; no
    retirement of undated pairs), bound and saturation;
  - several IDs for one stored code are all checked;
  - compromised detection;
  - save order: a failed first evidence save loads `compromised` after
    a restart; pairs from a failed identity save stay pending and
    reach the next successful save; state precedence and the identity
    freeze while `compromised` or `saturated`; a compromised store is
    never written, so it is still `compromised` after an accepted
    refresh and a restart; a re-observed pair's bound only moves later
    or to `null`; a refresh with no evidence changes still loads
    `revalidating`, not `compromised`, after a restart, and becomes
    `ready` after the next accepted refresh's step 1 save; pending
    pairs reach `compare`; an `_INVALID`
    fallback pair reaches `compare` and the store; an `_INVALID` pair
    whose code is in `legacy_codes` or `codes` with another ID aborts
    the refresh as ambiguous, both in the same refresh and after it is
    stored; a code-less `_INVALID` lookup is in the fallback's
    `legacy_observed` set; the bootstrap sweep's collision scan includes rejected
    legacy pairs (`BOOTSTRAP_AMBIGUOUS`) and records them; a
    compromised store during pending revalidation refuses with
    `REFUSAL_EVIDENCE_UNAVAILABLE`;
  - `RuntimeError` on unavailable lookups;
  - minor-version migration, revalidation and the pending refusal;
  - a migrated minor-version-1 payload downgrades every `known`
    candidate hint to unknown timing: not fallback-eligible, no
    `check_outs`, bootstrap pair retained;
  - every load is `revalidating` until an accepted refresh completes
    its step 1 save, including after both saves failed and a restart;
  - growing `past_days` beyond `covered_past_days` refuses writes as
    revalidating until a refresh under the wider window is stored,
    which records a re-entered rejected pair again;
  - rollback-compatible payload.
- **Coordinator and sensor**:
  - partial-stay projection;
  - expiry for single-raw-segment v3, multi-segment v3 and legacy
    projections;
  - expiry before the limit (SC-003 with eleven expired plus one
    future);
  - unverified-timezone window and expiry bound;
  - checkout-day uncertainty;
  - stale re-projection after a failed refresh, including an expired
    stale stay;
  - stale clearing;
  - after an identity abort, a failure after `_check_identity`, a
    failed identity save, and a non-`OSError` failure after the step 2
    save, the routing sets match the stored baseline; in the abort
    case every write is refused with no request; a write issued while
    the step 3 save is suspended uses the new routing; repeated
    failures add no lookups beyond the feature 008 limits;
  - per-record stale attributes.
- **Reporting**:
  - warning once, then DEBUG, then a warning on recurrence;
  - overflow aggregate;
  - timezone repair create and delete;
  - conflict-evidence repair;
  - diagnostics block shape and redaction.
- **Writes**:
  - a rejected or partial target is refused;
  - a corrected target becomes writable after a recovery refresh;
  - every write is refused while evidence is unavailable or
    revalidating, with no recovery refresh and no request;
  - a filtered but valid target stays writable.

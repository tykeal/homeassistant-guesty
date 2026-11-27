<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: By-ID Reservation Write Fallback

## Scope

This feature adds transient, in-memory state only. It does not change:

- the feature 008 persisted `IdentityBaseline`, Store key, or version;
- the reservation schema, public service fields, options, or entities;
- feature 012's attempt and outcome records, beyond reading them.

Nothing described here is persisted, logged with values, or exposed in
diagnostics.

## Existing entities (read, never modified on this path)

### Identity baseline

`ReservationIdentityGuard.baseline` (`identity.py`, `IdentityBaseline`)
has these fields:

- `path`
- `continuity`
- `codes`, bounded to `MAX_BASELINE_CODES`
- `legacy_codes`
- `bootstrap_codes`
- `fallback_codes`
- `candidates`

On this path the baseline is used only for conflict lookups, and
`classify_write_refusal` remains the gate that produces the missing-code
refusal. The baseline is not modified, and nothing is saved to the
`Store`.

### Coordinator publication and provenance

On `ReservationsCoordinator`, these attributes stay authoritative for
publication and routing:

- `data`;
- `fallback_served_ids` and `fallback_sourced_ids`;
- `reservations_path`.

### Feature 012 refresh outcome

`_write_miss_outcomes` entries have the shape (generation, attempt,
success, options, settings, entry, client), as recorded by `_async_refresh`.
The latest successful entry's `attempt` identifies the accepted refresh
that a snapshot must belong to.

### Confirmed-absent evidence

The confirmed-absent set,
`GuestyApiClient.confirmed_absent_reservations` (FR-052), is the
fallback's current, pruned retirement set. It is not a session history,
and it is not durable. The snapshot's `fallback_absent` covers the
accepted refresh itself, and the new session legacy-retired set (below)
covers retirements from earlier refreshes in this session. The spec
accepts that retirement evidence lost to a reload is unavailable.

## New or extended entities

### `ReservationFetchResult.rejected_ids` (extended model)

`rejected_ids: tuple[str, ...] = ()` lists the raw v3 `reservationId`
strings that the source returned but did not accept:

- **Before 013**: raw IDs minus parsed IDs, collected in
  `reservations_v3._map_results`.
- **After 013**: whatever the 013 refresh rejects, including FR-008d
  raw-segment rejections.

The field holds identifiers only and is used only to build the snapshot.

### `ReservationFetchResult.returned_ids` and `fallback_observed`

- `returned_ids: tuple[str, ...] = ()`: every string `reservationId` on
  the raw v3 search pages, accepted or rejected, set in
  `_map_results`. The existing `observed_ids` holds parsed IDs only and
  is merged with legacy lookup IDs, so it is not a substitute.
- `fallback_observed: tuple[str, ...] = ()`: `FallbackOutcome.observed`
  for the refresh, carried through the `replace` in
  `reservations_v3.py`.

### Session legacy-observed set

`ReservationFallbackSource` keeps an in-memory set of every ID a
successful legacy `_lookup` returned, whatever its status, for the life
of the client. It is exposed read-only as
`GuestyApiClient.legacy_observed_reservations`. It is bounded; when the
bound would be exceeded it is marked incomplete, and an incomplete set
makes every by-ID attempt ineligible (`evidence_unavailable`). It holds
identifiers only, is never persisted, and is dropped on unload.

### Session legacy-retired set

`ReservationFallbackSource` also keeps a bounded, in-memory, never-pruned
set of every ID `_retire` confirmed absent (including through
`retire_candidate`), exposed read-only as
`GuestyApiClient.legacy_retired_reservations`. It has the same overflow
rule: an incomplete set makes every by-ID attempt ineligible
(`evidence_unavailable`). It holds identifiers only and is never
persisted.

### Accepted-refresh snapshot

The snapshot (`AcceptedRefreshSnapshot`) is held on
`ReservationsCoordinator` and replaced wholesale when an accepted
refresh's identity record succeeds. It is cleared on unload. It holds:

- `attempt`: the `_write_miss_outcomes` attempt it was built from.
- `path`: the reservation source path of that refresh.
- `returned_ids`: copied from `ReservationFetchResult.returned_ids`. It
  is never built from the merged `observed_ids`.
- `rejected_ids`: copied from `ReservationFetchResult.rejected_ids`.
- `records`: one entry per accepted record, holding:
  - `code_kind`: `none`, `empty`, `string`, or `invalid`;
  - `hold`: whether the status is in `HOLD_STATUSES`;
  - `listing_ids`: a frozenset of verified `(listing_id,
    parent_listing_id)` association pairs, one per segment (or the
    top-level listing with `None` when there are no segments), or
    `None` when any segment's `parent_listing_id` is neither a string
    nor `None`, or a segment-less record's top-level `listing_id` is
    not a string (unverifiable; building it never fails the refresh);
  - `v3`: whether the record's ID is in `returned_ids`. It is not
    taken from `is_v3`, because retained v3 records the fallback
    republishes keep `is_v3=True`.
- `observed_codes`: the full, unbounded code → ID map from
  `observe_codes` over the accepted records.
- `fallback_observed_codes`: the code → ID map from `observe_codes`
  over the refresh's `observed_reservations`, ignoring the configured
  status filter but keeping the hold and empty-code exclusions.
- `fallback_served`, `fallback_sourced`, `fallback_absent`: copied from
  the fetch result.
- `fallback_observed`: copied from `ReservationFetchResult.fallback_observed`,
  including records not republished because their status left the
  configured set.

No confirmation code is stored in `records`, only its kind. The only
code-bearing fields are `observed_codes` and `fallback_observed_codes`,
which hold the same values the refresh already observes in memory
today. The snapshot is never serialized, and its type deliberately
defines no `__repr__` that prints codes.

**Validity rules:**

- The snapshot is used only if `attempt` equals the latest successful
  outcome's attempt, with matching witness options, settings, entry, and
  client.
- A missing or stale snapshot means the evidence is unavailable, so the
  action is refused.
- Size is bounded by one refresh's result set, the same memory the
  refresh already holds transiently.

### 013 evidence adapter

The 013 evidence adapter (`ConflictEvidence` port) is a thin internal
interface. It is satisfied by feature 013 when that feature merges, and it
returns "unavailable" until then.

- `available()`: returns `False` if the evidence is unavailable, the
  FR-008c bound was exceeded, or any of the three 013 interfaces
  (rejected IDs, conflict-only evidence, record validator) is missing.
- `codes_for(id)` and `ids_for(code)`: read-only conflict-only mappings.
- `validate_record(raw, action)`: returns `valid` or a rejection, under
  the 013 accepted-refresh rules. These include FR-008d for each raw
  segment and the facts the action needs.

### By-ID evidence

By-ID evidence is a local value inside one action attempt. It is never
stored on any object that outlives the attempt.

| Field | Meaning |
| --- | --- |
| `reservation_id` | Equals the target exactly |
| `qualifier` | `absent` or `codeless` |
| `record` | Validated `GuestyReservation` |
| `code` | Non-empty string; used only for the conflict check |
| `listing_ids` | Verified (listing, parent listing) pairs |
| `notes` | Validated str-keyed mapping; missing or null is `{}` |
| `snapshot_attempt` | Snapshot attempt it was authorized against |

For note writes, a missing or `null` `notes` normalizes to `{}`, a
non-object `notes` is `malformed`, and non-string keys are dropped as
`_read_v3_notes` does. The value passed as `prefetched_notes` is
therefore never `None`, which is reserved for ordinary writes that
still read.

### Write attempt (extended)

One action invocation goes through these stages, each at most once:

1. preflight;
2. the 012 qualifying refresh;
3. by-ID qualification;
4. the by-ID read;
5. authorization;
6. the pre-write recheck;
7. the v3 write.

## State transitions

```text
012 refresh accepted ── guard passes ──────────────────────▶ 012 write (unchanged)
        │
        └─ guard: ReservationIdentityMissError
              │
              ├─ not v3 / fallback provenance / rejected / confirmed
              │  absent / conflict-only / snapshot or 013 unavailable
              │      ──▶ unchanged 012 refusal (no request)
              │
              └─ qualifies (absent | codeless)
                    │ single GET (lock held)
                    ├─ read error ──▶ READ_FAILED(category), no write
                    ├─ [] / mismatched ──▶ NOT_FOUND, no write
                    ├─ malformed / no_code / disallowed_status /
                    │  identity_conflict / FR-007 disagreement
                    │      ──▶ FOUND_REFUSED(category), no write
                    └─ authorized
                          │ before_send recheck
                          ├─ context/witness fails ──▶ FOUND_REFUSED(guard_witness)
                          ├─ newer accepted refresh:
                          │     note ──▶ FOUND_REFUSED(guard_witness)
                          │     status/custom field: guard passes ∧ route V3
                          │         ──▶ normal v3 write
                          │     else ──▶ FOUND_REFUSED(guard_witness)
                          └─ unchanged ──▶ single v3 write
                                 ├─ success ──▶ FOUND_WRITTEN(qualifier)
                                 └─ failure ──▶ FOUND_WRITE_FAILED(qualifier)
```

Every terminal state discards the by-ID evidence. None of these states
loops back to an earlier one.

## Persistence and retention

Nothing in this feature is persisted. Compared with their state after the
qualifying refresh, just before the read, the by-ID path leaves all of
these unchanged:

- coordinator `data`;
- `current_ids()`;
- every baseline map and `candidates`;
- the Store save count;
- continuity and ambiguity;
- the fallback sets;
- confirmed-absent evidence;
- 013 conflict-only evidence;
- the snapshot.

The snapshot itself is replaced only by the coordinator's accepted
refresh, never by the action path.

## Validation and privacy

- Identifiers are compared exactly, as strings, with no case folding or
  trimming.
- Codes are accepted only when they are a non-empty `str`, using the same
  rule as `observe_codes`.
- Statuses are normalized with strip and lowercase, looked up directly
  in `V3_STATUS_MAP` before any parsing, and the mapped value is checked
  against the allowed set (research R7).
- Error and log text is built only from fixed strings, the reservation
  ID, the action type, the qualifier, and the category. No code, PIN,
  guest field, token, or raw or untrusted response content appears in
  it, and that includes untrusted status strings.
- Diagnostics are unchanged.

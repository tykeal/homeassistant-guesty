<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: By-ID Reservation Write Fallback

**Consumers**: The existing Home Assistant actions
`guesty.add_reservation_note`, `guesty.set_reservation_status`, and
`guesty.set_custom_field` with `target_type: reservation`.

**Scope**: After a feature 012 qualifying refresh, this feature adds at
most one single-attempt
`GET /reservations-v3?reservationIds={id}` and at most one v3 write. It
applies only when the target is absent from the accepted v3 result, or is
present there without a confirmation code. Service names, fields,
response shapes, write resources, the baseline schema, cache-hit
behavior, and all listing writes are unchanged.

## Preflight (unchanged from feature 012)

1. Service schemas and validators run first. Invalid input fails with no
   refresh, no read, and no write.
2. `guard_reservation_write` refusals other than the missing-code refusal
   fail as they do today. Those refusals are an unusable or unstored
   baseline, unconfirmed continuity, and ambiguity.
3. Feature 012's known-unroutable refusal, for retained-only or
   disabled-route fallback targets, applies before any refresh or read.
4. The 012 qualifying refresh must be accepted, and the 012 witness must
   hold. Otherwise the action fails as in 012, with no read.

## Qualification (no request)

Evaluate these checks inside the 012 dispatch lock, after the witness
context check passes and the identity guard still reports a missing
code. Any failure issues **no read** and re-raises the original
`ReservationIdentityMissError` unchanged (spec US2 scenario 7: "the
existing refusal applies"). The listed category is written only to a
DEBUG log containing the category and the reservation ID.

1. The configured and witnessed source is v3. Otherwise: `not_v3`.
2. The target is not in `fallback_served_ids` or `fallback_sourced_ids`,
   live or in the snapshot, nor in the snapshot's `fallback_observed`,
   nor in `api_client.legacy_observed_reservations`. Otherwise:
   `fallback_provenance`. An incomplete (overflowed) session set gives
   `evidence_unavailable`.
3. An accepted-refresh snapshot exists for the latest successful
   attempt. Otherwise: `evidence_unavailable`.
4. The target is not in the snapshot's `rejected_ids`. Otherwise:
   `rejected`. This check runs before step 5 because `returned_ids`
   includes rejected identifiers.
5. The target is absent from the snapshot's `returned_ids` (raw v3
   search IDs only), or it is an
   accepted, non-hold v3 record whose `code_kind` is `none` or `empty`.
   Otherwise: `not_qualifying`, meaning the target is in a state the 012
   guard decides.
6. The target is not in `confirmed_absent_reservations`, the
   snapshot's `fallback_absent`, or `legacy_retired_reservations` (the
   session legacy-retired set). Otherwise: `confirmed_absent`. An
   incomplete session set gives `evidence_unavailable`.
7. The 013 adapter is available and within its bound. Otherwise:
   `evidence_unavailable`. Once feature 013 is merged, this step is
   defensive only: 013's identity guard already refuses before the
   membership check while its evidence is unavailable or revalidating
   (see [Required feature 013 interface](#required-feature-013-interface)),
   so qualification is never reached in that state.
8. The 013 adapter holds no conflict-only evidence for the target.
   Otherwise: `rejected`.

## Read contract

- Send `GuestyActionsClient.read_reservation_by_id(reservation_id)`
  exactly once. The request is `GET /reservations-v3` with the single
  query parameter `reservationIds={reservation_id}`.
- The request runs with `single_attempt=True` under
  `private_http_logging(True)`. It does not retry on 429, 5xx, or
  transport errors, and it does not renew on 401.
- No other lookup follows the read, whether to another source, legacy,
  or a guest lookup.

Responses map to results as follows:

- **Candidate record**: a 2xx JSON object whose `results` is a list of
  exactly one object, whose `reservationId` is a string equal to
  `reservation_id`, and that either has no `pagination` key or has
  `pagination.hasMore` exactly `false`.
- **`not_found`**: a 2xx response with `results == []`.
- **`mismatched`**, reported as not found: a 2xx response with one object
  whose `reservationId` differs.
- **`malformed`**: any of these:
  - not JSON;
  - not an object;
  - a bare list;
  - `results` missing or not a list;
  - more than one item;
  - a non-object item;
  - a non-string ID;
  - a `pagination` key whose value is not an object, or whose `hasMore`
    is missing, `null`, non-boolean (for example `"true"`), or `true`.
    Only an absent `pagination` key or `hasMore` exactly `false` is
    accepted.
- **`authentication`**: 401, 403, a token auth or permission error, or
  any other unclassified token failure.
- **`rate_limit`**: 429, or the local token request budget
  (`GuestyRateLimitError`).
- **`http`** (token): a token endpoint non-200, including 429, or a bad
  token body (`GuestyResponseError`).
- **`http`**: any other non-2xx status.
- **`transport`**: a connect error, a timeout, any other transport
  error, or a token request connection failure.

## Authorization (no request)

For a candidate record, every check below must pass, in order. Any
failure refuses with no write.

1. The record's status is checked on the raw record, before any parsing.
   A missing or non-string status is refused as `disallowed_status`. The
   stripped, lowercased raw status is looked up directly in
   `V3_STATUS_MAP`, so an unknown value is never logged. The mapped value
   must be in the configured selection (`ACTIONABLE_STATUSES`) and must
   not be `alteration`. Otherwise (`alteration`, an unknown status, or a
   missing status): `disallowed_status`. As with an accepted refresh,
   `completed` and `cancelled` map into the selection.
2. The 013 single-record validator accepts the record (including FR-008d
   for each raw segment and the facts the action needs), and
   `GuestyReservation.from_v3_api_dict` parses it. Otherwise: `malformed`.
3. `confirmationCode` is a non-empty `str`. Otherwise: `no_code`.
4. No conflict exists in any of these maps (otherwise
   `identity_conflict`):
   - the snapshot `observed_codes` and `fallback_observed_codes`;
   - the baseline `codes`, `legacy_codes`, `bootstrap_codes`, and
     `fallback_codes`;
   - 013 conflict-only evidence.

   A conflict is the code mapping to another ID, or another code mapping
   to the target. Unavailable evidence refuses. Evidence is unavailable
   when there is no valid snapshot, when the baseline is not loaded or
   usable or is `_unstored`, or when the 013 adapter is unavailable or
   over its bound. A `None` legacy, bootstrap, or fallback sub-map
   counts as empty.
5. If the qualifier is `codeless`, the record's ID and verified
   `(listing_id, parent_listing_id)` association set must equal the
   snapshot record's, and neither set may be unverifiable (a non-string
   listing or parent ID). Otherwise: `identity_conflict`.

The code is used only for step 4. It is never a lookup key or a write
key.

## Write contract

- Immediately before sending (`before_send`), the recheck must pass all
  of these. A failure gives `guard_witness`, with no write.
  - The 012 context witness: options, settings, entry, client, and
    accepted outcomes.
  - `classify_write_refusal`, which must return no refusal or
    `missing_current_code` only. A baseline, `_unstored`, continuity, or
    ambiguity refusal fails.
  - The v3 source.
  - No fallback provenance.
  - Either an unchanged snapshot attempt, or the newer-accepted-refresh
    branch below.
- If a newer accepted refresh is visible at recheck:
  - a note write fails with `guard_witness`;
  - a status or custom-field write continues only if
    `guard_reservation_write` passes and `select_reservation_write_route`
    returns `ROUTE_V3`;
  - anything else fails with `guard_witness`.

  No second read is issued.
- Exactly one v3 write is sent, in recovery mode (`single_attempt=True`).
  A note write sends the verified record's note fields plus the appended
  text through `PUT /reservations-v3/{id}/notes`, with no read of its own.
  `prefetched_notes` is always a mapping on this path (missing or `null`
  notes become `{}`), never `None`, so no second GET can occur.
  Status writes use `PUT /reservations-v3/{id}/status`, and custom-field
  writes use `PUT /reservations-v3/{id}/custom-fields`.
- A rejected or unknown-outcome write is reported as "found by ID, write
  failed", with its qualifier, and is never retried. The read and the
  authorization run outside the write's error-conversion block. All
  three handlers, including the custom-field handler in `__init__.py`,
  translate the by-ID outcome errors explicitly.

## Retention

Compared with their state after the qualifying refresh, captured just
before the read, the read, the record, the code, and the outcome leave
no trace in any of these:

- coordinator `data`;
- `current_ids()`;
- any baseline map or `candidates`;
- the Store;
- continuity or ambiguity;
- the fallback sets;
- confirmed-absent evidence;
- 013 conflict-only evidence;
- the snapshot.

## Outcomes and privacy

Each failure outcome other than "not eligible" produces a
`HomeAssistantError` whose text names the outcome
class, the qualifier when one applies, the category, the reservation ID,
and a corrective hint. Examples of the hint:

- "retry after the next refresh";
- "check credentials";
- "Guesty rate-limited the lookup; retry later";
- "the reservation's identity conflicts with known evidence; review it
  in Guesty".

A successful write returns the action's normal service result. Its
outcome, "found by ID, written", goes to one INFO log line along with
the qualifier, the action type, and the reservation ID.

Every outcome class except "not eligible" carries the `absent` or
`codeless` qualifier:

- **Found by ID**: written, or write failed.
- **Not found by ID**: `not_found` or `mismatched`.
- **Found but refused**: `malformed`, `no_code`, `disallowed_status`,
  `identity_conflict`, or `guard_witness`.
- **Read failure**: `http`, `rate_limit`, `authentication`, or
  `transport`.
- **Not eligible (pre-read)**, which has no qualifier and no new message,
  only the unchanged 012 refusal plus a DEBUG category: `not_v3`,
  `fallback_provenance`, `rejected`, `confirmed_absent`,
  `evidence_unavailable`, or `not_qualifying`.

The `not_qualifying` category covers a returned record that has a valid
code but still drew a missing-code refusal. This can happen when the
bounded baseline (`MAX_BASELINE_CODES`) evicted its code. Such a target
is refused rather than read. Diagnostics are unchanged, and no outcome
counters are added.

Errors, logs, and diagnostics never contain any of these:

- confirmation codes;
- PINs or custom-field values;
- guest names, emails, or phone numbers;
- tokens or credentials;
- raw response bodies;
- untrusted status strings.

## Required feature 013 interface

- **Rejected IDs**: `ReservationFetchResult.rejected_ids` for each
  accepted refresh, with feature 013's definition: the sorted,
  de-duplicated, batch-verified v3 `reservationId` values of records
  that 013 rejects or finds partially valid (FR-008d raw-segment
  rejections included), including a rejected or partially valid hold.
  It excludes accepted holds, valid records that are unpublished,
  identifiers that fail batch validation (the refresh fails instead),
  and legacy by-ID invalid outcomes (013's `fallback_invalid_ids`). It
  is no longer "raw minus parsed".
- **Conflict-only evidence**: a read-only `available()`, which is false
  whenever the evidence is not `ready` (including when the FR-008c
  bound has been exceeded), plus lookups in both directions between
  code and ID.
- **Record validation**: `validate_record(raw, action)`, which applies
  the accepted-refresh rules to one raw v3 record.

While any of these interfaces is missing, the adapter reports unavailable
and every qualifying action keeps its unchanged 012 refusal, with no
read. Its DEBUG category is `evidence_unavailable`.

Once feature 013 is merged, its `classify_write_refusal` checks evidence
state after the ambiguity check and **before** the membership check.
While conflict-only evidence is `revalidating` the write is refused with
`REFUSAL_REVALIDATING`; while it is `compromised`, `saturated` (over its
bound), `unstored` or `unloaded` it is refused with
`REFUSAL_EVIDENCE_UNAVAILABLE`. Either is a plain `GuestyActionError`,
not a `ReservationIdentityMissError`, so there is no 012 recovery
refresh, no by-ID qualification and no by-ID read, and no 014 DEBUG
category is written. Tests for that state expect 013's refusal, not the
unchanged 012 refusal.

## Required contract tests

Each test asserts respx call counts for the search, the by-ID GET, the
token endpoint, and every write resource.

1. **#223 regression, once per write type** (note, status,
   custom-field door code):
   - **Setup**:
     - The baseline is stored and usable, continuity is confirmed, and
       the source is v3.
     - A same-day `confirmed` target with `createdAt` after its localized
       check-in is absent from the v3 search.
     - The by-ID response carries one valid coded record.
   - **Expected**:
     - One search, one GET, one PUT, the normal success result, and one
       INFO log reporting "found by ID, written", qualifier `absent`.
     - SC-004 retention is unchanged.
     - No token request is made beyond the setup.
2. **Code-less qualifier success**, once per write type. A listing
   mismatch is refused.
3. **SC-005**: a target outside the listing selection or local window
   that the search returned with a code takes the normal 012 path. An
   absent target outside the listing selection or window, found by ID,
   is written.
4. **Pre-read refusals** (zero GETs and zero writes, with the unchanged
   012 refusal text) for each of these:
   - an invalid identifier or input, which also makes zero refreshes;
   - every non-miss guard refusal;
   - a known-unroutable target;
   - a failed or superseded 012 refresh;
   - a non-v3 source;
   - served, sourced, and snapshot fallback provenance;
   - a legacy lookup that returned the target at a status outside the
     configured set (in `fallback_observed`, not served) while the v3
     search omitted it;
   - the same legacy observation from an earlier refresh, with the
     qualifying refresh's lookup not due (session set only);
   - an incomplete session legacy-observed set;
   - a target retired by an earlier scheduled refresh and pruned from
     the live confirmed-absent set (session legacy-retired set only);
   - an incomplete session legacy-retired set;
   - rejected in the refresh;
   - conflict-only evidence present;
   - the 013 adapter unavailable or over its bound (with the test
     double; with real 013 evidence, 013 refuses this state earlier, as
     § Required feature 013 interface states);
   - confirmed absent, in the live pruned set and in the snapshot's
     `fallback_absent` for the same accepted refresh;
   - a missing or stale snapshot.
5. **Read outcomes** (zero writes; exactly one by-ID GET once the read
   is dispatched, and zero by-ID GETs for a token-acquisition failure,
   which exits `_request` before the read is sent) for each of these:
   - `[]`;
   - mismatched;
   - two records;
   - duplicate matches;
   - a bare dict;
   - a bare list;
   - non-list `results`;
   - `hasMore: true`;
   - `pagination: null`, `pagination: []`, `pagination: {}`,
     `hasMore: null`, and `hasMore: "true"`;
   - non-JSON;
   - 400, 401 (no token refresh), 403, 404, 429 (no retry), 500, and 503;
   - a token request that is rejected (`authentication`), blocked by the
     local budget (`rate_limit`), or answered with an endpoint 429 or
     other non-200 (`http`), or that fails to connect (`transport`);
   - timeout and connect errors.
6. **Authorization refusals** (one GET, zero writes) for each of these:
   - a malformed record or segment (via the 013 validator double);
   - missing status;
   - a non-string status (number, list, or object);
   - a non-object `notes` on a note write (`malformed`);
   - `alteration`, unknown, and missing statuses, with a sentinel
     unknown status asserted absent from logs;
   - `completed` and `cancelled` accepted as in a refresh;
   - a missing code, `""`, a numeric code, and a list as the code;
   - the code mapped to another ID in each of the seven maps,
     including an out-of-status legacy-observed record with a different
     ID (`fallback_observed_codes`);
   - the target mapped to another code in each map (only the legacy and
     bootstrap maps can hold this before the action; the other baseline
     maps and conflict-only evidence must gain it during the read, and
     the snapshot maps are tested directly against authorization,
     because beforehand the target would be written normally or refused
     by qualification);
   - a code duplicating another ID's code;
   - each unavailable source: no snapshot, an unusable or `_unstored`
     baseline, and the 013 adapter unavailable;
   - a `None` sub-map treated as empty, which does not refuse;
   - a code-less qualifier whose by-ID segment has the same unit
     `listingId` but a different parent listing (`identity_conflict`).
7. **Recheck**: each of these fails with zero writes:
   - entry reload during the read;
   - the baseline becomes unstored, or continuity or ambiguity changes,
     between the read and the write;
   - options replacement;
   - source change;
   - an injected newer failed refresh;
   - an injected newer ambiguous refresh.

   An injected newer accepted refresh:
   - fails a note write;
   - lets a status or custom-field write through only when the target is
     now coded and v3-routed;
   - fails when the newer refresh gives the target fallback provenance.

   There is never a second GET. Each failure is reported as "found but
   refused" with `guard_witness`, never as "write failed".
8. **Write failure**: a rejected or timed-out write gives one PUT, no
   retry, and "found by ID, write failed".
9. **Overlap**: two concurrent actions, on the same target and on
   different targets, each make one
   GET and decide independently. No result is shared.
10. **Privacy**: sentinel codes, PINs, guest names, and raw-body markers
    are absent from errors, `caplog`, and diagnostics for every outcome
    above.
11. **Unchanged behavior**:
    - cache-hit writes;
    - 012 recovery where the refresh supplies a code;
    - the existing note read and `_v3_record` behavior;
    - listing writes are never eligible.

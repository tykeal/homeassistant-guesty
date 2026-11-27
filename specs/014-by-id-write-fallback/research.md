<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: By-ID Reservation Write Fallback

## Scope and decisions

This document records the live-code findings on `main` (the merge of
PR #322) that ground the plan, and one decision per open design question.
Line references are approximate and serve as navigation aids. Where a
feature 014 requirement depends on behavior that does not exist yet, the
decision is to fail closed and state the dependency, not to approximate
the requirement.

## R1 — Feature 013 is a hard dependency for authorization

### R1 findings

- Searching `custom_components/` for conflict-only evidence finds nothing.
  Feature 013 is specified but not implemented.
- `reservations_v3._map_results` silently drops records for which
  `GuestyReservation.from_v3_api_dict` returns `None`.
  `ReservationFetchResult.observed_ids` lists only parsed identifiers, so
  the set of identifiers "returned but rejected" exists only in local
  variables and is then lost.
- `from_v3_api_dict` returns `None` only for a missing `reservationId`, a
  missing `status`, or no usable stay segment. It does not apply 013
  FR-003b/c or FR-008d (raw-segment) validation.

### R1 decision

Split the work so that everything not dependent on 013 can land first:

- the strict read;
- the parser envelope;
- the error categories;
- the accepted-refresh snapshot;
- the interim rejected-ID collection.

Model the 013 needs as one thin adapter with three operations:

- rejected IDs for an accepted refresh, delivered through
  `ReservationFetchResult.rejected_ids`; absence from that set is
  relied on only while the adapter reports available;
- a conflict-only query, including availability and bound-exceeded
  state;
- a single-record validator.

Until 013 provides all three, the adapter reports **unavailable**. FR-002
("held negative evidence cannot be checked") and FR-006 ("evidence cannot
be checked") then refuse before any read is issued. Under US2 scenario 7
the existing 012 refusal applies, so the feature is inert and the
user-visible behavior is unchanged.

### R1 rationale

FR-005(b) and FR-006 reference 013 rules explicitly. Using today's
`from_v3_api_dict` as a stand-in for 013 validation would authorize writes
on weaker evidence than the spec requires, and treating "no conflict-only
store exists" as "no conflict" would be fail-open.

### R1 alternatives considered

- **Implement a private subset of 013 inside 014.** Rejected: it
  duplicates or diverges from the 013 design and touches 013 storage
  scope, which FR-014 forbids.
- **Ship 014 with permissive validation and tighten it later.** Rejected:
  it violates the fail-closed rule.

## R2 — Hook point: the 012 dispatch-lock witness

### R2 findings

- `execute_reservation_write` (`actions.py`, about line 543) runs
  `guard_reservation_write` (line 239). That function calls
  `coordinator.identity.classify_write_refusal` (`identity.py`, line 1050).
- `classify_write_refusal` checks, in order:
  1. a usable baseline;
  2. `_unstored`;
  3. continuity;
  4. `_ambiguous`;
  5. membership in `current_ids()`.

  `missing_current_code` is therefore returned only when every earlier
  conjunct passed. That refusal becomes `ReservationIdentityPresentError`
  or `ReservationIdentityAbsentError`, both subclasses of
  `ReservationIdentityMissError`.
- `_execute_recovered_reservation_write` (line 470) holds
  `coordinator.async_write_miss_dispatch_lock()` while it does three
  things:
  1. runs `_check_recovery_witness` (line 395);
  2. calls `write(route, True, recheck)`;
  3. runs `recheck` (the witness again) before send.
- `_check_recovery_witness` validates:
  - options identity and `_write_miss_settings()`;
  - entry liveness;
  - that every `_write_miss_outcomes` entry after the witness generation
    succeeded with the same options, settings, entry, and client;
  - `last_update_success`.

  It then runs the identity guard and route selection.

### R2 decision

Split `_check_recovery_witness` into two checks:

- `_check_recovery_context`: every check except identity and route.
- The existing identity and route checks.

On the first check inside the lock, a `ReservationIdentityMissError` from
the identity step enters the by-ID path. Every other refusal keeps its 012
behavior. The 012 write path is unchanged when the guard passes.

### R2 rationale

This reuses, rather than reimplements, every 012 precondition: the
qualifying refresh, the witness, liveness, the source, and the lock. It
also guarantees FR-002's "every other guard passes", because a
missing-code refusal is only reachable after those guards pass.

### R2 alternatives considered

- **Branch in `execute_reservation_write` before the lock.** Rejected: it
  would read outside the 012 serialization and need a second witness
  implementation.

## R3 — Target qualification uses an accepted-refresh snapshot

### R3 findings

- The coordinator keeps only:
  - published `data`;
  - `fallback_served_ids` and `fallback_sourced_ids`;
  - the identity baseline;
  - `_write_miss_outcomes`, a deque of (generation, attempt, success,
    options, settings, entry, client).
- It does not keep the raw returned IDs, the rejected IDs, per-record code
  facts for records filtered from publication, or the unbounded observed
  code map.
- Publication filters by listing selection and by the local window
  (#293). Absence from `coordinator.data` is therefore not the same as
  absence from the accepted result.

### R3 decision

The coordinator records an in-memory `AcceptedRefreshSnapshot` (see the
data model) on the accepted path only. It is keyed by the outcome
`attempt` and replaced wholesale on each accepted refresh. It is never
persisted, logged, or exposed in diagnostics.

A target qualifies only in one of two cases:

- **absent**: it is not among the snapshot's raw returned IDs;
- **code-less**: it is an accepted v3 record whose code is `None` or
  `""`, and it is not at the `alteration` hold status.

A non-string code is malformed evidence, not code-less, so it does not
qualify. A rejected ID never qualifies. If the snapshot is missing or its
attempt does not match the latest successful outcome, the action is
refused without a read.

### R3 rationale

This reads FR-002 against the accepted *result*, not the published
subset. That keeps #293 and SC-005: a target filtered only by listing or
window still counts as returned, so the normal guard decides it.

## R4 — Fallback provenance and the v3-only route

### R4 findings

- `select_reservation_write_route` routes `fallback_served_ids` to legacy
  (or refuses when retired) and refuses `fallback_sourced_ids`.
- When legacy routing is retired, the coordinator keeps the last known
  sets.
- `IdentityBaseline.candidates` (`CandidateHint`) holds durable hints for
  *every* reservation still known, so a reservation that vanishes can
  become a fallback lookup candidate (FR-049a). A candidate hint records
  a prior v3 observation, not legacy provenance.
- `api_client.confirmed_absent_reservations` is the fallback's current,
  pruned set of FR-052 retirements, not a full session history.
  `_prune` in `reservations_fallback.py` intersects it with the known
  candidates and the unreported absences. Once the identity record
  drops a retired ID's candidate hint, that ID leaves the set at the
  next refresh. `fetch_result.fallback_absent` holds the retirements
  from this refresh, which is why the snapshot keeps them for the
  accepted refresh it describes. Retirement evidence that is no longer
  held is unavailable, which the spec explicitly accepts.

### R4 decision

"Current, retained, or served fallback provenance" (FR-003) means
membership in any of:

- `coordinator.fallback_served_ids`;
- `coordinator.fallback_sourced_ids`;
- the snapshot's `fallback_served`, `fallback_sourced`, or
  `fallback_observed`;
- `api_client.legacy_observed_reservations`, the session legacy-observed
  set.

`fallback_observed` is built from `FallbackOutcome.observed` (the IDs in
`ReservationFetchResult.observed_reservations` that came from legacy).
It matters because `_lookup` in `reservations_fallback.py` returns a
legacy record whose status left the configured set while popping it
from `_served` and `_retained`. That ID then appears in neither served
nor sourced, yet legacy has just given current evidence about it. If v3
search also omits it, a by-ID v3 read could otherwise authorize a write
against a record the other source says is out of selection, which is
the source disagreement the spec requires to fail closed.

A per-refresh set is not enough. `_is_due` gates lookups by
`LEGACY_FALLBACK_RECHECK_SECONDS` (3600), so a write-miss refresh within
the hour usually does not look the target up again, and `_lookup` keeps
no other record of the observation. FR-003 covers provenance "retained
in this session", so the fallback source keeps a bounded, in-memory
session set of every legacy-observed ID. An overflowing set is marked
incomplete and makes every by-ID attempt ineligible, which fails closed.
`FallbackOutcome.observed` does not reach the coordinator today, so
`ReservationFetchResult` gains `fallback_observed`.

Any membership refuses without a read. Candidate hints alone do not
block. They are never added to or modified.

Confirmed absence means membership in:

- `api_client.confirmed_absent_reservations`, or
- the snapshot's `fallback_absent`.

Confirmed absence refuses. The route is forced to `ROUTE_V3`, and the
legacy route is never selected on this path.

### R4 rationale

Blocking every candidate would refuse any previously seen reservation
that later drops out of search, even though the spec treats "absent
from search" as qualifying whatever the cause. It would also block on
evidence the spec does not name as provenance. Prior v3 evidence for
the ID is still checked for conflicts (R8), and a candidate the fallback
actually served, published, observed, or retired in this session is
still refused, through the session sets described below.

Retirement needs its own session set. `_retire` adds the ID to
`_confirmed_absent`, `identity.async_record_refresh` then drops its
candidate hint, and `_prune` (`_confirmed_absent &= known |
_unreported_absent`) drops it at the next refresh. A retirement from an
earlier scheduled refresh is therefore gone from the live set and from
the qualifying snapshot's `fallback_absent`. The fallback source keeps
a bounded, in-memory, never-pruned session legacy-retired set recorded
in `_retire`, exposed as `GuestyApiClient.legacy_retired_reservations`.
Overflow marks it incomplete, which gives `evidence_unavailable`. It is
not durable, so it stays within the spec's "no new durable retirement
record"; retirement evidence lost to a reload remains the accepted
spec limitation.

### R4 alternatives considered

- **Treat candidate hints as retained provenance.** Rejected because it
  misreads FR-049a. It is not needed for safety either: real fallback
  service shows up in the served and sourced sets, a legacy lookup that
  declined to republish shows up in `fallback_observed` and the session
  legacy-observed set, and a legacy retirement shows up in the session
  legacy-retired set.

## R5 — A new strict, single-attempt by-ID read

### R5 findings

- `_read_v3_notes` (`api/actions.py`, about line 255) issues
  `GET /reservations-v3?reservationIds={id}` under the **default** retry
  policy. Only the caller wraps it in `private_http_logging(single_attempt)`.
- `_v3_record` (line 296) is permissive. It accepts:
  - a `results` list;
  - a bare list;
  - a dict *without* `results`, treated as the record itself.

  It returns the first item matched through `_identifies`, which also
  accepts `id` and `_id`.
- In `GuestyApiClient._request` (`api/client.py`, about line 506),
  `single_attempt=True` without `recovery_write` behaves as follows:
  - 401, 429, and 5xx responses are returned without retry or reactive
    token renewal;
  - `ConnectError` and `Timeout` become `GuestyConnectionError`;
  - other transport and token errors propagate.

  `recovery_write=True` maps errors to "write was not sent" messages,
  which are wrong for a read.

### R5 decision

Add `GuestyActionsClient.read_reservation_by_id(reservation_id)`. It sends
exactly one `_request("GET", RESERVATIONS_V3_ENDPOINT,
params={"reservationIds": reservation_id}, single_attempt=True)` under
`private_http_logging(True)`.

Errors map to `GuestyByIdReadError(category, target_id)` in
`api/exceptions.py`, with a fixed category message:

| Condition | Category |
| --- | --- |
| HTTP 401/403; token auth or permission error | `authentication` |
| Token local budget `GuestyRateLimitError` | `rate_limit` |
| Token `GuestyConnectionError`/`httpx.TransportError` | `transport` |
| Token `GuestyResponseError` (non-200 incl. endpoint 429, bad body) | `http` |
| Any other token failure | `authentication` |
| HTTP 429 | `rate_limit` |
| Other non-2xx | `http` |
| `ConnectError` / `Timeout` / other transport error | `transport` |
| Envelope or record not provable (R6) | `malformed` |
| `results == []` | `not_found` |
| One record, `reservationId` differs | `mismatched` |

HTTP 403 is reported as `authentication`, which keeps the read-failure
categories to the four that FR-012 names. The message text says the
credentials were rejected or lack permission. The existing note read
keeps its current behavior.

### R5 rationale

FR-004 requires no retry and no renewal. The derived decision on
single-attempt reads makes an authentication failure fail the action.
Reusing `_v3_record` would accept envelopes that cannot prove they
describe only the target.

### R5 alternatives considered

- **Tighten `_v3_record` for everyone.** Rejected: it would change
  feature 008 note behavior outside this scope.
- **Pass `recovery_write=True`.** Rejected: that path produces
  write-specific messages.

## R6 — Envelope completeness

### R6 decision

The response is authorizing evidence only if all of these hold:

- it is a JSON object;
- `results` is a list;
- `results` has exactly one element;
- that element is a JSON object;
- the element's `reservationId` is a string exactly equal to the target.
  `id` and `_id` are not consulted.

If a `pagination` key is present, its value must be an object whose
`hasMore` is exactly the boolean `false`; a missing, `null`, or
non-boolean `hasMore`, or a non-object `pagination`, does not establish
completeness. A bare dict, a bare list, a non-list `results`, more than
one element, a non-object element, or any such pagination is
`malformed`. An
empty `results` is `not_found`, and a single record whose ID differs is
`mismatched`. Mismatched is reported to the user under "not found by
identifier", as FR-012 requires.

### R6 rationale

This implements the spec edge case "an envelope that does not prove which
record was requested, or whose result set cannot be shown complete, is
malformed". It also matches the v3 page shape that feature 008 validated
(`results` plus `pagination`).

## R7 — Allowed status

### R7 findings

- No configurable status option exists.
  `ReservationsCoordinator._async_fetch_reservations` calls
  `get_reservations` without `statuses`, so the selection is
  `ACTIONABLE_STATUSES`: `confirmed`, `checked_in`, `checked_out`,
  `canceled`.
- `V3_STATUS_FILTER_MAP` adds `completed` for `checked_out`.
- `V3_STATUS_MAP` also accepts `cancelled`, but the search never
  requests it.
- `map_v3_status` logs the raw value when it is unrecognized.

### R7 decision

Check the status on the raw record before any parsing. A missing or
non-string status is refused as `disallowed_status`. Otherwise, look the
stripped,
lowercased raw status up directly in `V3_STATUS_MAP`, without calling
`map_v3_status`, so an unrecognized value is never logged. The mapped
status must be in the configured selection, `ACTIONABLE_STATUSES`, and
must not be `alteration`. As a result, `completed` (mapped to
`checked_out`) and `cancelled` (mapped to `canceled`) are allowed.
Anything unknown or missing is refused as `disallowed_status`.

### R7 rationale

The spec says a status in the selection is treated "exactly as the
qualifying refresh path would treat it" and that this feature neither
widens nor narrows eligibility. An accepted refresh record is mapped
through `V3_STATUS_MAP`, so the by-ID record uses the same mapping. The
direct lookup also keeps untrusted status text out of logs.

## R8 — Identity conflict evidence

### R8 findings

- `IdentityBaseline` holds `codes`, `legacy_codes`, `bootstrap_codes`, and
  `fallback_codes` (code → ID).
- `codes` is replaced on each refresh and capped at `MAX_BASELINE_CODES`
  (5000) by `_bounded`, which evicts the earliest checkout first.
- `observe_codes` excludes hold statuses and non-string or empty codes,
  and it records within-refresh collisions.

### R8 decision

Evaluate a conflict against all of these maps:

1. the snapshot's **full** observed code map from the accepted refresh,
   since the baseline cap may have evicted entries (the qualifying
   refresh's current identities);
2. the snapshot's `fallback_observed_codes`: `observe_codes` over the
   refresh's `observed_reservations`, which includes legacy records
   `_lookup` declined to republish because their status left the
   configured set. Those codes reach neither the accepted records nor
   the baseline `fallback_codes`, so without this map a by-ID code
   reused from a just-cancelled legacy booking would pass;
3. the baseline `codes`;
4. the baseline `legacy_codes`;
5. the baseline `bootstrap_codes`;
6. the baseline `fallback_codes`;
7. 013 conflict-only evidence, through the adapter.

Codes from out-of-status legacy observations in earlier refreshes are
not retained (only their IDs are, in the session legacy-observed set).
That is evidence no longer held, which the spec accepts as unavailable;
the target ID itself is still refused through provenance.

There is a conflict if either of these holds in any map:

- the by-ID code maps to a different ID;
- any code maps to the target while differing from the by-ID code.

A by-ID code that would duplicate one already held by another ID is a
conflict, and it does not change ambiguity or continuity. An unavailable
source refuses: no valid snapshot, a baseline that is not loaded, not
usable, or `_unstored`, or a 013 adapter that is unavailable or over its
bound. A `None` legacy, bootstrap, or fallback sub-map is legitimate "no
evidence" and counts as empty, as it does in the existing comparison
(`identity.py`, about line 503). A code with no mapping anywhere is
acceptable. The checks use a new read-only `ReservationIdentityGuard`
query, so no baseline state is mutated.

## R9 — Agreement for code-less refresh records (FR-007)

### R9 decision

The snapshot keeps, for every accepted v3 record, its ID and its
verified listing-association set: one `(listing_id, parent_listing_id)`
pair per validated stay segment, or `(listing_id, None)` from the top
level for a record without segments. Comparing only `listingId` is not
enough, because `_attribute_segments` in `coordinator.py` attributes a
segment to its parent listing when the unit itself is untracked, so a
same-unit, different-parent record could be attributed to a different
tracked listing. For a code-less qualifier, the by-ID record's parsed
ID and association set must equal the snapshot's. Otherwise the action is refused
with `identity_conflict`.

## R10 — Lock scope and the newer-refresh branch

### R10 decision

Hold the existing dispatch lock from the first witness check, through the
read and authorization, to the write's `before_send`. This is the same
scope 012 uses for its note read. `before_write` reruns:

- `_check_recovery_context`;
- `classify_write_refusal`, which must return no refusal or
  `missing_current_code` only, so any baseline, `_unstored`, continuity,
  or ambiguity refusal fails the action;
- the v3-only and provenance checks;
- a check that the latest successful outcome's attempt still equals the
  snapshot attempt used.

If a newer accepted refresh is visible:

- a note write fails closed;
- a status or custom-field write proceeds only if the standard guard
  passes and the standard route is `ROUTE_V3`;
- fallback provenance fails closed.

A newer failed or ambiguous refresh fails through the context check.

### R10 rationale

Under the lock a newer refresh cannot complete between the read and the
write, so the newer-refresh branch is defensive. It is still implemented
and tested by injecting an outcome, because FR-008 requires it and the
lock is an implementation detail.

## R11 — Note writes reuse the verified record

### R11 decision

Extend `add_reservation_note` and `_add_note_v3` with keyword-only
`prefetched_notes: Mapping[str, object] | None`. When it is set, skip
`_read_v3_notes` and echo every writable note field from the verified
record, as `_add_note_v3` does today. `None` is reserved for ordinary
writes. On the by-ID path the value is always a mapping: a missing or
`null` record `notes` becomes `{}`, a non-object `notes` refuses as
`malformed`, and non-string keys are dropped as in `_read_v3_notes`.
So a reservation without notes still gets exactly one GET. Extend the
`recovery_write` callable type in `execute_reservation_write` to accept
an optional `notes` argument:

- `_handle_add_note` forwards it.
- `_handle_set_reservation_status` and the custom-field handler in
  `__init__.py` ignore it.

### R11 rationale

FR-010 permits one read, and FR-014 supersedes the separate note read on
this path only.

## R12 — Write mode

### R12 decision

Use exactly the 012 recovered-write transport mode, `single_attempt=True`
with `before_send=recheck`, and force the route to v3. The read and the
authorization run outside the `write()` `try` block. Every write failure
is then wrapped in a by-ID outcome error that carries:

- "found by ID, write failed";
- the qualifier;
- the existing safe text, either the `GuestyRecovery*` message or "may
  have reached Guesty; not retried".

All three handlers, including the custom-field handler in `__init__.py`,
translate the by-ID outcome errors explicitly.

## R13 — No retention

### R13 decision

The by-ID path writes to no shared state. Tests snapshot and compare:

- `coordinator.data`;
- `identity.baseline` (including `candidates` and every code map);
- the Store save count;
- `fallback_served_ids` and `fallback_sourced_ids`;
- the snapshot itself;
- `api_client.confirmed_absent_reservations`;
- the 013 adapter's state.

## R14 — Outcomes and privacy

### R14 decision

Surface outcomes as `HomeAssistantError` translations or messages built
only from fixed text, the reservation ID, the action type, the qualifier
(`absent` or `codeless`), and the category:

- **Found by ID**: written, or write failed.
- **Not found by ID**: not found, or mismatched.
- **Found but refused**: `malformed`, `no_code`, `disallowed_status`,
  `identity_conflict`, or `guard_witness`.
- **Read failure**: `http`, `rate_limit`, `authentication`, or
  `transport`.
- **Pre-read ineligible**: `not_v3`, `fallback_provenance`, `rejected`,
  `confirmed_absent`, `evidence_unavailable`, or `not_qualifying`. These
  do not produce a new message. The original 012 refusal is re-raised
  unchanged (spec US2 scenario 7), and the category goes only to a
  DEBUG log containing the category and the reservation ID.

The reservation ID is already present in existing refusal messages, and
it is not a secret. Logs use the same text. A successful by-ID write
returns the action's normal service result, unchanged. Its outcome,
"found by ID, written", and its qualifier go to exactly one INFO log line
containing only the outcome class, the qualifier, the action type, and
the reservation ID. Diagnostics are unchanged:
no counters are added, so an outcome leaves no trace.

### R14 rationale

FR-012 and SC-006 require these outcomes to be distinguishable. The
sentinel tests in `tests/test_privacy_logging.py` already provide the
pattern.

## R15 — Agent context file

### R15 decision

Do not modify `.github/copilot-instructions.md`. The plan adds no
language, framework, storage, or tool. The feature 012 plan commit also
left the file unchanged.

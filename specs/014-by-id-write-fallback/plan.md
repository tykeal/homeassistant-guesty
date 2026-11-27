<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: By-ID Reservation Write Fallback

**Date**: 2026-11-27 | **Spec**: [spec.md](spec.md)
**Input**: `specs/014-by-id-write-fallback/spec.md`
**Tracking Issue**: [#321](https://github.com/tykeal/homeassistant-guesty/issues/321)

## Summary

Extend feature 012 recovery for the three reservation write actions
(note, status, reservation custom field). After the 012 qualifying refresh
succeeds, the target may still be absent from the accepted v3 result, or
present there without a confirmation code. In that case, and only when
every other 012 guard still passes, issue one single-attempt
`GET /reservations-v3?reservationIds={id}`. Authorize the one v3 write
only if the response is exactly one strictly valid record:

- its identifier matches the target exactly;
- it passes feature 013 validation;
- its status is in the configured selection and is not `alteration`;
- it carries a non-empty string code;
- the code conflicts with no identity evidence.

The read and the write happen under the existing 012 dispatch lock, with
the witness rechecked before the write. The by-ID record is never
published, cached, added to current writable identities, or persisted.
Every other outcome fails closed with a privacy-safe category, and nothing
is retried.

The authorization step depends on feature 013 interfaces that do not
exist on `main` yet (validation rules, rejected-ID retention, and
conflict-only evidence). Until they merge, the design's availability
adapter reports that evidence as unavailable. The fallback then issues
no read and re-raises the original 012 refusal, so user-visible 012
behavior is unchanged. See
[013 dependency](#feature-013-dependency).

## Technical Context

**Language/Version**: Python 3.14+ (`pyproject.toml` requires `>=3.14.2`)
**Primary Dependencies**: Home Assistant `>=2026.4.0` (locked
environment `2026.10.4`), httpx, voluptuous
**Storage**: None new. The by-ID record and its outcome are in memory for
one action attempt only. The existing identity `Store` format and contents
are unchanged.
**Testing**: pytest, pytest-asyncio, pytest-homeassistant-custom-component,
respx, pytest-freezer; run through `uv`
**Target Platform**: Home Assistant custom integration
**Project Type**: Python Home Assistant integration
**Performance Goals**: Per action attempt, at most one 012 qualifying
refresh (unchanged), at most one by-ID read, and at most one v3 write.
No retry, cooldown, or cache is added.
**Constraints**:

- Fail closed.
- v3 route only.
- No legacy call is added or re-routed.
- Single-attempt read with no 401 renewal and no retry on 429, 5xx, or
  transport errors.
- No codes, PINs, guest data, credentials, or raw bodies in errors, logs,
  or diagnostics.
- No change to identity, continuity, ambiguity, routing, or publication
  state.

**Scale/Scope**: Three reservation write services, one configuration entry
per coordinator. No new service, option, entity, or storage format.

## Constitution Check

Pre-research gate: **PASS**.

- **Code quality/TDD**: Each phase starts with failing tests covering the
  API read contract, the coordinator snapshot, the authorization rules,
  and every spec failure mode, including the #223 synthetic regression.
  Ruff, mypy (strict), docstrings, and full-suite gates remain required.
- **API client boundary**: The strict by-ID read and parser live in
  `custom_components/guesty/api/actions.py` with no Home Assistant
  imports. Orchestration and authorization live in
  `custom_components/guesty/actions.py`, and the accepted-refresh snapshot
  lives in `custom_components/guesty/coordinator.py`.
- **Security/privacy**: The read runs under `private_http_logging(True)`.
  Errors carry only a fixed category. No new value-bearing log lines are
  added. Diagnostics are unchanged.
- **Performance**: One extra GET only in the narrow fallback case. The
  normal cache-hit path and the 012 refresh are untouched.
- **Phased development**: Three functional phases with a green CI
  checkpoint at each boundary, plus a final documentation and validation
  gate (see [phase boundaries](#implementation-phase-boundaries)).
- **Licensing/commit gates**: Planning documents carry SPDX headers, and
  implementation follows REUSE, lint, type, and DCO rules.
- No constitution violation is required.

## Phase 0 — Research Findings

`research.md` records the live-code findings, decisions, and alternatives.
No NEEDS CLARIFICATION remains. The decisions that matter most:

1. **013 is a hard dependency for authorization.** The live code has no
   conflict-only evidence (`grep` finds none), retains no per-refresh
   rejected-ID set, and does not apply 013 FR-008d raw-segment
   validation. FR-002, FR-005(b), and FR-006 cannot be satisfied without
   them, so the authorization phase is gated on 013's interfaces.
2. **The existing read helper is too permissive.** `_v3_record` in
   `api/actions.py` accepts a bare dict as the record, accepts a bare
   list, and returns the first match among extras. That violates the
   spec's "exactly one record, proven complete" rule. `_read_v3_notes`
   also uses the default retry policy. A new strict, single-attempt read
   method is required, and the note path's existing behavior stays as is.
3. **The identity baseline is bounded** (`MAX_BASELINE_CODES` = 5000, with
   earliest-checkout eviction in `identity._bounded`), so it cannot serve
   as "the qualifying refresh's current identities" for conflict checks.
   The coordinator keeps an in-memory snapshot of the accepted refresh's
   full observed code map, bound to that refresh's attempt number.
4. **Missing-code refusal implies the other guards passed.**
   `ReservationIdentityGuard.classify_write_refusal` checks, in order: baseline
   usable, `_unstored`, continuity, ambiguity, then current IDs. A
   `missing_current_code` refusal therefore means every other guard
   passed, and the 012 witness check is the natural hook point.

## Phase 1 — Design

### Behavior and synchronization

Steps 1–3 are the existing 012 flow and are unchanged.

1. **Preflight**: Service schemas and validators run unchanged.
   `execute_reservation_write` (`actions.py`) runs `guard_reservation_write`.
   A non-miss refusal is unchanged. A `ReservationIdentityMissError` follows
   012: known-unroutable pre-check, source and liveness checks, witness
   capture, and `coordinator.async_refresh_for_write_miss`.
2. **Qualifying refresh**: Unchanged. A failed, superseded, or rejected
   refresh fails exactly as in 012, and no by-ID read is issued.
3. **Dispatch lock**: `_execute_recovered_reservation_write` acquires
   `coordinator.async_write_miss_dispatch_lock()` as today.
4. **Witness split**: Refactor `_check_recovery_witness` into two parts:
   - `_check_recovery_context`: options, settings, entry liveness, and
     accepted-outcome checks.
   - The existing identity guard and route selection.

   If the identity guard passes, 012 continues unchanged. If it raises
   `ReservationIdentityMissError` (`ReservationIdentityAbsentError` or
   `ReservationIdentityPresentError`), the action enters the by-ID path.
   Any other refusal keeps its 012 error. The by-ID path is not entered
   when the 012 path would write.
5. **By-ID preconditions** (no request yet). If any check fails, no read
   is issued and the original `ReservationIdentityMissError` (the
   unchanged 012 refusal) is re-raised, as spec US2 scenario 7 requires.
   The ineligibility category is written only to a privacy-safe DEBUG
   log containing the category and the reservation ID. The checks run in
   this order:
   - The action is one of the three reservation writes.
   - `api_client.reservations_api == RESERVATIONS_API_V3` and the
     witness settings path is v3.
   - The target is in neither `fallback_served_ids` nor
     `fallback_sourced_ids`, and the snapshot's own `fallback_served`,
     `fallback_sourced`, and `fallback_observed` sets don't contain it
     either, and it is not in the fallback source's in-memory session
     legacy-observed set (`api_client.legacy_observed_reservations`).
     `fallback_observed` holds every ID a legacy lookup returned in that
     refresh, including one it declined to republish because its status
     left the configured set (`_lookup` in `reservations_fallback.py`
     pops such IDs from `_served` and `_retained`). Because legacy
     lookups are rate-gated (`LEGACY_FALLBACK_RECHECK_SECONDS`), the
     qualifying refresh may not repeat an earlier lookup, so the session
     set retains every legacy observation for the life of the client.
     It is never persisted and is cleared with the client on unload.
     If it would exceed its bound, it is marked incomplete and every
     by-ID attempt is ineligible as `evidence_unavailable`. Together
     these are the served, current, and retained fallback provenance; a
     legacy observation is evidence that the sources disagree, so it
     refuses. Durable
     `IdentityBaseline.candidates` hints are not provenance, because they
     record earlier v3 observations (see research R4), and they are never
     modified.
   - The coordinator's accepted-refresh snapshot exists and belongs to the
     latest successful outcome's `attempt`.
   - The target is not in the snapshot's rejected IDs. This check runs
     before qualification because the returned IDs include rejected ones.
   - The target qualifies: it is absent from the snapshot's raw v3
     search IDs (`returned_ids`, never the merged `observed_ids`), or
     it is an accepted v3 record whose code is `None` or `""`.
     A non-string code, an `alteration` record, or fallback provenance
     does not qualify. A coded record can still reach this point when
     the bounded baseline evicted its code, and it is refused as
     `not_qualifying`.
   - The target is not in `api_client.confirmed_absent_reservations`,
     the snapshot's `fallback_absent`, or the session legacy-retired set
     (`api_client.legacy_retired_reservations`). The live confirmed-absent
     set is pruned by `_prune` at the next refresh once the identity
     baseline drops the candidate, so a retirement made by an earlier
     scheduled refresh would otherwise be forgotten by the qualifying
     refresh. `_retire` (which `retire_candidate` also uses) records
     into the session set, which is bounded, in-memory, never pruned,
     and marked incomplete on overflow (then `evidence_unavailable`).
   - The 013 adapter reports conflict-only evidence available within its
     bound and has none for the target.

   If any check cannot be evaluated, the action is refused without a read.
6. **Single read** under the held lock:
   `actions_client.read_reservation_by_id(target)` sends exactly one
   `GET /reservations-v3` with `params={"reservationIds": target}`,
   `single_attempt=True`, and `private_http_logging(True)`. It has no 401
   renewal and no retry. Failures map to `GuestyByIdReadError(category)`.
7. **Strict parse and authorization** (no request):
   - The envelope is a JSON object whose `results` is a list of exactly
     one object, and `reservationId` equals the target exactly.
     - An empty `results` list is "not found".
     - One object whose identifier differs is "not found (mismatched)".
     - A bare dict, a bare list, extra or duplicate items, a non-object
       item, or a non-JSON body is "malformed".
   - The status must be allowed. This check runs on the raw record,
     before any parsing, so `map_v3_status` never sees, or logs, an
     untrusted value. A missing or non-string status is refused. The
     stripped, lowercased raw status is looked up
     directly in `V3_STATUS_MAP`. The result must be in the configured
     selection, `ACTIONABLE_STATUSES`, which matches how an accepted
     refresh record would be mapped: `completed` maps to `checked_out`
     and `cancelled` maps to `canceled`. It must never be `alteration`.
     An unknown or missing status is refused.
   - The record must pass the 013 single-record validator (including
     FR-008d for every raw segment and the facts the action needs) and
     `GuestyReservation.from_v3_api_dict`.
   - The code must be a non-empty string after the same normalization
     `observe_codes` applies.
   - There must be no identity conflict across these maps:
     - the snapshot's full observed code map;
     - the snapshot's `fallback_observed_codes`, from `observe_codes`
       over the refresh's `observed_reservations`, including legacy
       records not republished because their status left the configured
       set (their codes reach neither the accepted records nor the
       baseline `fallback_codes`);
     - the baseline `codes`, `legacy_codes`, `bootstrap_codes`, and
       `fallback_codes`;
     - 013 conflict-only evidence.

     A conflict is the code mapping to another ID, or the target mapping
     to another code. A refusal follows if the evidence is unavailable:
     - there is no valid snapshot;
     - the baseline is not loaded or not usable, or is `_unstored`;
     - the 013 adapter is unavailable or over its bound.

     A `None` legacy, bootstrap, or fallback sub-map means "no such
     evidence" and counts as empty, as it does in the existing
     comparison.
   - For a code-less snapshot record (FR-007), the identifier and the
     verified listing-association set, as `(listing_id,
     parent_listing_id)` pairs (research R9), must equal the
     snapshot's.
8. **Pre-write recheck**: `before_write` reruns these checks:
   - `_check_recovery_context`.
   - `coordinator.identity.classify_write_refusal`. The result must be
     either no refusal or `missing_current_code` only. Any baseline,
     `_unstored`, continuity, or ambiguity refusal fails the action.
   - The v3-only and provenance conditions.
   - An unchanged-snapshot-attempt check, with these outcomes:
     - If the accepted attempt is unchanged, the by-ID evidence stands.
     - If a newer accepted refresh exists, the evidence is discarded.
       A note write fails closed. A status or custom-field write
       proceeds only if `guard_reservation_write` now passes and
       `select_reservation_write_route` returns V3 with no fallback
       provenance. Otherwise the action fails closed.

   A newer failed or ambiguous refresh, a reload, an options replacement,
   or a source change fails the action. No second read is ever issued.
9. **One write**: v3 only, in single-attempt recovery mode
   (`single_attempt=True`, `before_send=recheck`). The read and the
   authorization run inside the lock but **outside** the `write()`
   `try` block of `_execute_recovered_reservation_write`, so their
   outcome errors are never relabeled as write failures. The by-ID
   recheck refusal raised from `before_send`, a `_RecoveryGuardRefusal`
   that is also a `GuestyApiError`, is caught **first** and reported as
   "found but refused" with category `guard_witness`. Only then are the
   remaining write errors
   (the `GuestyRecovery*` errors, `GuestyRecoveryWriteError`, and any
   other `GuestyApiError`) are wrapped in one new by-ID outcome error
   that carries:
   - the outcome class "found by ID, write failed";
   - the qualifier (`absent` or `codeless`);
   - the existing safe write-failure text.

   The note and status handlers in `actions.py`, and the custom-field
   handler `_async_handle_set_custom_field` in `__init__.py`, translate
   this error, along with the read, not-found, and refusal outcome
   errors, explicitly, so none of them falls into a generic
   `GuestyApiError` branch. A successful write returns the normal
   service result, and the outcome "found by ID, written", with its
   qualifier, goes to one privacy-safe INFO log line.
   - Note writes call `add_reservation_note` with
     `prefetched_notes=<verified record notes>`, which skips
     `_read_v3_notes`.
   - Status and custom-field writes are unchanged apart from the v3
     route being forced.
   - An unknown post-send outcome is "may have reached Guesty; not
     retried", as in 012.
10. **No retention**: Discard the record, code, and outcome when the
    action ends. Nothing changes in coordinator data, `current_ids`, the
    baseline or its Store, fallback candidates, conflict-only evidence,
    continuity, ambiguity, or routing.

Overlapping actions each run their own path (FR-011). The existing
dispatch lock serializes them, which is not a shared result or a
cooldown. Under that lock a newer refresh cannot complete between the
read and the write. The newer-refresh branch of step 8 is still
implemented and tested defensively by injecting an outcome.

### Feature 013 dependency

- **FR-002, rejected in the refresh**: needs the rejected-ID set for
  each accepted refresh, including FR-008d raw-segment rejection. Until
  then, the interim rejected set (raw minus parsed) is collected, but the
  adapter still reports unavailable.
- **FR-002 and FR-006, conflict-only evidence**: needs a query exposing
  availability, a bound-exceeded flag, and code↔ID lookup. Until then,
  the evidence is unavailable, so the action refuses before reading.
- **FR-005(b), record validation**: needs a single-record validator that
  applies the accepted-refresh rules. Until then, the evidence is
  unavailable, so the action refuses before reading.

The interface is defined in `contracts/guesty-by-id-write-fallback.md`.
Phases 1 and 2 can merge before 013. Phase 3 enables dispatch only once
013 supplies these interfaces. If 013 ships them under different names,
adapt the thin adapter and leave the contract semantics unchanged.

### Constitution Check — post-design gate

**PASS.**

- The design adds no persistence, polling, cooldown, option, or entity.
- The HTTP read lives behind the API client boundary.
- The design reuses the 012 lock and witness rather than weakening them.
- Every uncertain branch refuses.
- The new client method is the only new request site, and tests pin its
  request count.

### Project Structure

#### Documentation (this feature)

```text
specs/014-by-id-write-fallback/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
└── contracts/
    └── guesty-by-id-write-fallback.md
```

`tasks.md` is produced in the separate Speckit tasks stage.

#### Source and tests (future implementation only)

```text
custom_components/guesty/
├── actions.py            # witness split, by-ID orchestration, outcomes,
│                         # recovery_write notes argument
├── __init__.py           # custom-field handler: by-ID outcome errors
├── coordinator.py        # accepted-refresh snapshot bound to attempt
├── identity.py           # read-only conflict query over baseline maps
└── api/
    ├── actions.py        # read_reservation_by_id + strict parser;
    │                     # add_reservation_note(prefetched_notes=...)
    ├── exceptions.py     # GuestyByIdReadError(category)
    ├── client.py         # legacy_observed/retired_reservations
    ├── models.py         # ReservationFetchResult returned_ids,
    │                     # rejected_ids, fallback_observed
    ├── reservations_fallback.py  # bounded session observed/retired
    │                             # sets
    └── reservations_v3.py  # returned/rejected IDs in _map_results;
                            # fallback_observed carried in replace

tests/
├── test_actions.py                 # note/status by-ID paths, #223
├── test_custom_field_service.py    # custom-field by-ID path, #223
├── test_coordinator.py             # snapshot lifecycle and binding
├── test_identity.py                # conflict query, no mutation
├── test_privacy_logging.py         # sentinel codes/PINs absent
├── test_diagnostics.py             # diagnostics unchanged, no values
└── api/
    ├── test_actions.py             # strict read/parse contract
    ├── test_client.py              # single-attempt read transport
    ├── test_reservations_fallback.py  # session observed/retired sets
    └── test_reservations_v3.py     # returned/rejected/observed IDs
```

**Structure Decision**: Keep the feature inside the existing service,
coordinator, identity, and API client modules. No new module or package is
needed. A private helper set in `actions.py` (for example
`_attempt_by_id_write`, `_qualify_by_id_target`, and
`_authorize_by_id_record`) keeps the 012 functions readable.

### Implementation phase boundaries

1. **API by-ID read** (no 013 dependency):
   - Add `GuestyActionsClient.read_reservation_by_id` and the strict
     parser.
   - Add `GuestyByIdReadError` with categories `http`, `rate_limit`,
     `authentication` (401/403, token auth/permission or
     unclassified token failure; other token mappings per research R5),
     `transport`, `malformed`,
     `not_found`, and `mismatched`.
   - Add the `prefetched_notes` path to the v3 note write.

   Tests in `tests/api/test_actions.py` and `tests/api/test_client.py`.
   No caller uses these yet.
2. **Accepted-refresh snapshot** (no 013 dependency):
   - Add `ReservationFetchResult.returned_ids` (every raw string
     `reservationId` on the v3 search pages) and `rejected_ids` (raw
     minus parsed), both set in `reservations_v3._map_results`. Today
     `observed_ids` holds parsed IDs only and is later merged with legacy
     lookup IDs, so neither field can be derived from it.
   - Add `ReservationFetchResult.fallback_observed`, set from
     `FallbackOutcome.observed` in the `replace` call in
     `reservations_v3.py` (it does not reach the coordinator today).
   - Add the bounded, in-memory session legacy-observed set to
     `ReservationFallbackSource`, updated on every successful `_lookup`
     whatever the status, and expose it read-only as
     `GuestyApiClient.legacy_observed_reservations`, mirroring
     `confirmed_absent_reservations`.
   - Add a matching bounded, never-pruned session legacy-retired set,
     recorded in `_retire`, exposed as
     `GuestyApiClient.legacy_retired_reservations`.
   - Add the coordinator snapshot, recorded only on the accepted path and
     bound to the outcome attempt.
   - Add a read-only identity conflict query.

   Tests in `tests/api/test_reservations_v3.py`,
   `tests/test_coordinator.py`, and `tests/test_identity.py`. No behavior
   change.
3. **Authorized by-ID dispatch** (gated on 013 interfaces):
   - Witness split.
   - Qualification, read, authorization, recheck, and write for all three
     actions.
   - Outcome errors.
   - The #223 regression.
   - Refusal matrix, privacy, and no-retention tests.

   Before 013 lands, this phase can merge only with the adapter returning
   "unavailable", with tests proving the action refuses with no read.
   Full-flow tests use a test double for the 013 adapter, and are rerun
   against the real 013 interfaces when 013 merges.

**Final gate**: README/operator guidance on the new outcome messages, a
release note, and the full validation results.

Each phase ends at a checkpoint with all CI tests green before the next
begins. Red–green unit testing stays mandatory within each phase.

### Validation plan (implementation stage)

Tests come first in each phase. Required cases are listed in the contract;
the key ones:

- **#223 synthetic regression**, for each write type:
  - **Setup**:
    - The persisted baseline is usable and continuity is confirmed.
    - The configured source is v3 and today's date is frozen.
    - A same-day `confirmed` reservation has `createdAt` after its
      localized check-in, and the v3 search omits it. Every other search
      result carries a code.
    - The by-ID response is one record with a fresh code, allowed status,
      and valid stay segment.
  - **Expected**:
    - Exactly one search refresh, one by-ID GET, and one v3 PUT: notes,
      status, or `/reservations-v3/{id}/custom-fields` (for example a
      door code).
    - The outcome is "found by ID (absent from search), written",
      asserted through the INFO log with `caplog`, and the service
      result is the normal success result.
    - Coordinator data, `current_ids()`, the baseline and its Store
      saves, and fallback candidates are unchanged from their state
      after the qualifying refresh, captured just before the by-ID read
      (SC-004). The refresh's own legitimate changes are not part of
      the comparison.
- **Code-less variant**: The target is present in the search with
  `confirmationCode` missing or `""` and the by-ID record agrees on ID
  and listings, so the write succeeds and is labeled "present without
  code". Listing disagreement refuses.
- **Refusals with zero writes**, one test per spec failure mode
  (SC-002):
  - a pre-read guard failure, including an invalid identifier or input
    (zero refreshes, reads, and writes);
  - each pre-read ineligibility (non-v3 source, fallback provenance,
    rejected, conflict-only evidence, confirmed absent, evidence
    unavailable), asserting zero reads and the unchanged 012 refusal
    text;
  - a non-v3 source;
  - fallback provenance, including a target a legacy lookup returned
    at a status outside the configured set and did not republish
    (`fallback_observed`) while the v3 search omitted it, and the same
    observation made in an earlier refresh when the qualifying
    refresh's lookup was not due (session legacy-observed set);
  - a target retired by an earlier scheduled refresh and already pruned
    from the live confirmed-absent set (session legacy-retired set);
  - an incomplete session observed or retired set;
  - rejected in the refresh;
  - conflict-only evidence present, unavailable, or over its bound;
  - confirmed absent;
  - not found;
  - mismatched;
  - multiple or duplicate records;
  - a bare dict or bare list envelope;
  - malformed record or segment;
  - no code (missing, `""`, or non-string);
  - disallowed status (`alteration`, an unknown value, or missing);
  - a code mapped to another ID in each evidence map;
  - the ID mapped to another code;
  - read HTTP 4xx/5xx, 401, 403, 429, timeout, and connect errors;
  - token acquisition failures during the read: an auth rejection
    (`authentication`), the local token budget (`rate_limit`), a token
    endpoint 429 or other non-200 (`http`), and a connection error
    (`transport`);
  - a recheck refusal, asserted as `guard_witness` and not as "write
    failed";
  - a missing or non-string (numeric, list, or object) status;
  - a baseline-unstored, continuity, or ambiguity change between the
    read and the write;
  - a witness change during the read (reload, options replacement,
    source change, failed or ambiguous newer refresh);
  - a newer accepted refresh for a note write;
  - a newer refresh giving fallback provenance.
- **Request counts** (SC-003): One by-ID GET in every case where the
  read is dispatched, including failures and note writes (also a note
  write on a record with missing or `null` notes), with zero retries. A
  token-acquisition failure exits `_request` before the read is sent,
  so it asserts zero by-ID GETs and the read-failure outcome. Assert
  using respx call counts, and assert that a 401 triggers no token
  request.
- **Privacy** (SC-006): Inject sentinel codes, PINs, guest names, raw
  body markers, and an unknown sentinel status. Assert they are absent
  from `caplog`, the raised error text, and
  `async_get_config_entry_diagnostics`.
- **Overlap** (FR-011): Two concurrent actions, on the same target and
  on different targets, each issue their own
  single read and decide independently.
- **Regression**: The existing 012 suites in `tests/test_actions.py` and
  `tests/test_custom_field_service.py` pass unchanged, except for
  documented expectation updates where 012 previously refused a
  qualifying absent or code-less target.
- **Commands**: Run `uv run pytest tests/`,
  `uv run ruff check custom_components/ tests/ scripts/`,
  `uv run --group releasenotes mypy custom_components/ tests/ scripts/`,
  and the pre-commit and REUSE checks. Verify HA compatibility against
  2026.4.0 and the locked 2026.10.4, as in 012.
- **Live verification**: No live Guesty verification. Whether the real
  by-ID read returns #223-style reservations remains a separate activity.

### Agent context

`.specify/scripts/bash/update-agent-context.sh` adds new technologies from
the plan's Technical Context. This feature introduces none: the language,
frameworks, testing tools, and storage are unchanged. As with the feature
012 plan, `.github/copilot-instructions.md` is intentionally left
unchanged.

## Planning Scope

This plan covers design only. Tasks and implementation belong to their
separate Speckit stages.

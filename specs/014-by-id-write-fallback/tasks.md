<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- Keep each task and its requirement references together. -->
<!-- markdownlint-disable MD013 -->

# Tasks: By-ID Reservation Write Fallback

**Input**: Design documents in `specs/014-by-id-write-fallback/`
(spec.md, plan.md, research.md, data-model.md,
contracts/guesty-by-id-write-fallback.md, quickstart.md)

**Tracking Issue**: [#321](https://github.com/tykeal/homeassistant-guesty/issues/321);
related #223, #293, #314 and #315

**Testing**: Tests are required by the specification (SC-001–SC-006), the
plan's validation plan and the project constitution. For every
production-code task, first add the listed tests and observe each new or
changed behavior fail for the intended reason before implementing the
minimum change. Tests marked as **baseline controls** protect existing
behavior: run and record them green before the code change and keep them
green afterward. Observe each red failure before implementation; the test
may stay uncommitted during that observation. Do not commit intentionally
failing tests or code, so every commit stays runnable.

**Mocked refreshes**: After T011, every full-flow test that mocks
`get_reservations` (T024–T030, T036–T040, T044–T046) passes explicit
`returned_ids=` (and `rejected_ids=` where relevant) to
`make_fetch_result`, which forwards them as `ReservationFetchResult`
fields. The default `returned_ids=()` makes every missing-code target
`absent`, so `codeless` and `not_qualifying` cases must list the target
in `returned_ids`.

**[P]**: The task adds separate test classes or touches separate files and
has no dependency on an incomplete task. Parallel test tasks may share a
test module when each adds its own class.

**Organization**: Tasks follow the plan's phase boundaries, with one
harmless deviation: the witness split and the evidence port (plan phase
3) move into foundational Phase 4 because they are a pure refactor with
no behavior change. Phases 2–4 are
foundational and land with **no user-visible behavior change**. Phases 5–7
are the user stories (US2 is sequenced before US1 because both are P1 and
every refusal must exist and be tested before any by-ID write can be
dispatched). Phase 8 enables the fallback against feature 013 and is
**BLOCKED**. IDs give traceability; dependencies are stated explicitly
below. Every task stays unchecked until its work and verification are
complete.

**Phase gates**: Each phase ends with a gate task. Record the gate commit
SHA, the CI run URLs and their conclusions for that SHA, and the local
validation output. A skipped, cancelled, pending, missing or failed CI job
is not a pass, and a later green run on a different SHA does not establish
an earlier gate.

## Feature 013 dependency (read before starting any task)

Feature 013 (`specs/013-reservation-data-validation/`) is **spec-only** on
`main`: no plan, no tasks, no code. This feature needs three 013 pieces
that do not exist yet (plan § Feature 013 dependency; contract § Required
feature 013 interface):

1. the per-accepted-refresh **rejected-ID set**, including 013 FR-008d
   raw-segment rejections;
2. **conflict-only evidence** with `available()` (false when the 013
   FR-008c bound is exceeded) and code↔ID lookups;
3. the **single-record validator** `validate_record(raw, action)` applying
   013's accepted-refresh rules.

Rules for every implementation agent:

- **Do not implement, approximate or stub any of these three pieces in
  production code.** Do not treat today's
  `GuestyReservation.from_v3_api_dict` as the 013 validator, do not treat
  "no conflict-only store exists" as "no conflict", and do not add any
  conflict-only storage (research R1). The only pre-013 rejected-ID data is
  the plan's interim raw-minus-parsed set (T011).
- Tasks marked **013-gated (double only)** may be implemented and merged
  before 013 **only** while the production adapter
  `_conflict_evidence()` returns `UnavailableConflictEvidence`. Their
  full-flow tests reach the by-ID path exclusively by patching
  `custom_components.guesty.actions._conflict_evidence` with the test
  double `FakeConflictEvidence` from `tests/conftest.py`. No production
  code path may construct an available adapter.
- Tasks marked **BLOCKED on 013** must not start until 013's interfaces
  are merged on `main`. If they are still missing, stop and report; do not
  create them here.
- Fail-closed reading used throughout: the adapter reports available only
  when **all three** 013 interfaces are present and within bound. If any
  one is missing, every qualifying action keeps its unchanged feature 012
  refusal with no read (`evidence_unavailable`).

## Phase 1: Setup prerequisite gate

**Purpose**: Use the existing integration, test suites and locked
dependencies. No new dependency, module, option, entity or storage format
is added.

- [ ] T001 Record the baseline on the current `main` SHA: run `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/` and `uv run --group releasenotes mypy custom_components/ tests/ scripts/`, and record that they pass. Confirm `specs/013-reservation-data-validation/` still holds only `spec.md` and `checklists/`, and that `grep -rni "conflict.only" custom_components/` finds nothing. Record which 013 pieces exist (expected: none) in the PR description of the first implementation PR. Do not modify `custom_components/guesty/` in this task.

**Checkpoint**: Baseline green; 013 status recorded.

## Phase 2: Foundational A — Strict by-ID read (plan phase 1; no caller, no behavior change)

**Purpose**: Add the strict, single-attempt v3 read, its error categories,
and the prefetched-notes path to the note write. Nothing calls them yet.

- [ ] T002 [P] Add failing tests in `tests/api/test_exceptions.py`, new class `TestGuestyByIdReadError`: `test_categories_are_the_fixed_set` (exactly `http`, `rate_limit`, `authentication`, `transport`, `malformed`, `not_found`, `mismatched`), `test_unknown_category_is_rejected` (raises `ValueError`), `test_message_is_fixed_text_per_category` (message built only from fixed text and `target_id`; 403 wording says credentials were rejected or lack permission), `test_is_api_error_subclass` (subclass of `GuestyApiError`, not of `GuestyActionError`) and `test_message_never_contains_injected_values` (research R5; FR-012).
- [ ] T003 Implement `GuestyByIdReadError(category: str, target_id: str)` and the module constant `BY_ID_READ_CATEGORIES` in `custom_components/guesty/api/exceptions.py`, with a `category` attribute and one fixed message per category; no Home Assistant import (plan phase 1; research R5).
- [ ] T004 [P] Add failing tests in `tests/api/test_actions.py`, new class `TestReadReservationById`, using respx and a real `GuestyApiClient`: `test_sends_one_get_with_only_reservation_ids_param` (exactly one `GET /reservations-v3` with the single query parameter `reservationIds=<id>`), `test_returns_the_single_matching_raw_record`, `test_empty_results_is_not_found`, `test_single_other_id_is_mismatched`, `test_missing_or_non_string_reservation_id_is_malformed` (an item carrying only `id` or `_id` is malformed; they are never consulted), and a parametrized `test_unprovable_envelope_is_malformed` covering two records, duplicate matching records, a bare dict, a bare list, `results` missing, non-list `results`, a non-object item, non-JSON, `pagination: null`, `pagination: []`, `pagination: {}`, `hasMore: null`, `hasMore: "true"` and `hasMore: true`; plus `test_absent_pagination_or_has_more_false_is_accepted` and `test_blank_reservation_id_sends_nothing` (raises `ValueError`, zero requests) (FR-004, FR-005(a); research R5, R6; contract § Read contract).
- [ ] T005 [P] Add failing tests in `tests/api/test_actions.py`, new class `TestReadReservationByIdFailures`: parametrized `test_http_status_maps_to_category` (400, 404, 500, 503 → `http`; 401 and 403 → `authentication`; 429 → `rate_limit`), each asserting exactly one by-ID GET and zero retries; `test_401_does_not_renew_token` (zero additional token requests); `test_429_and_5xx_do_not_sleep_or_retry`; `test_timeout_connect_and_other_transport_errors_are_transport`; parametrized `test_token_failure_category` (token auth rejection and `GuestyPermissionError` → `authentication`, local budget `GuestyRateLimitError` → `rate_limit`, token endpoint 429 or other non-200 / bad body `GuestyResponseError` → `http`, token `GuestyConnectionError` or `httpx.TransportError` → `transport`, any other token exception → `authentication`), each asserting **zero** by-ID GETs; and `test_errors_and_logs_carry_no_body_or_exception_text` (sentinel body marker and exception text absent from `str(exc)` and `caplog`). In `tests/api/test_client.py`, add baseline-control class `TestSingleAttemptRead` pinning today's `GuestyApiClient._request("GET", ..., single_attempt=True)` behavior (401/429/5xx returned unretried; connect/timeout → `GuestyConnectionError`) (FR-004, SC-003; research R5).
- [ ] T006 Implement `GuestyActionsClient.read_reservation_by_id(reservation_id: str) -> dict[str, object]` and a private strict parser (for example `_strict_v3_by_id_record`) in `custom_components/guesty/api/actions.py`: one `self._api_client._request("GET", RESERVATIONS_V3_ENDPOINT, params={"reservationIds": reservation_id}, single_attempt=True)` under `private_http_logging(True)`, every failure mapped to `GuestyByIdReadError` per the research R5 table and R6 envelope rules; a blank or non-string `reservation_id` raises `ValueError` before any request (T004 `test_blank_reservation_id_sends_nothing`). Leave `_read_v3_notes`, `_v3_record` and `_identifies` unchanged (existing `TestV3NoteReadIdentity` and `TestV3NoteWrites` are baseline controls) (FR-004, FR-005(a), FR-013).
- [ ] T007 [P] Add failing tests in `tests/api/test_actions.py`, new class `TestPrefetchedNoteWrite`: `test_prefetched_notes_issue_no_read` (zero GETs, one `PUT /reservations-v3/{id}/notes`), `test_prefetched_notes_echo_every_field_and_append` (body is every prefetched key plus `other` joined with `NOTE_SEPARATOR`), `test_empty_prefetched_notes_write_only_other`, `test_before_write_runs_once_before_put` with `single_attempt=True`, `test_prefetched_notes_refuse_legacy_route` (with `route=WRITE_ROUTE_LEGACY`, or a client whose `_writes_legacy()` is true, raises `ValueError` with zero requests; the check runs first, before `_refuse_when_retired` and any routing) and baseline control `test_none_prefetched_notes_keeps_the_read` (FR-010; research R11).
- [ ] T008 Implement keyword-only `prefetched_notes: Mapping[str, object] | None = None` on `GuestyActionsClient.add_reservation_note` and `_add_note_v3` in `custom_components/guesty/api/actions.py`: when set, skip `_read_v3_notes` and the post-read `before_write()` call, build the body from the mapping, and keep only the `before_send=before_write` call inside `_request` so the check runs exactly once; refuse (fail closed, `ValueError`, nothing sent) when combined with a legacy route or legacy-configured client, checked first, before `_refuse_when_retired` and any routing. `None` keeps today's behavior (FR-010; research R11).
- [ ] T009 Phase 2 gate: run `uv run pytest tests/api/test_actions.py tests/api/test_client.py tests/api/test_exceptions.py`, `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm all CI jobs are green on the gate SHA and record the evidence.

**Checkpoint**: The strict read and prefetched note write exist, are fully
tested, and have no caller. No user-visible change.

## Phase 3: Foundational B — Accepted-refresh evidence (plan phase 2; no behavior change)

**Purpose**: Carry the raw v3 returned/rejected IDs and legacy
observations to the coordinator, keep bounded session legacy evidence,
record the in-memory accepted-refresh snapshot and add a read-only
identity conflict query.

- [ ] T010 [P] Add failing tests in `tests/api/test_reservations_v3.py`, new class `TestV3ReturnedAndRejectedIds`: `test_map_results_records_every_raw_string_id` (`returned_ids` includes accepted and rejected records; non-string or missing `reservationId` values are excluded), `test_rejected_ids_are_raw_minus_parsed` (records missing `status` or without a usable stay segment), `test_ids_are_sorted_deduplicated_tuples`, `test_fetch_carries_fallback_observed_from_outcome` (`ReservationFetchResult.fallback_observed` equals `FallbackOutcome.observed`, including an out-of-selection legacy record), and baseline control `test_observed_ids_merge_is_unchanged` (data-model § `ReservationFetchResult` extensions).
- [ ] T011 Add `returned_ids`, `rejected_ids` and `fallback_observed` (all `tuple[str, ...] = ()`) to `ReservationFetchResult` in `custom_components/guesty/api/models.py` with docstrings; set `returned_ids` and the interim raw-minus-parsed `rejected_ids` in `_map_results` and `fallback_observed=outcome.observed` in the `replace` call of `V3ReservationSource.fetch` in `custom_components/guesty/api/reservations_v3.py`. The legacy source keeps the defaults. Do not add 013 FR-008d validation here (research R1, R3).
- [ ] T012 [P] Add failing tests in `tests/api/test_reservations_fallback.py`, new class `TestSessionLegacyEvidence`: `test_out_of_selection_lookup_is_recorded_as_observed` (and still popped from `_served`/`_retained`), `test_in_selection_lookup_is_recorded_as_observed`, `test_failed_lookup_is_not_observed`, `test_retire_and_retire_candidate_record_retired`, `test_prune_never_shrinks_session_retired_set` (an ID pruned from `confirmed_absent` stays retired), `test_overflow_marks_set_incomplete` for each set (property returns `None`, never silently drops), and `test_client_properties_are_read_only_frozensets` via `GuestyApiClient.legacy_observed_reservations` and `legacy_retired_reservations` (empty, complete frozenset when no fallback source exists) (FR-002, FR-003; research R4).
- [ ] T013 Implement the bounded in-memory session sets in `ReservationFallbackSource` (`custom_components/guesty/api/reservations_fallback.py`): record every successful `_lookup` ID whatever its status and every `_retire` ID; never prune them; on overflow mark the set incomplete. Add `MAX_LEGACY_SESSION_IDS` (5000, matching `MAX_BASELINE_CODES`) to `custom_components/guesty/api/const.py`. Expose `legacy_observed_reservations` and `legacy_retired_reservations` on `GuestyApiClient` in `custom_components/guesty/api/client.py` as `frozenset[str] | None`, where `None` means incomplete. Nothing is persisted.
- [ ] T014 [P] Add failing tests in `tests/test_identity.py`, new class `TestIdentityConflictQuery`, for a new read-only `ReservationIdentityGuard.check_identity_conflict(reservation_id, code)` returning `none`, `conflict` or `unavailable`: `test_code_mapped_to_other_id_conflicts` and `test_target_mapped_to_other_code_conflicts`, each parametrized over `codes`, `legacy_codes`, `bootstrap_codes` and `fallback_codes`; `test_same_id_same_code_is_consistent`; `test_unmapped_code_is_acceptable`; `test_none_sub_maps_count_as_empty`; `test_unloaded_unusable_or_unstored_baseline_is_unavailable`; and `test_query_mutates_nothing` (baseline maps and `candidates`, `_unstored`, `_ambiguous` and the `Store` save count unchanged) (FR-006, FR-009; research R8).
- [ ] T015 Implement `ReservationIdentityGuard.check_identity_conflict` in `custom_components/guesty/identity.py` as a pure read over the loaded baseline maps; it never calls `async_record_refresh`, `mark_ambiguous` or the `Store` (research R8).
- [ ] T016 Add failing tests in `tests/test_coordinator.py`, new class `TestAcceptedRefreshSnapshot`: `test_snapshot_recorded_only_on_accepted_refresh` (starting with no prior accepted refresh: none after `UpdateFailed`, identity abort, ambiguous refresh or `ReservationSearchRejected`), `test_snapshot_bound_to_outcome_attempt` (the accessor `accepted_refresh_snapshot()` returns it only when its `attempt` equals the latest successful `_write_miss_outcomes` entry with matching options, settings, entry and client; otherwise `None`), `test_snapshot_replaced_wholesale_on_next_accepted_refresh`, `test_snapshot_cleared_on_shutdown`, `test_record_facts` (`code_kind` `none`/`empty`/`string`/`invalid`, `hold`, `listing_ids` as `(listing_id, parent_listing_id)` pairs from stay segments or `(listing_id, None)` without segments), `test_malformed_parent_listing_id_marks_record_unverifiable` (parametrized over a list and an object `parentListingId`, which `GuestyStaySegment.from_api_dict` passes through today: the refresh completes exactly as before, nothing raises, and that record's `listing_ids` is `None`, never a pair containing the invalid parent), `test_v3_flag_comes_from_returned_ids_not_is_v3` (a retained record republished by the fallback with `is_v3=True` is not `v3`), `test_observed_codes_exceed_baseline_bound` (`MAX_BASELINE_CODES + 1` coded records all present), `test_fallback_observed_codes_ignore_status_filter_but_skip_holds_and_empty`, `test_fallback_sets_copied`, and `test_repr_contains_no_codes`. Tests must pass explicit `returned_ids=` to `make_fetch_result` (the helper is unchanged). In `tests/test_diagnostics.py`, add `test_snapshot_absent_from_diagnostics` to `TestReservationDiagnostics` (data-model § Accepted-refresh snapshot; research R3, R9).
- [ ] T017 Implement `AcceptedRefreshSnapshot` (frozen dataclass, `repr=False` on code-bearing fields) and its recording in `ReservationsCoordinator._record_identity` in `custom_components/guesty/coordinator.py` (so both `_async_fetch_reservations` call sites, the no-known-listings early return and the normal path, are covered), inside `_record_identity`, only after its local `stored` result is true (nothing is recorded otherwise; the `_unstored` guard already refuses), building each record's `listing_ids` only from string `parent_listing_id` values (any non-string, non-`None` parent sets that record's `listing_ids` to `None`, unverifiable, so building the snapshot never raises and never aborts or alters the refresh), stored in the private attribute `_accepted_snapshot` and tagged with the in-progress `_write_miss_attempt_generation`; add `accepted_refresh_snapshot()` validating it against `_write_miss_outcomes`; clear it in `async_shutdown`. It is never persisted, logged or added to diagnostics (data-model; research R3).
- [ ] T018 Phase 3 gate: run `uv run pytest tests/api/test_reservations_v3.py tests/api/test_reservations_fallback.py tests/test_coordinator.py tests/test_identity.py tests/test_diagnostics.py`, `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm all CI jobs green and record the evidence.

**Checkpoint**: Evidence is collected in memory; nothing reads it yet. No
user-visible change.

## Phase 4: Foundational C — 013 evidence port and witness split (no behavior change)

**Purpose**: Define the thin 013 port with an always-unavailable
production implementation, and split the 012 witness so the by-ID path
can reuse its context checks.

- [ ] T019 [P] Add failing tests in `tests/test_actions.py`, new class `TestConflictEvidencePort`: `test_production_adapter_is_unavailable` (`_conflict_evidence(coordinator).available()` is `False`), `test_unavailable_adapter_is_never_consulted_for_lookups` (calling `codes_for`, `ids_for` or `validate_record` on it raises `RuntimeError`), and `test_fake_double_satisfies_port`. Add the test double `FakeConflictEvidence` (configurable `available`, conflict-only maps and a validator verdict, call recording) to `tests/conftest.py` (plan § Feature 013 dependency; data-model § 013 evidence adapter).
- [ ] T020 Implement in `custom_components/guesty/actions.py`: the `ConflictEvidence` `Protocol` (`available()`, `codes_for(reservation_id)`, `ids_for(code)`, `validate_record(raw, action_type)`), `UnavailableConflictEvidence` (`available()` returns `False`; `codes_for`, `ids_for` and `validate_record` raise `RuntimeError`), and `_conflict_evidence(coordinator) -> ConflictEvidence` returning `UnavailableConflictEvidence()`. Its docstring states that feature 013 supplies the real implementation (T051) and that no 013 logic may be added here. **013-gated (double only).**
- [ ] T021 [P] Add failing test `test_recovery_context_check_excludes_identity_and_route` to `TestIdentityWritePredicate` in `tests/test_actions.py` (calls `_check_recovery_context` directly and proves it raises for options, settings, liveness and outcome changes but not for an identity miss). Run the existing recovery tests `test_recovered_executor_rechecks_witness_and_is_one_shot`, `test_recovered_executor_refuses_superseded_witness`, `test_recovered_executor_rejects_all_options_aba`, `test_recovered_executor_rechecks_after_await`, `test_recovered_executor_rejects_refresh_from_replaced_client` and `test_recovered_write_refuses_superseded_state` as baseline controls (research R2).
- [ ] T022 Refactor `_check_recovery_witness` in `custom_components/guesty/actions.py` into `_check_recovery_context(witness, reservation_id, action_type)` (options, settings, liveness, accepted outcomes and `last_update_success`) followed by the unchanged `guard_reservation_write` and `select_reservation_write_route`; `_check_recovery_witness` keeps its signature and messages (plan step 4; research R2).
- [ ] T023 Phase 4 gate: run `uv run pytest tests/test_actions.py tests/test_custom_field_service.py`, `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm all CI jobs green and record the evidence.

**Checkpoint**: All foundations exist. The feature is still entirely inert.

## Phase 5: User Story 2 — Refuse anything that cannot be proven (Priority: P1) — 013-gated (double only)

**Goal**: Qualify, read once and authorize under the 012 dispatch lock;
every uncertain, conflicting or disallowed case fails closed with no write.

**Independent Test**: With `_conflict_evidence` patched to an available
`FakeConflictEvidence`, vary pre-read state and the by-ID response; assert
zero writes, at most one by-ID GET and an explanatory failure in every
case. Unpatched, assert the unchanged 012 refusal with zero by-ID GETs.

### Tests for User Story 2

- [ ] T024 [P] [US2] Add failing tests in `tests/test_actions.py`, new class `TestByIdFallbackInertUntil013`, parametrized over `add_reservation_note` and `set_reservation_status` and over an absent target and a code-less target: with the **unpatched** production adapter, the action raises the unchanged 012 refusal text (`has no known confirmation code`, `REFUSAL_NO_CODE` in `custom_components/guesty/identity.py`, for both targets), `read_reservation_by_id` is never awaited, no write is sent, and exactly one DEBUG record carries the category `evidence_unavailable` and the reservation ID only. Existing `test_target_still_missing_after_one_refresh` and `test_missing_target_refreshes_once_without_write` are baseline controls (research R1; quickstart § User-visible contract).
- [ ] T025 [P] [US2] Add the same inert tests for the custom-field action in `tests/test_custom_field_service.py`, new class `TestByIdFallbackInertUntil013CustomField`; existing `test_missing_field_target_refreshes_once_without_write` and `test_custom_field_miss_refuses_after_failed_or_empty_refresh` are baseline controls. Add `test_listing_write_never_reads_by_id`: with the test double patched as available, a listing custom-field write issues zero by-ID GETs and behaves exactly as today; existing `test_listing_writes_are_unaffected` and `test_listing_target_is_unaffected` are baseline controls (FR-001; contract test 11).
- [ ] T026 [P] [US2] Add tests in `tests/test_actions.py`, new class `TestByIdPreReadGuards` (US2-S1): with the double available, an invalid identifier or input (zero refreshes), every non-miss guard refusal (no baseline, `_unstored`, unconfirmed continuity, ambiguity), a known-unroutable target, and a failed or superseded 012 refresh each produce zero by-ID GETs and zero writes with the unchanged 012 message. These are baseline controls except for the new `read_reservation_by_id` call-count assertion.
- [ ] T027 [P] [US2] Add failing tests in `tests/test_actions.py`, new class `TestByIdPreReadEligibility` (US2-S7; contract § Qualification), one test per category, each asserting zero by-ID GETs, zero writes, the unchanged 012 refusal and the DEBUG category: `test_non_v3_source_is_not_v3`; parametrized `test_fallback_provenance` (live `fallback_served_ids` and live `fallback_sourced_ids`, each with the target code-less and absent from them before the action and added by the qualifying refresh, because the qualifying refresh recomputes both sets, a pre-action `fallback_sourced` target is refused by route selection (T026's known-unroutable control), and a served target with a code would sit in `fallback_codes` so there is no miss; snapshot `fallback_served`/`fallback_sourced` (set up as a retired-legacy refresh so the live sets differ from the snapshot: live `fallback_served_ids` holds another ID, `legacy_write_routing_available` is false, and the mocked fetch result puts the target in `fallback_served` or `fallback_sourced`; in a normal refresh the live-set check fires first), snapshot `fallback_observed`, an out-of-selection legacy observation in this refresh, the same observation from an earlier refresh with the qualifying lookup not due, held only in `legacy_observed_reservations`); `test_incomplete_session_observed_set_is_evidence_unavailable`; `test_missing_or_stale_snapshot_is_evidence_unavailable` (clear `_accepted_snapshot` or append a newer outcome tuple directly, not via a store failure, which trips the `_unstored` guard first); `test_rejected_in_refresh_is_rejected` (checked before qualification, so a rejected ID that is in `returned_ids` reports `rejected`, not `not_qualifying`); parametrized `test_not_qualifying` (a returned coded record whose code the bounded baseline evicted, an `alteration` code-less record, `code_kind` `invalid`); parametrized `test_confirmed_absent` (live `confirmed_absent_reservations`, snapshot `fallback_absent`, an earlier-refresh retirement already pruned from the live set and held only in `legacy_retired_reservations`); `test_incomplete_session_retired_set_is_evidence_unavailable`; `test_adapter_unavailable_or_over_bound_is_evidence_unavailable`; and `test_conflict_only_evidence_for_target_is_rejected` (FR-002, FR-003, FR-006, SC-002).
- [ ] T028 [P] [US2] Add failing tests in `tests/test_actions.py`, new class `TestByIdReadOutcomes` (US2-S2, US2-S6; contract test 5), through the note and status services with respx, a qualifying absent target and the double: `[]` and a mismatched record report the T031 stem "not found by ID"; two records, duplicate matches, a bare dict, a bare list, non-list `results`, each unprovable `pagination` form and non-JSON report "found but refused" `malformed`; 400, 401, 403, 404, 429, 500, 503, timeout and connect errors report the read-failure category. Each asserts exactly one by-ID GET, zero retries, zero token renewal on 401 and zero PUTs. Token rejection, local budget, token endpoint 429/non-200 and token connection failure each assert **zero** by-ID GETs and the read-failure category (FR-004, FR-012, SC-002, SC-003).
- [ ] T029 [P] [US2] Add failing tests in `tests/test_actions.py`, new class `TestByIdAuthorizationRefusals` (US2-S2–S5; contract test 6), each asserting one by-ID GET and zero writes. Class setup: a usable stored baseline with `coordinator.identity.baseline.continuity = CONTINUITY_VERIFIED` set before the action (otherwise seeding `legacy_codes` or `bootstrap_codes` demotes continuity to unconfirmed and `classify_write_refusal` refuses before the read); bootstrap entries stay out of the search results with check-out inside the past-days window. Cases: `test_disallowed_status` parametrized over `alteration`, an unknown sentinel status, missing, number, list and object (sentinel absent from `caplog`; `map_v3_status` never called); `test_validator_rejection_is_malformed` (double's `validate_record` rejects a malformed record or segment); `test_unparseable_record_is_malformed` (`GuestyReservation.from_v3_api_dict` returns `None`); `test_non_object_notes_on_note_write_is_malformed`; `test_no_code` parametrized over missing, `""`, numeric and list; parametrized `test_code_mapped_to_other_id` over the seven maps, with the same setup rules as the next test because the qualifying refresh replaces baseline `codes` and `fallback_codes`: seed `legacy_codes`, `bootstrap_codes` and the double's conflict-only `ids_for` before the action; inject the code → other-ID entry for baseline `codes` and `fallback_codes` during the read as a side effect of the by-ID respx route; and cover snapshot `observed_codes` and snapshot `fallback_observed_codes` (with an out-of-status legacy record) in `test_authorize_rejects_code_mapped_in_snapshot_maps`, a direct unit test of `_authorize_by_id_record` (signature fixed in T033) with a crafted `AcceptedRefreshSnapshot`, each → `identity_conflict`; both direct tests install the crafted snapshot as `coordinator._accepted_snapshot` with an attempt matching the latest successful `_write_miss_outcomes` tuple (generation, attempt, `True`, options, settings, entry, client), a usable stored baseline and an available double, so the changed-snapshot branch does not fire, and each has a negative control where the same snapshot without the conflicting entry authorizes; parametrized `test_target_mapped_to_other_code`, where only `legacy_codes` and `bootstrap_codes` are seeded before the action (a pre-action target entry in baseline `codes` or `fallback_codes` puts it in `current_ids()` so the ordinary 012 write runs, one in snapshot `observed_codes` means it was returned coded, and one in `fallback_observed_codes` is refused by qualification as `fallback_provenance`), while the target → other-code entry for baseline `codes`, `fallback_codes` and the double's conflict-only `codes_for(target)` is injected **during the read** as a side effect of the by-ID respx route (pre-action conflict-only evidence is T027 `test_conflict_only_evidence_for_target_is_rejected`), each → `identity_conflict`; the snapshot `observed_codes` and `fallback_observed_codes` cases cannot be injected through the flow (replacing the snapshot is refused earlier as a changed snapshot), so cover them in `test_authorize_rejects_target_mapped_in_snapshot_maps`, a direct unit test that calls `_authorize_by_id_record` (signature fixed in T033) with a crafted `AcceptedRefreshSnapshot` whose maps hold the conflicting entry → `identity_conflict`, installed and controlled as described for `test_authorize_rejects_code_mapped_in_snapshot_maps`; `test_code_duplicating_another_ids_code_is_conflict` (and ambiguity and continuity stay unchanged); `test_unavailable_conflict_source_refuses` (each change injected during the read as a side effect on the by-ID respx route, because the same state before the action is refused earlier by `classify_write_refusal`: baseline becomes unusable or `_unstored`, adapter turning unavailable after the read, and the snapshot cleared or replaced during the read by setting the coordinator's `_accepted_snapshot` attribute (T017) to `None` or a different snapshot directly in the respx side effect, without calling `async_shutdown`, which sets `_shutdown_requested` and is instead caught by the post-read context check as `guard_witness`; each asserts category `identity_conflict`); `test_codeless_listing_disagreement_is_identity_conflict` (same unit `listingId`, different `parentListingId`), `test_unverifiable_snapshot_listing_pairs_refuse` (the snapshot record's `listing_ids` is `None` → `identity_conflict`), and `test_returned_codeless_outside_selection_gets_listing_check` (a code-less record returned by the search but outside the listing selection or window, so the miss is absent-style, qualifies as `codeless` and a disagreeing by-ID listing pair → `identity_conflict`) (FR-005, FR-006, FR-007; research R7–R9).
- [ ] T030 [P] [US2] Add failing tests in `tests/test_custom_field_service.py`, new class `TestByIdCustomFieldRefusals`: a not-found read, a `disallowed_status` refusal, an `identity_conflict` refusal and a `rate_limit` read failure each raise `HomeAssistantError` carrying the by-ID outcome text (never the generic `API error during custom field update`), with zero `PUT /reservations-v3/{id}/custom-fields` (plan step 9; research R12).

### Implementation for User Story 2

- [ ] T031 [US2] Implement `ReservationByIdOutcomeError(GuestyActionError)` in `custom_components/guesty/actions.py` with `outcome` (`found_written`, `found_write_failed`, `not_found`, `found_refused`, `read_failed`), `qualifier` (`absent` or `codeless`) and `category`, and a fixed-text message built only from those values, the action type and the reservation ID. The message starts with exactly one outcome stem, and tests match it with an anchored prefix: `found_write_failed` → "found by ID, write failed", `not_found` → "not found by ID", `found_refused` → "found but refused", `read_failed` → "by-ID read failed"; the INFO success line uses "found by ID, written". After the stem comes the qualifier phrase ("absent from search" or "present without a confirmation code"), the category, the reservation ID and the contract's corrective hint (FR-012; research R14).
- [ ] T032 [US2] Implement `_qualify_by_id_target` in `custom_components/guesty/actions.py` and hook it into `_execute_recovered_reservation_write`: inside the dispatch lock, after `_check_recovery_context` passes, a `ReservationIdentityMissError` from the identity step runs the contract's eight ordered qualification checks using `api_client.reservations_api`, the witness settings, the live and snapshot fallback sets, `legacy_observed_reservations`, `accepted_refresh_snapshot()`, `confirmed_absent_reservations`, `legacy_retired_reservations` and `_conflict_evidence`. Derive the qualifier from qualification step 5 only: `absent` when the target is not in the snapshot's `returned_ids`, `codeless` otherwise; never from whether the miss is a `ReservationIdentityAbsentError` or a present-without-code error, because a code-less returned record outside the listing selection or window raises the absent error yet must still get the FR-007 listing check. Any failure or unevaluable check logs one DEBUG line (category and reservation ID only) and re-raises the original miss unchanged. Every other refusal keeps its 012 behavior (FR-002, FR-003; plan step 5).
- [ ] T033 [US2] Implement the single read and `_authorize_by_id_record` in `custom_components/guesty/actions.py`: call `read_reservation_by_id` exactly once under the held lock and **outside** the write `try` block; map `GuestyByIdReadError` to `ReservationByIdOutcomeError` (categories `not_found` and `mismatched` → outcome `not_found`; `malformed` → `found_refused`; `http`, `rate_limit`, `authentication` and `transport` → `read_failed`); immediately after the read re-run `_check_recovery_context` and map a failure (entry reload, options replacement, source change) to "found but refused" `guard_witness`; then run, in contract order, the raw-status check (strip, lowercase, direct `V3_STATUS_MAP` lookup, must be in `ACTIONABLE_STATUSES` and not in `HOLD_STATUSES`); the facts check (`validate_record` plus `from_v3_api_dict`, then, for a note write, note normalization: missing or `null` → `{}`, non-object → `malformed`, non-string keys dropped); the code check; the conflict check (re-read `accepted_refresh_snapshot()` and re-call `available()` on the evidence port first, and treat a missing or changed snapshot (a deliberate fail-closed choice: a snapshot replaced during the read, even by a newer accepted refresh, refuses here rather than entering the US2-S9 branch, which only applies at the pre-write recheck), an unavailable port, an unusable baseline or an `_unstored` baseline as category `identity_conflict`; then snapshot `observed_codes` and `fallback_observed_codes`, `check_identity_conflict`, `codes_for`/`ids_for`); and the FR-007 listing-pair agreement (a snapshot record whose `listing_ids` is `None`, or a by-ID segment with a non-string parent, → `identity_conflict`). Signature: `_authorize_by_id_record(*, coordinator: ReservationsCoordinator, reservation_id: str, action_type: str, qualifier: str, raw: Mapping[str, object], snapshot: AcceptedRefreshSnapshot, evidence_port: ConflictEvidence) -> _ByIdEvidence`, raising `ReservationByIdOutcomeError` on refusal; the read and the post-read context check stay in the caller. Return a local `_ByIdEvidence` value. Until T042 wires the write, an authorized result re-raises the original 012 refusal with no write (FR-004–FR-007, FR-013; plan steps 6–7).
- [ ] T034 [US2] Verify that the existing first `except GuestyActionError` branch of `_async_handle_set_custom_field` in `custom_components/guesty/__init__.py` surfaces `ReservationByIdOutcomeError` text unchanged (it is a `GuestyActionError` subclass, so no new branch is needed; add one only if T030 shows otherwise), and confirm `_handle_add_note` and `_handle_set_reservation_status` in `custom_components/guesty/actions.py` surface its text unchanged (research R12).
- [ ] T035 [US2] Phase 5 gate: run `uv run pytest tests/test_actions.py tests/test_custom_field_service.py tests/test_privacy_logging.py`, `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm all CI jobs green, that the production adapter is still `UnavailableConflictEvidence`, and record the evidence.

**Checkpoint**: Every qualification and authorization refusal is implemented and tested with the double (the recheck refusals land in Phase 6);
no by-ID write can be sent, and production behavior is unchanged.

## Phase 6: User Story 1 — Write to a reservation the search omits (Priority: P1) — 013-gated (double only)

**Goal**: An authorized by-ID record allows exactly one v3 write after the
pre-write recheck, including the synthetic #223 regression.

**Independent Test**: With a usable stored baseline, confirmed continuity,
a v3 source and the double available, have the qualifying refresh omit the
target and the by-ID read return one valid, allowed, coded, unconflicted
record; each write type sends one search, one by-ID GET and one v3 PUT and
reports the real outcome.

### Tests for User Story 1

- [ ] T036 [P] [US1] Add failing tests in `tests/test_actions.py`, new class `TestByIdWriteFallback223`: `test_223_same_day_absent_reservation_note_written_by_id` and `test_223_same_day_absent_reservation_status_written_by_id`. Setup: `freezer` fixes today; usable stored baseline and confirmed continuity; v3 source; a same-day `confirmed` target whose `createdAt` is after its localized check-in is absent from the v3 search while every other search result carries a code; the by-ID response is one record with a fresh code, allowed status and valid stay segment. Expect exactly one search refresh, one by-ID GET, one PUT (`/reservations-v3/{id}/notes` echoing the verified notes plus the appended text with no second GET, or `/reservations-v3/{id}/status`), the normal service result, exactly one INFO record "found by ID, written" with qualifier `absent`, at most one token request during the action (the by-ID read and the PUT share it; the first acquisition may happen at the read because setup patches `test_connection`); "one search refresh" is the `get_reservations` mock call count, and SC-004 retention (coordinator `data`, `current_ids()`, every baseline map and `candidates`, baseline continuity and the `_ambiguous` flag, `Store` save count, the session legacy observed and retired sets, fallback sets, `confirmed_absent_reservations`, the snapshot and the double's state equal their values captured just before the by-ID read) (US1-S1, US1-S4, US1-S6; FR-009, FR-010; SC-001, SC-003, SC-004).
- [ ] T037 [P] [US1] Add failing tests in `tests/test_custom_field_service.py`, new class `TestByIdWriteFallback223CustomField`: `test_223_same_day_absent_reservation_door_code_written_by_id` (same setup and the same assertions as T036: one search refresh, one by-ID GET, one `PUT /reservations-v3/{id}/custom-fields` with the door code, the normal result, one INFO record with qualifier `absent`, no extra token request, and full SC-004 retention) and `test_codeless_custom_field_target_written_by_id` (qualifier `codeless`). This [US1] task also carries the custom-field US2-S9 recheck cases because they share this class's setup. In the same class, add the custom-field halves of T039 and T040, because `tests/test_actions.py` has no custom-field service setup: `test_newer_accepted_refresh_v3_provenance_writes` and `test_newer_accepted_refresh_fallback_provenance_refuses` (refresh injected after authorization through the write's `get_token` call, as in T039; the custom-field write proceeds through v3 only when an injected newer accepted refresh supplies the target coded with v3 provenance; otherwise "found but refused" `guard_witness`, zero writes, no second by-ID GET; US2-S9, FR-008), and `test_custom_field_by_id_write_failure` (a rejected PUT and a timeout after send produce exactly one PUT, no retry, and "found by ID, write failed" surfaced by `_async_handle_set_custom_field` rather than its generic "API error during custom field update" text; US1-S5, FR-010).
- [ ] T038 [P] [US1] Add failing tests in `tests/test_actions.py`, new class `TestByIdWriteFallbackVariants`: `test_codeless_target_written_by_id` parametrized over note and status and over `confirmationCode` missing and `""` (qualifier `codeless`); `test_absent_target_outside_listing_selection_or_window_is_written` (SC-005; US1-S3); baseline control `test_returned_coded_target_outside_selection_takes_012_path` (zero by-ID GETs); `test_completed_and_cancelled_statuses_are_allowed`; `test_none_sub_maps_do_not_refuse`; and `test_note_on_record_without_notes_writes_only_other` (missing and `null` `notes`; one GET, body `{"other": text}`) (FR-002, FR-005(c), FR-007; research R7, R11).
- [ ] T039 [P] [US2] Add failing tests in `tests/test_actions.py`, new class `TestByIdPreWriteRecheck` (US2-S8, US2-S9; contract test 7). Every case issues no second by-ID GET. Every case except the v3-provenance status case below has zero writes and a "found but refused" `guard_witness` outcome (never "write failed"); that case sends exactly one v3 PUT, returns the normal result and logs one INFO "found by ID, written". A "newer refresh" is injected manually, never by running a real refresh (the debouncer lock is already held): append an outcome tuple with a higher attempt to `_write_miss_outcomes` and, for an accepted refresh, set a new `_accepted_snapshot` and update the baseline codes. Injection points: entry reload, options replacement and source change are injected **during the read** (side effect on the by-ID respx route) and are caught by T033's post-read context check; every other change is injected **after authorization and before `before_send`**, by wrapping `api_client._token_manager.get_token` and injecting on its second call (the write's `_request` token acquisition that precedes `before_send`; the token endpoint is not hit again because the token is cached), so T033's snapshot and baseline checks have already passed. Cases: entry reload, options replacement and source change during the read; baseline becomes `_unstored`, continuity unconfirmed or ambiguous between read and write; an injected newer failed refresh and an injected newer ambiguous refresh outcome. For an injected newer accepted refresh: a note write fails with `guard_witness`; a status write proceeds through v3 only when that refresh supplies the target coded with v3 provenance, and fails with `guard_witness` when it gives the target fallback provenance (the injected snapshot puts the target in its `fallback_sourced` set, live `fallback_sourced_ids` is updated to match, and the target code goes into baseline `fallback_codes` so `guard_reservation_write` passes and the refusal comes from the fallback-provenance or non-`ROUTE_V3` route check, not a missing-code guard refusal) (FR-008). The custom-field cases live in T037. This US2 task sits in the US1 phase because the recheck needs the write wiring from T041–T042.
- [ ] T040 [P] [US1] Add failing tests in `tests/test_actions.py`, new class `TestByIdWriteFailure` (US1-S5; contract test 8): a rejected PUT (4xx and 5xx) and a timeout after send each produce exactly one PUT, no retry against v3 or any other source, and "found by ID, write failed" with the qualifier and the existing safe text ("may have reached Guesty; not retried" for the unknown outcome) (FR-010).

### Implementation for User Story 1

- [ ] T041 [US1] Extend the recovery-write callable type (the `recovery_write` parameter of `execute_reservation_write`, passed on as the `write` parameter of `_execute_recovered_reservation_write`) in `custom_components/guesty/actions.py` with a keyword `notes: Mapping[str, object] | None = None`, typed with a `Protocol` generic over `WriteResultT` whose `__call__` takes the three existing parameters positional-only (so existing `lambda route, single, check:` fakes still type-check) because `Callable` cannot express a keyword argument; also update the existing three-argument `write=` lambdas passed to `_execute_recovered_reservation_write` in `tests/test_actions.py` (in `TestIdentityWritePredicate`) to accept `notes=None`, so the mypy gate stays green; `_handle_add_note` forwards it as `prefetched_notes`, while `_handle_set_reservation_status` and the custom-field lambda in `custom_components/guesty/__init__.py` accept and ignore it (research R11).
- [ ] T042 [US1] Implement `_attempt_by_id_write` in `custom_components/guesty/actions.py`, replacing the T033 placeholder: force `ROUTE_V3`; build a by-ID `recheck` for `before_send` that reruns `_check_recovery_context`, `classify_write_refusal` (no refusal or `missing_current_code` only), the v3-only and provenance checks, and the snapshot-attempt check with the newer-accepted-refresh branch (note → refuse; status or custom field → only if `guard_reservation_write` passes and `select_reservation_write_route` returns `ROUTE_V3`); call `write(ROUTE_V3, True, recheck, notes=...)` once, where `notes` is the normalized mapping for a note write and `None` for status and custom-field writes; catch `_RecoveryGuardRefusal` **first** as `found_refused`/`guard_witness`; wrap the remaining write errors (the `GuestyRecovery*` errors, `GuestyRecoveryWriteError` and any other `GuestyApiError`) as `found_write_failed`, letting non-API exceptions propagate unchanged; on success, including a write allowed by the newer-accepted-refresh branch, emit one INFO line "found by ID, written" (outcome, qualifier, action type, reservation ID only) and return the normal result; discard the evidence on every exit (FR-008–FR-010; plan steps 8–10; research R10, R12, R13).
- [ ] T043 [US1] Phase 6 gate: run `uv run pytest tests/test_actions.py tests/test_custom_field_service.py`, `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm all existing 012 suites pass unchanged, the production adapter is still `UnavailableConflictEvidence`, all CI jobs are green, and record the evidence.

**Checkpoint**: The full by-ID flow works against the double; production
remains inert until Phase 8.

## Phase 7: User Story 3 — Diagnose why a fallback did or did not write (Priority: P2) — 013-gated (double only)

**Goal**: Distinguishable, actionable, privacy-safe outcomes; independent
overlapping attempts.

**Independent Test**: Exercise each outcome with sentinel sensitive values
and inspect errors, logs and diagnostics; run two overlapping actions.

### Tests for User Story 3

- [ ] T044 [P] [US3] Add failing tests in `tests/test_actions.py`, new class `TestByIdOutcomeMessages`: parametrized over both qualifiers (`absent`, `codeless`) and every outcome class except "not eligible", `test_outcome_text_is_distinguishable` asserting the error starts with the T031 stem for its class ("found by ID, write failed", "not found by ID", "found but refused" with its category, or "by-ID read failed" with its category, matched as an anchored prefix so "not found by ID" can never satisfy a found outcome) and names the qualifier text ("absent from search" or "present without a confirmation code"), plus the reservation ID and the contract's corrective hint (for example "retry after the next refresh", "check credentials", "Guesty rate-limited the lookup; retry later", "review it in Guesty"); `test_success_logs_exactly_one_info_line`; and `test_not_eligible_adds_no_new_message` (US3-S1; FR-012; SC-006; research R14).
- [ ] T045 [P] [US3] Add failing tests in `tests/test_privacy_logging.py`, new class `TestByIdFallbackPrivacy`: for every outcome in T027–T040, inject sentinel confirmation codes, a PIN or door-code custom-field value, guest name, email and phone in the by-ID record, a raw-body marker, an unknown status and a token, and assert none appears in `caplog` at any level (including DEBUG), in the raised error text, or in `async_get_config_entry_diagnostics` (US3-S2; FR-012; SC-006).
- [ ] T046 [P] [US3] Add failing tests in `tests/test_actions.py`, new class `TestByIdOverlap`: `test_overlapping_actions_same_target_each_read_once` and `test_overlapping_actions_different_targets_decide_independently` (each issues its own single by-ID GET; one may be written while the other is not found; no result is shared and no cooldown applies), and `test_coordinator_refresh_waits_for_by_id_critical_section` (quickstart § Compatibility verification) (US3-S3; FR-011).

### Implementation for User Story 3

- [ ] T047 [US3] Finalize the outcome text, hints and log formats of `ReservationByIdOutcomeError` and the by-ID INFO and DEBUG lines in `custom_components/guesty/actions.py` so T044–T046 pass; use only fixed text, the reservation ID, the action type, the qualifier and the category; add no diagnostics counters (FR-012; research R14).
- [ ] T048 [US3] Phase 7 gate: run `uv run pytest tests/test_actions.py tests/test_custom_field_service.py tests/test_privacy_logging.py tests/test_diagnostics.py`, `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm all CI jobs green and record the evidence.

**Checkpoint**: All three stories are complete against the double; the
shipped behavior is still the unchanged 012 refusal.

## Phase 8: Enable against feature 013 — BLOCKED on 013

**Purpose**: Replace the unavailable adapter with feature 013's real
interfaces. Nothing in this phase may start until 013 is merged on `main`.

- [ ] T049 **BLOCKED on 013**: Confirm 013 has merged its rejected-ID set (including FR-008d raw-segment rejections), conflict-only evidence (with bound state and both lookup directions) and single-record validator; record their real names and modules in `specs/014-by-id-write-fallback/tasks.md` under this phase. If any is missing, stop and report; do not create it. If names differ from the contract, adapt only the thin adapter (plan § Feature 013 dependency).
- [ ] T050 **BLOCKED on 013**: Add failing tests in `tests/test_actions.py`, new class `TestConflictEvidenceAdapter013` (`_conflict_evidence` maps each port operation to the real 013 interface; `available()` is false when the 013 FR-008c bound is exceeded or any interface is unavailable), and in `tests/api/test_reservations_v3.py` `test_rejected_ids_include_013_raw_segment_rejections`.
- [ ] T051 **BLOCKED on 013**: Implement `_conflict_evidence` in `custom_components/guesty/actions.py` over the real 013 interfaces and source `ReservationFetchResult.rejected_ids` from 013's rejected set in `custom_components/guesty/api/reservations_v3.py`, removing the interim raw-minus-parsed computation only if 013's set supersedes it.
- [ ] T052 **BLOCKED on 013**: Rerun every Phase 5–7 full-flow test against the real adapter (parametrize `TestByIdWriteFallback223`, `TestByIdWriteFallback223CustomField`, `TestByIdPreReadEligibility` and `TestByIdAuthorizationRefusals` over the double and the real 013 evidence). Convert `TestByIdFallbackInertUntil013` and `TestByIdFallbackInertUntil013CustomField` into "013 evidence unavailable or over bound" tests. Audit and update, with a documented reason, only the 012 tests in `tests/test_actions.py` and `tests/test_custom_field_service.py` that expected a refusal for a now-qualifying absent or code-less target (for example `test_target_still_missing_after_one_refresh`, `test_missing_target_refreshes_once_without_write`, `test_missing_field_target_refreshes_once_without_write`).
- [ ] T053 **BLOCKED on 013**: Phase 8 gate: run `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm all CI jobs green and record the evidence.

**Checkpoint**: The fallback is live in production for qualifying actions.

## Phase 9: Polish and final validation gate

- [ ] T054 [P] **BLOCKED on 013** (depends on T051): Update the `README.md` section "Reservation writes are fail-closed" with operator guidance for reservation write recovery with the by-ID outcome classes, qualifiers, categories and next steps, stating that nothing learned is kept and that live behavior for #223-style reservations is unverified. Do not document the fallback as active before T051 is merged.
- [ ] T055 [P] **BLOCKED on 013** (depends on T051): Create a release note with `uv run --group releasenotes reno new by-id-write-fallback-321` and edit the generated `releasenotes/notes/by-id-write-fallback-321-<hash>.yaml` (SPDX header as in existing notes) to describe the single by-ID read and v3 write after a feature 012 miss, its fail-closed refusals and its privacy guarantees, referencing #321 and #223 without claiming #223 is fixed (spec § Input). Run `uv run --group releasenotes reno lint`.
- [ ] T056 Final validation (depends on T053–T055): run the focused suites in `specs/014-by-id-write-fallback/quickstart.md`, then `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/`, `uv run pre-commit run --all-files` and `uv run --group releasenotes reno lint`. Repeat the dispatch-lock and witness tests in the locked HA 2026.10.4 environment and an isolated HA 2026.4.0 environment without changing `uv.lock`; record versions and results, or report a blocked gate. Do not perform or claim live Guesty verification.

**Checkpoint**: All local and CI gates pass; no live verification claimed.

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1** → everything.
- **Phase 2** (T002–T009) and **Phase 3** (T010–T018) are independent of
  each other and of 013; both may merge before 013.
- **Phase 4** (T019–T023) depends on Phase 1 only; may merge before 013.
- **Phase 5 / US2** (T024–T035) depends on Phases 2–4. 013-gated: merge
  only with the production adapter unavailable. US2 is complete only
  after T042, because its recheck scenarios (T039) need the US1 write
  wiring.
- **Phase 6 / US1** (T036–T043) depends on Phase 5 (every refusal and the
  outcome error must exist before any write is wired). 013-gated.
- **Phase 7 / US3** (T044–T048) depends on Phase 6. 013-gated.
- **Phase 8** (T049–T053) is **BLOCKED on feature 013** and depends on
  Phase 7.
- **Phase 9**: T054 and T055 are **BLOCKED on 013** through T051; T056
  depends on T053–T055.

### Within-task ordering

- T003 after T002; T006 after T003–T005; T008 after T007.
- T011 after T010; T013 after T012; T015 after T014; T016 after T011
  (its tests pass `returned_ids=`); T017 after T011,
  T015 and T016.
- T020 after T019; T022 after T021.
- T031–T034 after T024–T030; T032 after T020 and T022; T033 after T006,
  T015, T017, T031 and T032; T034 after T031.
- T041–T042 after T036–T040; T042 after T008 and T041.
- T047 after T044–T046.
- T050 after T049; T051 after T050; T052 after T051; T053 after T052.

### Blocked on 013 (do not start before 013 merges)

T049, T050, T051, T052, T053, T054, T055 and T056 (T056 through its
dependencies).

### 013-gated (double only; mergeable with the production adapter unavailable)

T020 and T024–T048.

### User story dependencies

- **US2 (P1)**: independent once Phases 2–4 exist; its tests need only the
  double.
- **US1 (P1)**: depends on US2's refusal and authorization code; sequenced
  second so no write is ever enabled before its refusals.
- **US3 (P2)**: depends on US1's success and write-failure outcomes.

### Traceability

Scenario shorthand `US1-S1..S6`, `US2-S1..S9` and `US3-S1..S3` refers to
the numbered acceptance scenarios in `spec.md`.

| Requirement or outcome | Tasks |
| --- | --- |
| FR-001 (three actions only) | T024–T025 (listing case in T025), T036–T038 |
| FR-002; US2-S1, US2-S7 | T010–T017, T026–T027, T032 |
| FR-003; US2-S7 | T012–T013, T027, T032, T039, T042 |
| FR-004; US2-S6 | T004–T006, T028, T033 |
| FR-005; US2-S2–S4 | T004–T006, T028, T029, T033, T038 |
| FR-006; US2-S5 | T014–T017, T029, T033 |
| FR-007; US1-S2 | T016–T017, T029, T037–T038 |
| FR-008; US2-S8–S9 | T021–T022, T037, T039, T042 |
| FR-009; US1-S6 | T014, T016, T036, T042 |
| FR-010; US1-S4–S5 | T007–T008, T036, T040–T042 |
| FR-011; US3-S3 | T046 |
| FR-012; US3-S1–S2 | T002–T003, T031, T044–T045, T047 |
| FR-013 | T004, T006, T033 |
| FR-014 | T033, T042, T052 |
| SC-001 | T036–T038 |
| SC-002 | T024–T030, T037, T039 |
| SC-003 | T004–T005, T007, T028, T036, T046 |
| SC-004 | T036, T037 |
| SC-005 | T038 |
| SC-006 | T044–T045 |
| 013 dependency | T019–T020, T024–T025, T049–T053 |

### Parallel opportunities

- Phase 2: T002, T004, T005 and T007 are separate test classes; T002 is
  a separate file.
- Phase 3: T010, T012 and T014 touch separate test modules; T016
  follows T011.
- Phase 4: T019 and T021 are separate test classes.
- US2: T024–T030 are separate test classes (T025 and T030 in
  `tests/test_custom_field_service.py`).
- US1 phase: T036–T040 are separate test classes (T037 in
  `tests/test_custom_field_service.py`).
- US3: T044 and T046 (`tests/test_actions.py`) and T045
  (`tests/test_privacy_logging.py`).
- Polish: T054 and T055 touch separate files.

### Implementation strategy

1. **Foundations first (no behavior change)**: Phases 2–4 can ship as one
   or more PRs before 013, each with a green gate.
2. **Safety before success**: US2 lands every refusal with the double;
   US1 then wires the single v3 write and the #223 regression; US3
   finalizes outcome text, privacy and overlap. All remain inert in
   production.
3. **MVP**: US2 + US1 (both P1) with the double proves SC-001–SC-005; the
   user-visible MVP is reached only when Phase 8 enables the real 013
   adapter.
4. **Enable**: When 013 merges, run Phase 8, then the README, release note
   and final validation.

## Task Status and Commit Discipline

- All tasks are intentionally unchecked at generation time.
- Do not mark a task complete until its implementation or documentation
  and its specified tests and validation are actually complete.
- Commit task-status changes separately from the code or documentation
  they track.

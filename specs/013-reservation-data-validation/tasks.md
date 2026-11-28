<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- Keep each task and its requirement references together. -->
<!-- markdownlint-disable MD013 -->

# Tasks: Verifiable Reservation Data

**Input**: Design documents in `specs/013-reservation-data-validation/`
(spec.md, plan.md, research.md, data-model.md,
contracts/guesty-reservation-data-validation.md, quickstart.md)

**Tracking Issue**: [#314](https://github.com/tykeal/homeassistant-guesty/issues/314);
related #297, #293, #223 and #321 (feature 014)

**Testing**: Tests are required by the specification (SC-001–SC-015), the
plan's validation plan and the project constitution. For every
production-code task, first add the listed tests and observe each new or
changed behavior fail for the intended reason before implementing the
minimum change. Tests marked as **baseline controls** protect existing
behavior: run and record them green before the code change and keep them
green afterward. Observe each red failure before implementation; the test
may stay uncommitted during that observation. Do not commit intentionally
failing tests or code, so every commit stays runnable.

**Updated existing tests**: Some live tests pin behavior this feature
changes on purpose. A task names each one it changes and the research
decision behind it. Update only those tests, record the reason in the
test docstring, and never weaken a test to make unrelated code pass.

**[P]**: The task adds separate test classes or touches separate files and
has no dependency on an incomplete task. Parallel test tasks may share a
test module when each adds its own class.

**[USn]**: The user story in `spec.md` the task serves. Foundational
tasks that serve several stories list each one.

**Organization**: Tasks follow the plan's six implementation phases
(plan § Implementation phase boundaries), after a setup gate:

| Tasks phase | Plan phase | Stories |
| --- | --- | --- |
| Phase 1 | setup gate | — |
| Phase 2 | 1 Validator | US1, US2 |
| Phase 3 | 2 Batch and sources | US1, US2 |
| Phase 4 | 3 Identity | US2 |
| Phase 5 | 4 Publication and staleness | US1, US2 |
| Phase 6 | 5 Reporting | US3 |
| Phase 7 | 6 Documentation and final gate | all |

The plan orders the phases by dependency rather than by story: every
story needs the Phase 2 validator, and the write-safety parts of US2
must exist before any rejection reaches `main`. IDs give traceability;
dependencies are stated explicitly below. Every task stays unchecked
until its work and verification are complete.

**Phase gates**: Each phase ends with a gate task. Record the gate commit
SHA, the CI run URLs and their conclusions for that SHA, and the local
validation output. A skipped, cancelled, pending, missing or failed CI job
is not a pass, and a later green run on a different SHA does not establish
an earlier gate.

**Merge unit (Phases 2–4)**: Phases 2, 3 and 4 are developed and gated as
separate phases but merge to `main` together, in one pull request.
Phase 2 starts rejecting records that today reach `observe_codes`
(for example an unrecognized string status), and only Phase 4 restores
their verified mappings to conflict detection as conflict-only evidence
and adds the revalidation and evidence-state refusals (research R5, R7
decision 1, R9, R18). Merging Phase 2 or 3 alone would weaken the
existing identity guard. Phases 5, 6 and 7 may each merge on their own
after the merge unit.

## Feature 014 interfaces (read before starting any task)

Feature 014 T001–T048 are merged (PR #325). Its fields and tests exist on
`main` (research R16). This feature provides the three interfaces in the
contract § Interfaces provided to feature 014. Their names are fixed;
do not rename them:

1. `ReservationFetchResult.rejected_ids: tuple[str, ...]` in
   `custom_components/guesty/api/models.py`: reuse 014's field unchanged
   and replace only the interim raw-minus-parsed computation in
   `reservations_v3._map_results` (T009).
2. `ConflictOnlyEvidence` in `custom_components/guesty/identity.py`,
   reached as `coordinator.identity.conflict_evidence`, with
   `available()`, `codes_for(reservation_id)` and `ids_for(code)` (T022).
3. `validate_record(raw, action_type) -> RecordValidation` with
   `ValidationReason` and `RecordValidation` in the new module
   `custom_components/guesty/api/validation.py` (T005).

Rules for every implementation agent:

- Do not edit `custom_components/guesty/actions.py` or
  `custom_components/guesty/api/actions.py`. 014 owns the
  `ConflictEvidence` port, `UnavailableConflictEvidence` and the future
  adapter (plan § Merge order with feature 014). Write refusals added
  here flow only through `ReservationIdentityGuard.classify_write_refusal`.
- Keep 014's `AcceptedRefreshSnapshot` and add 013's `VerifiedSnapshot`
  beside it; never merge the two (research R16).
- Keep 014's session sets in `reservations_fallback.py`
  (`_session_observed`, `_session_retired`, `_note_session`). Never
  remove IDs from them.
- 014's production adapter `_conflict_evidence()` keeps returning
  `UnavailableConflictEvidence` throughout this feature. Enabling it is
  014 T049–T056, which follow this feature (T043).

## Phase 1: Setup prerequisite gate

**Purpose**: Use the existing integration, test suites and locked
dependencies. No new dependency, option, entity or service is added.

- [ ] T001 Record the baseline on the current `main` SHA: run `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/` and `uv run --group releasenotes mypy custom_components/ tests/ scripts/`, and record that they pass. Confirm the 014 pieces this feature reuses exist: `returned_ids`, `rejected_ids` and `fallback_observed` on `ReservationFetchResult` (`custom_components/guesty/api/models.py`), the interim raw-minus-parsed computation in `_map_results` (`custom_components/guesty/api/reservations_v3.py`), `AcceptedRefreshSnapshot` (`custom_components/guesty/coordinator.py`), `check_identity_conflict` (`custom_components/guesty/identity.py`), and `ConflictEvidence` with `UnavailableConflictEvidence` (`custom_components/guesty/actions.py`). Confirm `custom_components/guesty/api/validation.py` does not exist. Record the result in the PR description of the first implementation PR. Do not modify `custom_components/guesty/` in this task.

**Checkpoint**: Baseline green; reused 014 pieces confirmed.

## Phase 2: Validator (plan phase 1; merge unit with Phases 3–4)

**Purpose**: Add the pure validator and build v3 models only from its
verified parts. Covers FR-001–FR-003a, FR-005–FR-007a and FR-008d at
record level.

- [ ] T002 [P] [US1] Add failing tests in new `tests/api/test_validation.py` (SPDX header), class `TestAssessV3Record`, for `assess_v3_record(raw) -> RecordAssessment`: parametrized `test_status_reasons` (`malformed_status` for a missing, empty or non-string `status`; `unrecognized_status` for a string not in `V3_STATUS_MAP`; recognized after `strip().lower()`, including `alteration` as a hold); `test_no_stay` (missing, empty or non-list `stay`); parametrized `test_segment_reasons` for `segment_malformed`, `segment_missing_listing`, `segment_malformed_parent` (list, object or empty-string `parentListingId`; absent and `null` stay verified), `segment_unverified_dates` and `segment_invalid_dates` (reversed dates and a zero-night `checkIn == checkOut`); `test_no_verified_segment`; `test_partial_record` (`raw_position` per raw segment, `raw_stay_count` equals the raw length, `accepted`, `partial` and `fully_verified` properties, `reason is None`); `test_record_reasons_follow_evaluation_order`; parametrized `test_top_level_localized_fill` covering the eligible feature 010 case and each predicate condition failing (two raw segments, a non-dict segment, a segment without a listing, a segment with its own `checkIn` or `checkOut`, an unparsable top-level `checkIn` or `checkOut`, and check-out not after check-in), where the date stays missing and the segment is rejected; `test_no_written_date_fallback` (a single-segment stay with only timestamp `checkIn`/`checkOut` and no localized date anywhere is `segment_unverified_dates`, record rejected); parametrized `test_invalid_optional_fields_become_none` (`guestsCount` negative, `bool` or string; `nightsCount`, planned arrival and departure, `source`, `notes`, `customFields` and `guestId` of wrong types) without rejecting; `test_conflict_code` (a non-empty string `confirmationCode` only for a non-hold status, on accepted and rejected records; `None` for a hold, a non-string or an empty code); and `test_never_logs_or_echoes_values` (a `caplog` sentinel in status, code, notes and listing values never appears in logs or in any reason) (research R1–R4; data-model § `ValidationReason`, § `SegmentAssessment` and `RecordAssessment`; contract C1–C3, C5–C7).
- [ ] T003 [P] [US2] Add failing tests in `tests/api/test_validation.py`, new class `TestValidateRecord`, for `validate_record(raw, action_type) -> RecordValidation`: parametrized `test_action_fact_table` (`add_reservation_note`, `set_reservation_status` and `set_custom_field` are valid for a fully verified record; any other action is `unsupported_action`); `test_missing_identifier` (missing, non-string or empty `reservationId`); `test_partial_record_is_partial_stay`; `test_rejected_record_reports_its_reason`; `test_hold_is_valid`; `test_reason_is_none_iff_valid`; `test_no_reason_value_is_valid` (no `ValidationReason` value equals `"valid"`, which 014's adapter relies on); `test_non_string_code_is_not_a_rejection`; and `test_pure` (no logging and no request; `custom_components.guesty.api.validation` imports nothing from `homeassistant`) (FR-008d; research R12; contract § Interfaces provided to feature 014, item 3).
- [ ] T004 [P] [US1] Add failing tests in `tests/api/test_models.py`, new class `TestV3ModelFromAssessment`: `test_rejected_record_returns_none` (including an unrecognized status, with no INFO log of the value); `test_fully_verified_record_is_unchanged` (baseline control: the same model as today for a valid record); `test_partial_record_keeps_only_verified_segments` (`stay` holds only verified segments, `raw_stay_count` keeps the raw length, `partial` is true, `check_in`, `check_out`, `check_in_local`, `check_out_local`, `nights_count` and `guests_count` are `None`, `listing_id` is the first verified segment's listing); `test_non_string_code_is_kept` (unchanged pass-through for 014's `code_kind=invalid`); and baseline control `test_stay_segment_from_api_dict_is_unchanged` (`GuestyStaySegment.from_api_dict` and models built directly keep their behavior) (research R1, R3, R4; data-model § `GuestyReservation`).
- [ ] T005 [US1] [US2] Implement new `custom_components/guesty/api/validation.py` (SPDX header, no Home Assistant import, no logging): `ValidationReason` (`StrEnum`, values and order exactly as data-model), frozen `SegmentAssessment`, `RecordAssessment` (with `accepted`, `fully_verified` and `partial`), `RecordValidation`, a non-logging `recognize_v3_status(raw) -> str | None` over `V3_STATUS_MAP` keys, `assess_v3_record(raw)` and `validate_record(raw: Mapping[str, object], action_type: str) -> RecordValidation`. Make T002 and T003 pass (research R1–R4, R12).
- [ ] T006 [US1] Add `partial: bool = False` to `GuestyReservation` and rewrite `GuestyReservation.from_v3_api_dict` in `custom_components/guesty/api/models.py` to build only from `assess_v3_record`'s verified parts (signature unchanged; `None` for a rejected record), removing the timestamp written-date fallback and the parser's `map_v3_status` call. Leave `GuestyStaySegment.from_api_dict`, `GuestyReservation.from_api_dict` and `map_v3_status` unchanged. Make T004 pass. Update, with a documented reason (research R4 decision 3), `TestV3TimingProvenance.test_feature_010_uses_written_dates_without_exact_fallback` in `tests/api/test_models.py` to expect rejection. Keep `test_malformed_listing_ids_mark_record_unverifiable` in `tests/test_coordinator.py` unchanged as a baseline control (plan § Merge order with feature 014).
- [ ] T007 Phase 2 gate: run `uv run pytest tests/api/test_validation.py tests/api/test_models.py`, `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm all CI jobs are green on the gate SHA and record the evidence. Do not merge to `main` yet (merge unit).

**Checkpoint**: The validator exists and the v3 model is built from it.
Not merged on its own.

## Phase 3: Batch and sources (plan phase 2; merge unit with Phases 2 and 4)

**Purpose**: Isolate malformed records in v3 and legacy batches, report
`rejected_ids`, `rejected_codes` and sanitized counts, exclude rejected
records from the legacy fallback and track per-record staleness. Covers
FR-003b, FR-003c, FR-005c, FR-008d, FR-012b, FR-012g and FR-012h.

- [ ] T008 [P] [US1] [US2] Add failing tests in `tests/api/test_reservations_v3.py`, new class `TestV3RecordIsolation`: `test_rejected_record_is_isolated` (other records publish; the rejected record is in `returned_ids`, not in `reservations`); baseline control `test_unverified_identifier_fails_the_refresh` (a non-dict item, a missing or non-string `reservationId`, a duplicate or a non-ascending ID still raise `GuestyResponseError` from `_validate_identifiers` before mapping); `test_rejected_ids_contents_order_and_exclusions` (rejected and partially valid records, including a rejected and a partially valid hold; excludes an accepted hold and valid records; sorted, de-duplicated; a subset of `returned_ids`); `test_partial_record_is_published_and_rejected`; `test_status_counts_are_sanitized` (recognized mapped statuses plus `unrecognized`; an injected raw status is never a key); `test_rejected_codes_pairs` (sorted `RejectedCode` pairs only for non-hold rejected or partial records with a non-empty string code; two rejected records sharing a code keep both pairs); parametrized `test_rejected_check_out_bounds` (latest verified segment checkout when every raw segment is dated; `utc_date(checkOut) + 1` for an explicit-offset top-level `checkOut`; a naive `checkOut` gives no candidate; the later candidate wins; no entry when neither exists); and `test_validation_summary_counts` (`rejected_records`, `partial_records`, `rejected_segments`, `reasons`). In class `TestV3ReturnedAndRejectedIds`, rename `test_rejected_ids_are_raw_minus_parsed` to `test_rejected_ids_are_assessment_based` and add a partially valid record that is in both `reservations` and `rejected_ids` (documented reason: research R16; 014 T010 note). Audit `TestV3Diagnostics.test_unusable_reservation_is_skipped` for the sanitized counts (research R2, R3, R5–R7; contract C1–C5, interface 1).
- [ ] T009 [US1] [US2] Implement in `custom_components/guesty/api/models.py`: `RejectedCode` (`NamedTuple(reservation_id: str, code: str)`), frozen `ValidationSummary`, and the new `ReservationFetchResult` fields `rejected_codes`, `rejected_check_out_bounds`, `validation`, `fallback_invalid_ids` and `stale_ids`, reusing 014's `returned_ids`, `rejected_ids` and `fallback_observed` unchanged. Add a pure retirement-bound helper to `custom_components/guesty/api/validation.py` (explicit-offset check on the raw string before parsing). In `custom_components/guesty/api/reservations_v3.py`, rewrite `_map_results` to call `assess_v3_record` once per item, replacing only the interim `rejected_ids` computation and the raw `status_counts`; keep `returned_ids` and `observed_ids`. Make T008 pass (research R2, R5–R7; data-model § `ReservationFetchResult`).
- [ ] T010 [P] [US1] [US2] Add failing tests in `tests/api/test_client.py`, new class `TestLegacyRecordValidation` (beside `TestGetReservations`), for `LegacyReservationSource`: `test_missing_or_non_string_id_fails_the_refresh` (an item that is not a dict or lacks a non-empty string `_id` raises; today it is skipped); `test_duplicates_are_first_wins_before_assessment` (across the primary and `checked_in` queries, a rejected record and an accepted duplicate never both survive); parametrized `test_rejected_record_reasons` (`malformed_status`, `unrecognized_status`, `invalid_listing` for a non-string, list or object `listingId`, `unverified_instant` for a missing, unparsable or naive `checkIn`/`checkOut`, `invalid_interval` for check-in not before check-out); `test_naive_instant_is_not_utc`; `test_rejected_records_fill_rejected_ids_and_codes` (same rules as v3, including retirement bounds from an offset-aware `checkOut`); and `test_previously_skipped_records_are_rejected` (records the current parser skips now feed `rejected_ids` and `rejected_codes`) (research R2, R5; contract C4, C5; § Legacy and fallback).
- [ ] T011 [US1] [US2] Implement legacy validation: add a pure legacy assessment to `custom_components/guesty/api/validation.py` (status allowlist, non-empty string `listingId`, explicit-offset instants, strict interval) and change `LegacyReservationSource._fetch_all_reservations` and `_merge_reservations` in `custom_components/guesty/api/reservations_legacy.py` to fail closed on a missing `_id`, de-duplicate raw items first-wins by `_id`, assess each record, build models only from verified records, and fill `rejected_ids`, `rejected_codes`, `rejected_check_out_bounds` and `validation`. `GuestyReservation.from_api_dict` keeps its behavior for direct callers. Make T010 pass (research R5).
- [ ] T012 [P] [US2] Add failing tests in `tests/api/test_reservations_fallback.py`, new classes `TestRejectedExclusion` (`resolve(..., rejected=...)` with fully rejected and partially valid IDs: never eligible or looked up; `_absorb` skips them; purged from `_served`, `_retained`, `_last_attempt` and `_stale`; discarded from `_confirmed_absent` and `_unreported_absent` and never confirmed absent; nothing retained for a partial record), `TestInvalidLookup` (a dict with the requested `_id` that fails legacy validation is `_INVALID`: purged, never confirmed absent, in `FallbackOutcome.observed` and in `legacy_observed_reservations` through an explicit `_note_session("observed", id)` even with no code, a `RejectedCode` and bound in `FallbackOutcome.rejected_codes` and `rejected_check_out_bounds` for a non-hold string code, its ID in `FallbackOutcome.invalid`; baseline control: a non-dict document, another `_id`, transport, HTTP and non-JSON failures stay `_FAILED`) and `TestPerRecordStale` (a failed due lookup of a published ID adds it to `_stale` and `FallbackOutcome.stale`; a deferred lookup changes nothing; `_OK`, `_absorb`, retirement and purge clear it; a served target stays routable and a retained-only target stays unroutable). In `tests/api/test_reservations_v3.py`, new class `TestV3FallbackMerge`: `fetch` passes all of `rejected_ids` as `rejected`, merges `FallbackOutcome.rejected_codes` and `rejected_check_out_bounds` into the result, and copies `invalid` to `fallback_invalid_ids` and `stale` to `stale_ids`; `_INVALID` IDs are never in `rejected_ids` (research R8; contract C2, C12; FR-012b, FR-012g, FR-012h).
- [ ] T013 [US2] Implement in `custom_components/guesty/api/reservations_fallback.py`: `resolve(..., rejected: Collection[str] = ())`, the `_INVALID` verdict in `_lookup` (split `_parse` so a dict with the requested `_id` is assessed with T011's legacy assessment; call `_note_session("observed", id)` explicitly on that branch), `_stale` tracking, and `FallbackOutcome.stale`, `invalid`, `rejected_codes` and `rejected_check_out_bounds`. In `V3ReservationSource.fetch` (`custom_components/guesty/api/reservations_v3.py`), pass `rejected=` and merge the new outcome fields into the `replace` call beside `fallback_observed`. Keep the live in-place cache updates and 014's session sets. Make T012 pass (research R8).
- [ ] T014 Phase 3 gate: run `uv run pytest tests/api/test_reservations_v3.py tests/api/test_reservations_fallback.py tests/api/test_client.py tests/api/test_timing.py`, `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm all CI jobs are green on the gate SHA and record the evidence. Do not merge to `main` yet (merge unit).

**Checkpoint**: Sources isolate malformed records and report rejections.
Interface 1 is complete. Not merged on its own.

## Phase 4: Identity and conflict-only evidence (plan phase 3; closes the merge unit)

**Purpose**: Feed rejected mappings to conflict detection, persist
conflict-only evidence, migrate the identity store with revalidation, and
add the two pre-membership refusals. Covers FR-008–FR-008c, FR-012d–FR-012f
and completes the 014 interfaces.

- [ ] T015 [P] [US2] Add failing tests in `tests/test_identity.py`, new class `TestConflictOnlyObservation`: `observe_codes(reservations, conflict_only=...)` runs one collision scan, so a shared code between two rejected records or between a rejected and an accepted record is a within-refresh collision (ambiguous abort); the conflict-only `code → id` map is returned apart from `mapping`; `compare(..., conflict_only=...)` keeps the existing outcomes (same-path stored conflict aborts, cross-path is soft); conflict-only pairs never add to `matched` or `cross_path_matched` and never enter `codes`, `current_ids()` or `known_ids()`; an ID in `fallback_ids` (including `fallback_invalid_ids`) keeps a hard outcome; a stored pair with `fallback: true` is never soft and never confirms; a stored code with several IDs is checked against each one under `SOURCE_CONFLICT_ONLY` (research R7 decisions 2–4; contract C13; FR-008b).
- [ ] T016 [P] [US2] Add failing tests in `tests/test_identity.py`, new class `TestConflictOnlyEvidenceStore`, for `ConflictOnlyEvidence`: the separate `Store` (version 1, key `guesty.{entry_id}.reservations_conflict_evidence`) and payload shape; `record()` adds pairs, keeps shared codes and several codes per ID, sets `fallback` only ever to true, and moves a re-observed bound only later or to `null`; `retire()` by supersession, by confirmed absence and by the horizon (`check_out_bound < utc_date(now − past_days) − 1` and not returned), never retiring an undated pair on the horizon; `MAX_CONFLICT_ONLY_EVIDENCE` (5000) saturation with no eviction, sticky and persisted; the 80% and 70% near-capacity thresholds; load validation that makes the store `compromised` for a malformed entry, a duplicate pair, a bad `covered_past_days`, a corrupt or unreadable file, or a `generation` below the baseline's `conflict_evidence_generation` (a missing store counts as 0), while a higher generation is accepted; a cleanly parsed lagging store still feeds `compare_maps()`; state precedence `compromised`, `saturated`, `unstored`, `revalidating`, `ready`; `available()` true only in `ready`; `codes_for` and `ids_for` return persisted pairs only (never accepted maps) and raise `RuntimeError` when unavailable; `state`, `count`, `dated` and `undated`; and `async_remove()` deletes the store (research R7; data-model § `ConflictOnlyEvidence`; contract C14, interface 2; FR-008a, FR-008c).
- [ ] T017 [P] [US2] Add failing tests in `tests/test_identity.py`, new class `TestIdentityStoreMigration`: the identity `Store` is version 1, minor version 2 through `_IdentityStore`; `_async_migrate_func(1, 1, data)` sets `revalidation_pending`; a migrated minor-version-1 payload downgrades **every** `known` candidate hint to unknown timing (not fallback-eligible, no `check_outs`, bootstrap pair retained by `_retired_bootstrap`); a minor-version-2 payload with the flag clear keeps a valid `known` hint, downgrades an invalid one and drops an unparsable `observed_at`; code maps keep only non-empty `str → str` entries; `legacy_codes`, `bootstrap_codes`, `continuity` and `divergent` are kept after structural validation; nothing dropped is treated as absent; `to_dict`/`from_dict` round-trip `revalidation_pending` and `conflict_evidence_generation` with unknown-key tolerance; and a rollback-compatible payload that a minor-version-1 `Store` loads (research R9; quickstart § Upgrade verification steps 1–3; FR-012d, FR-012e).
- [ ] T018 [P] [US2] Add failing tests in `tests/test_identity.py`, new classes `TestEvidenceSaveOrder` and `TestEvidenceWriteRefusals`. Save order (research R7 decision 8): step 1 runs on every accepted refresh, even with no pairs; a failed first evidence save loads `compromised` after a restart; pairs from a failed identity save stay pending and reach the next successful save and `compare`; both saves failing then a restart loads `revalidating`; a refresh with no evidence changes loads `revalidating`, not `compromised`, after a restart and becomes `ready` after the next step 1 and step 2 saves; a compromised store is never written and stays `compromised` after an accepted refresh and a restart; identity state is frozen while `compromised` or `saturated` (continuity not advanced, bootstrap not retired, `revalidation_pending` kept). Refusals: `classify_write_refusal` keeps the existing refusals and their order, then returns `REFUSAL_EVIDENCE_UNAVAILABLE` for `unloaded`, `unstored`, `saturated` and `compromised` (naming the repair issue only for the last two) and `REFUSAL_REVALIDATING` only for `revalidating`, both before `missing_current_code`; a compromised store during pending revalidation gives `REFUSAL_EVIDENCE_UNAVAILABLE`; every load refuses as revalidating until an accepted refresh completes steps 1 and 2; a write issued while step 2 awaits storage, for a current ID this refresh newly rejected, is refused as revalidating; growing `past_days` beyond `covered_past_days` refuses as revalidating until a refresh under the wider window is stored, which records a re-entered rejected pair again; messages are fixed strings with no codes (research R7 decisions 5, 7, 8, R18; contract C15, C16; FR-008, FR-012d).
- [ ] T019 [P] [US2] Add failing tests in `tests/test_identity.py`, class `TestBootstrapSweep`: `test_rejected_legacy_pair_collision_is_ambiguous` (`async_bootstrap_identity` scans with `observe_codes(fetched.reservations, conflict_only=fetched.rejected_codes)` and gives `BOOTSTRAP_AMBIGUOUS`); `test_rejected_legacy_pairs_are_recorded_before_identity` (legacy path, never confirming, evidence additions saved before `async_record_bootstrap`'s identity save with the advanced generation); and `test_missing_id_gives_unavailable` (`BOOTSTRAP_UNAVAILABLE`, writes stay refused) (research R5).
- [ ] T020 [P] [US2] Add failing tests in `tests/test_coordinator.py`, new class `TestCoordinatorConflictEvidence`: `_check_identity` records only fully verified, non-partial records and passes `rejected_codes` as conflict-only; an `_INVALID` pair reaches `compare` and the store; an `_INVALID` pair whose code is in `legacy_codes` or `codes` with another ID aborts as ambiguous, in the same refresh and after it is stored; `async_record_refresh` pops hints for `rejected_ids` and `fallback_invalid_ids` without treating them as absent; the routing sets (`fallback_served_ids`, `fallback_sourced_ids`) are assigned only in the `on_baseline_stored` callback, so after an identity abort (with `mark_ambiguous()`; every write refused with no request), a failure after `_check_identity`, a failed identity save and a non-`OSError` failure after the step 2 save they match the stored baseline; a write issued while the step 3 save is suspended uses the new routing; `last_fallback_counters` keeps its live assignment; repeated failures add no lookups beyond the feature 008 limits; the `past_days` supplier follows an options change without a reload; and `coordinator.identity.conflict_evidence` is the `ConflictOnlyEvidence` that 014's adapter reads (research R7, R8; contract C10 routing, C13; data-model § `ReservationFallbackSource`).
- [ ] T021 [P] [US2] Add failing tests in `tests/test_actions.py`, new class `TestEvidenceStateWriteRefusals`, and in `tests/test_custom_field_service.py`, new class `TestEvidenceStateCustomFieldRefusals`: for `add_reservation_note`, `set_reservation_status` and `set_custom_field`, every non-`ready` evidence state (`unloaded`, `revalidating`, `unstored`, `saturated`, `compromised`) refuses with a plain `GuestyActionError` (not `ReservationIdentityPresentError` or `ReservationIdentityAbsentError`), zero feature 012 recovery refreshes, zero by-ID GETs and zero write requests; a rejected or partially valid target is refused through the membership path; a corrected target becomes writable after the single recovery refresh; a valid target filtered from publication by listing, window or status stays writable (research R11, R18; contract C15–C17; SC-007; FR-008, FR-012f).
- [ ] T022 [US2] Implement in `custom_components/guesty/identity.py`: `ConflictOnlyEvidence` (states, `available()`, `codes_for`, `ids_for`, `record`, `retire`, `compare_maps`, `async_load`, `async_save`, `async_remove`, diagnostics accessors), `MAX_CONFLICT_ONLY_EVIDENCE = 5000`, `SOURCE_CONFLICT_ONLY`, `_IdentityStore` (minor version 2, `_async_migrate_func`), the new `IdentityBaseline` fields and load-time revalidation, `observe_codes(..., conflict_only=())`, `compare(..., conflict_only=...)`, `async_record_refresh(..., rejected_ids, fallback_invalid_ids, rejected_codes, rejected_check_out_bounds, on_baseline_stored)` with the three-step save order, the bootstrap order in `async_bootstrap_identity` and `async_record_bootstrap`, `ReservationIdentityGuard.conflict_evidence`, a `past_days` supplier argument, `async_load` loading both stores, `async_remove` deleting both, and `REFUSAL_EVIDENCE_UNAVAILABLE` and `REFUSAL_REVALIDATING` in `classify_write_refusal` after the ambiguity check and before membership. Make T015–T019 pass (research R7, R9, R18; data-model § `ConflictOnlyEvidence`, § `IdentityBaseline`).
- [ ] T023 [US2] Wire the coordinator in `custom_components/guesty/coordinator.py`: construct `ReservationIdentityGuard(hass, entry.entry_id, ...)` with the `past_days` supplier; pass only fully verified, non-partial records and the conflict-only pairs from `_check_identity`; pass the new arguments from `_record_identity`; compute the candidate served and sourced sets where `_async_fetch_reservations` assigns them today but assign `fallback_served_ids` and `fallback_sourced_ids` only in the `on_baseline_stored` callback. Make T020 and T021 pass without editing `actions.py` (research R8, R18).
- [ ] T024 Phase 4 gate (merge-unit gate): run `uv run pytest tests/test_identity.py tests/test_coordinator.py tests/test_actions.py tests/test_custom_field_service.py`, `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm the three 014 interfaces match the contract § Interfaces provided to feature 014 exactly (names, modules, signatures and the `rejected_ids` definition); confirm all CI jobs are green on the gate SHA and record the evidence. Merge Phases 2–4 to `main` together.

**Checkpoint**: Rejected data never authorizes a write, conflict-only
evidence is persisted and fail-closed, and all three 014 interfaces exist.

## Phase 5: Publication and staleness (plan phase 4)

**Purpose**: One pure publication function for accepted and failed
refreshes, verified-timezone expiry and window rules, and stale
last-verified data that stays visible. Covers FR-001, FR-003,
FR-004–FR-004c, FR-005b, FR-012a–FR-012c, SC-003 and SC-010.

- [ ] T025 [P] [US1] Add failing tests in `tests/test_coordinator.py`, new class `TestVerifiedPublication`: the verified-timezone predicate (`timezone_explicit` and a loadable `ZoneInfo`; explicit `UTC` is verified; the model default is not); parametrized `test_past_confirmed_stay_expires` for single-raw-segment v3, multi-segment v3 and legacy projections (`checked_out`, `reservation_status=None`, still in history within the window); `test_checkout_day_is_uncertain` (verified timezone, date-only checkout on the local day: status `unknown`, `reservation_status` set, `timing_status` `unknown`); `test_unverified_timezone_expiry_bound` (expired only when `check_out_local < utc_date − 1`, otherwise uncertain); `test_unverified_timezone_window_is_conclusive` (`check_out_local >= utc_date + 1 − past_days` and `check_in_local <= utc_date − 1 + future_days`; an inconclusive stay is not published but stays writable); `test_partial_stay_projection` (per-segment window, segment-aware projection even with one survivor, no reservation-level span, same-date handoffs only between raw-adjacent survivors); `test_unrecognized_status_from_sources_is_never_published` (v3 and legacy); and baseline control: verified-timezone exact instants and DST gap or fold handling are unchanged (research R3, R4; contract C6–C9; FR-004–FR-004c, FR-005a–FR-005c, FR-006, FR-007).
- [ ] T026 [P] [US1] Add failing tests in `tests/test_reservations_parity.py`, class `TestLegacyV3Parity`: an expired stay and a checkout-day stay project the same way from v3 and legacy sources (research R4 decision 6; SC-002, SC-009, SC-012).
- [ ] T027 [P] [US2] Add failing tests in `tests/test_coordinator.py`, new class `TestStaleSnapshot`: a `VerifiedSnapshot` is stored only through the `on_baseline_stored` callback of an accepted refresh and replaced wholesale; parametrized over `UpdateFailed`, `ReservationSearchRejected`, an identity abort, another exception and an `asyncio.CancelledError` that Home Assistant counts as a failure, a failed refresh re-projects the snapshot at the current instant under the current options and sets `data` and `snapshot_stale` before the failure reaches `_async_refresh`; an expired stale stay is not current; a re-projection that raises sets `data` to `None`, clears `snapshot_stale` and re-raises the original failure; with no snapshot nothing is invented; the `_async_refresh` override calls `async_update_listeners()` after a consecutive failure; an accepted refresh clears `snapshot_stale` and removes what it showed invalid, while a refresh attempt does not; `stale_display_available` and `last_verified_at` follow data-model; `stale_reservation_ids` follows `stale_ids`; `last_update_success` is never overridden; stale display never satisfies a write guard (`accepted_refresh_snapshot()` stays `None`, and `guard_reservation_write` reads `data` only to choose between the Present and Absent errors) (research R10, R13; contract C10, C11; FR-012a–FR-012c).
- [ ] T028 [P] [US2] Add failing tests in `tests/test_reservation_sensor.py`, new class `TestStaleReservationDisplay`, through a set-up config entry: after a failed refresh with a snapshot, `GuestyReservationSensor` and `GuestyFinancialSensor` stay available with `stale: true` and an ISO 8601 `last_verified_at`, also after a second consecutive failure; recorded `state_changed` events show no intermediate `unavailable` state on a first failure; with no snapshot, or after a re-projection that raises, both are unavailable; the other availability checks (listing still tracked, `data` present, the money field) still apply; `upcoming` items carry `stale`; per-record staleness after an accepted refresh sets `stale` with `last_verified_at` `null`; the financial sensor's `stale` follows its selected reservation's per-record staleness; and an accepted refresh clears both markers. Update, with a documented reason (research R13), the `MagicMock` coordinators in `TestSensorErrorResilience` (`test_sensor_stale_data_accessible_when_coordinator_fails`, `test_sensor_recovers_with_fresh_data`, `test_unavailable_during_outage_prevents_misfire`) and any other mock coordinator used for availability (including `mock_coordinator` in `tests/conftest.py`) to set `stale_display_available = False`, so they stay baseline controls for the no-snapshot case. Keep `TestReservationSensorEnumConfig.test_options_includes_unknown_status` and `TestPrioritySelection.test_unknown_status_passed_through` as baseline controls: they inject models directly, which sources can no longer produce (data-model § `SegmentAssessment`) (contract C10, C12; SC-010, SC-015).
- [ ] T029 [P] [US1] Add failing tests in `tests/test_reservation_sensor.py`, new class `TestExpiryBeforeUpcomingLimit`: eleven conclusively expired stays plus one future stay show the future stay as upcoming (SC-003), for single-raw-segment v3, multi-segment v3 and legacy data; and `_select_reservation` priority is unchanged (baseline control) (research R4 decision 9; FR-004c).
- [ ] T030 [US1] Implement the shared publication pipeline in `custom_components/guesty/coordinator.py`: one pure function taking the verified records, the current instant, options, listings and verified timezones, running attribution, expiry and uncertainty, the window and projection with `stale`; a shared verified-timezone predicate and expiry predicate used by `_project` (including its `len(stay) <= 1` branch), `_segment_status` and legacy projections; `_within_window` with the conclusive bound and per-segment membership for partial records; `_resolve_v3_reservations` keeping partial records' reservation-level fields `None`; `_apply_same_date_handoffs` using raw positions. Make T025, T026 and T029 pass (research R3, R4).
- [ ] T031 [US2] Implement staleness in `custom_components/guesty/coordinator.py`: `VerifiedSnapshot` (memory-only, distinct from `AcceptedRefreshSnapshot`), `_verified_snapshot`, `snapshot_stale`, `stale_reservation_ids`, the `stale_display_available` and `last_verified_at` properties, snapshot assignment in the `on_baseline_stored` callback, the failure path in `_async_update_data` (re-project, set `data` and `snapshot_stale` before re-raising, including `CancelledError`; on a re-projection failure set `data` to `None` and clear `snapshot_stale`), and `async_update_listeners()` after a consecutive failure in the `_async_refresh` override. Never assign `last_update_success`. Make T027 pass (research R10; data-model § `VerifiedSnapshot`).
- [ ] T032 [US2] Implement in `custom_components/guesty/sensor.py`: replace `super().available` in `GuestyReservationSensor.available` and `GuestyFinancialSensor.available` with `coordinator.last_update_success or coordinator.stale_display_available`, keeping every other check; add top-level `stale` and `last_verified_at` to the reservation sensor attributes and item-level `stale` in `_build_upcoming`; add `stale` and `last_verified_at` beside `listing_id` on the financial sensor; show the uncertain status through `_derive_state`. Make T028 pass (research R13; data-model § Reservation entity availability).
- [ ] T033 Phase 5 gate: run `uv run pytest tests/test_coordinator.py tests/test_reservation_sensor.py tests/test_reservations_parity.py tests/api/test_timing.py`, `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm all CI jobs are green on the gate SHA and record the evidence.

**Checkpoint**: Expired stays no longer crowd `upcoming`; failed refreshes
show last-verified data marked stale; nothing is invented.

## Phase 6: Reporting (plan phase 5)

**Purpose**: Non-repeating, privacy-safe exclusion reports, repair issues
and diagnostics. Covers FR-009–FR-011 and SC-004.

- [ ] T034 [P] [US3] Add failing tests in `tests/test_coordinator.py`, new class `TestExclusionReporting`, for `ExclusionTracker`: a new `(reason, reservation or listing ID)` key logs one WARNING; a repeat logs at DEBUG; a key absent from an accepted refresh is forgotten and warns again on recurrence; a failed refresh resolves nothing; above `MAX_TRACKED_EXCLUSIONS` (256) new keys log at DEBUG with one aggregate WARNING per overflow episode, ending when the set falls below the bound; messages hold only the reason and the ID (research R14; contract C18; FR-011, SC-004).
- [ ] T035 [P] [US3] Add failing tests in `tests/test_coordinator.py`, new class `TestValidationRepairs`, and in `tests/test_init.py`, new class `TestValidationIssueRemoval`: `listing_timezone_unverified_{entry_id}_{listing_id}` (warning, non-fixable, placeholders `listing_name` and `listing_id`) is created for a tracked listing with reservations and an unverified timezone and deleted when the timezone is verified or the listing is untracked; `conflict_evidence_near_capacity_{entry_id}` (warning, one WARNING log) at 80% and deleted below 70%; `conflict_evidence_unavailable_{entry_id}` (error) only for `saturated` and `compromised`; `async_remove_entry` deletes these issues and the evidence store, while an unload or reload keeps the store (research R7 decision 6, R14; data-model § Repair issues; contract C19; FR-009).
- [ ] T036 [P] [US3] Add failing tests in `tests/test_diagnostics.py`, new class `TestValidationDiagnostics`: the `reservations.validation` block matches data-model § Diagnostics exactly; `rejected_ids` is sorted and capped at 100 with `rejected_ids_truncated`; `conflict_evidence` holds `state`, `available`, `count`, `dated`, `undated`, `bound` and `near_capacity` and no codes or IDs; `stale` holds `snapshot_stale`, `stale_count` and `last_verified_at` (`null` unless `snapshot_stale`); `refresh.status_counts` is sanitized; diagnostics never show which IDs have stored pairs (research R15; FR-010).
- [ ] T037 [P] [US3] Add failing tests in `tests/test_privacy_logging.py`, new class `TestValidationPrivacy`: sentinel confirmation codes, guest names, notes, custom-field values, unrecognized status strings and raw payload markers from v3, legacy and `_INVALID` by-ID records never appear in `caplog`, diagnostics or repair placeholders (research R14, R15; contract C20; FR-010, SC-005).
- [ ] T038 [US3] Implement `ExclusionTracker` and the repair-issue reconciliation in `custom_components/guesty/coordinator.py` (after accepted refreshes only), the `reservations.validation` block in `custom_components/guesty/diagnostics.py`, issue deletion in `async_remove_entry` in `custom_components/guesty/__init__.py`, and the three issue translations in `custom_components/guesty/strings.json` and `custom_components/guesty/translations/en.json`. Make T034–T037 pass (research R14, R15).
- [ ] T039 Phase 6 gate: run `uv run pytest tests/test_coordinator.py tests/test_diagnostics.py tests/test_privacy_logging.py tests/test_init.py`, `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/` and `uv run pre-commit run --all-files`; confirm all CI jobs are green on the gate SHA and record the evidence.

**Checkpoint**: Exclusions are observable without exposing private data.

## Phase 7: Documentation and final gate (plan phase 6)

- [ ] T040 [P] Update `README.md`: under "Reservations: the v3 migration", add operator notes on unverified listing timezones (date-only, uncertain checkout-day status, the repair issue), the conflict-evidence repair issue and its remedy (remove and re-add the entry), revalidation after an upgrade, a restart or a `past_days` increase, stale display after a failed refresh (`stale` and `last_verified_at`), and the removal of the feature 010 written-date fallback; extend "Reservation writes are fail-closed" with the two new refusals (plan phase 6).
- [ ] T041 [P] Create a release note with `uv run --group releasenotes reno new reservation-data-validation-314` and edit the generated `releasenotes/notes/reservation-data-validation-314-<hash>.yaml` (SPDX header as in existing notes) to describe the visible changes in quickstart § User-visible contract, the revalidation refusal after upgrade, and the removed written-date fallback, referencing #314 and #297 without claiming #223 is fixed. Run `uv run --group releasenotes reno lint`.
- [ ] T042 Home Assistant 2026.4.0 compatibility check: in the locked 2026.10.4 environment and in an isolated 2026.4.0 environment built without changing `uv.lock`, run the `Store` minor-version migration and rollback tests (T017), and the stale-entity availability, attribute, consecutive-failure and `CancelledError` tests (T027, T028). Confirm in each installed Home Assistant source that `CoordinatorEntity.available` returns `last_update_success`, that state attributes are written only for available entities, that `_async_refresh` notifies listeners only on the first failure, and that `Store` loads a newer minor version. Record the installed versions and results; if 2026.4.0 cannot be built, report the gate as blocked (plan § Validation plan).
- [ ] T043 Final validation (depends on T040–T042): run the focused suites and the upgrade verification steps 1–8 in `specs/013-reservation-data-validation/quickstart.md`, then `uv run pytest tests/ -x -q`, `uv run ruff check custom_components/ tests/ scripts/`, `uv run ruff format --check custom_components/ tests/ scripts/`, `uv run --group releasenotes mypy custom_components/ tests/ scripts/`, `uv run pre-commit run --all-files` and `uv run --group releasenotes reno lint`; confirm all CI jobs are green on the final SHA and record the evidence. Do not perform or claim live Guesty verification. After merge, comment on #321 that feature 014 T049–T056 may start against the merged interfaces; do not edit 014 code or its task checkboxes here.

**Checkpoint**: All local and CI gates pass; 014 enablement is unblocked.

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1** → everything.
- **Phase 2** (T002–T007) → Phase 3.
- **Phase 3** (T008–T014) → Phase 4.
- **Phase 4** (T015–T024) closes the merge unit; Phases 2–4 merge
  together after T024.
- **Phase 5** (T025–T033) depends on Phase 4 (the snapshot is assigned in
  the `on_baseline_stored` callback).
- **Phase 6** (T034–T039) depends on Phase 4 (evidence states and
  counts) and Phase 5 (stale diagnostics).
- **Phase 7** (T040–T043) depends on Phase 6.

### Within-task ordering

- T005 after T002 and T003; T006 after T004 and T005.
- T009 after T008; T011 after T009 and T010; T013 after T011 and T012.
- T022 after T015–T019; T023 after T020–T022.
- T030 after T025, T026 and T029; T031 after T027 and T030; T032 after
  T028 and T031.
- T038 after T034–T037.
- T043 after T040–T042.

### User story dependencies

- **US1 (P1)**: the validator (Phase 2), source isolation (Phase 3) and
  publication (Phase 5).
- **US2 (P1)**: needs US1's rejections; its write safety (Phase 4) must
  merge with them, and its stale display follows in Phase 5.
- **US3 (P2)**: reports what US1 and US2 exclude (Phase 6).

### Feature 014 enablement follows 013

Feature 014 T049–T056 (`specs/014-by-id-write-fallback/tasks.md`
Phase 8 and Phase 9) are blocked on this feature. They may start only
after T024's merge unit is on `main`, which provides all three
interfaces; T043 announces it on #321. They wire `_conflict_evidence`
in `actions.py` to `coordinator.identity.conflict_evidence` and
`validate_record`, and expect this feature's pre-membership
`REFUSAL_EVIDENCE_UNAVAILABLE` and `REFUSAL_REVALIDATING` refusals.

### Traceability

Scenario shorthand `US1-S1..S21`, `US2-S1..S27` and `US3-S1..S3` refers to
the numbered acceptance scenarios in `spec.md`.

| Requirement or outcome | Tasks |
| --- | --- |
| FR-001, FR-003 | T002, T004–T006, T025, T030 |
| FR-002 | T002, T005, T006, T008, T010, T025 |
| FR-003a | T002, T008, T021, T025 |
| FR-003b, FR-003c | T008–T011 |
| FR-004–FR-004c | T025, T026, T029, T030 |
| FR-005, FR-005a, FR-005b | T002, T004, T006, T025, T030 |
| FR-005c | T002, T012, T013, T025 |
| FR-006, FR-007, FR-007a | T002, T010, T011, T025, T030, T035 |
| FR-008 | T015, T018, T020–T023 |
| FR-008a–FR-008c | T015, T016, T019, T022 |
| FR-008d | T003, T008, T009, T012 |
| FR-009–FR-011 | T034–T038 |
| FR-012, FR-012a–FR-012c | T027, T028, T031, T032 |
| FR-012d, FR-012e | T017, T018, T022 |
| FR-012f | T016, T021 |
| FR-012g, FR-012h | T012, T013, T028 |
| SC-001, SC-004, SC-005 | T002, T008, T034, T037 |
| SC-002, SC-003, SC-009, SC-012 | T025, T026, T029 |
| SC-006, SC-013 | T016–T019 |
| SC-007 | T021 |
| SC-008, SC-014 | T002, T004, T025 |
| SC-010, SC-015 | T012, T027, T028 |
| SC-011 | T008, T010 |
| 014 interfaces | T003, T005, T008, T009, T016, T022, T024 |
| Compatibility (HA 2026.4.0) | T017, T042 |

### Parallel opportunities

- Phase 2: T002, T003 (separate classes) and T004 (separate module).
- Phase 3: T008, T010 and T012 touch separate test modules.
- Phase 4: T015–T019 are separate classes in `tests/test_identity.py`;
  T020 and T021 are in other modules.
- Phase 5: T025 and T027 are separate classes; T026, T028 and T029 are in
  other modules or classes.
- Phase 6: T034–T037 are separate classes or modules.
- Phase 7: T040 and T041 touch separate files.

### Implementation strategy

1. **Safety first**: Phases 2–4 land together, so no record is rejected
   on `main` before its mapping reaches conflict detection and every
   write is refused while evidence is not `ready`.
2. **MVP**: the merge unit plus Phase 5 delivers US1 and US2 (both P1):
   verified publication, expiry before the limit and stale display.
3. **Then**: Phase 6 (US3 reporting) and Phase 7 (docs, release note,
   compatibility and final gates).
4. **Hand-off**: feature 014 T049–T056 follow.

## Task Status and Commit Discipline

- All tasks are intentionally unchecked at generation time.
- Do not mark a task complete until its implementation or documentation
  and its specified tests and validation are actually complete.
- Commit task-status changes separately from the code or documentation
  they track.

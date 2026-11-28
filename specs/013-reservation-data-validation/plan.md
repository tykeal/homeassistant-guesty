<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Verifiable Reservation Data

**Date**: 2026-11-27 | **Spec**: [spec.md](spec.md)
**Input**: `specs/013-reservation-data-validation/spec.md`

## Summary

Publish and act on reservation data only when it is verified. One pure
validator in a new `api/validation.py` assesses each raw v3 record and
each raw stay segment:

- recognized status allowlist;
- segment listing and parent checks;
- canonical localized dates with check-in before check-out;
- no timestamp written-date fallback.

Isolation and integrity:

- A record with a batch-verified identifier that fails assessment is
  skipped. A record without one, or any page-integrity failure, still
  fails the refresh closed.
- A partially valid reservation publishes only its verified segments,
  under raw-stay eligibility, and is never a current writable identity.
- Rejected records feed conflict detection only, through a bounded,
  separately persisted conflict-only evidence store. Every write is
  refused when that store is unavailable, saturated or compromised.

Upgrade:

- The identity `Store` gains a minor version.
- Stored mappings and hints are revalidated, and writes are refused
  until the first accepted refresh under the new rules.

Publication:

- Expiry, window and checkout-day uncertainty use a verified listing
  timezone, or a zone-independent offset bound, and run before the
  ten-item upcoming limit.
- After a failed refresh, the last verified snapshot is re-projected at
  the current instant and marked stale. Reservation entities stay
  available showing it with `stale` and `last_verified_at`; with no
  snapshot they are unavailable.

Reporting is privacy-safe and non-repeating: once-per-key warnings,
per-listing timezone repairs and a counts-only diagnostics block.

The feature provides exactly the three interfaces feature 014 needs:

- `ReservationFetchResult.rejected_ids`;
- `coordinator.identity.conflict_evidence` (`available`, `codes_for`,
  `ids_for`);
- `api.validation.validate_record(raw, action_type)`.

## Technical Context

- **Language/Version**: Python 3.14+ (`pyproject.toml` requires
  `>=3.14.2`).
- **Primary Dependencies**: Home Assistant `>=2026.4.0`, voluptuous and
  httpx. The locked environment is Home Assistant `2026.10.4`.
- **Storage**:
  - The existing per-entry identity `Store` moves to version 1, minor
    version 2.
  - One new per-entry `Store`,
    `guesty.{entry_id}.reservations_conflict_evidence` (version 1),
    holds codes and IDs only.
  - Coordinator snapshots and staleness are memory-only.
- **Testing**: pytest, pytest-homeassistant-custom-component, respx and
  pytest-httpx, run through `uv`.
- **Target Platform**: Home Assistant custom integration.
- **Project Type**: Python Home Assistant integration.
- **Performance Goals**:
  - No additional Guesty requests.
  - Validation is O(records × segments) per refresh.
  - Re-projection after a failure reuses the in-memory snapshot.
  - Conflict-only evidence is bounded at 5000 entries.
  - The warning tracker is bounded at 256 keys.
- **Constraints**:
  - The API boundary holds: `api/` has no Home Assistant imports.
  - No guest data, codes, notes, custom-field values, unrecognized
    statuses or payloads go to logs, diagnostics or repairs.
  - Feature 008, 010, 011, 012 and 014 guards and outcomes are
    preserved.
  - No new options, entities, services or service fields.
- **Scale/Scope**:
  - v3, legacy and legacy by-ID fallback reservation records.
  - Reservation sensors, binary sensors and calendar consumers, where
    they exist.
  - Diagnostics, repairs and the three reservation write actions.

No `NEEDS CLARIFICATION` item remains (see [research.md](research.md)).

## Constitution Check

Pre-research gate: **PASS**.

- **Code quality and TDD**: Every phase starts with failing tests:
  - the validator, then batch isolation, then identity and migration,
    then publication and staleness, then reporting;
  - full type annotations, docstrings, ruff and mypy.
- **API client boundary**: Validation, assessment and the feature 014
  validator are pure functions in `custom_components/guesty/api/`.
  `Store`, repairs and logging policy stay in the integration layer.
- **Security and privacy**:
  - Conflict-only codes live only in the separate `Store`, under the
    same handling as feature 008 identity codes.
  - Diagnostics are counts, states and capped reservation IDs.
  - Reasons are fixed enums.
  - Sentinel tests cover logs and diagnostics.
- **Performance**: No added I/O to Guesty. Every new state is bounded
  with no unbounded growth, and saturation fails closed instead of
  evicting.
- **Phased development**: Six implementation phases (below) each end in
  a green CI checkpoint.
- **Licensing and commit gates**: New docs and code carry SPDX headers,
  and commits follow the project conventions.
- No violation or scope expansion is required.

## Phase 0 — Research Findings

[research.md](research.md) records R1–R18. The main consequences are:

- **The feature 010 written-date fallback is removed** (R4). It
  converted a timestamp's written date into a "local" date, which FR-006
  forbids. Single-raw-segment stays still use top-level localized dates.
  Without any localized date, they are rejected until #223 provides
  evidence.
- **Batch integrity is anchored on the existing page and identifier
  validation** (R5). Isolation applies only to records with
  batch-verified IDs. The legacy source must fail closed on a missing
  `_id` and reject naive instants.
- **Rejected v3 records must be excluded from legacy fallback
  eligibility** (R8). Otherwise `resolve` treats them as missing and may
  serve them through legacy, bypassing FR-008 and FR-012b.
- **Conflict-only evidence uses a separate `Store`** (R7). Its
  persistence survives a rollback to a pre-013 release, and saturation
  or loss refuses every write until the entry is re-added.
- **The upgrade uses a minor version bump** (R9). Home Assistant rejects
  newer *major* versions on rollback.
- **Stale entities stay available** (R13): Home Assistant writes no
  attributes for an unavailable entity, and `CoordinatorEntity.available`
  returns `coordinator.last_update_success`. FR-012a requires
  reservation entities to stay available with eligible last-verified
  data marked stale, so `GuestyReservationSensor` and
  `GuestyFinancialSensor` override `available` to accept
  `coordinator.stale_display_available` in place of
  `last_update_success`. `last_update_success` is untouched, so stale
  data never satisfies a write guard. No capability-attribute or extra
  entity workaround is used.

## Phase 1 — Design

Artifacts:

- [data-model.md](data-model.md);
- [contracts/guesty-reservation-data-validation.md](contracts/guesty-reservation-data-validation.md);
- [quickstart.md](quickstart.md).

### Pipeline

1. `reservations_v3._map_results` calls `assess_v3_record` on each item.
   - A non-dict item raises `GuestyResponseError` (fails closed).
   - An accepted assessment builds the model from verified parts only.
   - A rejected or partial assessment adds to `rejected_ids`.
   - A rejected or partial record with a non-hold string code adds a
     `RejectedCode(reservation_id, code)` to `rejected_codes`, with its
     retirement-only `check_out_bound` when R7 decision 4 gives one.
   - Counts feed `ValidationSummary`. `status_counts` is sanitized.
2. `ReservationFallbackSource.resolve(..., rejected=...)`:
   - excludes, purges and never confirms absent the rejected IDs;
   - tracks per-record `_stale`;
   - keeps its live in-place cache updates; the routing sets are
     assigned in a synchronous `on_baseline_stored` callback right
     after the step 2 identity save succeeds and before step 3, so they
     always match the stored baseline (R8).
3. Identity:
   - `_check_identity` passes only fully verified, non-partial records
     as accepted observations, plus the `rejected_codes` pairs as
     conflict-only. One collision scan covers both (R7).
   - `compare` includes the stored conflict-only maps.
   - `_record_identity` uses the crash-consistent order (R7 decision
     8): an evidence save of additions (with any pending pairs), then
     the identity save with the advanced generation (clearing
     `revalidation_pending` only if the first save succeeded), then an
     evidence save of retirements.
   - Any persistence failure keeps writes refused (`unstored`).
4. Publication is one pure function, shared by accepted and failed
   refreshes. It takes the verified snapshot, the current instant,
   options, listings and verified timezones, and runs:
   - attribution;
   - expiry and uncertainty (R4);
   - the window (R4 conclusive bound);
   - projection with `stale`.
5. After an accepted refresh:
   - store the `VerifiedSnapshot`;
   - clear `snapshot_stale`;
   - report exclusions;
   - reconcile listing-timezone repairs.
6. After a failed refresh, re-publish from the snapshot (if any) and set
   `snapshot_stale`. The routing sets always match the stored
   baseline: a failure before the step 2 save leaves identity and
   routing untouched, and an identity abort also leaves the guard
   ambiguous, refusing every write (R8).

### Guards (unchanged precedence, two additions)

`classify_write_refusal` keeps its existing order. After the
ambiguity check and **before** the membership check
(`missing_current_code`) it adds:

- `REFUSAL_EVIDENCE_UNAVAILABLE` when the conflict-only evidence is
  `compromised`, `saturated`, `unstored` or `unloaded`;
- `REFUSAL_REVALIDATING` only when the reported state is
  `revalidating` (state precedence, research R7 decision 7).

Both reach the user as a plain `GuestyActionError` through the current
`guard_reservation_write` path. They are not identity misses, so they
start no feature 012 recovery refresh and no feature 014 by-ID read
(research R18).

### Constitution Check — post-design gate

**PASS.**

- The design adds bounded state only: 5000 evidence entries, 256
  tracker keys and 100 diagnostic IDs.
- It fails closed on every uncertain persistence state.
- It keeps the API boundary.
- It adds no I/O.
- It keeps every existing guard's order and outcome.

Stale reservation entities stay available as FR-012a requires (R13);
this keeps every write guard unchanged.

### Project Structure

#### Documentation (this feature)

```text
specs/013-reservation-data-validation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
└── contracts/
    └── guesty-reservation-data-validation.md
```

`tasks.md` is generated in the separate Speckit tasks stage.

#### Source and tests (future implementation only)

```text
custom_components/guesty/
├── api/validation.py            # NEW: ValidationReason, assessments,
│                                #      validate_record (014 interface 3)
├── api/models.py                # model from assessment; partial flag;
│                                #      ReservationFetchResult fields
├── api/reservations_v3.py       # _map_results assessment, rejected_ids
├── api/reservations_legacy.py   # status allowlist, offset instants, _id
├── api/reservations_fallback.py # rejected exclusion, per-record stale
├── identity.py                  # ConflictOnlyEvidence, store migration,
│                                #      refusals (014 interface 2)
├── coordinator.py               # snapshot, publication fn, expiry,
│                                #      tracker, repairs, failure path
├── sensor.py                    # stale availability and attributes;
│                                #      uncertain status
├── diagnostics.py               # validation block, sanitized counts
├── __init__.py                  # evidence store removal on entry removal
├── strings.json                 # repair issue translations
└── translations/en.json

tests/
├── api/test_validation.py       # NEW
├── api/test_models.py
├── api/test_reservations_v3.py
├── api/test_reservations_fallback.py
├── api/test_client.py           # legacy source record validation
├── api/test_timing.py
├── test_identity.py
├── test_coordinator.py
├── test_reservation_sensor.py
├── test_reservations_parity.py
├── test_actions.py
├── test_custom_field_service.py
├── test_diagnostics.py
├── test_privacy_logging.py
└── test_init.py
```

**Structure Decision**: The feature stays in the existing integration
and API client packages. The only new module is the pure validator.
`actions.py` is not changed by this feature: write refusal flows
through `classify_write_refusal`, and feature 014 owns the adapter.

### Merge order with feature 014

Feature 014 T001–T048 merged in PR #325 (`3734710`) while this plan was
written (research R16). Each 013 task touching a shared file first
updates its branch from current `main` (merge, never rewrite), then
follows these rules:

- **`ReservationFetchResult`**: 014's `returned_ids`, `rejected_ids` and
  `fallback_observed` exist. Reuse them unchanged and add only 013's
  new fields (`rejected_codes`, `rejected_check_out_bounds`,
  `fallback_invalid_ids`, `stale_ids`, `validation`).
- **`reservations_v3._map_results` and `fetch`**: replace only the
  interim raw-minus-parsed `rejected_ids` with the assessment-based
  set. Keep 014's `returned_ids` and `fallback_observed` plumbing
  intact.
- **`reservations_fallback.py`**: 014's session sets (observed,
  retired) are independent of 013's `_stale` and `rejected` handling.
  013 never removes IDs from them. The new `_INVALID` branch calls
  `_note_session("observed", id)` explicitly (research R8).
- **`coordinator.py`**: 014's `AcceptedRefreshSnapshot` and 013's
  `VerifiedSnapshot` are separate. 014's snapshot copies `rejected_ids`
  from the fetch result unchanged.
- **`identity.py`**: 014's `check_identity_conflict` stays a read over
  the baseline maps. Conflict-only evidence reaches 014 through the
  port.
- **`actions.py` and `api/actions.py`**: 013 makes no edits. Feature
  014 T049–T051 later replace `UnavailableConflictEvidence` with the
  adapter in the contract's "Adapter composition", which converts
  `RecordValidation` to the port's `"valid"`-or-rejection string.

013 now merges second, so it reviews two 014 tests:

- `test_rejected_ids_are_raw_minus_parsed`
  (`tests/api/test_reservations_v3.py`): its fixtures (missing status,
  no usable stay segment) stay correct, because those records are
  rejected. Rename it for the assessment-based rule and add a partial
  record, which is in `rejected_ids` while it is also published.
- `test_malformed_listing_ids_mark_record_unverifiable`
  (`tests/test_coordinator.py`): no change. It builds models directly
  (`GuestyStaySegment.from_api_dict`, `replace(...)`) and feeds them to
  the coordinator, so source validation never runs. Model constructors
  other than `from_v3_api_dict` and the legacy source path keep their
  behavior (data-model), so the coordinator's defensive
  `listing_ids=None` path stays covered. 013 adds source-level
  rejection tests for a non-string `parentListingId` (v3) and
  `listingId` (legacy) in the source test modules instead.

Once 013 is merged, unavailable or revalidating evidence is refused
before the membership check, with no 012 recovery refresh and no by-ID
read, and 014's own `evidence_unavailable` step stays defensive only
(research R16). The 014 documents now say so: its contract and T052
expect that refusal, and T010 and T011 note that 013 replaces the
interim "raw minus parsed" `rejected_ids`, which after 013 also
includes published partial records.

### Implementation phase boundaries

1. **Validator**: `api/validation.py` and the model built from
   assessment (US1, FR-001–FR-003a, FR-005–FR-007a, FR-008d). Test
   first in `tests/api/test_validation.py` and `test_models.py`.
2. **Batch and sources**: `_map_results` isolation, `rejected_ids` and
   `rejected_codes`, sanitized counts, legacy rules, fallback exclusion
   and per-record stale (FR-003b–FR-003c, FR-008d, FR-012b,
   FR-012g–FR-012h).
3. **Identity**: conflict-only participation, the `ConflictOnlyEvidence`
   store, the bound and states, the two refusals, and the minor-version
   migration with revalidation (FR-008–FR-008c, FR-012d–FR-012f). This
   phase completes the 014 interfaces.
4. **Publication and staleness**: the shared publication function,
   verified timezone, expiry and uncertainty before the limit (expiry
   is new for single-raw-segment v3 and legacy projections, R4),
   conclusive window, snapshot re-projection, stale entity
   availability and stale attributes
   (US2, FR-001, FR-003, FR-004–FR-004c, FR-005b, FR-012a–FR-012c, SC-003, SC-010).
5. **Reporting**: the exclusion tracker, timezone and evidence repairs,
   diagnostics and privacy sentinels (US3, FR-009–FR-011).
6. **Documentation and final gate**: README and operator notes
   (unverified-timezone behavior, conflict-evidence repair remedy and
   stale display after a failed refresh), a release note under
   `releasenotes/notes/`, and the full validation.

Each phase ends with all CI tests passing on its boundary commit before
the next phase begins. Red–green unit testing is mandatory within
phases.

Phases 1–3 merge to `main` together. Phase 1 already rejects records
whose mappings reach `observe_codes` today (for example an
unrecognized string status), and only phase 3 restores those mappings
to conflict detection and adds the revalidation and evidence-state
refusals (R5, R7, R18). Merging phase 1 or 2 alone would weaken the
existing identity guard. Phases 4–6 may merge on their own afterwards.

### Requirement traceability

Each requirement maps to the research decisions that design it and the
contract rules that state it. The contract lists the required tests.

- FR-001, FR-003: R1, R3, R4; C1–C3, C6, C7.
- FR-002: R2; C5.
- FR-003a: R3, R4 (hold and window rules); C2, C8, C9.
- FR-003b, FR-003c: R5; C2, C4.
- FR-004–FR-004c: R4 decisions 5–8; C8, C9.
- FR-005, FR-005a, FR-005b: R3, R4 (partial records have no span); C3,
  C6.
- FR-005c: R3, R8; C3, C12.
- FR-006, FR-007, FR-007a: R4, R5 (legacy naive instants); C6, C7,
  C19.
- FR-008: R7, R18; C13, C15, C16.
- FR-008a–FR-008c: R7; C13, C14.
- FR-008d: R3, R6, R12; C3, interfaces 1 and 3.
- FR-009–FR-011: R14, R15; C18–C20.
- FR-012, FR-012a–FR-012c: R10, R13; C10, C11.
- FR-012d, FR-012e: R9; C15, C16.
- FR-012f: R7 (supersession), R9; C11, C14.
- FR-012g, FR-012h: R8; C12.
- SC-001, SC-004, SC-005: R1–R3, R14; C1–C5, C18–C20.
- SC-002, SC-003, SC-009, SC-012: R4; C8, C9.
- SC-006, SC-013: R7, R9; C13–C16.
- SC-007: R11, R18; C16, C17.
- SC-008, SC-014: R3; C3.
- SC-010, SC-015: R8, R10, R13; C10–C12.
- SC-011: R5; C2, C4.

### Validation plan (implementation stage)

- At each phase boundary, record the following:
  - the commit SHA;
  - the CI run and check URLs and their conclusions;
  - the local results from [quickstart.md](quickstart.md).

  A skipped, cancelled, pending or failed check is not a pass.
- Run `uv run pytest tests/`,
  `uv run ruff check custom_components/ tests/ scripts/`,
  `uv run --group releasenotes mypy custom_components/ tests/ scripts/`
  and `uv run pre-commit run --all-files`.
- **Compatibility**: run the `Store` minor-version migration, rollback
  payload, and stale-entity availability and attribute tests in the
  locked 2026.10.4 environment and in an isolated 2026.4.0 environment without
  changing `uv.lock`. Record the installed versions. If 2026.4.0 cannot
  be built, report that gate as blocked.
- Do not perform live Guesty verification. #223 evidence is a
  precondition only for re-enabling a written-date fallback, which this
  plan does not do.

## Planning Scope

This plan covers research and design only. Task ordering and
implementation happen in their separate Speckit stages. Report the R4
removal of the written-date fallback to the spec owner.

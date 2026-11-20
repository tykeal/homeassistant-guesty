<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Narrow Stay-Date Fallback

**Input**: Design documents in
`specs/010-stay-date-fallback/` (`spec.md`, `plan.md`)
**Prerequisites**: `plan.md`, `spec.md`, and the analysis gate below
**Scope**: `custom_components/guesty/api/models.py`, focused tests, and one
unique `releasenotes/notes/stay-date-fallback-*.yaml` file only.

## Phase 1: Setup

**Purpose**: Confirm the existing parser and test conventions without
expanding the implementation scope.

- [ ] T001 Inspect parser behavior and fixture conventions in
    `custom_components/guesty/api/models.py` and
    `tests/api/test_models.py` before writing tests

## Phase 2: Foundational Analysis Gate

**Purpose**: Resolve design inconsistencies before implementation starts.

- [ ] T002 Run Speckit consistency analysis across
    `specs/010-stay-date-fallback/spec.md`,
    `specs/010-stay-date-fallback/plan.md`, and
    `specs/010-stay-date-fallback/tasks.md`; stop before implementation if a
    blocking finding requires edits outside the permitted scope

**Gate**: No implementation task may start until T002 passes. Resolve
meaningful artifact drift through a separately reviewed and merged
documentation fix before implementation. Escalate unresolved policy or
out-of-scope blockers.

## Phase 3: User Story 1 - Publish Single Stay With Absent Dates (P1)

**Goal**: Accept the narrow eligible single-raw-segment case using the valid
top-level interval, while preserving explicit segment dates and existing
attribution.

**Independent Test**: Exercise all four omitted/`null` combinations on a
single raw segment with a valid `listingId` and a valid strictly chronological
top-level interval; verify the segment dates and existing listing attribution.
Also verify that explicit valid segment dates are unchanged.

### Tests First (Red)

- [ ] T003 [US1] Add parameterized tests for both endpoints omitted, omitted
    plus `null`, `null` plus omitted, and both `null` in
    `tests/api/test_models.py`
- [ ] T004 [US1] Add a regression test that valid explicit segment dates are
    preserved when top-level dates differ in `tests/api/test_models.py`
- [ ] T005 [US1] Run `uv run pytest tests/api/test_models.py -q` and verify
    eligible fallback cases fail before changing
    `custom_components/guesty/api/models.py`

### Implementation (Green)

- [ ] T006 [US1] Implement the minimum guarded fallback in
    `custom_components/guesty/api/models.py`: count raw `stay` entries before
    filtering, require exactly one segment with a valid `listingId` under
    existing validation, both segment endpoints omitted or `None`, and a
    strictly chronological top-level interval parsed with existing rules
- [ ] T007 [US1] Re-run `uv run pytest tests/api/test_models.py -q` and verify
    all four eligible cases pass while explicit segment dates remain unchanged

**Checkpoint**: User Story 1 passes its focused parser tests without changing
listing inference, coordinator projection, identity checks, or write routing.

## Phase 4: User Story 2 - Reject Ambiguous or Invalid Fallback (Priority: P1)

**Goal**: Keep partial, malformed, invalid, and multi-raw-segment inputs
ineligible, and retain existing diagnostic privacy and reservation safeguards.

**Independent Test**: Verify partial actual-date pairs, invalid present
values, invalid top-level intervals, invalid listing identifiers, and multiple
raw segments do not receive fallback dates; verify diagnostics remain visible
and contain no guest personal data or confirmation-code values.

### Negative and Regression Tests

- [ ] T008 [US2] Add tests for partial actual-date pairs, empty or malformed
    or otherwise invalid present values, and missing or invalid `listingId`
    in `tests/api/test_models.py`; prove top-level data never supplies a
    missing segment listing identifier
- [ ] T009 [US2] Add tests rejecting missing, unparsable, equal, and reversed
    top-level intervals in `tests/api/test_models.py`
- [ ] T010 [US2] Add a raw-count regression test in
    `tests/api/test_models.py` proving multiple raw `stay` entries are
    ineligible even if filtering leaves one usable entry; do not test or
    change the known filtered-multi-to-one projection behavior
- [ ] T011 [US2] Add focused parser warning assertions with synthetic
    guest and confirmation-code values in `tests/api/test_models.py`;
    diagnostics must remain visible without exposing either value
- [ ] T012 [P] [US2] Run identity and write-safety regression coverage in
    `tests/test_identity.py`; do not change production code outside
    `custom_components/guesty/api/models.py`

### Story Validation

- [ ] T013 [US2] Run focused model and diagnostic tests in
    `tests/api/test_models.py`; confirm
    ineligible cases remain without fallback and diagnostics remain
    privacy-safe

**Checkpoint**: Both stories pass focused tests. The filtered-multi-to-one
projection risk remains unchanged and out of scope.

## Phase 5: Polish, Quality Gates, and PR Dependencies

**Purpose**: Document the exception, validate the full repository, and stage
review and commit dependencies.

- [ ] T014 [P] Add one SPDX-covered release note at a unique path matching
    `releasenotes/notes/stay-date-fallback-*.yaml`; describe only the narrow
    fallback and do not claim a live Guesty payload was verified
- [ ] T015 Run `uv run pytest tests/`,
    `uv run ruff check custom_components/ tests/ scripts/`,
    `uv run --group releasenotes mypy custom_components/ tests/ scripts/`,
    and all configured pre-commit hooks; resolve failures only within the
    permitted implementation scope
- [ ] T016 Review the implementation diff in
    `custom_components/guesty/api/models.py`, focused tests, and the release
    note for scope, raw-counting, strict chronology, explicit-date
    preservation, attribution, privacy, identity/write safety, SPDX, and
    unchanged filtered-multi-to-one projection behavior
- [ ] T017 If updating task checkboxes, commit those updates separately from
    implementation changes with DCO sign-off
- [ ] T018 After T015 and T016 pass, create a signed implementation commit
    containing only `custom_components/guesty/api/models.py`, focused tests,
    and the unique release note; exclude
    `specs/010-stay-date-fallback/tasks.md`
- [ ] T019 After the separate task-list PR is merged and T002 passes, submit
    the implementation PR; require CI and Copilot review to pass before
    merge, with no sibling work or unrelated files

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No prerequisite; inspect the existing parser/test
  structure.
- **Foundational analysis (Phase 2)**: T002 must pass before any implementation.
- **User Story 1 (Phase 3)**: Depends on the analysis gate. T003–T005 establish
  Red before T006; T007 establishes Green after T006.
- **User Story 2 (Phase 4)**: Depends on User Story 1's parser implementation.
  Model tests T008–T011 share `tests/api/test_models.py` and are executed
  sequentially. T012 may run independently after T007.
- **Polish and PR gates (Phase 5)**: T014 may be prepared in parallel with
  Phase 4 tests after T007. T015 depends on all test and release-note changes;
  T016 depends on T015. T018 depends on green validation and local review.
  T017, if performed, is a task-list-only commit separate from T018. T019
  depends on the earlier merged task-list PR, T002 and the implementation
  commit and requires CI/Copilot completion before merge.

### User Story Dependencies

- **User Story 1 (P1)**: Starts after the analysis gate; no dependency on
  another story.
- **User Story 2 (P1)**: Follows User Story 1 because the negative cases
  validate the same parser path and focused test module; no coordinator or
  identity production changes are introduced.

### Parallel Opportunities

- T012 (`tests/test_identity.py`) can run independently after T007.
- T014 (release note) can be prepared in parallel with Phase 4 test work
  because it touches a separate permitted file.
- Do not parallelize edits to `tests/api/test_models.py` or implementation in
  `custom_components/guesty/api/models.py`; preserve the ordered Red-Green
  sequence.

## Implementation Strategy

1. Pass the analysis gate before implementation.
2. Write the eligible and explicit-date tests, verify Red, and implement the
   minimum parser change.
3. Add and run negative, diagnostics, and identity/write-safety regressions.
4. Add the release note and pass full tests, ruff, mypy, and all pre-commit
   hooks.
5. Review and commit code; commit any later task tracking updates separately.
6. Require CI and Copilot review before merge.

**MVP scope**: User Story 1 is the functional increment, but it MUST NOT be
released without User Story 2's negative-case and safety verification; both
P1 stories together define the safe feature.

## Notes

- The only permitted implementation edits are
  `custom_components/guesty/api/models.py`, focused test files, and one
  uniquely named `releasenotes/notes/stay-date-fallback-*.yaml`.
- Do not change configuration/options, manifests, locks, coordinator
  production behavior, identity/write-safety production behavior, Feature 008
  artifacts, or sibling work.
- Count raw `stay` entries before filtering. The known filtered-multi-to-one
  projection behavior is explicitly out of scope and must not be changed.
- No live Guesty payload was inspected or verified; do not state otherwise in
  tests, diagnostics, or the release note.

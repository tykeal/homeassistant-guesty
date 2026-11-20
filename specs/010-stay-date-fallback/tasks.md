<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Narrow Stay-Date Fallback

**Input**: `specs/010-stay-date-fallback/spec.md` and `plan.md`
**Scope**: `custom_components/guesty/api/models.py`, focused tests, and one
unique `releasenotes/notes/stay-date-fallback-*.yaml` file.

## Prerequisites

- [x] T001 Inspect the existing parser and fixture conventions in
    `custom_components/guesty/api/models.py` and `tests/api/test_models.py`
- [x] T002 Run Speckit consistency analysis across the spec, plan, and tasks.
    No implementation begins until it passes. Resolve meaningful artifact
    drift in a separately reviewed and merged documentation PR; escalate
    unsettled policy or out-of-scope blockers.

## Phase 1: Safe Single-Segment Fallback

**Goal**: Deliver both P1 stories as one independently testable increment.
Do not release the positive path without the negative and safety cases.

### Tests First (Red)

- [x] T003 Add parameterized parser tests for the four eligible combinations:
    both date keys omitted, omitted/`null`, `null`/omitted, and both `null`.
    Assert a valid single segment uses the chronological top-level interval.
- [x] T004 Add parser regression tests for explicit valid segment dates,
    partial actual-date pairs, empty or malformed values, missing or invalid
    listing identifiers, and top-level listing inference rejection.
- [x] T005 Add parser tests rejecting missing, unparsable, equal, and reversed
    top-level intervals. Add raw-count cases for multi-segment stays, including
    one with only one otherwise usable segment after filtering.
- [x] T006 Add focused parser warning tests for unusable reservations using
    synthetic guest and confirmation-code values, verifying diagnostics remain
    visible without exposing those values.
- [x] T007 Add a focused coordinator regression proving an eligible fallback
    segment with a valid but untracked listing is skipped with a warning and
    never attributed to a listing inferred from top-level reservation data.
- [x] T008 Run the focused new tests and existing identity and write-safety
    regressions before implementation. Confirm the eligible fallback cases
    fail against the existing parser and existing safety regressions pass;
    existing rejection cases may already pass.

### Implementation (Green)

- [x] T009 Implement the minimal guarded fallback in
    `custom_components/guesty/api/models.py`: exactly one raw entry, a valid
    segment `listingId` under existing validation, both endpoints omitted or
    `None`, and a strictly chronological top-level interval parsed with
    existing rules.
- [x] T010 Re-run the focused tests and verify all eligible and ineligible
    cases pass. Run the existing identity and write-safety regression tests.
- [x] T011 Add one SPDX-covered release note at a unique path matching
    `releasenotes/notes/stay-date-fallback-*.yaml`; describe only the narrow
    fallback without claiming a live Guesty payload was verified.
- [x] T012 Run `uv run pytest tests/ -x -q`,
    `uv run ruff check custom_components/ tests/ scripts/`,
    `uv run --group releasenotes mypy custom_components/ tests/ scripts/`,
    and all configured pre-commit hooks. Fix any issues in scope and rerun.
- [x] T013 Complete a local rubber-duck review; fix and repeat until clean.
    Review raw counting, strict chronology, explicit-date precedence,
    attribution, privacy, identity/write safety, and the unchanged
    filtered-multi-to-one projection risk.
- [x] T014 Commit the implementation, tests, and release note together using
    `git commit -s` and the required trailer
    `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
    Verify the pushed commit's signature and effective author identity.
- [x] T015 If updating task checkboxes, commit those updates separately from
    the implementation, with DCO sign-off and the same co-author trailer.
- [ ] T016 Submit the implementation PR after the documentation stages are
    merged and T002 passes. Request Copilot review and resolve all threads;
    require every CI check to pass before merging.

**Phase checkpoint**: Full tests, lint, type checks, all pre-commit hooks,
local review, Copilot review, and all PR CI checks pass before this increment
is merged. Do not start manual or exploratory testing before CI is green.

## Dependencies and Execution Order

1. T001 and T002 precede all implementation work. The merged specification,
   plan, and task list are prerequisites; the analysis gate must pass.
2. T003–T007 precede T008. T008 establishes the failing positive-case Red
   phase before T009. Negative cases may already pass on the baseline.
3. T010 follows T009; T011 may be prepared after Red. T012 follows the code,
   tests, and note. T013 follows validation and is repeated after any fixes.
4. T014 follows clean validation and review. T015, if needed, is separate
   from the implementation commit. T016 follows signed commits and requires
   green CI and clean review before merge.

## Safety Boundaries

- Count raw `stay` entries before filtering. The known filtered-multi-to-one
  projection risk is out of scope and must not be changed by this feature.
- Preserve explicit segment dates, missing-listing rejection, existing
  attribution and projection, identity checks, and write routing. Do not
  modify coordinator production behavior.
- Do not change configuration/options, manifests, locks, Feature 008
  artifacts, shared release configuration, or sibling work.
- The issue-related live Guesty payload remains unverified.

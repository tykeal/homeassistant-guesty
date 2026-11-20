<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Narrow Stay-Date Fallback

**Branch**: `docs/stay268-plan1` | **Date**: 2026-11-20 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`/specs/010-stay-date-fallback/spec.md`

## Summary

For a reservation with exactly one raw `stay` entry, allow its segment to use
the reservation's top-level `checkIn` and `checkOut` only when the segment has
a valid `listingId`, both segment date keys are omitted or JSON `null`, both
top-level dates parse under the existing rules, and checkout is strictly
after check-in. Count the raw entries before filtering. Keep explicit segment
dates, existing listing attribution, reservation identity and write safety,
warning privacy, and all ineligible-case behavior unchanged.

Implementation is limited to `custom_components/guesty/api/models.py`, focused
tests, and a uniquely named
`releasenotes/notes/stay-date-fallback-*.yaml`. No live payload was verified;
this is not a claim about a confirmed Guesty payload or API behavior.

## Technical Context

**Language/Version**: Python 3.x, following the repository's configured
runtime.
**Primary Dependencies**: Home Assistant integration conventions, existing
`GuestyReservation` and `GuestyStaySegment` models, and the standard-library
datetime parser already used by the models.
**Storage**: N/A; parsing and projection are in-memory.
**Testing**: pytest; ruff, mypy, and repository pre-commit/CI checks.
**Target Platform**: Home Assistant custom integration.
**Project Type**: Python library/integration under `custom_components/`.
**Performance Goals**: No additional I/O or material per-reservation work
beyond checking raw segment count, date presence and interval validity.
**Constraints**: Preserve all Feature 008 attribution, projection, identity,
write-routing, status, and filtering behavior outside the narrow exception.
Do not change configuration-flow/options strings, README, Feature 008
documents, #266 tests, manifests, locks, release configuration, coordinator
production code, or files outside this isolated worktree.
**Scale/Scope**: One parser exception for one raw stay segment; tests in
existing test modules; one release note.

## Constitution Check

- **Code quality and testing — Pass.** Implement with test-first
  Red-Green-Refactor. Run the full test, lint, type-check, pre-commit, and
  CI/review gates.
- **API client design — Pass.** No HTTP, API client, or external-call changes.
- **Atomic changes and phased delivery — Pass.** This is one bounded feature;
  no commits are part of this plan stage.
- **Licensing and attribution — Pass.** Preserve or add SPDX coverage on each
  new file, including the release note.
- **User experience consistency — Pass.** No configuration or entity
  interface changes; document the narrow behavior in a release note.
- **Performance — Pass.** No new I/O, persistence, or unbounded processing.
- **Security and privacy — Pass.** Keep diagnostics free of guest PII and
  confirmation codes; do not weaken identity or write safeguards.
- **Project constraints — Pass.** Use Python and existing project tooling;
  make no dependency or lockfile changes.

No constitutional violation or complexity exception is required.

## Phase Boundary and Checkpoint

The implementation has one phase: deliver both P1 stories together as the
safe single-segment fallback described in `tasks.md` Phase 1. The phase
begins only after the consistency analysis gate passes. It concludes when
focused and full tests, lint, type checks, all pre-commit hooks, local review,
Copilot review, and all PR CI checks pass. No manual or exploratory testing
precedes green CI.

## Project Structure

### Documentation (this feature)

```text
specs/010-stay-date-fallback/
├── spec.md
├── checklists/requirements.md
└── plan.md
```

No `research.md`, `data-model.md`, `contracts/`, or `quickstart.md` is
necessary: this change adds no entity or persistent model, introduces no
external interface, and has no unresolved technology choice or user setup
step. Feature 008's data model and API contracts remain unchanged.

### Source and tests

```text
custom_components/guesty/api/models.py
tests/api/test_models.py
tests/test_coordinator.py
tests/test_identity.py
releasenotes/notes/stay-date-fallback-<unique>.yaml
```

**Structure Decision**: Reuse the current API-model parser and its unit tests.
Keep production changes out of the coordinator and identity/write modules.
Use existing coordinator and identity tests as regression protection; add only
focused assertions needed to pin the behavior.

## Design and Implementation

1. In `GuestyReservation.from_v3_api_dict`, preserve the raw `stay` collection
   and its entry count before parsing or dropping unusable entries. Fallback
   eligibility is based on exactly one raw entry, not the number of segments
   left after parsing.
2. Permit the existing segment parser to receive fallback dates only when all
   eligibility predicates hold:
   - the sole raw entry is a mapping with a valid, non-empty `listingId`
     under existing validation;
   - both `checkIn` and `checkOut` segment fields are absent by omission or
     JSON `null`; all four combinations qualify;
   - neither field is present with an empty, malformed, non-date, or otherwise
     invalid value; and
   - both top-level dates parse with the existing parser and top-level
     checkout is strictly later than check-in.
3. Use only the parsed top-level `checkIn` and `checkOut` values as the
   fallback interval. Do not copy or infer a listing from reservation-level
   fields. Retain existing localized-date handling; add no new date or
   localization derivation.
4. Preserve explicit valid segment dates exactly. Do not fallback for partial
   actual-date pairs, malformed present values, invalid top-level intervals,
   missing/invalid listing identifiers, or multiple raw segments, even if
   filtering leaves one usable segment.
5. Leave warning behavior visible and privacy-safe. Preserve existing
   attribution outcomes: a valid but unknown listing remains unknown and is
   skipped under existing rules; top-level reservation data must not supply a
   listing.
6. Do not change coordinator projection, segment selection, status, identity,
   write routing, or the preexisting filtered-multi-to-one projection
   behavior. The latter remains documented out of scope.
7. Add one release note under the permitted `stay-date-fallback-*.yaml`
   pattern. Do not edit release-note configuration.

## Test-First Plan

Before production changes, add focused failing tests and verify the Red phase.
Then implement the minimum parser change, verify Green, and refactor only while
tests remain green.

The tests must cover:

- all four eligible segment-date representations: both omitted, omitted plus
  `null`, `null` plus omitted, and both `null`;
- strictly valid top-level interval boundaries and rejection for equal,
  reversed, missing, or unparsable endpoints;
- exactly one raw segment before filtering, including a raw multi-segment case
  where only one entry would otherwise survive;
- explicit valid segment dates remaining unchanged even when top-level dates
  differ;
- rejection of partial actual-date pairs and empty, malformed, or otherwise
  invalid present segment values;
- missing/invalid segment listing identifiers, and confirmation that
  top-level data never supplies or infers listing attribution;
- visible warnings for unusable reservations without guest PII or
  confirmation-code values; and
- unchanged single-segment projection and existing identity/write-safety
  outcomes. The filtered-multi-to-one projection risk must remain unchanged
  and must not be “fixed” as part of these tests or implementation.

Use the existing model parser fixtures and coordinator/identity regression
tests where practical. Do not broaden the test scope into configuration-flow
strings, manifests, or #266 coverage.

## Validation and Completion Gates

Run the focused tests during Red-Green-Refactor, then the complete repository
gates from the worktree root:

1. `uv run pytest tests/`
2. `uv run ruff check custom_components/ tests/ scripts/`
3. `uv run --group releasenotes mypy custom_components/ tests/ scripts/`
4. Run the repository's complete configured pre-commit checks, including
   Markdown, YAML, SPDX/REUSE, formatting, and Python validation hooks.
5. Require the full CI workflow and review gates to pass before implementation
   completion. Review must confirm the owner scope, test-first sequence,
   privacy-safe warning behavior, no listing inference, unchanged identity and
   write safety, and no coordinator/projection scope expansion.

Do not start manual or exploratory testing before automated CI is green.

## Planning Notes and Uncertainties

The owner policy resolves the null and raw-segment-count questions. No
implementation-blocking technical clarification remains. The precise
issue-related live Guesty payload remains unverified, as recorded by the
specification; planning and implementation must not imply otherwise.

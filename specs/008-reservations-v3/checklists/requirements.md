<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Specification Quality Checklist: Reservations v3 API Migration

**Purpose**: Validate specification completeness and quality before proceeding
to planning
**Created**: 2026-11-13
**Feature**: [spec.md](../spec.md)

## Content Quality

- [ ] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [ ] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [ ] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [ ] No implementation details leak into specification

## Migration-Specific Quality

- [x] The vendor retirement date (2027-03-01) is stated and drives scope
- [x] Every v3 behavioural claim is marked documentation-derived, not
      verified
- [x] Entity, state and attribute backward compatibility is specified
      attribute by attribute (NFR-003, NFR-004)
- [x] Request-volume and rate-limit impact of the new lookups is bounded
      (NFR-001, NFR-002, SC-003, SC-010)
- [x] A rollback path with a stated default and deprecation plan exists
      (FR-036 to FR-040)
- [x] The wrong-identity write failure mode is explicitly guarded
      (FR-031 to FR-035, R-001)
- [x] Issue #204 is captured as a requirement (FR-002, FR-003, SC-004)
- [x] Risks carry mitigations that map to numbered requirements

## Notes

- Three content-quality items are intentionally unchecked. This is a
  migration specification whose entire subject is a change of API
  surface, so naming the specific Guesty resources and the existing
  Home Assistant entity and attribute names is necessary for the spec
  to be verifiable at all. Source-file line references are included
  for the same reason: they pin what "unchanged" means.
- The `[UNVERIFIED]` markers in the Assumptions section are **not**
  `[NEEDS CLARIFICATION]` markers. They are deliberate, permanent
  records of documentation-derived claims that no one can resolve
  without live Guesty API access, and FR-041 carries them forward into
  the plan's pre-release checklist. The one item that is genuinely
  unresolved and blocks planning is OQ-001 (financial sensors), which
  is flagged as awaiting the repository owner's decision; the
  "No [NEEDS CLARIFICATION] markers remain" item stays unchecked until
  that decision is recorded.
- Seven open questions (OQ-001 to OQ-007) are recorded. Only OQ-001 is
  a scope decision; the remainder are live-verification items that the
  plan stage must either answer from the vendor reference or carry as
  defensive handling.
- Scope decisions already settled by the repository owner and not
  re-opened here: no live API access, write actions in scope, cached
  guest lookups, a configuration toggle rather than a hard cutover,
  and #204 resolved by this migration.
- Spec ready for `/speckit.clarify` or `/speckit.plan` once OQ-001 is
  decided.

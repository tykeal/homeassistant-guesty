<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Reservation Identity Write-Miss Refresh

**Branch**: `012-reservation-write-miss-plan1` | **Date**: 2026-11-23
| **Spec**: [spec.md](spec.md)
**Input**: `/home/tykeal/repos/personal/homeassistant/worktrees/guesty/write-miss-plan1/specs/012-reservation-write-miss/spec.md`

## Summary

For the three reservation-write entry points only, distinguish a valid
target missing from current identity evidence (including a currently
published reservation without a confirmation code) from all other refusals.
After input validation and every independent safety check pass, use one
completed update from that entry's existing `ReservationsCoordinator`, or
join the update already in progress. Re-evaluate the latest accepted
identity, baseline, continuity, ambiguity, and provenance before writing.
Fail closed on stale/failed/cancelled refreshes, identity persistence
failure, missing or code-less results, source/reload changes, and routing
refusals. Recovery sends no targeted identity lookup and never changes the
existing configured-source, guest-lookup, or bounded fallback policy.

## Technical Context

**Language/Version**: Python 3.14+ (`pyproject.toml` requires `>=3.14.2`)
**Primary Dependencies**: Home Assistant `>=2026.4.0`, voluptuous, httpx;
the installed locked environment was Home Assistant `2026.10.4`
**Storage**: Existing per-entry Home Assistant `Store` identity baseline;
no new persistent state
**Testing**: pytest, pytest-homeassistant-custom-component, pytest-httpx;
tests run through `uv`
**Target Platform**: Home Assistant custom integration
**Project Type**: Python Home Assistant integration
**Performance Goals**: At most one coordinator refresh cycle per eligible
write attempt; concurrent misses share an in-flight cycle where possible;
retain existing bounded pagination, retry, guest lookup, and legacy fallback
behavior
**Constraints**: Validate before recovery; use only the configured
reservations coordinator; preserve feature 008 identity and route rules;
no per-ID identity fetch, arbitrary cooldown, cache-age policy, listing
refresh, or second write; do not log codes, PINs, guest data, or credentials
**Scale/Scope**: Three reservation write services (note, status, reservation
custom field), one configuration entry per coordinator; no schema,
configuration, entity, or baseline-format change

## Constitution Check

Pre-research gate: **PASS**.

- **Code quality/TDD**: Plan requires tests before production changes,
  focused on coordinator attempt lifecycle, service preflight, and request
  counts; type, lint, and test checks remain required.
- **API client boundary**: Recovery orchestration belongs in the HA
  service/coordinator layer. It reuses the configured coordinator's existing
  fetch path and the API client's existing request abstraction; no direct
  service-layer HTTP or HA imports into `custom_components/guesty/api/`.
- **Security/privacy**: No secret values in new state, logs, diagnostics, or
  errors. Do not log request/response payloads. Existing identity codes remain
  under feature 008's storage rules; do not broaden their exposure.
- **Performance**: One full configured-source update, not a per-ID lookup or
  a parallel fetch. Existing vendor retries and bounded fallback/guest
  policies remain unchanged; only the recovered write request is single
  attempt.
- **Phased development**: The implementation must deliver testable
  coordinator, action, and transport changes in phases, with TDD and a green
  checkpoint between them.
- **Licensing/commit gates**: New planning documents carry SPDX headers.
  Implementation files must follow REUSE and project lint/type/docstring
  requirements. No commit is part of this planning stage.
- No constitution violation or scope expansion is required.

## Phase 0 — Research Findings

`research.md` records the inspected Home Assistant semantics, integration
behavior, feature 008 requirements, and decisions. No technical choice
remains unresolved after research. The 2026.4.0 minimum-release source and
installed 2026.10.4 source both expose the same relevant coordinator
lock/refresh behavior; implementation tests must retain this compatibility.

Important synchronization consequence: neither `async_request_refresh()` nor
`async_refresh()` alone satisfies the feature. The former can return before
the debounced update starts; the latter waits for an active update and then
performs another one. The implementation therefore needs a small
coordinator-owned refresh-attempt/generation helper that serializes on the
same Home Assistant debouncer lock, checks whether an update completed after
the miss, and invokes the coordinator's normal `_async_refresh` path only
when no qualifying attempt completed. It must observe completion after HA
has updated `last_update_success`, not infer success from the method's
`None` return or retained `data`.

The helper's use of Home Assistant's protected `_async_refresh` and
`_debounced_refresh.async_lock()` is a compatibility-sensitive integration
point, not a license to fetch reservations independently. Keep the adapter
small and test its lifecycle against both the declared minimum (2026.4.0)
and the locked 2026.10.4 behavior. If a supported HA version does not provide
the same lock/completion semantics, stop for a design decision rather than
falling back to `async_request_refresh()`, a direct API fetch, or an
identifier-specific lookup.

## Phase 1 — Design

### Behavior and synchronization

1. Let Home Assistant service schemas run first. Before considering a miss,
   invoke the existing note/status validators and validate custom-field
   target/definition/value. Invalid IDs or payloads fail without recovery.
2. Evaluate the unchanged feature 008 baseline, stored-state, continuity,
   ambiguity, and known-routing refusals. A valid target absent from current
   identity evidence is eligible only when none of these independent guards
   refuses. Distinguish an entirely absent target from a published
   code-less target using current coordinator publication, but allow either
   to request the one recovery cycle. A known retained-only fallback target
   with no validated legacy route, or a retired route, is refused before a
   refresh.
3. Capture entry/coordinator/source identity and the refresh attempt
   generation at miss detection. Wait on the same coordinator lock used by
   scheduled and requested refreshes. If an update started before or after
   detection completes while this action waits, join its completion; do not
   initiate a second cycle even if that result fails, is ambiguous, or omits
   the target. Otherwise run one normal coordinator refresh against its
   current configured source and existing options. Concurrent eligible
   misses share the cycle and independently check their own targets.
4. Treat an attempt as fresh only if it completed after miss detection and
   the completed coordinator result is accepted, successful, unambiguous,
   and belongs to the same live entry/coordinator and source configuration.
   An attempt already in progress at detection can qualify even though its
   generation started earlier. HA catches many update exceptions and keeps
   stale `data`; require both the attempt completion marker and current
   `last_update_success`. A skipped/shutdown attempt, cancelled fetch,
   exception, stale result, reload, or source change is a refusal. Propagate
   cancellation of the action itself rather than converting it to success.
5. After every awaited step, recheck the current config-entry mapping,
   coordinator generation/state, persisted baseline and its
   `_unstored` state, continuity, ambiguity, target presence, current
   confirmation-code evidence, and route. The latest accepted coordinator
   state controls provenance. Never use a pre-await route or a successful
   result superseded by a failed/ambiguous refresh.
6. For recovery writes, serialize final authorization with coordinator
   refreshes through the actual write dispatch. Keep the note's existing
   read-before-write; a failed prerequisite read means no write. Revalidate
   after the note read and before the write, and use the route current for
   that accepted generation. Do not widen this synchronization or alter
   normal cache-hit actions.
7. Once recovery has qualified, issue at most one actual reservation write.
   Pass the API client's existing `single_attempt` mode for the write,
   regardless of whether current provenance selects v3 or legacy. Do not
   retry that write against either the same or another source. Keep
   prerequisite reads and the reservation refresh on their existing
   request/retry policies. No-miss/cache-hit writes retain existing retry
   behavior unchanged.
8. Use existing failure conversion to Home Assistant service errors, with
   distinct actionable messages for refresh failure/staleness, target still
   absent or code-less, and identity/routing refusals. Do not include raw API
   response bodies or confirmation codes, custom-field values/PINs, guest
   PII, or credentials in recovery errors, logs, or diagnostics.

### Constitution Check — post-design gate

**PASS, subject to the compatibility test gate above.** The design preserves
TDD, the API boundary, existing Store semantics, HA async I/O, bounded
configured fetch policies, and fail-closed handling. The recovery attempt
adds no persistence, polling interval, request-time cooldown, or new
external service field. Only a recovery-triggered write gets single-attempt
transport behavior; ordinary cache-hit behavior is explicitly unchanged.
Privacy tests must inspect both success and failure logs/diagnostics.

### Project Structure

#### Documentation (this feature)

```text
specs/012-reservation-write-miss/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    └── guesty-reservation-write-miss.md
```

`tasks.md` is intentionally not created: it belongs to the separate
Speckit tasks stage, which is outside this request.

#### Source and tests (future implementation only)

```text
custom_components/guesty/
├── actions.py                 # service preflight and write-miss orchestration
├── coordinator.py             # attempt generation, joining, freshness checks
├── identity.py                # existing persisted guard; semantics unchanged
├── api/actions.py             # one-shot recovery write plumbing/validation
└── api/custom_fields.py       # one-shot reservation write plumbing

tests/
├── test_actions.py
├── test_coordinator.py
├── test_identity.py
├── test_custom_field_service.py
├── api/test_actions.py
└── api/test_custom_fields.py
```

`identity.py` and service/client edits are conditional on what the tests
show; do not alter the feature 008 baseline format or comparison policy.

**Structure Decision**: Keep the feature in the existing integration
coordinator/service/API client and corresponding pytest suites. It requires
no new package, endpoint, configuration option, entity, or storage model.

### Validation plan (implementation stage)

- Run focused tests from the repository root as detailed in
  `quickstart.md`, then `uv run pytest tests/`.
- Run `uv run ruff check custom_components/ tests/ scripts/`,
  `uv run --group releasenotes mypy custom_components/ tests/ scripts/`,
  and project pre-commit/REUSE checks.
- Verify Home Assistant version behavior for 2026.4.0 and the lockfile's
  2026.10.4, particularly when updates fail, are cancelled, or are
  concurrently waiting on the coordinator lock.
- Do not perform live Guesty verification; it is unrelated to this
  cache-miss behavior and feature 008's vendor assumptions remain as-is.

## Planning Scope

This deliverable ends at Speckit plan stage. No task list, production code,
specification change, commit, push, or PR is included.

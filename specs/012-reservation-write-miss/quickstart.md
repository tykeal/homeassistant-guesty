<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Reservation Identity Write-Miss Refresh

## Implementation verification

Run commands from the repository root:

```bash
uv run pytest tests/test_actions.py tests/test_coordinator.py tests/test_identity.py
uv run pytest tests/test_custom_field_service.py
uv run pytest tests/api/test_actions.py tests/api/test_custom_fields.py
uv run pytest tests/
uv run ruff check custom_components/ tests/ scripts/
uv run --group releasenotes mypy custom_components/ tests/ scripts/
```

The planning stage does not execute these tests. The focused suites should
first cover:

- a successful miss-triggered refresh for each reservation write type;
- invalid input, baseline/persistence/continuity/ambiguity, and known-route
  refusals that make no refresh request;
- absent versus present-code-less targets, with both failing closed if still
  absent or code-less after refresh;
- overlap with scheduled and direct in-progress refreshes, concurrent misses,
  stale retained data, update failure, ambiguity, cancellation, and source or
  entry reload changes;
- current v3/legacy provenance after waits and note read-before-write;
- configured source filters, existing guest lookup policy and bounded
  fallback behavior, with no per-ID identity request;
- exactly one write after recovery under transient, 401, 429, and 5xx
  responses, while cache-hit retry behavior remains unchanged;
- storage failure and sanitized user-facing errors/logs/diagnostics.

## Compatibility verification

The implementation relies on the coordinator lock/completion behavior
observed in Home Assistant 2026.4.0 (the declared minimum) and 2026.10.4 (the
current lockfile environment). Run the compatibility tests in the locked
environment and in an isolated compatible 2026.4.0 environment without
changing `uv.lock`; record the actual installed versions and outcomes. If
the minimum-version environment cannot be built, report that gate as
blocked. A failed, cancelled, or skipped HA refresh must never be treated
as success
because `async_refresh()` returned or stale coordinator data remains
available.

## User-visible contract

There are no new service fields, configuration options, sensors, entities,
or manual refresh controls. Existing service calls remain unchanged. A
successful eligible miss may cause one full refresh through the selected
reservation source; a failed refresh or failed guard produces a clear service
error and sends no reservation write.

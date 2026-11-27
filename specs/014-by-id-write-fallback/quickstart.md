<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: By-ID Reservation Write Fallback

## Implementation verification

Run these commands from the repository root:

```bash
uv run pytest tests/api/test_actions.py tests/api/test_client.py
uv run pytest tests/api/test_reservations_v3.py tests/test_coordinator.py tests/test_identity.py
uv run pytest tests/test_actions.py tests/test_custom_field_service.py
uv run pytest tests/test_privacy_logging.py tests/test_diagnostics.py
uv run pytest tests/
uv run ruff check custom_components/ tests/ scripts/
uv run --group releasenotes mypy custom_components/ tests/ scripts/
```

The planning stage does not run these. Write the tests first, phase by
phase:

1. **API read**: `read_reservation_by_id` sends one GET with
   `reservationIds={id}`, and its envelope and categories follow the
   contract table. There is no retry on 429 or 5xx and no token renewal
   on 401. `add_reservation_note(prefetched_notes=...)` issues no read
   and echoes every note field.
2. **Snapshot**:
   - `returned_ids` is every raw v3 search ID, `rejected_ids` is raw
     minus parsed, and `fallback_observed` carries the legacy lookups.
   - The session legacy-observed set keeps out-of-selection lookups
     across refreshes, is bounded, and marks itself incomplete on
     overflow. The session legacy-retired set does the same for
     `_retire`, and `_prune` does not shrink it.
   - The snapshot is recorded only for an accepted refresh, is bound to
     the outcome attempt, and is replaced on the next accepted refresh.
   - The snapshot is not used after a failed or newer refresh.
   - The full `observed_codes` survives past the 5000-entry baseline
     bound.
   - The identity conflict query is read-only.
3. **Dispatch**:
   - The #223 regression for all three write types.
   - The code-less qualifier.
   - The pre-read, read, authorization, and recheck refusal matrix.
   - Single-attempt write failure.
   - Overlap.
   - Retention (SC-004).
   - Privacy sentinels (SC-006).
   - The adapter-unavailable inert behavior, where the unchanged 012
     refusal is raised without a read.

## Compatibility verification

Repeat the feature 012 Home Assistant compatibility runs (the 2026.4.0
minimum and the locked 2026.10.4) for the dispatch-lock and witness tests.
The by-ID path holds the same lock the 012 path uses, and the tests must
show that a concurrent coordinator refresh waits for the critical section.

## User-visible contract

No new service field, option, entity, or control is added.

When the qualifying refresh misses the target, an action that today fails
with "still missing after refresh" or "present without a confirmation
code" may now succeed. This requires all of the following:

- the source is v3;
- the target has no fallback provenance;
- one lookup by reservation ID returns a valid, allowed, coded,
  unconflicted record.

Every other outcome produces a clear error. The error says whether the
reservation was found by ID, not found, found but refused (with a
reason), or whether the lookup failed, and it reveals no code or guest
data. Nothing learned by the lookup is kept. The next accepted refresh
remains authoritative.

Until feature 013's evidence interfaces land, the fallback never reads.
Actions fail with the same feature 012 refusal they produce today, and
no extra request is made.

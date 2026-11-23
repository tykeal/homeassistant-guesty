<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Reservation Identity Write-Miss Refresh

## Scope

This feature adds transient coordination state only. It does not change the
feature 008 persisted `IdentityBaseline`, Store key/version, reservation
schema, public service fields, or Home Assistant entities.

## Existing entities

### Reservation identity

The current reservation identifier, its confirmation-code evidence (when
present), and current source provenance. Feature 008 continues to determine
whether this identity is confirmed and writable. Confirmation codes remain
cross-check evidence only; they are never lookup or write keys.

### Identity baseline

Existing per-config-entry stored identity evidence. Relevant write guards
remain: baseline exists and is usable; storage succeeded; continuity is not
unconfirmed; no unresolved ambiguous refresh exists; and the current target
has a confirmation code. No new fields or alternate baseline are introduced.

### Coordinator publication and provenance

`ReservationsCoordinator.data` remains the current per-listing published
projection. Its `fallback_served_ids` / `fallback_sourced_ids` and
`reservations_path` remain the provenance/routing authorities. Current
publication distinguishes a missing target from a present code-less target;
both may qualify for one recovery attempt only after other refusals pass.
Feature 008 still decides whether a fallback target is actually routable.

## New transient entities

### Refresh attempt

An in-memory record associated with one coordinator refresh cycle:

- `generation`: Monotonically increases when a real update begins.
- `entry_id` / coordinator identity: Scope to the loaded entry.
- `source_configuration`: Configured path and options, still matching on use.
- `started` / `completed`: Lifecycle markers; skips are not completed fetches.
- `outcome`: Accepted success, failure, ambiguity, cancellation, or skip.
- `data_generation`: Accepted result/provenance; never identity secrets.

This state is not persisted and is not exposed in diagnostics.

### Reservation write attempt

One invocation of note, status, or reservation custom-field service:

- `target_id`: Caller-supplied identifier, validated before recovery.
- `operation`: Note, status, or reservation custom field.
- `preflight`: Input validation and independent identity/routing refusals.
- `miss_kind`: Target absent or present but without current identity evidence.
- `start_generation`: Attempt sequence and in-flight status at miss detection.
- `qualified_generation`: The single completed accepted attempt used.
- `write_route`: Re-evaluated immediately before dispatch, not before waiting.
- `write_count`: Zero before dispatch, at most one after qualifying recovery.

The attempt does not retain or log confirmation-code values, guest fields,
credentials, or custom-field values.

## State transitions

```text
Service call
  ├─ invalid schema/payload/target ───────────────> refuse; no refresh
  ├─ independent identity or known-route refusal ─> refuse; no refresh
  ├─ current confirmed/writeable cache hit ───────> existing path unchanged
  └─ valid absent or code-less identity miss
       ├─ join an in-progress/post-detection coordinator attempt
       └─ otherwise run one configured coordinator refresh
            ├─ failed/skipped/cancelled/ambiguous/stale/reloaded
            │    └─> refuse; retained data is not fresh evidence
            ├─ success, target absent/code-less, baseline unstored,
            │  or any current guard/route fails
            │    └─> refuse; no write
            └─ success and all guards still pass
                 └─> current route + one single-attempt write
```

Overlapping eligible calls may await the same completed refresh generation.
They do not share authorization: each validates its own target and reruns
every guard. A shared failure or omitted target is not followed by another
refresh for either action.

## Persistence and retention

- No new persistence is added.
- Existing successful coordinator refreshes keep the feature 008 behavior:
  the identity baseline is stored before becoming authoritative.
- If storage fails, coordinator refresh success may still be true and data may
  publish, but `_unstored` remains a write refusal. Recovery must not clear,
  recreate, or promote the baseline.
- Last-known-good coordinator data after a failed/ambiguous attempt is
  retained for normal sensors but cannot satisfy write-miss freshness.
- Current fallback bounds, served-record retention, candidate retirement,
  and guest lookup cache behavior remain unchanged.

## Validation and privacy

- Service schemas validate call shape before handlers run. Existing
  client-level reservation note/status validation and custom-field target,
  definition, applicability, and value validation must all be performed
  before requesting recovery.
- A non-empty but absent reservation ID is not invalid solely because it is
  not cached; it is a candidate miss. Blank/invalid IDs are refused first.
- Logs and diagnostics must not add confirmation codes, PIN/custom-field
  values, guest personal information, credentials, request bodies, or
  response payloads.

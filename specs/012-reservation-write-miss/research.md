<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Reservation Identity Write-Miss Refresh

## Scope and decisions

No unresolved technical choice remains in this plan. The feature specification
settles user-visible behavior and boundaries; repository code, feature 008
artifacts, and the supported Home Assistant source resolve the implementation
questions. No research agent was needed because no unknown remained after
direct source inspection. Guesty live behavior is intentionally not researched
or changed; feature 008 continues to mark its vendor assumptions as
unverified.

## Home Assistant refresh and debounce semantics

**Decision**: Track refresh attempts on the existing
`ReservationsCoordinator`, sharing the same debouncer lock and running its
normal coordinator refresh path only when no qualifying update completed
after the miss was detected. Record a monotonically increasing in-memory
attempt generation and completion outcome; decide success only after the
coordinator has updated its success state. Do not treat either refresh API's
`None` return or retained `data` as proof of a fresh result.

**Rationale**:

- The installed package is Home Assistant `2026.10.4`
  (`uv.lock`; loaded from the worktree's `.venv` for inspection).
- In installed `homeassistant/helpers/update_coordinator.py`,
  `async_request_refresh()` awaits `Debouncer.async_call()`. The installed
  `Debouncer` has `immediate=True`, a 10-second cooldown, and returns early
  when a timer or execution lock is already active. Its request can therefore
  be coalesced/scheduled without waiting for the update to finish.
- `async_refresh()` acquires the same debouncer lock and calls
  `_async_refresh()`. If another update owns the lock, it waits; after the
  other update completes it starts another update rather than using that
  result.
- `_async_refresh()` catches update exceptions including
  `UpdateFailed`/ambiguity, updates `last_update_success`, and usually
  returns normally. In 2026.10.4 it catches `CancelledError` but re-raises
  when the current task is actively being cancelled; other cancellation
  outcomes are recorded as failures. On failure it leaves last-known-good
  `data` in place. Thus a direct `await
  async_refresh()` or checking `data` alone can mistake stale data for a
  fresh success.
- The same relevant behavior is present in Home Assistant 2026.4.0, the
  minimum in `pyproject.toml`: `async_request_refresh()` delegates to the
  debouncer; `async_refresh()` locks and invokes `_async_refresh()`; the
  latter consumes normal update failures and preserves previous data.
- The coordinator's debouncer lock is the serialization boundary shared by
  interval refreshes, request refreshes, and direct refreshes. To satisfy
  join semantics without introducing an independent API fetch, the write-miss
  helper must track completion after miss detection, including an attempt
  already in progress at detection, and invoke the normal coordinator update
  only if no qualifying attempt has completed. Because the needed lock and
  `_async_refresh` hook are protected
  Home Assistant details, keep the adapter isolated and test both supported
  version endpoints before implementation is considered complete.

**Alternatives considered**:

- `async_request_refresh()` alone: rejected because it can return before
  execution and does not provide a completion verdict.
- `async_refresh()` alone: rejected because an in-flight refresh completes,
  then `async_refresh()` performs an additional refresh rather than joining.
- A direct `api_client.get_reservations()` call, a per-ID lookup, or a second
  source-specific fetch: rejected because it bypasses coordinator publication,
  configured source and options, identity checks, and existing fallback
  bounds.
- Inferring freshness from coordinator `data`, listeners, or
  `last_update_success` without an attempt generation: rejected because
  failed updates retain data, successful identical data may not notify
  listeners, and an earlier success can leave the success flag true until a
  new attempt completes.

## Existing service validation and error handling

**Decision**: Keep the three external service signatures unchanged. Perform
existing and structurally necessary validation before miss recovery; invoke
the same existing API clients after authorization.

**Rationale**:

- Home Assistant's `ServiceRegistry.async_call()` validates the registered
  voluptuous schema before it constructs and invokes the service handler.
  `actions.py` schemas already reject missing/wrongly typed required fields
  and invalid reservation status before the handler runs.
- Note text emptiness/length and non-empty reservation ID are validated
  inside `GuestyActionsClient.add_reservation_note()`, currently after
  `execute_reservation_write()` has evaluated identity. Those checks must
  move into or be called by the service preflight before an eligible miss
  can request refresh.
- Status values are also checked in the API client; service schema
  validation remains first. Use the existing validator before recovery to
  protect direct/internal call paths as well.
- Reservation custom-field definitions, applicability, and value type are
  already checked before the service calls `execute_reservation_write()`.
  The non-empty target ID check is currently inside `set_field()` and must
  also run before recovery. Listing custom-field actions stay outside this
  feature.
- Existing handlers convert reservation action/API/value errors to
  `HomeAssistantError`. The custom-field service has its own error mapping
  and logs `GuestyCustomFieldError.message`; API clients may place a
  truncated response excerpt in that message. Truncation alone does not
  prove a response cannot echo a PIN or other sensitive value. Recovery
  errors/logging must therefore use a safe operation/status/category rather
  than raw request/response data, and tests must place a sentinel value in a
  mocked error response and assert it is absent from service errors, logs,
  and diagnostics. Preserve the existing service surface while
  distinguishing refresh, missing identity, and guard/routing failures.

**Alternatives considered**:

- Running refresh before all client validation: rejected because invalid
  requests would make network calls and violate FR-002.
- Changing service schemas or adding options: rejected; the public service
  contract is unchanged.

## Feature 008 identity, persistence, ambiguity, and routing

**Decision**: Treat feature 008's identity guard and fallback as the only
source of write eligibility and route evidence. Reuse its successful
coordinator result; do not alter baseline persistence, comparison, or
fallback policy.

**Rationale**:

- `ReservationIdentityGuard.write_refusal()` checks that the baseline is
  usable and stored, continuity is not unconfirmed, the latest refresh is
  not ambiguous, and the current baseline has the target's confirmation-code
  evidence. The code value is a cross-check, not a request key.
- A reservation may publish without a confirmation code. It is not a write
  target on its own; this feature allows one recovery attempt for a
  code-less current target and still refuses if the fresh result remains
  code-less.
- `ReservationsCoordinator._check_identity()` compares only already fetched
  records. Same-source mapping changes and duplicate-code collisions abort
  with `UpdateFailed` and mark ambiguity. Cross-path disagreement, bootstrap
  evidence, and fallback-only evidence retain their distinct existing
  treatment.
- `async_record_refresh()` persists a new baseline before adopting it.
  Storage failure is intentionally swallowed so the reservation refresh can
  publish; the identity guard sets `_unstored` and refuses writes until
  persistence succeeds. Therefore “coordinator refresh succeeded” does not
  override an unpersisted baseline refusal.
- Coordinator provenance is reflected by the current fallback served/sourced
  sets. A target served through legacy may route there only while the existing
  legacy write path is available; retained-only fallback targets do not route
  to either source. Failed fallback re-checks preserve a previously served
  validation under the existing policy.
- Refresh uses `get_reservations()` with the coordinator's configured path,
  existing time/status/listing selection, pagination, existing bounded
  fallback candidates and current guest lookup. Guest lookup failure is
  already contained and does not make the overall refresh fail.

**Alternatives considered**:

- Rebuild/promote the baseline to make an absent target writable: rejected
  by FR-009 and the fail-closed storage semantics.
- Probe an identifier, confirmation code, guest, or alternate path: rejected
  by FR-005, FR-012, FR-013 and feature 008 FR-034.
- Replace fallback, guest lookup, or configured source policies: rejected by
  FR-005 and the explicit feature boundary.

## Write retry behavior

**Decision**: Set the API client's existing `single_attempt=True` only for
the actual reservation write after miss-triggered recovery, for both v3 and
legacy routes and all three write types. Keep note prerequisite reads under
their current behavior and leave cache-hit writes unchanged.

**Rationale**:

- `_request()` retries connection/timeouts and transient 5xx responses,
  retries 429s, and retries authentication after 401 by default.
- Existing legacy-routed reservation writes already use `single_attempt`
  because repeating a write can duplicate an externally visible change.
  Existing v3 writes do not. FR-014 extends one-write behavior only to an
  action that performed or shared a miss refresh.
- `add_reservation_note` is read-before-write, so a failed read must abort;
  the write's attempt mode must be applied to the final PUT, not the
  prerequisite GET.
- The custom-field API already validates values and preserves strings such as
  leading-zero access codes; never log field values or request bodies.

**Alternatives considered**:

- Relying on “usually one write” while leaving retries enabled: rejected,
  because a 401, 429, transient failure, or 5xx can repeat the write.
- Globally changing the API client's retry defaults: rejected because
  cache-hit actions and unrelated API calls must retain current behavior.

## Feature 008 request-boundary reconciliation

**Decision**: Preserve zero-extra-request per-refresh comparison under FR-034.
The sole additional-request allowance is this feature's one full coordinated
write-miss refresh, as FR-013 explicitly scopes.

**Rationale**: The service does not initiate a baseline comparison request,
targeted lookup, sweep, or request keyed by a confirmation code. The ordinary
coordinator refresh may perform its already specified source retrieval,
pagination, bounded fallback, guest enrichment, and identity comparison.

**Alternatives considered**: No separate lookup strategy is admissible under
the merged specification.

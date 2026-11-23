<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Reservation Write-Miss Recovery

**Consumers**: Existing Home Assistant actions
`guesty.add_reservation_note`, `guesty.set_reservation_status`, and
`guesty.set_custom_field` for `target_type: reservation`.

**Scope**: Adds one conditional, configured-coordinator refresh before an
otherwise eligible reservation write when current identity evidence is
missing. Service names, required fields, response shape, write resources,
baseline schema, and normal cache-hit behavior are unchanged.

## Preflight

1. Home Assistant applies each service's registered voluptuous schema before
   its handler. Missing/wrongly typed fields and invalid reservation status
   values fail before any recovery work.
2. The handler/client validates reservation ID and all operation-specific
   inputs before any miss refresh. This includes note text constraints and
   reservation custom-field target ID, field definition/applicability, and
   value type.
3. Evaluate the existing feature 008 identity and known routing guards.
   Missing/unusable/unpersisted baseline, unconfirmed continuity, an
   ambiguous latest refresh, a known non-routable target, or any other
   independently determinable refusal produces an actionable service error
   without refresh or write.
4. A valid target absent from current identity evidence is a miss. A
   currently published reservation with no confirmation code is a distinct
   miss state; both may request recovery after steps 1–3 pass. A listing
   custom-field operation is never eligible.

## Refresh sharing and freshness

- Use only the same configuration entry's current
  `ReservationsCoordinator` and configured reservation path.
- Join a coordinator refresh already in progress at miss detection if it
  completes after detection. Concurrent eligible misses share the same
  attempt where possible; each call independently rechecks its target and
  guards. Cancellation of one waiter propagates to that action without
  cancelling the coordinator-owned shared attempt for the other waiters.
  Cancellation of the attempt itself cannot authorize any waiter.
- If no attempt that could complete after miss detection is available to
  join, perform one normal coordinator refresh. Joining an attempt consumes
  the one-cycle limit even if it fails, is cancelled, is ambiguous, is
  skipped, or omits the target; do not start a second refresh in that action.
- The refresh retains existing window, status, listing, pagination,
  supplementary guest lookup, legacy fallback cap/recheck/retention, cache,
  and transport retry behavior. It performs no per-ID or speculative
  identity lookup. Existing identity comparison remains request-free; this
  is the sole scoped exception allowing one write-miss-triggered full refresh.
- Qualifying means a real attempt completed successfully after the miss,
  HA reports `last_update_success`, and the accepted result is for the same
  live entry and source configuration. An earlier completed, skipped,
  throttled, failed, cancelled, stale, or superseded result does not qualify.
  Retained last-known-good `data` is never proof of freshness.
- If entry/source configuration changes or a later refresh fails or is
  ambiguous, refuse instead of using earlier authorization. If a newer
  accepted refresh supersedes the qualifying result, recheck all guards
  and use the newer result's current provenance.

## Post-refresh authorization and write

Before any write, independently re-evaluate:

- the live entry and coordinator generation;
- current refresh success and ambiguity;
- persisted baseline, storage status, and continuity;
- current target presence and current confirmation-code evidence;
- feature 008 cross-path and fallback provenance rules; and
- whether the current route is available.

A target still absent, present but code-less, ambiguous, unconfirmed,
unpersisted, or unroutable fails closed. Confirmation codes remain
cross-checks, never resource lookup/write keys. A target returned by the
current refresh uses the current accepted provenance: v3 uses the v3
resource; fallback-sourced targets use legacy only when successfully served
in this session and the legacy route remains available. Retained-only
fallback targets do not route to either source.

Use the same current identity and routing safeguards as a cache-hit write.
Publication is not an additional write-authorization boundary: a fetched,
coded target excluded from publication by selected-listing attribution or
local date trimming may still be writable if those safeguards pass. Do not
change the configured fetch and selection rules or add targeted requests.

For a recovery action, hold the coordinator's
`_debounced_refresh.async_lock()` from final authorization through the
actual write. Do not reenter it while holding it. The note operation holds
the lock across its existing read-before-write and aborts if its prerequisite
read fails. Recheck after the awaited prerequisite read and select the route
from the current accepted generation immediately before dispatch.

After recovery, make at most one actual reservation write. The actual PUT
uses the existing API client's single-attempt mode for either v3 or legacy,
including on 401, 429, transient transport failure, or transient 5xx. Do not
retry against another source. A note prerequisite GET retains its existing
retry behavior. Actions without recovery preserve existing retry behavior.

## Outcomes and privacy

- Success is reported only when the actual write succeeds.
- Refresh failure/staleness, target still absent/code-less, and identity or
  routing refusals are distinguishable actionable service errors.
- Rejected writes and failed note reads are errors, never success; a failed
  prerequisite read sends no write.
- Do not expose raw API response bodies, confirmation codes, PIN/custom-field
  values, guest personal data, credentials, or request bodies in recovery
  logs, diagnostics, or error messages. Test a mocked response that echoes a
  sentinel field value.

## Required contract tests

Test all three eligible actions; invalid requests and all independent
refusals without refresh; absent/code-less results after refresh; overlapping
and in-flight refresh sharing; HA-swallowed fetch failure with stale data;
ambiguity, cancellation of one of two waiters, cancellation of the refresh,
persistence failure, reload/source change, newer failure, changed
provenance; fallback served/retained/disabled cases;
note prerequisite failure; listing-write exclusion; exactly one write after
recovery even when transport retries are normally enabled; unchanged
cache-hit retry behavior; and secret-free diagnostics/logs.

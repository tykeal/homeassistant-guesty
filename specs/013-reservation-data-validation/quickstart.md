<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Verifiable Reservation Data

## Implementation verification

Run commands from the repository root:

```bash
uv run pytest tests/api/test_validation.py tests/api/test_models.py
uv run pytest tests/api/test_reservations_v3.py \
  tests/api/test_reservations_fallback.py tests/api/test_timing.py
uv run pytest tests/test_identity.py tests/test_coordinator.py \
  tests/test_reservation_sensor.py tests/test_reservations_parity.py
uv run pytest tests/test_actions.py tests/test_custom_field_service.py
uv run pytest tests/test_diagnostics.py tests/test_privacy_logging.py \
  tests/test_init.py
uv run pytest tests/
uv run ruff check custom_components/ tests/ scripts/
uv run ruff format --check custom_components/ tests/ scripts/
uv run --group releasenotes mypy custom_components/ tests/ scripts/
pre-commit run --all-files
```

The planning stage does not run these tests. Each implementation phase
adds failing tests first, and the focused suites should cover the
following.

**Validator and parsing**:

- every validation reason;
- isolated rejection versus batch fail-closed;
- partial stays keeping raw-stay eligibility;
- the written-date fallback is gone;
- invalid optional fields are dropped;
- the 014 action fact table.

**Identity**:

- rejected mappings keep the existing ambiguity and soft-conflict
  outcomes and never add continuity matches;
- conflict-only persistence, retirement, the 5000 bound and saturation;
- the compromised and missing store;
- write refusal in every unavailable state;
- identity store minor-version migration with revalidation pending;
- rollback compatibility of the payload.

**Fallback**:

- rejected v3 records are never looked up, served or retained;
- a failed lookup marks the record stale;
- a successful lookup or v3 absorb clears staleness.

**Publication**:

- a past `confirmed` stay expires on the single-raw-segment v3,
  multi-segment v3 and legacy paths;
- eleven expired plus one future stay shows the future stay
  (SC-003);
- checkout-day uncertainty;
- the unverified-timezone window and expiry bound;
- an unrecognized status is never published.

**Stale handling**:

- a failed refresh re-projects the last verified snapshot at the
  current instant;
- an expired stale stay is not current;
- reservation and financial sensors stay available with `stale: true`
  and `last_verified_at`, including after a second consecutive failure,
  with no intermediate `unavailable` state on a first failure;
- stale display never satisfies a write guard;
- an accepted refresh clears `stale`;
- nothing is invented with no snapshot, and the entities are
  unavailable.

**Reporting**:

- warning once, DEBUG on repeats, a warning on recurrence after
  resolution, and an overflow aggregate;
- timezone and conflict-evidence repair issues;
- diagnostics block shape;
- `caplog` and diagnostics redaction sentinels (codes, guest names,
  unrecognized status strings, notes).

## Upgrade verification

Use a `hass_storage` fixture that holds a version 1, minor 1 identity
payload, with valid and malformed code maps and candidate hints:

1. Set up the entry and assert:
   - the payload loads;
   - malformed entries are dropped or downgraded;
   - `revalidation_pending` is true;
   - every reservation write is refused with the revalidating reason.
2. Complete one accepted refresh and assert:
   - writes follow the normal guards;
   - the saved payload is minor version 2;
   - `conflict_evidence_generation` matches the evidence store's
     `generation`.
3. Load the saved payload with a minor version 1 `Store` to confirm that
   rollback reads it.
4. Restart with both stores intact and assert that writes are refused
   as revalidating until the first accepted refresh is stored.
5. Remove the conflict-evidence store, or roll its generation back
   below the identity payload's expected generation. Assert that the
   state is `compromised` and that writes are refused.
6. Fail only the first evidence save in an accepted refresh, restart,
   and assert that the store loads as `compromised`.
7. Fail only the identity save, stop returning the rejected record,
   run a successful refresh, and assert that the stored evidence still
   holds the pair.
8. Run an accepted refresh with no evidence changes, restart, and
   assert that the store loads `revalidating`, not `compromised`, and
   becomes `ready` after the next accepted refresh.

## User-visible contract

There are no new services, service fields, configuration options or
entities. Visible changes:

- `stale` and `last_verified_at` attributes on reservation and
  financial sensors, and `stale` on `upcoming` items;
- uncertain (`unknown`) checkout-day status;
- unrecognized statuses are no longer published;
- expired stays no longer crowd `upcoming`;
- a listing-timezone repair issue and a conflict-evidence repair issue;
- a `validation` diagnostics block.

During an overall refresh failure with a verified snapshot, the
reservation and financial sensors stay available with the last-verified
data marked stale; diagnostics show the same stale state (research
R13). With no snapshot they are unavailable.

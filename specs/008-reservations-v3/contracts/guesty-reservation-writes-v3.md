<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Guesty Reservation Writes v3 (as consumed)

**Consumer**: `GuestyActionsClient` in
`custom_components/guesty/api/actions.py`
**Replaces**: the catch-all `PUT /v1/reservations/{id}`
(`api/actions.py:115-120`, `api/actions.py:364-369`), retiring
2027-03-01
**Status**: every statement here is **[UNVERIFIED]** documentation-derived
(FR-041)

Both actions are gated by the identity guard before any request is sent.
A write is refused when no identity baseline exists, when the refresh
that produced the current data was aborted as ambiguous, or when the
target reservation has no confirmation code (FR-031c, FR-032, FR-033).

## 1. Add reservation note

**Today**: `GET /v1/reservations/{id}` then
`PUT /v1/reservations/{id}` with `{"note": …}`
(`api/actions.py:56-137`).

### 1.1 Read (preserves existing text)

| Item | Value |
| --- | --- |
| Resource | `GET /v1/reservations-v3?reservationIds={id}` |
| Parameters | `reservationIds` only; form-exploded |
| Consumed | `[0].notes.other` |

The coordinator's cached copy is deliberately not used: it can be a full
poll interval stale, and writing it back would silently discard a note
added in Guesty meanwhile.

### 1.2 Write

| Item | Value |
| --- | --- |
| Resource | `PUT /v1/reservations-v3/{reservationId}/notes` |
| Body | `{"other": "<existing>\n---\n<new>"}` |
| Separator | `NOTE_SEPARATOR` (`api/const.py:190-191`), unchanged |
| Validation | unchanged (`api/actions.py:418-440`) |

The legacy single `note` field is assumed to map to `notes.other`
(**[UNVERIFIED]**). Whether the resource replaces or merges the note set
is OQ-002: if it merges, the read in §1.1 is dropped and the action costs
one request fewer.

### 1.3 Outcomes

- **Read 2xx, write 2xx**: `ActionResult(success=True, target_id=…)`.
- **Read non-2xx**: `GuestyActionError` naming the resource, the status
  and a truncated body (`api/actions.py:389-414`); no write is
  attempted.
- **Write non-2xx**: `GuestyActionError` as above; never reported as a
  success (FR-029).
- **Guard refuses**: `GuestyActionError` explaining why and how to
  proceed, with **no request sent**.

## 2. Set reservation status

| Item | Value |
| --- | --- |
| Resource | `PUT /v1/reservations-v3/{reservationId}/status` |
| Body | `{"status": "checked_in"}` or `{"status": "checked_out"}` |
| Accepted values | `VALID_RESERVATION_STATUSES`, unchanged |
| Validation | unchanged (`api/actions.py:441-470`) |

| Result | Behaviour |
| --- | --- |
| 2xx | `ActionResult(success=True, target_id=…)` |
| non-2xx | `GuestyActionError` with the rejection detail (FR-029) |
| guard refuses | `GuestyActionError`; no request is sent |

## 3. Unchanged resources

Reservation custom-field reads and writes already use
`/reservations-v3/{id}/custom-fields` (`api/const.py:142-143`,
`api/custom_fields.py:116-141`, `api/custom_fields.py:200-260`) and are
**not** modified by this feature (FR-030).

## 4. Legacy path

When `reservations_api` is `legacy`, both actions issue exactly the
requests the previous release issued, byte for byte (FR-040). A legacy
failure whose shape is consistent with retirement (HTTP 404 or 410 on
`/v1/reservations/{id}`) is re-raised with a message telling the user to
set the option back to `v3` (FR-039).

## 5. Contract tests

- each action targets the v3 resource with the exact path and body;
- the note action reads before writing and preserves existing text with
  the unchanged separator;
- a non-success read or write raises, and never returns success;
- the guard blocks a write with no baseline, after an ambiguous refresh,
  and for a reservation with no confirmation code, sending no request;
- in legacy mode both actions still target `/v1/reservations/{id}`;
- the retirement-shaped legacy error carries the rollback instruction.

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
A write proceeds only when **every** one of these holds:

1. a usable identity baseline exists;
2. its `continuity` is `verified` or `not_applicable` — an
   `unconfirmed` baseline, which is what a failed or uninformative
   bootstrap leaves behind, blocks writes even though a baseline exists;
3. the refresh that produced the current data was not aborted as
   ambiguous;
4. the target reservation carries a confirmation code;
5. the target reservation is **not fallback-sourced** — that is, it is
   not currently being served from the legacy not-found fallback
   (FR-055). Its identifier would then have come from a legacy
   response, and Guesty Support states that an identifier that did not
   come from v3 returns "Reservation not found" on v3 resources
   (FR-031e). The refusal names the cause — the reservation is not yet
   available in v3 — and no request is sent (SC-016).

The confirmation code in condition 4 is a **cross-check only**. It is
never sent as, or used to derive, the `{reservationId}` path parameter
of any resource below; that identifier always comes from a v3
response.

Otherwise the action is refused with an explanatory error and no request
is sent (FR-031c, FR-032, FR-033). The predicate is re-evaluated from
the Store after a reload, and the guard tests cover each failing
conjunct, including after a reload.

## 1. Add reservation note

**Today**: `GET /v1/reservations/{id}` then
`PUT /v1/reservations/{id}` with `{"note": …}`
(`api/actions.py:56-137`).

### 1.1 Read (preserves existing text)

| Item | Value |
| --- | --- |
| Resource | `GET /v1/reservations-v3?reservationIds={id}` |
| Parameters | `reservationIds` only; form-exploded |
| Consumed | `[0].notes` — the whole object, not just `other` |

The coordinator's cached copy is deliberately not used: it can be a full
poll interval stale, and writing it back would silently discard a note
added in Guesty meanwhile.

**Not found on this read**: if the v3 read reports the reservation does
not exist, the write MUST be abandoned with an explanatory error, never
retried against a legacy write resource, and never reported as success.
Guesty Support advises that a v3 not-found may mean the reservation has
not been backfilled into v3 yet rather than that it is gone; condition 5
above is intended to catch that case before the read is even attempted,
and this read is the backstop for a reservation that disappeared from v3
between the last refresh and the write. Whether such a write should
instead be routed to the legacy write resource is spec OQ-010 and awaits
an owner decision.

### 1.2 Write

| Item | Value |
| --- | --- |
| Resource | `PUT /v1/reservations-v3/{reservationId}/notes` |
| Body | every note field read in §1.1, with `other` appended to |
| Separator | `NOTE_SEPARATOR` (`api/const.py:190-191`), unchanged |
| Validation | unchanged (`api/actions.py:418-440`) |

The legacy single `note` field is assumed to map to `notes.other`
(**[UNVERIFIED]**). Whether the resource replaces or merges the note set
is OQ-002, and both answers are handled conservatively:

- The body echoes **every** writable note field captured by the read, so
  a replacing resource cannot delete `notes.guest` or any sibling field.
  A multi-category fixture — a reservation carrying both `other` and
  `guest` notes — asserts that both survive the write.
- The read in §1.1 is **not** dropped on the strength of merge semantics
  alone: merging note *objects* is compatible with replacing the
  `notes.other` *string*, which would erase the existing note text.
  Dropping the read requires evidence that the server appends text with
  the required separator; absent that, the read stays.

If live verification instead shows that the resource neither merges
fields nor preserves text, and the full-object echo above is rejected by
the API, the action refuses rather than writing a partial note set.

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

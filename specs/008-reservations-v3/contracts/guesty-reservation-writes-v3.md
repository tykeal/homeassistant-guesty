<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Guesty Reservation Writes v3 (as consumed)

**Consumer**: `GuestyActionsClient` in
`custom_components/guesty/api/actions.py`
**Replaces**: the catch-all `PUT /v1/reservations/{id}`
(`api/actions.py:115-120`, `api/actions.py:364-369`), retiring
2027-03-01 — except for a fallback-sourced target, which keeps using it
until then (§4)
**Status**: every statement here is **[UNVERIFIED]** documentation-derived
(FR-041)

Both actions are gated by the identity guard before any request is sent,
and the guard is evaluated **before** the resource generation is chosen.
A write proceeds only when **every** one of these holds:

1. a usable identity baseline exists;
2. its `continuity` is `verified` or `not_applicable` — an
   `unconfirmed` baseline, which is what a failed or uninformative
   bootstrap leaves behind, blocks writes even though a baseline exists;
3. the refresh that produced the current data was not aborted as
   ambiguous;
4. the target reservation carries a confirmation code;
5. if — and only if — the target is fallback-sourced, the legacy write
   routing of §4 is available: when the fallback has self-disabled or
   the legacy path is retired, such a write is refused with an error
   naming the 2027-03-01 retirement and no request is sent (FR-061,
   SC-019); and when the target is fallback-sourced but only
   republished from retained values and has never been served in this
   session (FR-054), it is refused as not currently reachable in either
   API, again with no request (FR-058).

Once the guard permits a write, the **route** is chosen from the
target's provenance, read at write time from the fallback's `served`
map: a target the fallback is currently serving goes to §4, everything
else to §1 and §2. "Currently serving" means a legacy by-id lookup for
that reservation succeeded in this session and its served record has not
been dropped; a failed re-check keeps the record, so it stays routable.
Being published from retained values after a lookup that has never
succeeded is not enough, because no legacy identifier has been
validated for it.
A fallback-sourced identifier came from a legacy response, and Guesty
Support states that such an identifier returns "Reservation not found"
on v3 resources (FR-031e), so it is never sent to a v3 resource
(FR-055, FR-058, SC-016).

The confirmation code in condition 4 is a **cross-check only**. It is
never sent as, or used to derive, the `{reservationId}` path parameter
of any resource below — v3 **or** legacy; the identifier of a v3 write
always comes from a v3 response, and the identifier of a legacy write is
the one the fallback lookup succeeded with (FR-060).

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

**Not found on this read**: Guesty Support advises that a v3 not-found
may mean the reservation has not been backfilled into v3 yet rather than
that it is gone. The provenance check above is what normally catches
that case, before this read is attempted; this read is the backstop for
a reservation that disappeared from v3 between the last refresh and the
write. In that case the write MUST NOT be retried against the v3
resource and MUST NOT be reported as success. It MUST be abandoned with
an explanatory error: the coordinator has not yet established that the
reservation is fallback-sourced, so §4's routing is not available to it,
and a lookup issued here to establish provenance would be a legacy call
outside the bounds FR-053 places on the fallback. The next refresh
either returns it in v3 or makes it a fallback candidate, after which a
retry routes correctly.

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

## 3. Reservation custom fields

Reservation custom-field reads and writes already use
`/reservations-v3/{id}/custom-fields` (`api/const.py:142-143`,
`api/custom_fields.py:116-141`, `api/custom_fields.py:200-260`) and are
**not** modified by this feature (FR-030) — with one exception: a write
whose target is fallback-sourced is routed to §4 like the note and
status writes, because that resource is keyed by reservation identifier
and v3 cannot resolve one it has not backfilled (FR-059). Every other
custom-field read and write is untouched.

## 4. Legacy write routing for a fallback-sourced target

Permitted exception 3 of FR-001, specified by FR-058 to FR-063. It
applies **only** to a target currently served from the legacy not-found
fallback, and only to an invoked action — never during a refresh.

| Operation | Legacy request |
| --- | --- |
| note | `GET` then `PUT /v1/reservations/{id}` with `{"note": …}` |
| status | `PUT /v1/reservations/{id}` with the legacy status body |
| custom field | `PUT /v1/reservations/{id}` carrying the field value |

The note and status rows are the previous release's exact requests
(`api/actions.py:84-91`, `115-120`, `364-369`), which FR-040 keeps
working and tested; the note row uses the **legacy** read-before-write,
because the v3 read of §1.1 would not find the reservation either. The
custom-field row's body shape is **[UNVERIFIED]** and is spec OQ-011; if
live verification refutes it, that row reverts to a loud refusal as a
recorded change (FR-059). Values are sent verbatim, so a four-digit
access code keeps its leading zeros.

| Result | Behaviour |
| --- | --- |
| 2xx | `ActionResult(success=True, target_id=…)`; counted |
| non-2xx | `GuestyActionError` with the detail (FR-029, FR-063) |
| not found | confirmed absence: retire the candidate (FR-052) |
| retirement-shaped | routing self-disables; refuse (FR-061) |

A routed write is logged once at WARNING naming the operation and the
identifier, is counted by operation and outcome in both diagnostics
surfaces, exposes no sensor attribute and contains no personal data
(FR-062, SC-020). It is retried neither automatically nor against v3,
and never optimistically updates a published value. It is issued in the
single-attempt mode of FR-063, so the shared client's transient,
rate-limit and token retries cannot turn one invocation into several
writes.

## 5. Legacy path

When `reservations_api` is `legacy`, both actions issue exactly the
requests the previous release issued, byte for byte (FR-040). A legacy
failure whose shape is consistent with retirement (HTTP 404 or 410 on
`/v1/reservations/{id}`) is re-raised with a message telling the user to
set the option back to `v3` (FR-039).

## 6. Contract tests

- each action targets the v3 resource with the exact path and body;
- the note action reads before writing and preserves existing text with
  the unchanged separator;
- a non-success read or write raises, and never returns success;
- the guard blocks a write with no baseline, after an ambiguous refresh,
  and for a reservation with no confirmation code, sending no request;
- in legacy mode both actions still target `/v1/reservations/{id}`;
- the retirement-shaped legacy error carries the rollback instruction;
- a fallback-sourced target routes all three operations to §4 with no
  request to any v3 write resource, while a v3-sourced target issues no
  legacy request (SC-016);
- provenance is read at write time from the current `served` map, so a
  target that a refresh completing between action scheduling and
  execution found in v3 is written through v3 (FR-058);
- a target republished from retained values whose lookup has never
  succeeded is refused with no request of either generation, while a
  target whose earlier lookup succeeded and whose re-check then failed
  is still routed to §4 (FR-054, FR-058);
- with the fallback disabled, all three operations refuse with an error
  naming the retirement and send no request (SC-019);
- a routed write logs once, is counted by operation and outcome, and
  leaks no personal data (SC-020);
- a rejected routed write raises, issues exactly one request and is
  never reported as success — a transient failure, a 429 and a 401 each
  produce exactly one legacy write request, proving the single-attempt
  mode suppresses the shared client's retries — and a not-found retires
  the candidate (FR-063, SC-021);
- a four-digit custom-field value with a leading zero is transmitted
  verbatim (FR-059).

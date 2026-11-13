<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Guesty Reservations v3 Search (as consumed)

**Resource**: `GET https://open-api.guesty.com/v1/reservations-v3/search`
**Consumer**: `V3ReservationSource.fetch()` in
`custom_components/guesty/api/reservations_v3.py`, once per coordinator
refresh (default every 15 minutes)
**Replaces**: `GET /v1/reservations` (`api/const.py:88-89`), retiring
2027-03-01
**Status**: every statement here is **[UNVERIFIED]** documentation-derived
(FR-041)

## Request

| Parameter | Value | Notes |
| --- | --- | --- |
| `filter[checkOut][gte]` | date(now − `past_days`) − 1 d | window start |
| `filter[checkIn][lte]` | date(now + `future_days`) + 1 d | window end |
| `filter[status][in]` | translated status set, sorted | comma-joined |
| `sort` | `_id` | ascending; makes paging deterministic |
| `limit` | `100` | documented maximum |
| `skip` | `0, 100, 200, …` | `page × 100` |
| Authorization | `Bearer <token>` | existing token manager |

The one-day pad exists because the date filters are documented to compare
listing-localized dates while the configured window is instant-based. The
padded server window is a **superset**; the client trim is authoritative
(FR-002a).

**MUST NOT be sent**:

- `fields` or any projection — not supported (FR-007)
- `filter[listingId]` in any form — forbidden by FR-015, because it is
  documented to match the **first** stay segment only
- any parameter the vendor reference does not document

**MUST NOT be requested**: `GET /v1/reservations` or any other resource
the migration guide marks deprecated, while the v3 path is active
(SC-001). The single exception is the one-time identity bootstrap sweep
at the first v3 setup of an upgrading entry (plan.md, "Identity guard"),
which runs before any v3 data is published and never again; it requires
the specification amendment recorded there. No *refresh* ever targets a
legacy resource on the v3 path.

Example (unencoded for readability):

```text
GET /v1/reservations-v3/search
    ?filter[checkOut][gte]=2026-10-13
    &filter[checkIn][lte]=2027-11-14
    &filter[status][in]=canceled,checked_in,checked_out,completed,confirmed
    &sort=_id&limit=100&skip=0
```

## Response fields consumed

```jsonc
{
  "results": [
    {
      "reservationId": "string",      // REQUIRED; identity
      "status": "confirmed",          // REQUIRED; mapped at parse time
      "guestId": "string|null",       // drives the guest lookup
      "confirmationCode": "string",   // identity guard; sensor attribute
      "source": "string",
      "checkIn": "ISO-8601 UTC",
      "checkOut": "ISO-8601 UTC",
      "checkInDateLocalized": "YYYY-MM-DD",
      "checkOutDateLocalized": "YYYY-MM-DD",
      "plannedArrival": "string|null",   // OQ-003, may not exist
      "plannedDeparture": "string|null", // OQ-003, may not exist
      "notes": { "other": "string", "guest": "string" },
      "customFields": [                  // absent when no value is set
        { "_id": "string", "fieldId": "string", "value": "any" }
      ],
      "stay": [                          // REQUIRED, chronological, >= 1
        {
          "listingId": "string",         // REQUIRED; unitId ?? unitTypeId
          "parentListingId": "string",   // multi-unit only
          "checkIn": "ISO-8601 UTC",
          "checkOut": "ISO-8601 UTC",
          "checkInDateLocalized": "YYYY-MM-DD",
          "checkOutDateLocalized": "YYYY-MM-DD",
          "guestsCount": 2
        }
      ]
    }
  ],
  "pagination": { "skip": 0, "limit": 100, "hasMore": true }
}
```

Every other field is ignored. `nightsCount` is assumed absent and is
derived from the localized dates. No `money` block is read: the financial
sensors are deprecated and the guest folio is **not** called.

## Page validation and outcome

A refresh is accepted only when **every** page passes every check.
Otherwise the refresh raises, last-known-good data is retained, and
nothing is published (FR-006, NFR-005).

| Condition | Outcome |
| --- | --- |
| missing `results` or `pagination`, or non-boolean `hasMore` | abort |
| `pagination.skip` ≠ requested `skip` | abort |
| `len(results) > limit` | abort |
| `hasMore` true with an empty page | abort |
| `hasMore` true with a short page | abort |
| `reservationId` missing or not a string | abort |
| duplicate `reservationId` across pages | abort |
| `reservationId` not strictly ascending | abort |
| `hasMore` still true after 200 pages | abort |
| transport error, or 429 after existing retries | abort |
| HTTP 400 | `GuestyFilterRejectedError` (see below) |
| no segment carries a `listingId` | skip the reservation, warn, continue |
| unrecognized `status` | publish verbatim with an INFO log (FR-026) |
| `status` is `alteration` | drop with a debug log (FR-024) |

An HTTP 400 raises `GuestyFilterRejectedError`, naming the endpoint and
a truncated body excerpt. It is **never** reported as an empty result
and **never** retried with the filters removed (FR-008).

## Deferred: listing-filter chunking (OQ-008 only)

Not implemented. Recorded so that adopting it later is a specification
amendment rather than a redesign. If FR-015 is ever amended:

- `filter[listingId][in]` takes a comma-joined, sorted id list;
- ids are grouped into **families** — a multi-unit parent with its
  sub-units, a tracked child with its parent unit type, a single-unit
  listing alone — and whole families are packed into chunks of at most
  `MAX_LISTING_IDS_PER_SEARCH = 50`, so a first segment's `unitId` and
  `unitTypeId` always fall in the same chunk;
- only a family larger than 50 ids is split;
- each chunk is an independent page sequence with its own guard state;
- any chunk failing its guard fails the whole refresh;
- results merge by `reservationId`; a reservation returned by two chunks
  is kept once if the copies agree and fails the refresh if they differ;
- a 50-id chunk is roughly 1.4 KB of query string, keeping the request
  line under 2 KB (OQ-007 still needs confirming).

Adopting this accepts the documented coverage gap: a stay that begins in
an untracked listing and moves into a tracked one would be excluded by
the server, unrecoverably and undiagnosably (R-005 in the specification).

## Contract tests

- the exact query string for the first and subsequent pages;
- the absence of `fields` and of any `filter[listingId]`;
- the status translation, including `completed`;
- every validation row above, from dictionary fixtures;
- that no request targets `/v1/reservations` while the v3 path is active;
- that an out-of-window fixture in the fake backend is not returned, and
  that a 200-day-old in-progress stay is (SC-004, SC-005).

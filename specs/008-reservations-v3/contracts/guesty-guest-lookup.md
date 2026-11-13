<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Guesty Guest Lookup (as consumed)

**Resource**: `GET https://open-api.guesty.com/v1/guests-crud/{guestId}`
**Consumer**: `GuestCache.get()` in
`custom_components/guesty/api/guest_cache.py`, on the v3 path only
**Why**: v3 reservations carry only `guestId`; the guest name, phone and
email the sensor publishes today (`sensor.py:384-424`) must be
re-sourced (FR-016)
**Status**: **[UNVERIFIED]** documentation-derived; the exact resource is
OQ-004

## Request

| Item | Value |
| --- | --- |
| Path | `/v1/guests-crud/{guestId}` |
| `fields` | `fullName phone email` (space-separated) |
| Authorization | `Bearer <token>` |

- Issued **at most once per distinct guest per refresh** (FR-018).
- Issued **only** for reservations attributed to a tracked listing, so
  untracked reservations returned by the completeness-preserving search
  cost nothing extra (NFR-001).
- Never issued for a reservation without `guestId`.
- At most `MAX_GUEST_LOOKUPS_PER_REFRESH = 50` per refresh, oldest cache
  entries first; the remainder keep serving their previous values and are
  retried on the next refresh (NFR-002).
- At most `GUEST_LOOKUP_CONCURRENCY = 4` in flight.

**Fallback if live verification rejects this resource**:
`GET /v1/guests/{guestId}`, which the integration's legacy path has never
used but which the vendor guide's "the `/guests` endpoints" also covers.
The cache and all budget rules are unchanged by the swap.

## Response fields consumed

```jsonc
{
  "_id": "string",
  "fullName": "string",    // -> guest_name attribute
  "phone": "string|null",  // -> guest_phone attribute
  "email": "string|null"   // -> guest_email attribute
}
```

Mapped into the existing `GuestyGuest` dataclass
(`api/models.py:533-570`) with `guest_id` set from the requested id, so
attribute names and types are exactly what the legacy embedded guest
produced (FR-017, NFR-004).

## Outcomes

- **200 with a body**: the entry is stored with a 24 h TTL and the
  attributes are published.
- **404**: a negative entry is stored with a 1 h TTL; the guest
  attributes are `None` and the reservation is still published.
- **Other 4xx/5xx, transport error, or 429 after retries**: the previous
  entry is retained and `failed_at` is recorded; the reservation is
  still published with its previous guest values and the failure is
  logged without PII (FR-020, NFR-005, SC-009).
- **401/403**: unchanged, and handled by the existing auth layer.

A cached record is only ever published for the guest id it was fetched
for; the cache has no other key (FR-019). A guest id that changes on a
reservation is a different key, so the previous guest's details cannot
leak onto the new one.

## Privacy

Guest names, phone numbers and email addresses are **never** logged, at
any level including debug (FR-021, NFR-009). Loggable items are the
guest id and counts: lookups issued, cache hits, failures, deferrals.

## Contract tests

- the exact path and `fields` value;
- one request per distinct guest per refresh, and zero when every entry
  is a fresh hit (SC-010);
- TTL expiry triggers a refetch; a 404 does not;
- failure retains the previous value and never fails the refresh
  (SC-009);
- a changed guest id never publishes the previous guest's values;
- the LRU bound evicts rather than growing without limit;
- the burst cap defers rather than bursting;
- no guest field appears in captured log records at DEBUG.

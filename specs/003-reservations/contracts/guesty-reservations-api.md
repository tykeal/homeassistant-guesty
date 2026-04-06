<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# API Contract: Guesty Reservations

**Feature**: 003-reservations
**Date**: 2025-07-25
**API Version**: Guesty Open API v1

## Endpoint: List Reservations (Filtered)

### Request

```http
GET /v1/reservations
  ?limit=100
  &skip={offset}
  &sort=_id
  &fields=_id listingId status confirmationCode
          checkIn checkOut checkInDateLocalized
          checkOutDateLocalized plannedArrival
          plannedDeparture nightsCount guestsCount
          guest.fullName guest.phone guest.email
          money.totalPaid money.balanceDue
          money.currency source note
  &filters=[
    {
      "field": "checkIn",
      "operator": "$between",
      "from": "{past_boundary_iso}",
      "to": "{future_boundary_iso}"
    },
    {
      "field": "status",
      "operator": "$contains",
      "value": [
        "confirmed",
        "checked_in",
        "checked_out",
        "canceled"
      ]
    }
  ]
```

### Response (200 OK)

```json
{
  "results": [
    {
      "_id": "66f1c4a1246e7d9f0f8da88a",
      "listingId": "66054019764cbb000f37c450",
      "status": "confirmed",
      "confirmationCode": "GY-h5SdcsBL",
      "checkIn": "2025-08-17T15:00:00.000Z",
      "checkOut": "2025-08-22T11:00:00.000Z",
      "checkInDateLocalized": "2025-08-17",
      "checkOutDateLocalized": "2025-08-22",
      "plannedArrival": "16:00",
      "plannedDeparture": "10:00",
      "nightsCount": 5,
      "guestsCount": 3,
      "guest": {
        "fullName": "Jane Smith",
        "phone": "+1-555-0123",
        "email": "jane@example.com"
      },
      "money": {
        "totalPaid": 1250.00,
        "balanceDue": 0.00,
        "currency": "USD"
      },
      "source": "airbnb",
      "note": "Late check-in requested"
    }
  ],
  "count": 42,
  "limit": 100,
  "skip": 0
}
```

### Pagination Contract

- **Page size**: Always request `limit=100` (Guesty maximum)
- **Offset**: Start at `skip=0`, increment by `limit` per page
- **Sort**: Always `sort=_id` for deterministic ordering
- **Stop condition**: `len(results) < limit` — last page
- **Empty result**: `{"results": [], "count": 0, ...}`
- **Count field**: Informational only; trust `results` array
  length, not `count` metadata

### Filter Contract

Filters are passed as a JSON array in the `filters` query
parameter. Each filter is an object with `field`, `operator`,
and either `value` (for `$contains`, `$eq`) or `from`/`to`
(for `$between`).

| Filter | Operator | Purpose |
| ------ | -------- | ------- |
| `checkIn` | `$between` | Date range window |
| `status` | `$contains` | Actionable statuses only |

### Error Responses

| Status | Exception | Handling |
| ------ | --------- | -------- |
| 401 | `GuestyAuthError` | Token refresh + retry |
| 403 | `GuestyAuthError` | Insufficient permissions |
| 429 | `GuestyRateLimitError` | Exponential backoff |
| 5xx | `GuestyResponseError` | `UpdateFailed` in coordinator |

### Field Presence Contract

| Field | Always Present | Default If Missing |
| ----- | -------------- | ------------------ |
| `_id` | Expected | Skip reservation |
| `listingId` | Expected | Skip reservation |
| `status` | Expected | Skip reservation |
| `checkIn` | Expected | Skip reservation |
| `checkOut` | Expected | Skip reservation |
| `confirmationCode` | No | `None` |
| `checkInDateLocalized` | No | `None` |
| `checkOutDateLocalized` | No | `None` |
| `plannedArrival` | No | `None` |
| `plannedDeparture` | No | `None` |
| `nightsCount` | No | `None` |
| `guestsCount` | No | `None` |
| `guest` | No | `None` (no guest info) |
| `guest.fullName` | No | `None` |
| `guest.phone` | No | `None` |
| `guest.email` | No | `None` |
| `money` | No | `None` (no financial data) |
| `money.totalPaid` | No | `None` |
| `money.balanceDue` | No | `None` |
| `money.currency` | No | `None` |
| `source` | No | `None` |
| `note` | No | `None` |

## Internal Python Contract: GuestyApiClient

### Method: get_reservations

```python
async def get_reservations(
    self,
    *,
    past_days: int = 30,
    future_days: int = 365,
    statuses: frozenset[str] | None = None,
) -> list[GuestyReservation]:
    """Fetch reservations with date and status filters.

    Makes two paginated requests to the Guesty reservations
    endpoint:
    1. Primary: checkIn date-range filter with status filter
       for the configurable window.
    2. Secondary: checked_in status only (no date filter)
       to capture long-stay active reservations whose
       checkIn predates the date window.

    Results are merged and de-duplicated by reservation ID.
    Reservations missing required fields (_id, listingId,
    status, checkIn, checkOut) are skipped with a warning.

    Args:
        past_days: Days in the past for the check-in
            date filter window. Default 30.
        future_days: Days in the future for the check-in
            date filter window. Default 365.
        statuses: Set of reservation statuses to include.
            Defaults to ACTIONABLE_STATUSES if None.

    Returns:
        De-duplicated list of valid GuestyReservation
        objects from both requests.

    Raises:
        GuestyAuthError: On authentication failure.
        GuestyConnectionError: On network failure.
        GuestyRateLimitError: On rate limit exhaustion.
        GuestyResponseError: On malformed API response.
    """
```

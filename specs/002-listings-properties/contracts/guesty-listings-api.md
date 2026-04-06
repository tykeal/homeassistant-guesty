<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# API Contract: Guesty Listings

**Feature**: 002-listings-properties
**Date**: 2025-07-18
**API Version**: Guesty Open API v1

## Endpoint: List All Listings

### Request

```http
GET /v1/listings
  ?limit=100
  &skip={offset}
  &fields=_id title nickname listed active address
          propertyType roomType numberOfBedrooms
          numberOfBathrooms timezone defaultCheckInTime
          defaultCheckOutTime tags customFields
```

### Response (200 OK)

```json
{
  "results": [
    {
      "_id": "507f1f77bcf86cd799439011",
      "title": "Beach House",
      "nickname": "Beach Alt Name",
      "listed": true,
      "active": true,
      "address": {
        "full": "123 Beach Rd, Miami, FL 33139, USA",
        "street": "123 Beach Rd",
        "city": "Miami",
        "state": "FL",
        "zipcode": "33139",
        "country": "USA"
      },
      "propertyType": "apartment",
      "roomType": "Entire home/apartment",
      "numberOfBedrooms": 2,
      "numberOfBathrooms": 1.5,
      "timezone": "America/New_York",
      "defaultCheckInTime": "15:00",
      "defaultCheckOutTime": "11:00",
      "tags": ["pet-friendly", "beachfront"],
      "customFields": {
        "maintenance_status": "good",
        "region": "southeast"
      }
    }
  ],
  "count": 42,
  "limit": 100,
  "skip": 0
}
```

### Pagination Contract

- **Page size**: Always request `limit=100` (Guesty maximum)
- **Offset**: Start at `skip=0`, increment by `limit` each page
- **Stop condition**: `len(results) < limit` — last page reached
- **Empty account**: Returns `{"results": [], "count": 0, ...}`
- **Count field**: Informational only; trust `results` array
  length, not `count` metadata

### Error Responses

| Status | Exception | Handling |
| ------ | --------- | -------- |
| 401 | `GuestyAuthError` | Token refresh + retry (existing) |
| 403 | `GuestyAuthError` | Insufficient permissions |
| 429 | `GuestyRateLimitError` | Exponential backoff (existing) |
| 5xx | `GuestyResponseError` | Raise `UpdateFailed` in coordinator |

### Field Presence Contract

| Field | Always Present | Default If Missing |
| ----- | -------------- | ------------------ |
| `_id` | Expected | Skip listing (log warning) |
| `title` | Usually | Fall back to `nickname`, then `"Unknown"` |
| `nickname` | No | `None` |
| `listed` | Usually | Assume `true` |
| `active` | Usually | Assume `true` |
| `address` | No | `None` |
| `propertyType` | No | `None` |
| `roomType` | No | `None` |
| `numberOfBedrooms` | No | `None` |
| `numberOfBathrooms` | No | `None` |
| `timezone` | Usually | `"UTC"` |
| `defaultCheckInTime` | No | `None` |
| `defaultCheckOutTime` | No | `None` |
| `tags` | No | `[]` (empty list) |
| `customFields` | No | `{}` (empty dict) |

## Internal Python Contract: GuestyApiClient

### Method: get_listings

```python
async def get_listings(self) -> list[GuestyListing]:
    """Fetch all listings with automatic pagination.

    Iterates through all pages of the Guesty listings endpoint,
    requesting 100 listings per page. Listings missing a valid
    ``_id`` field are skipped with a warning log.

    Returns:
        Complete list of valid GuestyListing objects.

    Raises:
        GuestyAuthError: On authentication failure.
        GuestyConnectionError: On network failure.
        GuestyRateLimitError: On rate limit exhaustion.
        GuestyResponseError: On malformed API response.
    """
```

### Method: get_listing (future — not in Feature 002)

Reserved for future single-listing fetch with child listings.
Not implemented in this feature.

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# API Contract: Guesty Custom Fields

**Feature**: 004-custom-variables
**Date**: 2025-07-27
**API Version**: Guesty Open API v1

## Endpoints

### GET /v1/custom-fields

Fetch all account-level custom field definitions.

**Authentication**: Bearer token (OAuth 2.0)

**Request**:

```http
GET /v1/custom-fields HTTP/1.1
Host: open-api.guesty.com
Authorization: Bearer {token}
```

**Response** (200 OK):

```json
[
  {
    "id": "637bad36abcdef123456",
    "name": "Door Code",
    "type": "string",
    "objectType": "reservation"
  },
  {
    "id": "637bad36abcdef789012",
    "name": "Maintenance Alert",
    "type": "boolean",
    "objectType": "listing"
  }
]
```

**Response fields**:

| Field | Type | Description |
| ----- | ---- | ----------- |
| `id` | string | Unique field identifier |
| `name` | string | Human-readable display name |
| `type` | string | Value type (string, number, boolean) |
| `objectType` | string | Target: listing, reservation |

**Error responses**:

- 401 Unauthorized: Invalid or expired token
- 429 Too Many Requests: Rate limit exceeded
- 500 Internal Server Error: Guesty server error

**Integration mapping**:

- `id` → `GuestyCustomFieldDefinition.field_id`
- `name` → `GuestyCustomFieldDefinition.name`
- `type` → `GuestyCustomFieldDefinition.field_type`
  (mapped: "string" → "text")
- `objectType` → `GuestyCustomFieldDefinition.applicable_to`

---

### PUT /v1/listings/{listingId}/custom-fields

Update custom field values on a listing.

**Authentication**: Bearer token (OAuth 2.0)

**Request**:

```http
PUT /v1/listings/{listingId}/custom-fields HTTP/1.1
Host: open-api.guesty.com
Authorization: Bearer {token}
Content-Type: application/json

[
  {
    "fieldId": "637bad36abcdef789012",
    "value": true
  }
]
```

**Request body**: Array of field update objects.

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `fieldId` | string | ✅ | Custom field identifier |
| `value` | any | ✅ | New value (string, number, bool) |

**Response** (200 OK):

```json
[
  {
    "fieldId": "637bad36abcdef789012",
    "value": true
  }
]
```

**Error responses**:

- 400 Bad Request: Invalid field ID or value type mismatch
- 401 Unauthorized: Invalid or expired token
- 404 Not Found: Listing not found
- 429 Too Many Requests: Rate limit exceeded
- 500 Internal Server Error: Guesty server error

**Integration mapping**:

- Request: `GuestyCustomFieldUpdate` → JSON body
- Response: Confirm field in response array →
  `GuestyCustomFieldResult(success=True)`
- 400/404: → `GuestyCustomFieldError` with context

---

### PUT /v1/reservations-v3/{reservationId}/custom-fields

Update custom field values on a reservation using the v3
endpoint (per Guesty migration timeline).

**Authentication**: Bearer token (OAuth 2.0)

**Request**:

```http
PUT /v1/reservations-v3/{reservationId}/custom-fields HTTP/1.1
Host: open-api.guesty.com
Authorization: Bearer {token}
Content-Type: application/json

[
  {
    "fieldId": "637bad36abcdef123456",
    "value": "ABC-1234"
  }
]
```

**Response** (200 OK) — v3 envelope format:

```json
{
  "reservationId": "673e3b1fabcdef456789",
  "customFields": [
    {
      "_id": "68f9fa36abcdef111222",
      "fieldId": "637bad36abcdef123456",
      "value": "ABC-1234"
    }
  ]
}
```

**Response fields**:

| Field | Type | Description |
| ----- | ---- | ----------- |
| `reservationId` | string | Reservation identifier |
| `customFields` | array | Updated field objects |
| `customFields[].fieldId` | string | Field identifier |
| `customFields[].value` | any | Updated value |
| `customFields[]._id` | string | Internal object ID |

**Error responses**:

- 400 Bad Request: Invalid field ID or value type mismatch
- 401 Unauthorized: Invalid or expired token
- 404 Not Found: Reservation not found
- 429 Too Many Requests: Rate limit exceeded
- 500 Internal Server Error: Guesty server error

**Integration mapping**:

- Request: Same format as listing endpoint
- Response: Check `customFields` array for matching `fieldId`
  → `GuestyCustomFieldResult(success=True)`
- v3 difference: Response wrapped in envelope with
  `reservationId` and `customFields` keys

---

## HA Service Contract

### `guesty.set_custom_field`

**Registration**: `supports_response=SupportsResponse.OPTIONAL`

**Input schema** (voluptuous):

| Parameter | Type | Required | Constraint |
| --------- | ---- | -------- | ---------- |
| `target_type` | string | ✅ | "listing" or "reservation" |
| `target_id` | string | ✅ | Non-empty Guesty ID |
| `field_id` | string | ✅ | Non-empty field identifier |
| `value` | any | ✅ | str, int, float, or bool |

**Success response**:

```json
{
  "target_type": "listing",
  "target_id": "abc123",
  "field_id": "custom_field_1",
  "result": "success"
}
```

**Error behavior**: Raises `HomeAssistantError` with
actionable message. Error message includes target type,
target ID, and field ID for debugging context.

**Rate limits**: Inherits from Feature 001 API client.
Guesty limits: 15 requests/second, 120 requests/minute,
5000 requests/hour.

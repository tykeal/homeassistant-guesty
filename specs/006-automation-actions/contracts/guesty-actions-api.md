<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# API Contract: Guesty Actions

**Feature**: 006 — Automation Actions
**Date**: 2025-07-26
**API Version**: Guesty Open API v1

## Endpoints

### 1. Update Reservation (Notes / Custom Fields)

```text
PUT /v1/reservations/{reservationId}
Authorization: Bearer {token}
Content-Type: application/json
```

**Add note** (appends to existing note text):

```json
{
  "note": "{existing_note}\n---\n{new_note_text}"
}
```

**Update custom field**:

```json
{
  "customFields": {
    "{custom_field_id}": "{value}"
  }
}
```

**Success response** (200):

```json
{
  "_id": "reservation-id",
  "note": "combined note text",
  "customFields": { ... }
}
```

**Error responses**:

- `400` — Validation error (invalid field values)
- `401` — Authentication expired (triggers token refresh)
- `403` — Insufficient permissions
- `404` — Reservation not found
- `429` — Rate limited (triggers backoff/retry)

### 2. Update Listing Status

```text
PUT /v1/listings/{listingId}
Authorization: Bearer {token}
Content-Type: application/json
```

**Activate listing**:

```json
{
  "active": true,
  "listed": true
}
```

**Deactivate listing**:

```json
{
  "active": false
}
```

**Success response** (200):

```json
{
  "_id": "listing-id",
  "active": true,
  "listed": true
}
```

**Error responses**:

- `400` — Validation error
- `401` — Authentication expired
- `403` — Insufficient permissions
- `404` — Listing not found
- `429` — Rate limited

### 3. Create Task

```text
POST /v1/tasks-open-api/tasks
Authorization: Bearer {token}
Content-Type: application/json
```

**Request body**:

```json
{
  "listingId": "listing-id",
  "title": "Cleaning after checkout",
  "description": "Full cleaning required",
  "assigneeId": "user-id-or-null"
}
```

**Success response** (201):

```json
{
  "_id": "new-task-id",
  "listingId": "listing-id",
  "title": "Cleaning after checkout"
}
```

**Error responses**:

- `400` — Validation error (missing title, invalid listing)
- `401` — Authentication expired
- `403` — Insufficient permissions
- `404` — Listing not found
- `429` — Rate limited

### 4. Update Calendar Availability

```text
PUT /v1/availability-pricing/api/calendar/listings/{listingId}
Authorization: Bearer {token}
Content-Type: application/json
```

**Block dates**:

```json
{
  "dateFrom": "2025-08-01",
  "dateTo": "2025-08-05",
  "status": "unavailable",
  "note": "Owner stay"
}
```

**Unblock dates**:

```json
{
  "dateFrom": "2025-08-01",
  "dateTo": "2025-08-05",
  "status": "available"
}
```

**Success response** (200):

```json
{
  "data": {
    "days": [ ... ]
  }
}
```

**Error responses**:

- `400` — Validation error (invalid dates, conflict)
- `401` — Authentication expired
- `403` — Insufficient permissions
- `404` — Listing not found
- `409` — Conflict with existing reservation
- `429` — Rate limited

## Common Error Handling

All endpoints share the following error handling patterns
inherited from the existing API client (Feature 001):

| HTTP Status | Behavior |
| ----------- | -------- |
| 401 | Invalidate token, refresh, retry once |
| 403 | Raise `GuestyAuthError` |
| 404 | Raise `GuestyActionError` (not found) |
| 429 | Exponential backoff with jitter (3 retries) |
| 5xx | Raise `GuestyConnectionError` |
| Network err | Raise `GuestyConnectionError` |

## Rate Limits

Per Guesty documentation:

- 15 requests per second
- 120 requests per minute
- 5000 requests per hour

The existing `GuestyApiClient._request()` method handles
429 responses with exponential backoff (1s → 2s → 4s, max
30s, ±25% jitter, max 3 retries). Write operations reuse
this infrastructure.

## Data Validation (Client-Side)

All input is validated before making API requests:

| Field | Validation |
| ----- | ---------- |
| reservation_id | Non-empty string |
| listing_id | Non-empty string |
| note_text | 1–5000 characters |
| status | "active" or "inactive" |
| task_title | 1–255 characters |
| description | 1–5000 characters (optional) |
| assignee | Non-empty when provided |
| start_date | Valid YYYY-MM-DD |
| end_date | Valid YYYY-MM-DD, >= start_date |
| operation | "block" or "unblock" |
| custom_field_id | Non-empty string |
| value | 1–5000 characters |

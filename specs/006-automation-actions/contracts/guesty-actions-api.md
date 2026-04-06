<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# API Contract: Guesty Actions

**Feature**: 006 — Automation Actions
**Date**: 2025-07-26
**API Version**: Guesty Open API v1

**Base URL**: `https://open-api.guesty.com/v1`

All endpoint paths below are relative to the base URL.

## Endpoints

### 1. Update Reservation (Notes / Custom Fields)

```text
PUT /reservations/{reservation_id}
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
PUT /listings/{listing_id}
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
POST /tasks-open-api/tasks
Authorization: Bearer {token}
Content-Type: application/json
```

**Request body**:

```json
{
  "listingId": "listing-id",
  "title": "Cleaning after checkout",
  "description": "Full cleaning required",
  "assigneeId": null
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
PUT /availability-pricing/api/calendar/listings/{listing_id}
Authorization: Bearer {token}
Content-Type: application/json
```

**Block dates**:

```json
{
  "dateFrom": "2025-08-01",
  "dateTo": "2025-08-05",
  "status": "unavailable"
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

All endpoints share the following error handling patterns.
The base `GuestyApiClient._request()` (Feature 001) handles
401 (token refresh + retry), 403 (`GuestyAuthError`), 429
(exponential backoff), and network errors
(`GuestyConnectionError`). The actions client layer adds
handling for responses the base client returns to callers:

| HTTP Status | Handled by | Behavior |
| ----------- | ---------- | -------- |
| 401 | Base client | Invalidate token, refresh, retry once |
| 403 | Base client | Raise `GuestyAuthError` |
| 404 | Actions client | Raise `GuestyActionError` (not found) |
| 429 | Base client | Exponential backoff with jitter (3 retries) |
| 5xx | Actions client | Raise `GuestyConnectionError` |
| Network err | Base client | Raise `GuestyConnectionError` |

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

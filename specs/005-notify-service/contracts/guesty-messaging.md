<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# API Contract: Guesty Messaging

**Feature**: 005-notify-service | **Date**: 2025-07-24

This contract documents the Guesty Open API endpoints used for guest
messaging. All endpoints require OAuth 2.0 Bearer token authentication
as established in the Feature 001 OAuth contract.

## 1. Get Conversations (Resolve Reservation → Conversation)

### Conversations Request

```http
GET /v1/communication/conversations?filters=[{"field":"reservation._id","operator":"$eq","value":"{reservation_id}"}]
Authorization: Bearer {access_token}
Accept: application/json
```

**Query parameters**:

| Parameter | Type | Required | Description |
| --------- | ---- | -------- | ----------- |
| `filters` | JSON array | Yes | Filter by reservation ID |
| `fields` | string | No | Comma-separated field list |

**Filter structure**:

```json
[
  {
    "field": "reservation._id",
    "operator": "$eq",
    "value": "{reservation_id}"
  }
]
```

### Conversations Success (200 OK)

```json
{
  "results": [
    {
      "_id": "conv-abc123",
      "reservation": {
        "_id": "res-xyz789"
      },
      "module": {
        "type": "platform"
      },
      "availableModules": [
        {"type": "email"},
        {"type": "sms"},
        {"type": "platform"}
      ],
      "guest": {
        "_id": "guest-456"
      },
      "createdAt": "2025-07-01T10:00:00.000Z"
    }
  ],
  "count": 1,
  "limit": 25,
  "skip": 0
}
```

**Key response fields**:

| Field | Type | Description |
| ----- | ---- | ----------- |
| `results` | array | Matching conversations |
| `results[]._id` | string | Conversation identifier |
| `results[].reservation._id` | string | Associated reservation |
| `results[].module.type` | string | Default channel type |
| `results[].availableModules` | array | Channels available |
| `count` | integer | Total matching conversations |

### Conversations Errors

| Status | Meaning | Integration Handling |
| ------ | ------- | -------------------- |
| 401 | Token expired/invalid | Reactive refresh via GuestyApiClient |
| 403 | Insufficient scope | Raise `GuestyAuthError` |
| 429 | Rate limited | Retry with backoff via GuestyApiClient |
| 5xx | Server error | Retry with backoff, then `GuestyConnectionError` |

### Empty Result

When `results` is empty (count = 0), the reservation ID does not
have an associated conversation. Raise `GuestyMessageError` with
the reservation ID and a message indicating no conversation was
found.

## 2. Send Message

### Send Message Request

```http
POST /v1/communication/conversations/{conversationId}/send-message
Authorization: Bearer {access_token}
Content-Type: application/json
Accept: application/json
```

**Path parameters**:

| Parameter | Type | Required | Description |
| --------- | ---- | -------- | ----------- |
| `conversationId` | string | Yes | Target conversation ID |

**Request body**:

```json
{
  "body": "Your check-in code is 1234. Welcome!",
  "module": {
    "type": "email"
  }
}
```

**Body fields**:

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `body` | string | Yes | Message text content |
| `module` | object | No | Channel specification |
| `module.type` | string | Yes (if module) | Channel type identifier |

**Known module types**:

| Type | Description | Notes |
| ---- | ----------- | ----- |
| `email` | Email delivery | Uses guest or proxy email |
| `sms` | SMS delivery | Requires guest phone number |
| `airbnb2` | Airbnb messaging | Not supported for owner conversations |
| `platform` | Booking platform native | Routes via OTA (Booking.com, Vrbo) |
| `whatsapp` | WhatsApp messaging | Requires guest opt-in |

When `module` is omitted, the message is sent through the
conversation's default channel.

### Send Message Success (200 OK)

```json
{
  "_id": "msg-def456",
  "conversationId": "conv-abc123",
  "body": "Your check-in code is 1234. Welcome!",
  "module": {
    "type": "email"
  },
  "createdAt": "2025-07-24T14:30:00.000Z"
}
```

**Key response fields**:

| Field | Type | Description |
| ----- | ---- | ----------- |
| `_id` | string | Message identifier |
| `conversationId` | string | Conversation the message was sent to |
| `body` | string | Message content as sent |
| `module.type` | string | Channel used for delivery |
| `createdAt` | string | ISO 8601 timestamp |

### Send Message Errors

| Status | Meaning | Integration Handling |
| ------ | ------- | -------------------- |
| 400 | Invalid request body | Raise `GuestyMessageError` |
| 401 | Token expired/invalid | Reactive refresh via GuestyApiClient |
| 403 | Insufficient scope | Raise `GuestyAuthError` |
| 404 | Conversation not found | Raise `GuestyMessageError` |
| 429 | Rate limited | Retry with backoff via GuestyApiClient |
| 5xx | Server error | Retry with backoff, then `GuestyConnectionError` |

## 3. API Rate Limits

The messaging endpoints share the same rate limits as all Guesty
Open API endpoints (documented in the Feature 001 OAuth contract):

| Limit | Value |
| ----- | ----- |
| Per second | 15 requests |
| Per minute | 120 requests |
| Per hour | 5,000 requests |

Rate limit responses include:

- HTTP status 429
- Optional `Retry-After` header (seconds to wait)

The existing `GuestyApiClient` handles all rate limit responses with
exponential backoff and jitter. The messaging client does not
implement separate rate limit logic.

## 4. Integration Data Flow

```text
1. User provides reservation_id in service call
2. GET /v1/communication/conversations?filters=[reservation._id=$eq]
   → returns conversation with _id and availableModules
3. If channel specified: validate against availableModules
   If not specified: use conversation's default module
4. POST /v1/communication/conversations/{_id}/send-message
   → returns message confirmation with _id
5. Return MessageDeliveryResult to caller
```

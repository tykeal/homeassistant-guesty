<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Guesty OAuth 2.0 Token Endpoint

**Feature**: 001-auth-config-flow
**Date**: 2025-07-18
**Guesty API Version**: Open API v1

## Token Acquisition

### Request

```http
POST https://open-api.guesty.com/oauth2/token
Content-Type: application/x-www-form-urlencoded
Accept: application/json
```

**Body parameters** (form-encoded):

| Parameter | Type | Required | Description |
| --------- | ---- | -------- | ----------- |
| `grant_type` | string | Yes | `client_credentials` |
| `scope` | string | Yes | `open-api` |
| `client_id` | string | Yes | App client ID |
| `client_secret` | string | Yes | App client secret |

### Success Response (200 OK)

```json
{
  "token_type": "Bearer",
  "access_token": "<jwt-access-token>",
  "expires_in": 86400,
  "scope": "open-api"
}
```

| Field | Type | Description |
| ----- | ---- | ----------- |
| `token_type` | string | Always `"Bearer"` |
| `access_token` | string | JWT access token |
| `expires_in` | integer | Lifetime in seconds (86400) |
| `scope` | string | Granted scope (`"open-api"`) |

### Error Responses

**401 Unauthorized** — Invalid credentials:

```json
{
  "error": "invalid_client",
  "error_description": "Client authentication failed"
}
```

**429 Too Many Requests** — Token rate limit exceeded:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 3600
```

### Rate Limits

| Limit | Value | Window |
| ----- | ----- | ------ |
| Token requests | 5 | Per 24-hour rolling window per `client_id` |

Exceeding 5 token requests in a 24-hour window may result in
temporary account lockout.

---

## Authenticated API Requests

### Request Format

```http
GET https://open-api.guesty.com/v1/{endpoint}
Authorization: Bearer <access_token>
Accept: application/json
```

### API Rate Limits

| Tier | Limit | Window |
| ---- | ----- | ------ |
| Per-second | 15 requests | 1 second |
| Per-minute | 120 requests | 1 minute |
| Per-hour | 5000 requests | 1 hour |

### Rate Limit Response (429)

```http
HTTP/1.1 429 Too Many Requests
Retry-After: <seconds>
```

The `Retry-After` header indicates how many seconds to wait before
retrying. If absent, use exponential backoff.

### Common Error Responses

**401 Unauthorized** — Token expired or revoked:

```json
{
  "error": "Unauthorized",
  "message": "Invalid or expired token"
}
```

**403 Forbidden** — Insufficient scope:

```json
{
  "error": "Forbidden",
  "message": "Insufficient permissions"
}
```

---

## Connection Test

To validate credentials during config flow, acquire a token and
optionally call a lightweight endpoint:

```http
GET https://open-api.guesty.com/v1/listings?limit=1&fields=_id
Authorization: Bearer <access_token>
```

A successful response (200 with JSON body) confirms valid credentials
and API access. An empty results array is acceptable (new account
with no listings).

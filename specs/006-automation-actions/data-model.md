<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Automation Actions

**Feature**: 006 — Automation Actions
**Date**: 2025-07-26

## Entities

### ActionResult

Immutable outcome of a write operation returned to callers.

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| success | bool | Yes | Whether the action succeeded |
| target_id | str | Yes | Guesty resource identifier |
| error | str \| None | No | Human-readable error detail |

**Validation rules**:

- `target_id` must be a non-empty string
- When `success` is `False`, `error` must be non-empty
- When `success` is `True`, `error` must be `None`

**Pattern**: Frozen dataclass with `__post_init__` validation.
Follows
`MessageDeliveryResult` pattern from Feature 005.

### GuestyActionError

Exception for action-specific failures.

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| message | str | Yes | Error description |
| target_id | str \| None | No | Targeted resource ID |
| action_type | str \| None | No | Action that failed |

**Pattern**: Inherits `GuestyApiError`. Follows
`GuestyMessageError` pattern (adds contextual fields for
debugging).

## Service Definitions

### guesty.add\_reservation\_note

| Parameter | Type | Required | Validation |
| --------- | ---- | -------- | ---------- |
| reservation_id | str | Yes | Non-empty |
| note_text | str | Yes | 1–5000 chars |

**Returns**: `ActionResult` with `target_id` =
`reservation_id`.

### guesty.set\_listing\_status

| Parameter | Type | Required | Validation |
| --------- | ---- | -------- | ---------- |
| listing_id | str | Yes | Non-empty |
| status | str | Yes | "active" or "inactive" |

**Returns**: `ActionResult` with `target_id` =
`listing_id`.

### guesty.create\_task

| Parameter | Type | Required | Validation |
| --------- | ---- | -------- | ---------- |
| listing_id | str | Yes | Non-empty |
| task_title | str | Yes | 1–255 chars |
| description | str | No | 1–5000 chars |
| assignee | str | No | Non-empty when provided |

**Returns**: `ActionResult` with `target_id` =
`listing_id`.

### guesty.set\_calendar\_availability

| Parameter | Type | Required | Validation |
| --------- | ---- | -------- | ---------- |
| listing_id | str | Yes | Non-empty |
| start_date | str | Yes | YYYY-MM-DD format |
| end_date | str | Yes | YYYY-MM-DD format |
| operation | str | Yes | "block" or "unblock" |

**Validation**: `end_date` >= `start_date`.

**Returns**: `ActionResult` with `target_id` =
`listing_id`.

### guesty.update\_reservation\_custom\_field

| Parameter | Type | Required | Validation |
| --------- | ---- | -------- | ---------- |
| reservation_id | str | Yes | Non-empty |
| custom_field_id | str | Yes | Non-empty |
| value | str | Yes | 1–5000 chars |

**Returns**: `ActionResult` with `target_id` =
`reservation_id`.

## Relationships

```text
ActionResult ← returned by → GuestyActionsClient methods
GuestyActionsClient → delegates to → GuestyApiClient
GuestyActionError → raised by → GuestyActionsClient
HA service handlers → call → GuestyActionsClient
HA service handlers → translate → GuestyActionError
  → HomeAssistantError
```

## State Transitions

Actions are stateless write operations. No state machine
applies. Each service call is an independent request-response
cycle:

1. Service call received → validate parameters
2. Parameters valid → call GuestyActionsClient method
3. API success → return ActionResult(success=True)
4. API failure → raise GuestyActionError → translate to
   HomeAssistantError

Local sensor state updates on the next coordinator polling
cycle (eventual consistency).

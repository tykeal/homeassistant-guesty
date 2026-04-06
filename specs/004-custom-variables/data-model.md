<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Custom Variables

**Feature**: 004-custom-variables
**Date**: 2025-07-27

## Entities

### GuestyCustomFieldDefinition

Account-level definition of a custom field managed in Guesty.
Fetched periodically and cached by the
`CustomFieldsDefinitionCoordinator`.

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `field_id` | `str` | ✅ | Guesty-assigned unique field ID |
| `name` | `str` | ✅ | Human-readable display name |
| `field_type` | `str` | ✅ | Value type: text, number, boolean |
| `applicable_to` | `frozenset[str]` | ✅ | Target types |

**Factory**: `from_api_dict(data: dict) ->
GuestyCustomFieldDefinition | None`

**Validation**:

- Returns `None` if `id` or `name` is missing from API response
- Logs warning for skipped definitions
- `applicable_to` derived from API `objectType` field; defaults
  to `frozenset()` if missing

**Relationships**:

- Referenced by service handler for field validation
- Cached by `CustomFieldsDefinitionCoordinator`
- Filtered by target type for discovery (FR-010)

### GuestyCustomFieldUpdate

Represents a single custom field write request. Created by the
service handler and passed to `GuestyCustomFieldsClient`.

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `field_id` | `str` | ✅ | Target field identifier |
| `value` | `str \| int \| float \| bool` | ✅ | New value |

**Validation**:

- `field_id` must be non-empty
- `value` must be one of the supported types

**Serialization**: Converts to API format
`{"fieldId": field_id, "value": value}`

### GuestyCustomFieldResult

Represents the outcome of a custom field write operation.
Returned by `GuestyCustomFieldsClient.set_field()`.

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `success` | `bool` | ✅ | Whether update succeeded |
| `target_type` | `str` | ✅ | "listing" or "reservation" |
| `target_id` | `str` | ✅ | Guesty entity identifier |
| `field_id` | `str` | ✅ | Updated field identifier |
| `error_details` | `str \| None` | ❌ | Error message |

**Usage**:

- Returned from `GuestyCustomFieldsClient.set_field()`
- Converted to service response dict by HA service handler
- On failure, `error_details` contains actionable message

## Exception Model

### GuestyCustomFieldError

Custom-field-specific exception extending `GuestyApiError`.

| Attribute | Type | Description |
| --------- | ---- | ----------- |
| `message` | `str` | Error description |
| `target_type` | `str \| None` | Listing or reservation |
| `target_id` | `str \| None` | Guesty entity identifier |
| `field_id` | `str \| None` | Field identifier |

**Raised by**: `GuestyCustomFieldsClient` on API errors,
validation failures, or unexpected responses.

## Coordinator Model

### CustomFieldsDefinitionCoordinator

Extends `DataUpdateCoordinator[list[
GuestyCustomFieldDefinition]]`.

| Property | Type | Description |
| -------- | ---- | ----------- |
| `data` | `list[GuestyCustomFieldDefinition]` | Cached defs |

**Methods**:

- `_async_update_data()` — Calls
  `GuestyCustomFieldsClient.get_definitions()`
- `get_field(field_id: str) ->
  GuestyCustomFieldDefinition | None` — Lookup by ID
- `get_fields_for_target(target_type: str) ->
  list[GuestyCustomFieldDefinition]` — Filter by
  applicability

**Refresh interval**: Configurable, default 15 minutes,
minimum 5 minutes (matches existing coordinator pattern).

## Service Schema

### `guesty.set_custom_field`

| Parameter | Type | Required | Description |
| --------- | ---- | -------- | ----------- |
| `target_type` | `str` | ✅ | "listing" or "reservation" |
| `target_id` | `str` | ✅ | Guesty entity identifier |
| `field_id` | `str` | ✅ | Custom field identifier |
| `value` | `str\|int\|float\|bool` | ✅ | New field value |

**Response** (when `return_response=true`):

```json
{
  "target_type": "listing",
  "target_id": "abc123",
  "field_id": "custom_field_1",
  "result": "success"
}
```

**Errors**: Raises `HomeAssistantError` with actionable message
for validation failures, API errors, and unknown fields.

## State Transitions

Custom field definitions have no state machine. They are
fetched, cached, and replaced on each coordinator refresh.

Custom field writes are stateless one-shot operations:
service call → validate → API write → return result.

## Constants

### API Constants (`api/const.py`)

<!-- markdownlint-disable MD013 -->

| Constant | Value | Purpose |
| -------- | ----- | ------- |
| `CUSTOM_FIELDS_ENDPOINT` | `/custom-fields` | Defs |
| `LISTING_CUSTOM_FIELDS_PATH` | `/listings/{listing_id}/custom-fields` | Write |
| `RESERVATION_CUSTOM_FIELDS_PATH` | `/reservations-v3/{reservation_id}/custom-fields` | Write |
| `CUSTOM_FIELD_TYPES` | `frozenset({"text","number","boolean"})` | Valid |
| `CUSTOM_FIELD_TARGETS` | `frozenset({"listing","reservation"})` | Valid |

<!-- markdownlint-enable MD013 -->

### HA Constants (`const.py`)

| Constant | Value | Purpose |
| -------- | ----- | ------- |
| `SERVICE_SET_CUSTOM_FIELD` | `"set_custom_field"` | Svc name |
| `CONF_CF_SCAN_INTERVAL` | `"cf_scan_interval"` | Options |
| `DEFAULT_CF_SCAN_INTERVAL` | `15` | Minutes |

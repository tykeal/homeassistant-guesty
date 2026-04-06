<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Custom Variables

**Feature**: 004-custom-variables
**Date**: 2025-07-27

## R-001: Guesty Custom Fields API Endpoints

**Decision**: Use three Guesty Open API v1 endpoints for custom
field operations.

**Rationale**: Guesty exposes account-level custom field
definitions at `/custom-fields`, listing-level custom field
writes at `/listings/{id}/custom-fields`, and reservation-level
custom field writes at `/reservations-v3/{id}/custom-fields`.
The v3 reservation endpoint is required per Guesty's API
migration timeline; the older `/reservations/{id}/custom-fields`
path is deprecated.

**Endpoints**:

- `GET /v1/custom-fields` — Fetch all account-level custom
  field definitions. Returns an array of objects containing
  `id` (field identifier), `name` (display name), `type`
  (Guesty API value type: string, number, boolean; the
  integration maps `string` to the internal `text` type),
  and applicability metadata indicating whether the field
  applies to listings, reservations, or both.
- `PUT /v1/listings/{listingId}/custom-fields` — Write custom
  field values on a listing. Request body is an array of
  `{"fieldId": "...", "value": ...}` objects. Supports partial
  updates; only specified fields are changed.
- `PUT /v1/reservations-v3/{reservationId}/custom-fields` —
  Write custom field values on a reservation using the v3
  endpoint. Same request body format as listings. Response wraps
  results in a `{"reservationId": "...", "customFields": [...]}`
  envelope.

**Alternatives considered**:

- Using the deprecated `/reservations/{id}/custom-fields` (v2)
  path: rejected because Guesty's migration timeline deprecates
  this path. The spec explicitly requires the current version
  (FR-018).
- Batch updates (multiple fields per call): Guesty supports
  this but the spec scopes to single-field updates per service
  call. The API body format (`[{...}]`) naturally supports
  future batch extension.

## R-002: Custom Field Value Types

**Decision**: Support three value types: text (string), number,
and boolean. Pass unknown or multi-type values through to
Guesty for server-side validation.

**Rationale**: The spec identifies three types (FR-005): text,
number, and boolean. The Guesty API accepts these as JSON
values. Local validation catches obvious type mismatches early,
providing fast feedback (SC-005). However, Guesty may define
fields that accept multiple types or types not yet known to the
integration. The spec edge case explicitly states the service
should pass values through and surface Guesty rejections.

**Validation rules**:

- `text`: Value must be a string
- `number`: Value must have exact type `int` or `float`
  (reject `bool` before numeric check since Python's `bool`
  is a subclass of `int`; use `type(value) in (int, float)`)
- `boolean`: Value must be bool
- Unknown type: Skip local validation, pass to Guesty

**Alternatives considered**:

- Strict local validation only (reject unknown types):
  rejected because it would break forward compatibility with
  new Guesty field types.
- No local validation (always delegate to Guesty): rejected
  because it adds unnecessary round-trips for obvious errors
  and violates SC-005 (<2 second error response).

## R-003: Service Registration Pattern

**Decision**: Register `guesty.set_custom_field` as a
response-capable HA service using `hass.services.async_register`
with `supports_response=SupportsResponse.OPTIONAL`.

**Rationale**: Home Assistant supports response-capable services
that return structured data to callers. The spec requires
structured success responses (FR-006a) and error raising on
failure (FR-006b). Using `supports_response=SupportsResponse.OPTIONAL`
allows automations to optionally receive the response while
maintaining backward compatibility with fire-and-forget calls.

**Service schema (voluptuous)**:

```python
vol.Schema({
    vol.Required("target_type"): vol.In({"listing",
                                          "reservation"}),
    vol.Required("target_id"): vol.All(cv.string,
                                        vol.Length(min=1)),
    vol.Required("field_id"): vol.All(cv.string,
                                       vol.Length(min=1)),
    vol.Required("value"): vol.Any(str, int, float, bool),
})
```

**Response format** (on success):

```python
{
    "target_type": "listing",
    "target_id": "abc123",
    "field_id": "custom_field_1",
    "result": "success",
}
```

**Alternatives considered**:

- Entity-based service (like notify): rejected because custom
  field writes are not tied to a specific entity; they target
  arbitrary listings or reservations by ID.
- `supports_response=SupportsResponse.ONLY`: rejected because
  it would break fire-and-forget automation patterns.

## R-004: Custom Field Definition Caching

**Decision**: Use a `DataUpdateCoordinator` to periodically
fetch and cache account-level custom field definitions.

**Rationale**: Custom field definitions change infrequently
(managed in the Guesty dashboard). Periodic polling matches the
existing coordinator pattern used for listings (Feature 002).
The definition cache enables local field validation (FR-005,
FR-008), discovery (FR-010), and target-type filtering.
Refresh interval defaults to match the listing coordinator
(15 minutes, configurable).

**Alternatives considered**:

- Fetch definitions on every service call: rejected because it
  adds latency to every write operation and increases API call
  volume unnecessarily.
- Manual refresh only: rejected because the spec requires
  definitions to reflect changes within two refresh cycles
  (SC-006).
- Event-driven refresh (webhooks): rejected because the current
  integration does not use webhooks and adding webhook support
  is out of scope for this feature.

## R-005: Library Extractability

**Decision**: Place the custom fields client in
`api/custom_fields.py` as a new module with zero HA imports.

**Rationale**: The spec requires the custom fields client to be
reusable outside Home Assistant (FR-016). Following the existing
pattern established by `api/messaging.py`, the client receives
`GuestyApiClient` via dependency injection and operates purely
on the API layer. All HA-specific logic (coordinator, service
registration, error mapping) lives in the HA-side modules.

**Alternatives considered**:

- Adding custom field methods directly to `GuestyApiClient`:
  rejected because it would bloat the client class and mix
  concerns. The messaging pattern (separate orchestrator
  module) provides better separation.
- Placing all logic in HA-side `__init__.py`: rejected because
  it violates the library-extractable architecture requirement.

## R-006: Reservation Endpoint Version

**Decision**: Use `/reservations-v3/{id}/custom-fields` for all
reservation custom field operations.

**Rationale**: Guesty has migrated from v2 to v3 reservation
endpoints. The v3 response wraps results in an envelope with
`reservationId` and `customFields` array, each entry including
a unique `_id`. The spec explicitly requires the current version
(FR-018, SC-010). The v3 endpoint is backward-compatible with
reservations created under v2.

**Response parsing**:

- v3 PUT response:
  `{"reservationId": "...", "customFields": [{"_id": "...",
  "fieldId": "...", "value": ...}]}`
- Success is confirmed by presence of the field in the
  response `customFields` array
- Listing PUT response: simple array of updated fields

**Alternatives considered**:

- Using v2 reservation endpoint: rejected per FR-018 and
  Guesty's deprecation timeline.

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Automation Actions

**Feature**: 006 — Automation Actions
**Date**: 2025-07-26

## Research Tasks

### R1: Guesty Write Endpoint Patterns

**Decision**: Use the following Guesty Open API v1 endpoints for
each action:

| Action | Method | Endpoint |
| ------ | ------ | -------- |
| Add reservation note | PUT | `/reservations/{reservation_id}` |
| Set listing status | PUT | `/listings/{listing_id}` |
| Create task | POST | `/tasks-open-api/tasks` |
| Calendar availability | PUT | (see below) |
| Update custom field | PUT | `/reservations/{reservation_id}` |

**Calendar endpoint**: The Guesty availability/pricing calendar
API uses
`PUT /availability-pricing/api/calendar/listings/{listing_id}`
with a JSON body containing a date range, using `dateFrom` and
`dateTo` together with the availability status
(available/unavailable) to apply across that range.

**Rationale**: These endpoints are documented in the Guesty
Open API v1 reference and Postman collection. The reservation
update endpoint (`PUT /reservations/{reservation_id}`) accepts
partial
updates, allowing notes and custom fields to be set without
overwriting other reservation data. The listings endpoint
similarly supports partial updates for status changes.

**Alternatives considered**:

- PATCH endpoints: Guesty uses PUT for updates rather than
  PATCH. The PUT endpoints accept partial payloads.
- Separate notes endpoint: No dedicated notes endpoint
  exists; notes are a field on the reservation resource.
- Bulk calendar endpoint (`PUT /calendar`): Exists for
  multi-listing updates but is unnecessarily complex for
  single-listing operations.

### R2: Reservation Note Append Strategy

**Decision**: Use `PUT /reservations/{reservation_id}` with the
`note`
field in the request body. Guesty treats the `note` field as a
single text block, so appending requires reading the current
note, concatenating the new text, and writing back.

**Rationale**: The Guesty API does not provide an atomic
"append note" operation. The reservation `note` field is a
single string, not a list of note entries. To append without
overwriting, the implementation must:

1. Fetch the current reservation note via the existing
   `get_reservations()` method or a targeted GET
2. Concatenate the new note text with a separator
3. PUT the combined text back

**Alternatives considered**:

- Overwrite approach: Simpler but violates FR-003 (must
  not overwrite existing notes). Rejected.
- Guest communication API: The messaging API
  (`/communication/conversations`) sends messages to
  guests, not internal notes. Different purpose.
- Direct PUT without read: Risk of overwriting. The
  `note` field is set in its entirety on PUT. Rejected
  unless Guesty provides a merge/append semantic.

**Risk**: Race condition if two automations update the same
reservation's notes concurrently. Mitigation: document this
limitation; the last write wins. This matches the behavior
described in the edge cases section of the spec ("each call
is processed independently").

### R3: Listing Status Values

**Decision**: Support `active` and `inactive` as the minimum
set of listing status values (FR-005). The Guesty API
represents listing status through the `active` boolean field
on the listing resource.

**Rationale**: The existing `_derive_listing_status()` function
in `models.py` shows that Guesty listings have three possible
states: active, inactive, and archived (derived from
`pms.active`, `listed`, and `active` fields). For the set
listing status action:

- `active` → `PUT /listings/{listing_id}` with
  `{"active": true, "listed": true}`
- `inactive` → `PUT /listings/{listing_id}` with
  `{"active": false}`

**Alternatives considered**:

- Support `archived` status: Archiving has additional
  side effects in Guesty (removes from channel managers).
  Excluded from initial scope per spec (minimum: active
  and inactive).
- Use availability settings endpoint: The
  `/listings/{listing_id}/availability-settings` endpoint
  controls
  channel-specific availability, which is more granular
  than needed for the status toggle use case.

### R4: Task Creation API

**Decision**: Use `POST /tasks-open-api/tasks` to create
operational tasks in Guesty.

**Rationale**: The Guesty tasks endpoint accepts:

```json
{
  "listingId": "string",
  "title": "string",
  "description": "string",
  "assigneeId": "string"
}
```

The `listingId` field associates the task with a property.
The `assigneeId` is optional and references a Guesty user.

**Alternatives considered**:

- Legacy `/tasks` endpoint: Older endpoint with different
  payload structure. The `tasks-open-api` prefix indicates
  the current Open API version.

### R5: Calendar Availability Management

**Decision**: Use
`PUT /availability-pricing/api/calendar/listings/{listing_id}`
to block and unblock date ranges.

**Rationale**: The calendar API accepts a date range via `dateFrom` and
`dateTo` fields, plus a `status` field. For blocking, set
`status: "unavailable"`; for unblocking, set
`status: "available"`. The client sends the range directly
without expanding to individual dates.

**Conflict detection**: The spec requires rejecting blocks
that conflict with confirmed reservations (FR-009). The
Guesty API may return a 409 Conflict or validation error
when attempting to block dates with existing reservations.
The client should catch this and return a clear conflict
error. If the API does not detect conflicts server-side,
the client can perform a pre-flight check using the
existing `get_reservations()` method to verify no confirmed
reservations overlap the requested date range.

**Alternatives considered**:

- Client-side-only conflict detection: Always check
  reservations before blocking. More reliable but adds
  an extra API call. Acceptable trade-off for safety.
- Bulk calendar endpoint: Operates on multiple listings.
  Unnecessary complexity for single-listing operations.

### R6: Custom Field Update Strategy

**Decision**: Use `PUT /reservations/{reservation_id}` with
the custom
fields payload to update reservation custom fields.

**Rationale**: Guesty custom fields on reservations are
updated via the reservation update endpoint. The payload
format for custom fields uses the custom field ID as the
key within a `customFields` object:

```json
{
  "customFields": {
    "{custom_field_id}": "{value}"
  }
}
```

This updates only the specified custom field without
affecting other fields on the reservation.

**Alternatives considered**:

- Dedicated custom fields endpoint: Guesty does not
  expose a separate endpoint for reservation custom
  fields; they are part of the reservation resource.

### R7: HA Service Registration Pattern

**Decision**: Register actions as domain-level services using
`hass.services.async_register()` rather than entity-specific
services.

**Rationale**: The automation actions target Guesty resources
by explicit ID (reservation_id, listing_id) rather than HA
entities. Domain-level services are the correct pattern for
operations that do not map to local entities. This matches
how other HA integrations handle write-back operations to
external systems.

Services are registered in `async_setup_entry` since they
depend on the authenticated API client created during entry
setup. Each config entry registers its own service handlers
and stores the actions client in its entry-specific data,
supporting multiple Guesty accounts.

**Alternatives considered**:

- Entity-specific services via
  `async_register_entity_service`: Would require creating
  proxy entities for each action type. Adds complexity
  without benefit since actions target Guesty IDs, not HA
  entities. Rejected.
- Register in `async_setup`: Would require deferring
  client creation. The API client is not available until
  `async_setup_entry`. For custom components with a single
  config entry, registering in `async_setup_entry` is
  acceptable.

### R8: Service Response Pattern

**Decision**: Actions return `ActionResult` as service
response data using `supports_response=SupportsResponse.OPTIONAL`
(FR-022).

**Rationale**: HA service calls can return response data
that automations can use for conditional branching. By
returning the `ActionResult` (success, target_id, error),
automations can implement error handling logic. The
`OPTIONAL` flag means callers can choose whether to receive
the response.

**Alternatives considered**:

- Fire-and-forget (no response): Simpler but prevents
  automation branching on success/failure. Rejected per
  FR-022.
- Events: Could fire HA events on action completion.
  Adds complexity; the service response pattern is
  simpler and more idiomatic for HA automations.

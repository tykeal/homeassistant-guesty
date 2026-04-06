<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Custom Variables

**Feature Branch**: `004-custom-variables`
**Created**: 2025-07-26
**Status**: Draft
**Input**: User description: "The ability to set custom variables
in Guesty's system from Home Assistant. This allows property
managers to push data from HA (like device states, automation
results, etc.) back into Guesty custom fields on listings. These
custom variables can then be used in Guesty templates, reports,
and automation workflows."

> **Terminology note**: "Custom variables" in this feature refers
> to Guesty's "custom fields" — user-defined name-value pairs
> that can be attached to listings and reservations. This
> specification uses the term "custom fields" consistently to
> align with Guesty's API and dashboard terminology.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Set a Custom Field on a Listing (Priority: P1)

A property manager wants to push data from Home Assistant into a
Guesty custom field on one of their listings. For example, after
a smart thermostat reports a temperature reading or a door sensor
detects an event, the manager calls a service in Home Assistant
specifying the listing, the custom field identifier, and the new
value. The integration sends the updated value to Guesty, where
it becomes available for use in Guesty templates, automated
messages, reports, and operational workflows.

**Why this priority**: Writing a custom field value to a listing
is the core capability of this feature. Without this fundamental
write operation, no other custom variable functionality (automation
integration, reservation fields) can deliver value. Listing-level
custom fields are the most commonly used type in Guesty property
management workflows.

**Independent Test**: Can be fully tested by calling the service
with a valid listing identifier, a valid custom field identifier,
and a value, then verifying the field is updated in Guesty.
Delivers the ability to push Home Assistant data into Guesty for
use in Guesty workflows.

> **Service behavior**: The custom field service is
> response-capable. On success, it returns a structured response
> confirming the update (target, field, and outcome). On failure,
> the service raises a clear error that automations and scripts
> can detect through standard error handling mechanisms.

**Acceptance Scenarios**:

1. **Given** the Guesty integration is configured and authenticated,
   **When** the user calls the custom field service specifying
   "listing" as the target type, a valid listing identifier, a
   valid custom field identifier, and a text value, **Then** the
   custom field is updated on the listing in Guesty and the
   service returns a structured success response confirming the
   update.
2. **Given** the integration is configured, **When** the user calls
   the service targeting a listing with a custom field identifier
   that does not exist on the target listing, **Then** the service
   raises a clear error identifying the invalid field.
3. **Given** the integration is configured, **When** the user calls
   the service targeting a listing with a value that does not match
   the custom field's expected type (e.g., text for a numeric
   field), **Then** the service raises a clear validation error
   before sending the request to Guesty.
4. **Given** the integration is configured, **When** the user calls
   the service without a required parameter (target type, target
   identifier, field identifier, or value), **Then** the service
   raises a clear error identifying the missing parameter.

---

### User Story 2 - Set a Custom Field on a Reservation (Priority: P1)

A property manager wants to push data from Home Assistant into a
Guesty custom field associated with a specific reservation. For
example, after an automation generates a door access code for an
arriving guest, the manager stores the code in a reservation
custom field so it appears in Guesty guest communications and
check-in templates. The manager calls a service specifying the
reservation, the custom field identifier, and the value.

**Why this priority**: Reservation-level custom fields are
essential for guest-facing workflows in Guesty. Property managers
commonly use reservation custom fields to populate automated
messages (check-in instructions, access codes, Wi-Fi passwords).
This is equally critical to listing-level fields because both
are core write paths for pushing Home Assistant data into Guesty.

**Independent Test**: Can be fully tested by calling the service
with a valid reservation identifier, a valid custom field
identifier, and a value, then verifying the field is updated on
the reservation in Guesty.

**Acceptance Scenarios**:

1. **Given** the integration is configured, **When** the user calls
   the custom field service specifying "reservation" as the target
   type, a valid reservation identifier, field identifier, and
   value, **Then** the custom field is updated on the reservation
   in Guesty and the service returns a structured success response.
2. **Given** a reservation identifier that does not exist in Guesty,
   **When** the user calls the service targeting that reservation,
   **Then** the service raises a clear error indicating the
   reservation was not found.
3. **Given** a reservation has already ended (guest checked out),
   **When** the user attempts to update a custom field on that
   reservation, **Then** the service sends the request to Guesty
   and surfaces whatever response Guesty returns (success or
   error), since Guesty may allow updates to past reservations.

---

### User Story 3 - Automate Custom Field Updates (Priority: P2)

A property manager wants to automatically update Guesty custom
fields based on Home Assistant events and automations. For
example, when a water leak sensor triggers, an automation updates
a "maintenance_alert" custom field on the affected listing. When
a smart lock generates a new access code for an upcoming guest,
a script stores the code in the reservation's custom field. The
service integrates with Home Assistant automations and scripts
using the standard service call interface.

**Why this priority**: Automation-driven updates are the primary
reason property managers want to push data from Home Assistant
into Guesty. Manual service calls have limited value compared to
automated workflows triggered by device events, schedules, or
state changes. However, the underlying service call capability
(Stories 1 and 2) must work first.

**Independent Test**: Can be tested by creating an HA automation
that calls the custom field service on a trigger event and
verifying the custom field is updated in Guesty. Delivers
hands-free data synchronization between Home Assistant and Guesty.

**Acceptance Scenarios**:

1. **Given** an automation is configured to call the custom field
   service on a trigger event, **When** the trigger fires, **Then**
   the custom field is updated in Guesty with the specified value.
2. **Given** a script calls the custom field service with template
   variables (e.g., sensor state values), **When** the script
   executes, **Then** the value is rendered with the resolved
   variable values before sending to Guesty.
3. **Given** an automation calls the custom field service, **When**
   the update succeeds, **Then** the automation continues to the
   next action without blocking.
4. **Given** an automation calls the custom field service, **When**
   the update fails, **Then** the failure is logged and the
   automation can detect the error through standard error handling.

---

### User Story 4 - Discover Available Custom Fields (Priority: P2)

A property manager wants to see which custom fields are defined
in their Guesty account so they know which field identifiers to
use when calling the custom field service. The integration
exposes the list of available custom field definitions (name,
identifier, and value type) so the manager can reference them
when building automations or making service calls.

**Why this priority**: Without discovery of available custom field
definitions, users must look up field identifiers in the Guesty
dashboard manually. Exposing field definitions within Home
Assistant improves usability and reduces errors from mistyped
field identifiers. However, users who already know their field
identifiers can use Stories 1 and 2 without discovery.

**Independent Test**: Can be tested by verifying that the
integration exposes the account's custom field definitions
(name, identifier, type) and that the information matches
what is shown in the Guesty dashboard.

**Acceptance Scenarios**:

1. **Given** the Guesty account has custom fields defined, **When**
   the integration fetches custom field definitions, **Then** each
   field's name, identifier, value type, and applicability (listing,
   reservation, or both) are available within Home Assistant.
2. **Given** the Guesty account has no custom fields defined,
   **When** the integration fetches definitions, **Then** an empty
   list is returned without errors.
3. **Given** a new custom field is created in Guesty, **When** the
   next data refresh occurs, **Then** the new field definition
   appears in the available fields list.
4. **Given** a custom field is deleted in Guesty, **When** the next
   data refresh occurs, **Then** the deleted field is removed from
   the available fields list.
5. **Given** custom fields exist for both listings and reservations,
   **When** the user queries available fields for a specific target
   type, **Then** only fields applicable to that target type are
   shown.

---

### User Story 5 - Graceful Error Handling (Priority: P3)

A property manager encounters errors when setting custom fields
(invalid field identifiers, type mismatches, rate limiting, or
Guesty API outages). The service handles these situations
gracefully: validation errors produce clear feedback before
sending requests, rate-limited requests are retried with backoff,
and transient failures are retried before reporting failure. The
manager receives actionable error information rather than cryptic
messages.

**Why this priority**: Robust error handling ensures the custom
field service remains reliable under adverse conditions. Basic
validation and error reporting are already addressed in Stories
1 and 2. This story covers advanced resilience scenarios that
improve service reliability but are not required for initial
functionality.

**Independent Test**: Can be tested by simulating invalid field
identifiers, type mismatches, rate limit responses, and API
outages, then verifying the service responds with appropriate
validation errors, retries, and error messages.

**Acceptance Scenarios**:

1. **Given** the Guesty API returns a rate limit response, **When**
   the service receives it, **Then** the service retries the
   request after the indicated wait period using exponential
   backoff with jitter.
2. **Given** the Guesty API is temporarily unreachable, **When** the
   service attempts an update, **Then** the service retries with
   backoff and eventually reports failure if the service remains
   unavailable.
3. **Given** a custom field update fails after all retry attempts,
   **When** the failure is reported, **Then** the error includes
   the reason, the target entity (listing or reservation), and the
   field identifier, and is logged at an appropriate level.
4. **Given** the user provides a value that exceeds the maximum
   length or is otherwise invalid for the field type, **When** the
   service validates the input, **Then** a clear validation error
   is returned before any request is sent to Guesty.

---

### Edge Cases

- What happens when a custom field accepts multiple value types
  (e.g., string or number)? The service should accept the value
  as provided and let Guesty validate type compatibility,
  surfacing any rejection clearly.
- What happens when a listing or reservation has no custom fields
  configured? The service should return a clear error indicating
  no custom fields are available on the target entity.
- What happens when the same custom field is updated concurrently
  from multiple automations? Each update should be sent
  independently to Guesty; the last write wins as determined by
  Guesty's server-side behavior.
- What happens when the custom field value contains special
  characters, Unicode, or very long strings? The service should
  pass the value through without modification and surface any
  Guesty rejection clearly.
- What happens when the Guesty account's custom field definitions
  change while an update is in progress? The service should
  rely on Guesty's server-side validation and surface any
  rejection of stale field references.
- What happens when the integration is not yet authenticated
  (token expired or missing) at the time a service call is made?
  The existing auth infrastructure (Feature 001) handles token
  refresh transparently; the custom field service should not
  manage authentication directly.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The integration MUST register a service that accepts
  a target type (listing or reservation), a target identifier
  (the Guesty-assigned unique identifier for the listing or
  reservation), a custom field identifier (the Guesty-assigned
  field ID), and a value to set.
- **FR-002**: The service MUST support updating custom fields on
  listings using the listing identifier and custom field
  identifier.
- **FR-003**: The service MUST support updating custom fields on
  reservations using the reservation identifier and custom field
  identifier.
- **FR-004**: The service MUST validate that all required
  parameters (target type, target identifier, field identifier,
  value) are present before sending a request to Guesty.
- **FR-005**: The service MUST validate the value type against the
  custom field's defined type (text, number, boolean) when the
  field definition is known, and return a clear validation error
  for type mismatches.
- **FR-006**: The service MUST return a clear, actionable error
  when Guesty rejects the update (invalid field, invalid target,
  type mismatch, permission error).
- **FR-006a**: On successful update, the service MUST return a
  structured response containing the target type, target
  identifier, field identifier, and confirmation of the update.
- **FR-006b**: On failure, the service MUST raise an error rather
  than returning a silent failure, enabling automations and
  scripts to detect and handle errors through standard mechanisms.
- **FR-007**: The service MUST be compatible with Home Assistant
  automations and scripts, supporting standard service call
  patterns including template rendering for dynamic values.
- **FR-008**: The integration MUST fetch and cache account-level
  custom field definitions (name, identifier, value type,
  applicability) to enable field discovery, entity-type-based
  filtering, and local type validation. Applicability MUST
  indicate whether a field applies to listings, reservations,
  or both.
- **FR-009**: Custom field definitions MUST be refreshed
  periodically, with a default refresh interval matching the
  listing data refresh.
- **FR-010**: The integration MUST expose custom field definitions
  (name, identifier, value type, applicability) so users can
  discover available fields within Home Assistant for the
  relevant target entity type.
- **FR-011**: The service MUST handle rate limit responses from
  Guesty by retrying with exponential backoff and jitter,
  consistent with the rate limit handling from Feature 001.
- **FR-012**: The service MUST handle transient failures (network
  errors, temporary Guesty outages) with retry logic before
  reporting a delivery failure.
- **FR-013**: After all retry attempts are exhausted, the service
  MUST report a clear failure with the reason, target entity
  type and identifier, and field identifier.
- **FR-014**: The service MUST log update attempts and outcomes at
  appropriate severity levels without including sensitive data
  (field values that may contain access codes, personal
  information) in log entries.
- **FR-015**: The service MUST perform all network communication
  without degrading the host application's responsiveness during
  service calls, automations, or scripts.
- **FR-016**: The custom fields client functionality MUST be
  reusable outside Home Assistant without changing its externally
  observable behavior, maintaining the library-extractable
  architecture.
- **FR-017**: The service MUST use the existing authenticated API
  client from Feature 001 for all Guesty API communication.
- **FR-018**: The service MUST use the current version of Guesty's
  reservation custom fields endpoints for reservation-level
  updates, consistent with Guesty's API migration timeline.

### Validation Scenarios

The following scenarios verify functional requirements not
directly covered by user story acceptance scenarios.

1. **Given** the custom field definitions have been fetched
   (FR-008), **When** the user calls the service with a field
   identifier not present in the definitions for the specified
   target entity type, **Then** the service returns a clear error
   listing available fields for that entity type.
2. **Given** the custom field definitions cache has expired
   (FR-009), **When** the next scheduled refresh occurs, **Then**
   the definitions are re-fetched and the cache is updated.
3. **Given** the custom fields client module is imported in
   isolation (FR-016), **When** examined for dependencies, **Then**
   it has zero dependencies on Home Assistant packages.
4. **Given** a service call is in progress (FR-015), **When** the
   network operation executes, **Then** the host application
   remains responsive and no blocking calls are made on the main
   execution path.
5. **Given** the service updates a reservation custom field
   (FR-018), **When** the API request is constructed, **Then** it
   targets the current version of Guesty's reservation custom
   fields endpoint.

### Key Entities

- **Custom Field Definition**: An account-level definition of a
  custom field. Key attributes: unique identifier (field ID),
  display name, value type (text, number, boolean), and
  applicability indicating whether the field applies to listings,
  reservations, or both. Definitions are managed in Guesty and
  fetched by the integration for discovery and validation.
- **Custom Field Value**: A specific value assigned to a custom
  field on a listing or reservation. Key attributes: the field
  identifier (referencing a Custom Field Definition), the value
  (typed according to the definition), and the target entity
  (listing or reservation identifier). Values are written by the
  integration and stored in Guesty.
- **Listing** (from Feature 002): The parent device representing a
  Guesty property. Custom field updates target listings using the
  listing identifier.
- **Reservation** (from Feature 003): A booking record associated
  with a listing. Custom field updates target reservations using
  the reservation identifier.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can update a custom field on a listing or
  reservation in under 10 seconds from service call to Guesty
  acceptance confirmation.
- **SC-002**: 100% of service calls with valid parameters result
  in the custom field being updated in Guesty.
- **SC-003**: The custom field service integrates with Home
  Assistant automations with zero additional configuration beyond
  the standard service call syntax.
- **SC-004**: 100% of rate limit responses are handled with retry
  and backoff, with zero unhandled rate limit errors surfaced to
  the user.
- **SC-005**: Invalid service calls (missing parameters, type
  mismatches, unknown fields) produce clear, actionable error
  messages within 2 seconds.
- **SC-006**: Custom field definitions are discoverable within
  Home Assistant within two refresh cycles of integration setup.
- **SC-007**: All custom field operations complete without
  degrading the host application's responsiveness during normal
  operation.
- **SC-008**: Sensitive data (access codes, personal information
  stored in custom field values) never appears in log output at
  any log level.
- **SC-009**: The custom field service can be validated in
  repeatable non-production test scenarios covering successful
  updates, validation errors, rate limits, and transient failures
  without requiring a live Guesty connection.
- **SC-010**: Updates to reservation custom fields use the current
  version of Guesty's reservation endpoints, ensuring
  compatibility beyond Guesty's older endpoint deprecation
  dates.

## Assumptions

- The Guesty integration is already configured and authenticated
  via Feature 001 (Auth & Config Flow). The custom field service
  depends on this existing infrastructure for API client access
  and token management.
- Feature 002 (Listings & Properties) is implemented and provides
  listing devices that serve as targets for listing-level custom
  field updates.
- Feature 003 (Reservations) is implemented and provides
  reservation data that enables reservation-level custom field
  updates. Reservation identifiers are available from the
  reservation entities exposed by Feature 003.
- Guesty's custom field definitions are managed in the Guesty
  dashboard; this integration only reads definitions and writes
  values. Creating, modifying, or deleting custom field
  definitions is out of scope.
- Custom field values can be one of three types: text (string),
  number, or boolean. The integration validates values against
  these types when field definitions are available.
- The Guesty API accepts partial updates to custom fields, meaning
  updating one field does not affect other custom fields on the
  same entity.
- Rate limit handling leverages the existing rate limit
  infrastructure from Feature 001's API client rather than
  implementing separate rate limit logic.
- The current version of Guesty's reservation custom fields
  endpoints are used for all reservation-level operations,
  consistent with Guesty's API migration timeline.
- Only outbound writes (Home Assistant to Guesty) are in scope
  for the service call. Reading custom field values is handled
  by Feature 002 (listing custom fields as sensors) and
  potentially by Feature 003 (reservation data attributes).
- The custom fields client functionality is designed to be
  reusable outside Home Assistant, consistent with the project's
  library-extractable client architecture.
- This feature does not provide real-time synchronization of
  custom field values back to Home Assistant after writing.
  Confirmation that the write succeeded comes from the Guesty
  API response; updated values appear in Home Assistant at the
  next scheduled data refresh.

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Automation Actions

**Feature Branch**: `006-automation-actions`
**Created**: 2025-07-26
**Status**: Draft
**Input**: User description: "Action services that allow HA
automations to interact with Guesty data. This enables automations
to take data from Rental Control and Guesty to make changes in the
HA environment as well as push data back to Guesty. Actions should
include things like updating reservation notes, setting listing
statuses, triggering Guesty workflows, and other write operations
that automations would need."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Update Reservation Notes (Priority: P1)

A property manager wants to automatically record events and
observations as notes on Guesty reservations from Home Assistant
automations. For example, when a smart lock detects a guest's
first entry, the automation appends a timestamped note to the
reservation in Guesty documenting the actual arrival time. When
a noise sensor triggers an alert, the automation logs the
incident on the reservation record. The manager calls an action
specifying the reservation identifier and the note text, and the
note appears on the reservation in the Guesty dashboard.

**Why this priority**: Writing notes to reservations is the most
frequently needed write operation for property management
automations. It creates an audit trail of property events
directly on the booking record, bridging the gap between
physical device events in Home Assistant and the property
management record in Guesty. This is the minimum viable write
capability.

**Independent Test**: Can be fully tested by calling the action
with a valid reservation identifier and note text, then
verifying the note appears on the reservation in Guesty.
Delivers the ability to create an audit trail of smart home
events on booking records.

**Acceptance Scenarios**:

1. **Given** the integration is configured and authenticated,
   **When** the user calls the add reservation note action
   with a valid reservation identifier and note text, **Then**
   the note is added to the reservation in Guesty and a success
   confirmation is returned.
2. **Given** the integration is configured, **When** the user
   calls the action without a reservation identifier, **Then**
   the action returns a clear error indicating the reservation
   target is required.
3. **Given** the integration is configured, **When** the user
   calls the action with an empty note, **Then** the action
   returns a clear error indicating note text is required.
4. **Given** a valid reservation identifier is provided, **When**
   the action adds a note, **Then** the note appears in the
   Guesty reservation record without overwriting existing notes.
5. **Given** an automation triggers the action, **When** the
   note is added successfully, **Then** the action executes
   asynchronously without degrading Home Assistant
   responsiveness, and the automation can proceed to its next
   step after the service call completes.

---

### User Story 2 — Set Listing Status (Priority: P1)

A property manager wants to change a Guesty listing's
operational status from Home Assistant automations in response to
property events. For example, when a water leak sensor triggers,
an automation deactivates the listing to prevent new bookings
until the issue is resolved. When maintenance is complete and
the sensor clears, another automation reactivates the listing.
The manager calls an action specifying the listing identifier
and the desired status.

**Why this priority**: Controlling listing availability directly
from property events prevents overbooking and protects guests
from being booked into properties with active maintenance
issues. This is a safety-critical capability for property
management and complements the read-only listing data from
Feature 002.

**Independent Test**: Can be fully tested by calling the action
with a valid listing identifier and a target status, then
verifying the listing's status changes in Guesty. Delivers the
ability to control property availability based on real-time
property conditions.

**Acceptance Scenarios**:

1. **Given** a listing is currently active in Guesty, **When**
   the user calls the set listing status action with that
   listing's identifier and a status of "inactive", **Then**
   the listing becomes inactive in Guesty and new bookings are
   prevented.
2. **Given** a listing is currently inactive, **When** the user
   calls the action with a status of "active", **Then** the
   listing becomes active and is available for bookings again.
3. **Given** the user calls the action with an invalid listing
   identifier, **Then** the action returns a clear error
   indicating the listing was not found.
4. **Given** the user calls the action with an unsupported
   status value, **Then** the action returns a clear error
   listing the supported status values.
5. **Given** the listing status is updated successfully, **When**
   the next listing data refresh occurs (Feature 002), **Then**
   the listing status sensor in Home Assistant reflects the new
   status.

---

### User Story 3 — Create Operational Tasks (Priority: P2)

A property manager wants Home Assistant automations to create
operational tasks in Guesty. For example, after a guest checks
out (detected via the reservation status sensor from Feature
003), an automation creates a cleaning task in Guesty assigned
to the cleaning team for that property. When a smart home device
reports a maintenance issue, an automation creates a maintenance
task. The manager calls an action specifying the listing, task
title, and optional details such as description and assignee.

**Why this priority**: Task creation bridges the gap between
device-detected events and property management operations. It
enables hands-free workflow initiation based on real-world
property events. This supports the "triggering Guesty
workflows" requirement since Guesty's internal automation system
can respond to newly created tasks. However, it requires the
foundational write capabilities from P1 stories to be in place
first.

**Independent Test**: Can be tested by calling the action with
a listing identifier and task details, then verifying the task
appears in the Guesty task management interface. Delivers the
ability to automate operational task creation from property
events.

**Acceptance Scenarios**:

1. **Given** the integration is configured, **When** the user
   calls the create task action with a listing identifier, task
   title, and description, **Then** a new task is created in
   Guesty associated with that listing.
2. **Given** a task is created with an assignee specified,
   **When** the task appears in Guesty, **Then** it is assigned
   to the specified team or user.
3. **Given** the user calls the action without a required
   parameter (listing identifier or task title), **Then** the
   action returns a clear error identifying the missing field.
4. **Given** a Guesty workflow is configured to trigger on new
   task creation, **When** the action creates a task, **Then**
   the Guesty workflow fires as expected.

---

### User Story 4 — Update Calendar Availability (Priority: P2)

A property manager wants to block or unblock date ranges on a
listing's calendar from Home Assistant automations. For example,
when Rental Control or another system indicates an owner stay or
maintenance period, the automation blocks those dates in Guesty
to prevent guest bookings. When the period ends, the automation
unblocks the dates. The manager calls an action specifying the
listing, date range, and whether to block or unblock.

**Why this priority**: Calendar management is essential for
coordinating between multiple systems (Home Assistant, Rental
Control, Guesty). Blocking dates prevents double-bookings and
ensures property availability reflects real-world constraints.
However, it builds on the simpler write operations from P1
stories.

**Independent Test**: Can be tested by calling the action with
a listing identifier and date range, then verifying the dates
are blocked or unblocked in the Guesty calendar. Delivers the
ability to synchronize property availability across systems.

**Acceptance Scenarios**:

1. **Given** a listing has available dates, **When** the user
   calls the calendar block action with a date range, **Then**
   those dates become unavailable for booking in Guesty.
2. **Given** dates are currently blocked on a listing's
   calendar, **When** the user calls the calendar unblock
   action for those dates, **Then** the dates become available
   for booking again.
3. **Given** the user specifies a date range that overlaps with
   an existing reservation, **When** the block action is called,
   **Then** the action returns a clear error indicating the
   conflict with the existing reservation.
4. **Given** the user specifies an invalid date range (end date
   before start date), **When** the action is called, **Then**
   the action returns a clear validation error.

---

### User Story 5 — Update Custom Fields (Priority: P3)

A property manager wants automations to write data to
reservation custom fields in Guesty. For example, an automation
that generates a door access code writes the code to a
designated custom field on the reservation for reference by
other Guesty users and workflows. Or an automation records a
parking assignment, Wi-Fi password, or other property-specific
data on the reservation.

**Why this priority**: Custom field updates enable flexible data
exchange between Home Assistant and Guesty beyond the predefined
fields. This serves power users who leverage Guesty's custom
field system for property-specific workflows. However, the core
write operations (notes, status, tasks) deliver value to all
users first.

**Independent Test**: Can be tested by calling the action with
a reservation identifier, custom field identifier, and value,
then verifying the custom field is updated in Guesty. Delivers
the ability to write arbitrary structured data to reservation
records.

**Acceptance Scenarios**:

1. **Given** a reservation has a custom field defined in Guesty,
   **When** the user calls the update custom field action with
   the reservation identifier, field identifier, and new value,
   **Then** the custom field is updated in Guesty.
2. **Given** the user specifies a custom field identifier that
   does not exist for the reservation, **When** the action is
   called, **Then** the action returns a clear error indicating
   the field was not found.
3. **Given** the custom field value exceeds the maximum allowed
   length or does not match the expected type, **When** the
   action is called, **Then** the action returns a validation
   error before sending to Guesty.

---

### Edge Cases

- What happens when an action targets a reservation or listing
  that has been deleted in Guesty? The action returns a clear
  "not found" error with the identifier that was provided.
- What happens when the Guesty API rate limit is hit during an
  action call? The action retries with exponential backoff using
  the existing rate limit infrastructure from the API client.
- What happens when multiple automations call the same action
  for the same entity simultaneously? Each call is processed
  independently; the integration does not deduplicate or
  serialize concurrent calls to the same target.
- What happens when the integration's authentication token has
  expired at the time an action is called? The existing auth
  infrastructure from Feature 001 handles token refresh
  transparently.
- What happens when the Guesty API accepts the request but the
  change is not immediately visible due to eventual consistency?
  The action reports success based on the API acceptance
  response; the change may take a polling cycle to appear in
  local sensors.
- What happens when a user provides a note or task description
  with special characters, unicode, or very long text? Input is
  validated against reasonable limits before sending; the
  integration does not silently truncate content.
- What happens when an action is called while the integration
  is still performing its initial data load? The action succeeds
  independently since it communicates directly with Guesty and
  does not require local data to be loaded first.

## Requirements *(mandatory)*

### Service Definitions

Each action service is registered with Home Assistant under the
`guesty` domain. The following definitions enumerate the service
name, required and optional parameters, and validation rules for
each action.

- **SD-001 — Add Reservation Note**
  (`guesty.add_reservation_note`):
  - Required: `reservation_id` (string — Guesty reservation
    identifier), `note_text` (string — note content to append).
  - Validation: `note_text` MUST be 1 to 5000 characters.
  - Behavior: appends the note without overwriting existing
    notes or unrelated reservation data.

- **SD-002 — Set Listing Status**
  (`guesty.set_listing_status`):
  - Required: `listing_id` (string — Guesty listing
    identifier), `status` (string — target operational status).
  - Validation: `status` MUST be one of `active` or `inactive`
    (at minimum).

- **SD-003 — Create Task** (`guesty.create_task`):
  - Required: `listing_id` (string — Guesty listing
    identifier), `task_title` (string — title of the task).
  - Optional: `description` (string — detailed task
    description), `assignee` (string — Guesty user identifier
    or assignee reference).
  - Validation: `task_title` MUST be 1 to 255 characters;
    `description`, when provided, MUST be 1 to 5000
    characters.
  - Behavior: associates the created task with the specified
    listing.

- **SD-004 — Set Calendar Availability**
  (`guesty.set_calendar_availability`):
  - Required: `listing_id` (string — Guesty listing
    identifier), `start_date` (string — `YYYY-MM-DD` format),
    `end_date` (string — `YYYY-MM-DD` format), `operation`
    (string — `block` or `unblock`).
  - Validation: `end_date` MUST be the same as or later than
    `start_date`; `operation` MUST be `block` or `unblock`.
  - Behavior: rejects date ranges that conflict with existing
    confirmed reservations and returns a clear conflict error.

- **SD-005 — Update Reservation Custom Field**
  (`guesty.update_reservation_custom_field`):
  - Required: `reservation_id` (string — Guesty reservation
    identifier), `custom_field_id` (string — Guesty custom
    field identifier), `value` (string — new field value).
  - Validation: `value` MUST be 1 to 5000 characters unless
    Guesty enforces a stricter field-specific limit.

### Functional Requirements

- **FR-001**: The integration MUST register each action defined
  in the Service Definitions (SD-001 through SD-005) under the
  `guesty` domain in Home Assistant, enabling invocation from
  automations, scripts, and the developer tools interface.
- **FR-002**: The `guesty.add_reservation_note` action (SD-001)
  MUST accept `reservation_id` and `note_text` as required
  parameters and add the note to the reservation in Guesty.
- **FR-003**: The `guesty.add_reservation_note` action MUST
  append notes to the reservation without overwriting existing
  notes or other reservation data.
- **FR-004**: The `guesty.set_listing_status` action (SD-002)
  MUST accept `listing_id` and `status` as required parameters
  and update the listing's operational status in Guesty.
- **FR-005**: The supported `status` values for
  `guesty.set_listing_status` MUST include at minimum `active`
  and `inactive`, matching Guesty's supported operational
  statuses.
- **FR-006**: The `guesty.create_task` action (SD-003) MUST
  accept `listing_id` and `task_title` as required parameters,
  with optional `description` and `assignee` parameters.
- **FR-007**: The `guesty.create_task` action MUST associate
  created tasks with the specified listing in Guesty.
- **FR-008**: The `guesty.set_calendar_availability` action
  (SD-004) MUST accept `listing_id`, `start_date`, `end_date`,
  and `operation` as required parameters to block or unblock
  date ranges on a listing's calendar.
- **FR-009**: The `guesty.set_calendar_availability` action
  MUST reject date ranges that conflict with existing confirmed
  reservations and return a clear error explaining the
  conflict.
- **FR-010**: The `guesty.update_reservation_custom_field`
  action (SD-005) MUST accept `reservation_id`,
  `custom_field_id`, and `value` as required parameters.
- **FR-011**: All action services MUST validate required
  parameters before making API requests and return clear,
  actionable error messages when parameters are missing or
  invalid.
- **FR-012**: All action services MUST validate parameter values
  against the rules in each Service Definition (date formats,
  status values, text length limits) before making API requests.
- **FR-013**: All action services MUST handle Guesty API error
  responses (not found, validation errors, permission errors)
  and translate them into clear, actionable error messages for
  the user.
- **FR-014**: All action services MUST handle rate limit
  responses from the Guesty API by retrying with exponential
  backoff and jitter, consistent with the rate limit handling
  from Feature 001.
- **FR-015**: All action services MUST handle transient failures
  (network errors, temporary Guesty outages) with retry logic
  before reporting failure.
- **FR-016**: After all retry attempts are exhausted, each
  action MUST report a clear failure with the reason and the
  targeted entity identifier.
- **FR-017**: All action services MUST be fully compatible with
  Home Assistant automations, scripts, and the developer tools
  service call interface, supporting standard service call
  patterns including template rendering for parameters.
- **FR-018**: All action services MUST perform Guesty API
  communication asynchronously without degrading Home Assistant
  responsiveness.
- **FR-019**: The Guesty write-operation client functionality
  used by the action services MUST be reusable outside Home
  Assistant without changing its externally observable behavior.
- **FR-020**: All action services MUST log operation attempts
  and outcomes at appropriate severity levels without including
  sensitive data (guest PII, access codes, tokens) in log
  entries.
- **FR-021**: All action services MUST use the existing
  authenticated API client from Feature 001 for all Guesty API
  communication.
- **FR-022**: Each action MUST return a structured response
  containing at minimum a `success` indicator (boolean) and
  a `target_id` field identifying the targeted Guesty resource
  (reservation or listing identifier). On failure the response
  MUST include an `error` field with a human-readable reason.
  This response structure enables automations to branch on
  success or failure using standard conditional logic.

### Key Entities

- **Action Request**: Represents a single request to perform a
  write operation in Guesty. Contains the action type, target
  entity identifier (reservation or listing), and
  action-specific parameters. Created by a service call and
  consumed by the API client.
- **Reservation** (from Feature 003): The booking record
  targeted by note updates and custom field updates. Referenced
  by its unique reservation identifier.
- **Listing** (from Feature 002): The property targeted by
  status changes, task creation, and calendar updates.
  Referenced by its unique listing identifier.
- **Operational Task**: A task record created in Guesty's task
  management system. Contains a title, optional description,
  optional assignee, and is associated with a listing.
- **Calendar Block**: A date range on a listing's calendar that
  is blocked or unblocked for bookings. Contains start date,
  end date, and the listing reference.
- **Action Result**: The outcome of a write operation. Contains
  the success or failure status, any error details, and the
  target entity identifier. Returned to the caller and recorded
  in logs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can add a note to a reservation from an
  automation in under 10 seconds from action call to Guesty
  acceptance confirmation.
- **SC-002**: Users can change a listing's status from an
  automation in under 10 seconds from action call to Guesty
  acceptance confirmation.
- **SC-003**: All action services integrate with HA automations
  with zero additional configuration beyond the standard action
  call syntax.
- **SC-004**: 100% of rate limit responses are handled with
  retry and backoff, with zero unhandled rate limit errors
  surfaced to users.
- **SC-005**: Invalid action calls (missing parameters, invalid
  values) produce clear, actionable error messages within 2
  seconds.
- **SC-006**: Action failures include sufficient detail (target
  identifier, failure reason) for the user to diagnose and
  resolve the issue without inspecting source code.
- **SC-007**: All action operations complete without degrading
  Home Assistant responsiveness during normal operation.
- **SC-008**: Sensitive data (guest PII, access codes, tokens)
  never appears in log output at any log level.
- **SC-009**: All action services can be validated in repeatable
  test scenarios covering success, validation errors, rate
  limits, and failure conditions without requiring a live Guesty
  connection.
- **SC-010**: Calendar block operations correctly prevent
  conflicts with existing reservations, with zero accidental
  overbooking scenarios.

## Assumptions

- Feature 001 (Auth & Config Flow) is implemented and provides
  the authenticated API client for all Guesty communication
  including write operations.
- Feature 002 (Listings/Properties) is implemented and provides
  listing device data that the set listing status, create task,
  and calendar availability actions reference.
- Feature 003 (Reservations) is implemented and provides the
  reservation data model that the update notes and custom field
  actions reference.
- The Guesty API provides write endpoints for reservation
  updates, listing updates, task creation, and calendar
  management, consistent with the documented OpenAPI
  specification.
- Action services do not require local entity data to be loaded;
  they communicate directly with Guesty using identifiers
  provided by the caller.
- Guesty workflow triggering is achieved indirectly through
  write operations (creating tasks, updating reservations) that
  Guesty's internal automation system responds to, rather than
  through a direct workflow trigger endpoint.
- Rate limit handling leverages the existing infrastructure from
  Feature 001's API client rather than implementing separate
  rate limit logic for write operations.
- The write-operation client functionality is designed to be
  reusable outside Home Assistant, consistent with the project's
  library-extractable client architecture.
- Only outbound write operations (Home Assistant to Guesty) are
  in scope. Receiving real-time confirmation of changes via
  webhooks is out of scope for this feature.
- The Guesty API may exhibit eventual consistency; actions report
  success based on API acceptance, and local sensor data updates
  on the next polling cycle.
- Calendar block operations target availability management only
  and do not modify pricing or minimum-stay rules.
- Automations that "make changes in the HA environment" based on
  Guesty or Rental Control data use standard Home Assistant
  services for local device control; the actions defined in this
  feature are exclusively for writing data to Guesty.

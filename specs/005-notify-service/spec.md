<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Guesty Notify Service

**Feature Branch**: `005-specify-notify`
**Created**: 2025-07-24
**Status**: Draft
**Input**: User description: "Notify service for Guesty HA integration —
send messages to guests via Guesty communication APIs"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Send a Message to a Guest (Priority: P1)

A property manager using Home Assistant wants to send a message to a
guest who has an active reservation. The manager calls the Home
Assistant notify service, specifying the reservation ID and the message
text. The integration delivers the message through Guesty's
communication system using the default channel available for that
reservation's conversation. The manager receives confirmation that the
message was accepted for delivery.

**Why this priority**: Sending a single message to a guest is the
fundamental capability of the notify service. Without this, no other
notification features have value. This is the minimum viable
notification path.

**Independent Test**: Can be fully tested by calling the notify service
with a valid reservation ID and message body, then verifying Guesty
received the message request. Delivers the ability to communicate with
guests directly from Home Assistant.

**Acceptance Scenarios**:

1. **Given** the Guesty integration is configured and authenticated,
   **When** the user calls the notify service with a valid reservation
   ID and a message body, **Then** the message is sent to the guest
   through Guesty and a success confirmation is returned.
2. **Given** the Guesty integration is configured, **When** the user
   calls the notify service without specifying a reservation ID,
   **Then** the service returns a clear error indicating a reservation
   target is required.
3. **Given** the Guesty integration is configured, **When** the user
   calls the notify service with an empty message body, **Then** the
   service returns a clear error indicating a message body is required.
4. **Given** a valid reservation ID is provided, **When** the notify
   service sends the message, **Then** the message appears in the
   Guesty inbox conversation associated with that reservation.

---

### User Story 2 - Automate Guest Notifications (Priority: P1)

A property manager wants to automatically send messages to guests based
on Home Assistant events. For example, when a smart lock code is
generated for an upcoming check-in, the automation sends the access
code to the guest. The notify service integrates with Home Assistant
automations and scripts using the standard notify service interface, so
the manager can use the visual automation editor or YAML to configure
message triggers.

**Why this priority**: Automation is the primary reason property
managers integrate Guesty with Home Assistant. Manual message sending
has limited value compared to automated workflows triggered by device
events, schedules, or reservation status changes.

**Independent Test**: Can be tested by creating an HA automation that
calls the notify service on a trigger event and verifying the message
is dispatched to Guesty. Delivers the ability to build hands-free
guest communication workflows.

**Acceptance Scenarios**:

1. **Given** an automation is configured to call the Guesty notify
   service on a trigger event, **When** the trigger fires, **Then**
   the notify service sends the specified message to the target guest.
2. **Given** a script calls the Guesty notify service with template
   variables, **When** the script executes, **Then** the message body
   is rendered with the resolved variable values before sending.
3. **Given** an automation calls the notify service, **When** the
   message is sent successfully, **Then** the automation continues to
   the next action without blocking.
4. **Given** an automation calls the notify service, **When** the
   message delivery fails, **Then** the failure is logged and the
   automation can detect the error through standard HA error handling.

---

### User Story 3 - Choose a Message Channel (Priority: P2)

A property manager wants to send a message to a guest through a
specific communication channel (email, SMS, or the booking platform's
native messaging such as Airbnb or Booking.com). The manager specifies
the desired channel when calling the notify service. If the requested
channel is available for that reservation, the message is sent through
it. If the channel is not available, the manager receives a clear error
explaining which channels are available.

**Why this priority**: Channel selection gives the manager control over
how guests receive messages. Some messages are better suited to SMS
(urgent access codes) while others fit email (detailed check-in
instructions). However, the default channel path (Story 1) provides
basic functionality without channel selection.

**Independent Test**: Can be tested by calling the notify service with
an explicit channel parameter and verifying the message is routed
through the specified channel in Guesty. Delivers channel-specific
communication control.

**Acceptance Scenarios**:

1. **Given** a reservation has multiple available channels, **When**
   the user calls the notify service specifying a channel (e.g.,
   "email"), **Then** the message is sent through the specified channel.
2. **Given** a reservation's conversation supports only certain
   channels, **When** the user requests an unavailable channel, **Then**
   the service returns a clear error listing the available channels for
   that reservation.
3. **Given** no channel is specified, **When** the user calls the
   notify service, **Then** the message is sent through Guesty's
   default channel for that reservation's conversation.

---

### User Story 4 - Use Message Templates (Priority: P2)

A property manager wants to use reusable message templates with
variable placeholders for common notifications (check-in instructions,
Wi-Fi credentials, checkout reminders). The manager defines a template
with placeholders and provides the variable values when calling the
notify service. The integration substitutes the variables into the
template before sending the message.

**Why this priority**: Templates reduce repetitive manual composition
and ensure consistency across guest communications. They are especially
valuable for automated workflows where messages follow a standard
format. However, plain text messages (Story 1) provide the core
capability without templates.

**Independent Test**: Can be tested by calling the notify service with
a template containing placeholders and providing variable values, then
verifying the sent message contains the substituted values. Delivers
reusable, consistent guest messaging.

**Acceptance Scenarios**:

1. **Given** the user calls the notify service with a message template
   containing placeholders (e.g., `{guest_name}`, `{check_in_time}`),
   **When** the corresponding variable values are provided in the
   service call data, **Then** the sent message contains the
   substituted values.
2. **Given** a template contains a placeholder for which no value is
   provided, **When** the notify service processes the template,
   **Then** the service returns a clear error identifying the missing
   variable rather than sending a partially rendered message.
3. **Given** a template contains no placeholders, **When** the notify
   service processes it, **Then** the message is sent as-is without
   modification.

---

### User Story 5 - Graceful Error and Rate Limit Handling (Priority: P3)

A property manager sends messages during a period of heavy activity or
encounters delivery issues (invalid reservation, Guesty service
outage, rate limiting). The notify service handles these situations
gracefully: rate-limited requests are retried with appropriate backoff,
undeliverable messages produce clear error information, and transient
failures are retried before reporting failure. The manager sees
actionable feedback rather than cryptic errors.

**Why this priority**: Robust error handling ensures the notify service
remains reliable under adverse conditions. However, basic error
reporting (invalid input, connection failures) is already covered in
Story 1. This story addresses advanced resilience scenarios that
improve the service but are not required for initial functionality.

**Independent Test**: Can be tested by simulating rate limit responses,
invalid reservation IDs, and service outages, then verifying the
notify service responds with appropriate retries, backoff, and error
messages. Delivers production-grade reliability.

**Acceptance Scenarios**:

1. **Given** the Guesty API returns a rate limit response, **When** the
   notify service receives it, **Then** the service retries the message
   after the indicated wait period using exponential backoff with
   jitter.
2. **Given** the user provides a reservation ID that does not exist in
   Guesty, **When** the notify service attempts to send, **Then** the
   service returns a clear error indicating the reservation was not
   found.
3. **Given** the Guesty API is temporarily unreachable, **When** the
   notify service attempts to send, **Then** the service retries with
   backoff and eventually reports a delivery failure if the service
   remains unavailable.
4. **Given** a message delivery fails after all retry attempts, **When**
   the failure is reported, **Then** the error includes the reason for
   failure and the reservation ID targeted, and is logged at an
   appropriate severity level.

---

### Edge Cases

- What happens when the reservation has already ended (guest checked
  out)? The integration should attempt delivery and surface whatever
  error Guesty returns, since Guesty may still allow messaging to past
  guests depending on the channel.
- What happens when a reservation ID maps to a conversation that has
  been archived or deleted in Guesty? The service should return a clear
  error explaining the conversation is no longer available.
- What happens when the message body exceeds Guesty's maximum message
  length for a given channel? The service should return a validation
  error before sending rather than letting Guesty truncate or reject
  the message silently.
- What happens when multiple notify calls target the same reservation
  simultaneously? Each message should be sent independently without
  interference or deduplication.
- What happens when the Guesty API returns an unexpected response
  format from the communication endpoint? The service should raise a
  clear error rather than crashing or silently failing.
- What happens when the integration is not yet authenticated (token
  expired or missing) at the time a notify call is made? The existing
  auth infrastructure (Feature 001) handles token refresh
  transparently; the notify service should not need to manage
  authentication directly.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The integration MUST register a notify platform using
  Home Assistant's standard notify service interface, enabling message
  sending via `notify.guesty` (or similar service name).
- **FR-002**: The notify service MUST accept a reservation ID as the
  message target, provided in the service call data.
- **FR-003**: The notify service MUST accept a plain text message body
  as the content to send to the guest.
- **FR-004**: The notify service MUST send messages to Guesty using
  Guesty's conversation-based communication endpoints, associating the
  message with the reservation's conversation.
- **FR-005**: The notify service MUST return a clear, actionable error
  when called without a required parameter (reservation ID, message
  body).
- **FR-006**: The notify service MUST support specifying a
  communication channel (email, SMS, Airbnb, Booking.com, VRBO, etc.)
  in the service call data, with the channel being optional.
- **FR-007**: When no channel is specified, the notify service MUST
  send the message through Guesty's default channel for the target
  reservation's conversation.
- **FR-008**: When a requested channel is not available for the target
  reservation, the notify service MUST return a clear error listing the
  available channels.
- **FR-009**: The notify service MUST support message templates with
  variable placeholders that are substituted before sending.
- **FR-010**: When a template placeholder has no corresponding value
  provided, the notify service MUST return an error identifying the
  missing variable rather than sending a partially rendered message.
- **FR-011**: The notify service MUST be fully compatible with Home
  Assistant automations and scripts, supporting standard service call
  patterns including template rendering.
- **FR-012**: The notify service MUST handle rate limit responses from
  Guesty by retrying with exponential backoff and jitter, consistent
  with the rate limit handling established in Feature 001.
- **FR-013**: The notify service MUST handle transient failures
  (network errors, temporary Guesty outages) with retry logic before
  reporting a delivery failure.
- **FR-014**: After all retry attempts are exhausted, the notify
  service MUST report a clear delivery failure with the reason and the
  targeted reservation ID.
- **FR-015**: The notify service MUST log message delivery attempts and
  outcomes at appropriate severity levels without including message
  content or guest personal information in log entries.
- **FR-016**: All network operations in the notify service MUST be
  asynchronous and MUST NOT block the Home Assistant event loop.
- **FR-017**: The notify service API client layer (communication
  endpoint wrappers) MUST remain independent of Home Assistant imports,
  consistent with the library-extractable architecture of the
  `api/` sub-package.
- **FR-018**: The notify service MUST validate message parameters
  (reservation ID format, message body length, channel name) before
  making API requests to Guesty.
- **FR-019**: The notify service MUST use the existing authenticated
  API client from Feature 001 for all Guesty API communication.

### Key Entities

- **Notification Request**: Represents a single request to send a
  message to a guest. Contains the target reservation ID, the message
  body (plain text or rendered template), the optional channel
  preference, and any template variables. Created by a notify service
  call and consumed by the communication API client.
- **Conversation**: Represents a Guesty inbox conversation associated
  with a reservation. Contains the conversation identifier and the
  list of available communication channels. Retrieved from Guesty when
  resolving a reservation ID to a messaging target.
- **Message Delivery Result**: Represents the outcome of a message
  send attempt. Contains the delivery status (success, failed, rate
  limited), any error details, and retry metadata. Returned to the
  caller and recorded in logs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can send a message to a guest by reservation ID
  in under 10 seconds from service call to delivery confirmation.
- **SC-002**: 100% of notify service calls with valid parameters
  result in the message being submitted to Guesty for delivery.
- **SC-003**: The notify service integrates with HA automations with
  zero additional configuration beyond the standard service call
  syntax.
- **SC-004**: 100% of rate limit responses are handled with retry and
  backoff, with zero unhandled rate limit errors surfaced to the user.
- **SC-005**: Invalid service calls (missing reservation ID, empty
  message, unavailable channel) produce clear, actionable error
  messages within 2 seconds.
- **SC-006**: Message delivery failures include sufficient detail
  (reservation ID, failure reason) for the user to diagnose and
  resolve the issue without inspecting source code or logs.
- **SC-007**: All notify service operations complete without degrading
  Home Assistant responsiveness during normal operation.
- **SC-008**: Sensitive data (message content, guest personal
  information, tokens) never appears in log output at any log level.
- **SC-009**: The notify service can be validated in repeatable
  non-production test scenarios covering successful delivery, channel
  selection, template rendering, rate limits, and error conditions
  without requiring a live Guesty connection.
- **SC-010**: Template variable substitution correctly resolves all
  provided variables and rejects templates with missing variables,
  with zero partially rendered messages sent.

## Assumptions

- The Guesty integration is already configured and authenticated via
  Feature 001 (auth and config flow). The notify service depends on
  this existing infrastructure for API client access and token
  management.
- Guesty's communication API uses a conversation-based model where
  each reservation has an associated conversation with one or more
  available messaging channels.
- The Guesty Open API communication endpoints
  (`/v1/communication`, `/v1/guests-communication/conversations/`)
  accept messages targeted by conversation ID, which can be resolved
  from a reservation ID.
- Available messaging channels (email, SMS, Airbnb, Booking.com,
  VRBO, etc.) are determined by the booking source and guest contact
  information in Guesty; the integration does not control which
  channels are available.
- Message template variable substitution is handled locally within the
  integration before sending to Guesty, using a simple placeholder
  syntax (e.g., `{variable_name}`).
- The notify service does not provide delivery receipts or read
  receipts; it confirms that the message was accepted by Guesty for
  delivery, not that the guest received or read it.
- Rate limit handling leverages the existing rate limit infrastructure
  from Feature 001's API client rather than implementing separate rate
  limit logic.
- The `api/` sub-package remains library-extractable with zero Home
  Assistant imports; Guesty communication endpoint wrappers live in
  this package while HA-specific notify platform code lives outside it.
- Only outbound messaging (host to guest) is in scope. Inbound message
  handling (receiving guest replies) is out of scope for this feature.
- This feature does not depend on Feature 002 (listings) or Feature
  003 (reservations) data being loaded into Home Assistant; it
  communicates directly with Guesty using reservation IDs provided by
  the caller.

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Guesty Notify Service

**Feature**: 005-notify-service | **Date**: 2025-07-24

## R-001: Guesty Communication API — Conversation Model

**Decision**: Use Guesty's conversation-based messaging model where
each reservation has an associated conversation. Resolve reservation
ID → conversation ID, then send messages to that conversation.

**Rationale**: Guesty organizes guest communication around
conversations, not direct messages. Each reservation creates a
conversation in the Guesty Unified Inbox. The API requires a
conversation ID to send messages, not a reservation ID directly.
This two-step flow (resolve → send) is the only supported pattern.

**Alternatives considered**:

- Direct message by reservation ID: Not supported by Guesty API;
  conversations are the messaging abstraction layer.
- Direct message by guest ID: Guests may have multiple reservations
  with different conversations and channels; targeting by guest is
  ambiguous.

## R-002: Guesty Conversation Resolution Endpoint

**Decision**: Use
`GET /v1/communication/conversations?filters=[{"field":"reservation._id","operator":"$eq","value":"RESERVATION_ID"}]`
to resolve a reservation ID to its conversation.

**Rationale**: The Guesty API provides a filtered list endpoint for
conversations. Filtering by `reservation._id` returns the
conversation(s) associated with a specific reservation. The response
includes conversation metadata including the conversation ID and
available communication channels (module types).

**Alternatives considered**:

- GET by conversation ID directly: Requires the caller to know the
  conversation ID, which is an internal Guesty identifier. Users
  naturally think in terms of reservations, not conversation IDs.
- Search endpoint: Conversations list with filter is the documented
  approach for reservation-based lookup.

## R-003: Guesty Send Message Endpoint

**Decision**: Use
`POST /v1/communication/conversations/{conversationId}/send-message`
with a JSON body containing `module` (channel type) and `body`
(message text).

**Rationale**: This is the documented endpoint for sending outbound
messages in Guesty. The `module` object specifies the delivery
channel (e.g., `{"type": "platform"}` for the booking platform's
native messaging, `{"type": "email"}` for email). The `body` field
contains the plain text message content.

**Alternatives considered**:

- POST to `/v1/messages`: Older endpoint pattern; the
  conversations-based endpoint is the current documented approach.
- Webhooks for outbound: Webhooks are for inbound event
  notifications, not outbound message sending.

## R-004: Guesty Communication Channels (Module Types)

**Decision**: Support the following module types for channel
selection: `email`, `sms`, `airbnb2`, `platform` (booking platform
native), and `whatsapp`. Default to the conversation's primary
channel when no channel is specified.

**Rationale**: Guesty's messaging system uses "module types" to
determine the delivery channel. Available channels depend on the
booking source and guest contact information. The `platform` type
routes through the OTA's native messaging (Booking.com, Vrbo, etc.).
Owner conversations do not support `airbnb2`; messages sent with
`platform` type on owner conversations are delivered via email.

**Alternatives considered**:

- Support only platform default: Would prevent channel-specific
  routing (e.g., urgent SMS vs. detailed email), which is a P2
  requirement in the spec.
- Enumerate all OTA-specific channels: The `platform` type already
  abstracts OTA routing; enumerating each OTA would add complexity
  without user benefit.

## R-005: Home Assistant Notify Entity Platform

**Decision**: Implement the modern `NotifyEntity` platform (not the
legacy `BaseNotificationService`). Subclass
`homeassistant.components.notify.NotifyEntity` and implement
`async_send_message(message, title)`.

**Rationale**: Home Assistant introduced entity-based notifications
in 2024, replacing the legacy service-based pattern. `NotifyEntity`
provides entity-centric targeting (via `entity_id`), proper state
tracking (last message timestamp), and integration with the modern
automation editor. The legacy `BaseNotificationService` is being
phased out.

**Alternatives considered**:

- Legacy `BaseNotificationService`: Deprecated pattern; new
  integrations should use `NotifyEntity`.
- Custom service without entity: Would not integrate with HA's
  standard notify infrastructure, breaking automation editor
  compatibility and service call conventions.

## R-006: Service Call Data Structure

**Decision**: Use the `notify.send_message` service call with
`message` as the primary payload. Pass `reservation_id`, `channel`,
and `template_variables` via the service call data. The `title`
parameter is accepted but not used by Guesty's messaging API (logged
and discarded).

**Rationale**: The `NotifyEntity.async_send_message()` method
receives `message` and `title` as parameters. Additional data
(reservation target, channel, template variables) must be passed
through the service call's `data` field, which HA makes available
to the entity. This follows the pattern used by other HA notify
integrations that require target-specific parameters.

**Alternatives considered**:

- Reservation ID in `target`: The modern notify entity pattern
  does not use `target` in service data; entity targeting is done
  via `entity_id`.
- Separate custom service: Would duplicate the notify
  infrastructure and confuse users who expect
  `notify.send_message`.

## R-007: Template Variable Substitution

**Decision**: Use Python `str.format_map()` with a strict
`SafeDict` wrapper that raises `KeyError` on missing keys. Template
syntax uses `{variable_name}` placeholders. Substitution occurs in
the integration before sending to Guesty.

**Rationale**: The spec requires local template substitution with
strict validation — partially rendered messages must never be sent
(FR-010). Python's `str.format_map()` provides the substitution
mechanism; wrapping the variables dict to raise on missing keys
ensures strict validation. This is simpler than Jinja2 and avoids
introducing a template engine dependency.

**Alternatives considered**:

- Jinja2 templates: Overly complex for simple variable
  substitution; introduces a dependency; Jinja2's permissive
  handling of undefined variables (rendering as empty string)
  conflicts with the strict validation requirement.
- Python `str.format(**kwargs)`: Functionally similar but
  `format_map()` with a custom mapping gives better control over
  error messages for missing variables.
- `string.Template`: Uses `$variable` syntax which is less
  intuitive for property managers; `safe_substitute()` would allow
  partial rendering, violating FR-010.

## R-008: Existing API Client Reuse

**Decision**: The new `GuestyMessagingClient` receives the existing
`GuestyApiClient` via dependency injection and delegates all HTTP
communication to it. No new HTTP client or authentication logic is
created.

**Rationale**: Feature 001 established a fully functional
`GuestyApiClient` with token management, rate limit handling, retry
logic, and connection error handling. The messaging client needs
only to construct the correct request payloads and interpret
responses — all HTTP mechanics are handled by the existing client.
This avoids duplicating rate limit, retry, and auth logic.

**Alternatives considered**:

- Direct `httpx.AsyncClient` usage: Would bypass the token
  management, rate limit, and retry logic built into
  `GuestyApiClient`, requiring reimplementation.
- Extending `GuestyApiClient` with messaging methods: Would
  violate single responsibility; the API client handles HTTP
  mechanics while the messaging client handles messaging domain
  logic.

## R-009: Error Handling Strategy

**Decision**: Introduce `GuestyMessageError` as a new exception
subclass of `GuestyApiError` for messaging-specific failures
(conversation not found, message rejected, channel unavailable).
The notify entity maps exceptions to HA-appropriate error handling
(logging + `HomeAssistantError` for user-facing errors).

**Rationale**: Messaging failures have different semantics than
auth or connection errors. A conversation not found for a
reservation is a user input error, not a system failure. A channel
unavailable error should suggest alternatives. These require
distinct handling from the existing exception types.

**Alternatives considered**:

- Reuse `GuestyResponseError` for all messaging failures: Would
  lose the messaging-specific context (reservation ID, available
  channels) needed for actionable error messages.
- Return result objects instead of exceptions: Would require
  checking return values everywhere; exceptions provide cleaner
  error propagation through the call stack.

## R-010: Input Validation Strategy

**Decision**: Validate all inputs in the `GuestyMessagingClient`
before making API calls. Validate reservation ID is non-empty
string, message body is non-empty and within length limits, and
channel (if specified) is a known type. Raise `ValueError` for
invalid inputs.

**Rationale**: Local validation before API calls provides faster
error feedback (<2s per SC-005), reduces unnecessary API calls
(preserving rate limit budget), and produces clearer error messages
than Guesty API error responses. The `api/` package validates using
standard Python exceptions (`ValueError`) to maintain zero HA
imports.

**Alternatives considered**:

- Rely on Guesty API validation: Slower (network round-trip),
  less specific error messages, wastes API rate limit budget.
- Pydantic models for validation: Adds a dependency to the `api/`
  package; frozen dataclasses with `__post_init__` validation
  (matching existing `CachedToken` pattern) are sufficient.

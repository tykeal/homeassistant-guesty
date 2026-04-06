<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Reservations

**Feature Branch**: `003-reservations`
**Created**: 2025-07-25
**Status**: Draft
**Input**: User description: "Fetch and expose reservation/booking data from
the Guesty API in Home Assistant including guest stays, check-in/check-out
dates, guest information, financial data, and booking status for automations,
display, and property management integration."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reservations per Property (Priority: P1)

As a property manager, I want to see the current and upcoming reservations for
each of my properties displayed as sensors on the listing device in Home
Assistant, so I can quickly check who is staying and when the next guest arrives
without opening the Guesty dashboard.

**Why this priority**: This is the foundational capability of the feature —
without reservation data appearing on listing devices, no other reservation
functionality (automations, guest details, financial tracking) is possible. It
delivers immediate value by showing occupancy status at a glance.

**Independent Test**: Can be fully tested by configuring the integration with
valid Guesty credentials and verifying that each listing device shows a
reservation status sensor indicating the current occupancy state and
check-in/check-out dates.

**Acceptance Scenarios**:

1. **Given** the integration is configured and listings exist, **When** a
   listing has a confirmed future reservation, **Then** a reservation status
   sensor on that listing's device shows "awaiting_checkin" with the guest's
   check-in and check-out dates as attributes.
2. **Given** a listing has an active reservation (guest currently checked in),
   **When** the reservation data refreshes, **Then** the reservation status
   sensor shows "checked_in" with the current guest's name and check-out date
   as attributes.
3. **Given** a listing has no current or upcoming reservations within the
   configured lookahead window, **When** the reservation data refreshes,
   **Then** the reservation status sensor shows "no_reservation".
4. **Given** a reservation transitions from confirmed to checked in, **When**
   the next data refresh occurs, **Then** the sensor state changes from
   "awaiting_checkin" to "checked_in" and a state-change event fires for use
   in automations.

---

### User Story 2 - Check-in and Check-out Automations (Priority: P1)

As a property manager, I want Home Assistant automations to trigger based on
reservation check-in and check-out events, so I can automate tasks like
adjusting thermostats, unlocking doors, or sending welcome messages when guests
arrive or depart.

**Why this priority**: Automations based on reservation state changes are the
primary reason users integrate booking data into Home Assistant. Without this,
the integration is merely informational and loses its core value proposition of
enabling smart property management.

**Independent Test**: Can be fully tested by creating an automation that
triggers on the reservation status sensor state change (e.g., from
"awaiting_checkin" to "checked_in") and verifying it fires correctly when
reservation data updates.

**Acceptance Scenarios**:

1. **Given** an automation is configured to trigger on reservation status
   changes, **When** a reservation status transitions to "checked_in",
   **Then** the automation fires and the trigger context includes the listing
   identifier, guest name, and check-out date.
2. **Given** an automation is configured to trigger on "checked_out" status,
   **When** a guest checks out and the data refreshes, **Then** the automation
   fires so the user can initiate cleaning workflows or reset smart home
   devices.
3. **Given** a reservation is canceled in Guesty, **When** the data refreshes,
   **Then** the reservation status changes to "canceled" and any automations
   listening for that state transition fire accordingly.

---

### User Story 3 - Guest Information Access (Priority: P2)

As a property manager, I want to see guest details (name, phone number, email)
for the current and next upcoming reservation on each property, so I can
quickly contact guests or verify identity without switching to the Guesty
portal.

**Why this priority**: Guest contact information is the second most valuable
data point after check-in/out schedules. It enables direct guest communication
workflows and integrates with notification features (Feature 005). However, the
core reservation status from P1 stories is usable without guest details.

**Independent Test**: Can be fully tested by checking the attributes of the
reservation sensor on a listing device and verifying that guest name, phone,
and email are present for the current or upcoming reservation.

**Acceptance Scenarios**:

1. **Given** a listing has a confirmed reservation with guest details in
   Guesty, **When** the reservation data loads, **Then** the reservation sensor
   attributes include the guest's full name, phone number, and email address.
2. **Given** a reservation exists but the guest has not provided a phone
   number, **When** the reservation data loads, **Then** the guest phone
   attribute is absent or shows as unavailable, while other available guest
   details are still displayed.
3. **Given** multiple upcoming reservations exist for a listing, **When** a
   user views the reservation sensor, **Then** the sensor state reflects the
   nearest active/upcoming reservation and an attribute lists upcoming
   reservation summaries (guest name, check-in date, check-out date) for the
   next several bookings.

---

### User Story 4 - Track Reservation Financial Summary (Priority: P3)

As a property manager, I want to see financial summary data (total price,
balance due, currency) for reservations as diagnostic information on each
listing, so I can monitor revenue and payment status alongside property
operations.

**Why this priority**: Financial data is useful for dashboards and reporting
but is not essential for property management automations. It serves a
monitoring and visibility purpose. Marking it as diagnostic keeps the primary
entity list clean while making the data accessible to power users.

**Independent Test**: Can be fully tested by verifying that diagnostic-category
sensors on the listing device display the total price, balance due, and
currency for the current or upcoming reservation.

**Acceptance Scenarios**:

1. **Given** a reservation has financial data in Guesty (total price, balance
   due, currency), **When** the reservation data loads, **Then** diagnostic
   sensors on the listing device show the total price, balance due, and
   currency.
2. **Given** a reservation has no financial data available, **When** the
   reservation data loads, **Then** the financial diagnostic sensors show as
   unavailable rather than displaying misleading zero values.

---

### User Story 5 - Graceful Handling of Data Refresh Failures (Priority: P2)

As a property manager, I want reservation data to remain available even when
the Guesty API is temporarily unreachable, so that my automations and
dashboards continue to function during brief outages.

**Why this priority**: Reliability during API outages is critical for a home
automation system that may control physical devices (locks, thermostats). If
reservation data disappears during a transient API failure, automations could
misfire (e.g., locking a door because it thinks no guest is checked in). This
directly impacts the usability of P1 stories.

**Independent Test**: Can be fully tested by simulating a Guesty API failure
after initial data load and verifying that existing reservation data remains
displayed with a staleness indicator.

**Acceptance Scenarios**:

1. **Given** reservation data has been successfully fetched previously, **When**
   a subsequent data refresh fails due to a network or API error, **Then** the
   last known good reservation data is retained and sensors continue to report
   their previous state.
2. **Given** the data refresh has failed, **When** the sensors are displaying
   stale data, **Then** the integration logs a warning and the entity
   availability reflects the coordinator's error state so the user can be
   aware of the issue.
3. **Given** the API becomes reachable again after a temporary failure, **When**
   the next scheduled refresh succeeds, **Then** reservation data updates to
   the latest state and any staleness indicators clear.

---

### Edge Cases

- What happens when a listing is deleted in Guesty but still has future
  reservations? The reservation sensors on that listing's device become
  unavailable and do not create orphaned entities.
- What happens when a reservation's listing ID does not match any known listing
  device? The reservation data is logged as a warning and skipped — no phantom
  devices are created.
- How does the system handle overlapping reservations on the same listing
  (e.g., same-day turnover)? The reservation status sensor uses the
  chronologically nearest active reservation; overlapping bookings are exposed
  as upcoming reservation attributes.
- What happens when a reservation has no check-in or check-out time set? The
  system falls back to the listing's default check-in/check-out times; if
  those are also absent, dates are used without times.
- How does the system handle hundreds of reservations across many listings?
  Data is fetched with pagination and stored efficiently; only reservations
  within the configured date range window are retained.
- What happens when Guesty returns a reservation status not in the known set
  (e.g., a new status added by Guesty)? The system passes through the unknown
  status value as-is and logs an informational message, rather than failing.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST fetch reservations from the Guesty API with
  pagination support, processing all pages until complete.
- **FR-002**: System MUST fetch reservations filtered to a configurable date
  range window — defaulting to 30 days in the past through 365 days in the
  future from the current date.
- **FR-003**: System MUST retain and process reservations with known
  actionable statuses: confirmed, checked_in, checked_out, and canceled.
  Known non-actionable statuses, specifically inquiry and reserved, MUST
  be excluded by default. Unrecognized or newly introduced Guesty
  statuses MUST NOT be excluded solely for being unknown and MUST be
  passed through as-is.
- **FR-004**: System MUST associate each reservation with its parent listing
  device from Feature 002, using the listing identifier as the linkage key.
- **FR-005**: System MUST expose a reservation status sensor on each listing
  device that reflects the current occupancy or reservation lifecycle
  state: "no_reservation", "awaiting_checkin", "checked_in",
  "checked_out", or "canceled".
- **FR-006**: The reservation status sensor MUST derive its state from
  the chronologically nearest relevant reservation, with the following
  priority: the currently active reservation (`checked_in`) takes
  priority, followed by the next upcoming confirmed reservation
  (`awaiting_checkin`), followed by the nearest completed reservation
  (`checked_out`) or canceled reservation (`canceled`) when no active
  or upcoming confirmed reservation applies.
- **FR-007**: System MUST expose check-in and check-out dates and times as
  attributes on the reservation status sensor for the current or next
  reservation.
- **FR-008**: System MUST expose guest information (full name, phone number,
  email address) as attributes on the reservation status sensor for the current
  or next reservation.
- **FR-009**: System MUST expose a list of upcoming reservation summaries
  (guest name, check-in date, check-out date, status) as an attribute on the
  reservation status sensor.
- **FR-010**: System MUST expose financial data (total price, balance due,
  currency) as diagnostic-category sensors on the listing device.
- **FR-011**: System MUST expose a confirmation code attribute on the
  reservation status sensor when available.
- **FR-012**: System MUST update reservation data periodically using the same
  polling coordinator pattern established in Feature 002 (Listings), with a
  configurable refresh interval.
- **FR-013**: The default reservation polling interval MUST be 15 minutes, with
  a configurable minimum of 5 minutes.
- **FR-014**: System MUST retain the last known good reservation data when a
  data refresh fails, and restore it on the next successful refresh.
- **FR-015**: System MUST fire state-change events when the reservation status
  sensor transitions between states, enabling automations to trigger on
  check-in, check-out, and cancellation events.
- **FR-016**: System MUST handle listings that have no reservations in the
  configured date range by showing "no_reservation" status without errors.
- **FR-017**: System MUST skip reservations whose listing ID does not match any
  known listing device, logging a warning rather than creating orphaned
  entities.
- **FR-018**: System MUST support same-day turnovers by using the
  chronologically nearest reservation for the primary sensor state and listing
  additional reservations in the upcoming summaries attribute.
- **FR-019**: System MUST gracefully handle reservation records with missing
  optional fields (guest phone, email, financial data, notes) by omitting
  those attributes or marking them unavailable rather than failing.
- **FR-020**: System MUST expose the number of guests (guest count) as an
  attribute on the reservation status sensor when available.
- **FR-021**: System MUST expose the booking source (e.g., Airbnb,
  Booking.com, direct) as an attribute on the reservation status sensor when
  available.
- **FR-022**: System MUST expose the number of nights as an attribute on the
  reservation status sensor when available.
- **FR-023**: System MUST properly clean up all reservation entities and
  coordinator resources when the integration is removed or a listing device is
  removed.
- **FR-024**: All reservation data processing in the library layer MUST have
  zero Home Assistant imports — maintaining the library-shim architecture
  pattern.
- **FR-025**: System MUST pass through unrecognized reservation statuses as-is
  and log an informational message, rather than raising errors.

### Key Entities

- **Reservation**: A booking/stay record linking a guest to a listing for a
  specific date range. Key attributes: unique identifier, listing reference,
  status (confirmed, checked_in, checked_out, canceled), check-in date/time,
  check-out date/time, confirmation code, guest count, nights count, booking
  source, and notes. A reservation always belongs to exactly one listing.
- **Guest**: A person associated with a reservation. Key attributes: full name,
  phone number, email address, and guest identifier. A guest is always
  referenced through their reservation.
- **Reservation Financial Summary**: Payment and pricing data associated with a
  reservation. Key attributes: total price, balance due, and currency. This is
  ancillary diagnostic data attached to the reservation, not a standalone
  entity.
- **Listing Device** (from Feature 002): The parent device representing a Guesty
  property. Reservation sensors attach to the listing device using the listing
  identifier as the relationship key.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can view the current reservation status for any listing
  within 30 seconds of the integration loading for the first time.
- **SC-002**: Reservation status changes (check-in, check-out, cancellation)
  are reflected in Home Assistant within one polling interval (default 15
  minutes) of the change occurring in Guesty.
- **SC-003**: Automations triggered by reservation status changes fire reliably
  for 100% of detected state transitions.
- **SC-004**: The system handles properties with 50 or more reservations in the
  date range window without user-perceptible delay during data refresh.
- **SC-005**: Guest contact information (name, phone, email) is accessible on
  the listing device within two clicks/taps from the Home Assistant dashboard.
- **SC-006**: During a Guesty API outage lasting up to 1 hour, all reservation
  sensors retain their last known state and no automations misfire due to
  missing data.
- **SC-007**: Financial summary data for reservations is available as
  diagnostic sensors with no impact on the primary sensor entity list.
- **SC-008**: The system supports 100 or more listings with reservations across
  a single integration instance without degraded refresh performance.
- **SC-009**: Zero orphaned entities are created when reservations reference
  listings that no longer exist.
- **SC-010**: Users can configure the reservation polling interval
  independently, with changes taking effect within one polling cycle.

## Assumptions

- Feature 002 (Listings & Properties) is implemented and listing devices are
  available before reservation sensors are created. Reservations depend on
  listing devices as their parent.
- The Guesty API provides reservation data via the `/reservations` endpoint
  with support for filtering by listing ID, status, and date ranges, consistent
  with Guesty's published OpenAPI documentation.
- Guest contact information (phone, email) availability depends on what the
  booking channel provides to Guesty — not all reservations will have complete
  guest contact details.
- Financial data availability and structure depends on the Guesty account
  configuration — some accounts may not expose payment details via the API.
- The integration reuses the existing authenticated API client from Feature 1
  (Auth & Config Flow) — no separate authentication is needed for reservation
  endpoints.
- Reservation statuses returned by the Guesty API follow the known set
  (confirmed, checked_in, checked_out, canceled, inquiry, reserved) but may
  include additional statuses as Guesty evolves its platform.
- The default date range window of 30 days past to 365 days future is
  sufficient for typical property management needs. Users requiring different
  ranges can adjust configuration.
- Polling-based data refresh is acceptable for reservation updates; real-time
  webhook-based updates are out of scope for this feature and may be addressed
  in a future feature.
- Same-day turnovers (check-out and check-in on the same day) are a common
  scenario that must be handled correctly rather than treated as an edge case.
- The number of reservations per listing within the date range window is
  expected to be in the tens to low hundreds, not thousands.

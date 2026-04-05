<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Listings/Properties

**Feature Branch**: `002-listings-properties`
**Created**: 2025-07-18
**Status**: Draft
**Input**: User description: "Listings/Properties — fetch Guesty
listings and expose them as Home Assistant entities"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - View Listing Status in Home Assistant (Priority: P1)

A Home Assistant user has already configured the Guesty integration
(Feature 001). After setup, the integration automatically fetches all
listings (properties) from their Guesty account and creates a device
for each listing in Home Assistant. Each device exposes a primary
sensor showing the listing's operational status (active, inactive, or
archived). The user can see all their Guesty properties at a glance in
the Home Assistant Devices view and use the status sensor in
automations (e.g., notify when a listing becomes inactive).

**Why this priority**: Listing discovery and status visibility are
the foundational data layer. Without fetching listings, no other
Guesty entity types (reservations, calendars, guests) can be
associated with a property. The status sensor provides immediate
actionable value for property managers.

**Independent Test**: Can be fully tested by configuring the
integration and verifying that each Guesty listing appears as a
device with a status sensor in Home Assistant. Delivers the ability
to monitor property status and trigger automations on status changes.

**Acceptance Scenarios**:

1. **Given** the Guesty integration is configured and authenticated,
   **When** the integration performs its initial data fetch, **Then**
   one device is created in Home Assistant for each listing returned
   by the Guesty API.
2. **Given** listings have been fetched, **When** the user views the
   Devices page, **Then** each Guesty listing appears as a distinct
   device with its name displayed.
3. **Given** a listing device exists, **When** the user views the
   device detail page, **Then** a status sensor shows the listing's
   current operational status (active, inactive, or archived).
4. **Given** a listing's status changes in Guesty, **When** the next
   data refresh occurs, **Then** the status sensor in Home Assistant
   updates to reflect the new status.
5. **Given** the Guesty account has no listings, **When** the
   integration fetches data, **Then** the integration loads
   successfully with zero devices and logs an informational message.

---

### User Story 2 - Automatic Periodic Data Refresh (Priority: P1)

A Home Assistant user expects their Guesty listing data to stay
current without manual intervention. The integration periodically
polls the Guesty API at a configurable interval (default: 15 minutes)
to refresh listing data. When listing details change in Guesty
(e.g., a property is renamed, status changes, or a new listing is
added), the changes appear in Home Assistant after the next refresh
cycle. The user can adjust the polling interval through the
integration options flow to balance freshness against API usage.

**Why this priority**: Without automatic refresh, listing data
becomes stale immediately after initial fetch, making the integration
unreliable for automations that depend on current property state.
This is equally critical to Story 1 because static data has limited
value for a monitoring integration.

**Independent Test**: Can be tested by modifying a listing in Guesty,
waiting for a refresh interval, and verifying the change appears in
Home Assistant without user intervention.

**Acceptance Scenarios**:

1. **Given** the integration is running, **When** the configured
   refresh interval elapses, **Then** the integration fetches updated
   listing data from the Guesty API.
2. **Given** a new listing is created in Guesty, **When** the next
   data refresh occurs, **Then** a new device and its sensors appear
   in Home Assistant.
3. **Given** a listing is deleted or archived in Guesty, **When** the
   next data refresh occurs, **Then** the corresponding sensors
   update to reflect the new state.
4. **Given** the user navigates to the integration options, **When**
   the user changes the refresh interval, **Then** subsequent refresh
   cycles use the new interval.
5. **Given** the refresh interval is set below an enforced minimum,
   **When** the user saves the options, **Then** the system enforces
   the minimum interval and informs the user.

---

### User Story 3 - Rich Property Detail Sensors (Priority: P2)

A Home Assistant user wants to see detailed property information for
each Guesty listing without logging into the Guesty dashboard. Each
listing device exposes sensors for property details: listing name,
nickname, full address, property type, room type, number of bedrooms,
number of bathrooms, timezone, check-in time, and check-out time.
These sensors enable automations based on property characteristics
(e.g., adjust smart home scenes based on property type or timezone).

**Why this priority**: Detailed property sensors transform the
integration from a simple status monitor into a comprehensive
property management dashboard within Home Assistant. However, the
core status sensor (Story 1) provides standalone value first.

**Independent Test**: Can be tested by verifying each listing device
exposes the expected set of detail sensors with values matching the
Guesty dashboard for that property.

**Acceptance Scenarios**:

1. **Given** a listing device exists in Home Assistant, **When** the
   user views the device detail page, **Then** sensors are visible
   for listing name, nickname, address, property type, room type,
   bedrooms, bathrooms, timezone, check-in time, and check-out time.
2. **Given** a listing in Guesty has optional fields that are empty
   (e.g., no nickname, no check-in time), **When** the device is
   created, **Then** the corresponding sensors show an appropriate
   "unknown" or "not set" state rather than causing errors.
3. **Given** a listing's details change in Guesty (e.g., nickname
   updated), **When** the next data refresh occurs, **Then** the
   corresponding sensor updates to the new value.
4. **Given** a listing has a full address, **When** the address sensor
   is viewed, **Then** the address is displayed as a human-readable
   formatted string.

---

### User Story 4 - Listing Tags and Custom Fields (Priority: P3)

A Home Assistant user uses Guesty tags and custom fields to organize
their properties (e.g., tagging properties by region, adding custom
fields for maintenance status). The integration exposes tags as a
sensor attribute and custom fields as additional sensors, allowing
the user to build automations based on their custom categorization
(e.g., notify the cleaning team when a property tagged "premium" has
a new reservation).

**Why this priority**: Tags and custom fields provide advanced
organizational capabilities but are not universally used. Core
listing data (Stories 1-3) delivers value to all users, while this
story serves power users who leverage Guesty's customization
features.

**Independent Test**: Can be tested by adding tags and custom fields
to a listing in Guesty and verifying they appear as sensor attributes
in Home Assistant.

**Acceptance Scenarios**:

1. **Given** a listing in Guesty has tags assigned, **When** the
   listing device is viewed in Home Assistant, **Then** a sensor
   displays the tags as a list.
2. **Given** a listing in Guesty has custom fields defined, **When**
   the listing device is viewed, **Then** each custom field is
   exposed as a sensor with the custom field name and value.
3. **Given** a listing has no tags or custom fields, **When** the
   device is viewed, **Then** the tags sensor shows an empty list
   and no custom field sensors are created.
4. **Given** a tag is added to a listing in Guesty, **When** the next
   data refresh occurs, **Then** the tags sensor updates to include
   the new tag.

---

### User Story 5 - Graceful Degradation During Errors (Priority: P2)

A Home Assistant user experiences a temporary network outage or the
Guesty API returns errors during a scheduled data refresh. The
integration retains the last known good data and continues operating
with stale values rather than removing entities or showing errors for
every sensor. The user sees a clear indication that data may be stale
(e.g., through Home Assistant's standard "unavailable" mechanisms for
the coordinator) and receives log messages describing the issue. When
connectivity is restored, the next refresh cycle resumes normal
operation automatically.

**Why this priority**: Property management automations must be
resilient. A brief API outage should not cause cascading failures in
Home Assistant automations that depend on listing data. This is
essential for production reliability alongside the core data fetch.

**Independent Test**: Can be tested by simulating API errors during
a refresh cycle and verifying that existing entities retain their
last known values and recover automatically when the API becomes
available.

**Acceptance Scenarios**:

1. **Given** the integration has successfully fetched listing data,
   **When** a subsequent refresh fails due to a network error,
   **Then** all existing sensors retain their last known values.
2. **Given** a refresh has failed, **When** the user checks the
   integration status, **Then** the integration shows a warning
   indicating data may be stale.
3. **Given** a refresh has failed, **When** the next scheduled
   refresh succeeds, **Then** all sensors update to current values
   and the stale data warning clears.
4. **Given** the Guesty API returns rate limit errors during refresh,
   **When** the integration handles the error, **Then** it relies
   on the existing retry and backoff mechanisms from the API client
   and reports the issue if retries are exhausted.
5. **Given** the initial data fetch on startup fails, **When** the
   integration starts, **Then** the integration logs the error,
   sets itself as unavailable, and retries on the next refresh
   cycle.

---

### Edge Cases

- What happens when a Guesty account has hundreds of listings? The
  integration must paginate through all pages (up to 100 per request)
  and create devices for every listing without timeout or memory
  issues.
- What happens when the Guesty API returns a listing with missing or
  malformed required fields (e.g., no ID)? The integration should
  skip that listing, log a warning with context, and continue
  processing remaining listings.
- What happens when a listing that previously existed in Guesty is
  no longer returned by the API (deleted)? Existing devices and
  sensors should reflect the listing is no longer available rather
  than silently disappearing.
- What happens when the Guesty API response format changes
  unexpectedly (e.g., new fields, removed fields, changed types)?
  The integration should handle missing optional fields gracefully
  and raise clear errors for missing required fields.
- What happens when the data refresh is triggered while a previous
  refresh is still in progress? The coordinator must prevent
  concurrent refresh operations.
- What happens when the user removes the integration? All devices
  and sensors associated with Guesty listings must be cleaned up.
- What happens when listing data includes special characters or very
  long strings in names or addresses? Sensors must handle these
  values without truncation or encoding errors.
- What happens when the Guesty API returns an empty results array
  with a non-zero count (pagination mismatch)? The integration
  should trust the actual results returned rather than the count
  metadata.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The integration MUST fetch all listings from the Guesty
  API using the listings endpoint with pagination support (limit and
  skip parameters).
- **FR-002**: The integration MUST handle pagination by requesting up
  to 100 listings per page and continuing with subsequent pages until
  all listings have been retrieved.
- **FR-003**: The integration MUST create one Home Assistant device
  per Guesty listing, using the listing's unique identifier as the
  device identifier.
- **FR-004**: Each listing device MUST expose a sensor for the
  listing's operational status with possible values: active,
  inactive, or archived.
- **FR-005**: Each listing device MUST expose sensors for listing
  name, nickname, formatted address, property type, room type,
  number of bedrooms, number of bathrooms, timezone, check-in time,
  and check-out time.
- **FR-006**: Each listing device MUST expose a sensor for tags
  (as a list) and individual sensors for each custom field.
- **FR-007**: The integration MUST use a data update coordinator to
  periodically refresh listing data at a configurable interval with
  a default of 15 minutes.
- **FR-008**: The refresh interval MUST be configurable through the
  integration's options flow, with an enforced minimum to prevent
  excessive API calls.
- **FR-009**: The integration MUST retain the last known good data
  when a refresh cycle fails, allowing sensors to continue reporting
  their previous values.
- **FR-010**: The integration MUST skip listings that are missing a
  unique identifier and log a warning for each skipped listing.
- **FR-011**: The integration MUST handle optional listing fields
  gracefully, displaying an appropriate "unknown" state when a field
  is not present in the API response.
- **FR-012**: Each listing device MUST provide device information
  (manufacturer, model, name) following Home Assistant device
  registry conventions.
- **FR-013**: All listing sensors MUST follow modern Home Assistant
  entity patterns: entity name derivation from the device, and
  translation support for sensor names.
- **FR-014**: The integration MUST use the existing API client
  infrastructure (authentication, retry, rate limit handling) for
  all listing API calls.
- **FR-015**: The API client methods for listing retrieval MUST
  remain free of Home Assistant imports to preserve library
  extractability.
- **FR-016**: The integration MUST clean up all devices and entities
  when the integration entry is removed.
- **FR-017**: The integration MUST log informational messages when
  no listings are found and warning messages when listings are
  skipped due to data issues.
- **FR-018**: When a listing that was previously tracked is no longer
  returned by the API, the integration MUST update the listing's
  state to reflect it is no longer available.
- **FR-019**: The integration MUST prevent concurrent refresh
  operations from overlapping.
- **FR-020**: All data fetching and entity update operations MUST
  use asynchronous patterns and MUST NOT block the Home Assistant
  event loop.

### Key Entities

- **Listing**: Represents a single Guesty property. Key attributes
  include a unique identifier, display name, nickname, operational
  status (active/inactive/archived), full address, property type
  (e.g., apartment, house, villa), room type (e.g., entire
  home, private room, shared room), bedroom count, bathroom count,
  timezone, check-in time, check-out time, tags (list of strings),
  and custom fields (name-value pairs). Each listing is the root
  entity that all future Guesty data (reservations, calendars) will
  associate with.
- **Listing Address**: A structured sub-entity of a Listing
  containing street, city, state/province, postal code, and country.
  Displayed as a formatted human-readable string on the address
  sensor.
- **Custom Field**: A user-defined name-value pair associated with a
  Listing. The name serves as the sensor identifier and the value as
  the sensor state. Custom fields vary per Guesty account and per
  listing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All listings in a Guesty account are discoverable as
  Home Assistant devices within two refresh cycles after integration
  setup.
- **SC-002**: Listing data refreshes automatically at the configured
  interval with zero user intervention required.
- **SC-003**: Listing status changes in Guesty are reflected in Home
  Assistant within one refresh cycle (default: 15 minutes).
- **SC-004**: The integration handles accounts with 500 or more
  listings without timeout or memory issues during data fetch.
- **SC-005**: 100% of refresh failures (network errors, API errors,
  rate limits) are handled gracefully with the last known good data
  retained and clear log messages produced.
- **SC-006**: Users can adjust the refresh interval through the
  integration options without reconfiguring the integration.
- **SC-007**: Listings with missing optional fields display
  appropriate "unknown" states rather than causing errors or missing
  sensors.
- **SC-008**: Data fetching and entity updates impose no measurable
  degradation on Home Assistant responsiveness during normal
  operation.
- **SC-009**: All listing-related functionality can be validated
  through automated tests without requiring a live Guesty API
  connection.
- **SC-010**: Removing the integration cleanly removes all Guesty
  listing devices and sensors from Home Assistant.

## Assumptions

- The Guesty integration authentication and API client infrastructure
  (Feature 001) is fully operational, including token management,
  retry logic, and rate limit handling.
- The Guesty API listings endpoint follows the documented pagination
  pattern (limit/skip parameters, maximum 100 results per page) and
  returns data in the documented response format.
- Listing data changes infrequently enough that a 15-minute default
  polling interval provides adequate freshness for typical property
  management workflows.
- The Guesty account has a manageable number of listings (hundreds,
  not tens of thousands) appropriate for a Home Assistant polling
  integration. Accounts with extremely large listing counts may
  require longer refresh intervals.
- Custom fields in Guesty have string-representable values suitable
  for display as Home Assistant sensor states.
- The API client's existing rate limit handling and retry logic are
  sufficient for the additional API load introduced by listing
  fetches. No separate rate limit budget is needed for this feature.
- Entity cleanup on integration removal is handled through standard
  Home Assistant mechanisms (config entry unload and device registry
  cleanup).
- The minimum Home Assistant version specified in the integration
  manifest supports the modern entity patterns used (entity name
  derivation from device, translation keys, coordinator entity base
  class).

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Listing Filtering

**Feature Branch**: `007-listing-filtering` **Created**: 2025-07-24 **Status**:
Draft **Input**: User description: "Add listing filtering to the Guesty Home
Assistant integration. The integration currently fetches ALL listings from the
Guesty API and creates devices/sensors for every one. Users managing multiple
properties across different locations need the ability to filter which listings
are tracked by a given HA instance. The feature should provide two filtering
mechanisms in the options flow: (1) a listing selector multi-select, and (2)
tag-based filtering."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Select Specific Listings to Track (Priority: P1)

A property manager has 20 Guesty listings spread across three cities. They run a
dedicated Home Assistant instance at one of those locations and only want to
track the 5 listings physically near that HA hub. After initial setup (which
currently loads all 20 listings), they open the integration options flow, see
all 20 listings displayed by name and address, deselect the 15 they do not need,
and save. On the next data refresh, only the 5 selected listings remain as
devices. The deselected listings and all their associated sensors (reservations,
financials, custom fields) are cleanly removed from Home Assistant.

**Why this priority**: This is the core value proposition of the feature.
Without listing selection, users are forced to track every property, cluttering
dashboards and wasting resources. It delivers immediate, tangible value and can
function without tag-based filtering.

**Independent Test**: Can be fully tested by opening the options flow, toggling
listing selections, and verifying that only selected listings appear as devices
with the correct associated sensors.

**Acceptance Scenarios**:

1. **Given** an existing installation tracking all listings, **When** the user
   opens options and deselects specific listings then saves, **Then** only the
   selected listings have devices and sensors in Home Assistant after the next
   refresh.
2. **Given** a user has deselected some listings, **When** the coordinator
   refreshes data, **Then** devices, reservation sensors, financial sensors, and
   custom field sensors for deselected listings are removed from Home Assistant.
3. **Given** an existing installation with no prior filtering configuration,
   **When** the user opens the options flow for the first time after the feature
   is available, **Then** all listings are preselected (backward compatible
   default).
4. **Given** a user previously deselected listings, **When** they re-open
   options and re-select a previously deselected listing, **Then** that
   listing's devices and sensors are recreated on the next refresh.

---

### User Story 2 — Tag-Based Pre-Filtering (Priority: P2)

A property management company operates 80 Guesty listings organized by region
using Guesty tags (e.g., "miami-beach", "downtown-orlando", "tampa-bay"). A site
manager only cares about the 12 listings tagged "miami-beach". They open the
integration options flow, enter the tag "miami-beach" in the tag filter field,
and the listing selector immediately narrows to only the 12 matching listings.
They confirm the selection and save. Only those 12 listings are tracked.

**Why this priority**: Tag-based filtering is a convenience layer that makes
listing selection practical for users with dozens or hundreds of properties. It
enhances the P1 story but is not required for the core filtering capability.

**Independent Test**: Can be tested by entering tag values in the options flow
and verifying that the listing selector only shows listings whose Guesty tags
match the specified filter.

**Acceptance Scenarios**:

1. **Given** the user opens the options flow, **When** they enter one or more
   tags in the tag filter, **Then** the listing selector only displays listings
   whose Guesty tags include at least one of the specified tags.
2. **Given** no tags are specified in the tag filter, **When** the listing
   selector is displayed, **Then** all available listings appear (default
   behavior).
3. **Given** a tag filter is applied that matches zero listings, **When** the
   listing selector is displayed, **Then** an empty list is shown with a clear
   message indicating no listings match the specified tags.
4. **Given** the user specifies multiple tags, **When** the listing selector is
   displayed, **Then** listings matching any of the specified tags are shown (OR
   logic).

---

### User Story 3 — Backward-Compatible Upgrade (Priority: P1)

A user who has been running the Guesty integration since Feature 1 upgrades to
the version containing listing filtering. They have made no configuration
changes. All of their existing listings, reservation sensors, custom field
sensors, and automations continue to work exactly as before. The options flow
now shows the new filtering options, but because no filtering has been
configured, the integration behaves identically to before the upgrade.

**Why this priority**: Backward compatibility is critical for existing users. A
breaking upgrade would disrupt live property automations and erode trust. This
is co-priority P1 because it is a non-negotiable constraint on the P1 story.

**Independent Test**: Can be tested by upgrading an existing installation with
no configuration changes and verifying all entities, automations, and services
remain functional.

**Acceptance Scenarios**:

1. **Given** an existing installation with no listing filter configuration,
   **When** the integration loads after upgrade, **Then** all listings continue
   to be tracked with no user intervention required.
2. **Given** an existing installation, **When** the user opens the options flow
   after upgrade, **Then** all listings appear preselected and no tag filter is
   applied.
3. **Given** an existing installation with automations referencing specific
   listing sensors, **When** the integration loads after upgrade, **Then** all
   automations continue to trigger correctly with no entity ID changes.

---

### User Story 4 — Clean Removal of Deselected Listing Entities (Priority: P2)

A user deselects a listing that currently has active reservation sensors, custom
field sensors, and financial diagnostic sensors. After saving the options and
the coordinator refreshes, all entities associated with that listing are removed
from the Home Assistant entity registry. No orphaned entities remain. If the
user later re-selects that listing, fresh entities are created.

**Why this priority**: Clean entity lifecycle management prevents dashboard
clutter, stale data, and entity registry bloat. It is essential for the listing
selector (P1) to deliver a polished experience.

**Independent Test**: Can be tested by deselecting a listing with known
associated entities and verifying each entity type is removed from the registry.

**Acceptance Scenarios**:

1. **Given** a listing with reservation, custom field, and status sensors,
   **When** the listing is deselected and the coordinator refreshes, **Then**
   all associated sensors and the device entry are removed from Home Assistant.
2. **Given** a listing was previously deselected and its entities removed,
   **When** the listing is re-selected, **Then** new device and sensor entities
   are created with correct initial state.
3. **Given** a listing is deselected, **When** automations reference the removed
   entities, **Then** those automations report unavailable entities (standard HA
   behavior) rather than causing errors.

---

### User Story 5 — Immediate Refresh After Filter Changes (Priority: P3)

A user changes the listing filter configuration in the options flow and saves.
Rather than waiting up to 15 minutes for the next scheduled coordinator refresh,
the system triggers an immediate data refresh so the user sees their changes
take effect right away.

**Why this priority**: While not strictly necessary (users could wait for the
next refresh), immediate feedback significantly improves the user experience
when adjusting filters. This is a polish feature.

**Independent Test**: Can be tested by modifying the filter, saving, and
measuring how quickly the entity list reflects the change.

**Acceptance Scenarios**:

1. **Given** the user saves new filter settings in the options flow, **When**
   the options flow completes, **Then** a coordinator refresh is triggered
   immediately and entities are updated within seconds.
2. **Given** the user saves filter settings that are identical to the current
   settings, **When** the options flow completes, **Then** no unnecessary
   refresh is triggered.

---

### Edge Cases

- What happens when the Guesty API is unreachable during the options flow when
  fetching the listing/tag data for the selector? The options flow should
  display a clear error message and allow the user to retry or cancel without
  losing existing filter settings.
- What happens when a previously selected listing is deleted from Guesty? The
  coordinator should handle disappeared listings gracefully, consistent with
  existing behavior (Feature 002), and remove the associated entities.
- What happens when a listing's tags change in Guesty after a tag filter was
  configured? The tag filter is applied at options-flow time to populate the
  listing selector. If a listing's tags change later, the listing remains
  tracked as long as it is still in the saved selection list. Tag changes only
  affect what appears in the selector the next time the user opens options.
- What happens when all listings are deselected? The system should prevent the
  user from saving an empty selection; at least one listing must be selected.
- What happens when a new listing is created in Guesty after filtering was
  configured? The new listing is not automatically added to the selection. The
  user must open the options flow and explicitly select it. This prevents
  unexpected new devices from appearing.
- What happens when the user has both tag filtering and specific listing
  selections, and a tag filter change would exclude a currently selected
  listing? The listing selector should reflect the tag filter, and any
  previously selected listings that no longer match the tags should be
  deselected when the tag filter is applied. The user can review and confirm
  before saving.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The integration options flow MUST present a multi-select list of
  all available Guesty listings, allowing the user to choose which listings to
  track.
- **FR-002**: Each listing in the selector MUST be displayed with its name and
  address (city, state/country if available) to enable easy identification.
- **FR-003**: The integration MUST only create devices and sensors for listings
  that are currently selected in the filter configuration.
- **FR-004**: When no filter configuration exists (new install or pre-upgrade),
  ALL listings MUST be selected by default to maintain backward compatibility.
- **FR-005**: The integration MUST persist the selected listing IDs in the
  configuration entry so they survive restarts.
- **FR-006**: When a listing is deselected, the integration MUST remove the
  associated device and ALL sensors tied to that listing, including listing
  status sensors, reservation sensors, financial sensors, and custom field
  sensors.
- **FR-007**: The options flow MUST provide an optional tag filter field where
  the user can specify one or more Guesty tags.
- **FR-008**: When tags are specified, the listing selector MUST only display
  listings whose Guesty tags include at least one of the specified tags (OR
  logic).
- **FR-009**: Tag filtering MUST be applied before the listing selector is
  populated — the tags act as a pre-filter on the available listings.
- **FR-010**: When no tags are specified, all listings MUST appear in the
  selector (default behavior).
- **FR-011**: The options flow MUST fetch current listing data (including tags)
  from the Guesty API when presenting the selector, ensuring the list is up to
  date.
- **FR-012**: The integration MUST prevent the user from saving an empty listing
  selection; at least one listing must be selected.
- **FR-013**: After the user saves changed filter settings, the integration MUST
  trigger an immediate coordinator refresh rather than waiting for the next
  scheduled interval.
- **FR-014**: The integration MUST NOT trigger an unnecessary refresh if the
  filter settings have not changed.
- **FR-015**: New listings created in Guesty after filtering is configured MUST
  NOT be automatically added to the tracked set. The user must explicitly select
  them via the options flow.
- **FR-016**: The ListingsCoordinator MUST respect the configured listing filter
  and only return data for selected listings.
- **FR-017**: The ReservationsCoordinator MUST only fetch and track reservations
  for listings that are in the current filtered selection.
- **FR-018**: The CustomFieldsDefinitionCoordinator MUST continue to function
  independently of listing filtering, as custom field definitions are
  account-wide.
- **FR-019**: The options flow MUST handle Guesty API errors gracefully during
  listing/tag fetching, displaying a clear error message and preserving existing
  filter settings.
- **FR-020**: The integration MUST handle the case where a selected listing is
  deleted from Guesty by removing its entities, consistent with existing
  disappeared-listing behavior.

### Key Entities

- **Listing Filter Configuration**: The set of selected listing IDs and optional
  tag filter values that determine which Guesty listings are tracked. Persisted
  in the config entry options. Contains: selected listing IDs (list), tag filter
  values (list, optional).
- **Filtered Listing**: A Guesty listing that passes both the tag filter (if
  configured) and the listing selection. Represented as a device in Home
  Assistant with associated sensors. Inherits all attributes from the existing
  GuestyListing model (title, nickname, address, status, property type, tags,
  custom fields, etc.).
- **Deselected Listing**: A Guesty listing that exists in the Guesty account but
  has been excluded from tracking by the user's filter configuration. Its device
  and all associated entities are removed from Home Assistant.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can configure listing filters through the options flow and
  see changes reflected in under 30 seconds after saving.
- **SC-002**: Existing installations that upgrade to this version continue
  operating with all listings tracked and zero entity disruptions without any
  user action.
- **SC-003**: Users managing 50+ listings can narrow the selector to relevant
  listings using tags in under 1 minute.
- **SC-004**: When a listing is deselected, 100% of its associated entities
  (device, all sensor types) are removed with zero orphaned entities remaining.
- **SC-005**: The listing selector displays listing names with location
  information, enabling users to identify the correct listing on first attempt
  at least 95% of the time.
- **SC-006**: The integration handles Guesty API errors during the options flow
  without losing existing filter settings or crashing the configuration UI.
- **SC-007**: All filtering logic achieves 100% automated test coverage
  consistent with project standards.

## Assumptions

- The existing Guesty API client's `get_listings()` method returns listing tags
  as part of the response, which is confirmed by the existing `GuestyListing`
  model that includes a `tags: tuple[str, ...]` field.
- The Guesty API does not provide a server-side tag filtering endpoint; tag
  filtering will be applied client-side after fetching listings. If a
  server-side filter becomes available, it can be adopted as an optimization in
  a future iteration.
- The options flow can fetch listing data at flow-display time using the
  existing authenticated API client without creating a new authentication flow
  or separate API session.
- The Home Assistant entity registry and device registry provide standard
  mechanisms for removing devices and entities programmatically, and these
  mechanisms are available to the integration.
- Users have a reasonable number of tags (under 100) such that presenting them
  as a text input (comma-separated or multi-value) is adequate; a tag selector
  dropdown is not required for this version.
- The existing coordinator refresh mechanism (DataUpdateCoordinator) supports
  being triggered on demand in addition to its scheduled interval.
- Listing filtering configuration is per-config-entry, meaning each Guesty
  integration instance (if multiple accounts are configured) has its own
  independent filter settings.
- The existing options flow (which currently handles polling intervals and date
  range configuration) can be extended to include the new filtering steps
  without requiring a separate flow.

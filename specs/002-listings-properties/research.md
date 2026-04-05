<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Listings/Properties

**Feature**: 002-listings-properties
**Date**: 2025-07-18
**Status**: Complete

## R1: Guesty API Listings Endpoint

**Decision**: Use `GET /v1/listings` with skip-based pagination
(limit=100, skip=N) and explicit `fields` parameter to minimize
payload size.

**Rationale**: The Guesty Open API v1 `/listings` endpoint supports
field selection via a `fields` query parameter. Requesting only the
fields we need reduces response payload and API processing time.
The pagination model uses `limit` (max 100) and `skip` (offset)
parameters. The stop condition is `len(results) < limit`, which
indicates the last page. This pattern is validated by the
rentalsync-bridge reference implementation.

**Alternatives considered**:

- *Cursor-based pagination*: Not supported by the Guesty v1
  listings endpoint. Skip-based is the only option.
- *Fetch all fields*: Would return large payloads with many unused
  fields. Explicit `fields` selection is more efficient.
- *Individual listing fetches*: Would require N+1 API calls
  instead of paginated batch fetches. Impractical for accounts
  with hundreds of listings.

## R2: Guesty Listing Status Field

**Decision**: Derive `active` and `inactive` from the Guesty
`listed` and `active` booleans. Reserve `archived` for a
listing state explicitly indicated by Guesty in the listing
payload. A listing no longer returned by `GET /v1/listings`
is **not** mapped to `archived`; it is treated as
`unavailable` per R8 while the retained device remains in
Home Assistant.

**Rationale**: The Guesty API exposes `listed` (boolean) and
`active` (boolean) fields on listing objects. Those fields
derive the non-archived states:

- `active`: `listed=true` and `active=true`
- `inactive`: `listed=false` or `active=false` (still
  returned by API)
- `archived`: Only when Guesty returns an explicit archive
  indicator in the API response

This keeps entity availability separate from listing status.
A missing listing can reflect filtering, permissions, sync
delay, or deletion — not necessarily an archive. The exact
archive field/value mapping should be validated during
implementation against live API responses.

**Alternatives considered**:

- *Expose raw booleans*: Two sensors instead of one; less
  user-friendly for automations.
- *Only use `listed`*: Misses the `active` dimension.
- *Treat "not returned" as `archived`*: Rejected because
  it conflicts with the retained-device/unavailable behavior
  in R8 and makes `archived` ambiguous.

## R3: Listing Detail Field Mapping

**Decision**: Map Guesty API fields to sensor entities as follows:

| Guesty Field | Sensor | Type |
| ------------ | ------ | ---- |
| `title` | Name | diagnostic |
| `nickname` | Nickname | diagnostic |
| `address.full` (fallback: components) | Address | diagnostic |
| `propertyType` | Property type | diagnostic |
| `roomType` | Room type | diagnostic |
| `numberOfBedrooms` | Bedrooms | diagnostic |
| `numberOfBathrooms` | Bathrooms | diagnostic |
| `timezone` | Timezone | diagnostic |
| `defaultCheckInTime` | Check-in time | diagnostic |
| `defaultCheckoutTime` | Check-out time | diagnostic |
| `tags` | Tags | diagnostic |

**Rationale**: Field names confirmed via Guesty Open API
documentation and web research. The `address` field uses
`address.full` when available, falling back to component assembly
(street, city, state, zipcode, country) joined with commas. This
matches the rentalsync-bridge `_format_address()` pattern. All
detail sensors use `diagnostic` entity category since they are
informational metadata, not controllable state.

**Alternatives considered**:

- *Flatten all address components*: More sensors but less
  user-friendly. A single formatted string is more practical.
- *Use `entity_category=None`*: Would clutter the main entity
  list. Diagnostic is appropriate for metadata sensors.

## R4: DataUpdateCoordinator Pattern

**Decision**: Use a single `ListingsCoordinator` inheriting from
`DataUpdateCoordinator[dict[str, GuestyListing]]` with
`_async_update_data()` performing paginated listing fetch.
Coordinator data is a dict keyed by listing ID for O(1) lookup.

**Rationale**: The HA `DataUpdateCoordinator` handles:

- Periodic polling at configurable intervals
- Concurrent refresh prevention (built-in lock)
- Last-known-good data retention on failure
- Standard `UpdateFailed` exception for error reporting
- Coordinator listener notifications so
  `CoordinatorEntity`-based entities are scheduled for state
  updates when coordinator data changes

The rental-control reference implementation validates this pattern.
Using `dict[str, GuestyListing]` instead of `list[GuestyListing]`
enables O(1) entity lookups by listing ID during state updates,
which matters for accounts with hundreds of listings.

**Alternatives considered**:

- *Custom polling loop*: Reinvents what `DataUpdateCoordinator`
  provides. More code, more bugs, no HA integration.
- *List-based coordinator data*: O(n) lookup per entity update.
  Dict provides constant-time access.
- *Multiple coordinators (one per listing)*: Would create hundreds
  of coordinators for large accounts. Single coordinator with
  batch fetch is more efficient.

## R5: Entity Description Pattern

**Decision**: Use `SensorEntityDescription` dataclasses to define
all listing sensor types. A single `GuestyListingSensor` class
handles all sensor types via description-driven dispatch. Entity
descriptions define `key`, `translation_key`, `entity_category`,
and a `value_fn` callable for extracting values from the listing
model.

**Rationale**: Modern HA integrations use entity descriptions to
avoid per-sensor subclasses. This pattern:

- Reduces boilerplate (one class, many descriptions)
- Centralizes translation key mapping
- Makes adding new sensors trivial (add a description tuple)
- Follows HA core integration conventions

**Alternatives considered**:

- *Per-sensor subclasses*: More classes, more boilerplate, harder
  to maintain. No benefit over description-driven approach.
- *Dynamic entity creation*: Harder to type-check and test.
  Static descriptions are more predictable.

## R6: Options Flow for Refresh Interval

**Decision**: Add an options flow step to the existing config flow
that allows users to set the refresh interval (minutes). Enforce
a minimum of 5 minutes to prevent excessive API calls. Default
is 15 minutes. Store in `entry.options` and listen for option
updates to reconfigure the coordinator interval.

**Rationale**: The spec requires configurable refresh intervals
(FR-008). HA options flows are the standard mechanism for
post-setup configuration. The 5-minute minimum prevents users
from accidentally overwhelming the Guesty API rate limits. The
coordinator's `update_interval` can be changed at runtime via
the `async_set_update_interval()` method or by updating the
`update_interval` attribute.

**Alternatives considered**:

- *Config flow only (no runtime change)*: Requires reconfiguring
  the integration to change interval. Poor UX.
- *Service call to change interval*: Non-standard pattern for
  HA integrations. Options flow is idiomatic.
- *No minimum enforcement*: Risk of rate limiting and API abuse.

## R7: Custom Fields Handling

**Decision**: Expose custom fields as additional diagnostic
sensors with dynamic `key` and `unique_id` values derived from
the custom field name (slugified). Use a fixed
`translation_key` of `listing_custom_field` for all custom
field sensors since HA translation keys must be predefined for
localization. Surface the original custom field name in the
entity name and/or attributes, and use the custom field value
as the sensor state.

**Rationale**: Guesty custom fields vary per account and per
listing, so dynamic sensor creation based on actual data is the
only practical approach. However, Home Assistant translation
keys must be predefined for localization, so they cannot be
generated from arbitrary runtime custom field names. Using a
fixed `translation_key` while keeping the field name in the
entity metadata preserves per-field automation capability and
keeps these entities separate from primary listing sensors via
the diagnostic entity category.

**Alternatives considered**:

- *Single JSON attribute sensor*: Loses per-field automation
  capability. Users cannot trigger on individual field changes.
- *Ignore custom fields*: Reduces feature value for power users
  who rely on Guesty customization.
- *Dynamic translation keys*: Not feasible since HA translation
  keys must be known at localization time, not generated from
  arbitrary runtime field names.

## R8: Disappeared Listing Handling

**Decision**: When a listing that was previously tracked is no
longer returned by the API, mark its entities as `unavailable`
without changing the coordinator data shape. The coordinator
data remains `dict[str, GuestyListing]` for currently returned
listings, while disappeared listing IDs are tracked separately
by the coordinator (e.g., a `set[str]` attribute) for
availability decisions. Do not remove devices automatically.
Log a warning. Users can manually remove stale devices.

**Rationale**: Automatic device removal is disruptive — it
breaks automations that reference the device. Marking as
unavailable is the standard HA pattern for entities whose data
source has gone away. Tracking disappeared IDs separately
preserves the typed coordinator payload defined in R4 while
still allowing entities for previously known listings to
become unavailable and retain their last known device/entity
context for reference.

**Alternatives considered**:

- *Automatic device removal*: Breaks user automations. Too
  aggressive for a polling integration where temporary API
  omissions can occur.
- *Keep showing stale data*: Misleading. Unavailable state is
  honest about the data freshness.

## R9: Library Extractability Boundary

**Decision**: The `api/` sub-package contains only `httpx`,
`dataclasses`, and Python stdlib imports. All listing models
(`GuestyListing`, `GuestyAddress`) live in `api/models.py`.
The `get_listings()` method on `GuestyApiClient` returns
`list[GuestyListing]`. The HA-specific coordinator and entity
modules import from `api/` but never the reverse.

**Rationale**: Constitution Principle II requires the API client
to be independently testable without HA. FR-015 explicitly
requires zero HA imports in API client methods. The dependency
arrow is strictly: HA layer → API layer, never the reverse.

**Alternatives considered**:

- *Models in HA layer*: Would couple data definitions to HA.
  The API layer would need to return raw dicts, losing type
  safety.
- *Separate models package*: Unnecessary complexity for this
  project size. `api/models.py` is sufficient.

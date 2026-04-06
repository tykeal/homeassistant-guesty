<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Reservations

**Feature**: 003-reservations
**Date**: 2025-07-25
**Status**: Complete

## R1: Guesty Reservations Endpoint

**Decision**: Use `GET /v1/reservations` with skip-based
pagination (limit=100, skip=N), explicit `fields` parameter,
`filters` for date-range and status selection, and `sort=_id`
for deterministic ordering.

**Rationale**: The Guesty Open API v1 `/reservations` endpoint
uses the same skip-based pagination model as `/listings`
(limit max 100, skip offset, stop when `len(results) < limit`).
The `fields` parameter selects only the data we need, reducing
payload size. The `filters` parameter accepts an array of
filter objects with operators (`$between`, `$contains`, `$eq`)
to scope reservations by date range and status. Sorting by
`_id` ensures deterministic pagination (Guesty docs recommend
sorting by a unique field).

**Alternatives considered**:

- *Fetch all reservations without filters*: Would pull
  historical data going back years. Wasteful and slow for
  accounts with thousands of reservations.
- *Filter by listingId per listing*: Would require N API calls
  (one per listing) instead of a single batch fetch. The
  batch approach is more efficient; we group by listing ID
  client-side.
- *Cursor-based pagination*: Not supported by the Guesty v1
  reservations endpoint.

## R2: Date Range Filtering

**Decision**: Filter reservations using the `$between` operator
on the `checkIn` field, with a configurable window defaulting
to 30 days past through 365 days future. Additionally filter
`checkOut` to include reservations whose checkout is within the
past window (captures guests currently checked in whose
check-in was before the past window).

**Rationale**: The spec requires FR-002 configurable date range
defaulting to 30 past / 365 future days. Using `$between` on
`checkIn` captures upcoming and recent reservations. A
supplementary `$between` on `checkOut` with the past window
boundary ensures we do not miss currently active reservations
whose check-in date predates the window. Combining both filters
with the Guesty API is achieved via a single request with
a date-range filter on `checkIn` (the primary query), since
checked-in guests whose check-in is within 30 days past are
captured. For edge cases where a guest checked in more than 30
days ago and is still staying, the `checked_in` status filter
ensures inclusion regardless of check-in date.

**Alternatives considered**:

- *Filter only on checkIn*: Would miss long-stay guests whose
  check-in was before the past window boundary.
- *Separate API calls for active vs upcoming*: More complex
  and doubles API usage. A single broad filter with
  client-side grouping is simpler.
- *No date filtering*: Would fetch entire reservation
  history. Impractical for large accounts.

## R3: Status Filtering Strategy

**Decision**: Use the `$contains` filter operator to include
only actionable statuses: `confirmed`, `checked_in`,
`checked_out`, and `canceled`. Exclude `inquiry` and `reserved`
by default per FR-003. Pass through unrecognized statuses
from the API response without error per FR-025.

**Rationale**: The Guesty API supports filtering by status
using `$contains` (equivalent to SQL `IN`). Filtering
server-side reduces payload size. The spec explicitly requires
excluding `inquiry` and `reserved` while accepting unknown
statuses. Client-side handling of unknown statuses satisfies
FR-025: log an info message and pass the value through.

**Alternatives considered**:

- *Fetch all statuses and filter client-side*: Would
  transfer unnecessary data for inquiry/reserved records.
- *Use `$ne` to exclude specific statuses*: More fragile if
  Guesty adds new statuses; the inclusion list is explicit
  about what we want.
- *Strict status validation*: Rejected per FR-025; unknown
  statuses must be passed through, not rejected.

## R4: Reservation Data Field Mapping

**Decision**: Request the following fields from the Guesty API
and map them to Python model attributes:

| Guesty Field | Python Attribute | Type |
| ------------ | ---------------- | ---- |
| `_id` | `id` | `str` |
| `listingId` | `listing_id` | `str` |
| `status` | `status` | `str` |
| `confirmationCode` | `confirmation_code` | `str\|None` |
| `checkIn` | `check_in` | `datetime` |
| `checkOut` | `check_out` | `datetime` |
| `checkInDateLocalized` | `check_in_local` | `str\|None` |
| `checkOutDateLocalized` | `check_out_local` | `str\|None` |
| `plannedArrival` | `planned_arrival` | `str\|None` |
| `plannedDeparture` | `planned_departure` | `str\|None` |
| `nightsCount` | `nights_count` | `int\|None` |
| `guestsCount` | `guests_count` | `int\|None` |
| `guest.fullName` | `guest_name` | `str\|None` |
| `guest.phone` | `guest_phone` | `str\|None` |
| `guest.email` | `guest_email` | `str\|None` |
| `money.totalPaid` | `total_paid` | `float\|None` |
| `money.balanceDue` | `balance_due` | `float\|None` |
| `money.currency` | `currency` | `str\|None` |
| `source` | `source` | `str\|None` |
| `note` | `note` | `str\|None` |

**Rationale**: Each field maps directly to a spec requirement.
Financial fields (`money.*`) serve FR-010 diagnostic sensors.
Guest fields serve FR-008 (US-3). Localized dates and
planned arrival/departure support FR-007 with timezone-aware
display. The `source` field serves FR-021.

**Alternatives considered**:

- *Fetch entire reservation object*: Massive payload with
  many unused fields. Explicit selection is efficient.
- *Skip financial fields*: Would not satisfy FR-010 / US-4.
- *Skip localized dates*: Would require client-side timezone
  conversion. Guesty provides localized dates natively.

## R5: Coordinator Architecture

**Decision**: Create a dedicated `ReservationsCoordinator`
inheriting from `DataUpdateCoordinator` with data type
`dict[str, list[GuestyReservation]]` — a dict keyed by
listing ID where each value is a sorted list of reservations
for that listing. Use a separate coordinator from the existing
`ListingsCoordinator` with its own configurable polling
interval.

**Rationale**: Reservations have a different data shape and
potentially different polling needs than listings. Keying by
listing ID enables O(1) lookup when building per-listing
sensor state. Sorting reservations per listing by check-in
date enables efficient "nearest reservation" selection for
FR-006. Using `DataUpdateCoordinator` provides the same
benefits as the listings coordinator: periodic polling,
concurrent refresh prevention, last-known-good retention
(FR-014), and automatic entity notification.

**Alternatives considered**:

- *Extend ListingsCoordinator*: Would couple listing and
  reservation refresh cycles. They may need different
  intervals and have different failure modes.
- *Flat list of reservations*: O(n) lookup per listing
  during entity state updates. Dict-by-listing is better.
- *One coordinator per listing*: Would create hundreds of
  coordinators for large accounts. Single coordinator with
  batch fetch is more efficient.

## R6: Reservation Status Sensor State Machine

**Decision**: Implement the reservation status sensor state
per FR-006 with the following priority logic applied to
the sorted reservations for each listing:

1. **checked_in**: Any reservation with status `checked_in`
   (active guest) takes top priority.
2. **awaiting_checkin**: Next upcoming `confirmed` reservation
   (check-in in the future).
3. **checked_out**: Nearest completed reservation when no
   active or upcoming confirmed reservation applies.
4. **canceled**: Nearest canceled reservation when no other
   state applies.
5. **no_reservation**: No reservations in the date range.

Unknown statuses from the API are passed through as-is when
they are the only reservation for a listing.

**Rationale**: This priority order matches the spec FR-006
exactly. The "checked_in" state is always most relevant
(someone is physically in the property). "awaiting_checkin"
is next (preparation needed). Completed and canceled states
are fallbacks for historical context. The priority function
operates on the pre-sorted reservation list.

**Alternatives considered**:

- *Show all statuses simultaneously*: Too complex for a
  single sensor state. The priority approach gives one
  clear answer.
- *Separate sensor per reservation*: Would create a
  dynamic number of entities per listing. A single sensor
  with attributes for upcoming reservations is cleaner.

## R7: Sensor Entity Design

**Decision**: Create reservation sensors using the same
`SensorEntityDescription` pattern from Feature 002. The
reservation status sensor is the primary entity (no category).
Financial sensors use `diagnostic` category per FR-010.
All reservation sensors attach to the existing listing device
via `device_info` using the listing ID.

**Rationale**: Reusing the established entity description
pattern keeps the codebase consistent. Attaching to the
listing device (not creating a new device per reservation)
follows the spec requirement FR-004 and matches the user
mental model: reservations belong to properties.

Sensor descriptions for reservations:

| Key | Translation Key | Category |
| --- | --------------- | -------- |
| `reservation_status` | `reservation_status` | — |
| `reservation_total` | `reservation_total` | diag |
| `reservation_balance` | `reservation_balance` | diag |
| `reservation_currency` | `reservation_currency` | diag |

The status sensor carries rich attributes (guest info,
dates, upcoming summaries) per FR-007 through FR-011.

**Alternatives considered**:

- *Device per reservation*: Creates transient devices that
  appear and disappear. Poor UX.
- *All data as attributes on listing sensors*: Would
  overload listing sensors with unrelated data.
- *Non-diagnostic financial sensors*: Would clutter the
  primary entity list per FR-010 spec.

## R8: Upcoming Reservations Attribute

**Decision**: Include an `upcoming_reservations` attribute
on the reservation status sensor containing a list of
summary dicts for the next several upcoming reservations
(up to 10). Each summary includes guest name, check-in
date, check-out date, and status.

**Rationale**: FR-009 requires upcoming reservation
summaries as a sensor attribute. A list of dicts is the
standard HA pattern for structured extra state attributes.
Limiting to 10 entries prevents attribute bloat while
covering typical property management needs.

**Alternatives considered**:

- *Unlimited upcoming list*: Could produce very large
  attributes for properties with many future bookings.
- *Separate entity per upcoming reservation*: Dynamic
  entity count is complex and poor UX.
- *JSON string attribute*: Less usable in HA templates
  than native list-of-dicts.

## R9: Integration with Listings Coordinator

**Decision**: The `ReservationsCoordinator` depends on the
`ListingsCoordinator` for the set of known listing IDs.
During `_async_update_data()`, the reservations coordinator
reads the current listing IDs from the listings coordinator
data to implement FR-017 (skip reservations for unknown
listings). The reservations coordinator is created after
the listings coordinator in `async_setup_entry`.

**Rationale**: FR-017 requires skipping reservations whose
listing ID does not match any known listing. Rather than
maintaining a separate listing ID cache, we reference the
listings coordinator data directly. This ensures the
reservation filtering always uses the freshest listing data.

**Alternatives considered**:

- *Independent listing ID fetch in reservation coordinator*:
  Would duplicate the listings API call.
- *Pass listing IDs as constructor parameter*: Would become
  stale if listings change between refreshes.
- *No filtering*: Would create orphaned sensors for
  reservations on unknown listings.

## R10: Options Flow Extension

**Decision**: Extend the existing options flow to include
reservation-specific settings: reservation polling interval
(default 15 min, min 5 min per FR-013), past days window
(default 30), and future days window (default 365). These
are stored in `entry.options` alongside the existing
listings scan interval.

**Rationale**: FR-002 and FR-013 require configurable date
range and polling interval. The existing options flow
infrastructure from Feature 002 supports adding new fields.
Separate polling intervals for listings and reservations
allow users to tune each independently (e.g., faster
reservation polling during high-turnover periods).

**Alternatives considered**:

- *Single shared polling interval*: Would force listings
  and reservations to refresh at the same rate. Different
  data has different freshness needs.
- *Config flow only (not options)*: Would require
  reconfiguring the integration to change settings.
- *Hardcoded values*: Does not satisfy the configurability
  requirements in FR-002 and FR-013.

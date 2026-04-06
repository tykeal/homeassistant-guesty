<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Reservations

**Feature**: 003-reservations | **Date**: 2025-07-25

## Entity Overview

This feature introduces reservation data models in the `api/`
package and new HA sensor entities attached to existing listing
devices. All DTOs follow the frozen dataclass pattern established
in Features 001 and 002.

## API Package Models (`api/models.py`)

### GuestyGuest

Represents guest contact information associated with a
reservation. Frozen dataclass in the API layer with zero HA
imports.

```python
@dataclass(frozen=True)
class GuestyGuest:
    """Guest contact information from a Guesty reservation."""

    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    guest_id: str | None = None
```

**Fields**:

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `full_name` | `str \| None` | No | Guest display name |
| `phone` | `str \| None` | No | Contact phone |
| `email` | `str \| None` | No | Contact email |
| `guest_id` | `str \| None` | No | Guesty guest ID |

**Validation rules**: All fields optional per FR-019 (graceful
handling of missing guest data). Factory method handles absent
or partial guest objects from the API.

**Factory**:

- `from_api_dict(data: dict | None) -> GuestyGuest | None`:
  Returns `None` if input is `None` or empty dict.

### GuestyMoney

Represents financial summary data for a reservation. Frozen
dataclass in the API layer.

```python
@dataclass(frozen=True)
class GuestyMoney:
    """Financial summary for a Guesty reservation."""

    total_paid: float | None = None
    balance_due: float | None = None
    currency: str | None = None
```

**Fields**:

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `total_paid` | `float \| None` | No | Total paid amount |
| `balance_due` | `float \| None` | No | Outstanding balance |
| `currency` | `str \| None` | No | ISO currency code |

**Validation rules**: All fields optional per FR-019.
Financial data availability depends on account configuration.

**Factory**:

- `from_api_dict(data: dict | None) -> GuestyMoney | None`:
  Returns `None` if input is `None` or empty dict.

### GuestyReservation

A single reservation record linking a guest to a listing
for a specific date range. Frozen dataclass in the API layer.

```python
@dataclass(frozen=True)
class GuestyReservation:
    """A Guesty reservation/booking record."""

    id: str
    listing_id: str
    status: str
    check_in: datetime
    check_out: datetime
    confirmation_code: str | None = None
    check_in_local: str | None = None
    check_out_local: str | None = None
    planned_arrival: str | None = None
    planned_departure: str | None = None
    nights_count: int | None = None
    guests_count: int | None = None
    source: str | None = None
    note: str | None = None
    guest: GuestyGuest | None = None
    money: GuestyMoney | None = None
```

**Fields**:

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `id` | `str` | Yes | Guesty reservation ID |
| `listing_id` | `str` | Yes | Parent listing reference |
| `status` | `str` | Yes | Reservation lifecycle state |
| `check_in` | `datetime` | Yes | UTC check-in datetime |
| `check_out` | `datetime` | Yes | UTC check-out datetime |
| `confirmation_code` | `str \| None` | No | Booking code |
| `check_in_local` | `str \| None` | No | Localized check-in |
| `check_out_local` | `str \| None` | No | Localized check-out |
| `planned_arrival` | `str \| None` | No | Override arrival time |
| `planned_departure` | `str \| None` | No | Override departure |
| `nights_count` | `int \| None` | No | Stay duration |
| `guests_count` | `int \| None` | No | Party size |
| `source` | `str \| None` | No | Booking channel |
| `note` | `str \| None` | No | Reservation notes |
| `guest` | `GuestyGuest \| None` | No | Guest contact info |
| `money` | `GuestyMoney \| None` | No | Financial summary |

**Validation rules**:

- `id` MUST be non-empty; reservations without `_id` are
  skipped during parsing (log warning)
- `listing_id` MUST be non-empty; reservations without
  `listingId` are skipped (log warning)
- `status` is accepted as-is from the API; unknown statuses
  are passed through per FR-025
- `check_in` and `check_out` are parsed from ISO 8601
  strings; reservations with unparsable dates are skipped
- All other fields are optional per FR-019

**Factory**:

- `from_api_dict(data: dict) -> GuestyReservation | None`:
  Returns `None` if required fields are missing or invalid.

### GuestyReservationsResponse

Pagination response wrapper for the reservations endpoint.
Frozen dataclass.

```python
@dataclass(frozen=True)
class GuestyReservationsResponse:
    """Paginated response from reservations endpoint."""

    results: tuple[GuestyReservation, ...]
    count: int
    limit: int
    skip: int
```

**Fields**:

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `results` | `tuple[...]` | Yes | Parsed reservations |
| `count` | `int` | Yes | Total count (metadata) |
| `limit` | `int` | Yes | Page size used |
| `skip` | `int` | Yes | Offset used |

**Factory**:

- `from_api_dict(data: dict) -> GuestyReservationsResponse`:
  Parses `results` array via
  `GuestyReservation.from_api_dict()`, filtering `None`
  entries (invalid reservations).

## API Constants Extension (`api/const.py`)

New constants added to the existing module:

```python
# Reservation endpoints
RESERVATIONS_ENDPOINT = "/reservations"
RESERVATIONS_PAGE_SIZE = 100

# Reservation query fields
RESERVATIONS_FIELDS = (
    "_id",
    "listingId",
    "status",
    "confirmationCode",
    "checkIn",
    "checkOut",
    "checkInDateLocalized",
    "checkOutDateLocalized",
    "plannedArrival",
    "plannedDeparture",
    "nightsCount",
    "guestsCount",
    "guest.fullName",
    "guest.phone",
    "guest.email",
    "money.totalPaid",
    "money.balanceDue",
    "money.currency",
    "source",
    "note",
)

# Status constants
ACTIONABLE_STATUSES = frozenset({
    "confirmed",
    "checked_in",
    "checked_out",
    "canceled",
})

# Default date range window
DEFAULT_PAST_DAYS = 30
DEFAULT_FUTURE_DAYS = 365
```

## Relationships

```text
GuestyListing (Feature 002, existing)
└── has many → GuestyReservation (via listing_id)
    ├── has one → GuestyGuest (optional, embedded)
    └── has one → GuestyMoney (optional, embedded)

ConfigEntry (HA)
├── has one → ListingsCoordinator (existing)
│   └── manages → dict[str, GuestyListing]
└── has one → ReservationsCoordinator (new)
    └── manages → dict[str, list[GuestyReservation]]
        └── exposes → reservation sensors on listing devices
```

## State Transitions

### Reservation Status Sensor

```text
               ┌───────────────────┐
               │  no_reservation   │
               │ (no reservations  │
               │  in date range)   │
               └───────┬───────────┘
                       │ reservation appears
                       ▼
               ┌───────────────────┐
               │ awaiting_checkin  │
               │ (confirmed, future│
               │  check-in date)   │
               └───────┬───────────┘
                       │ guest checks in
                       ▼
               ┌───────────────────┐
               │   checked_in      │
               │ (active guest     │
               │  in property)     │
               └───────┬───────────┘
                       │ guest checks out
                       ▼
               ┌───────────────────┐
               │   checked_out     │
               │ (completed stay)  │
               └───────────────────┘

   At any point before check-in:
               ┌───────────────────┐
               │    canceled       │
               │ (booking canceled)│
               └───────────────────┘
```

### Priority Selection (FR-006)

```text
For a given listing, select sensor state:
  1. checked_in     → highest priority (active guest)
  2. awaiting_checkin → next confirmed future booking
  3. checked_out    → nearest completed stay
  4. canceled       → nearest canceled booking
  5. no_reservation → no reservations in range
```

## HA Entity Mapping

### Sensor Entity Descriptions (Reservation)

| Key | Translation Key | Category | Source |
| --- | --------------- | -------- | ------ |
| `reservation_status` | `reservation_status` | — | Priority state |
| `reservation_total` | `reservation_total` | diag | `money.total_paid` |
| `reservation_balance` | `reservation_balance` | diag | `money.balance_due` |
| `reservation_currency` | `reservation_currency` | diag | `money.currency` |

### Status Sensor Attributes (FR-007 through FR-011)

| Attribute | Type | Source |
| --------- | ---- | ------ |
| `reservation_id` | `str` | `reservation.id` |
| `check_in` | `str` | `reservation.check_in` (ISO) |
| `check_out` | `str` | `reservation.check_out` (ISO) |
| `check_in_local` | `str\|None` | `reservation.check_in_local` |
| `check_out_local` | `str\|None` | `reservation.check_out_local` |
| `planned_arrival` | `str\|None` | `reservation.planned_arrival` |
| `planned_departure` | `str\|None` | `reservation.planned_departure` |
| `guest_name` | `str\|None` | `guest.full_name` |
| `guest_phone` | `str\|None` | `guest.phone` |
| `guest_email` | `str\|None` | `guest.email` |
| `confirmation_code` | `str\|None` | FR-011 |
| `guests_count` | `int\|None` | FR-020 |
| `nights_count` | `int\|None` | FR-022 |
| `source` | `str\|None` | FR-021 |
| `upcoming_reservations` | `list[dict]` | FR-009 |

### Unique ID Format

```text
{config_entry_unique_id}_{listing_id}_{sensor_key}
```

Example:
`abc123_507f1f77bcf86cd799439011_reservation_status`

## Data Flow

```text
ReservationsCoordinator._async_update_data()
  → GuestyApiClient.get_reservations(
        past_days, future_days, statuses)
    → GET /v1/reservations?fields=...&filters=...
      (paginated, returns list[GuestyReservation])
  → Group by listing_id → dict[str, list[GuestyReservation]]
  → Filter out unknown listing IDs (FR-017)
  → Sort each list by check_in date
  → Return to coordinator

Sensor state update:
  → ReservationSensor.native_value
    → Read reservations for this listing from coordinator
    → Apply priority selection (FR-006)
    → Return state string
  → ReservationSensor.extra_state_attributes
    → Build attribute dict from selected reservation
    → Build upcoming_reservations list (FR-009)
```

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Listings/Properties

**Feature**: 002-listings-properties
**Date**: 2025-07-18

## Entities

### GuestyAddress (api/models.py)

A structured address for a Guesty listing. HA-independent frozen
dataclass in the API layer.

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `full` | `str \| None` | No | Pre-formatted full address from API |
| `street` | `str \| None` | No | Street address |
| `city` | `str \| None` | No | City name |
| `state` | `str \| None` | No | State/province |
| `zipcode` | `str \| None` | No | Postal code |
| `country` | `str \| None` | No | Country name |

**Methods**:

- `formatted() -> str | None`: Returns `full` if present;
  otherwise joins non-empty components with `", "`. Returns
  `None` if all components are empty.

**Validation rules**:

- All fields optional (Guesty may return partial addresses)
- `formatted()` never raises; degrades gracefully to `None`

**Factory**:

- `from_api_dict(data: dict | None) -> GuestyAddress | None`:
  Returns `None` if input is `None` or empty dict.

### GuestyListing (api/models.py)

A single Guesty property listing. HA-independent frozen dataclass
in the API layer.

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `id` | `str` | Yes | Guesty listing `_id` (MongoDB ObjectID) |
| `title` | `str` | Yes | Primary listing name |
| `nickname` | `str \| None` | No | Alternative display name |
| `status` | `str` | Yes | `active`, `inactive`, or `archived` |
| `address` | `GuestyAddress \| None` | No | Structured address |
| `property_type` | `str \| None` | No | E.g., apartment, house, villa |
| `room_type` | `str \| None` | No | E.g., entire home, private room |
| `bedrooms` | `int \| None` | No | Number of bedrooms |
| `bathrooms` | `float \| None` | No | Bathroom count (half-baths) |
| `timezone` | `str` | Yes | IANA timezone (default: UTC) |
| `check_in_time` | `str \| None` | No | Default check-in time (HH:MM) |
| `check_out_time` | `str \| None` | No | Default check-out time (HH:MM) |
| `tags` | `tuple[str, ...]` | Yes | Listing tags (empty tuple if none) |
| `custom_fields` | `dict[str, str]` | Yes | Custom name-value pairs |

**Validation rules**:

- `id` MUST be non-empty; listings without `_id` are skipped
  during parsing (FR-010)
- `title` defaults to `nickname` if absent, then to `"Unknown"`
- `status` derived from API `listed` and `active` boolean fields
- `timezone` defaults to `"UTC"` if not provided
- `bedrooms` and `bathrooms` are `None` when not present
  (FR-011)
- `tags` is an immutable tuple for hashability
- `custom_fields` values are coerced to strings

**Factory**:

- `from_api_dict(data: dict) -> GuestyListing | None`: Returns
  `None` if `_id` is missing or empty. Handles all optional
  field defaults.

**Status derivation logic**:

```text
if listed == true AND active == true → "active"
if listed == false OR active == false → "inactive"
if Guesty returns explicit archive indicator → "archived"
```

**Note**: A listing no longer returned by the API is NOT
mapped to `archived`. Instead, its entities are marked as
`unavailable` by the coordinator (see R8 in research.md).
The `archived` status requires an explicit API-side indicator.

### GuestyListingsResponse (api/models.py)

Pagination response wrapper for the listings endpoint.
HA-independent frozen dataclass.

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `results` | `list[GuestyListing]` | Yes | Parsed listings (filtered) |
| `count` | `int` | Yes | Total count from API (metadata only) |
| `limit` | `int` | Yes | Page size used |
| `skip` | `int` | Yes | Offset used |

**Factory**:

- `from_api_dict(data: dict) -> GuestyListingsResponse`:
  Parses `results` array via `GuestyListing.from_api_dict()`,
  filtering `None` entries (invalid listings).

## Relationships

```text
GuestyListing
├── has one → GuestyAddress (optional)
├── has many → tags (tuple of strings)
└── has many → custom_fields (dict of name-value pairs)

ConfigEntry (HA)
└── has one → ListingsCoordinator
    └── manages many → GuestyListing (via dict[str, GuestyListing])
        └── exposes many → SensorEntity (per listing per description)
```

## State Transitions

### Listing Status

```text
┌──────────┐  listed=true  ┌──────────┐
│ inactive  │ ──────────── │  active   │
│           │ ←─────────── │           │
└──────────┘  listed=false └──────────┘
      │                          │
      │  explicit archive        │
      ▼                          ▼
┌──────────────────────────────────┐
│           archived                │
│  (API returns archive indicator)  │
└──────────────────────────────────┘
```

### Entity Availability (Disappeared Listings)

```text
Any status (active/inactive/archived)
      │
      │  listing not returned by API
      ▼
┌──────────────────────────────────┐
│         unavailable               │
│  (entities marked unavailable;    │
│   device retained in HA)          │
└──────────────────────────────────┘
```

### Coordinator Refresh Cycle

```text
┌─────────┐   interval elapsed   ┌────────────┐
│  idle    │ ──────────────────── │  fetching   │
└─────────┘                      └────────────┘
                                       │
                           ┌───────────┴───────────┐
                           ▼                       ▼
                    ┌────────────┐          ┌────────────┐
                    │  success   │          │  failure   │
                    │ update data│          │ keep stale │
                    └────────────┘          │ log warning│
                           │               └────────────┘
                           ▼                       │
                    ┌────────────┐                 │
                    │  notify    │                 │
                    │  entities  │ ◄───────────────┘
                    └────────────┘
                           │
                           ▼
                    ┌─────────┐
                    │  idle    │
                    └─────────┘
```

## HA Entity Mapping

### Device Registry Entry (per listing)

| Device Property | Value |
| --------------- | ----- |
| `identifiers` | `{(DOMAIN, listing.id)}` |
| `name` | `listing.title` |
| `manufacturer` | `"Guesty"` |
| `model` | `listing.property_type or "Listing"` |

### Sensor Entity Descriptions

| Key | Translation Key | Cat. |
| --- | --------------- | ---- |
| `status` | `listing_status` | — |
| `name` | `listing_name` | diag |
| `nickname` | `listing_nickname` | diag |
| `address` | `listing_address` | diag |
| `property_type` | `listing_property_type` | diag |
| `room_type` | `listing_room_type` | diag |
| `bedrooms` | `listing_bedrooms` | diag |
| `bathrooms` | `listing_bathrooms` | diag |
| `timezone` | `listing_timezone` | diag |
| `check_in_time` | `listing_check_in_time` | diag |
| `check_out_time` | `listing_check_out_time` | diag |
| `tags` | `listing_tags` | diag |

**Cat.** = Entity category: `—` = `None`, `diag` = `diagnostic`

**Value source** for each key:

- `status` → `listing.status`
- `name` → `listing.title`
- `nickname` → `listing.nickname`
- `address` → `listing.address.formatted()`
- `property_type` → `listing.property_type`
- `room_type` → `listing.room_type`
- `bedrooms` → `listing.bedrooms`
- `bathrooms` → `listing.bathrooms`
- `timezone` → `listing.timezone`
- `check_in_time` → `listing.check_in_time`
- `check_out_time` → `listing.check_out_time`
- `tags` → `", ".join(listing.tags)`

### Dynamic Sensors (per custom field)

Custom field sensors are created dynamically per listing. Each
custom field becomes a diagnostic sensor with:

- `key`: `custom_{slugified_field_name}`
- `translation_key`: `listing_custom_field`
- `native_value`: field value as string

### Unique ID Format

```text
{config_entry_unique_id}_{listing_id}_{sensor_key}
```

Example: `abc123_507f1f77bcf86cd799439011_status`

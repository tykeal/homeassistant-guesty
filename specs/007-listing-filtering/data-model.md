<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Listing Filtering

**Feature**: 007-listing-filtering **Date**: 2025-07-24

## Entity: Config Entry Options (Extended)

The listing filter configuration is persisted in the HA config entry's options
dictionary. This extends the existing options schema.

### Existing Fields (unchanged)

| Field | Key | Type | Def | Description |
| --- | --- | --- | --- | --- |
| Scan Int | `scan_interval` | `int` | `15` | Listing poll min 5 |
| Resv Scan | `reservation_scan_interval` | `int` | `15` | Resv poll min 5 |
| Past Days | `past_days` | `int` | `30` | Past resv days (min 1) |
| Future Days | `future_days` | `int` | `365` | Future resv days (min 1) |

### New Fields

| Field | Key | Type | Def | Description |
| --- | --- | --- | --- | --- |
| Selected | `selected_listings` | `list[str]` | `None` | IDs; None=all |
| Tag Filter | `tag_filter` | `list[str]` | `[]` | Selector pre-filter tags |

### Validation Rules

- `selected_listings`:
  - When present, must be a non-empty list of strings (FR-012)
  - Each string must be a valid Guesty listing ID
  - IDs not present in the current Guesty account are silently ignored (listing
    may have been deleted)
  - When absent from options (`None`), coordinator treats as "all listings"

- `tag_filter`:
  - List of strings (may be empty)
  - Case-sensitive tag values matching Guesty's tag system
  - Used only at options-flow time to populate the listing selector
  - Not used by the coordinator at runtime (tags are a UI convenience, not a
    runtime filter)

### State Transitions

```text
┌────────────────────┐
│  No Filter Config   │  (pre-upgrade / fresh install)
│  selected_listings  │  = None (absent)
│  tag_filter = []    │
└────────┬───────────┘
         │  User opens options flow
         │  and saves filter settings
         ▼
┌────────────────────┐
│  Filter Active      │  (post-configuration)
│  selected_listings  │  = ["id1", "id2", ...]
│  tag_filter         │  = ["tag1", ...] or []
└────────┬───────────┘
         │  User modifies filter
         │  (add/remove listings)
         ▼
┌────────────────────┐
│  Filter Updated     │  (deselected listings removed,
│  selected_listings  │   new listings added)
│  = ["id1", "id3"]  │
└────────────────────┘
```

## Entity: GuestyListing (Unchanged)

The `GuestyListing` model in `api/models.py` is **not modified**. It already
contains all fields needed for this feature:

```python
@dataclass(frozen=True)
class GuestyListing:
    id: str                                    # Used as selection key
    title: str                                 # Displayed in selector
    nickname: str | None
    status: str
    address: GuestyAddress | None              # Displayed in selector (formatted)
    property_type: str | None
    room_type: str | None
    bedrooms: int | None
    bathrooms: float | None
    timezone: str
    check_in_time: str | None
    check_out_time: str | None
    tags: tuple[str, ...]                      # Used for tag pre-filtering
    custom_fields: MappingProxyType[str, str]
```

**Key fields for this feature**:

- `id` → Used as the value in the multi-select selector; stored in
  `selected_listings`
- `title` → Displayed as the primary label in the selector
- `address` → Displayed as secondary label (via `address.formatted()`)
- `tags` → Used for client-side tag pre-filtering

## Entity: Device (HA Device Registry)

Each tracked Guesty listing corresponds to one HA device. The device lifecycle
is:

| Event | Action | Mechanism |
| --- | --- | --- |
| Selected/first load | Created | `DeviceInfo` via entity |
| Deselected in opts | Removed | `async_update_device` remove |
| Re-selected | Recreated | Next update creates device |
| Deleted in Guesty | Stays unavail | `disappeared_listing_ids` |

**Device identifiers**: `{(DOMAIN, listing.id)}` — one device per Guesty listing
ID.

## Coordinator Data Flow

### ListingsCoordinator

```text
API: get_listings()
  → All GuestyListing objects (unfiltered)
  → Apply selected_listings filter (if configured)
  → Return dict[str, GuestyListing] (filtered)
  → Entities created/updated only for returned listings
```

**Filter logic in `_async_update_data()`**:

```python
selected = self.config_entry.options.get(CONF_SELECTED_LISTINGS)
if selected is not None:
    selected_set = set(selected)
    new_data = {lid: listing for lid, listing in new_data.items()
                if lid in selected_set}
```

### ReservationsCoordinator (Unchanged)

Already filters by `listings_coordinator.data.keys()`. No code changes needed —
filtering cascades automatically.

### CustomFieldsDefinitionCoordinator (Unchanged)

Account-wide definitions; not affected by listing filtering (FR-018).

## Options Flow Data Flow

```text
Step 1: init (Tag Filter)
  User input: optional tag list
  ┌─ Fetch all listings via api_client.get_listings()
  └─ Store in self._available_listings
  → Proceed to select_listings

Step 2: select_listings (Listing Selector)
  ┌─ Apply tag filter to self._available_listings
  ├─ Build SelectSelector options (label = title + address)
  ├─ Preselect: currently selected listings (or all if no prior filter)
  └─ Validate: at least one listing selected
  → Proceed to intervals

Step 3: intervals (Scan Settings)
  ┌─ Show existing interval fields (scan_interval, etc.)
  └─ On submit: merge all step data → async_create_entry()
```

## New Constants

| Constant | Value | Module |
| --- | --- | --- |
| `CONF_SELECTED_LISTINGS` | `"selected_listings"` | `const.py` |
| `CONF_TAG_FILTER` | `"tag_filter"` | `const.py` |

## Relationship Diagram

```text
ConfigEntry.options
├── selected_listings: list[str] ──────► ListingsCoordinator._async_update_data()
│                                           │ filters returned dict
│                                           ▼
│                                       coordinator.data: dict[str, GuestyListing]
│                                           │ drives entity creation
│                                           ▼
│                                       GuestyListingSensor (per selected listing)
│                                       GuestyReservationSensor (per selected listing)
│                                       GuestyFinancialSensor (per selected listing)
│                                           │
│                                       ReservationsCoordinator
│                                           └── filters by coordinator.data.keys()
│
├── tag_filter: list[str] ──────► Options Flow (selector pre-filter)
│
├── scan_interval: int ────────────────► ListingsCoordinator.update_interval
├── reservation_scan_interval: int ────► ReservationsCoordinator.update_interval
├── past_days: int ────────────────────► ReservationsCoordinator (API query param)
└── future_days: int ──────────────────► ReservationsCoordinator (API query param)
```

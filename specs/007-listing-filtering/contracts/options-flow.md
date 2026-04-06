<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Options Flow Contract: Listing Filtering

**Feature**: 007-listing-filtering **Date**: 2025-07-24

## Overview

The Guesty integration options flow is extended from a single-step flow to a
three-step linear flow. The steps are:

1. **Tag Filter** (`init`) — Optional tag-based pre-filtering
2. **Listing Selection** (`select_listings`) — Multi-select listing picker
3. **Polling Intervals** (`intervals`) — Existing scan interval configuration

## Step 1: Tag Filter (`init`)

**Step ID**: `init` **Purpose**: Allow the user to optionally specify Guesty
tags to narrow the listing selector.

### Tag Filter Schema

| Field | Key | Type | Req | Default | Selector |
| --- | --- | --- | --- | --- | --- |
| Tags | `tag_filter` | `list[str]` | No | Cur/`[]` | `TextSelector(multi)` |

### Tag Filter Behavior

- On initial display: shows current tag filter values
  (or empty if none
  configured)
- On submit with tags: fetches all listings from API, stores in flow handler
  state, transitions to `select_listings`
- On submit with empty tags: same behavior (no filtering applied)
- On API error: shows error `"cannot_connect"`, re-displays form, preserves
  existing settings
- The API fetch (`api_client.get_listings()`) happens between step 1 submission
  and step 2 display

### Tag Filter Error Handling

| Condition | Error Key | Behavior |
| --- | --- | --- |
| API unreachable | `cannot_connect` | Re-show form with error message |
| Auth expired | `invalid_auth` | Re-show form with error message |
| Rate limited | `rate_limited` | Re-show form with error message |

## Step 2: Listing Selection (`select_listings`)

**Step ID**: `select_listings` **Purpose**: Allow the user to select which
Guesty listings to track.

### Listing Selection Schema

| Field | Key | Type | Req | Default | Selector |
| --- | --- | --- | --- | --- | --- |
| Listings | `selected_listings` | `list[str]` | Yes | All | `SelectSelector` |

### Options Format

Each option in the `SelectSelector` is:

```python
SelectOptionDict(
    value=listing.id,       # Guesty listing ID
    label="{title} — {formatted_address}",  # Human-readable label
)
```

Where:

- `title` = `listing.title`
- `formatted_address` = `listing.address.formatted()` if address exists,
  otherwise `"No address"`

### Listing Selection Behavior

- Options are filtered by tag filter from step 1
  (if tags specified)
- Tag filtering uses OR logic: listing matches if any of its tags match any
  filter tag
- If no tags specified, all listings are shown
- Default selection: current `selected_listings` from config entry options
  (intersected with available options); if no prior filter, all listings
  preselected
- At least one listing must be selected; empty selection is rejected with error
  `"no_listings_selected"`
- If tag filter matches zero listings: empty list shown with
  `"no_listings_match_tags"` error

### Listing Selection Error Handling

| Condition | Error Key | Behavior |
| --- | --- | --- |
| No selection | `no_listings_selected` | Re-show with error |
| No tags match | `no_listings_match_tags` | Re-show, empty selector |

## Step 3: Polling Intervals (`intervals`)

**Step ID**: `intervals` **Purpose**: Configure polling intervals (existing
functionality, relocated to step 3).

### Polling Intervals Schema

| Field | Key | Type | Req | Default | Validator |
| --- | --- | --- | --- | --- | --- |
| Listing Scan | `scan_interval` | `int` | Yes | 15 | `Range(min=5)` |
| Resv Scan | `reservation_scan_interval` | `int` | Yes | 15 | `Range(min=5)` |
| Past Days | `past_days` | `int` | Yes | 30 | `Range(min=1)` |
| Future Days | `future_days` | `int` | Yes | 365 | `Range(min=1)` |

### Polling Intervals Behavior

- Identical to the current single-step options flow
- On submit: merges data from all three steps and calls
  `async_create_entry(data={...})`
- The merged data dict contains all keys: `tag_filter`, `selected_listings`,
  `scan_interval`, `reservation_scan_interval`, `past_days`, `future_days`

## Translation Strings

### New strings to add to `strings.json` and `translations/en.json`

<!-- markdownlint-disable MD013 -->
```json
{
  "options": {
    "step": {
      "init": {
        "title": "Filter by Tags",
        "description": "Optionally enter Guesty tags to narrow the listing selector. Leave empty to see all listings.",
        "data": {
          "tag_filter": "Guesty tags"
        },
        "data_description": {
          "tag_filter": "Enter one or more Guesty listing tags. Only listings with matching tags will appear in the selector."
        }
      },
      "select_listings": {
        "title": "Select Listings",
        "description": "Choose which Guesty listings to track in Home Assistant. At least one listing must be selected.",
        "data": {
          "selected_listings": "Listings to track"
        },
        "data_description": {
          "selected_listings": "Select the Guesty listings you want to monitor. Deselected listings will have their devices and sensors removed."
        }
      },
      "intervals": {
        "title": "Polling Configuration",
        "description": "Configure how often the integration polls the Guesty API.",
        "data": {
          "scan_interval": "Listing scan interval (minutes)",
          "reservation_scan_interval": "Reservation scan interval (minutes)",
          "past_days": "Reservation past days",
          "future_days": "Reservation future days"
        },
        "data_description": {
          "scan_interval": "How often to poll the Guesty API for listing updates. Minimum 5 minutes.",
          "reservation_scan_interval": "How often to poll the Guesty API for reservation updates. Minimum 5 minutes.",
          "past_days": "Number of past days to include in reservation queries.",
          "future_days": "Number of future days to include in reservation queries."
        }
      }
    },
    "error": {
      "invalid_scan_interval": "Scan interval must be at least 5 minutes.",
      "cannot_connect": "Unable to connect to the Guesty API. Please try again later.",
      "invalid_auth": "Authentication failed. Please re-authenticate the integration.",
      "rate_limited": "Guesty API rate limit reached. Please try again later.",
      "no_listings_selected": "At least one listing must be selected.",
      "no_listings_match_tags": "No listings match the specified tags. Try different tags or clear the tag filter."
    }
  }
}
```
<!-- markdownlint-enable MD013 -->

## Options Update Listener Contract

When options are saved, the update listener in `__init__.py` performs:

1. **Compare old vs new `selected_listings`** to detect filter changes
2. **Remove devices** for deselected listings (via device registry)
3. **Update coordinator intervals** (existing behavior)
4. **Trigger immediate refresh** of listings and reservations coordinators (if
   filter changed)
5. **Skip refresh** if filter settings are unchanged (FR-014)

### Trigger Conditions

| Change Detected | Actions |
| --- | --- |
| `selected_listings` changed | Remove devices → refresh |
| Only intervals changed | Update intervals (no device changes) |
| `tag_filter` changed only | No effect (UI-only for selector) |
| Nothing changed | No action |

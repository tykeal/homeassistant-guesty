<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Listing Filtering

**Feature**: 007-listing-filtering **Date**: 2025-07-24

## Research Task 1: Device and Entity Removal in HA Custom Integrations

**Context**: When a user deselects a listing, we must remove the corresponding
device and all its entities (listing sensors, reservation sensors, financial
sensors, custom field sensors) from Home Assistant.

### Decision: Use `async_update_device(remove_config_entry_id=...)`

### Device Removal Rationale

Home Assistant provides two device removal mechanisms:

1. **`dev_registry.async_remove_device(device.id)`** — Directly deletes the
   device and all associated entities. Simple but aggressive; does not account
   for devices shared across config entries.

2. **`dev_registry.async_update_device(device_id, remove_config_entry_id=entry.entry_id)`**
   — Removes the config entry association from the device. If the device has no
   remaining config entries, HA automatically deletes it and all entities. This
   is the recommended pattern per HA developer docs (integration quality scale:
   stale-devices rule).

We use approach #2 because:

- It follows HA's recommended stale-device removal pattern
- It is safe if a device were ever shared across config entries (unlikely for
  Guesty, but defensive)
- HA automatically cascades entity removal when a device loses all config
  entries
- It is the pattern endorsed by the HA integration quality scale

### Implementation Pattern

```python
from homeassistant.helpers import device_registry as dr

dev_registry = dr.async_get(hass)
for device in dr.async_entries_for_config_entry(dev_registry, entry.entry_id):
    # Extract the Guesty listing ID from device identifiers
    listing_ids_in_device = {
        identifier[1]
        for identifier in device.identifiers
        if identifier[0] == DOMAIN
    }
    # If this device's listing is no longer selected, remove it
    if not listing_ids_in_device & selected_listing_ids:
        dev_registry.async_update_device(
            device.id,
            remove_config_entry_id=entry.entry_id,
        )
```

### Device Removal Alternatives

- **Do nothing; let entities show unavailable**: Rejected. The spec (FR-006)
  explicitly requires removal of devices and sensors. Unavailable entities
  create dashboard clutter.
- **Remove entities individually via entity_registry**: Rejected. Removing the
  device cascades to all entities automatically. Individual removal is more
  code, more error-prone, and misses the device entry itself.
- **Reload the entire integration**: Rejected. `async_reload()` is heavy-handed,
  disrupts other coordinator listeners, and causes momentary unavailability for
  all entities.

---

## Research Task 2: Multi-Select Listing Selector in HA Options Flow

**Context**: The options flow needs a multi-select widget where users can choose
which Guesty listings to track. Each option should display the listing name and
location.

### Decision: Use `SelectSelector` with `SelectSelectorConfig(multiple=True, mode="list")`

### Multi-Select Rationale

Home Assistant's `homeassistant.helpers.selector` module provides
`SelectSelector` and `SelectSelectorConfig` which integrate directly with the HA
frontend. Using `multiple=True` enables multi-select, and the `mode="list"`
renders as a checkbox list (better UX for 5–100 items than a dropdown). Options
are provided as `SelectOptionDict` with `value` (listing ID) and `label`
(display name + location).

```python
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

listing_options = [
    SelectOptionDict(
        value=listing.id,
        label=(
            f"{listing.title} — "
            f"{listing.address.formatted()
            if listing.address
            else 'No address'}"
        ),
    )
    for listing in available_listings
]

schema = vol.Schema({
    vol.Required(
        CONF_SELECTED_LISTINGS,
        default=currently_selected_ids,
    ): SelectSelector(
        SelectSelectorConfig(
            options=listing_options,
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    ),
})
```

The `SelectSelector` is the standard HA mechanism for select inputs in config
flows. It renders natively in the HA frontend with proper multi-select support.

### Multi-Select Alternatives

- **`vol.In()` with plain list**: Rejected. Does not render as a multi-select
  widget in the HA frontend; only supports single selection.
- **Custom frontend card**: Rejected. Violates HA config flow conventions and
  would require custom JS.
- **Text input with comma-separated IDs**: Rejected. Terrible UX; users cannot
  identify listings by ID. The selector provides labels.

---

## Research Task 3: Multi-Step Options Flow Pattern

**Context**: The listing filter feature needs to fetch listings from the API
before showing the selector. A tag pre-filter optionally narrows the listing
set. The existing scan interval settings must be preserved. This requires a
multi-step options flow.

### Decision: Three-Step Options Flow with Tag Pre-Filter

### Multi-Step Flow Rationale

HA config flows support multi-step navigation via `async_step_{step_id}`
methods. Each step returns a form or transitions to the next step. Data is
accumulated across steps and committed in the final `async_create_entry()` call.

**Flow structure**:

1. **`async_step_init`** — Tag pre-filter input
   - Optional text field for comma-separated Guesty tags
   - If user submits, stores tags and transitions to `select_listings`
   - Fetches listings from the Guesty API (via the existing authenticated
     client)

2. **`async_step_select_listings`** — Listing multi-select
   - Fetches current listings from API (or uses cached result from step 1)
   - Applies tag filter if tags were specified
   - Shows multi-select `SelectSelector` with filtered listings
   - Preselects currently tracked listings (or all if no prior filter)
   - Validates at least one listing is selected (FR-012)
   - Transitions to `intervals`

3. **`async_step_intervals`** — Existing scan interval settings
   - Preserves the existing options form (scan_interval,
     reservation_scan_interval, past_days, future_days)
   - On submit, merges all data from all steps and calls `async_create_entry()`

**Data flow across steps**: Instance variables on the flow handler
(`self._tag_filter`, `self._selected_listings`, `self._available_listings`)
carry state between steps.

### Multi-Step Flow Alternatives

- **Single step with all fields**: Rejected. Cannot fetch listings dynamically
  based on tag filter within a single form submission. Tags must be submitted
  before the listing selector can be populated.
- **Two steps (filter+select combined, intervals)**: Rejected. Tag filter and
  listing select need sequential interaction — tags are input, listings are
  output. They must be separate steps.
- **Menu-based step selection**: Rejected. Unnecessary complexity; the 3-step
  linear flow is clearer and matches the user's mental model.

---

## Research Task 4: Triggering Immediate Coordinator Refresh

**Context**: After saving filter changes (FR-013), the integration must refresh
data immediately rather than waiting for the next scheduled poll (up to 15
minutes).

### Decision: Use `coordinator.async_request_refresh()` in the options update listener

### Immediate Refresh Rationale

`DataUpdateCoordinator.async_request_refresh()` schedules an immediate data
fetch, bypassing the normal update interval. It is the standard HA mechanism for
on-demand refreshes. It respects the coordinator's internal debouncer and
retry/backoff logic.

The call is placed in `_async_options_updated()` (the existing update listener
in `__init__.py`) after the coordinator's update interval has been reconfigured
and the filter settings have been applied:

```python
async def _async_options_updated(hass, entry):
    # ... existing interval updates ...

    # Trigger immediate refresh if filter settings changed
    old_selected = previous_options.get(CONF_SELECTED_LISTINGS)
    new_selected = entry.options.get(CONF_SELECTED_LISTINGS)
    if old_selected != new_selected:
        # Clean up devices for deselected listings FIRST
        _remove_deselected_devices(hass, entry, new_selected)
        # Then refresh coordinators
        await coordinator.async_request_refresh()
        await reservations_coordinator.async_request_refresh()
```

The refresh is conditional on filter changes (FR-014): if settings haven't
changed, no refresh is triggered.

### Immediate Refresh Alternatives

- **`await coordinator.async_refresh()`**: This also works but does not go
  through the debouncer. `async_request_refresh()` is the documented public API.
- **`await hass.config_entries.async_reload(entry.entry_id)`**: Rejected. Full
  reload is disruptive — tears down all entities and recreates them, causing
  momentary unavailability.
- **Do nothing (wait for next poll)**: Rejected. Violates FR-013 and degrades UX
  (user waits up to 15 minutes).

---

## Research Task 5: Accessing API Client from Options Flow

**Context**: The options flow needs to fetch current listing data from the
Guesty API to populate the selector (FR-011).

### Decision: Access the existing authenticated API client via `hass.data[DOMAIN][entry.entry_id]["api_client"]`

### API Client Access Rationale

The API client is already initialized, authenticated, and stored in `hass.data`
during `async_setup_entry()`. The options flow handler has access to `self.hass`
and `self.config_entry`, so it can retrieve the client directly:

```python
api_client = self.hass.data[DOMAIN][self.config_entry.entry_id]["api_client"]
listings = await api_client.get_listings()
```

This reuses the existing OAuth 2.0 session with valid tokens. No new
authentication flow is needed.

**Error handling**: If the API call fails (network error, auth expired), the
options flow shows an error message and preserves existing settings (FR-019).

### API Client Access Alternatives

- **Create a new API client in the flow**: Rejected. Wasteful; the existing
  client is already authenticated and handles token refresh.
- **Use coordinator data instead of API**: Rejected. The coordinator's data is
  filtered (after this feature); we need the unfiltered full listing set. Also,
  FR-011 requires fetching current data, not cached coordinator data.

---

## Research Task 6: Tag-Based Pre-Filtering Implementation

**Context**: Users can optionally specify Guesty tags to narrow the listing
selector (FR-007 through FR-010).

### Decision: Client-Side Tag Filtering After API Fetch

### Tag Filtering Rationale

The Guesty API does not provide server-side tag filtering (confirmed in spec
Assumptions). Tags are already present in the `GuestyListing` model as
`tags: tuple[str, ...]`. Filtering is a simple set intersection:

```python
def filter_listings_by_tags(
    listings: list[GuestyListing],
    tags: list[str],
) -> list[GuestyListing]:
    if not tags:
        return listings
    tag_set = set(tags)
    return [l for l in listings if tag_set & set(l.tags)]
```

Tag values are case-sensitive (matching Guesty's tag system). The filter uses OR
logic (FR-008): a listing matches if any of its tags appear in the filter set.
This is a pure function with no HA dependencies, so it can be placed in a
utility module or inline in the config flow.

Tags are stored in config entry options as `CONF_TAG_FILTER: list[str]`. An
empty list means no tag filter (show all listings).

### Tag Filtering Alternatives

- **Server-side filtering**: Not available in Guesty API. Could be added as
  optimization in future if Guesty adds this endpoint.
- **Case-insensitive matching**: Rejected for now. Guesty tags are
  case-sensitive strings. Applying case-insensitive matching could cause
  confusion if users have tags like "Miami" and "miami".
- **AND logic (all tags must match)**: Rejected. OR logic is more intuitive for
  the use case (spec FR-008 explicitly requires OR).

---

## Research Task 7: Backward Compatibility Strategy

**Context**: Existing installations with no filter configuration must continue
tracking all listings with zero user intervention (US-3, FR-004).

### Decision: Treat absent `CONF_SELECTED_LISTINGS` as "all listings selected"

### Backward Compatibility Rationale

When `CONF_SELECTED_LISTINGS` is not present in `entry.options` (pre-upgrade
state), the coordinator treats this as "no filter applied" and returns all
fetched listings. The sentinel is `None` (key absent), not an empty list:

```python
# In ListingsCoordinator._async_update_data():
selected = self.config_entry.options.get(CONF_SELECTED_LISTINGS)
if selected is not None:
    selected_set = set(selected)
    new_data = {lid: l for lid, l in new_data.items() if lid in selected_set}
# If selected is None, return all listings (backward compatible)
```

Similarly, when the options flow is opened for the first time (no prior filter),
all listings are preselected in the multi-select widget.

### Backward Compatibility Alternatives

- **Migration that sets all listing IDs on upgrade**: Rejected. Unnecessary
  complexity. The `None`-means-all convention is simpler, requires no migration,
  and handles the case where new listings are added to Guesty (they appear
  automatically for non-filtered users).
- **Empty list means all**: Rejected. Ambiguous. An empty list should mean
  "nothing selected" (which FR-012 prevents from being saved, but it's cleaner
  semantically).

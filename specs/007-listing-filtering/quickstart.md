<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Listing Filtering

**Feature**: 007-listing-filtering **Date**: 2025-07-24

## Prerequisites

- Python ≥3.14.2
- `uv` installed for dependency management
- Pre-commit hooks installed (`pre-commit install`)
- Working dev environment: `uv sync --group dev`

## Development Environment Setup

```bash
cd <repo-root>
git checkout -b 007-listing-filtering  # or switch if branch exists
uv sync --group dev
```

## Running Tests

```bash
# Run all tests
uv run pytest tests/

# Run specific test modules relevant to this feature
uv run pytest tests/test_config_flow.py -v
uv run pytest tests/test_coordinator.py -v
uv run pytest tests/test_init.py -v
uv run pytest tests/test_sensor.py -v

# Run with coverage
uv run pytest tests/ --cov=custom_components/guesty --cov-report=term-missing
```

## Running Linting

```bash
# Ruff linting + formatting
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/

# Type checking
uv run mypy custom_components/guesty/

# Docstring coverage (must be 100%)
uv run interrogate custom_components/guesty/
```

## Key Files to Modify

### 1. `custom_components/guesty/const.py`

Add new constants:

- `CONF_SELECTED_LISTINGS = "selected_listings"`
- `CONF_TAG_FILTER = "tag_filter"`

### 2. `custom_components/guesty/coordinator.py`

Modify `ListingsCoordinator._async_update_data()`:

- After fetching all listings, apply `selected_listings` filter from
  `config_entry.options`
- If `selected_listings` is `None` (absent), return all listings (backward
  compatible)
- If `selected_listings` is a list, filter `new_data` to only include those IDs

### 3. `custom_components/guesty/config_flow.py`

Extend `GuestyOptionsFlowHandler` to multi-step flow:

- `async_step_init()` — Tag filter input + API fetch
- `async_step_select_listings()` — Multi-select listing selector
- `async_step_intervals()` — Existing scan interval settings

New imports needed:

```python
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
```

### 4. `custom_components/guesty/__init__.py`

Modify `_async_options_updated()`:

- Detect `selected_listings` changes
- Remove devices for deselected listings via device registry
- Trigger `coordinator.async_request_refresh()` if filter changed

New imports needed:

```python
from homeassistant.helpers import device_registry as dr
```

### 5. `custom_components/guesty/strings.json` and `translations/en.json`

Add strings for new options flow steps (see contracts/options-flow.md for full
strings).

### 6. Test files

- `tests/test_config_flow.py` — Multi-step options flow tests
- `tests/test_coordinator.py` — Listing filter tests
- `tests/test_init.py` — Device cleanup and refresh trigger tests
- `tests/conftest.py` — New fixtures for multi-listing scenarios

## TDD Workflow

Follow Constitution I (Red-Green-Refactor):

1. **Red**: Write a failing test for the behavior
2. **Green**: Implement minimum code to pass
3. **Refactor**: Clean up while keeping tests green

### Example TDD sequence for coordinator filtering

```python
# RED: Test that coordinator filters by selected_listings
async def test_coordinator_filters_by_selected_listings(hass, ...):
    """Coordinator returns only selected listings."""
    entry.options = {CONF_SELECTED_LISTINGS: [listing_a.id]}
    await coordinator.async_refresh()
    assert listing_a.id in coordinator.data
    assert listing_b.id not in coordinator.data

# GREEN: Add filter logic to _async_update_data()
# REFACTOR: Extract filter to helper if needed
```

## Commit Conventions

```bash
# Feature commit
git commit -s -m "Feat(options): Add tag filter step to options flow

Add async_step_init with optional tag filter input and API
fetch for listing data.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

**Rules**:

- One logical change per commit
- Capitalized Conventional Commit types: `Feat`, `Test`, `Refactor`, `Fix`,
  `Chore`
- Always include `-s` for DCO sign-off
- Always include `Co-authored-by` trailer for AI-assisted commits
- Task list updates (`tasks.md`) are separate commits
- SPDX headers on all new files

## Architecture Notes

### api/ Package: NO CHANGES

The `api/` package must remain library-extractable with zero HA imports. All
filtering logic lives in the HA integration layer (`coordinator.py`,
`config_flow.py`, `__init__.py`).

### Data Flow

```text
API → get_listings() → ALL listings
  → ListingsCoordinator._async_update_data()
    → Filter by selected_listings (from entry.options)
    → coordinator.data = filtered dict
      → sensor.py creates entities for coordinator.data keys
      → ReservationsCoordinator filters by coordinator.data keys (existing)
```

### Entity Lifecycle

```text
Selected:     Entity exists, device in registry, data updates on refresh
Deselected:   Device removed via registry → all entities cascade-removed
Re-selected:  Next refresh creates new entities → new device auto-created
```

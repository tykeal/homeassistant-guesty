<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Developer Quickstart: Reservations

**Feature**: 003-reservations | **Date**: 2025-07-25

## Prerequisites

- Python >=3.14.2 with `uv` package manager
- Git with pre-commit hooks installed
- Feature 001 (auth/config flow) merged and functional
- Feature 002 (listings/properties) merged and functional
- Familiarity with the `api/` library-shim architecture

## Project Setup

```bash
# Clone and install dependencies
uv sync --all-extras --group dev

# Install pre-commit hooks
uv run pre-commit install

# Run existing tests to confirm baseline
uv run pytest tests/ -x -q
```

## Architecture Overview

This feature adds reservation data retrieval and sensor
exposure, extending the established library-shim and
coordinator patterns:

```text
┌──────────────────────────────────────────────┐
│  Home Assistant                               │
│  ┌──────────────────────────────────────┐    │
│  │  __init__.py                          │    │
│  │  Creates ReservationsCoordinator      │    │
│  └──────────┬───────────────────────────┘    │
│             │                                 │
│  ┌──────────▼───────────────────────────┐    │
│  │  ReservationsCoordinator              │    │
│  │  (DataUpdateCoordinator)              │    │
│  │  Data: dict[str, list[GuestyReservation]] │    │
│  └──────────┬───────────────────────────┘    │
│             │                                 │
│  ┌──────────▼───────────────────────────┐    │
│  │  sensor.py                            │    │
│  │  ReservationStatusSensor              │    │
│  │  ReservationFinancialSensor           │    │
│  │  (attach to listing devices)          │    │
│  └──────────┬───────────────────────────┘    │
│             │                                 │
├─────────────┼─────────────────────────────────┤
│  API Layer  │ (zero HA imports)               │
│  ┌──────────▼───────────────────────────┐    │
│  │  GuestyApiClient.get_reservations()   │    │
│  └──────────┬───────────────────────────┘    │
│             │                                 │
│  ┌──────────▼───────────────────────────┐    │
│  │  api/models.py                        │    │
│  │  GuestyReservation, GuestyGuest,      │    │
│  │  GuestyMoney                          │    │
│  └──────────────────────────────────────┘    │
└──────────────────────────────────────────────┘
```

**Key design rule**: The `api/` package has zero HA imports.
Communication with HA occurs only through:

- `GuestyApiClient` injection (for HTTP requests)
- Constructor parameters (for configuration)
- Return values and exceptions (for results)

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -x -q

# Run only reservation-related tests (after implementation)
uv run pytest tests/api/test_models.py \
  tests/test_coordinator.py \
  tests/test_reservation_sensor.py -x -q -v

# Run with coverage reporting
uv run pytest tests/ \
  --cov=custom_components/guesty --cov-report=term

# Run linting
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/

# Run type checking
uv run mypy custom_components/

# Run docstring coverage check
uv run interrogate custom_components/ -v

# Run all pre-commit hooks
# prek is a developer-local alias (not in repo);
# canonical: uv run pre-commit run --all-files
prek
```

## Key Files

| File | Purpose |
| ---- | ------- |
| `api/models.py` | `GuestyReservation`, `GuestyGuest`, `GuestyMoney` |
| `api/client.py` | `get_reservations()` with pagination/filters |
| `api/const.py` | Reservation endpoints, fields, statuses |
| `coordinator.py` | `ReservationsCoordinator` (new) |
| `sensor.py` | Reservation sensor descriptions + entities |
| `const.py` | Updated `PLATFORMS`, options constants |
| `config_flow.py` | Options flow with reservation settings |
| `__init__.py` | Setup/teardown of reservation coordinator |
| `strings.json` | Translation keys for reservation sensors |

## Key Patterns

### Reservation Priority Selection (FR-006)

```python
def select_primary_reservation(
    reservations: list[GuestyReservation],
) -> GuestyReservation | None:
    """Select the most relevant reservation for a listing.

    Priority:
    1. checked_in (active guest)
    2. confirmed with future check-in (awaiting_checkin)
    3. checked_out (nearest completed)
    4. canceled (nearest canceled)
    """
```

### Sensor State Derivation

```python
# The status sensor state is derived from the primary
# reservation, not stored directly:
if primary is None:
    return "no_reservation"
if primary.status == "checked_in":
    return "checked_in"
if primary.status == "confirmed":
    return "awaiting_checkin"
return primary.status  # checked_out, canceled, unknown
```

### Error Handling

```python
from custom_components.guesty.api import (
    GuestyConnectionError,
    GuestyRateLimitError,
    GuestyResponseError,
)

# In coordinator._async_update_data():
try:
    reservations = await self.api_client.get_reservations(
        past_days=self._past_days,
        future_days=self._future_days,
    )
except (
    GuestyConnectionError,
    GuestyRateLimitError,
    GuestyResponseError,
) as err:
    raise UpdateFailed(f"Reservation fetch failed: {err}") from err
```

## Development Workflow

1. **Write a failing test** (Red)
2. **Implement minimum code** to pass (Green)
3. **Refactor** while keeping tests green
4. **Run linting**: `uv run ruff check custom_components/ tests/`
5. **Run type check**: `uv run mypy custom_components/`
6. **Stage and commit** with sign-off and SPDX headers

## Commit Convention

```bash
git commit -s -m "Feat(reservations): Add reservation models

Implement GuestyReservation, GuestyGuest, and GuestyMoney
frozen dataclasses with from_api_dict factory methods.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

Subject line ≤50 chars, body wrapped at 72 chars, capitalized
conventional commit type, imperative mood, DCO sign-off.

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Guesty Integration Development Guidelines

Auto-generated from feature plans. Last updated: 2026-04-05

## Active Technologies
- Python >=3.14.2 + Home Assistant >=2026.4.0, httpx >=0.28 (002-plan-listings)
- HA config entry storage (via `hass.config_entries`) (002-plan-listings)

- Python 3.13 + httpx (async HTTP),
  homeassistant (>=2026.2.0)
  (001-auth-config-flow, historical baseline)

## Project Structure

```text
custom_components/guesty/
├── api/             # Library-extractable API client
├── __init__.py      # HA integration setup
├── config_flow.py   # HA config flow
├── const.py         # HA constants
├── manifest.json    # Integration metadata
└── strings.json     # Localized UI strings
tests/               # Test suite
specs/               # Feature specifications
```

## Commands

```bash
uv run pytest tests/ -x -q
uv run ruff check custom_components/ tests/
uv run mypy custom_components/ tests/
uv run pre-commit run --all-files
```

## Code Style

Python >=3.14.2: Full type annotations, 100% docstring coverage,
async/await patterns, frozen dataclasses for DTOs

## Recent Changes

- 002-plan-listings: Added Python >=3.14.2 +
  Home Assistant >=2026.4.0, httpx >=0.28

- 001-auth-config-flow: Added Python 3.13 +
  httpx, homeassistant

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Guesty Integration Development Guidelines

Auto-generated from feature plans. Last updated: 2026-04-06

## Active Technologies
- Python ≥3.14.2 (mypy target 3.14, ruff target py314) + homeassistant ≥2026.4.0, httpx ≥0.28, voluptuous (via HA) (007-listing-filtering)
- HA config entry options (persistent key-value via `entry.options`) (007-listing-filtering)

- Python >=3.14.2 + Home Assistant >=2026.4.0, httpx >=0.28 (006-actions-plan)
- Python >=3.14.2 + Home Assistant >=2026.4.0, httpx >=0.28 (002-plan-listings)
- HA config entry storage (via `hass.config_entries`) (002-plan-listings)
- Python >=3.14.2 + httpx (async HTTP), homeassistant (>=2026.4.0) (005-plan-notify)
- N/A — stateless message delivery; no persistent storage (005-plan-notify)

- Python >=3.14.2 + httpx, homeassistant (>=2026.4.0)
  (001-auth-config-flow, updated from initial 3.13/2026.2.0)

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
- 007-listing-filtering: Added Python ≥3.14.2 (mypy target 3.14, ruff target py314) + homeassistant ≥2026.4.0, httpx ≥0.28, voluptuous (via HA)

- 006-actions-plan: Added Python >=3.14.2 +
  Home Assistant >=2026.4.0, httpx >=0.28
- 004-custom-variables-plan: Added Python >=3.14.2 +
  Home Assistant >=2026.4.0, httpx >=0.28
- 003-reservations-plan: Added Python >=3.14.2 +
  Home Assistant >=2026.4.0, httpx >=0.28
- 005-plan-notify: Added Python >=3.14.2 +
  Home Assistant >=2026.4.0, httpx >=0.28
- 002-plan-listings: Added Python >=3.14.2 +
  Home Assistant >=2026.4.0, httpx >=0.28
- 001-auth-config-flow: Added Python >=3.14.2 +
  Home Assistant >=2026.4.0, httpx >=0.28

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->

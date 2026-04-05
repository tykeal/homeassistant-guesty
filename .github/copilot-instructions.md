<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Guesty Integration Development Guidelines

Auto-generated from feature plans. Last updated: 2025-07-18

## Active Technologies

- Python 3.13 + httpx (async HTTP), homeassistant (>=2026.2.0)
  (001-auth-config-flow)

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

Python 3.13: Full type annotations, 100% docstring coverage,
async/await patterns, frozen dataclasses for DTOs

## Recent Changes

- 001-auth-config-flow: Added Python 3.13 + httpx, homeassistant

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->

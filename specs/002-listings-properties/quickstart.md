<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Listings/Properties Feature

**Feature**: 002-listings-properties
**Date**: 2025-07-18

## Prerequisites

- Python >=3.14.2
- uv package manager
- Feature 001 (auth/API client) merged and functional
- Repository cloned and dependencies installed:

```bash
cd /path/to/guesty
uv sync --all-extras --group dev
```

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -x -q

# Run only listing-related tests
uv run pytest tests/test_models.py tests/test_coordinator.py \
  tests/test_sensor.py -x -q -v

# Run with coverage
uv run pytest tests/ --cov=custom_components/guesty \
  --cov-report=term-missing
```

## Linting and Type Checking

```bash
# Ruff lint + format check
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/

# Type checking
uv run mypy custom_components/

# Docstring coverage (must be 100%)
uv run interrogate custom_components/ -v
```

## Pre-Commit Hooks

```bash
# Run all hooks manually (recommended before committing)
# prek is a local alias at ~/.local/bin/prek
# Equivalent: uv run pre-commit run --all-files
prek

# Hooks run automatically on git commit
# If hooks fail: fix issues, git add, commit again
# NEVER use --no-verify
```

## Architecture Overview

```text
┌─────────────────────────────────────────────┐
│              Home Assistant                   │
│                                               │
│  ┌─────────────┐    ┌────────────────────┐   │
│  │ config_flow  │    │    __init__.py      │   │
│  │ (options)    │    │  setup/unload       │   │
│  └─────────────┘    └────────┬───────────┘   │
│                               │               │
│                    ┌──────────▼───────────┐   │
│                    │  ListingsCoordinator  │   │
│                    │  (DataUpdateCoord.)   │   │
│                    └──────────┬───────────┘   │
│                               │               │
│                    ┌──────────▼───────────┐   │
│                    │   sensor.py           │   │
│                    │  GuestyListingSensor  │   │
│                    │  (CoordinatorEntity)  │   │
│                    └──────────┬───────────┘   │
│                               │               │
├───────────────────────────────┼───────────────┤
│              API Layer        │  (no HA deps) │
│                    ┌──────────▼───────────┐   │
│                    │  GuestyApiClient      │   │
│                    │  get_listings()       │   │
│                    └──────────┬───────────┘   │
│                               │               │
│                    ┌──────────▼───────────┐   │
│                    │  api/models.py        │   │
│                    │  GuestyListing        │   │
│                    │  GuestyAddress        │   │
│                    └──────────────────────┘   │
└─────────────────────────────────────────────┘
```

## Key Files

| File | Purpose |
| ---- | ------- |
| `api/models.py` | `GuestyListing`, `GuestyAddress` dataclasses |
| `api/client.py` | `get_listings()` with pagination |
| `api/const.py` | `LISTINGS_PAGE_SIZE`, `LISTINGS_FIELDS` |
| `coordinator.py` | `ListingsCoordinator(DataUpdateCoordinator)` |
| `entity.py` | `GuestyEntity(CoordinatorEntity)` base |
| `sensor.py` | Sensor descriptions + platform setup |
| `const.py` | `PLATFORMS`, options constants |
| `config_flow.py` | Options flow for refresh interval |
| `strings.json` | Translation keys for all sensors |

## Development Workflow

1. **Write a failing test** (Red)
2. **Implement minimum code** to pass (Green)
3. **Refactor** while keeping tests green
4. **Run linting**: `uv run ruff check custom_components/ tests/`
5. **Run type check**: `uv run mypy custom_components/`
6. **Stage and commit** with sign-off:

```bash
git add <files>
git commit -s -m "Type(scope): description

Body explaining what and why.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Commit Conventions

- Types: Fix, Feat, Chore, Docs, Style, Refactor, Perf,
  Test, Revert, CI, Build
- Subject: ≤50 chars, imperative mood, no trailing period
- Body: wrap at 72 chars, explain what and why
- Always include `Co-authored-by` for AI-assisted commits
- Always use `-s` for DCO sign-off

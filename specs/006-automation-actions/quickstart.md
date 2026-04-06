<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Automation Actions

**Feature**: 006 — Automation Actions
**Date**: 2025-07-26

## Prerequisites

- Python >=3.14.2
- `uv` installed (`pip install uv`)
- Repository cloned and dependencies installed:

```bash
cd /path/to/homeassistant-guesty
uv sync
```

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -x -q

# Run only action client tests
uv run pytest tests/api/test_actions.py -x -q

# Run only HA service handler tests
uv run pytest tests/test_actions.py -x -q
```

## Running Linters

```bash
# Ruff check + format
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/

# Type checking
uv run mypy custom_components/

# Docstring coverage
uv run interrogate custom_components/
```

## Pre-Commit

```bash
# Run all hooks (use prek alias)
prek run --all-files
```

## Architecture Overview

```text
┌───────────────────────────────┐
│  HA Automation / Script       │
│  service: guesty.*            │
└──────────┬────────────────────┘
           │ ServiceCall
           ▼
┌───────────────────────────────┐
│  actions.py (HA side)         │
│  - Voluptuous schema          │
│  - Exception translation      │
│  - ServiceValidationError     │
└──────────┬────────────────────┘
           │ method call
           ▼
┌───────────────────────────────┐
│  api/actions.py (library)     │
│  - Input validation           │
│  - GuestyActionsClient        │
│  - ActionResult / Error       │
│  - Zero HA imports            │
└──────────┬────────────────────┘
           │ _request()
           ▼
┌───────────────────────────────┐
│  api/client.py                │
│  - GuestyApiClient            │
│  - Auth, retry, backoff       │
│  - Rate limit handling        │
└──────────┬────────────────────┘
           │ httpx
           ▼
┌───────────────────────────────┐
│  Guesty Open API v1           │
└───────────────────────────────┘
```

## Key Files

| File | Purpose |
| ---- | ------- |
| `api/actions.py` | Write-operation client (no HA) |
| `api/const.py` | Endpoint paths and limits |
| `api/models.py` | `ActionResult` dataclass |
| `api/exceptions.py` | `GuestyActionError` |
| `actions.py` | HA service handlers |
| `services.yaml` | Service definitions |
| `__init__.py` | Client creation and wiring |

## Adding a New Action

1. Add endpoint constant to `api/const.py`
2. Add validation constants to `api/const.py`
3. Write failing test in `tests/api/test_actions.py`
4. Add method to `GuestyActionsClient` in `api/actions.py`
5. Write failing test in `tests/test_actions.py`
6. Add HA service handler in `actions.py`
7. Add service definition to `services.yaml`
8. Add translation keys to `strings.json`
9. Update `api/__init__.py` exports if new types added

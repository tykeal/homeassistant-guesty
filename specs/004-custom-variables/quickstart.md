<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Custom Variables

**Feature**: 004-custom-variables
**Date**: 2025-07-27

## Prerequisites

- Python >=3.14.2
- `uv` installed for dependency management
- Repository cloned and dependencies installed:

  ```bash
  cd guesty
  uv sync --all-extras --group dev
  ```

- Features 001 (Auth & Config Flow), 002 (Listings), and 003
  (Reservations) implemented

## Architecture Overview

```text
┌──────────────────────────────────────────┐
│           Home Assistant                 │
│                                          │
│  __init__.py                             │
│  ├── set_custom_field service handler    │
│  │   ├── Validates inputs (voluptuous)   │
│  │   ├── Checks field in coordinator     │
│  │   ├── Validates type locally          │
│  │   └── Delegates to CF client          │
│  │                                       │
│  coordinator.py                          │
│  └── CustomFieldsDefinitionCoordinator   │
│      └── Polls definitions periodically  │
│                                          │
├──────────────────────────────────────────┤
│           api/ (zero HA imports)         │
│                                          │
│  custom_fields.py                        │
│  └── GuestyCustomFieldsClient            │
│      ├── get_definitions()               │
│      └── set_field()                     │
│                                          │
│  models.py                               │
│  ├── GuestyCustomFieldDefinition         │
│  ├── GuestyCustomFieldUpdate             │
│  └── GuestyCustomFieldResult             │
│                                          │
│  exceptions.py                           │
│  └── GuestyCustomFieldError              │
└──────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│        Guesty Open API v1                │
│  GET  /custom-fields          (defs)     │
│  PUT  /listings/{id}/custom-fields       │
│  PUT  /reservations-v3/{id}/custom-fields│
└──────────────────────────────────────────┘
```

## Development Workflow

### Phase 1: API Client (TDD)

1. Write tests for custom field models:

   ```bash
   uv run pytest tests/api/test_models.py -x -q -k custom
   ```

2. Implement models in `api/models.py`

3. Write tests for custom fields client:

   ```bash
   uv run pytest tests/api/test_custom_fields.py -x -q
   ```

4. Implement `api/custom_fields.py`

### Phase 2: Coordinator & Service (TDD)

1. Write coordinator tests:

   ```bash
   uv run pytest tests/test_coordinator.py -x -q -k custom
   ```

2. Implement coordinator in `coordinator.py`

3. Write service handler tests:

   ```bash
   uv run pytest tests/test_custom_field_service.py -x -q
   ```

4. Wire service in `__init__.py`

### Running All Tests

```bash
uv run pytest tests/ -x -q
```

### Linting

```bash
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/
```

## Key Patterns

### Dependency Injection

The custom fields client follows the same DI pattern as
`GuestyMessagingClient`:

```python
class GuestyCustomFieldsClient:
    def __init__(self, api_client: GuestyApiClient) -> None:
        self._api_client = api_client
```

### Frozen Dataclasses

All models use frozen dataclasses with `from_api_dict()`
factories, consistent with `GuestyListing`, `Conversation`,
etc.:

```python
@dataclass(frozen=True)
class GuestyCustomFieldDefinition:
    field_id: str
    name: str
    field_type: str
    applicable_to: frozenset[str]

    @classmethod
    def from_api_dict(
        cls, data: dict[str, Any]
    ) -> GuestyCustomFieldDefinition | None:
        ...
```

### Service Registration

Services are registered with voluptuous schema and response
support:

```python
hass.services.async_register(
    DOMAIN,
    SERVICE_SET_CUSTOM_FIELD,
    _async_handle_set_custom_field,
    schema=SET_CUSTOM_FIELD_SCHEMA,
    supports_response=SupportsResponse.OPTIONAL,
)
```

### Error Handling

The service handler maps API errors to HA errors:

```python
try:
    result = await cf_client.set_field(...)
except GuestyCustomFieldError as err:
    raise HomeAssistantError(str(err)) from err
```

## Commit Conventions

```bash
git commit -s \
  -m "Feat(api): Add custom fields client" \
  -m "Implement GuestyCustomFieldsClient with
get_definitions() and set_field() methods for reading
custom field definitions and writing values to
listings/reservations." \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

- Types: Fix, Feat, Chore, Docs, Test, Refactor
- Subject ≤50 chars, body wrapped at 72 chars
- DCO sign-off (`-s`) required
- Co-authored-by trailer required for AI-assisted commits

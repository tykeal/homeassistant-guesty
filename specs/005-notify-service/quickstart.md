<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Developer Quickstart: Guesty Notify Service

**Feature**: 005-notify-service | **Date**: 2025-07-24

## Prerequisites

- Python >=3.14.2 with `uv` package manager
- Git with pre-commit hooks installed
- Existing Guesty integration from Feature 001 (auth/config flow)
- Familiarity with the `api/` library-shim architecture

## Project Setup

```bash
# Clone and install dependencies
uv sync

# Install pre-commit hooks
uv run pre-commit install

# Run existing tests to confirm baseline
uv run pytest tests/ -x -q
```

## Architecture Overview

This feature adds guest messaging capabilities following the
established library-shim architecture:

```text
┌──────────────────────────────────────────────────────┐
│  Home Assistant                                      │
│  ┌────────────────────┐                              │
│  │  notify.py         │  NotifyEntity platform       │
│  │  GuestyNotifyEntity│  (HA imports OK)             │
│  └─────────┬──────────┘                              │
│            │ delegates to                             │
│  ┌─────────▼──────────────────────────────────────┐  │
│  │  api/ package          (ZERO HA imports)       │  │
│  │  ┌──────────────────┐  ┌────────────────────┐  │  │
│  │  │ messaging.py     │  │ client.py          │  │  │
│  │  │ GuestyMessaging  │──│ GuestyApiClient    │  │  │
│  │  │ Client           │  │ (existing)         │  │  │
│  │  └──────────────────┘  └────────────────────┘  │  │
│  │  ┌──────────────────┐  ┌────────────────────┐  │  │
│  │  │ models.py        │  │ exceptions.py      │  │  │
│  │  │ Conversation     │  │ GuestyMessageError │  │  │
│  │  │ MessageRequest   │  │ (new)              │  │  │
│  │  │ MessageResult    │  │                    │  │  │
│  │  └──────────────────┘  └────────────────────┘  │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

**Key design rule**: The `api/` package communicates with Home
Assistant only through:

- `GuestyApiClient` injection (for HTTP requests)
- Constructor parameters (for configuration)
- Return values and exceptions (for results)

No `homeassistant.*` imports are permitted in the `api/` package.

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -x -q

# Run only notify-related tests
uv run pytest tests/test_notify.py tests/api/test_messaging.py -x -q

# Run with coverage reporting
uv run pytest tests/ --cov=custom_components.guesty --cov-report=term

# Run linting
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/

# Run type checking
uv run mypy custom_components/

# Run docstring coverage check
uv run interrogate custom_components/ -v

# Run all pre-commit hooks
prek
```

## Key Patterns

### Messaging Client Usage (api/ layer)

```python
from custom_components.guesty.api import (
    GuestyApiClient,
    GuestyMessagingClient,
    GuestyMessageError,
)

# Create messaging client (injected with existing API client)
messaging = GuestyMessagingClient(api_client)

# Send a simple message
result = await messaging.send_message(
    reservation_id="abc123",
    body="Your check-in code is 1234.",
)

# Send with channel selection
result = await messaging.send_message(
    reservation_id="abc123",
    body="Urgent: please check your email.",
    channel="sms",
)

# Send with template variables
result = await messaging.send_message(
    reservation_id="abc123",
    body="Welcome {guest_name}! Code: {access_code}",
    template_variables={
        "guest_name": "Jane",
        "access_code": "5678",
    },
)
```

### Error Handling

```python
from custom_components.guesty.api import (
    GuestyMessageError,
    GuestyRateLimitError,
    GuestyConnectionError,
)

try:
    result = await messaging.send_message(
        reservation_id="abc123",
        body="Hello!",
    )
except GuestyMessageError as err:
    # Messaging-specific failure (conversation not found, etc.)
    logger.error(
        "Message failed for reservation %s: %s",
        err.reservation_id,
        err.message,
    )
except GuestyRateLimitError:
    # Rate limited — handled by retry logic in GuestyApiClient
    # but can propagate if max retries exhausted
    logger.warning("Rate limited sending message")
except GuestyConnectionError:
    # Network failure
    logger.error("Cannot reach Guesty API")
```

### Notify Entity Service Call (HA layer)

```yaml
# Simple message
service: notify.send_message
target:
  entity_id: notify.guesty_guest_messaging
data:
  message: "Your check-in code is 1234."
  reservation_id: "abc123"

# With channel selection
service: notify.send_message
target:
  entity_id: notify.guesty_guest_messaging
data:
  message: "Urgent: contact the host."
  reservation_id: "abc123"
  channel: "sms"

# With template variables
service: notify.send_message
target:
  entity_id: notify.guesty_guest_messaging
data:
  message: "Welcome {guest_name}! Code: {access_code}"
  reservation_id: "abc123"
  template_variables:
    guest_name: "Jane"
    access_code: "5678"
```

## Testing Patterns

### Mocking HTTP with respx

```python
import respx

@respx.mock
async def test_send_message_success(
    api_client: GuestyApiClient,
) -> None:
    """Test successful message delivery."""
    # Mock conversation resolution
    respx.get(
        f"{FAKE_BASE_URL}/communication/conversations",
    ).respond(
        200,
        json={
            "results": [{
                "_id": "conv-123",
                "reservation": {"_id": "res-123"},
                "module": {"type": "platform"},
            }],
        },
    )
    # Mock message send
    respx.post(
        f"{FAKE_BASE_URL}/communication/conversations"
        f"/conv-123/send-message",
    ).respond(
        200,
        json={"_id": "msg-456", "body": "Hello!"},
    )

    messaging = GuestyMessagingClient(api_client)
    result = await messaging.send_message(
        reservation_id="res-123",
        body="Hello!",
    )
    assert result.success
    assert result.message_id == "msg-456"
```

### Testing Notify Entity

```python
async def test_notify_entity_send(
    hass: HomeAssistant,
) -> None:
    """Test notify entity sends message via messaging client."""
    # Set up mock messaging client
    mock_messaging = AsyncMock(spec=GuestyMessagingClient)
    mock_messaging.send_message.return_value = (
        MessageDeliveryResult(success=True, message_id="msg-1")
    )

    # Call the service
    await hass.services.async_call(
        "notify",
        "send_message",
        {
            "message": "Test message",
            "reservation_id": "res-123",
        },
        target={"entity_id": "notify.guesty_guest_messaging"},
        blocking=True,
    )

    mock_messaging.send_message.assert_called_once_with(
        reservation_id="res-123",
        body="Test message",
        channel=None,
        template_variables=None,
    )
```

## Development Workflow

1. **Write a failing test** (Red) — define expected behavior
2. **Implement minimum code** (Green) — make the test pass
3. **Refactor** — improve code while tests stay green
4. **Run linting**: `uv run ruff check custom_components/ tests/`
5. **Run type check**: `uv run mypy custom_components/`
6. **Stage and commit** with sign-off and SPDX headers
7. **Pre-commit hooks** run automatically — fix any failures

## Commit Convention

```bash
git commit -s -m "Feat(notify): Add messaging client

Implement GuestyMessagingClient with conversation resolution,
message sending, and template variable substitution.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

Subject line ≤50 chars, body wrapped at 72 chars, capitalized
conventional commit type, imperative mood, DCO sign-off.

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Guesty Notify Service

**Feature**: 005-notify-service | **Date**: 2025-07-24

## Entity Overview

This feature introduces three new data transfer objects in the `api/`
package and one new HA entity. All DTOs follow the frozen dataclass
pattern established by `CachedToken` in Feature 001.

## API Package Models (`api/models.py`)

### Conversation

Represents a Guesty inbox conversation associated with a reservation.
Retrieved from the Guesty API when resolving a reservation ID to a
messaging target.

```python
@dataclass(frozen=True)
class Conversation:
    """Guesty conversation associated with a reservation."""

    id: str                     # Guesty conversation ID
    reservation_id: str         # Associated reservation ID
    available_channels: tuple[str, ...]  # Module types available
```

**Fields**:

| Field | Type | Validation | Description |
| ----- | ---- | ---------- | ----------- |
| `id` | `str` | Non-empty | Guesty conversation identifier |
| `reservation_id` | `str` | Non-empty | Associated reservation |
| `available_channels` | `tuple[str, ...]` | Non-empty | Available channels |

**Validation rules** (`__post_init__`):

- `id` must be a non-empty string
- `reservation_id` must be a non-empty string
- `available_channels` must be a non-empty tuple of strings

**Relationships**: One reservation maps to one conversation.
Conversations are resolved from the Guesty API, not persisted
locally.

### MessageRequest

Represents a validated request to send a message to a guest.
Constructed by the messaging client after input validation and
template rendering.

```python
@dataclass(frozen=True)
class MessageRequest:
    """Validated request to send a message via Guesty."""

    conversation_id: str        # Target conversation
    body: str                   # Rendered message text
    channel: str | None = None  # Optional channel override
```

**Fields**:

| Field | Type | Validation | Description |
| ----- | ---- | ---------- | ----------- |
| `conversation_id` | `str` | Non-empty | Target conversation ID |
| `body` | `str` | Non-empty, length-limited | Rendered message text |
| `channel` | `str \| None` | Known type if set | Delivery channel override |

**Validation rules** (`__post_init__`):

- `conversation_id` must be a non-empty string
- `body` must be a non-empty string within the configured maximum
  length
- `channel`, if provided, must be one of the known channel types

### MessageDeliveryResult

Represents the outcome of a message send attempt. Returned to the
caller after the API response is processed.

```python
@dataclass(frozen=True)
class MessageDeliveryResult:
    """Outcome of a Guesty message delivery attempt."""

    success: bool               # Whether delivery was accepted
    message_id: str | None = None   # Guesty message ID if successful
    error_details: str | None = None  # Error description if failed
    reservation_id: str | None = None  # For error context
```

**Fields**:

| Field | Type | Validation | Description |
| ----- | ---- | ---------- | ----------- |
| `success` | `bool` | Required | True if Guesty accepted |
| `message_id` | `str \| None` | Present on success | Guesty message ID |
| `error_details` | `str \| None` | Present on failure | Error description |
| `reservation_id` | `str \| None` | Optional | Reservation targeted |

**State transitions**: N/A — this is an immutable result object.

## Exception Hierarchy Extension (`api/exceptions.py`)

### GuestyMessageError

New exception for messaging-specific failures. Extends the existing
exception hierarchy.

```python
class GuestyMessageError(GuestyApiError):
    """Messaging delivery failure with reservation context."""

    def __init__(
        self,
        message: str,
        reservation_id: str | None = None,
        available_channels: tuple[str, ...] | None = None,
    ) -> None:
        ...
```

**Attributes**:

| Attribute | Type | Description |
| --------- | ---- | ----------- |
| `message` | `str` | Human-readable error description |
| `reservation_id` | `str \| None` | Targeted reservation for context |
| `available_channels` | `tuple[str, ...] \| None` | Channels (channel errors) |

**Relationship to existing hierarchy**:

```text
GuestyApiError (base)
├── GuestyAuthError           # 401/403 — EXISTING
├── GuestyRateLimitError      # 429 — EXISTING
├── GuestyConnectionError     # Network — EXISTING
├── GuestyResponseError       # Malformed response — EXISTING
└── GuestyMessageError        # Message delivery — NEW
```

## API Constants Extension (`api/const.py`)

New constants added to the existing module:

```python
# Messaging endpoints
CONVERSATIONS_PATH = "/communication/conversations"
SEND_MESSAGE_PATH = (
    "/communication/conversations/{conversation_id}/send-message"
)

# Validation limits
MAX_MESSAGE_LENGTH = 10000    # Conservative limit
KNOWN_CHANNEL_TYPES = frozenset({
    "email",
    "sms",
    "airbnb2",
    "platform",
    "whatsapp",
})
```

## Messaging Client (`api/messaging.py`)

### GuestyMessagingClient

Orchestrates conversation resolution, input validation, template
rendering, and message delivery. This class has zero HA imports.

```python
class GuestyMessagingClient:
    """Client for sending messages via Guesty conversations."""

    def __init__(self, api_client: GuestyApiClient) -> None:
        ...

    async def resolve_conversation(
        self, reservation_id: str
    ) -> Conversation:
        ...

    async def send_message(
        self,
        reservation_id: str,
        body: str,
        channel: str | None = None,
        template_variables: dict[str, str] | None = None,
    ) -> MessageDeliveryResult:
        ...

    def render_template(
        self,
        template: str,
        variables: dict[str, str],
    ) -> str:
        ...
```

**Methods**:

| Method | Description |
| ------ | ----------- |
| `resolve_conversation` | Resolves reservation ID → Conversation via API |
| `send_message` | Pipeline: validate → resolve → render → send → result |
| `render_template` | Fills `{variable}` placeholders; raises on missing vars |

**Dependencies** (injected):

- `GuestyApiClient` — for all HTTP communication

## HA Notify Entity (`notify.py`)

### GuestyNotifyEntity

Bridges the `GuestyMessagingClient` to Home Assistant's `NotifyEntity`
platform.

```python
class GuestyNotifyEntity(NotifyEntity):
    """Guesty guest messaging notify entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        messaging_client: GuestyMessagingClient,
        entry: ConfigEntry,
    ) -> None:
        ...

    async def async_send_message(
        self, message: str, title: str | None = None
    ) -> None:
        ...
```

**Service call data** (passed via `self.hass` service call context):

| Key | Type | Required | Description |
| --- | ---- | -------- | ----------- |
| `reservation_id` | `str` | Yes | Target reservation |
| `channel` | `str` | No | Delivery channel override |
| `template_variables` | `dict[str, str]` | No | Template substitution values |

**Entity attributes**:

| Attribute | Value |
| --------- | ----- |
| `_attr_has_entity_name` | `True` |
| `_attr_name` | `"Guest messaging"` |
| `unique_id` | `"{entry_id}_notify"` |
| `device_info` | Links to Guesty integration device |

## Integration Setup Changes (`__init__.py`)

**Updated `async_setup_entry`**:

- After creating `GuestyApiClient` (existing), create
  `GuestyMessagingClient(api_client)` and store in
  `hass.data[DOMAIN][entry.entry_id]`

**Updated `const.py`**:

```python
PLATFORMS: list[Platform] = [Platform.NOTIFY]
```

## Data Flow

```text
User → notify.send_message service call
  → GuestyNotifyEntity.async_send_message(message, title)
    → Extract reservation_id, channel, template_variables
      from service call data
    → GuestyMessagingClient.send_message(
        reservation_id, body, channel, template_variables)
      → validate inputs (raise ValueError on invalid)
      → render_template(body, template_variables) if provided
      → resolve_conversation(reservation_id) → Conversation
      → build MessageRequest
      → GuestyApiClient._request("POST", send_message_path, ...)
        → (handles auth, retry, rate limits)
      → parse response → MessageDeliveryResult
    → _async_record_notification() on success
    → raise HomeAssistantError on failure
```

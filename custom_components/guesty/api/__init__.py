# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty API client library (HA-independent).

Public API surface for the Guesty API client package. All symbols
exported here are part of the stable public interface.
"""

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    BASE_URL,
    TOKEN_URL,
)
from custom_components.guesty.api.custom_fields import (
    GuestyCustomFieldsClient,
)
from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyAuthError,
    GuestyConnectionError,
    GuestyCustomFieldError,
    GuestyMessageError,
    GuestyRateLimitError,
    GuestyResponseError,
)
from custom_components.guesty.api.messaging import (
    GuestyMessagingClient,
)
from custom_components.guesty.api.models import (
    CachedToken,
    Conversation,
    GuestyAddress,
    GuestyCustomFieldDefinition,
    GuestyCustomFieldResult,
    GuestyCustomFieldUpdate,
    GuestyGuest,
    GuestyListing,
    GuestyListingsResponse,
    GuestyMoney,
    GuestyReservation,
    GuestyReservationsResponse,
    MessageDeliveryResult,
    MessageRequest,
    TokenStorage,
)

__all__ = [
    "BASE_URL",
    "TOKEN_URL",
    "CachedToken",
    "Conversation",
    "GuestyAddress",
    "GuestyApiClient",
    "GuestyApiError",
    "GuestyAuthError",
    "GuestyConnectionError",
    "GuestyCustomFieldDefinition",
    "GuestyCustomFieldError",
    "GuestyCustomFieldResult",
    "GuestyCustomFieldUpdate",
    "GuestyCustomFieldsClient",
    "GuestyGuest",
    "GuestyListing",
    "GuestyListingsResponse",
    "GuestyMessageError",
    "GuestyMessagingClient",
    "GuestyMoney",
    "GuestyRateLimitError",
    "GuestyReservation",
    "GuestyReservationsResponse",
    "GuestyResponseError",
    "GuestyTokenManager",
    "MessageDeliveryResult",
    "MessageRequest",
    "TokenStorage",
]

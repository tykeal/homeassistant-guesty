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
from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyAuthError,
    GuestyConnectionError,
    GuestyRateLimitError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import (
    CachedToken,
    TokenStorage,
)

__all__ = [
    "BASE_URL",
    "TOKEN_URL",
    "CachedToken",
    "GuestyApiClient",
    "GuestyApiError",
    "GuestyAuthError",
    "GuestyConnectionError",
    "GuestyRateLimitError",
    "GuestyResponseError",
    "GuestyTokenManager",
    "TokenStorage",
]

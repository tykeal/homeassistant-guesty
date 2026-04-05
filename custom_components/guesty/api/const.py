# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""API constants for the Guesty client library."""

# Guesty OAuth 2.0 token endpoint URL.
TOKEN_URL: str = "https://open-api.guesty.com/oauth2/token"

# Guesty Open API v1 base URL.
BASE_URL: str = "https://open-api.guesty.com/v1"

# Default HTTP request timeout in seconds.
DEFAULT_TIMEOUT: int = 30

# Default token refresh buffer in seconds (5 min before expiry).
DEFAULT_REFRESH_BUFFER: int = 300

# Maximum token requests per rolling 24-hour window.
MAX_TOKEN_REQUESTS_PER_WINDOW: int = 5

# Token request rate limit window duration in seconds (24 hours).
TOKEN_WINDOW_SECONDS: int = 86400

# Maximum number of retry attempts for failed requests.
MAX_RETRIES: int = 3

# Initial backoff delay in seconds for exponential backoff.
INITIAL_BACKOFF: float = 1.0

# Multiplier for exponential backoff between retries.
BACKOFF_MULTIPLIER: float = 2.0

# Maximum backoff delay in seconds.
MAX_BACKOFF: float = 30.0

# OAuth 2.0 grant type for Guesty API authentication.
GRANT_TYPE: str = "client_credentials"

# OAuth 2.0 scope for Guesty Open API access.
SCOPE: str = "open-api"

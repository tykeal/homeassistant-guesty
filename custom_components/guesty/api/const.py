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

# Messaging endpoint: list/filter conversations.
CONVERSATIONS_PATH: str = "/communication/conversations"

# Messaging endpoint: send a message to a conversation.
SEND_MESSAGE_PATH: str = "/communication/conversations/{conversation_id}/send-message"

# Maximum message body length in characters.
MAX_MESSAGE_LENGTH: int = 10000

# Known Guesty communication channel types.
KNOWN_CHANNEL_TYPES: frozenset[str] = frozenset(
    {
        "email",
        "sms",
        "airbnb2",
        "platform",
        "whatsapp",
    }
)

# Guesty Open API v1 listings endpoint path.
LISTINGS_ENDPOINT: str = "/listings"

# Maximum listings per page (Guesty API maximum).
LISTINGS_PAGE_SIZE: int = 100

# Fields requested from the listings endpoint.
LISTINGS_FIELDS: tuple[str, ...] = (
    "_id",
    "title",
    "nickname",
    "listed",
    "active",
    "address",
    "propertyType",
    "roomType",
    "type",
    "bedrooms",
    "bathrooms",
    "accommodates",
    "timezone",
    "defaultCheckInTime",
    "defaultCheckOutTime",
    "tags",
    "customFields",
)

# Guesty Open API v1 reservations endpoint path.
RESERVATIONS_ENDPOINT: str = "/reservations"

# Maximum reservations per page (Guesty API maximum).
RESERVATIONS_PAGE_SIZE: int = 100

# Fields requested from the reservations endpoint.
RESERVATIONS_FIELDS: tuple[str, ...] = (
    "_id",
    "listingId",
    "status",
    "confirmationCode",
    "checkIn",
    "checkOut",
    "checkInDateLocalized",
    "checkOutDateLocalized",
    "plannedArrival",
    "plannedDeparture",
    "nightsCount",
    "guestsCount",
    "guest",
    "money.totalPaid",
    "money.balanceDue",
    "money.currency",
    "source",
    "note",
    "customFields",
)

# Actionable reservation statuses for filtering.
ACTIONABLE_STATUSES: frozenset[str] = frozenset(
    {
        "confirmed",
        "checked_in",
        "checked_out",
        "canceled",
    }
)

# Default past days for reservation date range window.
DEFAULT_PAST_DAYS: int = 30

# Default future days for reservation date range window.
DEFAULT_FUTURE_DAYS: int = 365

# Account lookup endpoint path.
ACCOUNTS_ME_ENDPOINT: str = "/accounts/me"

# Custom fields definitions endpoint path (requires account_id).
CUSTOM_FIELDS_ENDPOINT: str = "/accounts/{account_id}/custom-fields"

# Custom fields update path for listings.
LISTING_CUSTOM_FIELDS_PATH: str = "/listings/{listing_id}/custom-fields"

# Custom fields update path for reservations (v3 API).
RESERVATION_CUSTOM_FIELDS_PATH: str = "/reservations-v3/{reservation_id}/custom-fields"

# Recognised custom field value types.
CUSTOM_FIELD_TYPES: frozenset[str] = frozenset(
    {"text", "number", "boolean"},
)

# Valid custom field target entity types.
CUSTOM_FIELD_TARGETS: frozenset[str] = frozenset(
    {"listing", "reservation"},
)

# ── Action Constants ────────────────────────────────────────────────

# Guesty Open API tasks endpoint path.
TASKS_ENDPOINT: str = "/tasks-open-api/tasks"

# Guesty availability/pricing calendar endpoint path.
CALENDAR_ENDPOINT: str = "/availability-pricing/api/calendar/listings/{listing_id}"

# Maximum note text length in characters.
MAX_NOTE_LENGTH: int = 5000

# Maximum task title length in characters.
MAX_TASK_TITLE_LENGTH: int = 255

# Maximum description length in characters.
MAX_DESCRIPTION_LENGTH: int = 5000

# Maximum custom field value length in characters.
MAX_CUSTOM_FIELD_LENGTH: int = 5000

# Valid listing status values for set_listing_status.
VALID_LISTING_STATUSES: frozenset[str] = frozenset(
    {"active", "inactive"},
)

# Valid calendar operations for set_calendar_availability.
VALID_CALENDAR_OPS: frozenset[str] = frozenset(
    {"block", "unblock"},
)

# Separator used when appending notes to reservations.
NOTE_SEPARATOR: str = "\n---\n"

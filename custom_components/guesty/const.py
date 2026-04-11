# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Constants for the Guesty integration."""

from homeassistant.const import Platform

# Integration domain identifier.
DOMAIN: str = "guesty"

# Configuration key for Guesty API client ID.
CONF_CLIENT_ID: str = "client_id"

# Configuration key for Guesty API client secret.
CONF_CLIENT_SECRET: str = "client_secret"

# Platforms supported by this integration.
PLATFORMS: list[Platform] = [Platform.SENSOR]

# Configuration key for the polling scan interval.
CONF_SCAN_INTERVAL: str = "scan_interval"

# Default scan interval in minutes for the listings coordinator.
DEFAULT_SCAN_INTERVAL: int = 15

# Minimum scan interval in minutes for the listings coordinator.
MIN_SCAN_INTERVAL: int = 5

# Configuration key for the reservation polling scan interval.
CONF_RESERVATION_SCAN_INTERVAL: str = "reservation_scan_interval"

# Configuration key for the reservation past days window.
CONF_PAST_DAYS: str = "past_days"

# Configuration key for the reservation future days window.
CONF_FUTURE_DAYS: str = "future_days"

# Default scan interval in minutes for the reservations coordinator.
DEFAULT_RESERVATION_SCAN_INTERVAL: int = 15

# Minimum scan interval in minutes for the reservations coordinator.
MIN_RESERVATION_SCAN_INTERVAL: int = 5

# Service name for setting custom field values.
SERVICE_SET_CUSTOM_FIELD: str = "set_custom_field"

# Service name for retrieving custom field definitions.
SERVICE_GET_CUSTOM_FIELDS: str = "get_custom_fields"

# Service name for retrieving custom field values.
SERVICE_GET_CUSTOM_FIELD_VALUES: str = "get_custom_field_values"

# Service name for sending guest messages.
SERVICE_SEND_GUEST_MESSAGE: str = "send_guest_message"

# Configuration key for the custom fields definition scan interval.
CONF_CF_SCAN_INTERVAL: str = "cf_scan_interval"

# Default scan interval in minutes for custom fields definitions.
DEFAULT_CF_SCAN_INTERVAL: int = 15

# Configuration key for the selected listing IDs filter.
CONF_SELECTED_LISTINGS: str = "selected_listings"

# Configuration key for the tag-based listing pre-filter.
CONF_TAG_FILTER: str = "tag_filter"

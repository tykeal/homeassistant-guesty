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

# Platforms supported by this integration (empty for MVP).
PLATFORMS: list[Platform] = []

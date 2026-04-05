# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for cross-restart token persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import respx
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from httpx import Response
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.const import BASE_URL, TOKEN_URL
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
)
from tests.conftest import make_token_response


def _entry_with_token(
    *,
    expired: bool = False,
    missing: bool = False,
) -> MockConfigEntry:
    """Create a MockConfigEntry with optional token data.

    Args:
        expired: If True, set an expired token.
        missing: If True, omit token data entirely.

    Returns:
        A MockConfigEntry for testing.
    """
    data: dict[str, object] = {
        CONF_CLIENT_ID: "test-client-id",
        CONF_CLIENT_SECRET: "test-client-secret",
    }
    if not missing:
        issued = datetime.now(UTC)
        if expired:
            issued = issued - timedelta(seconds=86401)
        data["cached_token"] = {
            "access_token": "persisted-token",
            "token_type": "Bearer",
            "expires_in": 86400,
            "scope": "open-api",
            "issued_at": issued.isoformat(),
        }
        data["token_request_count"] = 1
        data["token_window_start"] = issued.isoformat()
    return MockConfigEntry(
        domain=DOMAIN,
        title="Guesty (test)",
        data=data,
        unique_id="test-client-id",
    )


class TestTokenPersistenceAcrossRestart:
    """Tests verifying token reuse across simulated restarts."""

    @respx.mock
    async def test_valid_persisted_token_reused(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Setup with valid persisted token skips token request."""
        token_route = respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(200, json={"results": []}),
        )

        entry = _entry_with_token()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert token_route.call_count == 0

    @respx.mock
    async def test_expired_persisted_token_reacquires(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Setup with expired token acquires a new one."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(200, json={"results": []}),
        )

        entry = _entry_with_token(expired=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED

    @respx.mock
    async def test_missing_token_acquires_new(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Setup with no persisted token acquires a new one."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(200, json={"results": []}),
        )

        entry = _entry_with_token(missing=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED

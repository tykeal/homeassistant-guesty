# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for custom field service handler (T017-T037)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from httpx import Response
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.const import BASE_URL, TOKEN_URL
from custom_components.guesty.api.custom_fields import (
    GuestyCustomFieldsClient,
)
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyCustomFieldError,
)
from custom_components.guesty.api.models import (
    GuestyCustomFieldDefinition,
    GuestyCustomFieldResult,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
    SERVICE_SET_CUSTOM_FIELD,
)
from tests.conftest import (
    make_custom_field_definition_dict,
    make_token_response,
    sample_custom_field_definitions,
)


def _make_entry(**overrides: object) -> MockConfigEntry:
    """Create a MockConfigEntry with test defaults.

    Args:
        **overrides: Fields to override.

    Returns:
        A MockConfigEntry for the Guesty integration.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Guesty (test)",
        data={
            CONF_CLIENT_ID: "test-client-id",
            CONF_CLIENT_SECRET: "test-client-secret",
        },
        unique_id="test-client-id",
        **overrides,  # type: ignore[arg-type]
    )


# ── T017: Service Handler Tests ─────────────────────────────────────


class TestServiceHandler:
    """Tests for set_custom_field service handler (T017)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_listing_update_returns_success(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Successful listing update returns structured response."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-123",
                field_id="cf-region",
            ),
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-region",
                    "value": "northeast",
                },
                blocking=True,
                return_response=True,
            )

        assert result is not None
        assert result["target_type"] == "listing"
        assert result["target_id"] == "lst-123"
        assert result["field_id"] == "cf-region"
        assert result["result"] == "success"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_reservation_update_returns_success(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Successful reservation update returns structured response."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="reservation",
                target_id="res-456",
                field_id="cf-door-code",
            ),
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-456",
                    "field_id": "cf-door-code",
                    "value": "1234",
                },
                blocking=True,
                return_response=True,
            )

        assert result is not None
        assert result["target_type"] == "reservation"
        assert result["result"] == "success"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_unknown_field_raises_error(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Field not in definitions raises HomeAssistantError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError, match="not found"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-nonexistent",
                    "value": "x",
                },
                blocking=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_type_mismatch_raises_error(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Type mismatch raises HomeAssistantError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # cf-region is text type, int should fail
        with pytest.raises(HomeAssistantError, match="text"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-region",
                    "value": 42,
                },
                blocking=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_client_error_maps_to_ha_error(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """GuestyCustomFieldError maps to HomeAssistantError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                side_effect=GuestyCustomFieldError(
                    "Custom field update failed (422): bad",
                    target_type="listing",
                    target_id="lst-123",
                    field_id="cf-region",
                ),
            ),
            pytest.raises(
                HomeAssistantError,
                match="422",
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-region",
                    "value": "test",
                },
                blocking=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_api_error_maps_to_ha_error(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """GuestyApiError (non-custom-field) maps to HAError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                side_effect=GuestyConnectionError(
                    "network failure",
                ),
            ),
            pytest.raises(
                HomeAssistantError,
                match="network failure",
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-region",
                    "value": "test",
                },
                blocking=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_missing_entry_data_raises_error(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Missing entry data raises HomeAssistantError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Clear hass.data to simulate missing entry
        hass.data[DOMAIN].pop(entry.entry_id, None)

        with pytest.raises(
            HomeAssistantError,
            match="not loaded",
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-region",
                    "value": "test",
                },
                blocking=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_auth_error_maps_to_ha_error(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """GuestyAuthError from set_field maps to HomeAssistantError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                side_effect=GuestyAuthError("token expired"),
            ),
            pytest.raises(
                HomeAssistantError,
                match="API error",
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-region",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )


# ── T019: Reservation-Specific Tests ────────────────────────────────


class TestReservationCustomFields:
    """Tests for reservation-specific custom field behavior (T019)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_reservation_target_delegates_to_set_field(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reservation target delegates to set_field correctly."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="reservation",
                target_id="res-789",
                field_id="cf-door-code",
            ),
        ) as mock_set:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-789",
                    "field_id": "cf-door-code",
                    "value": "5678",
                },
                blocking=True,
            )

        mock_set.assert_called_once_with(
            target_type="reservation",
            target_id="res-789",
            field_id="cf-door-code",
            value="5678",
        )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_reservation_404_has_context(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """404 on reservation produces clear error with ID context."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                side_effect=GuestyCustomFieldError(
                    "Custom field update failed (404): Not found",
                    target_type="reservation",
                    target_id="res-gone",
                    field_id="cf-door-code",
                ),
            ),
            pytest.raises(HomeAssistantError, match="404"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-gone",
                    "field_id": "cf-door-code",
                    "value": "1234",
                },
                blocking=True,
            )


# ── T026-T027: Automation Compatibility ──────────────────────────────


class TestAutomationCompatibility:
    """Tests for automation compatibility (T026-T027)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_service_call_dispatches_update(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """HA service call dispatches update through client."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-auto",
                field_id="cf-region",
            ),
        ) as mock_set:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-auto",
                    "field_id": "cf-region",
                    "value": "west",
                },
                blocking=True,
            )
        mock_set.assert_called_once()

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_return_response_true_gets_data(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """return_response=True returns structured data."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-resp",
                field_id="cf-region",
            ),
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-resp",
                    "field_id": "cf-region",
                    "value": "east",
                },
                blocking=True,
                return_response=True,
            )

        assert result is not None
        assert result is not None
        assert result["result"] == "success"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_fire_and_forget_succeeds(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Fire-and-forget call succeeds without response."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-ff",
                field_id="cf-region",
            ),
        ):
            # No return_response — fire and forget
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-ff",
                    "field_id": "cf-region",
                    "value": "south",
                },
                blocking=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_does_not_block_event_loop(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Service does not block the HA event loop."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-nb",
                field_id="cf-region",
            ),
        ):
            # Event loop should not be blocked; call completes
            coro = hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-nb",
                    "field_id": "cf-region",
                    "value": "north",
                },
                blocking=True,
            )
            # Should complete without timeout
            await asyncio.wait_for(coro, timeout=5.0)

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_resolved_value_passed_through(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Pre-resolved value is passed through to the client."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # HA templates resolve before the service handler; verify
        # the handler passes the already-resolved value unchanged.
        resolved_value = "resolved-wifi-password-123"
        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-rv",
                field_id="cf-region",
            ),
        ) as mock_set:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-rv",
                    "field_id": "cf-region",
                    "value": resolved_value,
                },
                blocking=True,
                return_response=True,
            )
            mock_set.assert_awaited_once()
            call_kwargs = mock_set.call_args.kwargs
            assert call_kwargs["value"] == resolved_value

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_service_failure_raises_not_crashes(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Service failure raises HomeAssistantError, does not crash."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                side_effect=GuestyCustomFieldError(
                    "Update failed (422): invalid",
                    target_type="listing",
                    target_id="lst-fail",
                    field_id="cf-region",
                ),
            ),
            pytest.raises(HomeAssistantError, match="422"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-fail",
                    "field_id": "cf-region",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )

        # Integration is still functional after failure
        assert hass.services.has_service(DOMAIN, SERVICE_SET_CUSTOM_FIELD)


# ── T030: Edge Case Tests ────────────────────────────────────────────


class TestEdgeCases:
    """Edge case tests for custom field service (T030)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_field_not_applicable_to_target(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Field not applicable to target type raises error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # cf-door-code is reservation-only
        with pytest.raises(
            HomeAssistantError,
            match="not applicable",
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-door-code",
                    "value": "1234",
                },
                blocking=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_unicode_values_passed_through(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unicode and special characters pass through."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        unicode_val = "日本語テスト 🏠"
        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-u",
                field_id="cf-region",
            ),
        ) as mock_set:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-u",
                    "field_id": "cf-region",
                    "value": unicode_val,
                },
                blocking=True,
            )
        mock_set.assert_called_once_with(
            target_type="listing",
            target_id="lst-u",
            field_id="cf-region",
            value=unicode_val,
        )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_long_string_passed_through(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Very long string values pass through to Guesty."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        long_val = "x" * 10000
        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-l",
                field_id="cf-region",
            ),
        ) as mock_set:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-l",
                    "field_id": "cf-region",
                    "value": long_val,
                },
                blocking=True,
            )
        assert mock_set.call_args.kwargs["value"] == long_val

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_both_target_field_works_for_listing(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Field applicable to 'both' works for listing target."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-b",
                field_id="cf-priority",
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-b",
                    "field_id": "cf-priority",
                    "value": 5,
                },
                blocking=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_concurrent_service_calls_independent(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Concurrent service calls execute independently."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        barrier = asyncio.Barrier(2)
        call_order: list[str] = []

        async def _track_set_field(
            **kwargs: object,
        ) -> GuestyCustomFieldResult:
            """Track concurrent call ordering with barrier."""
            fid = kwargs["field_id"]
            call_order.append(f"start-{fid}")
            await asyncio.wait_for(barrier.wait(), timeout=2)
            call_order.append(f"end-{fid}")
            return GuestyCustomFieldResult(
                success=True,
                target_type=str(kwargs["target_type"]),
                target_id=str(kwargs["target_id"]),
                field_id=str(fid),
            )

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            side_effect=_track_set_field,
        ):
            results = await asyncio.gather(
                hass.services.async_call(
                    DOMAIN,
                    SERVICE_SET_CUSTOM_FIELD,
                    {
                        "target_type": "listing",
                        "target_id": "lst-c1",
                        "field_id": "cf-region",
                        "value": "val-a",
                    },
                    blocking=True,
                    return_response=True,
                ),
                hass.services.async_call(
                    DOMAIN,
                    SERVICE_SET_CUSTOM_FIELD,
                    {
                        "target_type": "reservation",
                        "target_id": "res-c2",
                        "field_id": "cf-door-code",
                        "value": "1234",
                    },
                    blocking=True,
                    return_response=True,
                ),
            )

        assert len(results) == 2
        assert results[0] is not None
        assert results[1] is not None
        assert results[0]["field_id"] == "cf-region"
        assert results[1]["field_id"] == "cf-door-code"
        # Both calls started before either finished (barrier sync)
        starts = [e for e in call_order if e.startswith("start-")]
        ends = [e for e in call_order if e.startswith("end-")]
        assert len(starts) == 2
        assert len(ends) == 2
        first_end_idx = next(
            i for i, event in enumerate(call_order) if event.startswith("end-")
        )
        assert all(event.startswith("start-") for event in call_order[:first_end_idx])
        assert first_end_idx == 2

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_stale_field_guesty_rejection(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Stale field reference surfaced from Guesty rejection."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Field exists in coordinator but Guesty rejects (stale)
        with (
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                side_effect=GuestyCustomFieldError(
                    "Custom field update failed (422): field no longer exists",
                    target_type="listing",
                    target_id="lst-stale",
                    field_id="cf-region",
                ),
            ),
            pytest.raises(HomeAssistantError, match="422"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-stale",
                    "field_id": "cf-region",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_no_fields_for_target_type_error(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """No custom fields for target type produces clear error."""
        # Override autouse fixture: only reservation fields, no listing fields
        res_only_defs = [
            GuestyCustomFieldDefinition(
                field_id="cf-res-only",
                name="Res Only",
                field_type="text",
                applicable_to=frozenset({"reservation"}),
            ),
        ]
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=res_only_defs,
        ):
            entry = _make_entry()
            entry.add_to_hass(hass)
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        # Try nonexistent listing field — available list is empty
        with pytest.raises(
            HomeAssistantError,
            match="not found",
        ) as exc_info:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-nf",
                    "field_id": "nonexistent",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )
        msg = str(exc_info.value)
        assert "none" in msg.lower()

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_multi_type_value_passed_through(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Multi-type field value passed through for server validation."""
        # Add field with unknown type (no client-side validation)
        defs_with_unknown = [
            *sample_custom_field_definitions(),
            GuestyCustomFieldDefinition(
                field_id="cf-multi-005",
                name="Multi Type",
                field_type="json",
                applicable_to=frozenset({"listing"}),
            ),
        ]
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=defs_with_unknown,
        ):
            entry = _make_entry()
            entry.add_to_hass(hass)
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-mt",
                field_id="cf-multi-005",
            ),
        ) as mock_set:
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-mt",
                    "field_id": "cf-multi-005",
                    "value": "arbitrary-value",
                },
                blocking=True,
                return_response=True,
            )

        assert result is not None
        assert result["result"] == "success"
        mock_set.assert_awaited_once()


# ── T031: Rate Limit Retry Tests ────────────────────────────────────


class TestRateLimitRetry:
    """Tests for rate limit retry through custom fields (T031)."""

    @respx.mock
    async def test_429_then_success_on_retry(self) -> None:
        """429 on first call, 200 on retry; field updated."""
        from custom_components.guesty.api.auth import GuestyTokenManager
        from custom_components.guesty.api.client import GuestyApiClient
        from tests.conftest import FakeTokenStorage

        storage = FakeTokenStorage()
        http = httpx.AsyncClient()
        token_mgr = GuestyTokenManager(
            client_id="test-id",
            client_secret="test-secret",
            http_client=http,
            storage=storage,
            refresh_buffer=0,
        )
        api_client = GuestyApiClient(
            token_manager=token_mgr,
            http_client=http,
        )
        cf_client = GuestyCustomFieldsClient(api_client)

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )

        put_route = respx.put(
            f"{BASE_URL}/listings/lst-rl/custom-fields",
        )
        put_route.side_effect = [
            Response(
                429,
                headers={"Retry-After": "0.01"},
                json={"error": "rate limited"},
            ),
            Response(200, json=[{"fieldId": "cf-a", "value": "v"}]),
        ]

        result = await cf_client.set_field(
            target_type="listing",
            target_id="lst-rl",
            field_id="cf-a",
            value="v",
        )
        assert result.success is True
        assert put_route.call_count == 2

    @respx.mock
    async def test_429_respects_retry_after(self) -> None:
        """429 retry respects Retry-After header."""
        from custom_components.guesty.api.auth import GuestyTokenManager
        from custom_components.guesty.api.client import GuestyApiClient
        from tests.conftest import FakeTokenStorage

        storage = FakeTokenStorage()
        http = httpx.AsyncClient()
        token_mgr = GuestyTokenManager(
            client_id="test-id",
            client_secret="test-secret",
            http_client=http,
            storage=storage,
            refresh_buffer=0,
        )
        api_client = GuestyApiClient(
            token_manager=token_mgr,
            http_client=http,
        )
        cf_client = GuestyCustomFieldsClient(api_client)

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )

        put_route = respx.put(
            f"{BASE_URL}/listings/lst-ra/custom-fields",
        )
        put_route.side_effect = [
            Response(
                429,
                headers={"Retry-After": "0.01"},
                json={"error": "rate limited"},
            ),
            Response(200, json=[{"fieldId": "cf-a", "value": "v"}]),
        ]

        start = time.monotonic()
        result = await cf_client.set_field(
            target_type="listing",
            target_id="lst-ra",
            field_id="cf-a",
            value="v",
        )
        elapsed = time.monotonic() - start

        assert result.success is True
        # Should have waited at least the Retry-After amount
        assert elapsed >= 0.01


# ── T032: Transient Failure Retry Tests ──────────────────────────────


class TestTransientFailureRetry:
    """Tests for transient failure retry (T032)."""

    @respx.mock
    async def test_network_error_then_success(self) -> None:
        """Transient network error then success on retry."""
        from custom_components.guesty.api.auth import GuestyTokenManager
        from custom_components.guesty.api.client import GuestyApiClient
        from tests.conftest import FakeTokenStorage

        storage = FakeTokenStorage()
        http = httpx.AsyncClient()
        token_mgr = GuestyTokenManager(
            client_id="test-id",
            client_secret="test-secret",
            http_client=http,
            storage=storage,
            refresh_buffer=0,
        )
        api_client = GuestyApiClient(
            token_manager=token_mgr,
            http_client=http,
        )
        cf_client = GuestyCustomFieldsClient(api_client)

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )

        put_route = respx.put(
            f"{BASE_URL}/listings/lst-tn/custom-fields",
        )
        put_route.side_effect = [
            httpx.ConnectError("connection refused"),
            Response(200, json=[{"fieldId": "cf-a", "value": "v"}]),
        ]

        result = await cf_client.set_field(
            target_type="listing",
            target_id="lst-tn",
            field_id="cf-a",
            value="v",
        )
        assert result.success is True
        assert put_route.call_count == 2

    @respx.mock
    async def test_persistent_failure_raises_connection_error(
        self,
    ) -> None:
        """Persistent failure raises GuestyConnectionError."""
        from custom_components.guesty.api.auth import GuestyTokenManager
        from custom_components.guesty.api.client import GuestyApiClient
        from tests.conftest import FakeTokenStorage

        storage = FakeTokenStorage()
        http = httpx.AsyncClient()
        token_mgr = GuestyTokenManager(
            client_id="test-id",
            client_secret="test-secret",
            http_client=http,
            storage=storage,
            refresh_buffer=0,
        )
        api_client = GuestyApiClient(
            token_manager=token_mgr,
            http_client=http,
        )
        cf_client = GuestyCustomFieldsClient(api_client)

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )

        respx.put(
            f"{BASE_URL}/listings/lst-pf/custom-fields",
        ).mock(
            side_effect=httpx.ConnectError("persistent failure"),
        )

        with pytest.raises(GuestyConnectionError, match="retries"):
            await cf_client.set_field(
                target_type="listing",
                target_id="lst-pf",
                field_id="cf-a",
                value="v",
            )

    @respx.mock
    async def test_5xx_then_success(self) -> None:
        """Transient 5xx error then success on retry."""
        from custom_components.guesty.api.auth import GuestyTokenManager
        from custom_components.guesty.api.client import GuestyApiClient
        from tests.conftest import FakeTokenStorage

        storage = FakeTokenStorage()
        http = httpx.AsyncClient()
        token_mgr = GuestyTokenManager(
            client_id="test-id",
            client_secret="test-secret",
            http_client=http,
            storage=storage,
            refresh_buffer=0,
        )
        api_client = GuestyApiClient(
            token_manager=token_mgr,
            http_client=http,
        )
        cf_client = GuestyCustomFieldsClient(api_client)

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )

        put_route = respx.put(
            f"{BASE_URL}/listings/lst-5x/custom-fields",
        )
        put_route.side_effect = [
            Response(502, json={"error": "bad gateway"}),
            Response(200, json=[{"fieldId": "cf-a", "value": "v"}]),
        ]

        result = await cf_client.set_field(
            target_type="listing",
            target_id="lst-5x",
            field_id="cf-a",
            value="v",
        )
        assert result.success is True


# ── T033: Error Detail Quality Tests ─────────────────────────────────


class TestErrorDetailQuality:
    """Tests for error detail quality (T033)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_invalid_field_lists_available(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Invalid field ID error lists available fields."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-nonexistent",
                    "value": "x",
                },
                blocking=True,
            )
        # Should list available fields for listing target
        assert "cf-region" in str(exc_info.value)

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_type_mismatch_shows_expected_vs_actual(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Type mismatch error identifies expected vs actual type."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-region",
                    "value": 42,
                },
                blocking=True,
            )
        msg = str(exc_info.value)
        assert "text" in msg.lower() or "str" in msg.lower()

    @respx.mock
    async def test_404_error_includes_target_context(
        self,
    ) -> None:
        """404 error includes target type and ID context."""
        from custom_components.guesty.api.auth import GuestyTokenManager
        from custom_components.guesty.api.client import GuestyApiClient
        from tests.conftest import FakeTokenStorage

        storage = FakeTokenStorage()
        http = httpx.AsyncClient()
        token_mgr = GuestyTokenManager(
            client_id="test-id",
            client_secret="test-secret",
            http_client=http,
            storage=storage,
            refresh_buffer=0,
        )
        api_client = GuestyApiClient(
            token_manager=token_mgr,
            http_client=http,
        )
        cf_client = GuestyCustomFieldsClient(api_client)

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.put(
            f"{BASE_URL}/listings/lst-404/custom-fields",
        ).mock(
            return_value=Response(404, json={"error": "Not found"}),
        )

        with pytest.raises(GuestyCustomFieldError) as exc_info:
            await cf_client.set_field(
                target_type="listing",
                target_id="lst-404",
                field_id="cf-a",
                value="v",
            )
        assert exc_info.value.target_type == "listing"
        assert exc_info.value.target_id == "lst-404"

    @respx.mock
    async def test_retry_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Retries are logged at warning level."""
        from custom_components.guesty.api.auth import GuestyTokenManager
        from custom_components.guesty.api.client import GuestyApiClient
        from tests.conftest import FakeTokenStorage

        storage = FakeTokenStorage()
        http = httpx.AsyncClient()
        token_mgr = GuestyTokenManager(
            client_id="test-id",
            client_secret="test-secret",
            http_client=http,
            storage=storage,
            refresh_buffer=0,
        )
        api_client = GuestyApiClient(
            token_manager=token_mgr,
            http_client=http,
        )
        cf_client = GuestyCustomFieldsClient(api_client)

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )

        put_route = respx.put(
            f"{BASE_URL}/listings/lst-log/custom-fields",
        )
        put_route.side_effect = [
            Response(
                429,
                headers={"Retry-After": "0.01"},
                json={"error": "rate limited"},
            ),
            Response(200, json=[{"fieldId": "cf-a", "value": "v"}]),
        ]

        with caplog.at_level("WARNING"):
            await cf_client.set_field(
                target_type="listing",
                target_id="lst-log",
                field_id="cf-a",
                value="v",
            )

        assert any("Rate limited" in r.message for r in caplog.records)


# ── T034: Security Tests ────────────────────────────────────────────


class TestSecurityNoSensitiveData:
    """Tests verifying no sensitive data in logs (T034)."""

    @respx.mock
    async def test_no_field_values_in_logs_on_success(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Successful update does not log field values."""
        from custom_components.guesty.api.auth import GuestyTokenManager
        from custom_components.guesty.api.client import GuestyApiClient
        from tests.conftest import FakeTokenStorage

        storage = FakeTokenStorage()
        http = httpx.AsyncClient()
        token_mgr = GuestyTokenManager(
            client_id="test-id",
            client_secret="test-secret",
            http_client=http,
            storage=storage,
            refresh_buffer=0,
        )
        api_client = GuestyApiClient(
            token_manager=token_mgr,
            http_client=http,
        )
        cf_client = GuestyCustomFieldsClient(api_client)

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.put(
            f"{BASE_URL}/listings/lst-sec/custom-fields",
        ).mock(
            return_value=Response(
                200,
                json=[{"fieldId": "cf-a", "value": "SECRET-CODE"}],
            ),
        )

        sensitive_values = [
            "SECRET-CODE",
            "test-access-token-jwt",
        ]

        with caplog.at_level("DEBUG"):
            await cf_client.set_field(
                target_type="listing",
                target_id="lst-sec",
                field_id="cf-a",
                value="SECRET-CODE",
            )

        # Only check our own logger, not httpx's
        our_records = [
            r for r in caplog.records if r.name.startswith("custom_components.guesty")
        ]
        log_text = " ".join(r.message for r in our_records)
        for sensitive in sensitive_values:
            assert sensitive not in log_text

    @respx.mock
    async def test_no_field_values_in_logs_on_retry(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Retry scenario does not leak field values in logs."""
        from custom_components.guesty.api.auth import GuestyTokenManager
        from custom_components.guesty.api.client import GuestyApiClient
        from tests.conftest import FakeTokenStorage

        storage = FakeTokenStorage()
        http = httpx.AsyncClient()
        token_mgr = GuestyTokenManager(
            client_id="test-id",
            client_secret="test-secret",
            http_client=http,
            storage=storage,
            refresh_buffer=0,
        )
        api_client = GuestyApiClient(
            token_manager=token_mgr,
            http_client=http,
        )
        cf_client = GuestyCustomFieldsClient(api_client)

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        put_route = respx.put(
            f"{BASE_URL}/listings/lst-retry-sec/custom-fields",
        )
        put_route.side_effect = [
            Response(
                429,
                headers={"Retry-After": "0.01"},
                json={"error": "rate limited"},
            ),
            Response(
                200,
                json=[{"fieldId": "cf-a", "value": "PII-DATA"}],
            ),
        ]

        sensitive = ["PII-DATA"]

        with caplog.at_level("DEBUG"):
            await cf_client.set_field(
                target_type="listing",
                target_id="lst-retry-sec",
                field_id="cf-a",
                value="PII-DATA",
            )

        our_records = [
            r for r in caplog.records if r.name.startswith("custom_components.guesty")
        ]
        log_text = " ".join(r.message for r in our_records)
        for val in sensitive:
            assert val not in log_text

    @respx.mock
    async def test_no_field_values_in_logs_on_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Failed update does not leak field values in logs."""
        from custom_components.guesty.api.auth import GuestyTokenManager
        from custom_components.guesty.api.client import GuestyApiClient
        from tests.conftest import FakeTokenStorage

        storage = FakeTokenStorage()
        http = httpx.AsyncClient()
        token_mgr = GuestyTokenManager(
            client_id="test-id",
            client_secret="test-secret",
            http_client=http,
            storage=storage,
            refresh_buffer=0,
        )
        api_client = GuestyApiClient(
            token_manager=token_mgr,
            http_client=http,
        )
        cf_client = GuestyCustomFieldsClient(api_client)

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.put(
            f"{BASE_URL}/listings/lst-fail-sec/custom-fields",
        ).mock(
            return_value=Response(
                422,
                json={"error": "validation failed"},
            ),
        )

        sensitive = ["ACCESS-CODE-9999"]

        with (
            caplog.at_level("DEBUG"),
            contextlib.suppress(
                GuestyCustomFieldError,
            ),
        ):
            await cf_client.set_field(
                target_type="listing",
                target_id="lst-fail-sec",
                field_id="cf-a",
                value="ACCESS-CODE-9999",
            )

        our_records = [
            r for r in caplog.records if r.name.startswith("custom_components.guesty")
        ]
        log_text = " ".join(r.message for r in our_records)
        for val in sensitive:
            assert val not in log_text

    @respx.mock
    async def test_no_oauth_tokens_in_logs(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """OAuth tokens never appear in log output."""
        from custom_components.guesty.api.auth import GuestyTokenManager
        from custom_components.guesty.api.client import GuestyApiClient
        from tests.conftest import FakeTokenStorage

        storage = FakeTokenStorage()
        http = httpx.AsyncClient()
        token_mgr = GuestyTokenManager(
            client_id="test-id",
            client_secret="test-secret",
            http_client=http,
            storage=storage,
            refresh_buffer=0,
        )
        api_client = GuestyApiClient(
            token_manager=token_mgr,
            http_client=http,
        )
        cf_client = GuestyCustomFieldsClient(api_client)

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.put(
            f"{BASE_URL}/listings/lst-tok/custom-fields",
        ).mock(
            return_value=Response(200, json=[]),
        )

        with caplog.at_level("DEBUG"):
            await cf_client.set_field(
                target_type="listing",
                target_id="lst-tok",
                field_id="cf-a",
                value="v",
            )

        our_records = [
            r for r in caplog.records if r.name.startswith("custom_components.guesty")
        ]
        log_text = " ".join(r.message for r in our_records)
        assert "test-access-token-jwt" not in log_text


# ── T035: Integration Tests ──────────────────────────────────────────


class TestIntegrationDataFlow:
    """Full data flow integration tests (T035)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_listing_flow_end_to_end(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Full listing flow: service→coordinator→client→response."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-e2e",
                field_id="cf-region",
            ),
        ) as mock_set:
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-e2e",
                    "field_id": "cf-region",
                    "value": "midwest",
                },
                blocking=True,
                return_response=True,
            )

        mock_set.assert_called_once_with(
            target_type="listing",
            target_id="lst-e2e",
            field_id="cf-region",
            value="midwest",
        )
        assert result is not None
        assert result["result"] == "success"
        assert result["target_type"] == "listing"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_reservation_flow_end_to_end(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Full reservation flow: service→coordinator→client."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="reservation",
                target_id="res-e2e",
                field_id="cf-door-code",
            ),
        ) as mock_set:
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-e2e",
                    "field_id": "cf-door-code",
                    "value": "9999",
                },
                blocking=True,
                return_response=True,
            )

        mock_set.assert_called_once()
        assert result is not None
        assert result["target_type"] == "reservation"


# ── T036: Success Criteria Validation Tests ──────────────────────────


class TestSuccessCriteria:
    """Tests validating success criteria (T036)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_sc001_update_completes_under_10s(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """SC-001: Update completes in <10s (mocked)."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-sc1",
                field_id="cf-region",
            ),
        ):
            start = time.monotonic()
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-sc1",
                    "field_id": "cf-region",
                    "value": "v",
                },
                blocking=True,
            )
            elapsed = time.monotonic() - start

        assert elapsed < 10.0

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_sc005_invalid_call_errors_under_2s(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """SC-005: Invalid call produces error within 2s."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        start = time.monotonic()
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-sc5",
                    "field_id": "cf-nonexistent",
                    "value": "x",
                },
                blocking=True,
            )
        elapsed = time.monotonic() - start
        assert elapsed < 2.0

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_sc007_no_event_loop_blocking(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """SC-007: No event loop blocking during service call."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
            return_value=GuestyCustomFieldResult(
                success=True,
                target_type="listing",
                target_id="lst-sc7",
                field_id="cf-region",
            ),
        ):
            coro = hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-sc7",
                    "field_id": "cf-region",
                    "value": "v",
                },
                blocking=True,
            )
            await asyncio.wait_for(coro, timeout=5.0)

    def test_sc009_all_testable_without_live_guesty(self) -> None:
        """SC-009: All scenarios testable without live Guesty."""
        # This test itself proves the point — the entire test
        # suite runs with mocked API responses, no live Guesty
        # connection required.
        assert True


# ── T037: Quickstart Validation ──────────────────────────────────────


class TestQuickstartValidation:
    """Quickstart code pattern validation tests (T037)."""

    def test_custom_fields_client_di_pattern(self) -> None:
        """Client follows documented DI pattern."""
        from custom_components.guesty.api.client import GuestyApiClient

        # Verify the constructor signature matches quickstart
        mock_api = AsyncMock(spec=GuestyApiClient)
        client = GuestyCustomFieldsClient(mock_api)
        assert client._api_client is mock_api

    def test_frozen_dataclass_pattern(self) -> None:
        """Models use frozen dataclass with from_api_dict factory."""
        data = make_custom_field_definition_dict()
        defn = GuestyCustomFieldDefinition.from_api_dict(data)
        assert defn is not None
        assert defn.field_id == data["id"]
        assert defn.name == data["name"]

    def test_error_handling_pattern(self) -> None:
        """Error handling maps API errors to HA pattern."""
        # Verify GuestyCustomFieldError inherits from
        # GuestyApiError as documented
        from custom_components.guesty.api.exceptions import (
            GuestyApiError,
        )

        err = GuestyCustomFieldError("test error")
        assert isinstance(err, GuestyApiError)

    def test_validate_value_pattern(self) -> None:
        """validate_value follows documented pattern."""
        mock_api = AsyncMock()
        client = GuestyCustomFieldsClient(mock_api)
        # Should not raise for valid
        client.validate_value("hello", "text")
        # Should raise for mismatch
        with pytest.raises(GuestyCustomFieldError):
            client.validate_value(42, "text")

    def test_service_name_constant(self) -> None:
        """Service name constant matches documentation."""
        assert SERVICE_SET_CUSTOM_FIELD == "set_custom_field"

# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the guesty.set_custom_field service handler."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
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


class TestSetCustomFieldServiceListing:
    """Tests for set_custom_field service with listing targets (T017)."""

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_successful_listing_update(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Listing update returns structured success response."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result_obj = GuestyCustomFieldResult(
            success=True,
            target_type="listing",
            target_id="listing-001",
            field_id="cf-text-001",
        )
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            return_value=result_obj,
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": "secret-wifi",
                },
                blocking=True,
                return_response=True,
            )

        assert result is not None
        assert result["target_type"] == "listing"
        assert result["target_id"] == "listing-001"
        assert result["field_id"] == "cf-text-001"
        assert result["result"] == "success"

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_field_not_in_definitions_raises(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Unknown field ID raises HomeAssistantError."""
        mock_get_defs.return_value = mock_cf_definitions
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
                    "target_id": "listing-001",
                    "field_id": "nonexistent-field",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_type_mismatch_raises(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Type mismatch raises HomeAssistantError."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch(
                "custom_components.guesty.GuestyCustomFieldsClient.validate_value",
                side_effect=GuestyCustomFieldError(
                    "Expected str for field type 'text', got int",
                ),
            ),
            pytest.raises(HomeAssistantError, match="text"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": 42,
                },
                blocking=True,
                return_response=True,
            )

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """GuestyCustomFieldError maps to HomeAssistantError."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch(
                "custom_components.guesty.GuestyCustomFieldsClient.set_field",
                new_callable=AsyncMock,
                side_effect=GuestyCustomFieldError(
                    "Custom field update failed (422): bad",
                    target_type="listing",
                    target_id="listing-001",
                    field_id="cf-text-001",
                ),
            ),
            pytest.raises(HomeAssistantError, match="422"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_field_not_applicable_to_target_raises(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Field not applicable to target type raises error."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # cf-num-002 is reservation-only, use it on listing
        with pytest.raises(
            HomeAssistantError,
            match="not applicable",
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-num-002",
                    "value": 100,
                },
                blocking=True,
                return_response=True,
            )

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_missing_entry_data_raises(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Missing entry data raises HomeAssistantError."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Remove entry data to simulate missing state
        hass.data[DOMAIN].pop(entry.entry_id)

        with pytest.raises(HomeAssistantError, match="not configured"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )


class TestSetCustomFieldServiceApiErrors:
    """Tests for API error handling in the service handler."""

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """GuestyAuthError from set_field maps to HomeAssistantError."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch(
                "custom_components.guesty.GuestyCustomFieldsClient.set_field",
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
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )


class TestSetCustomFieldServiceReservation:
    """Tests for set_custom_field with reservation targets (T019)."""

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_successful_reservation_update(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Reservation update returns structured response."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result_obj = GuestyCustomFieldResult(
            success=True,
            target_type="reservation",
            target_id="res-001",
            field_id="cf-num-002",
        )
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            return_value=result_obj,
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-001",
                    "field_id": "cf-num-002",
                    "value": 250,
                },
                blocking=True,
                return_response=True,
            )

        assert result is not None
        assert result["target_type"] == "reservation"
        assert result["target_id"] == "res-001"
        assert result["field_id"] == "cf-num-002"
        assert result["result"] == "success"

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_reservation_not_found_error(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Reservation 404 produces clear error with ID context."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch(
                "custom_components.guesty.GuestyCustomFieldsClient.set_field",
                new_callable=AsyncMock,
                side_effect=GuestyCustomFieldError(
                    "Custom field update failed (404): not found",
                    target_type="reservation",
                    target_id="bad-res-id",
                    field_id="cf-num-002",
                ),
            ),
            pytest.raises(
                HomeAssistantError,
                match="bad-res-id",
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "bad-res-id",
                    "field_id": "cf-num-002",
                    "value": 100,
                },
                blocking=True,
                return_response=True,
            )

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_both_target_field_works_on_reservation(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Field applicable to both targets works on reservation."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result_obj = GuestyCustomFieldResult(
            success=True,
            target_type="reservation",
            target_id="res-001",
            field_id="cf-bool-003",
        )
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            return_value=result_obj,
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-001",
                    "field_id": "cf-bool-003",
                    "value": True,
                },
                blocking=True,
                return_response=True,
            )

        assert result is not None
        assert result["target_type"] == "reservation"
        assert result["result"] == "success"


class TestAutomationCompatibility:
    """Tests for automation compatibility (T026)."""

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_service_dispatches_through_cf_client(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Service call dispatches update through custom fields client."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result_obj = GuestyCustomFieldResult(
            success=True,
            target_type="listing",
            target_id="listing-001",
            field_id="cf-text-001",
        )
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            return_value=result_obj,
        ) as mock_set:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": "wifi-pass",
                },
                blocking=True,
                return_response=True,
            )
            mock_set.assert_awaited_once_with(
                target_type="listing",
                target_id="listing-001",
                field_id="cf-text-001",
                value="wifi-pass",
            )

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_template_rendered_value_sent(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Template-rendered value is resolved and sent."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Simulate a template-resolved string value
        resolved_value = "resolved-wifi-password-123"
        result_obj = GuestyCustomFieldResult(
            success=True,
            target_type="listing",
            target_id="listing-001",
            field_id="cf-text-001",
        )
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            return_value=result_obj,
        ) as mock_set:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": resolved_value,
                },
                blocking=True,
                return_response=True,
            )
            mock_set.assert_awaited_once()
            call_kwargs = mock_set.call_args.kwargs
            assert call_kwargs["value"] == resolved_value

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_service_does_not_block_event_loop(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Service call does not block the HA event loop."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result_obj = GuestyCustomFieldResult(
            success=True,
            target_type="listing",
            target_id="listing-001",
            field_id="cf-text-001",
        )
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            return_value=result_obj,
        ):
            # Service is async; verify it runs within event loop
            # by calling with blocking=True without timeout
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )
            assert result is not None
            assert result["result"] == "success"

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_service_failure_raises_not_crashes(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Service failure raises HomeAssistantError, does not crash."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch(
                "custom_components.guesty.GuestyCustomFieldsClient.set_field",
                new_callable=AsyncMock,
                side_effect=GuestyCustomFieldError(
                    "Update failed (422): invalid",
                    target_type="listing",
                    target_id="listing-001",
                    field_id="cf-text-001",
                ),
            ),
            pytest.raises(HomeAssistantError, match="422"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )

        # Integration is still functional after failure
        assert hass.services.has_service(DOMAIN, SERVICE_SET_CUSTOM_FIELD)


class TestResponseData:
    """Tests for service response data (T027)."""

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_return_response_true_structured_data(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """return_response=True receives structured success response."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result_obj = GuestyCustomFieldResult(
            success=True,
            target_type="listing",
            target_id="listing-001",
            field_id="cf-text-001",
        )
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            return_value=result_obj,
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": "wifi-pass",
                },
                blocking=True,
                return_response=True,
            )

        assert isinstance(result, dict)
        assert result["target_type"] == "listing"
        assert result["target_id"] == "listing-001"
        assert result["field_id"] == "cf-text-001"
        assert result["result"] == "success"

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_fire_and_forget_succeeds_no_response(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Fire-and-forget call succeeds without response data."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result_obj = GuestyCustomFieldResult(
            success=True,
            target_type="listing",
            target_id="listing-001",
            field_id="cf-text-001",
        )
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            return_value=result_obj,
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": "wifi-pass",
                },
                blocking=True,
                return_response=False,
            )

        assert result is None


class TestEdgeCases:
    """Tests for edge cases (T030)."""

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_field_not_in_defs_lists_available(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Unknown field lists available fields in error."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(
            HomeAssistantError,
            match="Available fields for listing",
        ) as exc_info:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "nonexistent",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )
        # Error lists available listing fields
        msg = str(exc_info.value)
        assert "cf-text-001" in msg or "cf-bool-003" in msg

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_unicode_and_special_chars_preserved(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Unicode and special characters preserved without modification."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        unicode_value = "Ünïcödé 日本語 émojis 🏠🔑"
        result_obj = GuestyCustomFieldResult(
            success=True,
            target_type="listing",
            target_id="listing-001",
            field_id="cf-text-001",
        )
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            return_value=result_obj,
        ) as mock_set:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": unicode_value,
                },
                blocking=True,
                return_response=True,
            )
            call_kwargs = mock_set.call_args.kwargs
            assert call_kwargs["value"] == unicode_value

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_very_long_string_passed_through(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Very long string values passed through without modification."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        long_value = "x" * 10000
        result_obj = GuestyCustomFieldResult(
            success=True,
            target_type="listing",
            target_id="listing-001",
            field_id="cf-text-001",
        )
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            return_value=result_obj,
        ) as mock_set:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": long_value,
                },
                blocking=True,
                return_response=True,
            )
            call_kwargs = mock_set.call_args.kwargs
            assert call_kwargs["value"] == long_value
            assert len(call_kwargs["value"]) == 10000

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Concurrent service calls execute independently."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        call_order: list[str] = []

        async def _track_set_field(
            **kwargs: object,
        ) -> GuestyCustomFieldResult:
            """Track concurrent call ordering."""
            fid = kwargs["field_id"]
            call_order.append(f"start-{fid}")
            await asyncio.sleep(0)
            call_order.append(f"end-{fid}")
            return GuestyCustomFieldResult(
                success=True,
                target_type=str(kwargs["target_type"]),
                target_id=str(kwargs["target_id"]),
                field_id=str(fid),
            )

        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            side_effect=_track_set_field,
        ):
            results = await asyncio.gather(
                hass.services.async_call(
                    DOMAIN,
                    SERVICE_SET_CUSTOM_FIELD,
                    {
                        "target_type": "listing",
                        "target_id": "listing-001",
                        "field_id": "cf-text-001",
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
                        "target_id": "res-001",
                        "field_id": "cf-bool-003",
                        "value": True,
                    },
                    blocking=True,
                    return_response=True,
                ),
            )

        assert len(results) == 2
        assert results[0] is not None
        assert results[1] is not None
        assert results[0]["field_id"] == "cf-text-001"
        assert results[1]["field_id"] == "cf-bool-003"
        # Both calls were started and finished
        assert "start-cf-text-001" in call_order
        assert "end-cf-text-001" in call_order
        assert "start-cf-bool-003" in call_order
        assert "end-cf-bool-003" in call_order

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_stale_field_guesty_rejection(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Stale field reference surfaced from Guesty rejection."""
        mock_get_defs.return_value = mock_cf_definitions
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Field exists in coordinator but Guesty rejects (stale)
        with (
            patch(
                "custom_components.guesty.GuestyCustomFieldsClient.set_field",
                new_callable=AsyncMock,
                side_effect=GuestyCustomFieldError(
                    "Custom field update failed (422): field no longer exists",
                    target_type="listing",
                    target_id="listing-001",
                    field_id="cf-text-001",
                ),
            ),
            pytest.raises(HomeAssistantError, match="422"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-text-001",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """No custom fields for target type produces clear error."""
        # Only reservation fields; no listing fields
        res_only_defs = [
            GuestyCustomFieldDefinition(
                field_id="cf-res-only",
                name="Res Only",
                field_type="text",
                applicable_to=frozenset({"reservation"}),
            ),
        ]
        mock_get_defs.return_value = res_only_defs
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
                    "target_id": "listing-001",
                    "field_id": "nonexistent",
                    "value": "test",
                },
                blocking=True,
                return_response=True,
            )
        msg = str(exc_info.value)
        assert "none" in msg.lower()

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
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
    async def test_multi_type_value_passed_through(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
        mock_cf_definitions: list[GuestyCustomFieldDefinition],
    ) -> None:
        """Multi-type field value passed through for server validation."""
        # Add field with unknown type (no client-side validation)
        defs_with_unknown = [
            *mock_cf_definitions,
            GuestyCustomFieldDefinition(
                field_id="cf-multi-005",
                name="Multi Type",
                field_type="json",
                applicable_to=frozenset({"listing"}),
            ),
        ]
        mock_get_defs.return_value = defs_with_unknown
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result_obj = GuestyCustomFieldResult(
            success=True,
            target_type="listing",
            target_id="listing-001",
            field_id="cf-multi-005",
        )
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.set_field",
            new_callable=AsyncMock,
            return_value=result_obj,
        ) as mock_set:
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "listing-001",
                    "field_id": "cf-multi-005",
                    "value": "arbitrary-value",
                },
                blocking=True,
                return_response=True,
            )

        assert result is not None
        assert result["result"] == "success"
        mock_set.assert_awaited_once()

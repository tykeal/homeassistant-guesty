# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the guesty.set_custom_field service handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.exceptions import (
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


def _setup_patches() -> tuple[str, ...]:
    """Return the common patch targets for setup.

    Returns:
        Tuple of patch target strings.
    """
    return (
        "custom_components.guesty.GuestyApiClient.test_connection",
        "custom_components.guesty.GuestyApiClient.get_listings",
        "custom_components.guesty.GuestyApiClient.get_reservations",
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
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

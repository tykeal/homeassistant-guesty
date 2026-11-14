# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty config flow."""

from __future__ import annotations

import logging
from collections.abc import Generator
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import respx
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_USER,
    ConfigFlowResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from httpx import Response
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.const import BASE_URL, TOKEN_URL
from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyAuthError,
    GuestyConnectionError,
    GuestyRateLimitError,
)
from custom_components.guesty.api.models import GuestyAddress, GuestyListing
from custom_components.guesty.const import (
    CONF_FUTURE_DAYS,
    CONF_PAST_DAYS,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_RESERVATIONS_API,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    CONF_TAG_FILTER,
    DEFAULT_RESERVATIONS_API,
    DOMAIN,
    MIN_RESERVATION_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
)
from tests.conftest import make_token_response

VALID_INPUT = {
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
}

# ── Test listings for the 3-step options flow ───────────────────────

_TEST_LISTINGS: list[GuestyListing] = [
    GuestyListing(
        id="lst_001",
        title="Beach House",
        nickname="beach",
        status="active",
        address=GuestyAddress(
            full="123 Ocean Dr, Miami, FL 33139, US",
            street="123 Ocean Dr",
            city="Miami",
            state="FL",
            zipcode="33139",
            country="US",
        ),
        property_type="apartment",
        room_type="entire_home",
        listing_type="SINGLE",
        bedrooms=2,
        bathrooms=1.5,
        accommodates=5,
        timezone="America/New_York",
        check_in_time="15:00",
        check_out_time="11:00",
        tags=("premium", "beachfront"),
        custom_fields=MappingProxyType({}),
    ),
    GuestyListing(
        id="lst_002",
        title="Mountain Cabin",
        nickname="cabin",
        status="active",
        address=GuestyAddress(
            full="456 Pine Rd, Asheville, NC 28801, US",
            street="456 Pine Rd",
            city="Asheville",
            state="NC",
            zipcode="28801",
            country="US",
        ),
        property_type="house",
        room_type="entire_home",
        listing_type="SINGLE",
        bedrooms=3,
        bathrooms=2.0,
        accommodates=6,
        timezone="America/New_York",
        check_in_time="16:00",
        check_out_time="10:00",
        tags=("mountain",),
        custom_fields=MappingProxyType({}),
    ),
    GuestyListing(
        id="lst_003",
        title="City Apartment",
        nickname="city",
        status="active",
        address=None,
        property_type="apartment",
        room_type="private_room",
        listing_type="SINGLE",
        bedrooms=1,
        bathrooms=1.0,
        accommodates=2,
        timezone="America/New_York",
        check_in_time="14:00",
        check_out_time="11:00",
        tags=(),
        custom_fields=MappingProxyType({}),
    ),
]

_DEFAULT_INTERVALS = {
    CONF_SCAN_INTERVAL: 15,
    CONF_RESERVATION_SCAN_INTERVAL: 15,
    CONF_PAST_DAYS: 30,
    CONF_FUTURE_DAYS: 365,
}


def _setup_api_client(
    hass: HomeAssistant,
    entry_id: str,
    listings: list[GuestyListing] | None = None,
) -> AsyncMock:
    """Populate hass.data with a mock API client for options flow.

    Args:
        hass: Home Assistant instance.
        entry_id: Config entry ID.
        listings: Listings to return from get_listings.

    Returns:
        The mock API client.
    """
    if listings is None:
        listings = list(_TEST_LISTINGS)
    mock_api = AsyncMock()
    mock_api.get_listings = AsyncMock(return_value=listings)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry_id] = {"api_client": mock_api}
    return mock_api


async def _navigate_to_intervals(
    hass: HomeAssistant,
    entry_id: str,
    flow_id: str | None = None,
    tag_filter: list[str] | None = None,
    selected_ids: list[str] | None = None,
) -> ConfigFlowResult:
    """Navigate options flow through init and select_listings.

    Args:
        hass: Home Assistant instance.
        entry_id: Config entry ID.
        flow_id: Flow ID if already started.
        tag_filter: Tags for init step.
        selected_ids: IDs for select_listings step.

    Returns:
        Flow result at the intervals step.
    """
    if tag_filter is None:
        tag_filter = []
    if selected_ids is None:
        selected_ids = [lst.id for lst in _TEST_LISTINGS]

    if flow_id is None:
        init_result = await hass.config_entries.options.async_init(
            entry_id,
        )
        flow_id = init_result["flow_id"]

    result = await hass.config_entries.options.async_configure(
        flow_id,
        user_input={CONF_TAG_FILTER: tag_filter},
    )
    assert result["step_id"] == "select_listings"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_SELECTED_LISTINGS: selected_ids},
    )
    assert result["step_id"] == "intervals"
    return result


class TestStepUser:
    """Tests for the user config flow step."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    async def test_form_displayed(self, hass: HomeAssistant) -> None:
        """User step shows the credential form."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_successful_entry_creation(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Valid credentials create a config entry."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["client_id"] == "test-client-id"
        assert result["data"]["client_secret"] == "test-client-secret"

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyAuthError("bad creds"),
    )
    async def test_invalid_auth_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Invalid credentials show invalid_auth error."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyConnectionError("network"),
    )
    async def test_cannot_connect_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Connection failure shows cannot_connect error."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyRateLimitError("rate limited"),
    )
    async def test_rate_limited_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Rate limit shows rate_limited error."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "rate_limited"}

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_duplicate_detection(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Duplicate client_id triggers already_configured abort."""
        # Create first entry
        first = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert first["type"] is FlowResultType.CREATE_ENTRY

        # Attempt duplicate
        second = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert second["type"] is FlowResultType.ABORT
        assert second["reason"] == "already_configured"

    async def test_form_fields(self, hass: HomeAssistant) -> None:
        """Form includes client_id and client_secret fields."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )
        schema = result["data_schema"]
        assert schema is not None
        keys = [str(k) for k in schema.schema]
        assert "client_id" in keys
        assert "client_secret" in keys

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=RuntimeError("unexpected"),
    )
    async def test_unknown_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unexpected exception shows unknown error."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "unknown"}


class TestStepReauth:
    """Tests for the reauth config flow step."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_reauth_form_displayed(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reauth step shows the confirmation form."""
        # Create initial entry
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_reauth_success(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Successful reauth updates entry and aborts."""
        # Create initial entry
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "client_id": "test-client-id",
                "client_secret": "new-secret",
            },
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyAuthError("bad creds"),
    )
    async def test_reauth_auth_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reauth auth error shows invalid_auth."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=VALID_INPUT,
            unique_id="test-client-id",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "client_id": "test-client-id",
                "client_secret": "bad",
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyConnectionError("network"),
    )
    async def test_reauth_connection_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reauth connection error shows cannot_connect."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=VALID_INPUT,
            unique_id="test-client-id",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "client_id": "test-client-id",
                "client_secret": "bad",
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyRateLimitError("rate limited"),
    )
    async def test_reauth_rate_limited_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reauth rate limit shows rate_limited."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=VALID_INPUT,
            unique_id="test-client-id",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "client_id": "test-client-id",
                "client_secret": "bad",
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "rate_limited"}

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=RuntimeError("unexpected"),
    )
    async def test_reauth_unknown_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reauth unexpected error shows unknown."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=VALID_INPUT,
            unique_id="test-client-id",
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "client_id": "test-client-id",
                "client_secret": "bad",
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "unknown"}


class TestValidateCredentialsDirect:
    """Tests for _validate_credentials without mocking it."""

    @respx.mock
    async def test_validate_credentials_success(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_validate_credentials succeeds with valid responses."""
        from custom_components.guesty.config_flow import (
            _validate_credentials,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json={"results": []},
            ),
        )

        await _validate_credentials(hass, "test-id", "test-secret")


class TestNullStorage:
    """Tests for _NullStorage used during config flow validation."""

    async def test_load_token_returns_none(self) -> None:
        """_NullStorage.load_token returns None."""
        from custom_components.guesty.config_flow import _NullStorage

        storage = _NullStorage()
        result = await storage.load_token()
        assert result is None


class TestOptionsFlow:
    """Tests for GuestyOptionsFlowHandler."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_options_flow_presents_init_form(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Options flow shows init step with tag filter."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )

        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)
        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_options_flow_valid_interval_saves(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Valid scan_interval saves to entry.options."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(
            hass,
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 10,
                CONF_RESERVATION_SCAN_INTERVAL: 15,
                CONF_PAST_DAYS: 30,
                CONF_FUTURE_DAYS: 365,
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_SCAN_INTERVAL] == 10

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_options_flow_below_minimum_rejects(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Interval below MIN_SCAN_INTERVAL raises error."""
        from homeassistant.data_entry_flow import InvalidData

        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(
            hass,
            entry.entry_id,
        )
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                result["flow_id"],
                user_input={
                    CONF_SCAN_INTERVAL: MIN_SCAN_INTERVAL - 1,
                    CONF_RESERVATION_SCAN_INTERVAL: 15,
                    CONF_PAST_DAYS: 30,
                    CONF_FUTURE_DAYS: 365,
                },
            )

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_config_flow_has_options_flow(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """GuestyConfigFlow has async_get_options_flow."""
        from custom_components.guesty.config_flow import GuestyConfigFlow

        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]

        handler = GuestyConfigFlow.async_get_options_flow(entry)
        assert handler is not None


class TestReservationOptionsFlow:
    """Tests for reservation-specific options flow fields."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_options_flow_presents_reservation_fields(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Options flow shows init step form."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_reservation_interval_saves(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Valid reservation_scan_interval saves to options."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(
            hass,
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 15,
                CONF_RESERVATION_SCAN_INTERVAL: 10,
                CONF_PAST_DAYS: 30,
                CONF_FUTURE_DAYS: 365,
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_RESERVATION_SCAN_INTERVAL] == 10
        assert entry.options[CONF_PAST_DAYS] == 30
        assert entry.options[CONF_FUTURE_DAYS] == 365

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_reservation_interval_below_minimum_rejects(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reservation interval below minimum raises error."""
        from homeassistant.data_entry_flow import InvalidData

        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(
            hass,
            entry.entry_id,
        )
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                result["flow_id"],
                user_input={
                    CONF_SCAN_INTERVAL: 15,
                    CONF_RESERVATION_SCAN_INTERVAL: (MIN_RESERVATION_SCAN_INTERVAL - 1),
                    CONF_PAST_DAYS: 30,
                    CONF_FUTURE_DAYS: 365,
                },
            )

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_past_days_accepts_positive_integer(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """past_days accepts positive integers."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(
            hass,
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 15,
                CONF_RESERVATION_SCAN_INTERVAL: 15,
                CONF_PAST_DAYS: 7,
                CONF_FUTURE_DAYS: 365,
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_PAST_DAYS] == 7

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_past_days_zero_rejects(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """past_days of 0 raises validation error."""
        from homeassistant.data_entry_flow import InvalidData

        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(
            hass,
            entry.entry_id,
        )
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                result["flow_id"],
                user_input={
                    CONF_SCAN_INTERVAL: 15,
                    CONF_RESERVATION_SCAN_INTERVAL: 15,
                    CONF_PAST_DAYS: 0,
                    CONF_FUTURE_DAYS: 365,
                },
            )


class TestListingOptionsFlow:
    """Tests for the 3-step listing selection options flow.

    Covers T010-T017: init, select_listings, and intervals steps.
    """

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_init_fetches_listings_and_transitions(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T010: init fetches listings and shows select_listings."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        mock_api = _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: []},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_listings"
        mock_api.get_listings.assert_awaited_once()

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_init_returns_cannot_connect_on_api_error(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T011: init shows cannot_connect on API failure."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        mock_api = _setup_api_client(hass, entry.entry_id)
        mock_api.get_listings.side_effect = GuestyApiError(
            "connection failed",
        )

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: []},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"
        assert result["errors"] == {"base": "cannot_connect"}

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_init_returns_invalid_auth_on_auth_error(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Init shows invalid_auth on GuestyAuthError."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        mock_api = _setup_api_client(hass, entry.entry_id)
        mock_api.get_listings.side_effect = GuestyAuthError(
            "bad auth",
        )

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: []},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"
        assert result["errors"] == {"base": "invalid_auth"}

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_init_returns_rate_limited_on_rate_error(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Init shows rate_limited on GuestyRateLimitError."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        mock_api = _setup_api_client(hass, entry.entry_id)
        mock_api.get_listings.side_effect = GuestyRateLimitError(
            "rate limited",
        )

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: []},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"
        assert result["errors"] == {"base": "rate_limited"}

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_select_listings_respects_prior_selection(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Prior selected_listings used as default selection."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        hass.config_entries.async_update_entry(
            entry,
            options={
                CONF_SELECTED_LISTINGS: ["lst_001", "lst_003"],
            },
        )
        _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: []},
        )
        assert result["step_id"] == "select_listings"

        schema = result["data_schema"]
        assert schema is not None
        schema_dict = schema.schema
        selector_key = next(iter(schema_dict))
        default = selector_key.default()
        assert default == ["lst_001", "lst_003"]

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_select_listings_builds_labeled_options(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T012: select_listings labels as 'title — address'."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: []},
        )
        assert result["step_id"] == "select_listings"

        schema = result["data_schema"]
        assert schema is not None
        schema_dict = schema.schema
        selector_key = next(iter(schema_dict))
        selector = schema_dict[selector_key]
        options = selector.config["options"]

        labels = {opt["value"]: opt["label"] for opt in options}
        assert labels["lst_001"] == (
            "Beach House \u2014 123 Ocean Dr, Miami, FL 33139, US"
        )
        assert labels["lst_002"] == (
            "Mountain Cabin \u2014 456 Pine Rd, Asheville, NC 28801, US"
        )
        assert labels["lst_003"] == ("City Apartment \u2014 No address")

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_select_listings_preselects_all_when_absent(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T013: All listings preselected when no prior config."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        assert CONF_SELECTED_LISTINGS not in entry.options
        _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: []},
        )
        assert result["step_id"] == "select_listings"

        schema = result["data_schema"]
        assert schema is not None
        schema_dict = schema.schema
        selector_key = next(iter(schema_dict))
        default = selector_key.default()
        assert set(default) == {
            "lst_001",
            "lst_002",
            "lst_003",
        }

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_select_listings_persists_selected_ids(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T014: Selected listing IDs saved in entry options."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(
            hass,
            entry.entry_id,
            selected_ids=["lst_001", "lst_003"],
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input=_DEFAULT_INTERVALS,
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_SELECTED_LISTINGS] == [
            "lst_001",
            "lst_003",
        ]

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_select_listings_empty_selection_error(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T015: Empty selection shows no_listings_selected."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: []},
        )
        assert result["step_id"] == "select_listings"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_SELECTED_LISTINGS: []},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_listings"
        assert result["errors"] == {"base": "no_listings_selected"}

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_intervals_shows_defaults_and_saves(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T016: Intervals step shows current values and saves."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(
            hass,
            entry.entry_id,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "intervals"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 20,
                CONF_RESERVATION_SCAN_INTERVAL: 10,
                CONF_PAST_DAYS: 14,
                CONF_FUTURE_DAYS: 180,
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_SCAN_INTERVAL] == 20
        assert entry.options[CONF_RESERVATION_SCAN_INTERVAL] == 10
        assert entry.options[CONF_PAST_DAYS] == 14
        assert entry.options[CONF_FUTURE_DAYS] == 180

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_complete_three_step_flow(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T017: Full 3-step flow produces merged options dict."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: ["premium"]},
        )
        assert result["step_id"] == "select_listings"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SELECTED_LISTINGS: ["lst_001"],
            },
        )
        assert result["step_id"] == "intervals"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 10,
                CONF_RESERVATION_SCAN_INTERVAL: 20,
                CONF_PAST_DAYS: 7,
                CONF_FUTURE_DAYS: 90,
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY

        opts = entry.options
        assert opts[CONF_TAG_FILTER] == ["premium"]
        assert opts[CONF_SELECTED_LISTINGS] == ["lst_001"]
        assert opts[CONF_SCAN_INTERVAL] == 10
        assert opts[CONF_RESERVATION_SCAN_INTERVAL] == 20
        assert opts[CONF_PAST_DAYS] == 7
        assert opts[CONF_FUTURE_DAYS] == 90


class TestTagFilterOptionsFlow:
    """Tests for tag-based pre-filtering in the options flow.

    Covers T023-T027: single tag, empty tags, OR logic, no-match
    error, and default-selection intersection.
    """

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    # helper to extract option IDs from the select_listings schema
    @staticmethod
    def _option_ids(result: ConfigFlowResult) -> list[str]:
        """Extract listing IDs from select_listings schema.

        Args:
            result: Flow result at the select_listings step.

        Returns:
            List of listing ID values in the selector.
        """
        schema = result["data_schema"]
        assert schema is not None
        schema_dict = schema.schema
        selector_key = next(iter(schema_dict))
        selector = schema_dict[selector_key]
        return [opt["value"] for opt in selector.config["options"]]

    @staticmethod
    def _default_ids(result: ConfigFlowResult) -> list[str]:
        """Extract default listing IDs from schema.

        Args:
            result: Flow result at the select_listings step.

        Returns:
            Default listing ID values.
        """
        schema = result["data_schema"]
        assert schema is not None
        schema_dict = schema.schema
        selector_key = next(iter(schema_dict))
        return list(selector_key.default())

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_single_tag_shows_matching_listings(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T023: select_listings shows only tag-matched listings."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: ["mountain"]},
        )
        assert result["step_id"] == "select_listings"
        assert self._option_ids(result) == ["lst_002"]

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_empty_tags_show_all_listings(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T024: select_listings shows all when tag_filter empty."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: []},
        )
        assert result["step_id"] == "select_listings"
        assert set(self._option_ids(result)) == {
            "lst_001",
            "lst_002",
            "lst_003",
        }

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_multiple_tags_use_or_logic(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T025: Multiple tags use OR — any match shown."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_TAG_FILTER: ["premium", "mountain"],
            },
        )
        assert result["step_id"] == "select_listings"
        assert set(self._option_ids(result)) == {
            "lst_001",
            "lst_002",
        }

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_no_matching_tags_shows_error(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T026: no_listings_match_tags error on zero matches."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: ["nonexistent"]},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"
        assert result["errors"] == {
            "base": "no_listings_match_tags",
        }

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_default_intersects_previous_with_filtered(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T027: Default = prior selection ∩ tag-filtered IDs."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        hass.config_entries.async_update_entry(
            entry,
            options={
                CONF_SELECTED_LISTINGS: [
                    "lst_001",
                    "lst_002",
                ],
            },
        )
        _setup_api_client(hass, entry.entry_id)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        # "beachfront" matches lst_001 only
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_TAG_FILTER: ["beachfront"]},
        )
        assert result["step_id"] == "select_listings"
        # lst_002 was previously selected but is not in
        # tag-filtered set, so only lst_001 remains
        assert self._default_ids(result) == ["lst_001"]


class TestReservationsApiOption:
    """Tests for the reservations_api options field (T005)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @staticmethod
    def _option_schema(result: ConfigFlowResult) -> dict[Any, Any]:
        """Return the intervals form schema as a plain dict.

        Args:
            result: Flow result holding the intervals schema.

        Returns:
            The voluptuous schema mapping.
        """
        data_schema = result["data_schema"]
        assert data_schema is not None
        return dict(data_schema.schema)

    @classmethod
    def _option_marker(cls, result: ConfigFlowResult) -> Any:
        """Find the reservations_api schema key in a form result.

        Args:
            result: Flow result holding the intervals schema.

        Returns:
            The voluptuous marker for the option.
        """
        for marker in cls._option_schema(result):
            if marker == CONF_RESERVATIONS_API:
                return marker
        raise AssertionError("reservations_api missing from schema")

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_option_offers_exactly_two_values(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """The selector offers exactly v3 and legacy."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(hass, entry.entry_id)
        marker = self._option_marker(result)
        selector = self._option_schema(result)[marker]
        values = [option["value"] for option in selector.config["options"]]
        assert sorted(values) == [
            RESERVATIONS_API_LEGACY,
            RESERVATIONS_API_V3,
        ]

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_entry_without_value_defaults_without_migration(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """A pre-feature entry resolves to the default value."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        assert CONF_RESERVATIONS_API not in entry.options
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(hass, entry.entry_id)
        marker = self._option_marker(result)
        assert marker.default() == DEFAULT_RESERVATIONS_API
        assert entry.version == 1

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_unparsable_value_resolves_to_v3_with_warning(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A stored nonsense value is shown as v3 with a WARNING."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_RESERVATIONS_API: "nonsense",
            },
        )
        _setup_api_client(hass, entry.entry_id)

        with caplog.at_level(logging.WARNING):
            result = await _navigate_to_intervals(hass, entry.entry_id)

        marker = self._option_marker(result)
        assert marker.default() == RESERVATIONS_API_V3
        assert "nonsense" in caplog.text

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_stored_null_resolves_like_the_client(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """A stored null is invalid, not an absent option."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_RESERVATIONS_API: None},
        )
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(hass, entry.entry_id)
        marker = self._option_marker(result)
        assert marker.default() == RESERVATIONS_API_V3

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_selected_value_is_stored(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Submitting the option stores it on the config entry."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(hass, entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                **_DEFAULT_INTERVALS,
                CONF_RESERVATIONS_API: RESERVATIONS_API_V3,
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_RESERVATIONS_API] == RESERVATIONS_API_V3

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_omitted_value_stores_the_default(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Omitting the option stores the staged default."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(hass, entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input=dict(_DEFAULT_INTERVALS),
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_RESERVATIONS_API] == DEFAULT_RESERVATIONS_API


class TestReservationsApiDefaultIsV3:
    """The option defaults to v3 for every entry (T085, FR-037)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @staticmethod
    def _option_marker(result: ConfigFlowResult) -> Any:
        """Find the reservations_api marker in a form result.

        Args:
            result: Flow result holding the intervals schema.

        Returns:
            The voluptuous marker for the option.
        """
        data_schema = result["data_schema"]
        assert data_schema is not None
        for marker in dict(data_schema.schema):
            if marker == CONF_RESERVATIONS_API:
                return marker
        raise AssertionError("reservations_api missing from schema")

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_fresh_installation_is_offered_v3(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """A brand-new entry sees v3 selected already."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(hass, entry.entry_id)

        assert self._option_marker(result).default() == RESERVATIONS_API_V3

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_submitting_the_form_untouched_stores_v3(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """An unattended installation becomes compliant."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(hass, entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input=dict(_DEFAULT_INTERVALS),
        )

        assert entry.options[CONF_RESERVATIONS_API] == RESERVATIONS_API_V3

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_upgraded_entry_is_offered_v3_unmigrated(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """A pre-feature entry gets v3 without a schema bump."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        hass.config_entries.async_update_entry(
            entry,
            options={CONF_SCAN_INTERVAL: 15},
        )
        _setup_api_client(hass, entry.entry_id)

        result = await _navigate_to_intervals(hass, entry.entry_id)

        assert CONF_RESERVATIONS_API not in entry.options
        assert self._option_marker(result).default() == RESERVATIONS_API_V3
        assert entry.version == 1


class TestRollbackWithoutASuccessfulSetup:
    """The rollback stays reachable when setup failed (FR-039)."""

    @staticmethod
    def _entry(hass: HomeAssistant) -> MockConfigEntry:
        """Add a configured entry whose setup never completed.

        Args:
            hass: Home Assistant instance.

        Returns:
            The entry, with no runtime data registered.
        """
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=VALID_INPUT,
            options={
                CONF_TAG_FILTER: ["beach"],
                CONF_SELECTED_LISTINGS: ["listing-1"],
                CONF_SCAN_INTERVAL: 15,
                CONF_RESERVATION_SCAN_INTERVAL: 15,
                CONF_PAST_DAYS: 30,
                CONF_FUTURE_DAYS: 365,
                CONF_RESERVATIONS_API: RESERVATIONS_API_V3,
            },
        )
        entry.add_to_hass(hass)
        return entry

    async def test_the_selector_is_reachable(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A failed first v3 refresh still offers the option."""
        entry = self._entry(hass)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )

        assert result["step_id"] == "rollback"
        schema = result["data_schema"]
        assert schema is not None
        assert CONF_RESERVATIONS_API in dict(schema.schema)

    async def test_switching_back_preserves_the_options(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Rolling back changes nothing but the reservation path."""
        entry = self._entry(hass)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_RESERVATIONS_API: RESERVATIONS_API_LEGACY},
        )
        await hass.async_block_till_done()

        assert entry.options[CONF_RESERVATIONS_API] == RESERVATIONS_API_LEGACY
        assert entry.options[CONF_SELECTED_LISTINGS] == ["listing-1"]
        assert entry.options[CONF_TAG_FILTER] == ["beach"]
        assert entry.options[CONF_PAST_DAYS] == 30

    async def test_submitting_reloads_the_entry(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Recovery does not wait for a retry or a restart."""
        entry = self._entry(hass)

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            new_callable=AsyncMock,
        ) as mock_reload:
            result = await hass.config_entries.options.async_init(
                entry.entry_id,
            )
            await hass.config_entries.options.async_configure(
                result["flow_id"],
                user_input={
                    CONF_RESERVATIONS_API: RESERVATIONS_API_LEGACY,
                },
            )
            await hass.async_block_till_done()

        assert entry.options[CONF_RESERVATIONS_API] == RESERVATIONS_API_LEGACY
        mock_reload.assert_awaited_once_with(entry.entry_id)

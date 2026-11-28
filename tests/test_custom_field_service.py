# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for custom field service handler (T017-T037)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import traceback
from collections.abc import AsyncGenerator, Generator
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import ANY, AsyncMock, PropertyMock, patch

import httpx
import pytest
import respx
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from httpx import Response
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.actions import (
    ReservationIdentityMissError,
    guard_reservation_write,
)
from custom_components.guesty.api.const import (
    BASE_URL,
    LEGACY_FALLBACK_RECHECK_SECONDS,
    NOTE_SEPARATOR,
    RESERVATION_BY_ID_LEGACY_PATH,
    RESERVATION_CUSTOM_FIELDS_PATH,
    RESERVATION_NOTES_PATH,
    RESERVATION_STATUS_PATH,
    RESERVATIONS_V3_ENDPOINT,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    TOKEN_URL,
    WRITE_ROUTE_LEGACY,
    WRITE_ROUTE_V3,
)
from custom_components.guesty.api.custom_fields import (
    GuestyCustomFieldsClient,
)
from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyApiError,
    GuestyAuthError,
    GuestyConnectionError,
    GuestyCustomFieldError,
    GuestyFilterRejectedError,
)
from custom_components.guesty.api.models import (
    GuestyCustomFieldDefinition,
    GuestyCustomFieldResult,
    GuestyListing,
    GuestyReservation,
    RejectedCode,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RESERVATIONS_API,
    CONF_SELECTED_LISTINGS,
    DOMAIN,
    RESERVATIONS_API_V3,
    SERVICE_SET_CUSTOM_FIELD,
)
from custom_components.guesty.identity import (
    EVIDENCE_COMPROMISED,
    EVIDENCE_REVALIDATING,
    EVIDENCE_SATURATED,
    EVIDENCE_UNLOADED,
    EVIDENCE_UNSTORED,
    REFUSAL_EVIDENCE_REPAIR,
    REFUSAL_EVIDENCE_UNAVAILABLE,
    REFUSAL_NO_CODE,
    REFUSAL_REVALIDATING,
    ConflictOnlyEvidence,
)
from tests.conftest import (
    BY_ID_TARGET,
    FakeConflictEvidence,
    by_id_network,
    by_id_not_found,
    capture_by_id_retention,
    make_by_id_record,
    make_custom_field_definition_dict,
    make_fetch_result,
    make_identity_reservation,
    make_identity_reservations,
    make_legacy_reservation_by_id,
    make_token_response,
    make_v3_reservation,
    make_v3_search_page,
    make_v3_segment,
    sample_custom_field_definitions,
    stub_guesty_network,
)

# Reservation identifiers these tests set custom fields on; the
# identity guard refuses writes to anything the baseline lacks.
_WRITE_TARGETS = (
    "res-123",
    "res-456",
    "res-789",
    "res-c2",
    "res-e2e",
    "res-gone",
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


@contextlib.asynccontextmanager
async def _make_cf_test_client() -> AsyncGenerator[GuestyCustomFieldsClient]:
    """Create a custom fields client for testing.

    Yields:
        A GuestyCustomFieldsClient backed by test fakes.
    """
    from custom_components.guesty.api.auth import GuestyTokenManager
    from custom_components.guesty.api.client import GuestyApiClient
    from tests.conftest import FakeTokenStorage

    storage = FakeTokenStorage()
    async with httpx.AsyncClient() as http:
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
        yield GuestyCustomFieldsClient(
            api_client,
            account_id="acc-test-svc",
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
                match=r"listing/lst-123$",
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
                match="API error during",
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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

        # Clear hass.data to simulate empty domain
        hass.data[DOMAIN].clear()

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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
    async def test_multiple_entries_raises_error(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Multiple config entries raises HomeAssistantError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Simulate a second config entry in domain data
        hass.data[DOMAIN]["fake-second-entry"] = {}

        with pytest.raises(
            HomeAssistantError,
            match="ambiguous",
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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

        mock_set.assert_awaited_once_with(
            target_type="reservation",
            target_id="res-789",
            field_id="cf-door-code",
            value="5678",
            route=WRITE_ROUTE_V3,
        )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
            pytest.raises(
                HomeAssistantError,
                match=r"reservation/res-gone$",
            ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        mock_set.assert_awaited_once()

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        assert result["result"] == "success"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
            pytest.raises(
                HomeAssistantError,
                match=r"listing/lst-fail$",
            ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        mock_set.assert_awaited_once_with(
            target_type="listing",
            target_id="lst-u",
            field_id="cf-region",
            value=unicode_val,
        )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
            pytest.raises(
                HomeAssistantError,
                match=r"listing/lst-stale$",
            ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        async with _make_cf_test_client() as cf_client:
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
        """429 retry respects Retry-After header value."""
        async with _make_cf_test_client() as cf_client:
            respx.post(TOKEN_URL).mock(
                return_value=Response(200, json=make_token_response()),
            )

            put_route = respx.put(
                f"{BASE_URL}/listings/lst-ra/custom-fields",
            )
            put_route.side_effect = [
                Response(
                    429,
                    headers={"Retry-After": "0.25"},
                    json={"error": "rate limited"},
                ),
                Response(200, json=[{"fieldId": "cf-a", "value": "v"}]),
            ]

            with patch(
                "asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep_mock:
                result = await cf_client.set_field(
                    target_type="listing",
                    target_id="lst-ra",
                    field_id="cf-a",
                    value="v",
                )

            assert result.success is True
            assert put_route.call_count == 2
            sleep_mock.assert_awaited_once_with(0.25)

    # ── T032: Transient Failure Retry Tests ──────────────────────────────


class TestTransientFailureRetry:
    """Tests for transient failure retry (T032)."""

    @respx.mock
    async def test_network_error_then_success(self) -> None:
        """Transient network error then success on retry."""
        async with _make_cf_test_client() as cf_client:
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
        async with _make_cf_test_client() as cf_client:
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
        async with _make_cf_test_client() as cf_client:
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        async with _make_cf_test_client() as cf_client:
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
        async with _make_cf_test_client() as cf_client:
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
        async with _make_cf_test_client() as cf_client:
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
                r
                for r in caplog.records
                if r.name.startswith("custom_components.guesty")
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
        async with _make_cf_test_client() as cf_client:
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
                r
                for r in caplog.records
                if r.name.startswith("custom_components.guesty")
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
        async with _make_cf_test_client() as cf_client:
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
                r
                for r in caplog.records
                if r.name.startswith("custom_components.guesty")
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
        async with _make_cf_test_client() as cf_client:
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
                r
                for r in caplog.records
                if r.name.startswith("custom_components.guesty")
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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

        mock_set.assert_awaited_once_with(
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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

        mock_set.assert_awaited_once()
        assert result is not None
        assert result["target_type"] == "reservation"


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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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
        return_value=make_fetch_result(
            make_identity_reservations(*_WRITE_TARGETS),
        ),
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

    @respx.mock
    async def test_sc009_all_testable_without_live_guesty(
        self,
    ) -> None:
        """SC-009: All scenarios testable without live Guesty."""
        # Unmocked outbound HTTP fails during tests, proving no
        # live Guesty connection is needed.
        from respx.models import AllMockedAssertionError

        with respx.mock(assert_all_mocked=True):
            async with httpx.AsyncClient() as client:
                with pytest.raises(AllMockedAssertionError):
                    await client.get(
                        f"{BASE_URL}/__test_unmocked__",
                    )


# ── T037: Quickstart Validation ──────────────────────────────────────


class TestQuickstartValidation:
    """Quickstart code pattern validation tests (T037)."""

    def test_custom_fields_client_di_pattern(self) -> None:
        """Client follows documented DI pattern."""
        from custom_components.guesty.api.client import GuestyApiClient

        # Verify the constructor signature matches quickstart
        mock_api = AsyncMock(spec=GuestyApiClient)
        client = GuestyCustomFieldsClient(
            mock_api,
            account_id="acc-test",
        )
        assert client._api_client is mock_api
        assert client._account_id == "acc-test"

    def test_frozen_dataclass_pattern(self) -> None:
        """Models use frozen dataclass with from_api_dict factory."""
        data = make_custom_field_definition_dict()
        defn = GuestyCustomFieldDefinition.from_api_dict(data)
        assert defn is not None
        assert defn.field_id == data["fieldId"]
        assert defn.name == data["key"]

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
        client = GuestyCustomFieldsClient(
            mock_api,
            account_id="acc-test",
        )
        # Should not raise for valid
        client.validate_value("hello", "text")
        # Should raise for mismatch
        with pytest.raises(GuestyCustomFieldError):
            client.validate_value(42, "text")

    def test_service_name_constant(self) -> None:
        """Service name constant matches documentation."""
        assert SERVICE_SET_CUSTOM_FIELD == "set_custom_field"


# ── Identity write predicate (T065) ─────────────────────────────────


class TestSetCustomFieldIdentityGuard:
    """guesty.set_custom_field fails closed on an unsure identity.

    Setting a custom field is how door access codes reach Guesty, so
    an unconfirmed identity must refuse the write rather than risk
    publishing a working code onto a stranger's booking.
    """

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests.

        Yields:
            None, while the definitions client is patched.
        """
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ):
            yield

    @staticmethod
    async def _setup(
        hass: HomeAssistant,
        reservations: list[GuestyReservation],
        *,
        upgraded: bool = False,
        **overrides: object,
    ) -> MockConfigEntry:
        """Load an entry whose first refresh seeds the baseline.

        Args:
            hass: Home Assistant instance.
            reservations: Reservations the first refresh returns.
            upgraded: Present the entry as an upgrade from a
                legacy-only release, so setup runs the one-time
                bootstrap sweep. The sweep is stubbed to return
                nothing, which leaves continuity unconfirmed.
            **overrides: Config entry fields to override.

        Returns:
            The loaded config entry.
        """
        entry = _make_entry(**overrides)
        entry.add_to_hass(hass)
        sweep = ExitStack()
        if upgraded:
            er.async_get(hass).async_get_or_create(
                "sensor",
                DOMAIN,
                f"{entry.unique_id}_listing-001_reservation_status",
                config_entry=entry,
            )
            source = AsyncMock()
            source.fetch = AsyncMock(return_value=make_fetch_result([]))
            sweep.enter_context(
                patch(
                    "custom_components.guesty.identity.LegacyReservationSource",
                    return_value=source,
                ),
            )
        with (
            sweep,
            patch(
                "custom_components.guesty.GuestyApiClient.test_connection",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "custom_components.guesty.GuestyApiClient.get_listings",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result(reservations),
            ),
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        return entry

    @staticmethod
    async def _expect_refusal(
        hass: HomeAssistant,
        target_id: str,
        match: str,
    ) -> None:
        """Assert a reservation custom field write is refused.

        Args:
            hass: Home Assistant instance.
            target_id: Reservation the write would target.
            match: Expected fragment of the refusal message.
        """
        with (
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
            ) as set_field,
            pytest.raises(HomeAssistantError, match=match),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": target_id,
                    "field_id": "cf-door-code",
                    "value": "1234",
                },
                blocking=True,
                return_response=True,
            )
        set_field.assert_not_awaited()

    async def test_missing_reservation_refreshes_before_custom_field_write(
        self, hass: HomeAssistant
    ) -> None:
        """A missing field target is refreshed before one valid write."""
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        target = make_identity_reservation("res-new", "CONF-res-new")
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [*make_identity_reservations(*_WRITE_TARGETS), target]
                ),
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                return_value=GuestyCustomFieldResult(
                    success=True,
                    target_type="reservation",
                    target_id="res-new",
                    field_id="cf-door-code",
                ),
            ) as write,
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-new",
                    "field_id": "cf-door-code",
                    "value": "0042",
                },
                blocking=True,
                return_response=True,
            )
        assert result is not None and result["result"] == "success"
        fetch.assert_awaited_once()
        write.assert_awaited_once()
        assert write.await_args is not None
        assert write.await_args.kwargs["value"] == "0042"

    @pytest.mark.parametrize("target_type", ["reservation", "listing"])
    async def test_existing_custom_field_write_does_not_refresh_reservations(
        self, hass: HomeAssistant, target_type: str
    ) -> None:
        """A cache-hit reservation or listing write keeps the existing path."""
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        target_id = "res-123" if target_type == "reservation" else "listing-001"
        field_id = "cf-door-code" if target_type == "reservation" else "cf-region"
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                return_value=GuestyCustomFieldResult(
                    success=True,
                    target_type=target_type,
                    target_id=target_id,
                    field_id=field_id,
                ),
            ) as write,
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": target_type,
                    "target_id": target_id,
                    "field_id": field_id,
                    "value": "0042",
                },
                blocking=True,
                return_response=True,
            )
        assert result is not None and result["result"] == "success"
        fetch.assert_not_awaited()
        write.assert_awaited_once()
        assert write.await_args is not None
        assert "single_attempt" not in write.await_args.kwargs

    @pytest.mark.parametrize(
        "guard",
        ["missing", "unusable", "unstored", "unconfirmed", "ambiguous", "route"],
    )
    async def test_independent_refusal_does_not_refresh(
        self, hass: HomeAssistant, guard: str
    ) -> None:
        """Each independent field guard refuses without refresh or write."""
        reservations = (
            [] if guard == "missing" else make_identity_reservations(*_WRITE_TARGETS)
        )
        entry = await self._setup(hass, reservations)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        baseline = coordinator.identity.baseline
        if guard != "missing":
            assert baseline is not None
            if guard == "unusable":
                baseline.codes.clear()
            elif guard == "unstored":
                coordinator.identity._unstored = True
            elif guard == "unconfirmed":
                baseline.continuity = "unconfirmed"
            elif guard == "ambiguous":
                coordinator.identity.mark_ambiguous()
            else:
                coordinator.fallback_sourced_ids = frozenset({"res-456"})
        target = "res-456" if guard == "route" else "res-new"
        category = {
            "missing": "has not established a reservation identity baseline",
            "unusable": "has not established a reservation identity baseline",
            "unstored": "could not be written to storage",
            "unconfirmed": "identity continuity is unconfirmed",
            "ambiguous": "last Guesty reservation refresh was aborted",
            "route": "has not been validated against either resource",
        }[guard]
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient, "set_field", new_callable=AsyncMock
            ) as write,
            pytest.raises(HomeAssistantError, match=category),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": target,
                    "field_id": "cf-door-code",
                    "value": "0042",
                },
                blocking=True,
                return_response=True,
            )
        fetch.assert_not_awaited()
        write.assert_not_awaited()

    @pytest.mark.parametrize("route", ["retained-only", "retired"])
    async def test_missing_identity_with_known_unroutable_target(
        self, hass: HomeAssistant, route: str
    ) -> None:
        """A missing-code field target cannot hide a known routing refusal."""
        entry = await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.fallback_sourced_ids = frozenset({"res-new"})
        if route == "retired":
            coordinator.fallback_served_ids = frozenset({"res-new"})
            coordinator.api_client.disable_legacy_write_routing()
        category = "has not been validated" if route == "retained-only" else "retire"
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient, "set_field", new_callable=AsyncMock
            ) as write,
            pytest.raises(HomeAssistantError, match=category),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-new",
                    "field_id": "cf-door-code",
                    "value": "0042",
                },
                blocking=True,
                return_response=True,
            )
        fetch.assert_not_awaited()
        write.assert_not_awaited()

    @pytest.mark.parametrize(
        ("field_id", "value", "message"),
        [
            ("missing-field", "0042", "not found"),
            ("cf-door-code", False, "Expected str"),
        ],
    )
    async def test_invalid_field_does_not_refresh(
        self,
        hass: HomeAssistant,
        field_id: str,
        value: str | bool,
        message: str,
    ) -> None:
        """Field and value validation precede identity recovery."""
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient, "set_field", new_callable=AsyncMock
            ) as write,
            pytest.raises(HomeAssistantError, match=message),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-new",
                    "field_id": field_id,
                    "value": value,
                },
                blocking=True,
                return_response=True,
            )
        fetch.assert_not_awaited()
        write.assert_not_awaited()

    async def test_blank_target_does_not_refresh_or_write(
        self, hass: HomeAssistant
    ) -> None:
        """A blank reservation target cannot trigger refresh or write."""
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient, "set_field", new_callable=AsyncMock
            ) as write,
            pytest.raises(HomeAssistantError),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "  ",
                    "field_id": "cf-door-code",
                    "value": "0042",
                },
                blocking=True,
                return_response=True,
            )
        fetch.assert_not_awaited()
        write.assert_not_awaited()

    async def test_blank_target_reports_validation_category(
        self, hass: HomeAssistant
    ) -> None:
        """A blank target reports invalid input rather than missing identity."""
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient, "set_field", new_callable=AsyncMock
            ) as write,
        ):
            try:
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_SET_CUSTOM_FIELD,
                    {
                        "target_type": "reservation",
                        "target_id": "  ",
                        "field_id": "cf-door-code",
                        "value": "0042",
                    },
                    blocking=True,
                    return_response=True,
                )
            except HomeAssistantError as exc:
                message = str(exc)
            else:
                pytest.fail("A blank target must be refused")
            fetch.assert_not_awaited()
            write.assert_not_awaited()
            assert "non-empty" in message

    async def test_published_codeless_field_refreshes_before_write(
        self, hass: HomeAssistant
    ) -> None:
        """A code-less field target may be confirmed by one refresh."""
        base = make_identity_reservations(*_WRITE_TARGETS)
        uncoded = make_identity_reservation("res-new")
        entry = await self._setup(hass, [*base, uncoded])
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.data = {"listing-001": [uncoded]}
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [*base, make_identity_reservation("res-new", "CONF-res-new")]
                ),
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                return_value=GuestyCustomFieldResult(
                    success=True,
                    target_type="reservation",
                    target_id="res-new",
                    field_id="cf-door-code",
                ),
            ) as write,
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-new",
                    "field_id": "cf-door-code",
                    "value": "0042",
                },
                blocking=True,
                return_response=True,
            )
        assert result is not None and result["result"] == "success"
        fetch.assert_awaited_once()
        write.assert_awaited_once()

    async def test_no_baseline_refuses(self, hass: HomeAssistant) -> None:
        """Without a baseline there is nothing to verify against."""
        await self._setup(hass, [])
        await self._expect_refusal(
            hass,
            "res-456",
            "has not established a reservation identity baseline",
        )

    async def test_unconfirmed_continuity_refuses(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A v3 baseline without cross-path evidence refuses."""
        await self._setup(
            hass,
            make_identity_reservations(*_WRITE_TARGETS),
            upgraded=True,
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
        )
        await self._expect_refusal(
            hass,
            "res-456",
            "continuity is unconfirmed",
        )

    async def test_ambiguous_refresh_refuses(
        self,
        hass: HomeAssistant,
    ) -> None:
        """An aborted refresh blocks writes until it is resolved."""
        entry = await self._setup(
            hass,
            make_identity_reservations(*_WRITE_TARGETS),
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        with patch(
            "custom_components.guesty.GuestyApiClient.get_reservations",
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                [make_identity_reservation("res-999", "CONF-res-456")],
            ),
        ):
            await coordinator.async_refresh()

        assert not coordinator.last_update_success
        await self._expect_refusal(
            hass,
            "res-456",
            "aborted because a confirmation code",
        )

    async def test_target_without_confirmation_code_refuses(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-033: a codeless reservation is not a write target.

        Feature 014 now gives the codeless target one by-ID read
        (T052 audit); it finds nothing, so the write is still refused.
        """
        await self._setup(
            hass,
            [
                *make_identity_reservations(*_WRITE_TARGETS),
                make_identity_reservation("res-nocode"),
            ],
        )
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [
                        *make_identity_reservations(*_WRITE_TARGETS),
                        make_identity_reservation("res-nocode"),
                    ]
                ),
            ) as fetch,
            by_id_not_found() as read,
        ):
            await self._expect_refusal(
                hass,
                "res-nocode",
                "not found by ID",
            )
        fetch.assert_awaited_once()
        assert read.call_count == 1

    async def test_listing_writes_are_unaffected(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The guard covers reservations only, never listings."""
        await self._setup(hass, [])

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
        ) as set_field:
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-region",
                    "value": "emea",
                },
                blocking=True,
                return_response=True,
            )

        assert result is not None
        assert result["result"] == "success"
        set_field.assert_awaited_once()

    async def test_fallback_sourced_target_is_refused(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A target neither resource validates sends nothing.

        Setting a custom field is how a door access code reaches
        Guesty, so a reservation republished from retained values
        alone must not be written to (FR-058).
        """
        entry = await self._setup(
            hass,
            make_identity_reservations(*_WRITE_TARGETS),
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.fallback_sourced_ids = frozenset({"res-456"})

        await self._expect_refusal(
            hass,
            "res-456",
            "no write is sent",
        )

    async def test_fallback_served_target_is_allowed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A legacy-validated target still reaches the write."""
        entry = await self._setup(
            hass,
            make_identity_reservations(*_WRITE_TARGETS),
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.fallback_served_ids = frozenset({"res-456"})
        coordinator.fallback_sourced_ids = frozenset({"res-456"})

        with patch.object(
            GuestyCustomFieldsClient,
            "set_field",
            new_callable=AsyncMock,
        ) as set_field:
            await hass.services.async_call(
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

        set_field.assert_awaited_once()

    async def test_retired_retained_miss_explains_retirement(
        self, hass: HomeAssistant
    ) -> None:
        """A missing retained identity reports retirement without a refresh."""
        entry = await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        coordinator.fallback_sourced_ids = frozenset({"res-new"})
        api_client = entry_data["api_client"]
        with (
            patch.object(
                type(api_client),
                "legacy_write_routing_available",
                property(lambda _: False),
            ),
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
            ) as fetch,
        ):
            await self._expect_refusal(hass, "res-new", "2027-03-01")
        fetch.assert_not_awaited()

    async def test_recovered_write_error_does_not_log_value(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A response echo of a field value is not logged or returned."""
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        target = make_identity_reservation("res-new", "CONF-res-new")
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [*make_identity_reservations(*_WRITE_TARGETS), target]
                ),
            ),
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                side_effect=GuestyCustomFieldError(
                    "server echoed SECRET-PIN",
                    target_type="reservation",
                    target_id="res-new",
                    field_id="cf-door-code",
                ),
            ),
            pytest.raises(HomeAssistantError) as error,
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-new",
                    "field_id": "cf-door-code",
                    "value": "SECRET-PIN",
                },
                blocking=True,
                return_response=True,
            )
        assert "SECRET-PIN" not in str(error.value)
        assert "SECRET-PIN" not in caplog.text

    @pytest.mark.parametrize("confirm", [False, True])
    async def test_published_codeless_custom_field_target(
        self, hass: HomeAssistant, confirm: bool
    ) -> None:
        """A published code-less target is writable only if refresh confirms it.

        Unconfirmed, feature 014 reads it by ID once (T052 audit); the
        read finds nothing, so the field is still never written.
        """
        base = make_identity_reservations(*_WRITE_TARGETS)
        uncoded = make_identity_reservation("res-new")
        entry = await self._setup(hass, [*base, uncoded])
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.data = {"listing-001": [uncoded]}
        refreshed = make_identity_reservation(
            "res-new", "CONF-res-new" if confirm else None
        )
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result([*base, refreshed]),
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                return_value=GuestyCustomFieldResult(
                    success=True,
                    target_type="reservation",
                    target_id="res-new",
                    field_id="cf-door-code",
                ),
            ) as write,
        ):
            payload = {
                "target_type": "reservation",
                "target_id": "res-new",
                "field_id": "cf-door-code",
                "value": "0042",
            }
            if confirm:
                result = await hass.services.async_call(
                    DOMAIN,
                    SERVICE_SET_CUSTOM_FIELD,
                    payload,
                    blocking=True,
                    return_response=True,
                )
                assert result is not None and result["result"] == "success"
                write.assert_awaited_once()
            else:
                with (
                    by_id_not_found() as read,
                    pytest.raises(HomeAssistantError, match="not found by ID"),
                ):
                    await hass.services.async_call(
                        DOMAIN,
                        SERVICE_SET_CUSTOM_FIELD,
                        payload,
                        blocking=True,
                        return_response=True,
                    )
                write.assert_not_awaited()
                assert read.call_count == 1
        fetch.assert_awaited_once()

    async def test_refreshed_custom_field_fallback_uses_legacy_route(
        self, hass: HomeAssistant
    ) -> None:
        """A newly served fallback identity keeps its verified route."""
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        target = make_identity_reservation("res-new", "CONF-res-new")
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [*make_identity_reservations(*_WRITE_TARGETS), target],
                    fallback_served=("res-new",),
                    fallback_sourced=("res-new",),
                ),
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                return_value=GuestyCustomFieldResult(
                    success=True,
                    target_type="reservation",
                    target_id="res-new",
                    field_id="cf-door-code",
                ),
            ) as write,
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-new",
                    "field_id": "cf-door-code",
                    "value": "0042",
                },
                blocking=True,
                return_response=True,
            )
        fetch.assert_awaited_once()
        write.assert_awaited_once_with(
            target_type="reservation",
            target_id="res-new",
            field_id="cf-door-code",
            value="0042",
            route="legacy",
            single_attempt=True,
            before_write=ANY,
        )

    @pytest.mark.parametrize("legacy", [False, True])
    @pytest.mark.parametrize("operation", ["note", "status", "field"])
    @respx.mock
    async def test_recovered_service_does_not_repeat_actual_put(
        self, hass: HomeAssistant, legacy: bool, operation: str
    ) -> None:
        """A transient response produces one real PUT on the selected route."""
        entry = await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        api_client = hass.data[DOMAIN][entry.entry_id]["api_client"]
        refresh_result = make_fetch_result(
            [
                *make_identity_reservations(*_WRITE_TARGETS),
                make_identity_reservation("res-new", "CONF-res-new"),
            ],
            fallback_served=("res-new",) if legacy else (),
            fallback_sourced=("res-new",) if legacy else (),
        )
        if legacy:
            path = RESERVATION_BY_ID_LEGACY_PATH.format(reservation_id="res-new")
        else:
            template = {
                "note": RESERVATION_NOTES_PATH,
                "status": RESERVATION_STATUS_PATH,
                "field": RESERVATION_CUSTOM_FIELDS_PATH,
            }[operation]
            path = template.format(reservation_id="res-new")
        put = respx.put(f"{BASE_URL}{path}").mock(
            return_value=Response(500, json={"error": "unavailable"})
        )
        if operation == "note":
            read_url = (
                f"{BASE_URL}{path}"
                if legacy
                else f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}"
            )
            document = (
                {"_id": "res-new", "note": ""}
                if legacy
                else {"results": [{"reservationId": "res-new", "notes": {}}]}
            )
            respx.get(read_url).mock(return_value=Response(200, json=document))
            service = "add_reservation_note"
            payload: dict[str, object] = {
                "reservation_id": "res-new",
                "note_text": "hello",
            }
        elif operation == "status":
            service = "set_reservation_status"
            payload = {"reservation_id": "res-new", "status": "checked_in"}
        else:
            service = SERVICE_SET_CUSTOM_FIELD
            payload = {
                "target_type": "reservation",
                "target_id": "res-new",
                "field_id": "cf-door-code",
                "value": "0042",
            }
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=refresh_result,
            ) as fetch,
            patch.object(api_client._token_manager, "get_token", return_value="token"),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(HomeAssistantError),
        ):
            await hass.services.async_call(
                DOMAIN, service, payload, blocking=True, return_response=True
            )
        fetch.assert_awaited_once()
        assert len(put.calls) == 1

    @respx.mock
    @pytest.mark.parametrize("legacy", [False, True])
    @pytest.mark.parametrize("operation", ["note", "status", "field"])
    async def test_recovered_service_success_over_http(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        legacy: bool,
        operation: str,
    ) -> None:
        """A real refresh and successful HTTP PUT retain the selected route and data."""
        freezer.move_to("2026-11-23 12:00:00+00:00")
        entry = await self._setup(
            hass,
            make_identity_reservations(*_WRITE_TARGETS),
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        listing = GuestyListing.from_api_dict(
            {
                "_id": "listing-001",
                "title": "Selected",
                "timezone": "UTC",
                "defaultCheckInTime": "15:00",
                "defaultCheckOutTime": "11:00",
            }
        )
        assert listing is not None
        coordinator.listings_coordinator.data = {listing.id: listing}
        coordinator.listings_coordinator.timing_listings = {listing.id: listing}
        begin = datetime.now(UTC) + timedelta(days=1)
        end = begin + timedelta(days=2)
        segment = make_v3_segment(
            checkIn=begin.isoformat(),
            checkOut=end.isoformat(),
            checkInDateLocalized=begin.date().isoformat(),
            checkOutDateLocalized=end.date().isoformat(),
        )
        originals = [
            make_v3_reservation(
                reservation_id,
                segments=[segment],
                guest_id=None,
                confirmationCode=f"CONF-{reservation_id}",
            )
            for reservation_id in sorted(_WRITE_TARGETS)
        ]
        target = make_v3_reservation(
            "res-new",
            segments=[segment],
            guest_id=None,
            confirmationCode=None if legacy else "CONF-res-new",
        )
        search = respx.get(f"{BASE_URL}{RESERVATIONS_V3_SEARCH_ENDPOINT}")
        if legacy:
            search.mock(
                side_effect=[
                    Response(200, json=make_v3_search_page([*originals, target])),
                    Response(200, json=make_v3_search_page(originals)),
                ]
            )
        else:
            search.mock(
                return_value=Response(
                    200, json=make_v3_search_page([*originals, target])
                )
            )
        token = respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response())
        )
        legacy_url = f"{BASE_URL}" + RESERVATION_BY_ID_LEGACY_PATH.format(
            reservation_id="res-new"
        )
        if legacy:
            fallback = make_legacy_reservation_by_id(
                "res-new",
                checkIn=begin.isoformat(),
                checkOut=end.isoformat(),
                checkInDateLocalized=begin.date().isoformat(),
                checkOutDateLocalized=end.date().isoformat(),
                confirmationCode="CONF-res-new",
                note="prior",
            )
            legacy_get = respx.get(legacy_url).mock(
                return_value=Response(200, json=fallback)
            )
        elif operation == "note":
            note_get = respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
                return_value=Response(
                    200,
                    json={
                        "results": [
                            {
                                "reservationId": "res-new",
                                "notes": {"other": "prior", "guest": "keep"},
                            }
                        ]
                    },
                )
            )
        if legacy:
            await coordinator.async_refresh()
            assert "res-new" in coordinator._fallback_candidates()
        generation = coordinator.write_miss_generation
        template = {
            "note": RESERVATION_NOTES_PATH,
            "status": RESERVATION_STATUS_PATH,
            "field": RESERVATION_CUSTOM_FIELDS_PATH,
        }[operation]
        write_url = (
            legacy_url
            if legacy
            else f"{BASE_URL}{template.format(reservation_id='res-new')}"
        )
        put = respx.put(write_url).mock(return_value=Response(200, json={}))
        service = {
            "note": "add_reservation_note",
            "status": "set_reservation_status",
            "field": SERVICE_SET_CUSTOM_FIELD,
        }[operation]
        payload: dict[str, str] = {
            "note": {"reservation_id": "res-new", "note_text": "hello"},
            "status": {"reservation_id": "res-new", "status": "checked_in"},
            "field": {
                "target_type": "reservation",
                "target_id": "res-new",
                "field_id": "cf-door-code",
                "value": "0042",
            },
        }[operation]
        result = await hass.services.async_call(
            DOMAIN, service, payload, blocking=True, return_response=True
        )
        assert coordinator.write_miss_generation == generation + 1
        assert len(search.calls) == (2 if legacy else 1)
        assert len(put.calls) == 1
        assert token.called
        assert result == (
            {
                "target_type": "reservation",
                "target_id": "res-new",
                "field_id": "cf-door-code",
                "result": "success",
            }
            if operation == "field"
            else {"success": True, "target_id": "res-new"}
        )
        body = json.loads(put.calls.last.request.content)
        if operation == "note":
            assert body == (
                {"note": f"prior{NOTE_SEPARATOR}hello"}
                if legacy
                else {"other": f"prior{NOTE_SEPARATOR}hello", "guest": "keep"}
            )
            assert len(legacy_get.calls if legacy else note_get.calls) == (
                2 if legacy else 1
            )
        elif operation == "status":
            assert body == {"status": "checked_in"}
        else:
            assert body == {
                "customFields": [{"fieldId": "cf-door-code", "value": "0042"}]
            }
        if legacy and operation != "note":
            assert len(legacy_get.calls) == 1

    @respx.mock
    @pytest.mark.parametrize("operation", ["status", "field"])
    @pytest.mark.parametrize("failure", ["403", "429", "500", "exhausted"])
    async def test_recovered_service_real_token_failure_is_unsent(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        operation: str,
        failure: str,
    ) -> None:
        """Real token errors before a recovered PUT report no write was sent."""
        entry = await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        api_client = hass.data[DOMAIN][entry.entry_id]["api_client"]
        manager = api_client._token_manager
        manager.invalidate()
        if failure == "exhausted":
            manager._request_count = 5
            manager._window_start = datetime.now(UTC)
        secret = "private-token-response-sentinel"
        token = respx.post(TOKEN_URL).mock(
            return_value=Response(
                429 if failure == "exhausted" else int(failure), text=secret
            )
        )
        path = (
            RESERVATION_STATUS_PATH
            if operation == "status"
            else RESERVATION_CUSTOM_FIELDS_PATH
        ).format(reservation_id="res-new")
        put = respx.put(f"{BASE_URL}{path}").mock(return_value=Response(200))
        service = (
            "set_reservation_status"
            if operation == "status"
            else SERVICE_SET_CUSTOM_FIELD
        )
        payload: dict[str, object] = (
            {"reservation_id": "res-new", "status": "checked_in"}
            if operation == "status"
            else {
                "target_type": "reservation",
                "target_id": "res-new",
                "field_id": "cf-door-code",
                "value": "0042",
            }
        )
        with (
            caplog.at_level(logging.DEBUG),
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    make_identity_reservations(*_WRITE_TARGETS, "res-new")
                ),
            ) as fetch,
            pytest.raises(HomeAssistantError) as raised,
        ):
            await hass.services.async_call(
                DOMAIN, service, payload, blocking=True, return_response=True
            )
        fetch.assert_awaited_once()
        assert not put.called
        assert len(token.calls) == (0 if failure == "exhausted" else 1)
        assert "token" in str(raised.value).lower()
        assert "write was not sent" in str(raised.value).lower()
        assert "may have reached" not in str(raised.value).lower()
        assert secret not in str(raised.value)
        assert secret not in "".join(traceback.format_exception(raised.value))
        assert secret not in caplog.text

    @respx.mock
    @pytest.mark.parametrize("failure", ["403", "429", "500", "exhausted"])
    async def test_recovered_note_write_token_failure_is_unsent(
        self, hass: HomeAssistant, failure: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A token failure after a successful note GET still precedes every PUT."""
        entry = await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        manager = hass.data[DOMAIN][entry.entry_id]["api_client"]._token_manager
        manager.invalidate()
        secret = "private-note-token-sentinel"
        token = respx.post(TOKEN_URL).mock(
            side_effect=[
                Response(200, json=make_token_response()),
                Response(429 if failure == "exhausted" else int(failure), text=secret),
            ]
        )

        def finish_read(request: httpx.Request) -> Response:
            """Invalidate the first token only after the actual prerequisite GET."""
            manager.invalidate()
            if failure == "exhausted":
                manager._request_count = 5
                manager._window_start = datetime.now(UTC)
            return Response(
                200, json={"results": [{"reservationId": "res-new", "notes": {}}]}
            )

        read = respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
            side_effect=finish_read
        )
        put = respx.put(
            f"{BASE_URL}" + RESERVATION_NOTES_PATH.format(reservation_id="res-new")
        ).mock(return_value=Response(200))
        with (
            caplog.at_level(logging.DEBUG),
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    make_identity_reservations(*_WRITE_TARGETS, "res-new")
                ),
            ) as fetch,
            pytest.raises(HomeAssistantError) as raised,
        ):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-new", "note_text": "hello"},
                blocking=True,
                return_response=True,
            )
        fetch.assert_awaited_once()
        assert len(read.calls) == 1
        assert len(token.calls) == (1 if failure == "exhausted" else 2)
        assert not put.called
        assert "token" in str(raised.value).lower()
        assert "write was not sent" in str(raised.value).lower()
        assert secret not in str(raised.value)
        assert secret not in "".join(traceback.format_exception(raised.value))
        assert secret not in caplog.text

    @pytest.mark.parametrize("legacy", [False, True])
    @pytest.mark.parametrize("operation", ["note", "status", "field"])
    @pytest.mark.parametrize(
        "failure",
        [
            "401",
            "403",
            "token",
            "token_connection",
            "read_error",
            "write_error",
            "protocol",
            "local_protocol",
            "proxy",
            "connect",
            "connect_timeout",
            "pool_timeout",
        ],
    )
    @respx.mock
    async def test_recovered_service_reports_safe_write_failure(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        legacy: bool,
        operation: str,
        failure: str,
    ) -> None:
        """Recovered transport failures report their safe phase and category."""
        entry = await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        api_client = hass.data[DOMAIN][entry.entry_id]["api_client"]
        refresh_result = make_fetch_result(
            [
                *make_identity_reservations(*_WRITE_TARGETS),
                make_identity_reservation("res-new", "CONF-res-new"),
            ],
            fallback_served=("res-new",) if legacy else (),
            fallback_sourced=("res-new",) if legacy else (),
        )
        path = (
            RESERVATION_BY_ID_LEGACY_PATH.format(reservation_id="res-new")
            if legacy
            else {
                "note": RESERVATION_NOTES_PATH,
                "status": RESERVATION_STATUS_PATH,
                "field": RESERVATION_CUSTOM_FIELDS_PATH,
            }[operation].format(reservation_id="res-new")
        )
        secret = "sample-secret-response-0042"
        put = respx.put(f"{BASE_URL}{path}")
        if failure in ("401", "403"):
            put.mock(return_value=Response(int(failure), text=secret))
        elif failure in ("token", "token_connection"):
            put.mock(return_value=Response(200, json={}))
        else:
            error_type = {
                "read_error": httpx.ReadError,
                "write_error": httpx.WriteError,
                "protocol": httpx.RemoteProtocolError,
                "local_protocol": httpx.LocalProtocolError,
                "proxy": httpx.ProxyError,
                "connect": httpx.ConnectError,
                "connect_timeout": httpx.ConnectTimeout,
                "pool_timeout": httpx.PoolTimeout,
            }[failure]
            put.mock(side_effect=error_type(secret))

        if operation == "note":
            read_url = (
                f"{BASE_URL}{path}"
                if legacy
                else f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}"
            )
            document = (
                {"_id": "res-new", "note": ""}
                if legacy
                else {"results": [{"reservationId": "res-new", "notes": {}}]}
            )
            respx.get(read_url).mock(return_value=Response(200, json=document))
            service = "add_reservation_note"
            payload: dict[str, object] = {
                "reservation_id": "res-new",
                "note_text": "hello",
            }
        elif operation == "status":
            service = "set_reservation_status"
            payload = {"reservation_id": "res-new", "status": "checked_in"}
        else:
            service = SERVICE_SET_CUSTOM_FIELD
            payload = {
                "target_type": "reservation",
                "target_id": "res-new",
                "field_id": "cf-door-code",
                "value": "0042",
            }
        token_calls = 0

        async def token() -> str:
            """Fail token acquisition only at the write, after any note GET."""
            nonlocal token_calls
            token_calls += 1
            if failure in ("token", "token_connection") and (
                operation != "note" or token_calls > 1
            ):
                if failure == "token":
                    raise GuestyAuthError(secret)
                raise GuestyConnectionError(secret)
            return "token"

        with (
            caplog.at_level(logging.DEBUG),
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=refresh_result,
            ) as fetch,
            patch.object(api_client._token_manager, "get_token", side_effect=token),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(HomeAssistantError) as raised,
        ):
            await hass.services.async_call(
                DOMAIN, service, payload, blocking=True, return_response=True
            )
        fetch.assert_awaited_once()
        assert len(put.calls) == (0 if failure.startswith("token") else 1)
        message = str(raised.value).lower()
        if failure == "token":
            assert "authentication" in message
            assert "write was not sent" in message
        elif failure == "token_connection":
            assert "token" in message
            assert "write was not sent" in message
        elif failure in ("401", "403"):
            assert ("authentication" if failure == "401" else "permission") in message
            assert "not retried" in message
        elif failure in ("proxy", "connect", "connect_timeout", "pool_timeout"):
            assert "write was not sent" in message
            assert "not retried" in message
        else:
            assert "may have reached guesty" in message
            assert "not retried" in message
        assert secret not in str(raised.value)
        assert secret not in caplog.text
        assert secret not in "".join(traceback.format_exception(raised.value))

    @pytest.mark.parametrize("legacy", [False, True])
    @pytest.mark.parametrize("failure", ["401", "403", "422", "token"])
    @respx.mock
    async def test_recovered_note_read_failure_reports_no_write(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        legacy: bool,
        failure: str,
    ) -> None:
        """A failed prerequisite GET reports that no note PUT was sent."""
        entry = await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        api_client = hass.data[DOMAIN][entry.entry_id]["api_client"]
        refresh_result = make_fetch_result(
            [
                *make_identity_reservations(*_WRITE_TARGETS),
                make_identity_reservation("res-new", "CONF-res-new"),
            ],
            fallback_served=("res-new",) if legacy else (),
            fallback_sourced=("res-new",) if legacy else (),
        )
        path = RESERVATION_BY_ID_LEGACY_PATH.format(reservation_id="res-new")
        read_url = f"{BASE_URL}{path if legacy else RESERVATIONS_V3_ENDPOINT}"
        secret = "sample-sensitive-read-response"
        read = respx.get(read_url).mock(
            return_value=Response(
                200 if failure == "token" else int(failure), text=secret
            )
        )
        put = respx.route(method="PUT").mock(return_value=Response(200, json={}))
        with (
            caplog.at_level(logging.DEBUG),
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=refresh_result,
            ) as fetch,
            patch.object(
                api_client._token_manager,
                "get_token",
                side_effect=GuestyAuthError(secret) if failure == "token" else None,
                return_value="token",
            ),
            pytest.raises(HomeAssistantError) as raised,
        ):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-new", "note_text": "hello"},
                blocking=True,
                return_response=True,
            )
        fetch.assert_awaited_once()
        assert not put.called
        expected_reads = 0 if failure == "token" else 2 if failure == "401" else 1
        assert len(read.calls) == expected_reads
        message = str(raised.value).lower()
        if failure in ("401", "403", "token"):
            assert ("permission" if failure == "403" else "authentication") in message
        else:
            assert "read" in message
        assert "write was not sent" in message
        assert secret not in str(raised.value)
        assert secret not in caplog.text
        assert secret not in "".join(traceback.format_exception(raised.value))

    @pytest.mark.parametrize("legacy", [False, True])
    @pytest.mark.parametrize("operation", ["note", "status", "field"])
    async def test_recovered_service_reaches_single_attempt_transport(
        self, hass: HomeAssistant, legacy: bool, operation: str
    ) -> None:
        """Each recovered service reaches one actual routed write request."""
        entry = await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        entry_data = hass.data[DOMAIN][entry.entry_id]
        api_client = entry_data["api_client"]
        fetch_result = make_fetch_result(
            [
                *make_identity_reservations(*_WRITE_TARGETS),
                make_identity_reservation("res-new", "CONF-res-new"),
            ],
            fallback_served=("res-new",) if legacy else (),
            fallback_sourced=("res-new",) if legacy else (),
        )
        writes: list[dict[str, object]] = []

        async def request(method: str, path: str, **kwargs: object) -> Response:
            """Answer the note read, then fail the actual write."""
            if method == "GET":
                if legacy:
                    return Response(200, json={"_id": "res-new", "note": ""})
                return Response(
                    200,
                    json={"results": [{"reservationId": "res-new", "notes": {}}]},
                )
            writes.append({"path": path, **kwargs})
            raise GuestyApiError("unavailable")

        if operation == "note":
            service = "add_reservation_note"
            payload: dict[str, object] = {
                "reservation_id": "res-new",
                "note_text": "hello",
            }
        elif operation == "status":
            service = "set_reservation_status"
            payload = {"reservation_id": "res-new", "status": "checked_in"}
        else:
            service = SERVICE_SET_CUSTOM_FIELD
            payload = {
                "target_type": "reservation",
                "target_id": "res-new",
                "field_id": "cf-door-code",
                "value": "0042",
            }
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=fetch_result,
            ) as fetch,
            patch.object(api_client, "_request", side_effect=request),
            pytest.raises(HomeAssistantError),
        ):
            await hass.services.async_call(
                DOMAIN, service, payload, blocking=True, return_response=True
            )
        fetch.assert_awaited_once()
        assert len(writes) == 1
        assert writes[0]["single_attempt"] is True
        assert callable(writes[0]["before_send"])
        assert ("/reservations-v3/" in str(writes[0]["path"])) is not legacy

    @pytest.mark.parametrize("operation", ["note", "status", "field"])
    @pytest.mark.parametrize("status", [404, 410])
    async def test_recovered_legacy_verdict_remains_actionable(
        self, hass: HomeAssistant, operation: str, status: int
    ) -> None:
        """A safe legacy absence or retirement verdict retains its explanation."""
        entry = await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        api_client = hass.data[DOMAIN][entry.entry_id]["api_client"]
        fetch_result = make_fetch_result(
            [
                *make_identity_reservations(*_WRITE_TARGETS),
                make_identity_reservation("res-new", "CONF-res-new"),
            ],
            fallback_served=("res-new",),
            fallback_sourced=("res-new",),
        )
        methods: list[str] = []

        async def request(method: str, path: str, **kwargs: object) -> Response:
            """Return a legacy verdict without echoing the raw body."""
            methods.append(method)
            return Response(
                status,
                json={"error": "Reservation res-new not found SECRET-PIN"},
                request=httpx.Request(method, f"{BASE_URL}{path}"),
            )

        if operation == "note":
            service = "add_reservation_note"
            payload: dict[str, object] = {
                "reservation_id": "res-new",
                "note_text": "hello",
            }
        elif operation == "status":
            service = "set_reservation_status"
            payload = {"reservation_id": "res-new", "status": "checked_in"}
        else:
            service = SERVICE_SET_CUSTOM_FIELD
            payload = {
                "target_type": "reservation",
                "target_id": "res-new",
                "field_id": "cf-door-code",
                "value": "0042",
            }
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=fetch_result,
            ),
            patch.object(api_client, "_request", side_effect=request),
            pytest.raises(HomeAssistantError) as error,
        ):
            await hass.services.async_call(
                DOMAIN, service, payload, blocking=True, return_response=True
            )
        assert ("2027-03-01" if status == 410 else "no longer exists") in str(
            error.value
        )
        assert "SECRET-PIN" not in str(error.value)
        assert methods == (["GET"] if operation == "note" else ["PUT"])

    async def test_missing_field_target_refreshes_once_without_write(
        self, hass: HomeAssistant
    ) -> None:
        """A valid field miss performs one refresh and refuses an omitted target.

        Updated for feature 014 (T052): the target is still absent after
        the refresh, so it now qualifies for the single by-ID read; the
        read finds nothing, so the write is refused as not found by ID.
        """
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        with (
            by_id_not_found() as read,
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    make_identity_reservations(*_WRITE_TARGETS)
                ),
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient, "set_field", new_callable=AsyncMock
            ) as write,
            pytest.raises(HomeAssistantError, match="not found by ID"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-new",
                    "field_id": "cf-door-code",
                    "value": "0042",
                },
                blocking=True,
                return_response=True,
            )
        fetch.assert_awaited_once()
        write.assert_not_awaited()
        assert read.call_count == 1

    @pytest.mark.parametrize(
        "outcome", ["absent", "codeless", "unconfirmed", "unstored"]
    )
    async def test_refreshed_identity_refusal_is_reported(
        self, hass: HomeAssistant, outcome: str
    ) -> None:
        """A field action reports the actual refreshed identity guard refusal.

        An absent or codeless target now gets one feature 014 by-ID
        read (T052 audit), which finds nothing and is reported; the
        pure identity guard still names the 012 refusal.
        """
        entry = await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        baseline = coordinator.identity.baseline
        assert baseline is not None
        if outcome == "unconfirmed":
            baseline.legacy_codes = {"CONF-res-123": "legacy-res-123"}
        reservations = make_identity_reservations(*_WRITE_TARGETS)
        if outcome == "codeless":
            reservations.append(make_identity_reservation("res-new"))
        elif outcome in {"unconfirmed", "unstored"}:
            reservations.append(make_identity_reservation("res-new", "CONF-res-new"))
        category = {
            "absent": "has no known confirmation code",
            "codeless": "has no known confirmation code",
            "unconfirmed": "continuity is unconfirmed",
            "unstored": "could not be written to storage",
        }[outcome]
        reported = "not found by ID" if outcome in {"absent", "codeless"} else category
        with ExitStack() as stack:
            read = stack.enter_context(by_id_not_found())
            fetch = stack.enter_context(
                patch(
                    "custom_components.guesty.GuestyApiClient.get_reservations",
                    new_callable=AsyncMock,
                    return_value=make_fetch_result(reservations),
                )
            )
            write = stack.enter_context(
                patch.object(
                    GuestyCustomFieldsClient, "set_field", new_callable=AsyncMock
                )
            )
            if outcome == "unstored":
                stack.enter_context(
                    patch.object(
                        coordinator.identity, "async_store", side_effect=OSError
                    )
                )
            with pytest.raises(HomeAssistantError) as error:
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_SET_CUSTOM_FIELD,
                    {
                        "target_type": "reservation",
                        "target_id": "res-new",
                        "field_id": "cf-door-code",
                        "value": "0042",
                    },
                    blocking=True,
                    return_response=True,
                )
        fetch.assert_awaited_once()
        write.assert_not_awaited()
        assert read.call_count == (outcome in {"absent", "codeless"})
        assert coordinator.last_update_success
        with pytest.raises(GuestyActionError, match=category):
            guard_reservation_write(entry_data, "res-new", SERVICE_SET_CUSTOM_FIELD)
        assert reported in str(error.value)

    @pytest.mark.parametrize("failed", [True, False])
    async def test_custom_field_miss_refuses_after_failed_or_empty_refresh(
        self, hass: HomeAssistant, failed: bool
    ) -> None:
        """A failed or omitted reservation is never written after one refresh.

        An omitted target now gets one feature 014 by-ID read (T052
        audit); it finds nothing, so the write is still refused. A
        failed refresh never qualifies, so nothing is read.
        """
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                side_effect=GuestyApiError("unavailable") if failed else None,
                return_value=make_fetch_result(
                    make_identity_reservations(*_WRITE_TARGETS)
                ),
            ) as fetch,
            by_id_not_found() as read,
        ):
            await self._expect_refusal(
                hass,
                "res-new",
                "refresh" if failed else "not found by ID",
            )
        fetch.assert_awaited_once()
        assert read.call_count == (not failed)

    @pytest.mark.parametrize("previously_served", [False, True])
    async def test_real_fallback_failure_preserves_custom_field_routing(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        previously_served: bool,
    ) -> None:
        """Only a successful prior legacy lookup authorizes retained writes."""
        freezer.move_to("2026-11-23 12:00:00+00:00")
        entry = await self._setup(
            hass,
            make_identity_reservations(*_WRITE_TARGETS),
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        listing = GuestyListing.from_api_dict(
            {
                "_id": "listing-001",
                "title": "Selected",
                "timezone": "UTC",
                "defaultCheckInTime": "15:00",
                "defaultCheckOutTime": "11:00",
            }
        )
        assert listing is not None
        coordinator.listings_coordinator.data = {listing.id: listing}
        coordinator.listings_coordinator.timing_listings = {listing.id: listing}
        begin = datetime.now(UTC) + timedelta(days=1)
        end = begin + timedelta(days=2)
        segment = make_v3_segment(
            checkIn=begin.isoformat(),
            checkOut=end.isoformat(),
            checkInDateLocalized=begin.date().isoformat(),
            checkOutDateLocalized=end.date().isoformat(),
        )
        originals = [
            make_v3_reservation(
                reservation_id,
                segments=[segment],
                guest_id=None,
                confirmationCode=f"CONF-{reservation_id}",
            )
            for reservation_id in sorted(_WRITE_TARGETS)
        ]
        fallback_calls = 0
        include_target = True

        async def request(method: str, path: str, **kwargs: Any) -> Response:
            """Answer real search pages and one legacy fallback candidate."""
            nonlocal fallback_calls
            assert method == "GET"
            if path == RESERVATIONS_V3_SEARCH_ENDPOINT:
                results = [
                    record
                    for record in originals
                    if include_target or record["reservationId"] != "res-456"
                ]
                return Response(200, json=make_v3_search_page(results))
            assert path == RESERVATION_BY_ID_LEGACY_PATH.format(
                reservation_id="res-456"
            )
            fallback_calls += 1
            if previously_served and fallback_calls == 1:
                return Response(
                    200,
                    json=make_legacy_reservation_by_id(
                        "res-456",
                        checkIn=begin.isoformat(),
                        checkOut=end.isoformat(),
                        checkInDateLocalized=begin.date().isoformat(),
                        checkOutDateLocalized=end.date().isoformat(),
                        confirmationCode="CONF-res-456",
                    ),
                )
            return Response(500, json={"error": "temporarily unavailable"})

        with patch.object(coordinator.api_client, "_request", side_effect=request):
            await coordinator.async_refresh()
            assert "res-456" in coordinator._fallback_candidates()
            include_target = False
            await coordinator.async_refresh()
            if previously_served:
                freezer.tick(timedelta(seconds=LEGACY_FALLBACK_RECHECK_SECONDS + 1))
                await coordinator.async_refresh()
            assert coordinator.last_update_success
            assert coordinator.last_fallback_counters.failed == 1
            assert fallback_calls == (2 if previously_served else 1)
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
            ) as write:
                if previously_served:
                    result = await hass.services.async_call(
                        DOMAIN,
                        SERVICE_SET_CUSTOM_FIELD,
                        {
                            "target_type": "reservation",
                            "target_id": "res-456",
                            "field_id": "cf-door-code",
                            "value": "0042",
                        },
                        blocking=True,
                        return_response=True,
                    )
                    assert result is not None and result["result"] == "success"
                    write.assert_awaited_once()
                    assert write.await_args is not None
                    assert write.await_args.kwargs["route"] == WRITE_ROUTE_LEGACY
                else:
                    with pytest.raises(HomeAssistantError):
                        await hass.services.async_call(
                            DOMAIN,
                            SERVICE_SET_CUSTOM_FIELD,
                            {
                                "target_type": "reservation",
                                "target_id": "res-456",
                                "field_id": "cf-door-code",
                                "value": "0042",
                            },
                            blocking=True,
                            return_response=True,
                        )
                    write.assert_not_awaited()
            assert fallback_calls == (2 if previously_served else 1)

    @pytest.mark.parametrize("previously_served", [False, True])
    async def test_custom_field_miss_rechecks_real_fallback_failure(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        previously_served: bool,
    ) -> None:
        """A code-less target remains refused after a real failed fallback."""
        freezer.move_to("2026-11-23 12:00:00+00:00")
        entry = await self._setup(
            hass,
            make_identity_reservations(*_WRITE_TARGETS),
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        listing = GuestyListing.from_api_dict(
            {
                "_id": "listing-001",
                "title": "Selected",
                "timezone": "UTC",
                "defaultCheckInTime": "15:00",
                "defaultCheckOutTime": "11:00",
            }
        )
        assert listing is not None
        coordinator.listings_coordinator.data = {listing.id: listing}
        coordinator.listings_coordinator.timing_listings = {listing.id: listing}
        begin = datetime.now(UTC) + timedelta(days=1)
        end = begin + timedelta(days=2)
        segment = make_v3_segment(
            checkIn=begin.isoformat(),
            checkOut=end.isoformat(),
            checkInDateLocalized=begin.date().isoformat(),
            checkOutDateLocalized=end.date().isoformat(),
        )
        originals = [
            make_v3_reservation(
                reservation_id,
                segments=[segment],
                guest_id=None,
                confirmationCode=(
                    None if reservation_id == "res-new" else f"CONF-{reservation_id}"
                ),
            )
            for reservation_id in sorted([*_WRITE_TARGETS, "res-new"])
        ]
        include_target = True
        search_calls = 0
        fallback_calls = 0

        async def request(method: str, path: str, **kwargs: Any) -> Response:
            """Answer a normal v3 search and the legacy fallback lookup."""
            nonlocal search_calls, fallback_calls
            assert method == "GET"
            if path == RESERVATIONS_V3_SEARCH_ENDPOINT:
                search_calls += 1
                return Response(
                    200,
                    json=make_v3_search_page(
                        [
                            record
                            for record in originals
                            if include_target or record["reservationId"] != "res-new"
                        ]
                    ),
                )
            assert path == RESERVATION_BY_ID_LEGACY_PATH.format(
                reservation_id="res-new"
            )
            fallback_calls += 1
            if previously_served and fallback_calls == 1:
                return Response(
                    200,
                    json=make_legacy_reservation_by_id(
                        "res-new",
                        checkIn=begin.isoformat(),
                        checkOut=end.isoformat(),
                        checkInDateLocalized=begin.date().isoformat(),
                        checkOutDateLocalized=end.date().isoformat(),
                        confirmationCode=None,
                    ),
                )
            return Response(500, json={"error": "temporarily unavailable"})

        with patch.object(coordinator.api_client, "_request", side_effect=request):
            await coordinator.async_refresh()
            assert "res-new" in coordinator._fallback_candidates()
            include_target = False
            if previously_served:
                await coordinator.async_refresh()
                assert "res-new" in coordinator.fallback_served_ids
                freezer.tick(timedelta(seconds=LEGACY_FALLBACK_RECHECK_SECONDS + 1))
            before_search = search_calls
            with patch.object(
                GuestyCustomFieldsClient, "set_field", new_callable=AsyncMock
            ) as write:
                with pytest.raises(HomeAssistantError):
                    await hass.services.async_call(
                        DOMAIN,
                        SERVICE_SET_CUSTOM_FIELD,
                        {
                            "target_type": "reservation",
                            "target_id": "res-new",
                            "field_id": "cf-door-code",
                            "value": "0042",
                        },
                        blocking=True,
                        return_response=True,
                    )
                write.assert_not_awaited()
            assert search_calls == before_search + 1
            assert fallback_calls == (2 if previously_served else 1)
            assert coordinator.last_fallback_counters.failed == 1
            assert ("res-new" in coordinator.fallback_served_ids) is previously_served

    async def test_custom_field_miss_reports_safe_filter_failure(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A search rejection cannot leak a custom-field value or write it."""
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        secret = "sample-sensitive-pin-0072"
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                side_effect=GuestyFilterRejectedError(
                    secret,
                    endpoint="/reservations-v3/search",
                    status_code=400,
                    body_excerpt=secret,
                ),
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient, "set_field", new_callable=AsyncMock
            ) as write,
            pytest.raises(HomeAssistantError) as error,
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-new",
                    "field_id": "cf-door-code",
                    "value": secret,
                },
                blocking=True,
                return_response=True,
            )
        fetch.assert_awaited_once()
        write.assert_not_awaited()
        detail = str(error.value)
        assert secret not in detail
        assert secret not in caplog.text
        assert "/reservations-v3/search" in detail
        assert "400" in detail
        assert "search settings" in detail
        assert "contact support" in detail

    async def test_blank_custom_field_target_does_not_refresh(
        self, hass: HomeAssistant
    ) -> None:
        """Whitespace-only targets are rejected before the coordinator runs."""
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        with patch(
            "custom_components.guesty.GuestyApiClient.get_reservations",
            new_callable=AsyncMock,
        ) as fetch:
            await self._expect_refusal(hass, " ", "non-empty")
        fetch.assert_not_awaited()

    @pytest.mark.parametrize(
        ("guard", "message"),
        [
            ("unusable", "identity baseline"),
            ("unstored", "could not be written to storage"),
            ("unconfirmed", "continuity is unconfirmed"),
            ("ambiguous", "aborted because a confirmation code"),
            ("unroutable", "no write is sent"),
        ],
    )
    async def test_custom_field_independent_refusal_does_not_refresh(
        self, hass: HomeAssistant, guard: str, message: str
    ) -> None:
        """Independent identity and provenance refusals precede recovery."""
        entry = await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        baseline = coordinator.identity.baseline
        assert baseline is not None
        if guard == "unusable":
            baseline.codes.clear()
        elif guard == "unstored":
            coordinator.identity._unstored = True
        elif guard == "unconfirmed":
            baseline.continuity = "unconfirmed"
        elif guard == "ambiguous":
            coordinator.identity.mark_ambiguous()
        else:
            coordinator.fallback_sourced_ids = frozenset({"res-new"})
        with patch(
            "custom_components.guesty.GuestyApiClient.get_reservations",
            new_callable=AsyncMock,
        ) as fetch:
            await self._expect_refusal(hass, "res-new", message)
        fetch.assert_not_awaited()

    @pytest.mark.parametrize(
        ("field_id", "value"),
        [("missing-field", "0042"), ("cf-door-code", False)],
    )
    async def test_invalid_custom_field_does_not_refresh(
        self, hass: HomeAssistant, field_id: str, value: str | bool
    ) -> None:
        """An invalid field definition or value fails before recovery."""
        await self._setup(hass, make_identity_reservations(*_WRITE_TARGETS))
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
            ) as fetch,
            patch.object(
                GuestyCustomFieldsClient, "set_field", new_callable=AsyncMock
            ) as write,
            pytest.raises(HomeAssistantError),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-new",
                    "field_id": field_id,
                    "value": value,
                },
                blocking=True,
                return_response=True,
            )
        fetch.assert_not_awaited()
        write.assert_not_awaited()


class TestReservationCustomFieldRouting:
    """The service routes a served reservation to legacy (T129)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests.

        Yields:
            None, with the definitions call patched.
        """
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ):
            yield

    @staticmethod
    async def _loaded_entry(hass: HomeAssistant) -> MockConfigEntry:
        """Set up an entry with the usual API calls mocked.

        Args:
            hass: Home Assistant instance.

        Returns:
            The loaded config entry.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)
        with (
            patch(
                "custom_components.guesty.GuestyApiClient.test_connection",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "custom_components.guesty.GuestyApiClient.get_listings",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    make_identity_reservations(*_WRITE_TARGETS),
                ),
            ),
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        return entry

    async def test_served_reservation_routes_to_legacy(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The door code is sent verbatim on the legacy route."""
        entry = await self._loaded_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        coordinator.fallback_served_ids = frozenset({"res-789"})
        coordinator.fallback_sourced_ids = frozenset({"res-789"})

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
                    "value": "0042",
                },
                blocking=True,
            )

        mock_set.assert_awaited_once_with(
            target_type="reservation",
            target_id="res-789",
            field_id="cf-door-code",
            value="0042",
            route=WRITE_ROUTE_LEGACY,
        )
        counters = coordinator.legacy_write_counters
        assert counters.custom_field_succeeded == 1

    async def test_retired_routing_refuses_the_write(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A retired legacy generation sends nothing at all."""
        entry = await self._loaded_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        coordinator.fallback_served_ids = frozenset({"res-789"})
        coordinator.fallback_sourced_ids = frozenset({"res-789"})
        api_client = entry_data["api_client"]

        with (
            patch.object(
                type(api_client),
                "legacy_write_routing_available",
                property(lambda _self: False),
            ),
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
            ) as mock_set,
            pytest.raises(HomeAssistantError, match="2027-03-01"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-789",
                    "field_id": "cf-door-code",
                    "value": "0042",
                },
                blocking=True,
            )

        assert mock_set.await_count == 0
        assert coordinator.legacy_write_counters.custom_field_refused == 1

    async def test_listing_target_is_unaffected(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A listing write never acquires a route."""
        entry = await self._loaded_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.fallback_served_ids = frozenset({"res-789"})

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
        ) as mock_set:
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

        await_args = mock_set.await_args
        assert await_args is not None
        assert "route" not in await_args.kwargs
        assert coordinator.legacy_write_counters.total == 0

    async def test_routing_messages_survive_the_service(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The retirement explanation is not genericized."""
        entry = await self._loaded_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        coordinator.fallback_served_ids = frozenset({"res-789"})
        coordinator.fallback_sourced_ids = frozenset({"res-789"})

        with (
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                side_effect=GuestyActionError(
                    "legacy answered /v1/reservations/res-789 with "
                    "HTTP 410; the legacy resources retire on "
                    "2027-03-01",
                    target_id="res-789",
                    action_type="set_custom_field",
                ),
            ),
            pytest.raises(HomeAssistantError) as exc_info,
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "reservation",
                    "target_id": "res-789",
                    "field_id": "cf-door-code",
                    "value": "0042",
                },
                blocking=True,
            )

        assert "2027-03-01" in str(exc_info.value)
        assert coordinator.legacy_write_counters.custom_field_failed == 1


# ── Feature 014: by-ID write fallback ───────────────────────────────

_BY_ID = BY_ID_TARGET
_CF_ACTIONS_LOGGER = "custom_components.guesty.actions"
_DOOR_CODE = "0042"


def _cf_refresh(*extra: GuestyReservation, **fields: Any) -> Any:
    """Build a qualifying refresh that keeps every write target coded.

    Args:
        *extra: Reservations the refresh also returns.
        **fields: Fetch result fields to set.

    Returns:
        The refresh result.
    """
    return make_fetch_result(
        [*make_identity_reservations(*_WRITE_TARGETS), *extra], **fields
    )


def _cf_codeless_refresh() -> Any:
    """Build a refresh that returns the target without a code.

    Returns:
        The refresh result.
    """
    return _cf_refresh(make_identity_reservation(_BY_ID), returned_ids=(_BY_ID,))


def _written(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Collect the by-ID success INFO lines.

    Args:
        caplog: The log capture.

    Returns:
        Every INFO message reporting a by-ID write.
    """
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == _CF_ACTIONS_LOGGER
        and record.levelno == logging.INFO
        and "found by ID, written" in record.getMessage()
    ]


# Shared by-ID cases run against the 013 double and the real 013
# adapter (T052); ``real`` installs no double.
_CF_EVIDENCE_KINDS = pytest.mark.parametrize("evidence_kind", ["double", "real"])


class _ByIdCustomFieldBase:
    """Shared setup for the by-ID custom-field classes."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions.

        Yields:
            None, while the definitions client is patched.
        """
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ):
            yield

    @staticmethod
    async def _setup(hass: HomeAssistant) -> Any:
        """Load an entry seeded with every write target.

        Args:
            hass: Home Assistant instance.

        Returns:
            The entry's reservations coordinator.
        """
        entry = await TestSetCustomFieldIdentityGuard._setup(
            hass, make_identity_reservations(*_WRITE_TARGETS)
        )
        return hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]

    @staticmethod
    async def _call(hass: HomeAssistant, target_id: str = _BY_ID) -> Any:
        """Set the door code on a reservation.

        Args:
            hass: Home Assistant instance.
            target_id: The reservation targeted.

        Returns:
            The service response.
        """
        return await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_CUSTOM_FIELD,
            {
                "target_type": "reservation",
                "target_id": target_id,
                "field_id": "cf-door-code",
                "value": _DOOR_CODE,
            },
            blocking=True,
            return_response=True,
        )

    @classmethod
    async def _refusal(cls, hass: HomeAssistant) -> str:
        """Set the door code expecting a failure.

        Args:
            hass: Home Assistant instance.

        Returns:
            The surfaced error text.
        """
        with pytest.raises(HomeAssistantError) as error:
            await cls._call(hass)
        return str(error.value)


class TestByIdRefusedWhileEvidenceNotReadyCustomField(_ByIdCustomFieldBase):
    """Non-ready 013 evidence refuses custom fields first (T025, T052).

    Converted from ``TestByIdFallbackInertUntil013CustomField``: with
    the real 013 adapter a qualifying target is refused by 013's
    evidence-state guard before membership, with no 012 recovery
    refresh, no by-ID read, no write and no 014 DEBUG category.
    """

    @pytest.mark.parametrize(
        ("state", "match"),
        [
            (EVIDENCE_UNLOADED, REFUSAL_EVIDENCE_UNAVAILABLE),
            (EVIDENCE_REVALIDATING, REFUSAL_REVALIDATING),
            (EVIDENCE_UNSTORED, REFUSAL_EVIDENCE_UNAVAILABLE),
            (
                EVIDENCE_SATURATED,
                f"{REFUSAL_EVIDENCE_UNAVAILABLE} {REFUSAL_EVIDENCE_REPAIR}",
            ),
            (
                EVIDENCE_COMPROMISED,
                f"{REFUSAL_EVIDENCE_UNAVAILABLE} {REFUSAL_EVIDENCE_REPAIR}",
            ),
        ],
    )
    @pytest.mark.parametrize("target", ["absent", "codeless"])
    async def test_not_ready_evidence_refuses_before_any_read(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        state: str,
        match: str,
        target: str,
    ) -> None:
        """013's refusal stands; nothing is refreshed, read or written."""
        coordinator = await self._setup(hass)
        if target == "codeless":
            with by_id_network(refresh=_cf_codeless_refresh()):
                await coordinator.async_refresh()
        caplog.set_level(logging.DEBUG, logger=_CF_ACTIONS_LOGGER)
        with (
            patch.object(
                ConflictOnlyEvidence, "state", new_callable=PropertyMock
            ) as current,
            by_id_network(refresh=_cf_refresh()) as routes,
            pytest.raises(HomeAssistantError) as error,
        ):
            current.return_value = state
            await self._call(hass)

        assert str(error.value) == match
        assert isinstance(error.value.__cause__, GuestyActionError)
        assert not isinstance(error.value.__cause__, ReservationIdentityMissError)
        routes.fetch.assert_not_awaited()
        assert routes.read.call_count == 0
        assert routes.writes == 0
        assert _DOOR_CODE not in caplog.text
        assert [
            tuple(record.args or ())
            for record in caplog.records
            if record.name == _CF_ACTIONS_LOGGER and "not eligible" in str(record.msg)
        ] == []

    async def test_ready_evidence_reaches_the_by_id_read(
        self, hass: HomeAssistant
    ) -> None:
        """Control: the same unpatched flow writes once evidence is ready."""
        coordinator = await self._setup(hass)
        assert coordinator.identity.conflict_evidence.available()
        with by_id_network(refresh=_cf_refresh()) as routes:
            result = await self._call(hass)

        assert result is not None
        assert result["result"] == "success"
        routes.fetch.assert_awaited_once()
        assert routes.read.call_count == 1
        assert routes.fields.call_count == 1
        assert routes.writes == 1

    async def test_listing_write_never_reads_by_id(self, hass: HomeAssistant) -> None:
        """A listing custom-field write is untouched by the fallback."""
        await self._setup(hass)
        with (
            by_id_network(
                refresh=_cf_refresh(), evidence=FakeConflictEvidence()
            ) as routes,
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                return_value=GuestyCustomFieldResult(
                    success=True,
                    target_type="listing",
                    target_id="lst-123",
                    field_id="cf-region",
                ),
            ) as set_field,
        ):
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
                {
                    "target_type": "listing",
                    "target_id": "lst-123",
                    "field_id": "cf-region",
                    "value": "emea",
                },
                blocking=True,
                return_response=True,
            )

        assert result is not None
        assert result["result"] == "success"
        set_field.assert_awaited_once()
        routes.fetch.assert_not_awaited()
        assert routes.read.call_count == 0


class TestByIdCustomFieldRefusals(_ByIdCustomFieldBase):
    """By-ID refusals surface their own text, never the generic one (T030)."""

    @pytest.mark.parametrize(
        ("case", "stem", "category"),
        [
            ("not_found", "not found by ID", "not_found"),
            ("status", "found but refused", "disallowed_status"),
            ("conflict", "found but refused", "identity_conflict"),
            ("rate_limit", "by-ID read failed", "rate_limit"),
        ],
    )
    @_CF_EVIDENCE_KINDS
    async def test_by_id_refusal_text(
        self,
        hass: HomeAssistant,
        case: str,
        stem: str,
        category: str,
        evidence_kind: str,
    ) -> None:
        """Each refusal names its outcome and sends no custom-field PUT."""
        coordinator = await self._setup(hass)
        read: Any = None
        evidence = FakeConflictEvidence() if evidence_kind == "double" else None
        if case == "not_found":
            read = Response(200, json={"results": []})
        elif case == "status":
            read = Response(
                200, json={"results": [make_by_id_record(status="alteration")]}
            )
        elif case == "conflict" and evidence is not None:
            evidence.ids["CONF-res-new"] = frozenset({"res-other"})
        elif case == "conflict":
            coordinator.identity.conflict_evidence.record(
                [RejectedCode("res-other", "CONF-res-new")],
                {},
                "v3",
                datetime.now(UTC),
            )
            assert coordinator.identity.conflict_evidence.available()
        else:
            read = Response(429)
        with by_id_network(
            refresh=_cf_refresh(), read=read, evidence=evidence
        ) as routes:
            text = await self._refusal(hass)

        assert text.startswith(stem)
        assert category in text
        assert "API error during custom field update" not in text
        assert _DOOR_CODE not in text
        assert routes.read.call_count == 1
        assert routes.fields.call_count == 0


class TestByIdWriteFallback223CustomField(_ByIdCustomFieldBase):
    """The #223 door code is written by ID (T037)."""

    @_CF_EVIDENCE_KINDS
    async def test_223_same_day_absent_reservation_door_code_written_by_id(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
        evidence_kind: str,
    ) -> None:
        """One search, one by-ID read and one PUT, retaining nothing."""
        freezer.move_to("2026-11-23 18:00:00+00:00")
        coordinator = await self._setup(hass)
        evidence = FakeConflictEvidence() if evidence_kind == "double" else None
        segment = make_v3_segment(
            checkIn="2026-11-23T15:00:00+00:00",
            checkOut="2026-11-25T11:00:00+00:00",
            checkInDateLocalized="2026-11-23",
            checkOutDateLocalized="2026-11-25",
        )
        record = make_by_id_record(
            stay=[segment], createdAt="2026-11-23T17:30:00+00:00"
        )
        captured: dict[str, Any] = {}

        def read(request: httpx.Request) -> Response:
            """Capture retained state just before the read answers."""
            captured.update(capture_by_id_retention(coordinator, evidence))
            captured["saves"] = save.await_count
            return Response(200, json={"results": [record]})

        caplog.set_level(logging.INFO, logger=_CF_ACTIONS_LOGGER)
        store = coordinator.identity._store
        with (
            patch.object(store, "async_save", wraps=store.async_save) as save,
            by_id_network(
                refresh=_cf_refresh(), read=read, evidence=evidence
            ) as routes,
        ):
            result = await self._call(hass)
            after = capture_by_id_retention(coordinator, evidence)
            after["saves"] = save.await_count

        assert result is not None
        assert result["result"] == "success"
        routes.fetch.assert_awaited_once()
        assert routes.read.call_count == 1
        assert routes.token.call_count <= 1
        assert routes.writes == 1
        assert routes.fields.call_count == 1
        body = json.loads(routes.fields.calls[0].request.content)
        assert _DOOR_CODE in json.dumps(body)
        lines = _written(caplog)
        assert len(lines) == 1
        assert "absent" in lines[0]
        assert after == captured

    @_CF_EVIDENCE_KINDS
    async def test_codeless_custom_field_target_written_by_id(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        evidence_kind: str,
    ) -> None:
        """A returned code-less target is written with its qualifier."""
        await self._setup(hass)
        caplog.set_level(logging.INFO, logger=_CF_ACTIONS_LOGGER)
        with by_id_network(
            refresh=_cf_codeless_refresh(),
            evidence=FakeConflictEvidence() if evidence_kind == "double" else None,
        ) as routes:
            result = await self._call(hass)

        assert result["result"] == "success"
        assert routes.read.call_count == 1
        assert routes.fields.call_count == 1
        lines = _written(caplog)
        assert len(lines) == 1
        assert "codeless" in lines[0]

    @staticmethod
    def _inject_newer_refresh(
        hass: HomeAssistant, coordinator: Any, *, fallback: bool
    ) -> Any:
        """Inject a newer accepted refresh on the write's token call.

        Args:
            hass: Home Assistant instance.
            coordinator: The entry's coordinator.
            fallback: Give the target fallback provenance instead of
                a v3 code.

        Returns:
            The active patcher.
        """
        manager = coordinator.api_client._token_manager
        original = manager.get_token
        calls = 0

        def inject() -> None:
            """Accept a newer refresh coding the target."""
            latest = coordinator._write_miss_outcomes[-1]
            attempt = latest[1] + 1
            coordinator._write_miss_outcomes.append(
                (latest[0] + 1, attempt, True, *latest[3:])
            )
            baseline = coordinator.identity.baseline
            if fallback:
                coordinator._accepted_snapshot = replace(
                    coordinator._accepted_snapshot,
                    attempt=attempt,
                    fallback_sourced=frozenset({_BY_ID}),
                )
                coordinator.fallback_sourced_ids = frozenset({_BY_ID})
                baseline.fallback_codes = {
                    **(baseline.fallback_codes or {}),
                    "CONF-res-new": _BY_ID,
                }
            else:
                coordinator._accepted_snapshot = replace(
                    coordinator._accepted_snapshot, attempt=attempt
                )
                baseline.codes["CONF-res-new"] = _BY_ID

        async def get_token(*args: Any, **kwargs: Any) -> str:
            """Inject on the write's token acquisition."""
            nonlocal calls
            calls += 1
            if calls == 2:
                inject()
            token: str = await original(*args, **kwargs)
            return token

        return patch.object(manager, "get_token", side_effect=get_token)

    async def test_newer_accepted_refresh_v3_provenance_writes(
        self, hass: HomeAssistant
    ) -> None:
        """A newer refresh coding the target on v3 lets the PUT go."""
        coordinator = await self._setup(hass)
        with (
            by_id_network(
                refresh=_cf_refresh(), evidence=FakeConflictEvidence()
            ) as routes,
            self._inject_newer_refresh(hass, coordinator, fallback=False),
        ):
            result = await self._call(hass)

        assert result["result"] == "success"
        assert routes.read.call_count == 1
        assert routes.fields.call_count == 1

    async def test_newer_accepted_refresh_fallback_provenance_refuses(
        self, hass: HomeAssistant
    ) -> None:
        """A newer refresh giving the target fallback provenance refuses."""
        coordinator = await self._setup(hass)
        with (
            by_id_network(
                refresh=_cf_refresh(), evidence=FakeConflictEvidence()
            ) as routes,
            self._inject_newer_refresh(hass, coordinator, fallback=True),
        ):
            text = await self._refusal(hass)

        assert text.startswith("found but refused")
        assert "guard_witness" in text
        assert routes.read.call_count == 1
        assert routes.writes == 0

    @pytest.mark.parametrize(
        ("write", "hint"),
        [
            (Response(400), "rejected"),
            (httpx.ReadTimeout("timed out"), "may have reached Guesty"),
        ],
        ids=["rejected", "timeout"],
    )
    async def test_custom_field_by_id_write_failure(
        self, hass: HomeAssistant, write: Any, hint: str
    ) -> None:
        """One PUT, no retry, and the by-ID write-failure text."""
        await self._setup(hass)
        with (
            by_id_network(
                refresh=_cf_refresh(), write=write, evidence=FakeConflictEvidence()
            ) as routes,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            text = await self._refusal(hass)

        assert text.startswith("found by ID, write failed")
        assert hint in text
        assert "not retried" in text
        assert "API error during custom field update" not in text
        assert routes.read.call_count == 1
        assert routes.fields.call_count == 1


# ── Feature 013: evidence-state custom-field refusals (T021) ────────

_CF_REJECTED = "res-rej"


def _cf_rejected_refresh(kind: str = "partial") -> Any:
    """Build a refresh whose rejected target is partial or dropped.

    Args:
        kind: ``partial`` keeps the record as partial; ``dropped``
            leaves it only in ``rejected_ids``.

    Returns:
        The refresh result.
    """
    extra = (
        [replace(make_identity_reservation(_CF_REJECTED, "CONF-REJ"), partial=True)]
        if kind == "partial"
        else []
    )
    return make_fetch_result(
        [*make_identity_reservations(*_WRITE_TARGETS), *extra],
        rejected_ids=(_CF_REJECTED,),
        rejected_codes=(RejectedCode(_CF_REJECTED, "CONF-REJ"),),
    )


class TestEvidenceStateCustomFieldRefusals:
    """Evidence state and validation decide custom-field writes (T021)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions.

        Yields:
            None, while the definitions client is patched.
        """
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ):
            yield

    @staticmethod
    async def _write(
        hass: HomeAssistant,
        target_id: str,
        refresh: Any,
        evidence: FakeConflictEvidence | None = None,
    ) -> tuple[Any, Any, AsyncMock]:
        """Set the door code, capturing the network routes and write mock.

        Args:
            hass: Home Assistant instance.
            target_id: The reservation targeted.
            refresh: What a recovery refresh returns.
            evidence: The by-ID evidence double to install, or None
                to keep the production adapter.

        Returns:
            The response or raised error, the mocked routes (the
            recovery refresh and the by-ID read) and the write mock.
        """
        with (
            by_id_network(
                refresh=refresh, evidence=evidence, reservation_id=target_id
            ) as routes,
            patch.object(
                GuestyCustomFieldsClient,
                "set_field",
                new_callable=AsyncMock,
                return_value=GuestyCustomFieldResult(
                    success=True,
                    target_type="reservation",
                    target_id=target_id,
                    field_id="cf-door-code",
                ),
            ) as set_field,
        ):
            try:
                outcome: Any = await hass.services.async_call(
                    DOMAIN,
                    SERVICE_SET_CUSTOM_FIELD,
                    {
                        "target_type": "reservation",
                        "target_id": target_id,
                        "field_id": "cf-door-code",
                        "value": "1234",
                    },
                    blocking=True,
                    return_response=True,
                )
            except HomeAssistantError as err:
                outcome = err
        return outcome, routes, set_field

    @pytest.mark.parametrize(
        ("state", "match"),
        [
            (EVIDENCE_UNLOADED, REFUSAL_EVIDENCE_UNAVAILABLE),
            (EVIDENCE_REVALIDATING, REFUSAL_REVALIDATING),
            (EVIDENCE_UNSTORED, REFUSAL_EVIDENCE_UNAVAILABLE),
            (
                EVIDENCE_SATURATED,
                f"{REFUSAL_EVIDENCE_UNAVAILABLE} {REFUSAL_EVIDENCE_REPAIR}",
            ),
            (
                EVIDENCE_COMPROMISED,
                f"{REFUSAL_EVIDENCE_UNAVAILABLE} {REFUSAL_EVIDENCE_REPAIR}",
            ),
        ],
    )
    async def test_unavailable_evidence_refuses_plainly(
        self, hass: HomeAssistant, state: str, match: str
    ) -> None:
        """A non-ready store refuses before membership and refresh."""
        await TestSetCustomFieldIdentityGuard._setup(
            hass, make_identity_reservations(*_WRITE_TARGETS)
        )
        with patch.object(
            ConflictOnlyEvidence, "state", new_callable=PropertyMock
        ) as current:
            current.return_value = state
            outcome, routes, set_field = await self._write(
                hass,
                "res-001",
                make_fetch_result(make_identity_reservations(*_WRITE_TARGETS)),
                evidence=FakeConflictEvidence(),
            )

        assert isinstance(outcome, HomeAssistantError)
        assert str(outcome) == match
        assert isinstance(outcome.__cause__, GuestyActionError)
        assert not isinstance(outcome.__cause__, ReservationIdentityMissError)
        routes.fetch.assert_not_awaited()
        assert routes.read.call_count == 0
        set_field.assert_not_awaited()

    @pytest.mark.parametrize("hidden_by", ["listing_selection", "out_of_window"])
    async def test_filtered_valid_target_is_written(
        self, hass: HomeAssistant, hidden_by: str
    ) -> None:
        """A valid record hidden from publication is still written (R11).

        Listing selection and the configured window decide what is
        displayed, never what is valid. The only status-based drop,
        an alteration hold, is refused for writes by design, so it is
        not a filtered-but-valid case.
        """
        target = "res-hidden"
        record = make_identity_reservation(target, f"CONF-{target}")
        options: dict[str, Any] = {}
        if hidden_by == "listing_selection":
            options[CONF_SELECTED_LISTINGS] = ["another-listing"]
        else:
            assert record.check_in is not None
            assert record.check_out is not None
            record = replace(
                record,
                check_in=record.check_in + timedelta(days=400),
                check_out=record.check_out + timedelta(days=400),
            )
        listing = GuestyListing.from_api_dict(
            {"_id": "listing-001", "title": "Hidden", "timezone": "UTC"}
        )
        assert listing is not None
        records = [*make_identity_reservations(*_WRITE_TARGETS), record]
        entry = _make_entry(options=options)
        entry.add_to_hass(hass)
        with (
            stub_guesty_network(listings=[listing], reservations=records),
            patch(
                "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
                new_callable=AsyncMock,
                return_value=sample_custom_field_definitions(),
            ),
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        assert coordinator.last_update_success
        published = {r.id for items in coordinator.data.values() for r in items}
        assert bool(published) is (hidden_by == "out_of_window")
        assert target not in published
        assert coordinator.identity.write_refusal(target) is None

        outcome, routes, set_field = await self._write(
            hass, target, make_fetch_result(records)
        )

        assert not isinstance(outcome, HomeAssistantError)
        routes.fetch.assert_not_awaited()
        set_field.assert_awaited_once()
        assert set_field.await_args is not None
        assert target in set_field.await_args.args + tuple(
            set_field.await_args.kwargs.values()
        )

    @pytest.mark.parametrize("kind", ["partial", "dropped"])
    async def test_rejected_target_is_refused_then_written_once_fixed(
        self, hass: HomeAssistant, kind: str
    ) -> None:
        """A rejected target misses membership until a refresh fixes it.

        The refusal is the plain membership refusal: one recovery
        refresh, no by-ID read and no custom-field write.
        """
        entry = await TestSetCustomFieldIdentityGuard._setup(
            hass, make_identity_reservations(*_WRITE_TARGETS)
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        with patch(
            "custom_components.guesty.GuestyApiClient.get_reservations",
            new_callable=AsyncMock,
            return_value=_cf_rejected_refresh(kind),
        ):
            await coordinator.async_refresh()
        assert coordinator.identity.conflict_evidence.available()

        refused, routes, set_field = await self._write(
            hass, _CF_REJECTED, _cf_rejected_refresh(kind)
        )
        assert isinstance(refused, HomeAssistantError)
        assert str(refused) == REFUSAL_NO_CODE.format(reservation_id=_CF_REJECTED)
        routes.fetch.assert_awaited_once()
        assert routes.read.call_count == 0
        set_field.assert_not_awaited()

        fixed = make_fetch_result(
            [
                *make_identity_reservations(*_WRITE_TARGETS),
                make_identity_reservation(_CF_REJECTED, "CONF-REJ"),
            ]
        )
        written, routes, set_field = await self._write(hass, _CF_REJECTED, fixed)
        assert not isinstance(written, HomeAssistantError)
        routes.fetch.assert_awaited_once()
        set_field.assert_awaited_once()

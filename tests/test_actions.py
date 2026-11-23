# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty HA action service handlers."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import ANY, AsyncMock, patch

import httpx
import pytest
import respx
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from httpx import Response
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.actions import (
    _SCHEMA_ADD_NOTE,
    ROUTE_LEGACY,
    ROUTE_V3,
    execute_reservation_write,
    select_reservation_write_route,
)
from custom_components.guesty.api.actions import GuestyActionsClient
from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    BASE_URL,
    CALENDAR_ENDPOINT,
    LISTINGS_ENDPOINT,
    RESERVATIONS_ENDPOINT,
    TASKS_ENDPOINT,
    TOKEN_URL,
)
from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyApiError,
)
from custom_components.guesty.api.models import (
    ActionResult,
    GuestyListing,
    GuestyReservation,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FUTURE_DAYS,
    CONF_PAST_DAYS,
    CONF_RESERVATIONS_API,
    CONF_SELECTED_LISTINGS,
    DOMAIN,
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
)
from custom_components.guesty.identity import CONTINUITY_UNCONFIRMED
from tests.conftest import (
    FakeTokenStorage,
    make_fetch_result,
    make_identity_reservation,
    make_identity_reservations,
    make_token_response,
)

# ── Patch targets ───────────────────────────────────────────────────

_PATCH_TEST = "custom_components.guesty.GuestyApiClient.test_connection"
_PATCH_LISTINGS = "custom_components.guesty.GuestyApiClient.get_listings"
_PATCH_RESERVATIONS = "custom_components.guesty.GuestyApiClient.get_reservations"


# ── Helpers ─────────────────────────────────────────────────────────


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


# Reservation identifiers the write actions target in this module.
# Reservation identifiers the action tests write to; each needs a
# baseline entry before the identity guard will permit the write.
_WRITE_TARGETS = ("res-001", "res-bad", "res-e2e", "x")


async def _setup_entry(
    hass: HomeAssistant,
    reservations: list[GuestyReservation] | None = None,
    *,
    upgraded: bool = False,
    **entry_overrides: object,
) -> MockConfigEntry:
    """Set up a config entry with all mocks.

    Args:
        hass: Home Assistant instance.
        reservations: Reservations the first refresh returns, which
            seed the identity baseline the write guard consults.
        upgraded: Present the entry as an upgrade from a legacy-only
            release, so setup runs the one-time bootstrap sweep. The
            sweep is stubbed to return nothing, which is the
            uninformative outcome that leaves continuity unconfirmed.
        **entry_overrides: Fields to override on the config entry.

    Returns:
        The loaded MockConfigEntry.
    """
    entry = _make_entry(**entry_overrides)
    entry.add_to_hass(hass)

    if reservations is None:
        reservations = make_identity_reservations(*_WRITE_TARGETS)

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
            _PATCH_TEST,
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            _PATCH_LISTINGS,
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(reservations),
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    return entry


def _result_dict(
    *,
    success: bool = True,
    target_id: str = "test-id",
    error: str | None = None,
) -> dict[str, Any]:
    """Build an expected ActionResult response dict.

    Args:
        success: Whether action succeeded.
        target_id: Resource identifier.
        error: Optional error description.

    Returns:
        Expected service response dictionary.
    """
    result: dict[str, Any] = {
        "success": success,
        "target_id": target_id,
    }
    if error is not None:
        result["error"] = error
    return result


# ── Service Registration Tests ──────────────────────────────────────


class TestActionServiceRegistration:
    """Tests for action service lifecycle (T014)."""

    async def test_services_registered_after_setup(
        self,
        hass: HomeAssistant,
    ) -> None:
        """All five action services are registered after setup."""
        await _setup_entry(hass)

        for service_name in (
            "add_reservation_note",
            "set_listing_status",
            "create_task",
            "set_calendar_availability",
            "set_reservation_status",
        ):
            assert hass.services.has_service(DOMAIN, service_name), (
                f"Service {DOMAIN}.{service_name} not registered"
            )

    async def test_services_removed_after_last_unload(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Services removed when last config entry is unloaded."""
        entry = await _setup_entry(hass)

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        for service_name in (
            "add_reservation_note",
            "set_listing_status",
            "create_task",
            "set_calendar_availability",
            "set_reservation_status",
        ):
            assert not hass.services.has_service(
                DOMAIN,
                service_name,
            ), f"Service {DOMAIN}.{service_name} not removed"

    async def test_services_not_registered_twice(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Second entry does not re-register services."""
        await _setup_entry(hass)

        entry2 = MockConfigEntry(
            domain=DOMAIN,
            title="Guesty (test2)",
            data={
                CONF_CLIENT_ID: "test-client-id-2",
                CONF_CLIENT_SECRET: "test-client-secret-2",
            },
            unique_id="test-client-id-2",
        )
        entry2.add_to_hass(hass)

        with (
            patch(
                _PATCH_TEST,
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                _PATCH_LISTINGS,
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    make_identity_reservations(*_WRITE_TARGETS)
                ),
            ),
        ):
            await hass.config_entries.async_setup(entry2.entry_id)
            await hass.async_block_till_done()

        # Services still registered
        assert hass.services.has_service(
            DOMAIN,
            "add_reservation_note",
        )

    async def test_services_kept_when_not_last_entry(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Services kept when other entries remain loaded."""
        entry1 = await _setup_entry(hass)

        entry2 = MockConfigEntry(
            domain=DOMAIN,
            title="Guesty (test2)",
            data={
                CONF_CLIENT_ID: "test-client-id-2",
                CONF_CLIENT_SECRET: "test-client-secret-2",
            },
            unique_id="test-client-id-2",
        )
        entry2.add_to_hass(hass)

        with (
            patch(
                _PATCH_TEST,
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                _PATCH_LISTINGS,
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    make_identity_reservations(*_WRITE_TARGETS)
                ),
            ),
        ):
            await hass.config_entries.async_setup(entry2.entry_id)
            await hass.async_block_till_done()

        # Unload first entry
        await hass.config_entries.async_unload(entry1.entry_id)
        await hass.async_block_till_done()

        # Services still registered (entry2 remains)
        assert hass.services.has_service(
            DOMAIN,
            "add_reservation_note",
        )

    async def test_multi_entry_config_entry_id_routes(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Service call with config_entry_id targets correct entry."""
        entry1 = await _setup_entry(hass)

        entry2 = MockConfigEntry(
            domain=DOMAIN,
            title="Guesty (test2)",
            data={
                CONF_CLIENT_ID: "test-client-id-2",
                CONF_CLIENT_SECRET: "test-client-secret-2",
            },
            unique_id="test-client-id-2",
        )
        entry2.add_to_hass(hass)

        with (
            patch(
                _PATCH_TEST,
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                _PATCH_LISTINGS,
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    make_identity_reservations(*_WRITE_TARGETS)
                ),
            ),
        ):
            await hass.config_entries.async_setup(
                entry2.entry_id,
            )
            await hass.async_block_till_done()

        other_client = AsyncMock()
        other_client.add_reservation_note = AsyncMock(
            return_value=ActionResult(
                success=True,
                target_id="res-other",
            ),
        )
        hass.data[DOMAIN][entry1.entry_id]["actions_client"] = other_client
        hass.data[DOMAIN][entry2.entry_id]["actions_client"] = mock_actions_client

        result = await hass.services.async_call(
            DOMAIN,
            "add_reservation_note",
            {
                "reservation_id": "res-001",
                "note_text": "Test note",
                "config_entry_id": entry2.entry_id,
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.add_reservation_note.assert_awaited_once()
        other_client.add_reservation_note.assert_not_awaited()
        assert result == _result_dict(target_id="res-001")


# ── Schema Validation Tests ─────────────────────────────────────────


class TestSchemaValidation:
    """Tests for Voluptuous schema enforcement."""

    async def test_set_listing_status_rejects_bad_status(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Invalid status value is rejected by schema."""
        await _setup_entry(hass)

        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "set_listing_status",
                {
                    "listing_id": "listing-001",
                    "status": "bogus",
                },
                blocking=True,
            )

    async def test_set_calendar_rejects_bad_operation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Invalid operation value is rejected by schema."""
        await _setup_entry(hass)

        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "set_calendar_availability",
                {
                    "listing_id": "listing-001",
                    "start_date": "2025-08-01",
                    "end_date": "2025-08-05",
                    "operation": "delete",
                },
                blocking=True,
            )

    async def test_add_note_missing_required_field(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Missing required field is rejected by schema."""
        await _setup_entry(hass)

        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-001"},
                blocking=True,
            )

    async def test_create_task_missing_required_field(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Missing required listing_id is rejected by schema."""
        await _setup_entry(hass)

        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "create_task",
                {"task_title": "Test"},
                blocking=True,
            )


# ── Add Reservation Note Tests ──────────────────────────────────────


class TestHandleAddReservationNote:
    """Tests for guesty.add_reservation_note handler (T016)."""

    async def test_successful_call(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Successful note addition returns ActionResult dict."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        result = await hass.services.async_call(
            DOMAIN,
            "add_reservation_note",
            {
                "reservation_id": "res-001",
                "note_text": "Test note",
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.add_reservation_note.assert_awaited_once_with(
            reservation_id="res-001",
            note_text="Test note",
            route=ROUTE_V3,
        )
        assert result == _result_dict(target_id="res-001")

    async def test_action_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """GuestyActionError is translated to HomeAssistantError."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.add_reservation_note.side_effect = GuestyActionError(
            "Not found",
            target_id="res-bad",
            action_type="add_reservation_note",
        )

        with pytest.raises(HomeAssistantError, match="Not found"):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {
                    "reservation_id": "res-bad",
                    "note_text": "Test",
                },
                blocking=True,
                return_response=True,
            )

    async def test_api_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """GuestyApiError is translated to HomeAssistantError."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.add_reservation_note.side_effect = GuestyApiError(
            "Connection lost"
        )

        with pytest.raises(
            HomeAssistantError,
            match="Connection lost",
        ):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {
                    "reservation_id": "res-001",
                    "note_text": "Test",
                },
                blocking=True,
                return_response=True,
            )

    async def test_value_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """ValueError is translated to HomeAssistantError."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.add_reservation_note.side_effect = ValueError(
            "note_text must be non-empty"
        )

        with pytest.raises(
            HomeAssistantError,
            match="note_text must be non-empty",
        ):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {
                    "reservation_id": "res-001",
                    "note_text": "x",
                },
                blocking=True,
                return_response=True,
            )


# ── Set Listing Status Tests ────────────────────────────────────────


class TestHandleSetListingStatus:
    """Tests for guesty.set_listing_status handler (T020)."""

    async def test_successful_call(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Successful status change returns ActionResult dict."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        result = await hass.services.async_call(
            DOMAIN,
            "set_listing_status",
            {
                "listing_id": "listing-001",
                "status": "active",
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.set_listing_status.assert_awaited_once_with(
            listing_id="listing-001",
            status="active",
        )
        assert result == _result_dict(target_id="listing-001")

    async def test_inactive_status(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Setting inactive status delegates correctly."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        await hass.services.async_call(
            DOMAIN,
            "set_listing_status",
            {
                "listing_id": "listing-001",
                "status": "inactive",
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.set_listing_status.assert_awaited_once_with(
            listing_id="listing-001",
            status="inactive",
        )

    async def test_action_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """GuestyActionError is translated to HomeAssistantError."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.set_listing_status.side_effect = GuestyActionError(
            "Not found"
        )

        with pytest.raises(HomeAssistantError, match="Not found"):
            await hass.services.async_call(
                DOMAIN,
                "set_listing_status",
                {
                    "listing_id": "bad-id",
                    "status": "active",
                },
                blocking=True,
                return_response=True,
            )


# ── Create Task Tests ───────────────────────────────────────────────


class TestHandleCreateTask:
    """Tests for guesty.create_task handler (T024)."""

    async def test_successful_with_all_fields(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Task creation with all fields returns ActionResult."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        result = await hass.services.async_call(
            DOMAIN,
            "create_task",
            {
                "listing_id": "listing-001",
                "task_title": "Clean unit",
                "description": "Deep clean",
                "assignee": "user-001",
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.create_task.assert_awaited_once_with(
            listing_id="listing-001",
            task_title="Clean unit",
            description="Deep clean",
            assignee="user-001",
        )
        assert result == _result_dict(target_id="task-001")

    async def test_successful_required_only(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Task creation with required fields only."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        await hass.services.async_call(
            DOMAIN,
            "create_task",
            {
                "listing_id": "listing-001",
                "task_title": "Fix AC",
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.create_task.assert_awaited_once_with(
            listing_id="listing-001",
            task_title="Fix AC",
            description=None,
            assignee=None,
        )

    async def test_action_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """GuestyActionError is translated to HomeAssistantError."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.create_task.side_effect = GuestyActionError(
            "Failed to create"
        )

        with pytest.raises(
            HomeAssistantError,
            match="Failed to create",
        ):
            await hass.services.async_call(
                DOMAIN,
                "create_task",
                {
                    "listing_id": "listing-001",
                    "task_title": "Test",
                },
                blocking=True,
                return_response=True,
            )


# ── Set Calendar Availability Tests ─────────────────────────────────


class TestHandleSetCalendarAvailability:
    """Tests for guesty.set_calendar_availability handler (T028)."""

    async def test_successful_block(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Blocking a date range returns ActionResult dict."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        result = await hass.services.async_call(
            DOMAIN,
            "set_calendar_availability",
            {
                "listing_id": "listing-001",
                "start_date": "2025-08-01",
                "end_date": "2025-08-05",
                "operation": "block",
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.set_calendar_availability.assert_awaited_once_with(
            listing_id="listing-001",
            start_date="2025-08-01",
            end_date="2025-08-05",
            operation="block",
        )
        assert result == _result_dict(target_id="listing-001")

    async def test_successful_unblock(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Unblocking a date range delegates correctly."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        await hass.services.async_call(
            DOMAIN,
            "set_calendar_availability",
            {
                "listing_id": "listing-001",
                "start_date": "2025-09-01",
                "end_date": "2025-09-05",
                "operation": "unblock",
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.set_calendar_availability.assert_awaited_once_with(
            listing_id="listing-001",
            start_date="2025-09-01",
            end_date="2025-09-05",
            operation="unblock",
        )

    async def test_action_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """GuestyActionError is translated to HomeAssistantError."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.set_calendar_availability.side_effect = GuestyActionError(
            "Conflict"
        )

        with pytest.raises(HomeAssistantError, match="Conflict"):
            await hass.services.async_call(
                DOMAIN,
                "set_calendar_availability",
                {
                    "listing_id": "listing-001",
                    "start_date": "2025-08-01",
                    "end_date": "2025-08-05",
                    "operation": "block",
                },
                blocking=True,
                return_response=True,
            )


# ── Result Conversion Tests ─────────────────────────────────────────


class TestResultToDict:
    """Tests for ActionResult to dict conversion."""

    def test_success_result(self) -> None:
        """Successful result converts to dict without error key."""
        from custom_components.guesty.actions import (
            _result_to_dict,
        )

        result = ActionResult(success=True, target_id="res-001")
        assert _result_to_dict(result) == {
            "success": True,
            "target_id": "res-001",
        }

    def test_failure_result(self) -> None:
        """Failed result includes error key."""
        from custom_components.guesty.actions import (
            _result_to_dict,
        )

        result = ActionResult(
            success=False,
            target_id="res-001",
            error="Something failed",
        )
        assert _result_to_dict(result) == {
            "success": False,
            "target_id": "res-001",
            "error": "Something failed",
        }


# ── Config Entry Resolution Tests ───────────────────────────────────


class TestGetActionsClient:
    """Tests for config entry resolution logic."""

    async def test_no_entries_raises(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Raises HomeAssistantError when no entries loaded."""
        from custom_components.guesty.actions import (
            _get_actions_client,
        )

        hass.data[DOMAIN] = {}
        fake_call = AsyncMock()
        fake_call.data = {}

        with pytest.raises(
            HomeAssistantError,
            match="No Guesty config entries loaded",
        ):
            _get_actions_client(hass, fake_call)

    async def test_multiple_entries_no_id_raises(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Raises when multiple entries and no config_entry_id."""
        from custom_components.guesty.actions import (
            _get_actions_client,
        )

        hass.data[DOMAIN] = {
            "entry-1": {"actions_client": mock_actions_client},
            "entry-2": {"actions_client": mock_actions_client},
        }
        fake_call = AsyncMock()
        fake_call.data = {}

        with pytest.raises(
            HomeAssistantError,
            match="Multiple Guesty config entries",
        ):
            _get_actions_client(hass, fake_call)

    async def test_explicit_entry_id_resolves(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Explicit config_entry_id resolves correct client."""
        from custom_components.guesty.actions import (
            _get_actions_client,
        )

        hass.data[DOMAIN] = {
            "entry-1": {"actions_client": mock_actions_client},
        }
        fake_call = AsyncMock()
        fake_call.data = {"config_entry_id": "entry-1"}

        client = _get_actions_client(hass, fake_call)
        assert client is mock_actions_client

    async def test_bad_entry_id_raises(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Raises when config_entry_id not found."""
        from custom_components.guesty.actions import (
            _get_actions_client,
        )

        hass.data[DOMAIN] = {}
        fake_call = AsyncMock()
        fake_call.data = {"config_entry_id": "nonexistent"}

        with pytest.raises(
            HomeAssistantError,
            match="Config entry 'nonexistent' not found",
        ):
            _get_actions_client(hass, fake_call)

    async def test_single_entry_resolves(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Single entry resolves without config_entry_id."""
        from custom_components.guesty.actions import (
            _get_actions_client,
        )

        hass.data[DOMAIN] = {
            "entry-1": {"actions_client": mock_actions_client},
        }
        fake_call = AsyncMock()
        fake_call.data = {}

        client = _get_actions_client(hass, fake_call)
        assert client is mock_actions_client

    async def test_no_domain_data_raises(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Raises when DOMAIN not in hass.data at all."""
        from custom_components.guesty.actions import (
            _get_actions_client,
        )

        hass.data.pop(DOMAIN, None)
        fake_call = AsyncMock()
        fake_call.data = {}

        with pytest.raises(
            HomeAssistantError,
            match="No Guesty config entries loaded",
        ):
            _get_actions_client(hass, fake_call)


# ── Integration / End-to-End Tests (T035) ──────────────────────────


class TestIntegrationEndToEnd:
    """End-to-end tests: HA service → real client → mocked API.

    These tests use the real GuestyActionsClient wired through HA
    service handlers, with respx intercepting HTTP to Guesty.
    """

    async def test_add_note_end_to_end(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Full path: service → handler → client → API."""

        import respx
        from httpx import Response

        from custom_components.guesty.api.const import (
            BASE_URL,
            TOKEN_URL,
        )

        # This is the legacy note write route, which
        # FR-040 keeps byte-identical; the v3 route has
        # its own end-to-end coverage below.
        await _setup_entry(
            hass,
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_LEGACY},
        )

        with respx.mock:
            respx.post(TOKEN_URL).mock(
                return_value=Response(
                    200,
                    json=make_token_response(),
                ),
            )
            res_path = f"{RESERVATIONS_ENDPOINT}/res-e2e"
            respx.get(f"{BASE_URL}{res_path}").mock(
                return_value=Response(
                    200,
                    json={
                        "_id": "res-e2e",
                        "note": "Old note",
                    },
                ),
            )
            put_route = respx.put(
                f"{BASE_URL}{res_path}",
            ).mock(
                return_value=Response(
                    200,
                    json={"_id": "res-e2e"},
                ),
            )

            result = await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {
                    "reservation_id": "res-e2e",
                    "note_text": "New note",
                },
                blocking=True,
                return_response=True,
            )

        assert result == {
            "success": True,
            "target_id": "res-e2e",
        }
        payload = json.loads(
            put_route.calls[0].request.content,
        )
        assert "Old note" in payload["note"]
        assert "New note" in payload["note"]

    async def test_set_listing_status_end_to_end(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Full path: service → handler → client → API."""

        import respx
        from httpx import Response

        from custom_components.guesty.api.const import (
            BASE_URL,
            LISTINGS_ENDPOINT,
            TOKEN_URL,
        )

        await _setup_entry(hass)

        with respx.mock:
            respx.post(TOKEN_URL).mock(
                return_value=Response(
                    200,
                    json=make_token_response(),
                ),
            )
            path = f"{LISTINGS_ENDPOINT}/lst-e2e"
            put_route = respx.put(
                f"{BASE_URL}{path}",
            ).mock(
                return_value=Response(
                    200,
                    json={
                        "_id": "lst-e2e",
                        "active": False,
                    },
                ),
            )

            result = await hass.services.async_call(
                DOMAIN,
                "set_listing_status",
                {
                    "listing_id": "lst-e2e",
                    "status": "inactive",
                },
                blocking=True,
                return_response=True,
            )

        assert result == {
            "success": True,
            "target_id": "lst-e2e",
        }
        payload = json.loads(
            put_route.calls[0].request.content,
        )
        assert payload == {"active": False}

    async def test_create_task_end_to_end(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Full path: service → handler → client → API."""

        import respx
        from httpx import Response

        from custom_components.guesty.api.const import (
            BASE_URL,
            TOKEN_URL,
        )

        await _setup_entry(hass)

        with respx.mock:
            respx.post(TOKEN_URL).mock(
                return_value=Response(
                    200,
                    json=make_token_response(),
                ),
            )
            post_route = respx.post(
                f"{BASE_URL}{TASKS_ENDPOINT}",
            ).mock(
                return_value=Response(
                    201,
                    json={
                        "_id": "task-e2e",
                        "listingId": "lst-e2e",
                        "title": "E2E task",
                    },
                ),
            )

            result = await hass.services.async_call(
                DOMAIN,
                "create_task",
                {
                    "listing_id": "lst-e2e",
                    "task_title": "E2E task",
                    "description": "Full test",
                },
                blocking=True,
                return_response=True,
            )

        assert result == {
            "success": True,
            "target_id": "task-e2e",
        }
        payload = json.loads(
            post_route.calls[0].request.content,
        )
        assert payload["listingId"] == "lst-e2e"
        assert payload["title"] == "E2E task"
        assert payload["description"] == "Full test"

    async def test_set_calendar_end_to_end(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Full path: service → handler → client → API."""

        import respx
        from httpx import Response

        from custom_components.guesty.api.const import (
            BASE_URL,
            TOKEN_URL,
        )

        await _setup_entry(hass)

        with respx.mock:
            respx.post(TOKEN_URL).mock(
                return_value=Response(
                    200,
                    json=make_token_response(),
                ),
            )
            cal_path = CALENDAR_ENDPOINT.format(
                listing_id="lst-e2e",
            )
            put_route = respx.put(
                f"{BASE_URL}{cal_path}",
            ).mock(
                return_value=Response(
                    200,
                    json={"data": {"days": []}},
                ),
            )

            result = await hass.services.async_call(
                DOMAIN,
                "set_calendar_availability",
                {
                    "listing_id": "lst-e2e",
                    "start_date": "2025-08-01",
                    "end_date": "2025-08-05",
                    "operation": "block",
                },
                blocking=True,
                return_response=True,
            )

        assert result == {
            "success": True,
            "target_id": "lst-e2e",
        }
        payload = json.loads(
            put_route.calls[0].request.content,
        )
        assert payload == {
            "dateFrom": "2025-08-01",
            "dateTo": "2025-08-05",
            "status": "unavailable",
        }

    async def test_add_note_e2e_api_failure(
        self,
        hass: HomeAssistant,
    ) -> None:
        """API failure propagates as HomeAssistantError."""
        import respx
        from httpx import Response

        from custom_components.guesty.api.const import (
            BASE_URL,
            TOKEN_URL,
        )

        # This is the legacy note write route, which
        # FR-040 keeps byte-identical; the v3 route has
        # its own end-to-end coverage below.
        await _setup_entry(
            hass,
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_LEGACY},
        )

        with respx.mock:
            respx.post(TOKEN_URL).mock(
                return_value=Response(
                    200,
                    json=make_token_response(),
                ),
            )
            res_path = f"{RESERVATIONS_ENDPOINT}/res-bad"
            respx.get(f"{BASE_URL}{res_path}").mock(
                return_value=Response(
                    422,
                    text="Not Found",
                ),
            )

            with (
                patch(
                    "asyncio.sleep",
                    new_callable=AsyncMock,
                ),
                pytest.raises(
                    HomeAssistantError,
                    match="Failed to fetch",
                ),
            ):
                await hass.services.async_call(
                    DOMAIN,
                    "add_reservation_note",
                    {
                        "reservation_id": "res-bad",
                        "note_text": "Test",
                    },
                    blocking=True,
                    return_response=True,
                )


# ── Additional handler edge case tests ──────────────────────────────


class TestHandlerEdgeCases:
    """Edge case tests for HA service handlers."""

    async def test_set_listing_status_api_error(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """GuestyApiError from set_listing_status translated."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.set_listing_status.side_effect = GuestyApiError(
            "Connection timeout"
        )

        with pytest.raises(
            HomeAssistantError,
            match="Connection timeout",
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_listing_status",
                {
                    "listing_id": "lst-001",
                    "status": "active",
                },
                blocking=True,
                return_response=True,
            )

    async def test_create_task_value_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """ValueError from create_task translated correctly."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.create_task.side_effect = ValueError(
            "task_title exceeds maximum length of 255 characters",
        )

        with pytest.raises(
            HomeAssistantError,
            match="task_title exceeds maximum length",
        ):
            await hass.services.async_call(
                DOMAIN,
                "create_task",
                {
                    "listing_id": "lst-001",
                    "task_title": "x",
                },
                blocking=True,
                return_response=True,
            )

    async def test_set_calendar_value_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """ValueError from calendar op translated correctly."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.set_calendar_availability.side_effect = ValueError(
            "end_date must be >= start_date"
        )

        with pytest.raises(
            HomeAssistantError,
            match="end_date must be >= start_date",
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_calendar_availability",
                {
                    "listing_id": "lst-001",
                    "start_date": "2025-08-05",
                    "end_date": "2025-08-01",
                    "operation": "block",
                },
                blocking=True,
                return_response=True,
            )

    async def test_set_calendar_api_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """GuestyApiError from calendar op translated correctly."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.set_calendar_availability.side_effect = GuestyApiError(
            "Server error"
        )

        with pytest.raises(
            HomeAssistantError,
            match="Server error",
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_calendar_availability",
                {
                    "listing_id": "lst-001",
                    "start_date": "2025-08-01",
                    "end_date": "2025-08-05",
                    "operation": "block",
                },
                blocking=True,
                return_response=True,
            )

    async def test_create_task_api_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """GuestyApiError from create_task translated correctly."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.create_task.side_effect = GuestyApiError(
            "Rate limit exceeded"
        )

        with pytest.raises(
            HomeAssistantError,
            match="Rate limit exceeded",
        ):
            await hass.services.async_call(
                DOMAIN,
                "create_task",
                {
                    "listing_id": "lst-001",
                    "task_title": "Test",
                },
                blocking=True,
                return_response=True,
            )


# ── End-to-End Integration Tests (T035) ─────────────────────────────


@pytest.fixture
async def real_actions_client() -> AsyncGenerator[GuestyActionsClient]:
    """Yield a real GuestyActionsClient for e2e tests.

    Creates a real httpx.AsyncClient and properly closes it
    after the test completes.

    Yields:
        A GuestyActionsClient backed by test fakes and a real
        httpx.AsyncClient (intercepted by respx in tests).
    """
    storage = FakeTokenStorage()
    http = httpx.AsyncClient()
    mgr = GuestyTokenManager(
        client_id="test-client-id",
        client_secret="test-client-secret",
        http_client=http,
        storage=storage,
        refresh_buffer=0,
    )
    api = GuestyApiClient(token_manager=mgr, http_client=http)
    yield GuestyActionsClient(api)
    await http.aclose()


def _mock_token() -> None:
    """Register a respx route for the Guesty token endpoint."""
    respx.post(TOKEN_URL).mock(
        return_value=Response(200, json=make_token_response()),
    )


class TestEndToEndIntegration:
    """End-to-end tests for response structure and error propagation (T035).

    These tests exercise the complete path: HA service call →
    handler → real GuestyActionsClient → real GuestyApiClient →
    respx-mocked HTTP → ActionResult response. Verifies
    automation compatibility (FR-017) and response structure
    (FR-022).
    """

    @respx.mock
    async def test_e2e_response_has_no_error_key_on_success(
        self,
        hass: HomeAssistant,
        real_actions_client: GuestyActionsClient,
    ) -> None:
        """FR-022: success response omits the error key."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = real_actions_client

        _mock_token()
        lst_url = f"{BASE_URL}{LISTINGS_ENDPOINT}/lst-chk"
        respx.put(lst_url).mock(
            return_value=Response(
                200,
                json={"_id": "lst-chk", "active": False},
            ),
        )

        result = await hass.services.async_call(
            DOMAIN,
            "set_listing_status",
            {
                "listing_id": "lst-chk",
                "status": "inactive",
            },
            blocking=True,
            return_response=True,
        )

        assert result is not None
        assert "error" not in result
        assert set(result.keys()) == {"success", "target_id"}

    @respx.mock
    async def test_e2e_error_becomes_ha_error(
        self,
        hass: HomeAssistant,
        real_actions_client: GuestyActionsClient,
    ) -> None:
        """HTTP 422 propagates as HomeAssistantError."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = real_actions_client

        _mock_token()
        lst_url = f"{BASE_URL}{LISTINGS_ENDPOINT}/lst-bad"
        respx.put(lst_url).mock(
            return_value=Response(422, text="Unprocessable"),
        )

        with pytest.raises(
            HomeAssistantError,
            match="Failed to set listing status",
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_listing_status",
                {
                    "listing_id": "lst-bad",
                    "status": "active",
                },
                blocking=True,
                return_response=True,
            )


# ── Set Reservation Status Tests ────────────────────────────────────


class TestHandleSetReservationStatus:
    """Tests for guesty.set_reservation_status handler."""

    async def test_checked_in_success(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Successful checked_in returns ActionResult dict."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        result = await hass.services.async_call(
            DOMAIN,
            "set_reservation_status",
            {
                "reservation_id": "res-001",
                "status": "checked_in",
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.set_reservation_status.assert_awaited_once_with(
            reservation_id="res-001",
            status="checked_in",
            route=ROUTE_V3,
        )
        assert result == _result_dict(target_id="res-001")

    async def test_checked_out_success(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Successful checked_out returns ActionResult dict."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        result = await hass.services.async_call(
            DOMAIN,
            "set_reservation_status",
            {
                "reservation_id": "res-001",
                "status": "checked_out",
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.set_reservation_status.assert_awaited_once_with(
            reservation_id="res-001",
            status="checked_out",
            route=ROUTE_V3,
        )
        assert result == _result_dict(target_id="res-001")

    async def test_action_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """GuestyActionError is translated to HomeAssistantError."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.set_reservation_status.side_effect = GuestyActionError(
            "Not found",
            target_id="res-bad",
            action_type="set_reservation_status",
        )

        with pytest.raises(HomeAssistantError, match="Not found"):
            await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {
                    "reservation_id": "res-bad",
                    "status": "checked_in",
                },
                blocking=True,
                return_response=True,
            )

    async def test_api_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """GuestyApiError is translated to HomeAssistantError."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.set_reservation_status.side_effect = GuestyApiError(
            "Connection lost"
        )

        with pytest.raises(
            HomeAssistantError,
            match="Connection lost",
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {
                    "reservation_id": "res-001",
                    "status": "checked_in",
                },
                blocking=True,
                return_response=True,
            )

    async def test_value_error_translated(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """ValueError is translated to HomeAssistantError."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        mock_actions_client.set_reservation_status.side_effect = ValueError(
            "reservation_id must be non-empty"
        )

        with pytest.raises(
            HomeAssistantError,
            match="reservation_id must be non-empty",
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {
                    "reservation_id": "x",
                    "status": "checked_in",
                },
                blocking=True,
                return_response=True,
            )

    async def test_invalid_status_rejected_by_schema(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Invalid status value is rejected by schema."""
        await _setup_entry(hass)

        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {
                    "reservation_id": "res-001",
                    "status": "confirmed",
                },
                blocking=True,
            )

    async def test_multi_entry_config_entry_id(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Service call with config_entry_id targets correct entry."""
        await _setup_entry(hass)

        entry2 = MockConfigEntry(
            domain=DOMAIN,
            title="Guesty (test2)",
            data={
                CONF_CLIENT_ID: "test-client-id-2",
                CONF_CLIENT_SECRET: "test-client-secret-2",
            },
            unique_id="test-client-id-2",
        )
        entry2.add_to_hass(hass)

        with (
            patch(
                _PATCH_TEST,
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                _PATCH_LISTINGS,
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    make_identity_reservations(*_WRITE_TARGETS)
                ),
            ),
        ):
            await hass.config_entries.async_setup(entry2.entry_id)
            await hass.async_block_till_done()

        hass.data[DOMAIN][entry2.entry_id]["actions_client"] = mock_actions_client

        result = await hass.services.async_call(
            DOMAIN,
            "set_reservation_status",
            {
                "reservation_id": "res-001",
                "status": "checked_in",
                "config_entry_id": entry2.entry_id,
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.set_reservation_status.assert_awaited_once()
        assert result == _result_dict(target_id="res-001")


# ── Identity write predicate (T065) ─────────────────────────────────


class TestIdentityWritePredicate:
    """Reservation writes fail closed on an unsure identity.

    Each conjunct of the FR-031c predicate is asserted on its own,
    at both reservation write entry points, and every refusal must
    send no request at all.
    """

    @staticmethod
    def _client(hass: HomeAssistant, entry: MockConfigEntry) -> AsyncMock:
        """Replace the entry's actions client with a mock.

        Args:
            hass: Home Assistant instance.
            entry: The loaded config entry.

        Returns:
            The mock now wired into the entry.
        """
        client = AsyncMock()
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = client
        return client

    @staticmethod
    async def _expect_refusal(
        hass: HomeAssistant,
        service: str,
        data: dict[str, Any],
        match: str,
    ) -> None:
        """Assert a reservation write is refused.

        Args:
            hass: Home Assistant instance.
            service: The service to call.
            data: The service call payload.
            match: Expected fragment of the refusal.
        """
        with pytest.raises(HomeAssistantError, match=match):
            await hass.services.async_call(
                DOMAIN,
                service,
                data,
                blocking=True,
                return_response=True,
            )

    async def test_no_baseline_refuses_both_entry_points(
        self,
        hass: HomeAssistant,
    ) -> None:
        """An empty refresh leaves no baseline, so writes refuse."""
        entry = await _setup_entry(hass, reservations=[])
        client = self._client(hass, entry)

        with patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch:
            await self._expect_refusal(
                hass,
                "add_reservation_note",
                {"reservation_id": "res-001", "note_text": "hi"},
                "has not established a reservation identity baseline",
            )
            await self._expect_refusal(
                hass,
                "set_reservation_status",
                {"reservation_id": "res-001", "status": "checked_in"},
                "has not established a reservation identity baseline",
            )

        fetch.assert_not_awaited()
        assert client.mock_calls == []

    @pytest.mark.parametrize(
        ("service", "payload"),
        [
            ("add_reservation_note", {"note_text": "hello"}),
            ("set_reservation_status", {"status": "checked_in"}),
        ],
    )
    async def test_blank_target_refuses_without_refresh(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
    ) -> None:
        """Whitespace is not a valid target and must not trigger recovery."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        with patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch:
            await self._expect_refusal(
                hass, service, {"reservation_id": "  ", **payload}, "non-empty"
            )
        fetch.assert_not_awaited()
        assert client.mock_calls == []

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
    async def test_independent_refusal_does_not_refresh(
        self, hass: HomeAssistant, guard: str, message: str
    ) -> None:
        """A non-miss safety refusal cannot trigger an extra fetch."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        baseline = coordinator.identity.baseline
        assert baseline is not None
        if guard == "unusable":
            baseline.codes.clear()
        elif guard == "unstored":
            coordinator.identity._unstored = True
        elif guard == "unconfirmed":
            baseline.continuity = CONTINUITY_UNCONFIRMED
        elif guard == "ambiguous":
            coordinator.identity.mark_ambiguous()
        else:
            coordinator.fallback_sourced_ids = frozenset({"res-new"})
        with patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch:
            await self._expect_refusal(
                hass,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                message,
            )
        fetch.assert_not_awaited()
        assert client.mock_calls == []

    async def test_changed_source_before_miss_does_not_refresh(
        self, hass: HomeAssistant
    ) -> None:
        """A not-yet-reloaded option cannot use the old coordinator source."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        hass.config_entries.async_update_entry(
            entry, options={CONF_RESERVATIONS_API: RESERVATIONS_API_LEGACY}
        )
        write = AsyncMock()
        with (
            patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch,
            pytest.raises(GuestyActionError, match="source"),
        ):
            await execute_reservation_write(
                entry_data,
                "res-new",
                operation="status",
                action_type="set_reservation_status",
                write=write,
            )
        fetch.assert_not_awaited()
        write.assert_not_awaited()

    @pytest.mark.parametrize(
        ("key", "changed"),
        [
            (CONF_RESERVATIONS_API, RESERVATIONS_API_LEGACY),
            (CONF_PAST_DAYS, 77),
            (CONF_FUTURE_DAYS, 88),
            (CONF_SELECTED_LISTINGS, ["another-listing"]),
        ],
    )
    async def test_recovery_refuses_when_options_change_and_revert(
        self, hass: HomeAssistant, key: str, changed: object
    ) -> None:
        """Back-to-back HA option updates cannot restore write authorization."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        started = asyncio.Event()
        release = asyncio.Event()
        reservations = [
            *make_identity_reservations(*_WRITE_TARGETS),
            make_identity_reservation("res-new", "CONF-res-new"),
        ]

        async def fetch(*args: object, **kwargs: object) -> object:
            """Pause the fetch while both configuration changes occur."""
            started.set()
            await release.wait()
            return make_fetch_result(reservations)

        with patch(_PATCH_RESERVATIONS, side_effect=fetch) as refresh:
            service = asyncio.create_task(
                hass.services.async_call(
                    DOMAIN,
                    "set_reservation_status",
                    {"reservation_id": "res-new", "status": "checked_in"},
                    blocking=True,
                    return_response=True,
                )
            )
            waiter = asyncio.create_task(started.wait())
            try:
                await asyncio.wait(
                    {service, waiter}, return_when=asyncio.FIRST_COMPLETED
                )
                if started.is_set():
                    options = dict(entry.options)
                    hass.config_entries.async_update_entry(
                        entry, options={**options, key: changed}
                    )
                    hass.config_entries.async_update_entry(entry, options=options)
                release.set()
                with pytest.raises(HomeAssistantError):
                    await service
                assert refresh.await_count == 1
                assert client.mock_calls == []
            finally:
                release.set()
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)

    @pytest.mark.parametrize(
        ("service", "payload", "method"),
        [
            ("add_reservation_note", {"note_text": "hello"}, "add_reservation_note"),
            (
                "set_reservation_status",
                {"status": "checked_in"},
                "set_reservation_status",
            ),
        ],
    )
    async def test_missing_identity_refreshes_before_write(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        method: str,
    ) -> None:
        """A missing identity gets one refresh and then a guarded write."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        getattr(client, method).return_value = ActionResult(
            success=True, target_id="res-new"
        )
        reservations = [
            *make_identity_reservations(*_WRITE_TARGETS),
            make_identity_reservation("res-new", "CONF-res-new"),
        ]
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(reservations),
        ) as fetch:
            result = await hass.services.async_call(
                DOMAIN,
                service,
                {"reservation_id": "res-new", **payload},
                blocking=True,
                return_response=True,
            )
        assert result == {"success": True, "target_id": "res-new"}
        fetch.assert_awaited_once()
        getattr(client, method).assert_awaited_once()

    async def test_codeless_identity_can_be_confirmed_by_refresh(
        self, hass: HomeAssistant
    ) -> None:
        """A previously code-less target becomes writable only after confirmation."""
        entry = await _setup_entry(
            hass,
            reservations=[
                *make_identity_reservations(*_WRITE_TARGETS),
                make_identity_reservation("res-new"),
            ],
        )
        client = self._client(hass, entry)
        client.set_reservation_status.return_value = ActionResult(
            success=True, target_id="res-new"
        )
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                [
                    *make_identity_reservations(*_WRITE_TARGETS),
                    make_identity_reservation("res-new", "CONF-res-new"),
                ]
            ),
        ) as fetch:
            await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                blocking=True,
                return_response=True,
            )
        fetch.assert_awaited_once()
        client.set_reservation_status.assert_awaited_once()

    @pytest.mark.parametrize(
        ("service", "payload", "method"),
        [
            ("add_reservation_note", {"note_text": "hello"}, "add_reservation_note"),
            (
                "set_reservation_status",
                {"status": "checked_in"},
                "set_reservation_status",
            ),
        ],
    )
    async def test_published_codeless_target_refreshes_and_is_classified(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        service: str,
        payload: dict[str, str],
        method: str,
    ) -> None:
        """A published code-less target gets one refresh and a distinct safe log."""
        uncoded = make_identity_reservation("res-new")
        base = make_identity_reservations(*_WRITE_TARGETS)
        entry = await _setup_entry(hass, reservations=[*base, uncoded])
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.data = {"listing-001": [uncoded]}
        client = self._client(hass, entry)
        getattr(client, method).return_value = ActionResult(
            success=True, target_id="res-new"
        )
        coded = make_identity_reservation("res-new", "CONF-res-new")
        with (
            caplog.at_level(logging.DEBUG, logger="custom_components.guesty.actions"),
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result([*base, coded]),
            ) as fetch,
        ):
            result = await hass.services.async_call(
                DOMAIN,
                service,
                {"reservation_id": "res-new", **payload},
                blocking=True,
                return_response=True,
            )
        assert result == {"success": True, "target_id": "res-new"}
        fetch.assert_awaited_once()
        getattr(client, method).assert_awaited_once()
        integration_logs = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name.startswith("custom_components.guesty")
        )
        assert "present-without-identity" in integration_logs
        assert "CONF-res-new" not in integration_logs

    @pytest.mark.parametrize("source", [RESERVATIONS_API_V3, RESERVATIONS_API_LEGACY])
    async def test_recovery_uses_existing_source_and_window(
        self, hass: HomeAssistant, source: str
    ) -> None:
        """Recovery calls the configured coordinator fetch without a target lookup."""
        entry = await _setup_entry(
            hass,
            options={
                CONF_RESERVATIONS_API: source,
                CONF_PAST_DAYS: 13,
                CONF_FUTURE_DAYS: 21,
            },
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        client.set_reservation_status.return_value = ActionResult(
            success=True, target_id="res-new"
        )
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                [
                    *make_identity_reservations(*_WRITE_TARGETS),
                    make_identity_reservation("res-new", "CONF-res-new"),
                ]
            ),
        ) as fetch:
            await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                blocking=True,
                return_response=True,
            )
        assert coordinator.api_client.reservations_api == source
        fetch.assert_awaited_once_with(past_days=13, future_days=21, candidates=ANY)
        client.set_reservation_status.assert_awaited_once()

    @pytest.mark.parametrize("initially_known", [True, False])
    @pytest.mark.parametrize("publication", ["selected", "unselected", "trimmed"])
    async def test_identity_evidence_not_entity_publication_decides_write(
        self, hass: HomeAssistant, initially_known: bool, publication: str
    ) -> None:
        """Selected, unselected, and locally trimmed evidence use the same guard."""
        target = make_identity_reservation("res-new", "CONF-res-new")
        if publication == "unselected":
            target = replace(target, listing_id="listing-unselected")
        elif publication == "trimmed":
            now = datetime.now(UTC)
            target = replace(
                target,
                check_in=now + timedelta(days=1000),
                check_out=now + timedelta(days=1002),
            )
        base = make_identity_reservations(*_WRITE_TARGETS)
        entry = await _setup_entry(
            hass,
            reservations=[*base, target] if initially_known else base,
            options={CONF_SELECTED_LISTINGS: ["listing-001"]},
        )
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        listing = GuestyListing.from_api_dict(
            {"_id": "listing-001", "title": "Selected"}
        )
        assert listing is not None
        coordinator.listings_coordinator.data = {listing.id: listing}
        if initially_known:
            with patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result([*base, target]),
            ):
                await coordinator.async_refresh()
        client = self._client(hass, entry)
        client.set_reservation_status.return_value = ActionResult(
            success=True, target_id="res-new"
        )
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result([*base, target]),
        ) as fetch:
            result = await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                blocking=True,
                return_response=True,
            )
        assert result == {"success": True, "target_id": "res-new"}
        assert fetch.await_count == (0 if initially_known else 1)
        baseline = coordinator.identity.baseline
        assert baseline is not None and "res-new" in baseline.current_ids()
        published = any(
            reservation.id == "res-new"
            for reservations in coordinator.data.values()
            for reservation in reservations
        )
        assert published is (publication == "selected")
        client.set_reservation_status.assert_awaited_once()
        if initially_known:
            assert (
                "single_attempt" not in client.set_reservation_status.await_args.kwargs
            )

    async def test_failed_miss_refresh_never_uses_retained_data(
        self, hass: HomeAssistant
    ) -> None:
        """A failed refresh is a service failure and never sends a write."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            side_effect=GuestyApiError("unavailable"),
        ) as fetch:
            await self._expect_refusal(
                hass,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                "refresh",
            )
        fetch.assert_awaited_once()
        assert client.mock_calls == []

    async def test_failed_miss_refresh_does_not_log_response(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A fetch error cannot leak an echoed sensitive value to logs."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            side_effect=GuestyApiError("server echoed SECRET-PIN"),
        ):
            await self._expect_refusal(
                hass,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                "refresh",
            )
        assert "SECRET-PIN" not in caplog.text
        assert client.mock_calls == []

    async def test_target_still_missing_after_one_refresh(
        self, hass: HomeAssistant
    ) -> None:
        """A successful refresh without the target refuses without a write."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(make_identity_reservations(*_WRITE_TARGETS)),
        ) as fetch:
            await self._expect_refusal(
                hass,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                "has no known confirmation code",
            )
        fetch.assert_awaited_once()
        assert client.mock_calls == []

    async def test_conflicting_refresh_refuses_without_write(
        self, hass: HomeAssistant
    ) -> None:
        """A genuine same-path identity conflict aborts recovery."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        conflicting = [
            make_identity_reservation("res-999", "CONF-res-001"),
            make_identity_reservation("res-new", "CONF-res-new"),
        ]
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(conflicting),
        ) as fetch:
            await self._expect_refusal(
                hass,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                "refresh",
            )
        fetch.assert_awaited_once()
        assert client.mock_calls == []

    async def test_duplicate_code_refresh_refuses_without_write(
        self, hass: HomeAssistant
    ) -> None:
        """A duplicate code in the fresh response aborts recovery."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        duplicates = [
            *make_identity_reservations(*_WRITE_TARGETS),
            make_identity_reservation("res-new", "CONF-duplicate"),
            make_identity_reservation("res-other", "CONF-duplicate"),
        ]
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(duplicates),
        ) as fetch:
            await self._expect_refusal(
                hass,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                "refresh",
            )
        fetch.assert_awaited_once()
        assert client.mock_calls == []

    async def test_cross_path_divergence_refuses_after_refresh(
        self, hass: HomeAssistant
    ) -> None:
        """Cross-generation disagreement prevents an otherwise eligible write."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        baseline = coordinator.identity.baseline
        assert baseline is not None
        baseline.legacy_codes = {"CONF-res-001": "legacy-res-001"}
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                [
                    *make_identity_reservations(*_WRITE_TARGETS),
                    make_identity_reservation("res-new", "CONF-res-new"),
                ]
            ),
        ) as fetch:
            await self._expect_refusal(
                hass,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                "continuity is unconfirmed",
            )
        fetch.assert_awaited_once()
        assert client.mock_calls == []

    async def test_refresh_storage_failure_refuses_without_write(
        self, hass: HomeAssistant
    ) -> None:
        """A fetched target cannot authorize an unstored identity baseline."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        with (
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [
                        *make_identity_reservations(*_WRITE_TARGETS),
                        make_identity_reservation("res-new", "CONF-res-new"),
                    ]
                ),
            ) as fetch,
            patch.object(coordinator.identity, "async_store", side_effect=OSError),
        ):
            await self._expect_refusal(
                hass,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                "could not be written to storage",
            )
        fetch.assert_awaited_once()
        assert client.mock_calls == []

    async def test_note_rechecks_guard_after_prerequisite_read(
        self, hass: HomeAssistant
    ) -> None:
        """A note read cannot authorize a write after the guard changes."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = entry_data["actions_client"]

        async def read_notes(reservation_id: str) -> dict[str, object]:
            """Invalidate identity while a prerequisite read is awaited."""
            coordinator.identity.mark_ambiguous()
            return {}

        with (
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [
                        *make_identity_reservations(*_WRITE_TARGETS),
                        make_identity_reservation("res-new", "CONF-res-new"),
                    ]
                ),
            ),
            patch.object(client, "_read_v3_notes", side_effect=read_notes),
            patch.object(client._api_client, "_request", new_callable=AsyncMock) as put,
        ):
            await self._expect_refusal(
                hass,
                "add_reservation_note",
                {"reservation_id": "res-new", "note_text": "hello"},
                "aborted because a confirmation code",
            )
        put.assert_not_awaited()

    async def test_refresh_waits_through_note_read_and_write(
        self, hass: HomeAssistant
    ) -> None:
        """The note read and write remain under the coordinator dispatch lock."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = entry_data["actions_client"]
        reading = asyncio.Event()
        release = asyncio.Event()

        async def read_notes(reservation_id: str) -> dict[str, object]:
            """Pause the note read until a later refresh is waiting."""
            reading.set()
            await release.wait()
            return {}

        with (
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [
                        *make_identity_reservations(*_WRITE_TARGETS),
                        make_identity_reservation("res-new", "CONF-res-new"),
                    ]
                ),
            ) as fetch,
            patch.object(client, "_read_v3_notes", side_effect=read_notes),
            patch.object(
                client._api_client,
                "_request",
                new_callable=AsyncMock,
                return_value=Response(200, json={}),
            ) as put,
        ):
            action = asyncio.create_task(
                hass.services.async_call(
                    DOMAIN,
                    "add_reservation_note",
                    {"reservation_id": "res-new", "note_text": "hello"},
                    blocking=True,
                    return_response=True,
                )
            )
            await asyncio.wait_for(reading.wait(), 2)
            subsequent = hass.async_create_task(
                coordinator.async_refresh(), "competing refresh", eager_start=True
            )
            assert fetch.await_count == 1
            assert not subsequent.done()
            release.set()
            await asyncio.wait_for(asyncio.gather(action, subsequent), 2)
            assert fetch.await_count == 2
        put.assert_awaited_once()

    async def test_refreshed_fallback_target_uses_legacy_route(
        self, hass: HomeAssistant
    ) -> None:
        """A newly served fallback identity keeps its verified legacy route."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        client.set_reservation_status.return_value = ActionResult(
            success=True, target_id="res-new"
        )
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                [
                    *make_identity_reservations(*_WRITE_TARGETS),
                    make_identity_reservation("res-new", "CONF-res-new"),
                ],
                fallback_served=("res-new",),
                fallback_sourced=("res-new",),
            ),
        ) as fetch:
            await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                blocking=True,
                return_response=True,
            )
        fetch.assert_awaited_once()
        client.set_reservation_status.assert_awaited_once_with(
            reservation_id="res-new",
            status="checked_in",
            route=ROUTE_LEGACY,
            single_attempt=True,
            before_write=ANY,
        )
        assert coordinator.legacy_write_counters.status_succeeded == 1

    async def test_refreshed_note_fallback_uses_legacy_route(
        self, hass: HomeAssistant
    ) -> None:
        """A recovered fallback note uses legacy with single-attempt mode."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        client.add_reservation_note.return_value = ActionResult(
            success=True, target_id="res-new"
        )
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                [
                    *make_identity_reservations(*_WRITE_TARGETS),
                    make_identity_reservation("res-new", "CONF-res-new"),
                ],
                fallback_served=("res-new",),
                fallback_sourced=("res-new",),
            ),
        ) as fetch:
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-new", "note_text": "hello"},
                blocking=True,
                return_response=True,
            )
        fetch.assert_awaited_once()
        client.add_reservation_note.assert_awaited_once_with(
            reservation_id="res-new",
            note_text="hello",
            route=ROUTE_LEGACY,
            single_attempt=True,
            before_write=ANY,
        )

    async def test_concurrent_misses_share_refresh(self, hass: HomeAssistant) -> None:
        """Concurrent actions share one fetch and recheck separate identities."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        client.set_reservation_status.return_value = ActionResult(
            success=True, target_id="res-new"
        )
        started = asyncio.Event()
        release = asyncio.Event()
        joined = asyncio.Event()
        join_count = 0
        refresh = coordinator.async_refresh_for_write_miss

        async def observe_join(generation: int) -> bool:
            """Signal after both actions request the shared refresh."""
            nonlocal join_count
            join_count += 1
            if join_count == 2:
                joined.set()
            return bool(await refresh(generation))

        async def fetch(**kwargs: Any) -> Any:
            """Pause the shared coordinator fetch."""
            started.set()
            await release.wait()
            return make_fetch_result(
                [
                    *make_identity_reservations(*_WRITE_TARGETS),
                    make_identity_reservation("res-new", "CONF-res-new"),
                ]
            )

        async def call() -> Any:
            """Call the status service."""
            return await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                blocking=True,
                return_response=True,
            )

        with (
            patch(_PATCH_RESERVATIONS, side_effect=fetch) as mocked_fetch,
            patch.object(coordinator, "async_refresh_for_write_miss", observe_join),
        ):
            first = asyncio.create_task(call())
            await asyncio.wait_for(started.wait(), 2)
            second = asyncio.create_task(call())
            await asyncio.wait_for(joined.wait(), 2)
            release.set()
            results = await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert len(results) == 2
        mocked_fetch.assert_awaited_once()
        assert client.set_reservation_status.await_count == 2

    async def test_shared_refresh_rechecks_each_target(
        self, hass: HomeAssistant
    ) -> None:
        """A missing second target cannot borrow the first one's authorization."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        client.set_reservation_status.return_value = ActionResult(
            success=True, target_id="res-new"
        )
        started = asyncio.Event()
        release = asyncio.Event()
        joined = asyncio.Event()
        join_count = 0
        refresh = coordinator.async_refresh_for_write_miss

        async def observe_join(generation: int) -> bool:
            """Signal after both actions request the shared refresh."""
            nonlocal join_count
            join_count += 1
            if join_count == 2:
                joined.set()
            return bool(await refresh(generation))

        async def fetch(**kwargs: Any) -> Any:
            """Pause one shared fetch that finds only the first target."""
            started.set()
            await release.wait()
            return make_fetch_result(
                [
                    *make_identity_reservations(*_WRITE_TARGETS),
                    make_identity_reservation("res-new", "CONF-res-new"),
                ]
            )

        async def call(target: str) -> Any:
            """Call the status service for one target."""
            return await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": target, "status": "checked_in"},
                blocking=True,
                return_response=True,
            )

        with (
            patch(_PATCH_RESERVATIONS, side_effect=fetch) as mocked_fetch,
            patch.object(coordinator, "async_refresh_for_write_miss", observe_join),
        ):
            first = asyncio.create_task(call("res-new"))
            await asyncio.wait_for(started.wait(), 2)
            second = asyncio.create_task(call("res-omitted"))
            await asyncio.wait_for(joined.wait(), 2)
            release.set()
            results = await asyncio.wait_for(
                asyncio.gather(first, second, return_exceptions=True), 2
            )
        mocked_fetch.assert_awaited_once()
        assert isinstance(results[1], HomeAssistantError)
        assert client.set_reservation_status.await_count == 1
        assert client.set_reservation_status.await_args.kwargs["reservation_id"] == (
            "res-new"
        )

    async def test_cancelled_miss_does_not_cancel_shared_refresh(
        self, hass: HomeAssistant
    ) -> None:
        """One cancelled service leaves the other miss's fetch intact."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        client.set_reservation_status.return_value = ActionResult(
            success=True, target_id="res-new"
        )
        started = asyncio.Event()
        release = asyncio.Event()
        joined = asyncio.Event()
        join_count = 0
        refresh = coordinator.async_refresh_for_write_miss

        async def observe_join(generation: int) -> bool:
            """Signal after both actions request the shared refresh."""
            nonlocal join_count
            join_count += 1
            if join_count == 2:
                joined.set()
            return bool(await refresh(generation))

        async def fetch(**kwargs: Any) -> Any:
            """Pause the shared fetch while one service is cancelled."""
            started.set()
            await release.wait()
            return make_fetch_result(
                [
                    *make_identity_reservations(*_WRITE_TARGETS),
                    make_identity_reservation("res-new", "CONF-res-new"),
                ]
            )

        async def call() -> Any:
            """Call the status service."""
            return await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                blocking=True,
                return_response=True,
            )

        with (
            patch(_PATCH_RESERVATIONS, side_effect=fetch) as mocked_fetch,
            patch.object(coordinator, "async_refresh_for_write_miss", observe_join),
        ):
            first = asyncio.create_task(call())
            await asyncio.wait_for(started.wait(), 2)
            second = asyncio.create_task(call())
            await asyncio.wait_for(joined.wait(), 2)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            await asyncio.wait_for(second, 2)
        mocked_fetch.assert_awaited_once()
        client.set_reservation_status.assert_awaited_once()

    @pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
    async def test_services_share_preexisting_refresh(
        self, hass: HomeAssistant, outcome: str
    ) -> None:
        """Two misses share a refresh already running, including its failure."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        client.set_reservation_status.return_value = ActionResult(
            success=True, target_id="res-new"
        )
        started = asyncio.Event()
        joined = asyncio.Event()
        release = asyncio.Event()
        join_count = 0
        refresh = coordinator.async_refresh_for_write_miss

        async def fetch(**kwargs: Any) -> Any:
            """Pause the coordinator's already-running fetch."""
            started.set()
            await release.wait()
            if outcome == "failure":
                raise GuestyApiError("unavailable")
            return make_fetch_result(
                [
                    *make_identity_reservations(*_WRITE_TARGETS),
                    make_identity_reservation("res-new", "CONF-res-new"),
                ]
            )

        async def observe_join(generation: int) -> bool:
            """Signal once both service attempts have joined."""
            nonlocal join_count
            join_count += 1
            if join_count == 2:
                joined.set()
            return bool(await refresh(generation))

        async def call() -> Any:
            """Call the same reservation status service."""
            return await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                blocking=True,
                return_response=True,
            )

        with (
            patch(_PATCH_RESERVATIONS, side_effect=fetch) as mocked_fetch,
            patch.object(coordinator, "async_refresh_for_write_miss", observe_join),
        ):
            existing = asyncio.create_task(coordinator.async_refresh())
            await asyncio.wait_for(started.wait(), 2)
            first = asyncio.create_task(call())
            second = asyncio.create_task(call())
            joined_waiter = asyncio.create_task(joined.wait())
            try:
                await asyncio.wait(
                    {joined_waiter, first, second},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not joined.is_set():
                    release.set()
                    await asyncio.gather(
                        existing, first, second, return_exceptions=True
                    )
                    assert join_count == 2
                if outcome == "cancel":
                    existing.cancel()
                release.set()
                results = await asyncio.wait_for(
                    asyncio.gather(existing, first, second, return_exceptions=True), 2
                )
            finally:
                release.set()
                joined_waiter.cancel()
                await asyncio.gather(joined_waiter, return_exceptions=True)
        mocked_fetch.assert_awaited_once()
        if outcome == "success":
            assert all(not isinstance(result, BaseException) for result in results)
            assert client.set_reservation_status.await_count == 2
        else:
            assert all(isinstance(result, HomeAssistantError) for result in results[1:])
            client.set_reservation_status.assert_not_awaited()

    async def test_refresh_waits_for_recovered_write(self, hass: HomeAssistant) -> None:
        """A coordinator refresh cannot change identity during dispatch."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        writing = asyncio.Event()
        release = asyncio.Event()

        async def write(**kwargs: Any) -> ActionResult:
            """Hold the write while a later refresh is requested."""
            writing.set()
            await release.wait()
            return ActionResult(success=True, target_id="res-new")

        client.set_reservation_status.side_effect = write
        reservations = [
            *make_identity_reservations(*_WRITE_TARGETS),
            make_identity_reservation("res-new", "CONF-res-new"),
        ]
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(reservations),
        ) as fetch:
            action = asyncio.create_task(
                hass.services.async_call(
                    DOMAIN,
                    "set_reservation_status",
                    {"reservation_id": "res-new", "status": "checked_in"},
                    blocking=True,
                    return_response=True,
                )
            )
            await asyncio.wait_for(writing.wait(), 2)
            subsequent = hass.async_create_task(
                coordinator.async_refresh(), "competing refresh", eager_start=True
            )
            assert fetch.await_count == 1
            assert not subsequent.done()
            release.set()
            await asyncio.wait_for(asyncio.gather(action, subsequent), 2)
            assert fetch.await_count == 2

    @pytest.mark.parametrize(
        "change",
        [
            "reload",
            "failed",
            "options",
            "options-reverted",
            "removed",
            "client",
            "coordinator",
        ],
    )
    async def test_recovered_write_refuses_superseded_state(
        self, hass: HomeAssistant, change: str
    ) -> None:
        """The accepted fetch cannot authorize a subsequently invalid state."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = self._client(hass, entry)
        original = coordinator.async_refresh_for_write_miss

        async def refresh_then_change(generation: int) -> bool:
            """Change live state after the qualifying refresh."""
            result = await original(generation)
            if change == "reload":
                hass.data[DOMAIN][entry.entry_id] = {}
            elif change == "failed":
                coordinator.last_update_success = False
            elif change == "options":
                hass.config_entries.async_update_entry(entry, options={"changed": True})
            elif change == "options-reverted":
                options = dict(entry.options)
                hass.config_entries.async_update_entry(entry, options={"changed": True})
                hass.config_entries.async_update_entry(entry, options=options)
            elif change == "client":
                entry_data["api_client"] = AsyncMock()
            elif change == "coordinator":
                entry_data["reservations_coordinator"] = AsyncMock()
            elif change == "removed":
                baseline = coordinator.identity.baseline
                assert baseline is not None
                baseline.codes.pop("CONF-res-new")
            return result is True

        with (
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [
                        *make_identity_reservations(*_WRITE_TARGETS),
                        make_identity_reservation("res-new", "CONF-res-new"),
                    ]
                ),
            ) as fetch,
            patch.object(
                coordinator, "async_refresh_for_write_miss", refresh_then_change
            ),
            pytest.raises(HomeAssistantError),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                blocking=True,
                return_response=True,
            )
        if change == "reload":
            hass.data[DOMAIN][entry.entry_id] = entry_data
        fetch.assert_awaited_once()
        assert client.mock_calls == []

    async def test_recovered_write_error_hides_response_body(
        self, hass: HomeAssistant
    ) -> None:
        """A response echo does not leak through the service error."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        client.set_reservation_status.side_effect = GuestyActionError(
            "response contains SECRET-PIN",
            target_id="res-new",
            action_type="set_reservation_status",
        )
        with (
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [
                        *make_identity_reservations(*_WRITE_TARGETS),
                        make_identity_reservation("res-new", "CONF-res-new"),
                    ]
                ),
            ),
            pytest.raises(HomeAssistantError) as error,
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "checked_in"},
                blocking=True,
                return_response=True,
            )
        assert "SECRET-PIN" not in str(error.value)

    async def test_unconfirmed_continuity_refuses_writes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A v3 baseline without cross-path evidence refuses."""
        entry = await _setup_entry(
            hass,
            upgraded=True,
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
        )
        client = self._client(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        baseline = coordinator.identity.baseline

        assert baseline is not None
        assert baseline.continuity == CONTINUITY_UNCONFIRMED
        await self._expect_refusal(
            hass,
            "add_reservation_note",
            {"reservation_id": "res-001", "note_text": "hi"},
            "continuity is unconfirmed",
        )
        assert client.mock_calls == []

    async def test_ambiguous_last_refresh_refuses_writes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """An aborted refresh blocks writes until it is resolved."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]

        rekeyed = [make_identity_reservation("res-999", "CONF-res-001")]
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(rekeyed),
        ):
            await coordinator.async_refresh()

        assert not coordinator.last_update_success
        await self._expect_refusal(
            hass,
            "set_reservation_status",
            {"reservation_id": "res-001", "status": "checked_in"},
            "aborted because a confirmation code",
        )
        assert client.mock_calls == []

    @pytest.mark.parametrize(
        ("service", "payload"),
        [
            ("add_reservation_note", {"note_text": "hi"}),
            ("set_reservation_status", {"status": "checked_in"}),
        ],
    )
    async def test_target_without_confirmation_code_refuses(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
    ) -> None:
        """FR-033: a codeless reservation is not a write target."""
        entry = await _setup_entry(
            hass,
            reservations=[
                *make_identity_reservations(*_WRITE_TARGETS),
                make_identity_reservation("res-nocode"),
            ],
        )
        client = self._client(hass, entry)

        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                [
                    *make_identity_reservations(*_WRITE_TARGETS),
                    make_identity_reservation("res-nocode"),
                ]
            ),
        ) as fetch:
            await self._expect_refusal(
                hass,
                service,
                {"reservation_id": "res-nocode", **payload},
                "has no known confirmation code",
            )
        fetch.assert_awaited_once()
        assert client.mock_calls == []

    async def test_permitted_write_still_reaches_the_client(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A confirmed identity leaves the write path unchanged."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        client.add_reservation_note = AsyncMock(
            return_value=ActionResult(success=True, target_id="res-001"),
        )

        with patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch:
            result = await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-001", "note_text": "hi"},
                blocking=True,
                return_response=True,
            )

        fetch.assert_not_awaited()
        assert result == {"success": True, "target_id": "res-001"}
        client.add_reservation_note.assert_awaited_once()
        assert "single_attempt" not in client.add_reservation_note.await_args.kwargs

    async def test_predicate_is_re_evaluated_after_a_reload(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The refusal is read back from the Store on reload."""
        entry = await _setup_entry(
            hass,
            upgraded=True,
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
        )

        with (
            patch(_PATCH_TEST, new_callable=AsyncMock, return_value=True),
            patch(_PATCH_LISTINGS, new_callable=AsyncMock, return_value=[]),
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    make_identity_reservations(*_WRITE_TARGETS)
                ),
            ),
        ):
            await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()

        client = self._client(hass, entry)
        await self._expect_refusal(
            hass,
            "add_reservation_note",
            {"reservation_id": "res-001", "note_text": "hi"},
            "continuity is unconfirmed",
        )
        assert client.mock_calls == []


class TestWriteRouteSelection:
    """Reservation writes pick a resource generation (T122).

    Phase 6a only selects the route and refuses a target neither
    resource can be shown to hold; the legacy requests themselves
    land in Phase 7 (FR-058, FR-059).
    """

    @staticmethod
    def _coordinator(
        hass: HomeAssistant,
        entry: MockConfigEntry,
        *,
        served: frozenset[str] = frozenset(),
        sourced: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Put a fallback state on the entry's coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The loaded config entry.
            served: Identifiers a legacy lookup has validated.
            sourced: Identifiers the fallback published.

        Returns:
            The entry's hass.data mapping.
        """
        entry_data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        coordinator.fallback_served_ids = served
        coordinator.fallback_sourced_ids = sourced | served
        return entry_data

    async def test_fallback_served_target_selects_legacy(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A reservation only legacy holds routes to legacy."""
        entry = await _setup_entry(hass)
        entry_data = self._coordinator(
            hass,
            entry,
            served=frozenset({"res-001"}),
        )

        assert (
            select_reservation_write_route(
                entry_data,
                "res-001",
                "add_reservation_note",
            )
            == ROUTE_LEGACY
        )

    async def test_every_other_target_selects_v3(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A v3-sourced target can never select legacy."""
        entry = await _setup_entry(hass)
        entry_data = self._coordinator(
            hass,
            entry,
            served=frozenset({"res-001"}),
        )

        assert (
            select_reservation_write_route(
                entry_data,
                "res-002",
                "add_reservation_note",
            )
            == ROUTE_V3
        )

    async def test_selection_reads_the_served_set_at_write_time(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A target that reappeared in v3 selects v3 again."""
        entry = await _setup_entry(hass)
        entry_data = self._coordinator(
            hass,
            entry,
            served=frozenset({"res-001"}),
        )
        assert (
            select_reservation_write_route(
                entry_data,
                "res-001",
                "add_reservation_note",
            )
            == ROUTE_LEGACY
        )

        self._coordinator(hass, entry)

        assert (
            select_reservation_write_route(
                entry_data,
                "res-001",
                "add_reservation_note",
            )
            == ROUTE_V3
        )

    async def test_sourced_but_never_served_target_is_refused(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Retained values alone validate no identifier."""
        entry = await _setup_entry(hass)
        entry_data = self._coordinator(
            hass,
            entry,
            sourced=frozenset({"res-001"}),
        )

        with pytest.raises(GuestyActionError, match="no write is sent"):
            select_reservation_write_route(
                entry_data,
                "res-001",
                "add_reservation_note",
            )

    async def test_served_target_survives_a_failed_recheck(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A validated identifier stays validated."""
        entry = await _setup_entry(hass)
        entry_data = self._coordinator(
            hass,
            entry,
            served=frozenset({"res-001"}),
            sourced=frozenset({"res-001"}),
        )

        assert (
            select_reservation_write_route(
                entry_data,
                "res-001",
                "add_reservation_note",
            )
            == ROUTE_LEGACY
        )

    async def test_refusal_reaches_both_reservation_services(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Neither write entry point sends a request."""
        entry = await _setup_entry(hass)
        self._coordinator(hass, entry, sourced=frozenset({"res-001"}))
        client = AsyncMock()
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = client

        for service, payload in (
            (
                "add_reservation_note",
                {"reservation_id": "res-001", "note_text": "hi"},
            ),
            (
                "set_reservation_status",
                {"reservation_id": "res-001", "status": "checked_in"},
            ),
        ):
            with pytest.raises(
                HomeAssistantError,
                match="no write is sent",
            ):
                await hass.services.async_call(
                    DOMAIN,
                    service,
                    payload,
                    blocking=True,
                    return_response=True,
                )

        assert client.mock_calls == []

    async def test_caller_input_cannot_choose_the_route(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A route key in the payload is rejected by the schema."""
        entry = await _setup_entry(hass)
        self._coordinator(hass, entry, served=frozenset({"res-001"}))

        with pytest.raises(vol.Invalid):
            _SCHEMA_ADD_NOTE(
                {
                    "reservation_id": "res-001",
                    "note_text": "hi",
                    "route": ROUTE_LEGACY,
                },
            )


# ── Phase 7: routing disabled and counters (T130, T131) ────────────


class TestLegacyWriteRoutingDisabled:
    """A retired legacy generation refuses, never writes (T130)."""

    @staticmethod
    def _retire(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
        """Serve res-001 from the fallback, then retire legacy.

        Args:
            hass: Home Assistant instance.
            entry: The loaded config entry.

        Returns:
            The entry's hass.data mapping.
        """
        entry_data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        coordinator.fallback_served_ids = frozenset({"res-001"})
        coordinator.fallback_sourced_ids = frozenset({"res-001"})
        api_client = entry_data["api_client"]
        patcher = patch.object(
            type(api_client),
            "legacy_write_routing_available",
            property(lambda _self: False),
        )
        patcher.start()
        return entry_data

    async def test_selection_refuses_and_names_the_date(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The refusal names the retirement date, not a route."""
        entry = await _setup_entry(hass)
        entry_data = self._retire(hass, entry)

        with pytest.raises(GuestyActionError) as exc_info:
            select_reservation_write_route(
                entry_data,
                "res-001",
                "add_reservation_note",
            )

        assert "2027-03-01" in str(exc_info.value)
        patch.stopall()

    async def test_no_request_reaches_either_generation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """All three operations refuse without writing."""
        entry = await _setup_entry(hass)
        entry_data = self._retire(hass, entry)
        client = AsyncMock()
        entry_data["actions_client"] = client
        cf_client = AsyncMock()
        entry_data["cf_client"] = cf_client

        for service, payload in (
            (
                "add_reservation_note",
                {"reservation_id": "res-001", "note_text": "hi"},
            ),
            (
                "set_reservation_status",
                {"reservation_id": "res-001", "status": "checked_in"},
            ),
        ):
            with pytest.raises(HomeAssistantError, match="2027-03-01"):
                await hass.services.async_call(
                    DOMAIN,
                    service,
                    payload,
                    blocking=True,
                    return_response=True,
                )

        assert client.mock_calls == []
        assert cf_client.mock_calls == []
        patch.stopall()

    async def test_the_refusal_is_sticky_for_the_session(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A second attempt is refused exactly the same way."""
        entry = await _setup_entry(hass)
        entry_data = self._retire(hass, entry)

        for _ in range(2):
            with pytest.raises(GuestyActionError, match="2027-03-01"):
                select_reservation_write_route(
                    entry_data,
                    "res-001",
                    "set_reservation_status",
                )
        patch.stopall()


class TestLegacyWriteCounters:
    """Routed writes are counted and warned about once (T131)."""

    @staticmethod
    def _serve(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
        """Serve res-001 from the fallback.

        Args:
            hass: Home Assistant instance.
            entry: The loaded config entry.

        Returns:
            The entry's hass.data mapping.
        """
        entry_data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        coordinator.fallback_served_ids = frozenset({"res-001"})
        coordinator.fallback_sourced_ids = frozenset({"res-001"})
        return entry_data

    async def test_routed_note_counts_and_warns_once(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """One WARNING names the operation and the identifier."""
        entry = await _setup_entry(hass)
        entry_data = self._serve(hass, entry)
        client = AsyncMock()
        client.add_reservation_note.return_value = ActionResult(
            success=True,
            target_id="res-001",
        )
        entry_data["actions_client"] = client

        with caplog.at_level(logging.WARNING):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-001", "note_text": "hi"},
                blocking=True,
                return_response=True,
            )

        warnings = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING
            and "not-found fallback" in record.getMessage()
        ]
        assert len(warnings) == 1
        assert "res-001" in warnings[0].getMessage()
        assert "add_reservation_note" in warnings[0].getMessage()
        assert client.add_reservation_note.await_args.kwargs["route"] == ROUTE_LEGACY

        coordinator = entry_data["reservations_coordinator"]
        counters = coordinator.legacy_write_counters
        assert counters.note_succeeded == 1
        assert counters.note_failed == 0
        assert counters.note_refused == 0

    async def test_a_failed_routed_write_counts_as_failed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A rejected routed write is counted, not swallowed."""
        entry = await _setup_entry(hass)
        entry_data = self._serve(hass, entry)
        client = AsyncMock()
        client.set_reservation_status.side_effect = GuestyActionError(
            "Failed to set reservation status via /reservations/res-001",
            target_id="res-001",
            action_type="set_reservation_status",
        )
        entry_data["actions_client"] = client

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": "res-001", "status": "checked_in"},
                blocking=True,
                return_response=True,
            )

        counters = entry_data["reservations_coordinator"].legacy_write_counters
        assert counters.status_failed == 1
        assert counters.status_succeeded == 0

    async def test_a_refused_write_counts_as_refused(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A route refusal is counted against the operation."""
        entry = await _setup_entry(hass)
        entry_data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        coordinator.fallback_sourced_ids = frozenset({"res-001"})
        coordinator.fallback_served_ids = frozenset()
        client = AsyncMock()
        entry_data["actions_client"] = client

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-001", "note_text": "hi"},
                blocking=True,
                return_response=True,
            )

        assert client.mock_calls == []
        assert coordinator.legacy_write_counters.note_refused == 1

    async def test_a_v3_write_is_not_counted_or_warned(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Routine v3 writes stay silent and uncounted."""
        entry = await _setup_entry(hass)
        entry_data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
        client = AsyncMock()
        client.add_reservation_note.return_value = ActionResult(
            success=True,
            target_id="res-001",
        )
        entry_data["actions_client"] = client

        with caplog.at_level(logging.WARNING):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-001", "note_text": "hi"},
                blocking=True,
                return_response=True,
            )

        assert client.add_reservation_note.await_args.kwargs["route"] == ROUTE_V3
        coordinator = entry_data["reservations_coordinator"]
        assert coordinator.legacy_write_counters.total == 0
        assert not [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING
            and "not-found fallback" in record.getMessage()
        ]


class TestIdentityGuardPrecedesRouting:
    """Identity is settled before a route exists (T132)."""

    async def test_unconfirmed_identity_refuses_a_served_target(
        self,
        hass: HomeAssistant,
    ) -> None:
        """No route is chosen and no legacy count is taken."""
        entry = await _setup_entry(hass)
        entry_data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        baseline = coordinator.identity.baseline
        assert baseline is not None
        baseline.continuity = CONTINUITY_UNCONFIRMED
        coordinator.fallback_served_ids = frozenset({"res-unknown"})
        coordinator.fallback_sourced_ids = frozenset({"res-unknown"})
        client = AsyncMock()
        entry_data["actions_client"] = client

        with (
            patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch,
            pytest.raises(HomeAssistantError) as exc_info,
        ):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-unknown", "note_text": "hi"},
                blocking=True,
                return_response=True,
            )

        assert "2027-03-01" not in str(exc_info.value)
        fetch.assert_not_awaited()
        assert client.mock_calls == []
        assert coordinator.legacy_write_counters.total == 0


class TestRetirementSurvivesAnIdentityRefusal:
    """A retired target still explains itself (T130, T131)."""

    async def test_refusal_adds_the_retirement_explanation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A refresh between self-disable and the write is covered."""
        entry = await _setup_entry(hass)
        entry_data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        # The identity evidence for this reservation is gone: only
        # the retired legacy resource ever held it.
        coordinator.fallback_served_ids = frozenset({"res-unknown"})
        coordinator.fallback_sourced_ids = frozenset({"res-unknown"})
        client = AsyncMock()
        entry_data["actions_client"] = client
        api_client = entry_data["api_client"]

        with (
            patch.object(
                type(api_client),
                "legacy_write_routing_available",
                property(lambda _self: False),
            ),
            pytest.raises(HomeAssistantError) as exc_info,
        ):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-unknown", "note_text": "hi"},
                blocking=True,
                return_response=True,
            )

        assert "2027-03-01" in str(exc_info.value)
        assert client.mock_calls == []
        assert coordinator.legacy_write_counters.note_refused == 1

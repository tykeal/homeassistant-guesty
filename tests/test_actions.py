# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty HA action service handlers."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import traceback
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager, suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any
from unittest.mock import ANY, AsyncMock, PropertyMock, patch

import httpx
import pytest
import respx
import voluptuous as vol
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from httpx import Response
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.actions import (
    _SCHEMA_ADD_NOTE,
    REFUSAL_NOT_REACHABLE,
    ROUTE_LEGACY,
    ROUTE_V3,
    ConflictEvidence,
    ReservationByIdOutcomeError,
    ReservationIdentityMissError,
    UnavailableConflictEvidence,
    _authorize_by_id_record,
    _capture_recovery_witness,
    _check_recovery_context,
    _check_recovery_witness,
    _conflict_evidence,
    _execute_recovered_reservation_write,
    execute_reservation_write,
    guard_reservation_write,
    select_reservation_write_route,
)
from custom_components.guesty.api.actions import GuestyActionsClient
from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    BASE_URL,
    CALENDAR_ENDPOINT,
    LEGACY_WRITE_NOTE,
    LEGACY_WRITE_STATUS,
    LISTINGS_ENDPOINT,
    MAX_NOTE_LENGTH,
    NOTE_SEPARATOR,
    RESERVATIONS_ENDPOINT,
    RESERVATIONS_PAGE_SIZE,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    TASKS_ENDPOINT,
    TOKEN_URL,
)
from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyApiError,
    GuestyAuthError,
    GuestyConnectionError,
    GuestyRateLimitError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import (
    ActionResult,
    GuestyListing,
    GuestyReservation,
    RejectedCode,
    ReservationFetchResult,
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
from custom_components.guesty.coordinator import (
    AcceptedRefreshSnapshot,
    SnapshotRecord,
)
from custom_components.guesty.identity import (
    CONTINUITY_UNCONFIRMED,
    CONTINUITY_VERIFIED,
    EVIDENCE_COMPROMISED,
    EVIDENCE_READY,
    EVIDENCE_REVALIDATING,
    EVIDENCE_SATURATED,
    EVIDENCE_UNLOADED,
    EVIDENCE_UNSTORED,
    REFUSAL_AMBIGUOUS,
    REFUSAL_EVIDENCE_REPAIR,
    REFUSAL_EVIDENCE_UNAVAILABLE,
    REFUSAL_NO_BASELINE,
    REFUSAL_NO_CODE,
    REFUSAL_REVALIDATING,
    REFUSAL_UNCONFIRMED,
    REFUSAL_UNSTORED,
    ConflictOnlyEvidence,
)
from tests.conftest import (
    BY_ID_TARGET,
    FakeConflictEvidence,
    FakeTokenStorage,
    by_id_network,
    capture_by_id_retention,
    make_by_id_record,
    make_fetch_result,
    make_identity_reservation,
    make_identity_reservations,
    make_token_response,
    make_v3_reservation,
    make_v3_search_page,
    make_v3_segment,
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

    @pytest.mark.parametrize("joined", [False, True])
    async def test_real_refresh_proxy_logs_are_redacted(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        socket_enabled: None,
        joined: bool,
    ) -> None:
        """Owned and joined search failures hide real HTTPcore proxy details."""
        secret = "private-refresh-proxy-sentinel"
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = coordinator.api_client
        started = asyncio.Event()
        release = asyncio.Event()
        waiter_joined = asyncio.Event()
        requests: list[bytes] = []
        original_miss = coordinator.async_refresh_for_write_miss

        async def reject_proxy(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            """Pause one real HTTPS proxy connection until the service joins."""
            try:
                requests.append(await reader.readuntil(b"\r\n\r\n"))
                started.set()
                await release.wait()
                writer.write(
                    f"HTTP/1.1 407 {secret}\r\nContent-Length: 0\r\n\r\n".encode()
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async def observe_miss(generation: int) -> bool:
            """Signal when the service starts its refresh wait."""
            waiter_joined.set()
            return bool(await original_miss(generation))

        server = await asyncio.start_server(reject_proxy, "127.0.0.1", 0)
        assert server.sockets is not None
        port = server.sockets[0].getsockname()[1]
        existing: asyncio.Task[None] | None = None
        service: asyncio.Task[Any] | None = None
        try:
            async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}") as http:
                with (
                    caplog.at_level(logging.DEBUG),
                    patch.object(client, "_http", http),
                    patch.object(
                        client._token_manager, "get_token", return_value="token"
                    ),
                    patch.object(
                        coordinator, "async_refresh_for_write_miss", observe_miss
                    ),
                    patch.object(http, "request", wraps=http.request) as request,
                ):
                    if joined:
                        existing = asyncio.create_task(coordinator.async_refresh())
                        await asyncio.wait_for(started.wait(), 2)
                    service = asyncio.create_task(
                        hass.services.async_call(
                            DOMAIN,
                            "set_reservation_status",
                            {"reservation_id": "res-new", "status": "checked_in"},
                            blocking=True,
                            return_response=True,
                        )
                    )
                    await asyncio.wait_for(started.wait(), 2)
                    await asyncio.wait_for(waiter_joined.wait(), 2)
                    release.set()
                    with pytest.raises(HomeAssistantError) as raised:
                        await asyncio.wait_for(service, 2)
                    if existing is not None:
                        await asyncio.wait_for(existing, 2)
                    assert secret not in caplog.text
                    assert secret not in "".join(
                        traceback.format_exception(raised.value)
                    )
                    assert len(requests) == 1
                    assert [call.args[0] for call in request.await_args_list] == ["GET"]
        finally:
            release.set()
            if service is not None:
                await asyncio.gather(service, return_exceptions=True)
            if existing is not None:
                await asyncio.gather(existing, return_exceptions=True)
            server.close()
            await server.wait_closed()

    @pytest.mark.parametrize(
        "change", [None, "options", "settings", "liveness", "outcome"]
    )
    async def test_recovery_context_check_excludes_identity_and_route(
        self, hass: HomeAssistant, change: str | None
    ) -> None:
        """The context check refuses context changes but not an identity miss."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        witness = _capture_recovery_witness(entry_data)
        generation = coordinator.write_miss_generation
        reservations = make_identity_reservations(*_WRITE_TARGETS)
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(reservations),
        ):
            assert await coordinator.async_refresh_for_write_miss(generation)
        action = "set_reservation_status"
        with pytest.raises(GuestyActionError):
            _check_recovery_witness(witness, "res-new", action)

        with ExitStack() as stack:
            if change == "options":
                hass.config_entries.async_update_entry(
                    entry, options={**entry.options, "unrelated_option": True}
                )
            elif change == "settings":
                stack.enter_context(
                    patch.object(
                        coordinator, "_write_miss_settings", return_value=("other",)
                    )
                )
            elif change == "liveness":
                hass.data[DOMAIN][entry.entry_id] = {}
            elif change == "outcome":
                coordinator.last_update_success = False

            if change is None:
                _check_recovery_context(witness, "res-new", action)
            else:
                with pytest.raises(GuestyActionError) as raised:
                    _check_recovery_context(witness, "res-new", action)
                assert not isinstance(raised.value, ReservationIdentityMissError)

    @pytest.mark.parametrize("scenario", ["options-replaced", "retryable-response"])
    async def test_recovered_executor_rechecks_witness_and_is_one_shot(
        self, hass: HomeAssistant, scenario: str
    ) -> None:
        """A recovered executor rechecks witnesses and never repeats the PUT."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = entry_data["actions_client"]
        witness = _capture_recovery_witness(entry_data)
        generation = coordinator.write_miss_generation
        reservations = make_identity_reservations(*_WRITE_TARGETS, "res-new")
        options = entry.options
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(reservations),
        ) as fetch:
            assert await coordinator.async_refresh_for_write_miss(generation)
        fetch.assert_awaited_once()
        assert entry.options is options
        changed = False

        async def token() -> str:
            """Change a captured source witness while the write awaits a token."""
            nonlocal changed
            if scenario == "options-replaced":
                hass.config_entries.async_update_entry(
                    entry, options={**entry.options, "unrelated_option": True}
                )
                changed = entry.options is not options
            return "test-token"

        response = Response(200 if scenario == "options-replaced" else 500)
        with (
            patch.object(client._api_client._token_manager, "get_token", token),
            patch.object(
                client._api_client._http,
                "request",
                new_callable=AsyncMock,
                return_value=response,
            ) as request,
            patch("asyncio.sleep", new_callable=AsyncMock),
            suppress(GuestyApiError),
        ):
            await _execute_recovered_reservation_write(
                witness,
                "res-new",
                operation=LEGACY_WRITE_STATUS,
                action_type="set_reservation_status",
                write=lambda route, single, check, notes=None: (
                    client.set_reservation_status(
                        "res-new",
                        "checked_in",
                        route=route,
                        single_attempt=single,
                        before_write=check,
                    )
                ),
            )
        if scenario == "options-replaced":
            assert changed
            assert request.await_count == 0
        else:
            assert request.await_count == 1

    async def test_recovered_executor_note_lock_cancellation(
        self, hass: HomeAssistant
    ) -> None:
        """Cancellation releases the recovery lock without sending the note."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = entry_data["actions_client"]
        witness = _capture_recovery_witness(entry_data)
        generation = coordinator.write_miss_generation
        reservations = make_identity_reservations(*_WRITE_TARGETS, "res-new")
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(reservations),
        ):
            assert await coordinator.async_refresh_for_write_miss(generation)

        reading = asyncio.Event()
        release = asyncio.Event()
        update_entered = asyncio.Event()

        async def read_notes(reservation_id: str) -> dict[str, object]:
            """Pause the real note operation at its prerequisite read."""
            reading.set()
            await release.wait()
            return {}

        async def update() -> dict[str, list[GuestyReservation]]:
            """Record when the competing refresh enters its fetch."""
            update_entered.set()
            return {}

        with (
            patch.object(client, "_read_v3_notes", side_effect=read_notes),
            patch.object(client._api_client, "_request", new_callable=AsyncMock) as put,
            patch.object(coordinator, "_async_update_data", side_effect=update),
        ):
            action = asyncio.create_task(
                _execute_recovered_reservation_write(
                    witness,
                    "res-new",
                    operation=LEGACY_WRITE_NOTE,
                    action_type="add_reservation_note",
                    write=lambda route, single, check, notes=None: (
                        client.add_reservation_note(
                            "res-new",
                            "hello",
                            route=route,
                            single_attempt=single,
                            before_write=check,
                        )
                    ),
                )
            )
            refresh: asyncio.Task[Any] | None = None
            try:
                await asyncio.wait_for(reading.wait(), 2)
                refresh = hass.async_create_task(
                    coordinator.async_refresh(), "competing refresh", eager_start=True
                )
                overtook_read = update_entered.is_set()
                action.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await action
                put.assert_not_awaited()
                await asyncio.wait_for(refresh, 2)
                assert update_entered.is_set()
                assert not overtook_read
            finally:
                release.set()
                await asyncio.gather(action, return_exceptions=True)
                if refresh is not None:
                    await asyncio.gather(refresh, return_exceptions=True)

    @pytest.mark.parametrize(
        "change", ["reload", "failed", "options-reverted", "client", "coordinator"]
    )
    async def test_recovered_executor_refuses_superseded_witness(
        self, hass: HomeAssistant, change: str
    ) -> None:
        """A recovered executor refuses superseded witnesses before the PUT."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = entry_data["actions_client"]
        witness = _capture_recovery_witness(entry_data)
        api_client = entry_data["api_client"]
        options = dict(entry.options)
        generation = coordinator.write_miss_generation
        reservations = make_identity_reservations(*_WRITE_TARGETS, "res-new")
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(reservations),
        ):
            assert await coordinator.async_refresh_for_write_miss(generation)

        if change == "reload":
            hass.data[DOMAIN][entry.entry_id] = {}
        elif change == "failed":
            coordinator.last_update_success = False
        elif change == "options-reverted":
            hass.config_entries.async_update_entry(entry, options={"changed": True})
            hass.config_entries.async_update_entry(entry, options=options)
        elif change == "client":
            entry_data["api_client"] = AsyncMock()
        else:
            entry_data["reservations_coordinator"] = copy.copy(coordinator)
        try:
            with (
                patch.object(
                    client._api_client._token_manager, "get_token", return_value="token"
                ),
                patch.object(
                    client._api_client._http,
                    "request",
                    new_callable=AsyncMock,
                    return_value=Response(200),
                ) as request,
                suppress(GuestyApiError),
            ):
                await _execute_recovered_reservation_write(
                    witness,
                    "res-new",
                    operation=LEGACY_WRITE_STATUS,
                    action_type="set_reservation_status",
                    write=lambda route, single, check, notes=None: (
                        client.set_reservation_status(
                            "res-new",
                            "checked_in",
                            route=route,
                            single_attempt=single,
                            before_write=check,
                        )
                    ),
                )
            assert request.await_count == 0
        finally:
            hass.data[DOMAIN][entry.entry_id] = entry_data
            entry_data["api_client"] = api_client
            entry_data["reservations_coordinator"] = coordinator

    @pytest.mark.parametrize("status", [200, 500])
    async def test_recovered_legacy_executor_preserves_accounting(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture, status: int
    ) -> None:
        """A real recovered legacy write retains warning and result counters."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = entry_data["actions_client"]
        witness = _capture_recovery_witness(entry_data)
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                make_identity_reservations(*_WRITE_TARGETS, "res-new"),
                fallback_served=("res-new",),
                fallback_sourced=("res-new",),
            ),
        ):
            assert await coordinator.async_refresh_for_write_miss(witness.generation)
        with (
            caplog.at_level(logging.WARNING),
            patch.object(
                client._api_client._token_manager, "get_token", return_value="token"
            ),
            patch.object(
                client._api_client._http,
                "request",
                new_callable=AsyncMock,
                return_value=Response(status, json={}),
            ) as request,
            suppress(GuestyApiError),
        ):
            await _execute_recovered_reservation_write(
                witness,
                "res-new",
                operation=LEGACY_WRITE_STATUS,
                action_type="set_reservation_status",
                write=lambda route, single, check, notes=None: (
                    client.set_reservation_status(
                        "res-new",
                        "checked_in",
                        route=route,
                        single_attempt=single,
                        before_write=check,
                    )
                ),
            )
        request.assert_awaited_once()
        assert coordinator.legacy_write_counters.status_succeeded == (status == 200)
        assert coordinator.legacy_write_counters.status_failed == (status == 500)
        assert "legacy Guesty reservation resource" in caplog.text

    @pytest.mark.parametrize(
        ("outcome", "category"),
        [
            ("absent", "has no known confirmation code"),
            ("codeless", "has no known confirmation code"),
            ("unconfirmed", "continuity is unconfirmed"),
            ("unstored", "could not be written to storage"),
        ],
    )
    async def test_recovered_executor_enforces_refreshed_identity(
        self, hass: HomeAssistant, outcome: str, category: str
    ) -> None:
        """A real accepted refresh never bypasses an existing identity guard."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        witness = _capture_recovery_witness(entry_data)
        baseline = coordinator.identity.baseline
        assert baseline is not None
        if outcome == "unconfirmed":
            baseline.legacy_codes = {"CONF-res-001": "legacy-res-001"}
        reservations = make_identity_reservations(*_WRITE_TARGETS)
        if outcome == "codeless":
            reservations.append(make_identity_reservation("res-new"))
        elif outcome in {"unconfirmed", "unstored"}:
            reservations.append(make_identity_reservation("res-new", "CONF-res-new"))
        with ExitStack() as stack:
            fetch = stack.enter_context(
                patch(
                    _PATCH_RESERVATIONS,
                    new_callable=AsyncMock,
                    return_value=make_fetch_result(reservations),
                )
            )
            if outcome == "unstored":
                stack.enter_context(
                    patch.object(
                        coordinator.identity, "async_store", side_effect=OSError
                    )
                )
            assert await coordinator.async_refresh_for_write_miss(witness.generation)
        fetch.assert_awaited_once()
        write = AsyncMock()
        with pytest.raises(GuestyActionError, match=category):
            await _execute_recovered_reservation_write(
                witness,
                "res-new",
                operation=LEGACY_WRITE_STATUS,
                action_type="set_reservation_status",
                write=write,
            )
        write.assert_not_awaited()

    @pytest.mark.parametrize(
        ("key", "changed"),
        [
            (CONF_RESERVATIONS_API, RESERVATIONS_API_LEGACY),
            (CONF_PAST_DAYS, 17),
            (CONF_FUTURE_DAYS, 48),
            (CONF_SELECTED_LISTINGS, ["another-listing"]),
            ("unrelated-option", True),
        ],
    )
    async def test_recovered_executor_rejects_all_options_aba(
        self, hass: HomeAssistant, key: str, changed: object
    ) -> None:
        """Back-to-back HA options replacements invalidate the recovery witness."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        witness = _capture_recovery_witness(entry_data)
        options = dict(entry.options)
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                make_identity_reservations(*_WRITE_TARGETS, "res-new")
            ),
        ):
            assert await coordinator.async_refresh_for_write_miss(witness.generation)
        hass.config_entries.async_update_entry(entry, options={**options, key: changed})
        hass.config_entries.async_update_entry(entry, options=options)
        write = AsyncMock()
        with pytest.raises(GuestyActionError, match="configuration"):
            await _execute_recovered_reservation_write(
                witness,
                "res-new",
                operation=LEGACY_WRITE_STATUS,
                action_type="set_reservation_status",
                write=write,
            )
        write.assert_not_awaited()

    async def test_recovered_executor_allows_token_only_entry_update(
        self, hass: HomeAssistant
    ) -> None:
        """Entry data updates alone do not invalidate the options witness."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        witness = _capture_recovery_witness(entry_data)
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                make_identity_reservations(*_WRITE_TARGETS, "res-new")
            ),
        ):
            assert await coordinator.async_refresh_for_write_miss(witness.generation)
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "token": "fake"}
        )
        write = AsyncMock(return_value=ActionResult(success=True, target_id="res-new"))
        await _execute_recovered_reservation_write(
            witness,
            "res-new",
            operation=LEGACY_WRITE_STATUS,
            action_type="set_reservation_status",
            write=write,
        )
        write.assert_awaited_once()

    @pytest.mark.parametrize("change", ["route", "identity"])
    async def test_recovered_executor_rechecks_after_await(
        self, hass: HomeAssistant, change: str
    ) -> None:
        """A note read or token await cannot authorize a stale write."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = entry_data["actions_client"]
        witness = _capture_recovery_witness(entry_data)
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                make_identity_reservations(*_WRITE_TARGETS, "res-new")
            ),
        ):
            assert await coordinator.async_refresh_for_write_miss(witness.generation)

        async def token() -> str:
            """Change live provenance during token acquisition."""
            if change == "route":
                coordinator.fallback_served_ids = frozenset({"res-new"})
                coordinator.fallback_sourced_ids = frozenset({"res-new"})
            return "token"

        async def read_notes(reservation_id: str) -> dict[str, object]:
            """Remove the current identity during the note prerequisite."""
            if change == "identity":
                assert coordinator.identity.baseline is not None
                coordinator.identity.baseline.codes.pop("CONF-res-new")
            return {}

        with (
            patch.object(client._api_client._token_manager, "get_token", token),
            patch.object(client, "_read_v3_notes", side_effect=read_notes),
            patch.object(
                client._api_client._http, "request", new_callable=AsyncMock
            ) as put,
            pytest.raises(GuestyActionError),
        ):
            await _execute_recovered_reservation_write(
                witness,
                "res-new",
                operation=LEGACY_WRITE_NOTE,
                action_type="add_reservation_note",
                write=lambda route, single, check, notes=None: (
                    client.add_reservation_note(
                        "res-new",
                        "hello",
                        route=route,
                        single_attempt=single,
                        before_write=check,
                    )
                ),
            )
        put.assert_not_awaited()

    async def test_recovered_executors_share_fetch_not_authorization(
        self, hass: HomeAssistant
    ) -> None:
        """One shared fetch does not authorize an omitted second target."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        first_witness = _capture_recovery_witness(entry_data)
        second_witness = _capture_recovery_witness(entry_data)
        started = asyncio.Event()
        release = asyncio.Event()
        joined = asyncio.Event()
        write = AsyncMock(return_value="written")

        async def fetch(**kwargs: Any) -> Any:
            """Pause the single actual coordinator fetch."""
            started.set()
            await release.wait()
            return make_fetch_result(
                make_identity_reservations(*_WRITE_TARGETS, "res-new")
            )

        async def run(witness: Any, target: str) -> Any:
            """Refresh and independently authorize a target."""
            if target == "res-omitted":
                joined.set()
            assert await coordinator.async_refresh_for_write_miss(witness.generation)
            return await _execute_recovered_reservation_write(
                witness,
                target,
                operation=LEGACY_WRITE_STATUS,
                action_type="set_reservation_status",
                write=write,
            )

        with patch(_PATCH_RESERVATIONS, side_effect=fetch) as mocked_fetch:
            first = asyncio.create_task(run(first_witness, "res-new"))
            await asyncio.wait_for(started.wait(), 2)
            second = asyncio.create_task(run(second_witness, "res-omitted"))
            await asyncio.wait_for(joined.wait(), 2)
            release.set()
            results = await asyncio.wait_for(
                asyncio.gather(first, second, return_exceptions=True), 2
            )
        mocked_fetch.assert_awaited_once()
        assert results[0] == "written"
        assert isinstance(results[1], GuestyActionError)
        write.assert_awaited_once()

    @pytest.mark.parametrize("outcome", ["conflict", "duplicate", "divergence"])
    async def test_refresh_conflict_blocks_recovered_executor(
        self, hass: HomeAssistant, outcome: str
    ) -> None:
        """Real contradictory identity evidence cannot authorize a recovery."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        witness = _capture_recovery_witness(entry_data)
        assert coordinator.identity.baseline is not None
        reservations = make_identity_reservations(*_WRITE_TARGETS, "res-new")
        if outcome == "conflict":
            reservations = [
                make_identity_reservation("res-999", "CONF-res-001"),
                make_identity_reservation("res-new", "CONF-res-new"),
            ]
        elif outcome == "duplicate":
            reservations.extend(
                [
                    make_identity_reservation("res-other", "CONF-duplicate"),
                    make_identity_reservation("res-another", "CONF-duplicate"),
                ]
            )
        else:
            coordinator.identity.baseline.legacy_codes = {
                "CONF-res-001": "legacy-res-001"
            }
        write = AsyncMock()
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(reservations),
        ):
            accepted = await coordinator.async_refresh_for_write_miss(
                witness.generation
            )
        if outcome == "divergence":
            assert accepted
            with pytest.raises(GuestyActionError, match="continuity is unconfirmed"):
                await _execute_recovered_reservation_write(
                    witness,
                    "res-new",
                    operation=LEGACY_WRITE_STATUS,
                    action_type="set_reservation_status",
                    write=write,
                )
        else:
            assert not accepted
        write.assert_not_awaited()

    async def test_recovered_executor_rejects_refresh_from_replaced_client(
        self, hass: HomeAssistant
    ) -> None:
        """A different client cannot supply an accepted refresh for the witness."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        witness = _capture_recovery_witness(entry_data)
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                make_identity_reservations(*_WRITE_TARGETS, "res-new")
            ),
        ):
            assert await coordinator.async_refresh_for_write_miss(witness.generation)
            original = coordinator.api_client
            coordinator.api_client = copy.copy(original)
            try:
                await coordinator.async_refresh()
            finally:
                coordinator.api_client = original
        write = AsyncMock()
        with pytest.raises(GuestyActionError):
            await _execute_recovered_reservation_write(
                witness,
                "res-new",
                operation=LEGACY_WRITE_STATUS,
                action_type="set_reservation_status",
                write=write,
            )
        write.assert_not_awaited()

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

    async def test_guard_classifies_misses_after_real_refresh(
        self, hass: HomeAssistant
    ) -> None:
        """Real guard failures distinguish absent, code-less, and unsafe states."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        listing = GuestyListing.from_api_dict({"_id": "listing-001"})
        assert listing is not None
        coordinator.listings_coordinator.data = {"listing-001": listing}
        client = self._client(hass, entry)
        reservations = [
            *make_identity_reservations(*_WRITE_TARGETS),
            make_identity_reservation("res-code-less"),
        ]
        generation = coordinator.write_miss_generation
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(reservations),
        ) as fetch:
            assert await coordinator.async_refresh_for_write_miss(generation)
        fetch.assert_awaited_once()
        assert any(
            reservation.id == "res-code-less"
            for listing in coordinator.data.values()
            for reservation in listing
        )
        with pytest.raises(GuestyActionError) as absent:
            guard_reservation_write(entry_data, "res-absent", "add_reservation_note")
        with pytest.raises(GuestyActionError) as codeless:
            guard_reservation_write(entry_data, "res-code-less", "add_reservation_note")
        baseline = coordinator.identity.baseline
        assert baseline is not None
        baseline.continuity = CONTINUITY_UNCONFIRMED
        with pytest.raises(GuestyActionError) as unsafe:
            guard_reservation_write(entry_data, "res-absent", "add_reservation_note")

        assert type(absent.value) is not type(codeless.value)
        assert type(absent.value) is not type(unsafe.value)
        assert type(codeless.value) is not type(unsafe.value)
        assert client.mock_calls == []

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
        """A missing target gets one fresh update and one guarded write."""
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

    @pytest.mark.parametrize(
        ("service", "payload"),
        [
            ("add_reservation_note", {"note_text": "hello"}),
            ("set_reservation_status", {"status": "checked_in"}),
        ],
    )
    @pytest.mark.parametrize(
        "guard",
        ["missing", "unusable", "unstored", "unconfirmed", "ambiguous", "route"],
    )
    async def test_independent_refusal_does_not_refresh(
        self, hass: HomeAssistant, service: str, payload: dict[str, str], guard: str
    ) -> None:
        """Each independent guard refuses both actions without a fetch or write."""
        entry = await _setup_entry(
            hass, reservations=[] if guard == "missing" else None
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        baseline = coordinator.identity.baseline
        if guard != "missing":
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
                coordinator.fallback_sourced_ids = frozenset({"res-001"})
        target = "res-001" if guard == "route" else "res-new"
        category = {
            "missing": "has not established a reservation identity baseline",
            "unusable": "has not established a reservation identity baseline",
            "unstored": "could not be written to storage",
            "unconfirmed": "identity continuity is unconfirmed",
            "ambiguous": "last Guesty reservation refresh was aborted",
            "route": "has not been validated against either resource",
        }[guard]
        with (
            patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch,
            pytest.raises(HomeAssistantError, match=category),
        ):
            await hass.services.async_call(
                DOMAIN,
                service,
                {"reservation_id": target, **payload},
                blocking=True,
                return_response=True,
            )
        fetch.assert_not_awaited()
        assert client.mock_calls == []

    @pytest.mark.parametrize(
        ("reservation_id", "note_text", "message"),
        [
            pytest.param("res-new", "", "note_text must be non-empty", id="empty-note"),
            pytest.param(
                "res-new",
                "x" * (MAX_NOTE_LENGTH + 1),
                "note_text exceeds maximum length",
                id="oversized-note",
            ),
            pytest.param(
                "", "hello", "reservation_id must be non-empty", id="empty-id"
            ),
        ],
    )
    async def test_invalid_note_with_missing_target_reports_validation(
        self, hass: HomeAssistant, reservation_id: str, note_text: str, message: str
    ) -> None:
        """An invalid note reports its own category before recovery."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        with (
            patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch,
            pytest.raises(HomeAssistantError, match=message),
        ):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": reservation_id, "note_text": note_text},
                blocking=True,
                return_response=True,
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
    @pytest.mark.parametrize("route", ["retained-only", "retired"])
    async def test_missing_identity_with_known_unroutable_target(
        self, hass: HomeAssistant, service: str, payload: dict[str, str], route: str
    ) -> None:
        """A missing-code guard cannot hide an independently unroutable target."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        coordinator.fallback_sourced_ids = frozenset({"res-new"})
        if route == "retired":
            coordinator.fallback_served_ids = frozenset({"res-new"})
            coordinator.api_client.disable_legacy_write_routing()
        category = "has not been validated" if route == "retained-only" else "retire"
        with (
            patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch,
            pytest.raises(HomeAssistantError, match=category),
        ):
            await hass.services.async_call(
                DOMAIN,
                service,
                {"reservation_id": "res-new", **payload},
                blocking=True,
                return_response=True,
            )
        fetch.assert_not_awaited()
        assert client.mock_calls == []

    async def test_invalid_status_with_missing_target_reports_validation(
        self, hass: HomeAssistant
    ) -> None:
        """The status schema rejects invalid input before recovery."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        with (
            patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch,
            pytest.raises(vol.Invalid),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_reservation_status",
                {"reservation_id": "res-new", "status": "invalid"},
                blocking=True,
                return_response=True,
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
    async def test_blank_target_does_not_refresh_or_write(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
    ) -> None:
        """A blank identifier cannot cause a refresh or a write."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        with (
            patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch,
            pytest.raises(HomeAssistantError),
        ):
            await hass.services.async_call(
                DOMAIN,
                service,
                {"reservation_id": "  ", **payload},
                blocking=True,
                return_response=True,
            )
        fetch.assert_not_awaited()
        assert client.mock_calls == []

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
    async def test_cache_hit_does_not_refresh(
        self, hass: HomeAssistant, service: str, payload: dict[str, str], method: str
    ) -> None:
        """A known identity uses the existing write path without a refresh."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        getattr(client, method).return_value = ActionResult(
            success=True, target_id="res-001"
        )
        with patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch:
            result = await hass.services.async_call(
                DOMAIN,
                service,
                {"reservation_id": "res-001", **payload},
                blocking=True,
                return_response=True,
            )
        assert result == {"success": True, "target_id": "res-001"}
        fetch.assert_not_awaited()
        getattr(client, method).assert_awaited_once()
        assert "single_attempt" not in getattr(client, method).await_args.kwargs

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
    async def test_published_codeless_identity_refreshes_before_write(
        self, hass: HomeAssistant, service: str, payload: dict[str, str], method: str
    ) -> None:
        """A published code-less target may be confirmed by one refresh."""
        base = make_identity_reservations(*_WRITE_TARGETS)
        uncoded = make_identity_reservation("res-new")
        entry = await _setup_entry(hass, reservations=[*base, uncoded])
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.data = {"listing-001": [uncoded]}
        client = self._client(hass, entry)
        getattr(client, method).return_value = ActionResult(
            success=True, target_id="res-new"
        )
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                [*base, make_identity_reservation("res-new", "CONF-res-new")]
            ),
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

    async def test_cancelled_note_read_releases_recovery_dispatch_lock(
        self, hass: HomeAssistant
    ) -> None:
        """Cancel during a note GET, then prove a waiting refresh resumes."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = entry_data["actions_client"]
        reading = asyncio.Event()
        release = asyncio.Event()

        @asynccontextmanager
        async def recovery_lock() -> AsyncIterator[None]:
            """Use the recovery lock if implemented; otherwise the old path."""
            factory = getattr(coordinator, "async_write_miss_dispatch_lock", None)
            if factory is None:
                yield
            else:
                async with factory():
                    yield

        async def read_notes(reservation_id: str) -> dict[str, object]:
            """Pause the existing note prerequisite read."""
            reading.set()
            await release.wait()
            return {}

        async def dispatch_note() -> None:
            """Exercise the real note write inside the recovery lock seam."""
            async with recovery_lock():
                await client.add_reservation_note("res-001", "hello")

        with (
            patch.object(client, "_read_v3_notes", side_effect=read_notes),
            patch.object(client._api_client, "_request", new_callable=AsyncMock) as put,
            patch.object(coordinator, "_async_update_data", return_value={}) as update,
        ):
            action = asyncio.create_task(dispatch_note())
            try:
                await asyncio.wait_for(reading.wait(), 2)
                refresh = hass.async_create_task(
                    coordinator.async_refresh(),
                    "competing reservation refresh",
                    eager_start=True,
                )
                calls_during_read = update.await_count
                action.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await action
                put.assert_not_awaited()
                await asyncio.wait_for(refresh, 2)
                assert update.await_count == 1
                assert calls_during_read == 0
            finally:
                release.set()
                await asyncio.gather(action, return_exceptions=True)

    async def test_recovery_dispatch_blocks_refresh_through_note_get_and_put(
        self, hass: HomeAssistant
    ) -> None:
        """A coordinator refresh cannot overtake the actual note read or write."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = entry_data["actions_client"]
        reading = asyncio.Event()
        allow_read = asyncio.Event()
        writing = asyncio.Event()
        allow_write = asyncio.Event()

        @asynccontextmanager
        async def recovery_lock() -> AsyncIterator[None]:
            """Use the recovery lock if implemented; otherwise the old path."""
            factory = getattr(coordinator, "async_write_miss_dispatch_lock", None)
            if factory is None:
                yield
            else:
                async with factory():
                    yield

        async def read_notes(reservation_id: str) -> dict[str, object]:
            """Pause the existing prerequisite."""
            reading.set()
            await allow_read.wait()
            return {}

        async def put(*args: object, **kwargs: object) -> httpx.Response:
            """Pause the real write request."""
            writing.set()
            await allow_write.wait()
            return httpx.Response(200)

        async def dispatch_note() -> None:
            """Use the real API note operation within the recovery seam."""
            async with recovery_lock():
                await client.add_reservation_note("res-001", "hello")

        with (
            patch.object(client, "_read_v3_notes", side_effect=read_notes),
            patch.object(client._api_client, "_request", side_effect=put) as request,
            patch.object(coordinator, "_async_update_data", return_value={}) as update,
        ):
            action = asyncio.create_task(dispatch_note())
            try:
                await asyncio.wait_for(reading.wait(), 2)
                refresh = hass.async_create_task(
                    coordinator.async_refresh(),
                    "competing reservation refresh",
                    eager_start=True,
                )
                calls_during_read = update.await_count
                allow_read.set()
                await asyncio.wait_for(writing.wait(), 2)
                calls_during_write = update.await_count
                allow_write.set()
                await asyncio.wait_for(action, 2)
                await asyncio.wait_for(refresh, 2)
                request.assert_awaited_once()
                assert update.await_count == 1
                assert calls_during_read == 0
                assert calls_during_write == 0
            finally:
                allow_read.set()
                allow_write.set()
                await asyncio.gather(action, return_exceptions=True)

    async def test_no_baseline_refuses_both_entry_points(
        self,
        hass: HomeAssistant,
    ) -> None:
        """An empty refresh leaves no baseline, so writes refuse."""
        entry = await _setup_entry(hass, reservations=[])
        client = self._client(hass, entry)

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

        assert client.mock_calls == []

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

    async def test_target_without_confirmation_code_refuses(
        self,
        hass: HomeAssistant,
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
                "add_reservation_note",
                {"reservation_id": "res-nocode", "note_text": "hi"},
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

        result = await hass.services.async_call(
            DOMAIN,
            "add_reservation_note",
            {"reservation_id": "res-001", "note_text": "hi"},
            blocking=True,
            return_response=True,
        )

        assert result == {"success": True, "target_id": "res-001"}
        client.add_reservation_note.assert_awaited_once()

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
        ("service", "payload", "error"),
        [
            ("add_reservation_note", {"note_text": ""}, HomeAssistantError),
            ("set_reservation_status", {"status": "invalid"}, vol.Invalid),
        ],
    )
    async def test_invalid_action_refuses_without_refresh(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        error: type[Exception],
    ) -> None:
        """Invalid action input cannot refresh or write a missing target."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        with (
            patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch,
            pytest.raises(error),
        ):
            await hass.services.async_call(
                DOMAIN,
                service,
                {"reservation_id": "res-new", **payload},
                blocking=True,
                return_response=True,
            )
        fetch.assert_not_awaited()
        assert client.mock_calls == []

    @pytest.mark.parametrize("replacement", ["entry-mapping", "coordinator"])
    async def test_stale_entry_before_miss_does_not_refresh(
        self, hass: HomeAssistant, replacement: str
    ) -> None:
        """A stale entry mapping or coordinator cannot start recovery."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        replacement_data = dict(entry_data)
        if replacement == "coordinator":
            replacement_data["reservations_coordinator"] = AsyncMock()
        hass.data[DOMAIN][entry.entry_id] = replacement_data
        write = AsyncMock()
        try:
            with (
                patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch,
                pytest.raises(GuestyActionError, match="no longer loaded"),
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
        finally:
            hass.data[DOMAIN][entry.entry_id] = entry_data

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
        ("service", "payload"),
        [
            ("add_reservation_note", {"note_text": "hello"}),
            ("set_reservation_status", {"status": "checked_in"}),
        ],
    )
    async def test_retired_retained_miss_explains_retirement(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
    ) -> None:
        """A retained but never-served target explains legacy retirement."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        coordinator.fallback_sourced_ids = frozenset({"res-new"})
        client = self._client(hass, entry)
        api_client = entry_data["api_client"]
        with (
            patch.object(
                type(api_client),
                "legacy_write_routing_available",
                property(lambda _: False),
            ),
            patch(_PATCH_RESERVATIONS, new_callable=AsyncMock) as fetch,
        ):
            await self._expect_refusal(
                hass, service, {"reservation_id": "res-new", **payload}, "2027-03-01"
            )
        fetch.assert_not_awaited()
        assert client.mock_calls == []

    @pytest.mark.parametrize("operation", ["note-read", "note-write", "status"])
    @pytest.mark.parametrize("status", [404, 410])
    async def test_recovered_legacy_primary_verdict_is_safe_and_actionable(
        self, hass: HomeAssistant, operation: str, status: int
    ) -> None:
        """A legacy-primary 404/410 preserves advice without response contents."""
        entry = await _setup_entry(
            hass, options={CONF_RESERVATIONS_API: RESERVATIONS_API_LEGACY}
        )
        entry_data = hass.data[DOMAIN][entry.entry_id]
        api_client = entry_data["api_client"]
        methods: list[str] = []

        async def request(method: str, path: str, **kwargs: object) -> Response:
            """Answer the note read or send the selected retirement verdict."""
            methods.append(method)
            if operation == "note-write" and method == "GET":
                return Response(200, json={"_id": "res-new", "note": ""})
            return Response(
                status,
                json={"error": "SECRET-PIN"},
                request=httpx.Request(method, f"https://example.test{path}"),
            )

        service = (
            "set_reservation_status"
            if operation == "status"
            else "add_reservation_note"
        )
        payload = (
            {"status": "checked_in"} if operation == "status" else {"note_text": "hi"}
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
            patch.object(api_client, "_request", side_effect=request),
            pytest.raises(HomeAssistantError) as raised,
        ):
            await hass.services.async_call(
                DOMAIN,
                service,
                {"reservation_id": "res-new", **payload},
                blocking=True,
                return_response=True,
            )
        message = str(raised.value)
        assert "2027-03-01" in message and "v3" in message
        assert "SECRET-PIN" not in message
        assert methods == (
            ["GET"]
            if operation == "note-read"
            else ["GET", "PUT"]
            if operation == "note-write"
            else ["PUT"]
        )

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

    async def test_recovery_uses_configured_v3_fetch_and_pagination(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A service miss uses normal v3 windows, paging and guest lookup."""
        freezer.move_to("2026-11-23 12:00:00+00:00")
        entry = await _setup_entry(
            hass,
            options={
                CONF_RESERVATIONS_API: RESERVATIONS_API_V3,
                CONF_PAST_DAYS: 5,
                CONF_FUTURE_DAYS: 12,
                CONF_SELECTED_LISTINGS: ["listing-001"],
            },
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        listing = GuestyListing.from_api_dict(
            {"_id": "listing-001", "title": "Selected", "timezone": "UTC"}
        )
        assert listing is not None
        coordinator.listings_coordinator.data = {listing.id: listing}
        coordinator.listings_coordinator.timing_listings = {listing.id: listing}
        client = self._client(hass, entry)
        client.set_reservation_status.return_value = ActionResult(
            success=True, target_id="z-target"
        )
        start = datetime.now(UTC) + timedelta(days=1)
        end = start + timedelta(days=2)
        segment = make_v3_segment(
            checkIn=start.isoformat(),
            checkOut=end.isoformat(),
            checkInDateLocalized=start.date().isoformat(),
            checkOutDateLocalized=end.date().isoformat(),
        )
        ids = sorted([*_WRITE_TARGETS, *[f"item-{n:03}" for n in range(96)]])
        first_page = [
            make_v3_reservation(
                reservation_id,
                segments=[segment],
                guest_id=None,
                confirmationCode=f"CONF-{reservation_id}",
            )
            for reservation_id in ids
        ]
        target = make_v3_reservation(
            "z-target",
            segments=[segment],
            guest_id="guest-001",
            confirmationCode="CONF-z-target",
        )
        requests: list[tuple[str, str, dict[str, Any]]] = []

        async def request(method: str, path: str, **kwargs: Any) -> Response:
            """Answer only configured source pages and the one guest lookup."""
            requests.append((method, path, kwargs))
            if path == RESERVATIONS_V3_SEARCH_ENDPOINT:
                skip = kwargs["params"]["skip"]
                if skip == 0:
                    return Response(
                        200, json=make_v3_search_page(first_page, has_more=True)
                    )
                assert skip == RESERVATIONS_PAGE_SIZE
                return Response(
                    200,
                    json=make_v3_search_page([target], skip=RESERVATIONS_PAGE_SIZE),
                )
            assert path == "/guests-crud/guest-001"
            return Response(200, json={"_id": "guest-001", "fullName": "Test"})

        with patch.object(coordinator.api_client, "_request", side_effect=request):
            try:
                result = await hass.services.async_call(
                    DOMAIN,
                    "set_reservation_status",
                    {"reservation_id": "z-target", "status": "checked_in"},
                    blocking=True,
                    return_response=True,
                )
            except HomeAssistantError:
                result = None
        assert [(method, path) for method, path, _ in requests] == [
            ("GET", RESERVATIONS_V3_SEARCH_ENDPOINT),
            ("GET", RESERVATIONS_V3_SEARCH_ENDPOINT),
            ("GET", "/guests-crud/guest-001"),
        ]
        params = requests[0][2]["params"]
        assert params["filter[checkOut][gte]"] == "2026-11-17"
        assert params["filter[checkIn][lte]"] == "2026-12-06"
        assert params["filter[status][in]"] == (
            "canceled,checked_in,checked_out,completed,confirmed"
        )
        assert params["skip"] == 0
        assert requests[1][2]["params"]["skip"] == RESERVATIONS_PAGE_SIZE
        assert result == {"success": True, "target_id": "z-target"}
        client.set_reservation_status.assert_awaited_once()

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
            try:
                result = await hass.services.async_call(
                    DOMAIN,
                    "set_reservation_status",
                    {"reservation_id": "res-new", "status": "checked_in"},
                    blocking=True,
                    return_response=True,
                )
            except HomeAssistantError:
                result = None
        assert fetch.await_count == (0 if initially_known else 1)
        baseline = coordinator.identity.baseline
        assert baseline is not None and "res-new" in baseline.current_ids()
        published = any(
            reservation.id == "res-new"
            for reservations in coordinator.data.values()
            for reservation in reservations
        )
        assert published is (publication == "selected")
        assert result == {"success": True, "target_id": "res-new"}
        client.set_reservation_status.assert_awaited_once()
        if initially_known:
            assert (
                "single_attempt" not in client.set_reservation_status.await_args.kwargs
            )

    @pytest.mark.parametrize(
        ("service", "payload"),
        [
            ("add_reservation_note", {"note_text": "hello"}),
            ("set_reservation_status", {"status": "checked_in"}),
        ],
    )
    async def test_failed_miss_refresh_never_uses_retained_data(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
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
                service,
                {"reservation_id": "res-new", **payload},
                "refresh",
            )
        fetch.assert_awaited_once()
        assert client.mock_calls == []

    @pytest.mark.parametrize(
        ("service", "payload"),
        [
            ("add_reservation_note", {"note_text": "hello"}),
            ("set_reservation_status", {"status": "checked_in"}),
        ],
    )
    async def test_failed_refresh_cannot_authorize_even_if_guard_would_pass(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
    ) -> None:
        """A failed refresh cannot use newly available identity as success."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        baseline = coordinator.identity.baseline
        assert baseline is not None

        async def fail_after_identity_change(*args: object, **kwargs: object) -> object:
            """Expose otherwise sufficient identity, then fail the actual fetch."""
            baseline.codes["CONF-res-new"] = "res-new"
            raise GuestyApiError("unavailable")

        with patch(
            _PATCH_RESERVATIONS, side_effect=fail_after_identity_change
        ) as fetch:
            await self._expect_refusal(
                hass, service, {"reservation_id": "res-new", **payload}, "refresh"
            )
        fetch.assert_awaited_once()
        assert coordinator.identity.write_refusal("res-new") is None
        assert coordinator.last_update_success is False
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

    @pytest.mark.parametrize(
        ("service", "payload"),
        [
            ("add_reservation_note", {"note_text": "hello"}),
            ("set_reservation_status", {"status": "checked_in"}),
        ],
    )
    async def test_missing_target_refreshes_once_without_write(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
    ) -> None:
        """A valid miss performs one refresh and refuses an omitted target."""
        entry = await _setup_entry(hass)
        client = self._client(hass, entry)
        with (
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    make_identity_reservations(*_WRITE_TARGETS)
                ),
            ) as fetch,
            pytest.raises(HomeAssistantError),
        ):
            await hass.services.async_call(
                DOMAIN,
                service,
                {"reservation_id": "res-new", **payload},
                blocking=True,
                return_response=True,
            )
        fetch.assert_awaited_once()
        assert client.mock_calls == []

    @pytest.mark.parametrize(
        ("service", "payload"),
        [
            ("add_reservation_note", {"note_text": "hello"}),
            ("set_reservation_status", {"status": "checked_in"}),
        ],
    )
    @pytest.mark.parametrize(
        "outcome", ["absent", "codeless", "unconfirmed", "unstored"]
    )
    async def test_refreshed_identity_refusal_is_reported(
        self, hass: HomeAssistant, service: str, payload: dict[str, str], outcome: str
    ) -> None:
        """Report the real post-refresh guard refusal without issuing a write."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = self._client(hass, entry)
        baseline = coordinator.identity.baseline
        assert baseline is not None
        if outcome == "unconfirmed":
            baseline.legacy_codes = {"CONF-res-001": "legacy-res-001"}
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
        with ExitStack() as stack:
            fetch = stack.enter_context(
                patch(
                    _PATCH_RESERVATIONS,
                    new_callable=AsyncMock,
                    return_value=make_fetch_result(reservations),
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
                    service,
                    {"reservation_id": "res-new", **payload},
                    blocking=True,
                    return_response=True,
                )
        fetch.assert_awaited_once()
        assert coordinator.last_update_success
        assert client.mock_calls == []
        with pytest.raises(GuestyActionError, match=category):
            guard_reservation_write(entry_data, "res-new", service)
        assert category in str(error.value)

    @pytest.mark.parametrize(
        ("service", "payload"),
        [
            ("add_reservation_note", {"note_text": "hello"}),
            ("set_reservation_status", {"status": "checked_in"}),
        ],
    )
    async def test_target_still_missing_after_one_refresh(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
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
                service,
                {"reservation_id": "res-new", **payload},
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

    async def test_cancelling_recovered_note_read_releases_dispatch(
        self, hass: HomeAssistant
    ) -> None:
        """Cancellation during the prerequisite read sends no PUT."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = entry_data["actions_client"]
        reading = asyncio.Event()
        release = asyncio.Event()

        async def read_notes(reservation_id: str) -> dict[str, object]:
            """Pause the read until the service is cancelled."""
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
            patch.object(client._api_client, "_request", new_callable=AsyncMock) as put,
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
            try:
                await asyncio.wait_for(reading.wait(), 2)
                action.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await action
                put.assert_not_awaited()
                await asyncio.wait_for(coordinator.async_refresh(), 2)
                assert fetch.await_count == 2
            finally:
                release.set()
                await asyncio.gather(action, return_exceptions=True)

    async def test_failed_recovered_note_read_sends_no_write(
        self, hass: HomeAssistant
    ) -> None:
        """A failed prerequisite read cannot dispatch a recovered note."""
        entry = await _setup_entry(hass)
        client = hass.data[DOMAIN][entry.entry_id]["actions_client"]
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
                client,
                "_read_v3_notes",
                new_callable=AsyncMock,
                side_effect=GuestyActionError(
                    "read failed", target_id="res-new", action_type="add_note"
                ),
            ),
            patch.object(client._api_client, "_request", new_callable=AsyncMock) as put,
            pytest.raises(HomeAssistantError),
        ):
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-new", "note_text": "hello"},
                blocking=True,
                return_response=True,
            )
        fetch.assert_awaited_once()
        put.assert_not_awaited()

    @pytest.mark.parametrize("stage", ["preflight", "note-read"])
    async def test_unload_in_progress_refuses_recovered_write(
        self, hass: HomeAssistant, stage: str
    ) -> None:
        """A real entry unload prevents a new fetch or a write after a note read."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        client = entry_data["actions_client"]
        reading = asyncio.Event()
        release_read = asyncio.Event()
        unloading = asyncio.Event()
        release_unload = asyncio.Event()
        original_unload = hass.config_entries.async_unload_platforms

        async def read_notes(reservation_id: str) -> dict[str, object]:
            """Pause the prerequisite read until the entry starts unloading."""
            reading.set()
            await release_read.wait()
            return {}

        async def unload_platforms(*args: Any, **kwargs: Any) -> bool:
            """Hold the integration data mapping while HA marks it unloading."""
            unloading.set()
            await release_unload.wait()
            return await original_unload(*args, **kwargs)

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
            patch.object(client._api_client, "_request", new_callable=AsyncMock) as put,
            patch.object(
                hass.config_entries, "async_unload_platforms", unload_platforms
            ),
        ):
            action: asyncio.Task[Any] | None = None
            unload: asyncio.Task[bool] | None = None
            try:
                if stage == "note-read":
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
                unload = asyncio.create_task(
                    hass.config_entries.async_unload(entry.entry_id)
                )
                await asyncio.wait_for(unloading.wait(), 2)
                assert entry.state is ConfigEntryState.UNLOAD_IN_PROGRESS
                assert hass.data[DOMAIN][entry.entry_id] is entry_data
                if action is None:
                    action = asyncio.create_task(
                        hass.services.async_call(
                            DOMAIN,
                            "add_reservation_note",
                            {"reservation_id": "res-new", "note_text": "hello"},
                            blocking=True,
                            return_response=True,
                        )
                    )
                release_read.set()
                with pytest.raises(HomeAssistantError):
                    await asyncio.wait_for(action, 2)
                assert fetch.await_count == (0 if stage == "preflight" else 1)
                put.assert_not_awaited()
            finally:
                release_read.set()
                release_unload.set()
                if action is not None:
                    await asyncio.gather(action, return_exceptions=True)
                if unload is not None:
                    await asyncio.gather(unload, return_exceptions=True)

    async def test_unload_cancels_recovery_fetch_before_identity_storage(
        self, hass: HomeAssistant
    ) -> None:
        """A recovery task cannot outlive its entry and persist an old baseline."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = self._client(hass, entry)
        started = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()

        async def fetch(*args: object, **kwargs: object) -> object:
            """Pause the API fetch until unload cancels it."""
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return make_fetch_result(
                [
                    *make_identity_reservations(*_WRITE_TARGETS),
                    make_identity_reservation("res-new", "CONF-res-new"),
                ]
            )

        with (
            patch(_PATCH_RESERVATIONS, side_effect=fetch),
            patch.object(
                coordinator.identity,
                "async_store",
                wraps=coordinator.identity.async_store,
            ) as store,
        ):
            action = asyncio.create_task(
                hass.services.async_call(
                    DOMAIN,
                    "set_reservation_status",
                    {"reservation_id": "res-new", "status": "checked_in"},
                    blocking=True,
                    return_response=True,
                )
            )
            try:
                await asyncio.wait_for(started.wait(), 2)
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert cancelled.is_set()
                with pytest.raises(HomeAssistantError, match="refresh"):
                    await action
                store.assert_not_awaited()
                client.set_reservation_status.assert_not_awaited()
            finally:
                release.set()
                await asyncio.gather(action, return_exceptions=True)

    @pytest.mark.parametrize("stopping", ["coordinator", "home-assistant"])
    async def test_shutdown_after_note_read_prevents_write(
        self, hass: HomeAssistant, stopping: str
    ) -> None:
        """A successful recovery cannot dispatch after shutdown begins."""
        entry = await _setup_entry(hass)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = entry_data["reservations_coordinator"]
        client = entry_data["actions_client"]
        original_state = hass.state

        async def read_notes(reservation_id: str) -> dict[str, object]:
            """Start shutdown while the prerequisite note read is awaited."""
            if stopping == "coordinator":
                await coordinator.async_shutdown()
            else:
                hass.set_state(CoreState.stopping)
            return {}

        try:
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
                patch.object(
                    client._api_client, "_request", new_callable=AsyncMock
                ) as put,
            ):
                await self._expect_refusal(
                    hass,
                    "add_reservation_note",
                    {"reservation_id": "res-new", "note_text": "hello"},
                    "refused",
                )
                put.assert_not_awaited()
        finally:
            hass.set_state(original_state)

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

    @pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "omission"])
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
            targets = (
                []
                if outcome == "omission"
                else [
                    make_identity_reservation("res-new", "CONF-res-new"),
                    make_identity_reservation("res-other", "CONF-res-other"),
                ]
            )
            return make_fetch_result(
                [*make_identity_reservations(*_WRITE_TARGETS), *targets]
            )

        async def observe_join(generation: int) -> bool:
            """Signal once both service attempts have joined."""
            nonlocal join_count
            join_count += 1
            if join_count == 2:
                joined.set()
            return bool(await refresh(generation))

        async def call(target: str) -> Any:
            """Call the reservation status service for one target."""
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
            existing = asyncio.create_task(coordinator.async_refresh())
            await asyncio.wait_for(started.wait(), 2)
            first = asyncio.create_task(call("res-new"))
            second = asyncio.create_task(call("res-other"))
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
            pytest.raises(HomeAssistantError) as error,
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
        category = {
            "reload": "no longer loaded",
            "failed": "refresh failed",
            "options": "configuration",
            "options-reverted": "configuration",
            "removed": "has no known confirmation code",
            "client": "no longer loaded",
            "coordinator": "no longer loaded",
        }[change]
        assert category in str(error.value)

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
        coordinator.fallback_served_ids = frozenset({"res-unknown"})
        coordinator.fallback_sourced_ids = frozenset({"res-unknown"})
        client = AsyncMock()
        entry_data["actions_client"] = client

        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                DOMAIN,
                "add_reservation_note",
                {"reservation_id": "res-unknown", "note_text": "hi"},
                blocking=True,
                return_response=True,
            )

        assert "2027-03-01" not in str(exc_info.value)
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


# ── Feature 014: the 013 evidence port (T019) ───────────────────────


class TestConflictEvidencePort:
    """The thin 013 port stays unavailable in production."""

    async def test_production_adapter_is_unavailable(self, hass: HomeAssistant) -> None:
        """The production adapter reports unavailable."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]

        assert _conflict_evidence(coordinator).available() is False

    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("codes_for", ("res-1",)),
            ("ids_for", ("CONF-1",)),
            ("validate_record", ({}, "add_reservation_note")),
        ],
    )
    def test_unavailable_adapter_is_never_consulted_for_lookups(
        self, method: str, args: tuple[Any, ...]
    ) -> None:
        """Every lookup on the unavailable adapter raises."""
        with pytest.raises(RuntimeError):
            getattr(UnavailableConflictEvidence(), method)(*args)

    def test_fake_double_satisfies_port(self) -> None:
        """The test double satisfies the port and records its calls."""
        fake = FakeConflictEvidence(
            codes={"res-1": frozenset({"C1"})},
            ids={"C1": frozenset({"res-1"})},
            verdict="rejected",
        )
        port: ConflictEvidence = fake

        assert port.available() is True
        assert port.codes_for("res-1") == frozenset({"C1"})
        assert port.ids_for("C2") == frozenset()
        assert port.validate_record({}, "set_reservation_status") == "rejected"
        assert [name for name, _ in fake.calls] == [
            "available",
            "codes_for",
            "ids_for",
            "validate_record",
        ]


# ── Feature 014: by-ID write fallback (T024-T046) ───────────────────

_BY_ID = BY_ID_TARGET
_ACTIONS_LOGGER = "custom_components.guesty.actions"
_NO_CODE = "has no known confirmation code"
_BY_ID_SERVICES = pytest.mark.parametrize(
    ("service", "payload"),
    [
        ("add_reservation_note", {"note_text": "hello"}),
        ("set_reservation_status", {"status": "checked_in"}),
    ],
    ids=["note", "status"],
)


def _refresh(*extra: GuestyReservation, **fields: Any) -> ReservationFetchResult:
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


def _codeless_refresh(code: str | None = None, **fields: Any) -> ReservationFetchResult:
    """Build a refresh that returns the target without a code.

    Args:
        code: The target's code, None or empty.
        **fields: Further fetch result fields to set.

    Returns:
        The refresh result.
    """
    return _refresh(
        make_identity_reservation(_BY_ID, code),
        returned_ids=(_BY_ID,),
        **fields,
    )


def _by_id_coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Return the entry's reservations coordinator.

    Args:
        hass: Home Assistant instance.
        entry: The loaded entry.

    Returns:
        The coordinator.
    """
    return hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]


async def _call_by_id(
    hass: HomeAssistant,
    service: str,
    payload: dict[str, Any],
    reservation_id: str = _BY_ID,
) -> Any:
    """Call a reservation write service on a target.

    Args:
        hass: Home Assistant instance.
        service: The service to call.
        payload: The service fields other than the identifier.
        reservation_id: The reservation targeted.

    Returns:
        The service response.
    """
    return await hass.services.async_call(
        DOMAIN,
        service,
        {"reservation_id": reservation_id, **payload},
        blocking=True,
        return_response=True,
    )


async def _by_id_refusal(
    hass: HomeAssistant,
    service: str,
    payload: dict[str, Any],
    reservation_id: str = _BY_ID,
) -> str:
    """Call a write expected to fail and return the error text.

    Args:
        hass: Home Assistant instance.
        service: The service to call.
        payload: The service fields other than the identifier.
        reservation_id: The reservation targeted.

    Returns:
        The surfaced error text.
    """
    with pytest.raises(HomeAssistantError) as error:
        await _call_by_id(hass, service, payload, reservation_id)
    return str(error.value)


def _ineligible(caplog: pytest.LogCaptureFixture) -> list[tuple[object, ...]]:
    """Collect the by-ID not-eligible DEBUG records' arguments.

    Args:
        caplog: The log capture.

    Returns:
        One ``(category, reservation_id)`` tuple per record.
    """
    return [
        tuple(record.args or ())
        for record in caplog.records
        if record.name == _ACTIONS_LOGGER
        and record.levelno == logging.DEBUG
        and "not eligible" in str(record.msg)
    ]


class TestByIdFallbackInertUntil013:
    """Production keeps the unchanged 012 refusal (T024)."""

    @_BY_ID_SERVICES
    @pytest.mark.parametrize("target", ["absent", "codeless"])
    async def test_unpatched_adapter_keeps_012_refusal(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        service: str,
        payload: dict[str, str],
        target: str,
    ) -> None:
        """No by-ID read happens and one DEBUG line names the reason."""
        await _setup_entry(hass)
        refresh = _refresh() if target == "absent" else _codeless_refresh()
        caplog.set_level(logging.DEBUG, logger=_ACTIONS_LOGGER)
        with (
            by_id_network(refresh=refresh) as routes,
            patch.object(
                GuestyActionsClient, "read_reservation_by_id", autospec=True
            ) as read,
        ):
            text = await _by_id_refusal(hass, service, payload)

        assert text == REFUSAL_NO_CODE.format(reservation_id=_BY_ID)
        read.assert_not_called()
        assert routes.read.call_count == 0
        assert routes.writes == 0
        assert _ineligible(caplog) == [("evidence_unavailable", _BY_ID)]


class TestByIdPreReadGuards:
    """Earlier 012 refusals still decide first (T026)."""

    @_BY_ID_SERVICES
    @pytest.mark.parametrize(
        ("state", "match"),
        [
            ("no_baseline", REFUSAL_NO_BASELINE),
            ("unstored", REFUSAL_UNSTORED),
            ("unconfirmed", REFUSAL_UNCONFIRMED),
            ("ambiguous", REFUSAL_AMBIGUOUS),
        ],
    )
    async def test_non_miss_guard_refusal_reads_nothing(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        state: str,
        match: str,
    ) -> None:
        """A non-miss guard refusal refreshes and reads nothing."""
        entry = await _setup_entry(hass)
        identity = _by_id_coordinator(hass, entry).identity
        if state == "no_baseline":
            identity._baseline = None
        elif state == "unstored":
            identity._unstored = True
        elif state == "unconfirmed":
            identity.baseline.continuity = CONTINUITY_UNCONFIRMED
        else:
            identity._ambiguous = True
        with by_id_network(
            refresh=_refresh(), evidence=FakeConflictEvidence()
        ) as routes:
            text = await _by_id_refusal(hass, service, payload)

        assert text == match
        routes.fetch.assert_not_awaited()
        assert routes.read.call_count == 0
        assert routes.writes == 0

    @_BY_ID_SERVICES
    async def test_invalid_identifier_reads_nothing(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
    ) -> None:
        """A blank identifier fails before any refresh or read."""
        await _setup_entry(hass)
        with (
            by_id_network(
                refresh=_refresh(), evidence=FakeConflictEvidence()
            ) as routes,
            pytest.raises((HomeAssistantError, vol.Invalid)),
        ):
            await _call_by_id(hass, service, payload, reservation_id="  ")

        routes.fetch.assert_not_awaited()
        assert routes.read.call_count == 0
        assert routes.writes == 0

    @_BY_ID_SERVICES
    async def test_known_unroutable_target_reads_nothing(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
    ) -> None:
        """A retained-only fallback target is refused before any refresh."""
        entry = await _setup_entry(hass)
        _by_id_coordinator(hass, entry).fallback_sourced_ids = frozenset({_BY_ID})
        with by_id_network(
            refresh=_refresh(), evidence=FakeConflictEvidence()
        ) as routes:
            text = await _by_id_refusal(hass, service, payload)

        assert text == REFUSAL_NOT_REACHABLE.format(reservation_id=_BY_ID)
        routes.fetch.assert_not_awaited()
        assert routes.read.call_count == 0
        assert routes.writes == 0

    @_BY_ID_SERVICES
    async def test_failed_refresh_reads_nothing(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
    ) -> None:
        """A failed qualifying refresh keeps its 012 refusal."""
        await _setup_entry(hass)
        with by_id_network(
            refresh=_refresh(), evidence=FakeConflictEvidence()
        ) as routes:
            routes.fetch.side_effect = GuestyApiError("unavailable")
            text = await _by_id_refusal(hass, service, payload)

        assert "refresh failed" in text
        assert routes.read.call_count == 0
        assert routes.writes == 0

    @_BY_ID_SERVICES
    async def test_superseded_refresh_reads_nothing(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
    ) -> None:
        """A refresh superseded by a failed one keeps its 012 refusal."""
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)
        original = coordinator.async_refresh_for_write_miss

        async def superseded(generation: int) -> bool:
            """Complete the refresh, then record a newer failed one."""
            result: bool = await original(generation)
            latest = coordinator._write_miss_outcomes[-1]
            coordinator._write_miss_outcomes.append(
                (latest[0] + 1, latest[1] + 1, False, *latest[3:])
            )
            return result

        with (
            by_id_network(
                refresh=_refresh(), evidence=FakeConflictEvidence()
            ) as routes,
            patch.object(
                coordinator, "async_refresh_for_write_miss", side_effect=superseded
            ),
        ):
            text = await _by_id_refusal(hass, service, payload)

        assert "failed or was superseded" in text
        assert routes.read.call_count == 0
        assert routes.writes == 0


class TestByIdPreReadEligibility:
    """Every unqualified miss keeps its 012 refusal (T027)."""

    @staticmethod
    async def _expect_ineligible(
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        category: str,
        *,
        refresh: ReservationFetchResult | None = None,
        evidence: FakeConflictEvidence | None = None,
        service: str = "set_reservation_status",
        payload: dict[str, str] | None = None,
    ) -> None:
        """Assert one not-eligible category and the 012 refusal.

        Args:
            hass: Home Assistant instance.
            caplog: The log capture.
            category: The expected DEBUG category.
            refresh: The qualifying refresh; target absent if None.
            evidence: The 013 double; available if None.
            service: The service to call.
            payload: The service fields other than the identifier.
        """
        caplog.set_level(logging.DEBUG, logger=_ACTIONS_LOGGER)
        with by_id_network(
            refresh=refresh or _refresh(),
            evidence=evidence or FakeConflictEvidence(),
        ) as routes:
            text = await _by_id_refusal(
                hass, service, payload or {"status": "checked_in"}
            )

        assert text == REFUSAL_NO_CODE.format(reservation_id=_BY_ID)
        routes.fetch.assert_awaited_once()
        assert routes.read.call_count == 0
        assert routes.writes == 0
        assert _ineligible(caplog) == [(category, _BY_ID)]

    async def test_non_v3_source_is_not_v3(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A legacy-sourced entry never reads by ID."""
        await _setup_entry(
            hass, options={CONF_RESERVATIONS_API: RESERVATIONS_API_LEGACY}
        )
        await self._expect_ineligible(hass, caplog, "not_v3")

    @pytest.mark.parametrize(
        "fields",
        [
            {"fallback_served": (_BY_ID,)},
            {"fallback_sourced": (_BY_ID,)},
            {"fallback_observed": (_BY_ID,)},
        ],
        ids=["live-served", "live-sourced", "snapshot-observed"],
    )
    async def test_fallback_provenance(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        fields: dict[str, tuple[str, ...]],
    ) -> None:
        """Provenance added by the qualifying refresh refuses the read."""
        await _setup_entry(hass)
        refresh = _refresh(make_identity_reservation(_BY_ID), **fields)
        await self._expect_ineligible(
            hass, caplog, "fallback_provenance", refresh=refresh
        )

    @pytest.mark.parametrize("field", ["fallback_served", "fallback_sourced"])
    async def test_snapshot_only_fallback_provenance(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture, field: str
    ) -> None:
        """A retired refresh's snapshot provenance alone refuses."""
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)
        coordinator.fallback_served_ids = frozenset({"res-other"})
        refresh = _refresh(**{field: (_BY_ID,)})
        with patch.object(
            type(coordinator.api_client),
            "legacy_write_routing_available",
            property(lambda _self: False),
        ):
            await self._expect_ineligible(
                hass, caplog, "fallback_provenance", refresh=refresh
            )
        assert _BY_ID not in (
            coordinator.fallback_served_ids | coordinator.fallback_sourced_ids
        )

    async def test_session_legacy_observation_is_provenance(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An earlier refresh's legacy observation alone refuses."""
        entry = await _setup_entry(hass)
        client = _by_id_coordinator(hass, entry).api_client
        with patch.object(
            type(client),
            "legacy_observed_reservations",
            property(lambda _self: frozenset({_BY_ID})),
        ):
            await self._expect_ineligible(hass, caplog, "fallback_provenance")

    async def test_incomplete_session_observed_set_is_evidence_unavailable(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An overflowed legacy-observed set cannot clear provenance."""
        entry = await _setup_entry(hass)
        client = _by_id_coordinator(hass, entry).api_client
        with patch.object(
            type(client), "legacy_observed_reservations", property(lambda _self: None)
        ):
            await self._expect_ineligible(hass, caplog, "evidence_unavailable")

    @pytest.mark.parametrize("stale", [False, True], ids=["missing", "stale"])
    async def test_missing_or_stale_snapshot_is_evidence_unavailable(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture, stale: bool
    ) -> None:
        """Without the accepted refresh's snapshot nothing is read."""
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)
        original = coordinator.async_refresh_for_write_miss

        async def drop_snapshot(generation: int) -> bool:
            """Complete the refresh, then lose or outdate the snapshot."""
            result: bool = await original(generation)
            if stale:
                latest = coordinator._write_miss_outcomes[-1]
                coordinator._write_miss_outcomes.append(
                    (latest[0] + 1, latest[1] + 1, *latest[2:])
                )
            else:
                coordinator._accepted_snapshot = None
            return result

        with patch.object(
            coordinator, "async_refresh_for_write_miss", side_effect=drop_snapshot
        ):
            await self._expect_ineligible(hass, caplog, "evidence_unavailable")

    async def test_rejected_in_refresh_is_rejected(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A returned but rejected target reports ``rejected``."""
        await _setup_entry(hass)
        refresh = _refresh(returned_ids=(_BY_ID,), rejected_ids=(_BY_ID,))
        await self._expect_ineligible(hass, caplog, "rejected", refresh=refresh)

    @pytest.mark.parametrize("case", ["evicted", "alteration", "invalid"])
    async def test_not_qualifying(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture, case: str
    ) -> None:
        """A returned record that is not code-less stays with 012."""
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)
        target = make_identity_reservation(_BY_ID, "CONF-res-new")
        if case == "alteration":
            target = replace(target, status="alteration", confirmation_code=None)
        elif case == "invalid":
            target = replace(target, confirmation_code=7)  # type: ignore[arg-type]
        refresh = _refresh(target, returned_ids=(_BY_ID,))
        original = coordinator.async_refresh_for_write_miss

        async def evict(generation: int) -> bool:
            """Complete the refresh, then evict the target's code."""
            result: bool = await original(generation)
            coordinator.identity.baseline.codes.pop("CONF-res-new", None)
            return result

        with patch.object(
            coordinator, "async_refresh_for_write_miss", side_effect=evict
        ):
            await self._expect_ineligible(
                hass, caplog, "not_qualifying", refresh=refresh
            )

    @pytest.mark.parametrize("source", ["live", "snapshot", "session"])
    async def test_confirmed_absent(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture, source: str
    ) -> None:
        """Legacy-confirmed absence refuses the read."""
        entry = await _setup_entry(hass)
        client = _by_id_coordinator(hass, entry).api_client
        refresh = _refresh(fallback_absent=(_BY_ID,) if source == "snapshot" else ())
        name = {
            "live": "confirmed_absent_reservations",
            "snapshot": "legacy_retired_reservations",
            "session": "legacy_retired_reservations",
        }[source]
        value = frozenset() if source == "snapshot" else frozenset({_BY_ID})
        with patch.object(type(client), name, property(lambda _self: value)):
            await self._expect_ineligible(
                hass, caplog, "confirmed_absent", refresh=refresh
            )

    async def test_incomplete_session_retired_set_is_evidence_unavailable(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An overflowed legacy-retired set cannot clear absence."""
        entry = await _setup_entry(hass)
        client = _by_id_coordinator(hass, entry).api_client
        with patch.object(
            type(client), "legacy_retired_reservations", property(lambda _self: None)
        ):
            await self._expect_ineligible(hass, caplog, "evidence_unavailable")

    async def test_adapter_unavailable_or_over_bound_is_evidence_unavailable(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An unavailable 013 adapter refuses the read."""
        await _setup_entry(hass)
        await self._expect_ineligible(
            hass,
            caplog,
            "evidence_unavailable",
            evidence=FakeConflictEvidence(available=False),
        )

    async def test_conflict_only_evidence_for_target_is_rejected(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Conflict-only evidence about the target refuses the read."""
        await _setup_entry(hass)
        await self._expect_ineligible(
            hass,
            caplog,
            "rejected",
            evidence=FakeConflictEvidence(codes={_BY_ID: frozenset({"CONF-X"})}),
        )


_RECORD = make_by_id_record()
_MALFORMED_BODIES: dict[str, Any] = {
    "two-records": {"results": [_RECORD, make_by_id_record("res-zzz")]},
    "duplicate-matches": {"results": [_RECORD, _RECORD]},
    "bare-dict": _RECORD,
    "bare-list": [_RECORD],
    "non-list-results": {"results": {"0": _RECORD}},
    "has-more": {"results": [_RECORD], "pagination": {"hasMore": True}},
    "pagination-null": {"results": [_RECORD], "pagination": None},
    "pagination-list": {"results": [_RECORD], "pagination": []},
    "pagination-empty": {"results": [_RECORD], "pagination": {}},
    "has-more-null": {"results": [_RECORD], "pagination": {"hasMore": None}},
    "has-more-string": {"results": [_RECORD], "pagination": {"hasMore": "true"}},
}


class TestByIdReadOutcomes:
    """Every by-ID read answer maps to one outcome (T028)."""

    @_BY_ID_SERVICES
    @pytest.mark.parametrize(
        ("body", "category"),
        [
            ({"results": []}, "not_found"),
            ({"results": [make_by_id_record("res-zzz")]}, "mismatched"),
        ],
        ids=["empty", "mismatched"],
    )
    async def test_not_found(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        body: dict[str, Any],
        category: str,
    ) -> None:
        """An empty or mismatched answer is not found by ID."""
        await _setup_entry(hass)
        with by_id_network(
            refresh=_refresh(),
            read=Response(200, json=body),
            evidence=FakeConflictEvidence(),
        ) as routes:
            text = await _by_id_refusal(hass, service, payload)

        assert text.startswith("not found by ID")
        assert category in text
        assert routes.read.call_count == 1
        assert routes.writes == 0

    @_BY_ID_SERVICES
    @pytest.mark.parametrize("body", list(_MALFORMED_BODIES))
    async def test_unprovable_answer_is_malformed(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        body: str,
    ) -> None:
        """An answer that cannot prove one record is refused."""
        await _setup_entry(hass)
        with by_id_network(
            refresh=_refresh(),
            read=Response(200, json=_MALFORMED_BODIES[body]),
            evidence=FakeConflictEvidence(),
        ) as routes:
            text = await _by_id_refusal(hass, service, payload)

        assert text.startswith("found but refused")
        assert "malformed" in text
        assert routes.read.call_count == 1
        assert routes.writes == 0

    async def test_non_json_is_malformed(self, hass: HomeAssistant) -> None:
        """A non-JSON answer is refused as malformed."""
        await _setup_entry(hass)
        with by_id_network(
            refresh=_refresh(),
            read=Response(200, content=b"<html>"),
            evidence=FakeConflictEvidence(),
        ) as routes:
            text = await _by_id_refusal(
                hass, "set_reservation_status", {"status": "checked_in"}
            )

        assert text.startswith("found but refused")
        assert "malformed" in text
        assert routes.writes == 0

    @_BY_ID_SERVICES
    @pytest.mark.parametrize(
        ("read", "category"),
        [
            (Response(400), "http"),
            (Response(401), "authentication"),
            (Response(403), "authentication"),
            (Response(404), "http"),
            (Response(429), "rate_limit"),
            (Response(500), "http"),
            (Response(503), "http"),
            (httpx.ReadTimeout("timed out"), "transport"),
            (httpx.ConnectError("refused"), "transport"),
        ],
        ids=["400", "401", "403", "404", "429", "500", "503", "timeout", "connect"],
    )
    async def test_read_failure(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        read: Any,
        category: str,
    ) -> None:
        """A failed read is reported once, unretried and unrenewed."""
        await _setup_entry(hass)
        with (
            by_id_network(
                refresh=_refresh(), read=read, evidence=FakeConflictEvidence()
            ) as routes,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            text = await _by_id_refusal(hass, service, payload)

        assert text.startswith("by-ID read failed")
        assert category in text
        assert routes.read.call_count == 1
        assert routes.token.call_count == 1
        assert routes.writes == 0

    @_BY_ID_SERVICES
    @pytest.mark.parametrize(
        ("error", "category"),
        [
            (GuestyAuthError("rejected"), "authentication"),
            (GuestyRateLimitError("budget"), "rate_limit"),
            (GuestyResponseError("token 429"), "http"),
            (GuestyConnectionError("down"), "transport"),
        ],
        ids=["rejected", "budget", "endpoint", "connect"],
    )
    async def test_token_failure_sends_no_read(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        error: Exception,
        category: str,
    ) -> None:
        """A token failure exits before the read is sent."""
        entry = await _setup_entry(hass)
        manager = hass.data[DOMAIN][entry.entry_id]["api_client"]._token_manager
        with (
            by_id_network(
                refresh=_refresh(), evidence=FakeConflictEvidence()
            ) as routes,
            patch.object(manager, "get_token", side_effect=error),
        ):
            text = await _by_id_refusal(hass, service, payload)

        assert text.startswith("by-ID read failed")
        assert category in text
        assert routes.read.call_count == 0
        assert routes.writes == 0


class TestByIdAuthorizationRefusals:
    """A found record must prove itself before any write (T029)."""

    @staticmethod
    async def _setup(hass: HomeAssistant) -> Any:
        """Load an entry with verified continuity.

        Args:
            hass: Home Assistant instance.

        Returns:
            The entry's coordinator.
        """
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)
        coordinator.identity.baseline.continuity = CONTINUITY_VERIFIED
        return coordinator

    @staticmethod
    async def _refused(
        hass: HomeAssistant,
        *,
        read: Any = None,
        record: dict[str, Any] | None = None,
        evidence: FakeConflictEvidence | None = None,
        refresh: ReservationFetchResult | None = None,
        service: str = "set_reservation_status",
        payload: dict[str, str] | None = None,
    ) -> str:
        """Run one authorization refusal and check nothing was written.

        Args:
            hass: Home Assistant instance.
            read: The by-ID response or side effect.
            record: The by-ID record, when ``read`` is None.
            evidence: The 013 double; available if None.
            refresh: The qualifying refresh; target absent if None.
            service: The service to call.
            payload: The service fields other than the identifier.

        Returns:
            The surfaced error text.
        """
        if read is None and record is not None:
            read = Response(200, json={"results": [record]})
        with by_id_network(
            refresh=refresh or _refresh(),
            read=read,
            evidence=evidence or FakeConflictEvidence(),
        ) as routes:
            text = await _by_id_refusal(
                hass, service, payload or {"status": "checked_in"}
            )
        assert routes.read.call_count == 1
        assert routes.writes == 0
        assert text.startswith("found but refused")
        return text

    @pytest.mark.parametrize(
        "status",
        ["alteration", "SENTINEL-STATUS-9F", None, 7, ["confirmed"], {"s": 1}],
        ids=["alteration", "unknown", "missing", "number", "list", "object"],
    )
    async def test_disallowed_status(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        status: object,
    ) -> None:
        """Only an allowed raw status may authorize a write.

        Feature 013 routes parsing through ``validate_record`` and the
        validation assessment, so the refusal must also happen before
        the evidence validator or ``from_v3_api_dict`` sees the record.
        """
        await self._setup(hass)
        record = make_by_id_record()
        if status is None:
            del record["status"]
        else:
            record["status"] = status
        caplog.set_level(logging.DEBUG)
        evidence = FakeConflictEvidence()
        with (
            patch("custom_components.guesty.api.models.map_v3_status") as mapped,
            patch.object(
                GuestyReservation,
                "from_v3_api_dict",
                wraps=GuestyReservation.from_v3_api_dict,
            ) as parsed,
        ):
            text = await self._refused(hass, record=record, evidence=evidence)

        assert "disallowed_status" in text
        mapped.assert_not_called()
        parsed.assert_not_called()
        assert not [c for c in evidence.calls if c[0] == "validate_record"]
        assert "SENTINEL-STATUS-9F" not in caplog.text
        assert "SENTINEL-STATUS-9F" not in text

    async def test_validator_rejection_is_malformed(self, hass: HomeAssistant) -> None:
        """The 013 validator's rejection refuses the record."""
        await self._setup(hass)
        text = await self._refused(
            hass, evidence=FakeConflictEvidence(verdict="segment")
        )
        assert "malformed" in text

    async def test_unparseable_record_is_malformed(self, hass: HomeAssistant) -> None:
        """A record the v3 parser rejects is refused."""
        await self._setup(hass)
        text = await self._refused(hass, record=make_by_id_record(stay=[]))
        assert "malformed" in text

    async def test_non_object_notes_on_note_write_is_malformed(
        self, hass: HomeAssistant
    ) -> None:
        """A note write needs an object of note fields."""
        await self._setup(hass)
        text = await self._refused(
            hass,
            record=make_by_id_record(notes="SENTINEL-NOTE"),
            service="add_reservation_note",
            payload={"note_text": "hello"},
        )
        assert "malformed" in text

    @pytest.mark.parametrize(
        "code", [None, "", 7, ["CONF-res-new"]], ids=["missing", "empty", "num", "list"]
    )
    async def test_no_code(self, hass: HomeAssistant, code: object) -> None:
        """A record without a usable code is refused."""
        await self._setup(hass)
        record = make_by_id_record()
        if code is None:
            del record["confirmationCode"]
        else:
            record["confirmationCode"] = code
        text = await self._refused(hass, record=record)
        assert "no_code" in text

    @pytest.mark.parametrize(
        "where",
        ["codes", "legacy_codes", "bootstrap_codes", "fallback_codes", "conflict_only"],
    )
    async def test_code_mapped_to_other_id(
        self, hass: HomeAssistant, where: str
    ) -> None:
        """The code naming another reservation anywhere refuses."""
        coordinator = await self._setup(hass)
        baseline = coordinator.identity.baseline
        evidence = FakeConflictEvidence()
        if where in {"legacy_codes", "bootstrap_codes"}:
            setattr(baseline, where, {"CONF-res-new": "res-other"})
        elif where == "conflict_only":
            evidence.ids["CONF-res-new"] = frozenset({"res-other"})

        def inject(request: httpx.Request) -> Response:
            """Add the conflicting entry during the read."""
            if where in {"codes", "fallback_codes"}:
                live = coordinator.identity.baseline
                setattr(
                    live,
                    where,
                    {**(getattr(live, where) or {}), "CONF-res-new": "res-other"},
                )
            return Response(200, json={"results": [make_by_id_record()]})

        text = await self._refused(hass, read=inject, evidence=evidence)
        assert "identity_conflict" in text

    @pytest.mark.parametrize(
        "where",
        ["codes", "legacy_codes", "bootstrap_codes", "fallback_codes", "conflict_only"],
    )
    async def test_target_mapped_to_other_code(
        self, hass: HomeAssistant, where: str
    ) -> None:
        """Another code naming the target anywhere refuses."""
        coordinator = await self._setup(hass)
        baseline = coordinator.identity.baseline
        evidence = FakeConflictEvidence()
        if where in {"legacy_codes", "bootstrap_codes"}:
            setattr(baseline, where, {"CONF-OLD": _BY_ID})

        def inject(request: httpx.Request) -> Response:
            """Add the conflicting entry during the read."""
            if where in {"codes", "fallback_codes"}:
                live = coordinator.identity.baseline
                setattr(
                    live, where, {**(getattr(live, where) or {}), "CONF-OLD": _BY_ID}
                )
            elif where == "conflict_only":
                evidence.codes[_BY_ID] = frozenset({"CONF-OLD"})
            return Response(200, json={"results": [make_by_id_record()]})

        text = await self._refused(hass, read=inject, evidence=evidence)
        assert "identity_conflict" in text

    @staticmethod
    def _install(coordinator: Any, **fields: Any) -> AcceptedRefreshSnapshot:
        """Install a crafted snapshot valid for the latest refresh.

        Args:
            coordinator: The entry's coordinator.
            **fields: Snapshot fields to replace.

        Returns:
            The installed snapshot.
        """
        current = coordinator.accepted_refresh_snapshot()
        assert isinstance(current, AcceptedRefreshSnapshot)
        crafted = replace(current, **fields)
        coordinator._accepted_snapshot = crafted
        installed = coordinator.accepted_refresh_snapshot()
        assert installed is crafted
        return installed

    @pytest.mark.parametrize("conflict", [True, False])
    @pytest.mark.parametrize("field", ["observed_codes", "fallback_observed_codes"])
    @pytest.mark.parametrize("direction", ["code", "target"])
    async def test_authorize_rejects_mapped_in_snapshot_maps(
        self, hass: HomeAssistant, field: str, direction: str, conflict: bool
    ) -> None:
        """Snapshot code maps are conflict evidence in both directions."""
        coordinator = await self._setup(hass)
        entry = (
            {"CONF-res-new": "res-other"}
            if direction == "code"
            else {"CONF-OLD": _BY_ID}
        )
        snapshot = self._install(
            coordinator,
            **{field: MappingProxyType(entry if conflict else {})},
        )

        def authorize() -> object:
            """Authorize the default record against the snapshot."""
            return _authorize_by_id_record(
                coordinator=coordinator,
                reservation_id=_BY_ID,
                action_type="set_reservation_status",
                qualifier="absent",
                raw=make_by_id_record(),
                snapshot=snapshot,
                evidence_port=FakeConflictEvidence(),
            )

        if not conflict:
            assert authorize() is not None
            return
        with pytest.raises(ReservationByIdOutcomeError) as error:
            authorize()
        assert error.value.category == "identity_conflict"

    async def test_code_duplicating_another_ids_code_is_conflict(
        self, hass: HomeAssistant
    ) -> None:
        """Another reservation's code refuses and changes no state."""
        coordinator = await self._setup(hass)
        identity = coordinator.identity
        text = await self._refused(
            hass, record=make_by_id_record(confirmationCode="CONF-res-001")
        )
        assert "identity_conflict" in text
        assert identity._ambiguous is False
        assert identity.baseline.continuity == CONTINUITY_VERIFIED

    @pytest.mark.parametrize(
        "change", ["unusable", "unstored", "adapter", "cleared", "replaced"]
    )
    async def test_unavailable_conflict_source_refuses(
        self, hass: HomeAssistant, change: str
    ) -> None:
        """Conflict evidence lost during the read refuses."""
        coordinator = await self._setup(hass)
        evidence = FakeConflictEvidence()

        def inject(request: httpx.Request) -> Response:
            """Lose one conflict source during the read."""
            identity = coordinator.identity
            if change == "unusable":
                live = identity.baseline
                live.codes = {}
                live.legacy_codes = live.bootstrap_codes = live.fallback_codes = None
            elif change == "unstored":
                identity._unstored = True
            elif change == "adapter":
                evidence.is_available = False
            elif change == "cleared":
                coordinator._accepted_snapshot = None
            else:
                coordinator._accepted_snapshot = replace(coordinator._accepted_snapshot)
            return Response(200, json={"results": [make_by_id_record()]})

        text = await self._refused(hass, read=inject, evidence=evidence)
        assert "identity_conflict" in text

    @pytest.mark.parametrize(
        ("segment", "category"),
        [
            (
                {"listingId": "listing-001", "parentListingId": "parent-x"},
                "identity_conflict",
            ),
            ({"listingId": "listing-other"}, "identity_conflict"),
            ({"listingId": "listing-001", "parentListingId": 7}, "malformed"),
        ],
        ids=["parent", "unit", "non-string-parent"],
    )
    async def test_codeless_listing_disagreement_is_identity_conflict(
        self, hass: HomeAssistant, segment: dict[str, Any], category: str
    ) -> None:
        """A code-less target's by-ID listings must match the search.

        Feature 013 (research R3) supersedes 014 T029/T033 for the
        ``non-string-parent`` case: a non-string ``parentListingId`` is
        ``segment_malformed_parent``, so the single-segment record is
        rejected, ``from_v3_api_dict`` returns None and
        ``_authorize_by_id_record`` refuses it as ``malformed``.
        """
        await self._setup(hass)
        record = make_by_id_record(stay=[make_v3_segment(**segment)])
        text = await self._refused(hass, record=record, refresh=_codeless_refresh())
        assert category in text
        if category == "identity_conflict":
            assert "present without a confirmation code" in text

    async def test_unverifiable_snapshot_listing_pairs_refuse(
        self, hass: HomeAssistant
    ) -> None:
        """A snapshot record without verified listings refuses."""
        coordinator = await self._setup(hass)
        snapshot = self._install(
            coordinator,
            records=MappingProxyType(
                {
                    _BY_ID: SnapshotRecord(
                        code_kind="none", hold=False, listing_ids=None, v3=True
                    )
                }
            ),
        )
        with pytest.raises(ReservationByIdOutcomeError) as error:
            _authorize_by_id_record(
                coordinator=coordinator,
                reservation_id=_BY_ID,
                action_type="set_reservation_status",
                qualifier="codeless",
                raw=make_by_id_record(),
                snapshot=snapshot,
                evidence_port=FakeConflictEvidence(),
            )
        assert error.value.category == "identity_conflict"

    async def test_returned_codeless_outside_selection_gets_listing_check(
        self, hass: HomeAssistant
    ) -> None:
        """An unpublished code-less record still gets the listing check."""
        coordinator = await self._setup(hass)
        record = make_by_id_record(stay=[make_v3_segment(listingId="listing-other")])
        text = await self._refused(hass, record=record, refresh=_codeless_refresh())
        assert "identity_conflict" in text
        assert "present without a confirmation code" in text
        assert not any(
            reservation.id == _BY_ID
            for reservations in (coordinator.data or {}).values()
            for reservation in reservations
        )


def _written_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Collect the by-ID success INFO lines.

    Args:
        caplog: The log capture.

    Returns:
        Every INFO message reporting a by-ID write.
    """
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == _ACTIONS_LOGGER
        and record.levelno == logging.INFO
        and "found by ID, written" in record.getMessage()
    ]


class TestByIdWriteFallback223:
    """The #223 same-day reservation is written by ID (T036)."""

    @pytest.mark.parametrize(
        "service", ["add_reservation_note", "set_reservation_status"]
    )
    async def test_223_same_day_absent_reservation_written_by_id(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
        service: str,
    ) -> None:
        """One search, one by-ID read and one write, retaining nothing."""
        freezer.move_to("2026-11-23 18:00:00+00:00")
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)
        evidence = FakeConflictEvidence()
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

        payload = (
            {"note_text": "hello"}
            if service == "add_reservation_note"
            else {"status": "checked_in"}
        )
        caplog.set_level(logging.INFO, logger=_ACTIONS_LOGGER)
        store = coordinator.identity._store
        with (
            patch.object(store, "async_save", wraps=store.async_save) as save,
            by_id_network(refresh=_refresh(), read=read, evidence=evidence) as routes,
        ):
            result = await _call_by_id(hass, service, payload)
            after = capture_by_id_retention(coordinator, evidence)
            after["saves"] = save.await_count

        assert result == {"success": True, "target_id": _BY_ID}
        routes.fetch.assert_awaited_once()
        assert routes.read.call_count == 1
        assert routes.read.calls[0].request.url.params["reservationIds"] == _BY_ID
        assert routes.token.call_count <= 1
        assert routes.writes == 1
        if service == "add_reservation_note":
            body = json.loads(routes.notes.calls[0].request.content)
            assert body == {"other": f"Late arrival{NOTE_SEPARATOR}hello"}
        else:
            assert routes.status.call_count == 1
        lines = _written_lines(caplog)
        assert len(lines) == 1
        assert "absent" in lines[0]
        assert after == captured


class TestByIdWriteFallbackVariants:
    """Code-less, out-of-window and edge-case targets (T038)."""

    @_BY_ID_SERVICES
    @pytest.mark.parametrize("code", [None, ""], ids=["missing", "empty"])
    async def test_codeless_target_written_by_id(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        service: str,
        payload: dict[str, str],
        code: str | None,
    ) -> None:
        """A returned code-less target is written with its qualifier."""
        await _setup_entry(hass)
        caplog.set_level(logging.INFO, logger=_ACTIONS_LOGGER)
        with by_id_network(
            refresh=_codeless_refresh(code), evidence=FakeConflictEvidence()
        ) as routes:
            result = await _call_by_id(hass, service, payload)

        assert result == {"success": True, "target_id": _BY_ID}
        assert routes.read.call_count == 1
        assert routes.writes == 1
        lines = _written_lines(caplog)
        assert len(lines) == 1
        assert "codeless" in lines[0]

    async def test_absent_target_outside_listing_selection_or_window_is_written(
        self, hass: HomeAssistant
    ) -> None:
        """An absent target is written whatever its listing or dates."""
        await _setup_entry(hass)
        segment = make_v3_segment(
            listingId="listing-elsewhere",
            checkIn="2020-01-01T15:00:00+00:00",
            checkOut="2020-01-03T11:00:00+00:00",
            checkInDateLocalized="2020-01-01",
            checkOutDateLocalized="2020-01-03",
        )
        read = Response(200, json={"results": [make_by_id_record(stay=[segment])]})
        with by_id_network(
            refresh=_refresh(), read=read, evidence=FakeConflictEvidence()
        ) as routes:
            await _call_by_id(hass, "set_reservation_status", {"status": "checked_in"})

        assert routes.status.call_count == 1

    async def test_returned_coded_target_outside_selection_takes_012_path(
        self, hass: HomeAssistant
    ) -> None:
        """A refresh that supplies the code needs no by-ID read."""
        await _setup_entry(hass)
        refresh = _refresh(
            make_identity_reservation(_BY_ID, "CONF-res-new"), returned_ids=(_BY_ID,)
        )
        with by_id_network(refresh=refresh, evidence=FakeConflictEvidence()) as routes:
            await _call_by_id(hass, "set_reservation_status", {"status": "checked_in"})

        assert routes.read.call_count == 0
        assert routes.status.call_count == 1

    @pytest.mark.parametrize("status", ["completed", "cancelled"])
    async def test_completed_and_cancelled_statuses_are_allowed(
        self, hass: HomeAssistant, status: str
    ) -> None:
        """Statuses a refresh accepts are allowed by ID too."""
        await _setup_entry(hass)
        read = Response(200, json={"results": [make_by_id_record(status=status)]})
        with by_id_network(
            refresh=_refresh(), read=read, evidence=FakeConflictEvidence()
        ) as routes:
            await _call_by_id(hass, "set_reservation_status", {"status": "checked_in"})

        assert routes.status.call_count == 1

    async def test_none_sub_maps_do_not_refuse(self, hass: HomeAssistant) -> None:
        """A missing legacy, bootstrap or fallback map is no evidence."""
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)

        def read(request: httpx.Request) -> Response:
            """Drop every optional map during the read."""
            baseline = coordinator.identity.baseline
            baseline.legacy_codes = baseline.bootstrap_codes = None
            baseline.fallback_codes = None
            return Response(200, json={"results": [make_by_id_record()]})

        with by_id_network(
            refresh=_refresh(), read=read, evidence=FakeConflictEvidence()
        ) as routes:
            await _call_by_id(hass, "set_reservation_status", {"status": "checked_in"})

        assert routes.status.call_count == 1

    @pytest.mark.parametrize("notes", ["missing", None])
    async def test_note_on_record_without_notes_writes_only_other(
        self, hass: HomeAssistant, notes: str | None
    ) -> None:
        """A record without notes gets only the appended text."""
        await _setup_entry(hass)
        record = make_by_id_record()
        if notes == "missing":
            del record["notes"]
        else:
            record["notes"] = None
        read = Response(200, json={"results": [record]})
        with by_id_network(
            refresh=_refresh(), read=read, evidence=FakeConflictEvidence()
        ) as routes:
            await _call_by_id(hass, "add_reservation_note", {"note_text": "hello"})

        assert routes.read.call_count == 1
        assert json.loads(routes.notes.calls[0].request.content) == {"other": "hello"}


def _inject_after_authorization(
    hass: HomeAssistant, entry: MockConfigEntry, change: Callable[[], None]
) -> Any:
    """Patch token acquisition to run a change before the write's recheck.

    The read acquires the token first; the write's request acquires
    it again just before ``before_send``.

    Args:
        hass: Home Assistant instance.
        entry: The loaded entry.
        change: The state change to inject.

    Returns:
        The active patcher.
    """
    manager = hass.data[DOMAIN][entry.entry_id]["api_client"]._token_manager
    original = manager.get_token
    calls = 0

    async def get_token(*args: Any, **kwargs: Any) -> str:
        """Inject the change on the write's token acquisition."""
        nonlocal calls
        calls += 1
        if calls == 2:
            change()
        token: str = await original(*args, **kwargs)
        return token

    return patch.object(manager, "get_token", side_effect=get_token)


def _newer_outcome(coordinator: Any, *, success: bool = True) -> int:
    """Append an injected newer refresh outcome.

    Args:
        coordinator: The entry's coordinator.
        success: Whether the injected refresh succeeded.

    Returns:
        The injected attempt.
    """
    latest = coordinator._write_miss_outcomes[-1]
    attempt = latest[1] + 1
    coordinator._write_miss_outcomes.append(
        (latest[0] + 1, attempt, success, *latest[3:])
    )
    return int(attempt)


class TestByIdPreWriteRecheck:
    """The write rechecks everything just before sending (T039)."""

    @pytest.mark.parametrize("change", ["reload", "options", "source"])
    async def test_change_during_read_refuses(
        self, hass: HomeAssistant, change: str
    ) -> None:
        """Entry, options or source changes during the read refuse."""
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)

        def read(request: httpx.Request) -> Response:
            """Change the context while the read is in flight."""
            if change == "reload":
                hass.data[DOMAIN][entry.entry_id] = dict(
                    hass.data[DOMAIN][entry.entry_id]
                )
            elif change == "options":
                hass.config_entries.async_update_entry(
                    entry, options={**entry.options, "unrelated_option": True}
                )
            else:
                coordinator.reservations_path = RESERVATIONS_API_LEGACY
            return Response(200, json={"results": [make_by_id_record()]})

        with by_id_network(
            refresh=_refresh(), read=read, evidence=FakeConflictEvidence()
        ) as routes:
            text = await _by_id_refusal(
                hass, "set_reservation_status", {"status": "checked_in"}
            )

        assert text.startswith("found but refused")
        assert "guard_witness" in text
        assert routes.read.call_count == 1
        assert routes.writes == 0

    @_BY_ID_SERVICES
    @pytest.mark.parametrize(
        "change",
        [
            "unstored",
            "unconfirmed",
            "ambiguous",
            "newer-failed",
            "newer-ambiguous",
            "newer-fallback",
        ],
    )
    async def test_change_before_write_refuses(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        change: str,
    ) -> None:
        """A change after authorization refuses with no write."""
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)

        def inject() -> None:
            """Apply the change between authorization and sending."""
            identity = coordinator.identity
            if change == "unstored":
                identity._unstored = True
            elif change == "unconfirmed":
                identity.baseline.continuity = CONTINUITY_UNCONFIRMED
            elif change == "ambiguous":
                identity._ambiguous = True
            elif change == "newer-failed":
                _newer_outcome(coordinator, success=False)
            elif change == "newer-ambiguous":
                _newer_outcome(coordinator)
                identity._ambiguous = True
            else:
                attempt = _newer_outcome(coordinator)
                coordinator._accepted_snapshot = replace(
                    coordinator._accepted_snapshot,
                    attempt=attempt,
                    fallback_sourced=frozenset({_BY_ID}),
                )
                coordinator.fallback_sourced_ids = frozenset({_BY_ID})
                baseline = identity.baseline
                baseline.fallback_codes = {
                    **(baseline.fallback_codes or {}),
                    "CONF-res-new": _BY_ID,
                }

        with (
            by_id_network(
                refresh=_refresh(), evidence=FakeConflictEvidence()
            ) as routes,
            _inject_after_authorization(hass, entry, inject),
        ):
            text = await _by_id_refusal(hass, service, payload)

        assert text.startswith("found but refused")
        assert "guard_witness" in text
        assert routes.read.call_count == 1
        assert routes.writes == 0

    @_BY_ID_SERVICES
    async def test_newer_accepted_refresh_with_v3_code(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        service: str,
        payload: dict[str, str],
    ) -> None:
        """A newer refresh coding the target lets only a status write go."""
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)

        def inject() -> None:
            """Accept a newer refresh that codes the target on v3."""
            attempt = _newer_outcome(coordinator)
            coordinator._accepted_snapshot = replace(
                coordinator._accepted_snapshot, attempt=attempt
            )
            coordinator.identity.baseline.codes["CONF-res-new"] = _BY_ID

        caplog.set_level(logging.INFO, logger=_ACTIONS_LOGGER)
        with (
            by_id_network(
                refresh=_refresh(), evidence=FakeConflictEvidence()
            ) as routes,
            _inject_after_authorization(hass, entry, inject),
        ):
            if service == "add_reservation_note":
                text = await _by_id_refusal(hass, service, payload)
                assert text.startswith("found but refused")
                assert "guard_witness" in text
                assert routes.writes == 0
            else:
                result = await _call_by_id(hass, service, payload)
                assert result == {"success": True, "target_id": _BY_ID}
                assert routes.status.call_count == 1
                assert len(_written_lines(caplog)) == 1
        assert routes.read.call_count == 1


class TestByIdWriteFailure:
    """A failed write is reported once and never retried (T040)."""

    @_BY_ID_SERVICES
    @pytest.mark.parametrize(
        ("write", "hint"),
        [
            (Response(400), "rejected"),
            (Response(500), "rejected"),
            (httpx.ReadTimeout("timed out"), "may have reached Guesty"),
        ],
        ids=["400", "500", "timeout"],
    )
    async def test_write_failure(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        write: Any,
        hint: str,
    ) -> None:
        """One PUT, no retry, and a found-by-ID write failure."""
        await _setup_entry(hass)
        with (
            by_id_network(
                refresh=_refresh(), write=write, evidence=FakeConflictEvidence()
            ) as routes,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            text = await _by_id_refusal(hass, service, payload)

        assert text.startswith("found by ID, write failed")
        assert "absent from search" in text
        assert hint in text
        assert "not retried" in text
        assert routes.read.call_count == 1
        assert routes.writes == 1


class TestByIdOutcomeMessages:
    """Every outcome is distinguishable and actionable (T044)."""

    @pytest.mark.parametrize("qualifier", ["absent", "codeless"])
    @pytest.mark.parametrize(
        ("outcome", "stem", "hint"),
        [
            ("not_found", "not found by ID", "retry after the next refresh"),
            ("refused", "found but refused", "retry after the next refresh"),
            (
                "read_failed",
                "by-ID read failed",
                "Guesty rate-limited the lookup; retry later",
            ),
            ("write_failed", "found by ID, write failed", "Check Guesty"),
        ],
    )
    async def test_outcome_text_is_distinguishable(
        self,
        hass: HomeAssistant,
        qualifier: str,
        outcome: str,
        stem: str,
        hint: str,
    ) -> None:
        """Each class has its own stem, qualifier, identifier and hint."""
        await _setup_entry(hass)
        record = make_by_id_record()
        read: Any = None
        write: Any = None
        if outcome == "not_found":
            read = Response(200, json={"results": []})
        elif outcome == "refused":
            del record["confirmationCode"]
            read = Response(200, json={"results": [record]})
        elif outcome == "read_failed":
            read = Response(429)
        else:
            write = Response(500)
        refresh = _refresh() if qualifier == "absent" else _codeless_refresh()
        with (
            by_id_network(
                refresh=refresh,
                read=read,
                write=write,
                evidence=FakeConflictEvidence(),
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            text = await _by_id_refusal(
                hass, "set_reservation_status", {"status": "checked_in"}
            )

        assert text.startswith(stem)
        assert {
            "absent": "absent from search",
            "codeless": "present without a confirmation code",
        }[qualifier] in text
        assert _BY_ID in text
        assert hint in text

    @pytest.mark.parametrize("qualifier", ["absent", "codeless"])
    async def test_success_logs_exactly_one_info_line(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        qualifier: str,
    ) -> None:
        """A by-ID write logs one INFO line naming only safe facts."""
        await _setup_entry(hass)
        refresh = _refresh() if qualifier == "absent" else _codeless_refresh()
        caplog.set_level(logging.DEBUG, logger=_ACTIONS_LOGGER)
        with by_id_network(refresh=refresh, evidence=FakeConflictEvidence()):
            await _call_by_id(hass, "set_reservation_status", {"status": "checked_in"})

        info = [
            record
            for record in caplog.records
            if record.name == _ACTIONS_LOGGER and record.levelno >= logging.INFO
        ]
        assert len(info) == 1
        message = info[0].getMessage()
        assert "found by ID, written" in message
        assert qualifier in message
        assert _BY_ID in message
        assert "set_reservation_status" in message
        assert "CONF-" not in message

    async def test_not_eligible_adds_no_new_message(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An ineligible miss surfaces exactly the 012 refusal."""
        await _setup_entry(hass)
        caplog.set_level(logging.INFO, logger=_ACTIONS_LOGGER)
        with by_id_network(
            refresh=_refresh(), evidence=FakeConflictEvidence(available=False)
        ):
            text = await _by_id_refusal(
                hass, "set_reservation_status", {"status": "checked_in"}
            )

        assert text == REFUSAL_NO_CODE.format(reservation_id=_BY_ID)
        assert not [
            r
            for r in caplog.records
            if r.name == _ACTIONS_LOGGER and r.levelno >= logging.INFO
        ]


class TestByIdOverlap:
    """Overlapping actions decide independently (T046)."""

    async def test_overlapping_actions_same_target_each_read_once(
        self, hass: HomeAssistant
    ) -> None:
        """Two actions on one target each read once and write once."""
        await _setup_entry(hass)
        with by_id_network(
            refresh=_refresh(), evidence=FakeConflictEvidence()
        ) as routes:
            results = await asyncio.gather(
                _call_by_id(hass, "set_reservation_status", {"status": "checked_in"}),
                _call_by_id(hass, "set_reservation_status", {"status": "checked_out"}),
            )

        assert results == [{"success": True, "target_id": _BY_ID}] * 2
        assert routes.read.call_count == 2
        assert routes.status.call_count == 2

    async def test_overlapping_actions_different_targets_decide_independently(
        self, hass: HomeAssistant
    ) -> None:
        """One target can be written while the other is not found."""
        await _setup_entry(hass)

        def read(request: httpx.Request) -> Response:
            """Find only the first target."""
            target = request.url.params["reservationIds"]
            results = [make_by_id_record()] if target == _BY_ID else []
            return Response(200, json={"results": results})

        with by_id_network(
            refresh=_refresh(), read=read, evidence=FakeConflictEvidence()
        ) as routes:
            outcomes = await asyncio.gather(
                _call_by_id(hass, "set_reservation_status", {"status": "checked_in"}),
                _call_by_id(
                    hass,
                    "set_reservation_status",
                    {"status": "checked_in"},
                    reservation_id="res-gone",
                ),
                return_exceptions=True,
            )

        assert outcomes[0] == {"success": True, "target_id": _BY_ID}
        assert isinstance(outcomes[1], HomeAssistantError)
        assert str(outcomes[1]).startswith("not found by ID")
        assert routes.read.call_count == 2
        assert routes.status.call_count == 1

    async def test_coordinator_refresh_waits_for_by_id_critical_section(
        self, hass: HomeAssistant
    ) -> None:
        """A coordinator refresh waits for the read and the write."""
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)
        reading = asyncio.Event()
        release = asyncio.Event()
        original = GuestyActionsClient.read_reservation_by_id

        async def held(self: GuestyActionsClient, reservation_id: str) -> Any:
            """Hold the read while a refresh is requested."""
            reading.set()
            await release.wait()
            return await original(self, reservation_id)

        with (
            by_id_network(
                refresh=_refresh(), evidence=FakeConflictEvidence()
            ) as routes,
            patch.object(GuestyActionsClient, "read_reservation_by_id", held),
        ):
            fetches_at_write: list[int] = []

            def write(request: httpx.Request) -> Response:
                """Record how many refreshes ran before the write."""
                fetches_at_write.append(routes.fetch.await_count)
                return Response(200, json={})

            routes.status.mock(side_effect=write)
            action = asyncio.create_task(
                _call_by_id(hass, "set_reservation_status", {"status": "checked_in"})
            )
            await asyncio.wait_for(reading.wait(), 2)
            subsequent = hass.async_create_task(
                coordinator.async_refresh(), "competing refresh", eager_start=True
            )
            assert routes.fetch.await_count == 1
            assert not subsequent.done()
            release.set()
            await asyncio.wait_for(asyncio.gather(action, subsequent), 2)

        assert fetches_at_write == [1]
        assert routes.fetch.await_count == 2


# ── Feature 013: evidence-state write refusals (T021) ───────────────

_EVIDENCE_STATE_REFUSALS = pytest.mark.parametrize(
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
_REJECTED = "res-rej"


def _rejected_refresh(kind: str, **fields: Any) -> ReservationFetchResult:
    """Build a refresh whose rejected target is partial or dropped.

    Args:
        kind: ``partial`` keeps the record as partial; ``dropped``
            leaves it only in ``rejected_ids``.
        **fields: Further fetch result fields to set.

    Returns:
        The refresh result.
    """
    extra = (
        [replace(make_identity_reservation(_REJECTED, "CONF-REJ"), partial=True)]
        if kind == "partial"
        else []
    )
    return _refresh(
        *extra,
        rejected_ids=(_REJECTED,),
        rejected_codes=(RejectedCode(_REJECTED, "CONF-REJ"),),
        **fields,
    )


class TestEvidenceStateWriteRefusals:
    """Evidence state and validation decide writes (T021, FR-008c)."""

    @_BY_ID_SERVICES
    @_EVIDENCE_STATE_REFUSALS
    async def test_unavailable_evidence_refuses_plainly(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        state: str,
        match: str,
    ) -> None:
        """A non-ready store refuses before membership, reading nothing."""
        await _setup_entry(hass)
        with (
            patch.object(
                ConflictOnlyEvidence, "state", new_callable=PropertyMock
            ) as current,
            by_id_network(
                refresh=_refresh(),
                evidence=FakeConflictEvidence(),
                reservation_id="res-001",
            ) as routes,
            pytest.raises(HomeAssistantError) as error,
        ):
            current.return_value = state
            await _call_by_id(hass, service, payload, "res-001")

        assert str(error.value) == match
        assert "{" not in match
        assert not isinstance(error.value.__cause__, ReservationIdentityMissError)
        routes.fetch.assert_not_awaited()
        assert routes.read.call_count == 0
        assert routes.writes == 0

    @_BY_ID_SERVICES
    @pytest.mark.parametrize("kind", ["partial", "dropped"])
    async def test_rejected_target_is_refused_via_membership(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        kind: str,
    ) -> None:
        """A rejected target misses membership and is never written."""
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)
        with by_id_network(
            refresh=_rejected_refresh(kind), reservation_id=_REJECTED
        ) as routes:
            await coordinator.async_refresh()
            assert coordinator.identity.conflict_evidence.available()
            text = await _by_id_refusal(hass, service, payload, _REJECTED)

        assert text == REFUSAL_NO_CODE.format(reservation_id=_REJECTED)
        routes.fetch.assert_awaited()
        assert routes.writes == 0

    @_BY_ID_SERVICES
    async def test_corrected_target_is_written_after_recovery(
        self, hass: HomeAssistant, service: str, payload: dict[str, str]
    ) -> None:
        """A target fixed upstream is written once the refresh accepts it."""
        entry = await _setup_entry(hass)
        coordinator = _by_id_coordinator(hass, entry)
        with by_id_network(refresh=_rejected_refresh("partial")):
            await coordinator.async_refresh()
        fixed = _refresh(make_identity_reservation(_REJECTED, "CONF-REJ"))
        with by_id_network(refresh=fixed, reservation_id=_REJECTED) as routes:
            result = await _call_by_id(hass, service, payload, _REJECTED)

        assert result == {"success": True, "target_id": _REJECTED}
        routes.fetch.assert_awaited_once()
        assert routes.writes == 1

    @_BY_ID_SERVICES
    @pytest.mark.parametrize("hidden_by", ["listing_selection", "out_of_window"])
    async def test_filtered_valid_target_stays_writable(
        self,
        hass: HomeAssistant,
        service: str,
        payload: dict[str, str],
        hidden_by: str,
    ) -> None:
        """A valid record hidden from publication is still written (R11).

        Publication filters (listing selection and the configured
        window) decide what is displayed, never what is valid, so the
        hidden target stays writable and the write is actually sent.
        The only status-based publication drop, an alteration hold,
        is not a display filter: holds are refused for writes too.
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
        entry = await _setup_entry(
            hass,
            [*make_identity_reservations(*_WRITE_TARGETS), record],
            options=options,
        )
        coordinator = _by_id_coordinator(hass, entry)
        assert coordinator.last_update_success
        published = {r.id for items in coordinator.data.values() for r in items}
        assert target not in published
        assert coordinator.identity.conflict_evidence.state == EVIDENCE_READY
        assert coordinator.identity.write_refusal(target) is None
        with by_id_network(refresh=_refresh(record), reservation_id=target) as routes:
            result = await _call_by_id(hass, service, payload, target)

        assert result == {"success": True, "target_id": target}
        routes.fetch.assert_not_awaited()
        assert routes.writes == 1

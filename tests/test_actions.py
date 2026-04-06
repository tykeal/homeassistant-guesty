# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty HA action service handlers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyApiError,
)
from custom_components.guesty.api.models import ActionResult
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
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


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a config entry with all mocks.

    Args:
        hass: Home Assistant instance.

    Returns:
        The loaded MockConfigEntry.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

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
            return_value=[],
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
            "update_reservation_custom_field",
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
            "update_reservation_custom_field",
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
                return_value=[],
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
                return_value=[],
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
                return_value=[],
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

    async def test_update_field_missing_all_required(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Missing all required fields is rejected by schema."""
        await _setup_entry(hass)

        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "update_reservation_custom_field",
                {},
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


# ── Update Reservation Custom Field Tests ───────────────────────────


class TestHandleUpdateCustomField:
    """Tests for guesty.update_reservation_custom_field (T032)."""

    async def test_successful_call(
        self,
        hass: HomeAssistant,
        mock_actions_client: AsyncMock,
    ) -> None:
        """Successful update returns ActionResult dict."""
        entry = await _setup_entry(hass)
        hass.data[DOMAIN][entry.entry_id]["actions_client"] = mock_actions_client

        result = await hass.services.async_call(
            DOMAIN,
            "update_reservation_custom_field",
            {
                "reservation_id": "res-001",
                "field_id": "cf-001",
                "value": "VIP",
            },
            blocking=True,
            return_response=True,
        )

        mock_actions_client.update_reservation_custom_field.assert_awaited_once_with(
            reservation_id="res-001",
            custom_field_id="cf-001",
            value="VIP",
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

        mock_actions_client.update_reservation_custom_field.side_effect = (
            GuestyActionError(
                "Not found",
            )
        )

        with pytest.raises(HomeAssistantError, match="Not found"):
            await hass.services.async_call(
                DOMAIN,
                "update_reservation_custom_field",
                {
                    "reservation_id": "res-bad",
                    "field_id": "cf-001",
                    "value": "VIP",
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

        mock_actions_client.update_reservation_custom_field.side_effect = ValueError(
            "value must be non-empty",
        )

        with pytest.raises(
            HomeAssistantError,
            match="value must be non-empty",
        ):
            await hass.services.async_call(
                DOMAIN,
                "update_reservation_custom_field",
                {
                    "reservation_id": "res-001",
                    "field_id": "cf-001",
                    "value": "x",
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

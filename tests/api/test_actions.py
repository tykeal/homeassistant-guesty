# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyActionsClient."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from httpx import Response

from custom_components.guesty.api.actions import GuestyActionsClient
from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    BASE_URL,
    CALENDAR_ENDPOINT,
    LISTINGS_ENDPOINT,
    MAX_CUSTOM_FIELD_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_TASK_TITLE_LENGTH,
    NOTE_SEPARATOR,
    RESERVATIONS_ENDPOINT,
    TASKS_ENDPOINT,
    TOKEN_URL,
)
from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import ActionResult
from tests.conftest import FakeTokenStorage, make_token_response


def _make_actions_client() -> GuestyActionsClient:
    """Create a GuestyActionsClient with test defaults.

    Returns:
        A GuestyActionsClient backed by test fakes.
    """
    storage = FakeTokenStorage()
    http = httpx.AsyncClient()
    token_manager = GuestyTokenManager(
        client_id="test-client-id",
        client_secret="test-client-secret",
        http_client=http,
        storage=storage,
        refresh_buffer=0,
    )
    api_client = GuestyApiClient(
        token_manager=token_manager,
        http_client=http,
    )
    return GuestyActionsClient(api_client)


# ── add_reservation_note tests ──────────────────────────────────────


class TestAddReservationNote:
    """Tests for add_reservation_note."""

    @respx.mock
    async def test_appends_note_to_existing(self) -> None:
        """Appends new text to existing note with separator."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": "Existing"},
            ),
        )
        put_route = respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": "combined"},
            ),
        )

        client = _make_actions_client()
        result = await client.add_reservation_note(
            "res-001",
            "New note",
        )

        assert isinstance(result, ActionResult)
        assert result.success is True
        assert result.target_id == "res-001"
        body = put_route.calls[0].request.content
        import json

        payload = json.loads(body)
        assert payload["note"] == f"Existing{NOTE_SEPARATOR}New note"

    @respx.mock
    async def test_sets_note_when_empty(self) -> None:
        """Sets note directly when existing note is empty."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": ""},
            ),
        )
        put_route = respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": "New note"},
            ),
        )

        client = _make_actions_client()
        result = await client.add_reservation_note(
            "res-001",
            "New note",
        )

        assert result.success is True
        import json

        payload = json.loads(put_route.calls[0].request.content)
        assert payload["note"] == "New note"

    @respx.mock
    async def test_sets_note_when_null(self) -> None:
        """Sets note directly when existing note is null."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": None},
            ),
        )
        put_route = respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001"},
            ),
        )

        client = _make_actions_client()
        result = await client.add_reservation_note(
            "res-001",
            "First note",
        )

        assert result.success is True
        import json

        payload = json.loads(put_route.calls[0].request.content)
        assert payload["note"] == "First note"

    @respx.mock
    async def test_sets_note_when_key_missing(self) -> None:
        """Sets note directly when note key is absent."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001"},
            ),
        )
        respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001"},
            ),
        )

        client = _make_actions_client()
        result = await client.add_reservation_note(
            "res-001",
            "First note",
        )

        assert result.success is True

    async def test_empty_reservation_id_raises(self) -> None:
        """Empty reservation_id raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="reservation_id"):
            await client.add_reservation_note("", "note text")

    async def test_empty_note_text_raises(self) -> None:
        """Empty note_text raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="note_text"):
            await client.add_reservation_note("res-001", "")

    async def test_note_too_long_raises(self) -> None:
        """Note exceeding MAX_NOTE_LENGTH raises ValueError."""
        client = _make_actions_client()
        long_note = "x" * (MAX_NOTE_LENGTH + 1)
        with pytest.raises(ValueError, match="maximum length"):
            await client.add_reservation_note("res-001", long_note)

    @respx.mock
    async def test_get_failure_raises_action_error(self) -> None:
        """GET failure raises GuestyActionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(422, text="Not Found"),
        )

        client = _make_actions_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyActionError,
                match="Failed to fetch",
            ) as exc_info,
        ):
            await client.add_reservation_note("res-001", "note")

        assert exc_info.value.target_id == "res-001"
        assert exc_info.value.action_type == "add_reservation_note"

    @respx.mock
    async def test_get_invalid_json_raises(self) -> None:
        """Non-JSON GET response raises GuestyResponseError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(200, text="not json"),
        )

        client = _make_actions_client()
        with pytest.raises(
            GuestyResponseError,
            match="not valid JSON",
        ):
            await client.add_reservation_note("res-001", "note")

    @respx.mock
    async def test_get_non_dict_json_raises(self) -> None:
        """Non-dict JSON response raises GuestyResponseError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(200, json=["not", "a", "dict"]),
        )

        client = _make_actions_client()
        with pytest.raises(
            GuestyResponseError,
            match="must be a JSON object",
        ):
            await client.add_reservation_note("res-001", "note")

    @respx.mock
    async def test_put_failure_raises_action_error(self) -> None:
        """PUT failure raises GuestyActionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": ""},
            ),
        )
        respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(422, text="Unprocessable"),
        )

        client = _make_actions_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyActionError,
                match="Failed to update reservation note",
            ) as exc_info,
        ):
            await client.add_reservation_note("res-001", "note")

        assert exc_info.value.target_id == "res-001"


# ── set_listing_status tests ────────────────────────────────────────


class TestSetListingStatus:
    """Tests for set_listing_status."""

    @respx.mock
    async def test_activate_listing(self) -> None:
        """Activating sends active=True, listed=True."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        path = f"{LISTINGS_ENDPOINT}/lst-001"
        put_route = respx.put(f"{BASE_URL}{path}").mock(
            return_value=Response(
                200,
                json={
                    "_id": "lst-001",
                    "active": True,
                    "listed": True,
                },
            ),
        )

        client = _make_actions_client()
        result = await client.set_listing_status(
            "lst-001",
            "active",
        )

        assert isinstance(result, ActionResult)
        assert result.success is True
        assert result.target_id == "lst-001"
        import json

        payload = json.loads(put_route.calls[0].request.content)
        assert payload == {"active": True, "listed": True}

    @respx.mock
    async def test_deactivate_listing(self) -> None:
        """Deactivating sends active=False."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        path = f"{LISTINGS_ENDPOINT}/lst-001"
        put_route = respx.put(f"{BASE_URL}{path}").mock(
            return_value=Response(
                200,
                json={"_id": "lst-001", "active": False},
            ),
        )

        client = _make_actions_client()
        result = await client.set_listing_status(
            "lst-001",
            "inactive",
        )

        assert result.success is True
        import json

        payload = json.loads(put_route.calls[0].request.content)
        assert payload == {"active": False}

    async def test_empty_listing_id_raises(self) -> None:
        """Empty listing_id raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="listing_id"):
            await client.set_listing_status("", "active")

    async def test_invalid_status_raises(self) -> None:
        """Invalid status raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="invalid status"):
            await client.set_listing_status("lst-001", "archived")

    @respx.mock
    async def test_api_failure_raises_action_error(self) -> None:
        """Non-2xx response raises GuestyActionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        path = f"{LISTINGS_ENDPOINT}/lst-001"
        respx.put(f"{BASE_URL}{path}").mock(
            return_value=Response(422, text="Not Found"),
        )

        client = _make_actions_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyActionError,
                match="Failed to set listing status",
            ) as exc_info,
        ):
            await client.set_listing_status("lst-001", "active")

        assert exc_info.value.target_id == "lst-001"
        assert exc_info.value.action_type == "set_listing_status"


# ── create_task tests ───────────────────────────────────────────────


class TestCreateTask:
    """Tests for create_task."""

    @respx.mock
    async def test_minimal_task_creation(self) -> None:
        """Creates task with required fields only."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.post(f"{BASE_URL}{TASKS_ENDPOINT}").mock(
            return_value=Response(
                201,
                json={
                    "_id": "task-001",
                    "listingId": "lst-001",
                    "title": "Clean up",
                },
            ),
        )

        client = _make_actions_client()
        result = await client.create_task("lst-001", "Clean up")

        assert isinstance(result, ActionResult)
        assert result.success is True
        assert result.target_id == "task-001"

    @respx.mock
    async def test_full_task_creation(self) -> None:
        """Creates task with all optional fields."""
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
                    "_id": "task-002",
                    "listingId": "lst-001",
                    "title": "Deep clean",
                },
            ),
        )

        client = _make_actions_client()
        result = await client.create_task(
            "lst-001",
            "Deep clean",
            description="Full deep cleaning",
            assignee="user-abc",
        )

        assert result.success is True
        assert result.target_id == "task-002"
        import json

        payload = json.loads(
            post_route.calls[0].request.content,
        )
        assert payload["listingId"] == "lst-001"
        assert payload["title"] == "Deep clean"
        assert payload["description"] == "Full deep cleaning"
        assert payload["assigneeId"] == "user-abc"

    @respx.mock
    async def test_task_without_id_uses_listing_id(self) -> None:
        """Falls back to listing_id when response has no _id."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.post(f"{BASE_URL}{TASKS_ENDPOINT}").mock(
            return_value=Response(
                201,
                json={"listingId": "lst-001"},
            ),
        )

        client = _make_actions_client()
        result = await client.create_task("lst-001", "Task")

        assert result.target_id == "lst-001"

    async def test_empty_listing_id_raises(self) -> None:
        """Empty listing_id raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="listing_id"):
            await client.create_task("", "Task title")

    async def test_empty_task_title_raises(self) -> None:
        """Empty task_title raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="task_title"):
            await client.create_task("lst-001", "")

    async def test_task_title_too_long_raises(self) -> None:
        """Task title exceeding limit raises ValueError."""
        client = _make_actions_client()
        long_title = "x" * (MAX_TASK_TITLE_LENGTH + 1)
        with pytest.raises(ValueError, match="maximum length"):
            await client.create_task("lst-001", long_title)

    async def test_empty_description_raises(self) -> None:
        """Empty description when provided raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(
            ValueError,
            match="description must be non-empty",
        ):
            await client.create_task(
                "lst-001",
                "Task",
                description="",
            )

    async def test_description_too_long_raises(self) -> None:
        """Description exceeding limit raises ValueError."""
        client = _make_actions_client()
        long_desc = "x" * (MAX_DESCRIPTION_LENGTH + 1)
        with pytest.raises(ValueError, match="maximum length"):
            await client.create_task(
                "lst-001",
                "Task",
                description=long_desc,
            )

    async def test_empty_assignee_raises(self) -> None:
        """Empty assignee when provided raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(
            ValueError,
            match="assignee must be non-empty",
        ):
            await client.create_task(
                "lst-001",
                "Task",
                assignee="",
            )

    @respx.mock
    async def test_api_failure_raises_action_error(self) -> None:
        """Non-2xx response raises GuestyActionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.post(f"{BASE_URL}{TASKS_ENDPOINT}").mock(
            return_value=Response(422, text="Bad Request"),
        )

        client = _make_actions_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyActionError,
                match="Failed to create task",
            ) as exc_info,
        ):
            await client.create_task("lst-001", "Task")

        assert exc_info.value.target_id == "lst-001"
        assert exc_info.value.action_type == "create_task"

    @respx.mock
    async def test_invalid_json_response_raises(self) -> None:
        """Non-JSON response raises GuestyResponseError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.post(f"{BASE_URL}{TASKS_ENDPOINT}").mock(
            return_value=Response(201, text="not json"),
        )

        client = _make_actions_client()
        with pytest.raises(
            GuestyResponseError,
            match="not valid JSON",
        ):
            await client.create_task("lst-001", "Task")

    @respx.mock
    async def test_non_dict_json_response_raises(self) -> None:
        """Non-dict JSON response raises GuestyResponseError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.post(f"{BASE_URL}{TASKS_ENDPOINT}").mock(
            return_value=Response(
                201,
                json=["not", "a", "dict"],
            ),
        )

        client = _make_actions_client()
        with pytest.raises(
            GuestyResponseError,
            match="must be a JSON object",
        ):
            await client.create_task("lst-001", "Task")


# ── set_calendar_availability tests ─────────────────────────────────


class TestSetCalendarAvailability:
    """Tests for set_calendar_availability."""

    @respx.mock
    async def test_block_dates(self) -> None:
        """Block operation sends status=unavailable."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        cal_path = CALENDAR_ENDPOINT.format(
            listing_id="lst-001",
        )
        put_route = respx.put(f"{BASE_URL}{cal_path}").mock(
            return_value=Response(
                200,
                json={"data": {"days": []}},
            ),
        )

        client = _make_actions_client()
        result = await client.set_calendar_availability(
            "lst-001",
            "2025-08-01",
            "2025-08-05",
            "block",
        )

        assert isinstance(result, ActionResult)
        assert result.success is True
        assert result.target_id == "lst-001"
        import json

        payload = json.loads(put_route.calls[0].request.content)
        assert payload == {
            "dateFrom": "2025-08-01",
            "dateTo": "2025-08-05",
            "status": "unavailable",
        }

    @respx.mock
    async def test_unblock_dates(self) -> None:
        """Unblock operation sends status=available."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        cal_path = CALENDAR_ENDPOINT.format(
            listing_id="lst-001",
        )
        put_route = respx.put(f"{BASE_URL}{cal_path}").mock(
            return_value=Response(
                200,
                json={"data": {"days": []}},
            ),
        )

        client = _make_actions_client()
        result = await client.set_calendar_availability(
            "lst-001",
            "2025-08-01",
            "2025-08-05",
            "unblock",
        )

        assert result.success is True
        import json

        payload = json.loads(put_route.calls[0].request.content)
        assert payload["status"] == "available"

    async def test_empty_listing_id_raises(self) -> None:
        """Empty listing_id raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="listing_id"):
            await client.set_calendar_availability(
                "",
                "2025-08-01",
                "2025-08-05",
                "block",
            )

    async def test_invalid_start_date_raises(self) -> None:
        """Invalid start_date format raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="start_date"):
            await client.set_calendar_availability(
                "lst-001",
                "08-01-2025",
                "2025-08-05",
                "block",
            )

    async def test_invalid_end_date_raises(self) -> None:
        """Invalid end_date format raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="end_date"):
            await client.set_calendar_availability(
                "lst-001",
                "2025-08-01",
                "bad-date",
                "block",
            )

    async def test_end_before_start_raises(self) -> None:
        """End date before start date raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="must be >="):
            await client.set_calendar_availability(
                "lst-001",
                "2025-08-05",
                "2025-08-01",
                "block",
            )

    async def test_invalid_operation_raises(self) -> None:
        """Invalid operation raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(
            ValueError,
            match="invalid operation",
        ):
            await client.set_calendar_availability(
                "lst-001",
                "2025-08-01",
                "2025-08-05",
                "delete",
            )

    @respx.mock
    async def test_same_start_and_end_accepted(self) -> None:
        """Same start and end date is valid (single day)."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        cal_path = CALENDAR_ENDPOINT.format(
            listing_id="lst-001",
        )
        respx.put(f"{BASE_URL}{cal_path}").mock(
            return_value=Response(
                200,
                json={"data": {"days": []}},
            ),
        )

        client = _make_actions_client()
        result = await client.set_calendar_availability(
            "lst-001",
            "2025-08-01",
            "2025-08-01",
            "block",
        )

        assert result.success is True

    @respx.mock
    async def test_api_failure_raises_action_error(self) -> None:
        """Non-2xx response raises GuestyActionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        cal_path = CALENDAR_ENDPOINT.format(
            listing_id="lst-001",
        )
        respx.put(f"{BASE_URL}{cal_path}").mock(
            return_value=Response(422, text="Conflict"),
        )

        client = _make_actions_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyActionError,
                match="Failed to set calendar",
            ) as exc_info,
        ):
            await client.set_calendar_availability(
                "lst-001",
                "2025-08-01",
                "2025-08-05",
                "block",
            )

        assert exc_info.value.target_id == "lst-001"
        assert exc_info.value.action_type == "set_calendar_availability"


# ── update_reservation_custom_field tests ───────────────────────────


class TestUpdateReservationCustomField:
    """Tests for update_reservation_custom_field."""

    @respx.mock
    async def test_successful_update(self) -> None:
        """Successfully updates custom field on reservation."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        put_route = respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={
                    "_id": "res-001",
                    "customFields": {"cf-001": "new-val"},
                },
            ),
        )

        client = _make_actions_client()
        result = await client.update_reservation_custom_field(
            "res-001",
            "cf-001",
            "new-val",
        )

        assert isinstance(result, ActionResult)
        assert result.success is True
        assert result.target_id == "res-001"
        import json

        payload = json.loads(put_route.calls[0].request.content)
        assert payload == {
            "customFields": {"cf-001": "new-val"},
        }

    async def test_empty_reservation_id_raises(self) -> None:
        """Empty reservation_id raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="reservation_id"):
            await client.update_reservation_custom_field(
                "",
                "cf-001",
                "value",
            )

    async def test_empty_custom_field_id_raises(self) -> None:
        """Empty custom_field_id raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="custom_field_id"):
            await client.update_reservation_custom_field(
                "res-001",
                "",
                "value",
            )

    async def test_empty_value_raises(self) -> None:
        """Empty value raises ValueError."""
        client = _make_actions_client()
        with pytest.raises(ValueError, match="value"):
            await client.update_reservation_custom_field(
                "res-001",
                "cf-001",
                "",
            )

    async def test_value_too_long_raises(self) -> None:
        """Value exceeding max length raises ValueError."""
        client = _make_actions_client()
        long_value = "x" * (MAX_CUSTOM_FIELD_LENGTH + 1)
        with pytest.raises(ValueError, match="maximum length"):
            await client.update_reservation_custom_field(
                "res-001",
                "cf-001",
                long_value,
            )

    @respx.mock
    async def test_api_failure_raises_action_error(self) -> None:
        """Non-2xx response raises GuestyActionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(422, text="Not Found"),
        )

        client = _make_actions_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyActionError,
                match="Failed to update custom field",
            ) as exc_info,
        ):
            await client.update_reservation_custom_field(
                "res-001",
                "cf-001",
                "value",
            )

        assert exc_info.value.target_id == "res-001"
        assert exc_info.value.action_type == "update_reservation_custom_field"


# ── _error_detail tests ─────────────────────────────────────────────


class TestErrorDetail:
    """Tests for GuestyActionsClient._error_detail."""

    def test_includes_response_text(self) -> None:
        """Error detail includes response body text."""
        response = Response(422, text="Validation failed")
        detail = GuestyActionsClient._error_detail(
            "Action failed",
            "res-001",
            response,
        )
        assert "422" in detail
        assert "Validation failed" in detail
        assert "res-001" in detail

    def test_includes_json_body(self) -> None:
        """Error detail includes JSON response body."""
        response = Response(
            400,
            json={"error": "bad request"},
        )
        detail = GuestyActionsClient._error_detail(
            "Action failed",
            "lst-001",
            response,
        )
        assert "400" in detail
        assert "bad request" in detail

    def test_truncates_long_detail(self) -> None:
        """Error detail truncates very long response text."""
        long_text = "x" * 500
        response = Response(422, text=long_text)
        detail = GuestyActionsClient._error_detail(
            "Action failed",
            "res-001",
            response,
        )
        assert detail.endswith("...")
        # 200 chars of text + "..." + prefix
        assert len(long_text) > 200

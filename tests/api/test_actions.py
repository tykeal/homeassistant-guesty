# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyActionsClient."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
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
    GuestyAuthError,
    GuestyRateLimitError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import ActionResult
from tests.conftest import FakeTokenStorage, make_token_response


@pytest.fixture
async def actions_client() -> AsyncGenerator[GuestyActionsClient]:
    """Yield a GuestyActionsClient with proper HTTP cleanup.

    Creates a real httpx.AsyncClient and closes it in teardown.

    Yields:
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
    yield GuestyActionsClient(api_client)
    await http.aclose()


# ── add_reservation_note tests ──────────────────────────────────────


class TestAddReservationNote:
    """Tests for add_reservation_note."""

    @respx.mock
    async def test_appends_note_to_existing(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.add_reservation_note(
            "res-001",
            "New note",
        )

        assert isinstance(result, ActionResult)
        assert result.success is True
        assert result.target_id == "res-001"
        body = put_route.calls[0].request.content

        payload = json.loads(body)
        assert payload["note"] == f"Existing{NOTE_SEPARATOR}New note"

    @respx.mock
    async def test_sets_note_when_empty(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.add_reservation_note(
            "res-001",
            "New note",
        )

        assert result.success is True

        payload = json.loads(put_route.calls[0].request.content)
        assert payload["note"] == "New note"

    @respx.mock
    async def test_sets_note_when_null(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.add_reservation_note(
            "res-001",
            "First note",
        )

        assert result.success is True

        payload = json.loads(put_route.calls[0].request.content)
        assert payload["note"] == "First note"

    @respx.mock
    async def test_sets_note_when_key_missing(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.add_reservation_note(
            "res-001",
            "First note",
        )

        assert result.success is True

    async def test_empty_reservation_id_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Empty reservation_id raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="reservation_id"):
            await client.add_reservation_note("", "note text")

    async def test_empty_note_text_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Empty note_text raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="note_text"):
            await client.add_reservation_note("res-001", "")

    async def test_note_too_long_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Note exceeding MAX_NOTE_LENGTH raises ValueError."""
        client = actions_client
        long_note = "x" * (MAX_NOTE_LENGTH + 1)
        with pytest.raises(ValueError, match="maximum length"):
            await client.add_reservation_note("res-001", long_note)

    @respx.mock
    async def test_get_failure_raises_action_error(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
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
    async def test_get_invalid_json_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        with pytest.raises(
            GuestyResponseError,
            match="not valid JSON",
        ):
            await client.add_reservation_note("res-001", "note")

    @respx.mock
    async def test_get_non_dict_json_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        with pytest.raises(
            GuestyResponseError,
            match="must be a JSON object",
        ):
            await client.add_reservation_note("res-001", "note")

    @respx.mock
    async def test_put_failure_raises_action_error(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
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
    async def test_activate_listing(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.set_listing_status(
            "lst-001",
            "active",
        )

        assert isinstance(result, ActionResult)
        assert result.success is True
        assert result.target_id == "lst-001"

        payload = json.loads(put_route.calls[0].request.content)
        assert payload == {"active": True, "listed": True}

    @respx.mock
    async def test_deactivate_listing(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.set_listing_status(
            "lst-001",
            "inactive",
        )

        assert result.success is True

        payload = json.loads(put_route.calls[0].request.content)
        assert payload == {"active": False}

    async def test_empty_listing_id_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Empty listing_id raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="listing_id"):
            await client.set_listing_status("", "active")

    async def test_invalid_status_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Invalid status raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="invalid status"):
            await client.set_listing_status("lst-001", "archived")

    @respx.mock
    async def test_api_failure_raises_action_error(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
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
    async def test_minimal_task_creation(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.create_task("lst-001", "Clean up")

        assert isinstance(result, ActionResult)
        assert result.success is True
        assert result.target_id == "task-001"

    @respx.mock
    async def test_full_task_creation(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.create_task(
            "lst-001",
            "Deep clean",
            description="Full deep cleaning",
            assignee="user-abc",
        )

        assert result.success is True
        assert result.target_id == "task-002"

        payload = json.loads(
            post_route.calls[0].request.content,
        )
        assert payload["listingId"] == "lst-001"
        assert payload["title"] == "Deep clean"
        assert payload["description"] == "Full deep cleaning"
        assert payload["assigneeId"] == "user-abc"

    @respx.mock
    async def test_task_without_id_uses_listing_id(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.create_task("lst-001", "Task")

        assert result.target_id == "lst-001"

    async def test_empty_listing_id_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Empty listing_id raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="listing_id"):
            await client.create_task("", "Task title")

    async def test_empty_task_title_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Empty task_title raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="task_title"):
            await client.create_task("lst-001", "")

    async def test_task_title_too_long_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Task title exceeding limit raises ValueError."""
        client = actions_client
        long_title = "x" * (MAX_TASK_TITLE_LENGTH + 1)
        with pytest.raises(ValueError, match="maximum length"):
            await client.create_task("lst-001", long_title)

    async def test_empty_description_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Empty description when provided raises ValueError."""
        client = actions_client
        with pytest.raises(
            ValueError,
            match="description must be non-empty",
        ):
            await client.create_task(
                "lst-001",
                "Task",
                description="",
            )

    async def test_description_too_long_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Description exceeding limit raises ValueError."""
        client = actions_client
        long_desc = "x" * (MAX_DESCRIPTION_LENGTH + 1)
        with pytest.raises(ValueError, match="maximum length"):
            await client.create_task(
                "lst-001",
                "Task",
                description=long_desc,
            )

    async def test_empty_assignee_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Empty assignee when provided raises ValueError."""
        client = actions_client
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
    async def test_api_failure_raises_action_error(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
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
    async def test_invalid_json_response_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        with pytest.raises(
            GuestyResponseError,
            match="not valid JSON",
        ):
            await client.create_task("lst-001", "Task")

    @respx.mock
    async def test_non_dict_json_response_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        with pytest.raises(
            GuestyResponseError,
            match="must be a JSON object",
        ):
            await client.create_task("lst-001", "Task")


# ── set_calendar_availability tests ─────────────────────────────────


class TestSetCalendarAvailability:
    """Tests for set_calendar_availability."""

    @respx.mock
    async def test_block_dates(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.set_calendar_availability(
            "lst-001",
            "2025-08-01",
            "2025-08-05",
            "block",
        )

        assert isinstance(result, ActionResult)
        assert result.success is True
        assert result.target_id == "lst-001"

        payload = json.loads(put_route.calls[0].request.content)
        assert payload == {
            "dateFrom": "2025-08-01",
            "dateTo": "2025-08-05",
            "status": "unavailable",
        }

    @respx.mock
    async def test_unblock_dates(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.set_calendar_availability(
            "lst-001",
            "2025-08-01",
            "2025-08-05",
            "unblock",
        )

        assert result.success is True

        payload = json.loads(put_route.calls[0].request.content)
        assert payload["status"] == "available"

    async def test_empty_listing_id_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Empty listing_id raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="listing_id"):
            await client.set_calendar_availability(
                "",
                "2025-08-01",
                "2025-08-05",
                "block",
            )

    async def test_invalid_start_date_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Invalid start_date format raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="start_date"):
            await client.set_calendar_availability(
                "lst-001",
                "08-01-2025",
                "2025-08-05",
                "block",
            )

    async def test_invalid_end_date_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Invalid end_date format raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="end_date"):
            await client.set_calendar_availability(
                "lst-001",
                "2025-08-01",
                "bad-date",
                "block",
            )

    async def test_end_before_start_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """End date before start date raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="must be >="):
            await client.set_calendar_availability(
                "lst-001",
                "2025-08-05",
                "2025-08-01",
                "block",
            )

    async def test_invalid_operation_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Invalid operation raises ValueError."""
        client = actions_client
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
    async def test_same_start_and_end_accepted(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
        result = await client.set_calendar_availability(
            "lst-001",
            "2025-08-01",
            "2025-08-01",
            "block",
        )

        assert result.success is True

    @respx.mock
    async def test_api_failure_raises_action_error(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
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

        client = actions_client
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


# ── Edge case and boundary tests (T035/T036) ───────────────────────


class TestEdgeCasesReservationNote:
    """Edge case tests for add_reservation_note."""

    @respx.mock
    async def test_unicode_in_note_text(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Unicode chars survive the read-modify-write cycle."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": "Existing"},
            ),
        )
        put_route = respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(200, json={"_id": "res-001"}),
        )

        note = "Gäste 🏠 日本語テスト — emoji ✅"
        client = actions_client
        result = await client.add_reservation_note("res-001", note)

        assert result.success is True

        payload = json.loads(put_route.calls[0].request.content)
        assert note in payload["note"]

    @respx.mock
    async def test_note_at_max_length_succeeds(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Note exactly at MAX_NOTE_LENGTH is accepted."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": ""},
            ),
        )
        put_route = respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(200, json={"_id": "res-001"}),
        )

        max_note = "x" * MAX_NOTE_LENGTH
        client = actions_client
        result = await client.add_reservation_note(
            "res-001",
            max_note,
        )

        assert result.success is True

        payload = json.loads(
            put_route.calls[0].request.content,
        )
        assert payload["note"] == max_note
        assert len(payload["note"]) == MAX_NOTE_LENGTH

    @respx.mock
    async def test_special_chars_in_note(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """HTML entities and newlines survive round-trip."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": ""},
            ),
        )
        put_route = respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(200, json={"_id": "res-001"}),
        )

        note = '<b>bold</b> & "quoted"\nnewline'
        client = actions_client
        result = await client.add_reservation_note("res-001", note)

        assert result.success is True

        payload = json.loads(put_route.calls[0].request.content)
        assert payload["note"] == note

    @respx.mock
    async def test_existing_separator_in_note_preserved(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Existing separator in note is not collapsed."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        existing = f"First{NOTE_SEPARATOR}Second"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": existing},
            ),
        )
        put_route = respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(200, json={"_id": "res-001"}),
        )

        client = actions_client
        await client.add_reservation_note("res-001", "Third")

        payload = json.loads(put_route.calls[0].request.content)
        expected = f"{existing}{NOTE_SEPARATOR}Third"
        assert payload["note"] == expected

    @respx.mock
    async def test_concurrent_note_additions(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Multiple concurrent note additions all succeed."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        for rid in ("res-a", "res-b", "res-c"):
            path = f"{RESERVATIONS_ENDPOINT}/{rid}"
            respx.get(f"{BASE_URL}{path}").mock(
                return_value=Response(
                    200,
                    json={"_id": rid, "note": ""},
                ),
            )
            respx.put(f"{BASE_URL}{path}").mock(
                return_value=Response(
                    200,
                    json={"_id": rid},
                ),
            )

        client = actions_client
        results = await asyncio.gather(
            client.add_reservation_note("res-a", "Note A"),
            client.add_reservation_note("res-b", "Note B"),
            client.add_reservation_note("res-c", "Note C"),
        )

        assert all(r.success for r in results)
        assert {r.target_id for r in results} == {
            "res-a",
            "res-b",
            "res-c",
        }

    @respx.mock
    async def test_deleted_reservation_returns_error(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """422 for deleted reservation raises GuestyActionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-deleted"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                422,
                text="Reservation not found",
            ),
        )

        client = actions_client
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyActionError,
                match="Failed to fetch",
            ) as exc_info,
        ):
            await client.add_reservation_note(
                "res-deleted",
                "note",
            )

        assert exc_info.value.target_id == "res-deleted"


class TestEdgeCasesListingStatus:
    """Edge case tests for set_listing_status."""

    @respx.mock
    async def test_deleted_listing_returns_error(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """422 for deleted listing raises GuestyActionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        path = f"{LISTINGS_ENDPOINT}/lst-gone"
        respx.put(f"{BASE_URL}{path}").mock(
            return_value=Response(422, text="Listing not found"),
        )

        client = actions_client
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyActionError,
                match="Failed to set listing status",
            ) as exc_info,
        ):
            await client.set_listing_status("lst-gone", "active")

        assert exc_info.value.target_id == "lst-gone"
        assert exc_info.value.action_type == "set_listing_status"


class TestEdgeCasesCreateTask:
    """Edge case tests for create_task."""

    @respx.mock
    async def test_unicode_in_task_description(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Unicode in description passes through to API."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        post_route = respx.post(
            f"{BASE_URL}{TASKS_ENDPOINT}",
        ).mock(
            return_value=Response(
                201,
                json={"_id": "t-001", "title": "Clean"},
            ),
        )

        desc = "Deep cleaning — floor 2 🧹"
        client = actions_client
        result = await client.create_task(
            "lst-001",
            "Clean",
            description=desc,
        )

        assert result.success is True

        payload = json.loads(
            post_route.calls[0].request.content,
        )
        assert payload["description"] == desc

    @respx.mock
    async def test_title_at_max_length_succeeds(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Title exactly at MAX_TASK_TITLE_LENGTH is accepted."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        post_route = respx.post(
            f"{BASE_URL}{TASKS_ENDPOINT}",
        ).mock(
            return_value=Response(
                201,
                json={"_id": "t-002", "title": "x"},
            ),
        )

        max_title = "x" * MAX_TASK_TITLE_LENGTH
        client = actions_client
        result = await client.create_task("lst-001", max_title)

        assert result.success is True

        payload = json.loads(
            post_route.calls[0].request.content,
        )
        assert payload["title"] == max_title
        assert len(payload["title"]) == MAX_TASK_TITLE_LENGTH

    @respx.mock
    async def test_description_at_max_length_succeeds(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Desc exactly at MAX_DESCRIPTION_LENGTH is accepted."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        post_route = respx.post(
            f"{BASE_URL}{TASKS_ENDPOINT}",
        ).mock(
            return_value=Response(
                201,
                json={"_id": "t-003", "title": "Task"},
            ),
        )

        max_desc = "y" * MAX_DESCRIPTION_LENGTH
        client = actions_client
        result = await client.create_task(
            "lst-001",
            "Task",
            description=max_desc,
        )

        assert result.success is True

        payload = json.loads(
            post_route.calls[0].request.content,
        )
        assert payload["description"] == max_desc
        assert len(payload["description"]) == MAX_DESCRIPTION_LENGTH

    @respx.mock
    async def test_special_chars_in_title(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Special characters in task title pass through."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        post_route = respx.post(
            f"{BASE_URL}{TASKS_ENDPOINT}",
        ).mock(
            return_value=Response(
                201,
                json={"_id": "t-004", "title": "x"},
            ),
        )

        title = 'Fix AC — unit #3 "premium"'
        client = actions_client
        await client.create_task("lst-001", title)

        payload = json.loads(
            post_route.calls[0].request.content,
        )
        assert payload["title"] == title


class TestEdgeCasesCalendar:
    """Edge case tests for set_calendar_availability."""

    @respx.mock
    async def test_calendar_422_error(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """422 response raises GuestyActionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        cal_path = CALENDAR_ENDPOINT.format(listing_id="lst-001")
        respx.put(f"{BASE_URL}{cal_path}").mock(
            return_value=Response(
                422,
                json={"error": "dates conflict with reservation"},
            ),
        )

        client = actions_client
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

    @respx.mock
    async def test_concurrent_calendar_operations(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Multiple concurrent calendar ops all complete."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        for lid in ("lst-a", "lst-b"):
            cal_path = CALENDAR_ENDPOINT.format(listing_id=lid)
            respx.put(f"{BASE_URL}{cal_path}").mock(
                return_value=Response(
                    200,
                    json={"data": {"days": []}},
                ),
            )

        client = actions_client
        results = await asyncio.gather(
            client.set_calendar_availability(
                "lst-a",
                "2025-08-01",
                "2025-08-05",
                "block",
            ),
            client.set_calendar_availability(
                "lst-b",
                "2025-09-01",
                "2025-09-10",
                "unblock",
            ),
        )

        assert all(r.success for r in results)
        assert results[0].target_id == "lst-a"
        assert results[1].target_id == "lst-b"


class TestErrorDetailEdgeCases:
    """Edge case tests for _error_detail formatting."""

    def test_empty_response_body(self) -> None:
        """Error detail handles empty response body."""
        response = Response(422, text="")
        detail = GuestyActionsClient._error_detail(
            "Action failed",
            "res-001",
            response,
        )
        assert "422" in detail
        assert "res-001" in detail

    def test_json_error_with_nested_structure(self) -> None:
        """Error detail serializes nested JSON body."""
        response = Response(
            400,
            json={
                "error": {"code": "INVALID", "details": ["bad"]},
            },
        )
        detail = GuestyActionsClient._error_detail(
            "Action failed",
            "lst-001",
            response,
        )
        assert "INVALID" in detail
        assert "400" in detail


# ── Edge Case Tests (T036) ─────────────────────────────────────────


class TestEdgeCases:
    """Edge case and boundary value tests (T036)."""

    @respx.mock
    async def test_special_characters_in_description(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """HTML entities and newlines in task description."""
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
                json={"_id": "task-sp", "title": "clean"},
            ),
        )

        client = actions_client
        desc = '<b>Bold</b> & "quoted"\nnewline'
        result = await client.create_task(
            "lst-001",
            "Clean up",
            description=desc,
        )

        assert result.success is True

        payload = json.loads(
            post_route.calls[0].request.content,
        )
        assert payload["description"] == desc

    @respx.mock
    async def test_deleted_reservation_returns_not_found(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """404 for a deleted reservation raises GuestyActionError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-gone"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                404,
                text="Not Found",
            ),
        )

        client = actions_client
        with pytest.raises(
            GuestyActionError,
            match="Failed to fetch reservation",
        ) as exc_info:
            await client.add_reservation_note(
                "res-gone",
                "note",
            )

        assert exc_info.value.target_id == "res-gone"

    @respx.mock
    async def test_concurrent_action_calls(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Multiple concurrent actions succeed independently."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.put(
            f"{BASE_URL}{LISTINGS_ENDPOINT}/lst-a",
        ).mock(
            return_value=Response(
                200,
                json={"_id": "lst-a", "active": True},
            ),
        )
        respx.put(
            f"{BASE_URL}{LISTINGS_ENDPOINT}/lst-b",
        ).mock(
            return_value=Response(
                200,
                json={"_id": "lst-b", "active": False},
            ),
        )

        client = actions_client
        results = await asyncio.gather(
            client.set_listing_status("lst-a", "active"),
            client.set_listing_status("lst-b", "inactive"),
        )

        assert len(results) == 2
        assert results[0].success is True
        assert results[0].target_id == "lst-a"
        assert results[1].success is True
        assert results[1].target_id == "lst-b"


# ── Retry & Recovery Tests (T036) ──────────────────────────────────


class TestRetryBehavior:
    """Tests for retry, rate-limit, and auth recovery paths."""

    @respx.mock
    async def test_rate_limit_retry_then_success(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """429 triggers backoff retry; eventual 200 succeeds."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        lst_url = f"{BASE_URL}{LISTINGS_ENDPOINT}/lst-rl"
        respx.put(lst_url).mock(
            side_effect=[
                Response(
                    429,
                    headers={"Retry-After": "0"},
                ),
                Response(
                    200,
                    json={
                        "_id": "lst-rl",
                        "active": True,
                    },
                ),
            ],
        )

        client = actions_client
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.set_listing_status(
                "lst-rl",
                "active",
            )

        assert result.success is True
        assert result.target_id == "lst-rl"

    @respx.mock
    async def test_rate_limit_max_retries_exhausted(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """429 beyond MAX_RETRIES raises GuestyRateLimitError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        lst_url = f"{BASE_URL}{LISTINGS_ENDPOINT}/lst-rl2"
        respx.put(lst_url).mock(
            return_value=Response(
                429,
                headers={"Retry-After": "0"},
            ),
        )

        client = actions_client
        from unittest.mock import patch as _patch

        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(GuestyRateLimitError),
        ):
            await client.set_listing_status(
                "lst-rl2",
                "active",
            )

    @respx.mock
    async def test_token_refresh_during_action(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """401 triggers token refresh then retries successfully."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        lst_url = f"{BASE_URL}{LISTINGS_ENDPOINT}/lst-auth"
        respx.put(lst_url).mock(
            side_effect=[
                Response(401, text="Unauthorized"),
                Response(
                    200,
                    json={
                        "_id": "lst-auth",
                        "active": True,
                    },
                ),
            ],
        )

        client = actions_client
        result = await client.set_listing_status(
            "lst-auth",
            "active",
        )

        assert result.success is True
        assert result.target_id == "lst-auth"

    @respx.mock
    async def test_token_refresh_double_401_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Double 401 after refresh raises GuestyAuthError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        lst_url = f"{BASE_URL}{LISTINGS_ENDPOINT}/lst-auth2"
        respx.put(lst_url).mock(
            return_value=Response(401, text="Unauthorized"),
        )

        client = actions_client
        with pytest.raises(GuestyAuthError):
            await client.set_listing_status(
                "lst-auth2",
                "active",
            )

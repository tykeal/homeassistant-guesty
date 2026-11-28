# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyActionsClient."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import traceback
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

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
    LEGACY_RESERVATIONS_RETIREMENT_DATE,
    LISTINGS_ENDPOINT,
    MAX_DESCRIPTION_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_TASK_TITLE_LENGTH,
    NOTE_SEPARATOR,
    RESERVATION_BY_ID_LEGACY_PATH,
    RESERVATION_NOTES_PATH,
    RESERVATION_STATUS_PATH,
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
    RESERVATIONS_ENDPOINT,
    RESERVATIONS_V3_ENDPOINT,
    TASKS_ENDPOINT,
    TOKEN_URL,
    WRITE_ROUTE_LEGACY,
    WRITE_ROUTE_V3,
)
from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyApiError,
    GuestyAuthError,
    GuestyByIdReadError,
    GuestyConnectionError,
    GuestyPermissionError,
    GuestyPrerequisiteReadError,
    GuestyRateLimitError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import ActionResult
from tests.conftest import FakeTokenStorage, make_token_response


def _recovery_options(method: Any) -> dict[str, Any]:
    """Use recovery mode when present or exercise the existing transport."""
    return (
        {"single_attempt": True}
        if "single_attempt" in inspect.signature(method).parameters
        else {}
    )


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


# ── set_reservation_status tests ────────────────────────────────────


class TestSetReservationStatus:
    """Tests for set_reservation_status."""

    @respx.mock
    async def test_checked_in_success(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Setting checked_in sends correct PUT body."""
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
                    "status": "checked_in",
                },
            ),
        )

        client = actions_client
        result = await client.set_reservation_status(
            "res-001",
            "checked_in",
        )

        assert isinstance(result, ActionResult)
        assert result.success is True
        assert result.target_id == "res-001"

        payload = json.loads(put_route.calls[0].request.content)
        assert payload == {"status": "checked_in"}

    @respx.mock
    async def test_checked_out_success(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Setting checked_out sends correct PUT body."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-002"
        put_route = respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={
                    "_id": "res-002",
                    "status": "checked_out",
                },
            ),
        )

        client = actions_client
        result = await client.set_reservation_status(
            "res-002",
            "checked_out",
        )

        assert result.success is True
        assert result.target_id == "res-002"

        payload = json.loads(put_route.calls[0].request.content)
        assert payload == {"status": "checked_out"}

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
        res_path = f"{RESERVATIONS_ENDPOINT}/res-bad"
        respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(422, text="Unprocessable"),
        )

        client = actions_client
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyActionError,
                match="Failed to set reservation status",
            ) as exc_info,
        ):
            await client.set_reservation_status(
                "res-bad",
                "checked_in",
            )

        assert exc_info.value.target_id == "res-bad"
        assert exc_info.value.action_type == "set_reservation_status"

    async def test_empty_reservation_id_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Empty reservation_id raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="reservation_id"):
            await client.set_reservation_status(
                "",
                "checked_in",
            )

    async def test_invalid_status_raises(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Invalid status raises ValueError."""
        client = actions_client
        with pytest.raises(ValueError, match="invalid status"):
            await client.set_reservation_status(
                "res-001",
                "confirmed",
            )

    async def test_invalid_status_canceled(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Canceled status is not allowed."""
        client = actions_client
        with pytest.raises(ValueError, match="invalid status"):
            await client.set_reservation_status(
                "res-001",
                "canceled",
            )

    async def test_invalid_status_random(
        self,
        actions_client: GuestyActionsClient,
    ) -> None:
        """Random string status is not allowed."""
        client = actions_client
        with pytest.raises(ValueError, match="invalid status"):
            await client.set_reservation_status(
                "res-001",
                "some_random_status",
            )


# ── Legacy retirement signalling (T008) ─────────────────────────────


def _make_actions_client(
    http: httpx.AsyncClient,
    reservations_api: str,
) -> GuestyActionsClient:
    """Build an actions client bound to a reservation path.

    Args:
        http: Async HTTP client to use.
        reservations_api: Reservation path selector.

    Returns:
        A GuestyActionsClient on the requested path.
    """
    token_manager = GuestyTokenManager(
        client_id="test-client-id",
        client_secret="test-client-secret",
        http_client=http,
        storage=FakeTokenStorage(),
        refresh_buffer=0,
    )
    api_client = GuestyApiClient(
        token_manager=token_manager,
        http_client=http,
        reservations_api=reservations_api,
    )
    return GuestyActionsClient(api_client)


class TestLegacyRetirementErrors:
    """Tests for retirement-shaped legacy failures (FR-039)."""

    @respx.mock
    @pytest.mark.parametrize("status_code", [404, 410])
    async def test_legacy_note_fetch_tells_user_to_roll_forward(
        self,
        status_code: int,
    ) -> None:
        """A 404/410 names the retirement and the rollback step."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(status_code, json={"error": "gone"}),
        )
        http = httpx.AsyncClient()
        client = _make_actions_client(http, RESERVATIONS_API_LEGACY)
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await client.add_reservation_note("res-001", "note")
        finally:
            await http.aclose()

        message = str(exc_info.value)
        assert LEGACY_RESERVATIONS_RETIREMENT_DATE in message
        assert "reservations_api" in message
        assert "v3" in message

    @respx.mock
    async def test_legacy_status_write_tells_user_to_roll_forward(
        self,
    ) -> None:
        """A 410 on the status write carries the same instruction."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(410, json={"error": "gone"}),
        )
        http = httpx.AsyncClient()
        client = _make_actions_client(http, RESERVATIONS_API_LEGACY)
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await client.set_reservation_status(
                    "res-001",
                    "checked_in",
                )
        finally:
            await http.aclose()

        assert LEGACY_RESERVATIONS_RETIREMENT_DATE in str(exc_info.value)

    @respx.mock
    async def test_other_status_codes_keep_plain_message(self) -> None:
        """A non-retirement failure carries no rollback hint."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(422, json={"error": "nope"}),
        )
        http = httpx.AsyncClient()
        client = _make_actions_client(http, RESERVATIONS_API_LEGACY)
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await client.set_reservation_status(
                    "res-001",
                    "checked_in",
                )
        finally:
            await http.aclose()

        assert LEGACY_RESERVATIONS_RETIREMENT_DATE not in str(exc_info.value)

    @respx.mock
    async def test_v3_path_keeps_plain_message(self) -> None:
        """The v3 path never emits the legacy rollback hint."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        res_path = RESERVATION_STATUS_PATH.format(
            reservation_id="res-001",
        )
        respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(404, json={"error": "gone"}),
        )
        http = httpx.AsyncClient()
        client = _make_actions_client(http, RESERVATIONS_API_V3)
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await client.set_reservation_status(
                    "res-001",
                    "checked_in",
                )
        finally:
            await http.aclose()

        assert LEGACY_RESERVATIONS_RETIREMENT_DATE not in str(exc_info.value)


class TestIdentifierProvenance:
    """Writes address reservations by identifier only (T121).

    Guesty Support confirmed that a confirmation code, an
    ``originId`` or any other external reference answers
    "Reservation not found" on a v3 endpoint. A write must
    therefore never put one in a path or a filter (FR-031e).
    """

    @respx.mock
    @pytest.mark.parametrize(
        "reservations_api",
        [RESERVATIONS_API_V3, RESERVATIONS_API_LEGACY],
    )
    async def test_no_write_url_carries_an_external_reference(
        self,
        reservations_api: str,
    ) -> None:
        """Every request URL names the Guesty identifier."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.route(host="open-api.guesty.com").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "status": "confirmed"},
            ),
        )
        http = httpx.AsyncClient()
        client = _make_actions_client(http, reservations_api)
        try:
            await client.add_reservation_note("res-001", "note")
            await client.set_reservation_status("res-001", "checked_in")
        finally:
            await http.aclose()

        urls = [
            str(call.request.url)
            for call in respx.calls
            if "oauth2" not in str(call.request.url)
        ]
        assert urls
        for url in urls:
            assert "CONF-" not in url
            assert "originId" not in url
            assert "confirmationCode" not in url
        assert any("res-001" in url for url in urls)


# ── Phase 7: v3 write resources (T077 to T080) ──────────────────────


def _v3_client(http: httpx.AsyncClient) -> GuestyActionsClient:
    """Build an actions client on the v3 reservation path.

    Args:
        http: Async HTTP client to use.

    Returns:
        A GuestyActionsClient bound to the v3 path.
    """
    return _make_actions_client(http, RESERVATIONS_API_V3)


def _mock_token() -> None:
    """Mock the OAuth token endpoint for a respx test."""
    respx.post(TOKEN_URL).mock(
        return_value=Response(200, json=make_token_response()),
    )


class TestV3NoteWrites:
    """The note action uses the dedicated v3 resources (T077)."""

    @respx.mock
    async def test_reads_v3_search_and_writes_notes_resource(
        self,
    ) -> None:
        """Reads by reservationIds then PUTs every note field."""
        _mock_token()
        read_route = respx.get(
            f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}",
        ).mock(
            return_value=Response(
                200,
                json={
                    "results": [
                        {
                            "reservationId": "res-001",
                            "notes": {
                                "other": "Existing",
                                "guest": "Guest note",
                            },
                        },
                    ],
                },
            ),
        )
        write_route = respx.put(
            f"{BASE_URL}{RESERVATION_NOTES_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))
        legacy_route = respx.route(
            method="PUT",
            url=f"{BASE_URL}{RESERVATIONS_ENDPOINT}/res-001",
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            result = await _v3_client(http).add_reservation_note(
                "res-001",
                "New note",
            )
        finally:
            await http.aclose()

        assert result.success is True
        assert read_route.called
        assert read_route.calls[0].request.url.params["reservationIds"] == "res-001"
        assert not legacy_route.called
        payload = json.loads(write_route.calls[0].request.content)
        assert payload == {
            "other": f"Existing{NOTE_SEPARATOR}New note",
            "guest": "Guest note",
        }

    @respx.mock
    async def test_sets_other_when_no_note_exists(self) -> None:
        """An absent notes object writes the new text alone."""
        _mock_token()
        respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
            return_value=Response(
                200,
                json={"results": [{"reservationId": "res-001"}]},
            ),
        )
        write_route = respx.put(
            f"{BASE_URL}{RESERVATION_NOTES_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            await _v3_client(http).add_reservation_note(
                "res-001",
                "New note",
            )
        finally:
            await http.aclose()

        payload = json.loads(write_route.calls[0].request.content)
        assert payload == {"other": "New note"}

    @respx.mock
    async def test_read_failure_raises_without_writing(self) -> None:
        """A non-2xx read raises and attempts no write."""
        _mock_token()
        respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
            return_value=Response(422, json={"error": "nope"}),
        )
        write_route = respx.put(
            f"{BASE_URL}{RESERVATION_NOTES_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            with pytest.raises(GuestyActionError, match="Failed to fetch"):
                await _v3_client(http).add_reservation_note(
                    "res-001",
                    "New note",
                )
        finally:
            await http.aclose()

        assert not write_route.called

    @respx.mock
    async def test_missing_reservation_raises_without_writing(
        self,
    ) -> None:
        """An empty v3 result abandons the write (contract 1.1)."""
        _mock_token()
        respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
            return_value=Response(200, json={"results": []}),
        )
        write_route = respx.put(
            f"{BASE_URL}{RESERVATION_NOTES_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await _v3_client(http).add_reservation_note(
                    "res-001",
                    "New note",
                )
        finally:
            await http.aclose()

        assert not write_route.called
        assert "res-001" in str(exc_info.value)


class TestV3StatusWrites:
    """The status action uses the v3 status resource (T078)."""

    @respx.mock
    @pytest.mark.parametrize("status", ["checked_in", "checked_out"])
    async def test_writes_status_resource(self, status: str) -> None:
        """PUTs the accepted status to the v3 status resource."""
        _mock_token()
        write_route = respx.put(
            f"{BASE_URL}{RESERVATION_STATUS_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))
        legacy_route = respx.route(
            method="PUT",
            url=f"{BASE_URL}{RESERVATIONS_ENDPOINT}/res-001",
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            result = await _v3_client(http).set_reservation_status(
                "res-001",
                status,
            )
        finally:
            await http.aclose()

        assert result.success is True
        assert not legacy_route.called
        payload = json.loads(write_route.calls[0].request.content)
        assert payload == {"status": status}

    async def test_invalid_status_is_still_rejected(self) -> None:
        """Validation is unchanged by the resource change."""
        http = httpx.AsyncClient()
        try:
            with pytest.raises(ValueError, match="invalid status"):
                await _v3_client(http).set_reservation_status(
                    "res-001",
                    "bogus",
                )
        finally:
            await http.aclose()


class TestV3WriteRejections:
    """A rejected v3 write names its resource (T079)."""

    @respx.mock
    async def test_note_write_rejection_names_resource(self) -> None:
        """The note write error carries resource and detail."""
        _mock_token()
        respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
            return_value=Response(
                200,
                json={"results": [{"reservationId": "res-001"}]},
            ),
        )
        notes_path = RESERVATION_NOTES_PATH.format(
            reservation_id="res-001",
        )
        respx.put(f"{BASE_URL}{notes_path}").mock(
            return_value=Response(422, json={"error": "nope"}),
        )

        http = httpx.AsyncClient()
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await _v3_client(http).add_reservation_note(
                    "res-001",
                    "New note",
                )
        finally:
            await http.aclose()

        message = str(exc_info.value)
        assert notes_path in message
        assert "nope" in message
        assert "422" in message

    @respx.mock
    async def test_status_write_rejection_names_resource(self) -> None:
        """The status write error carries resource and detail."""
        _mock_token()
        status_path = RESERVATION_STATUS_PATH.format(
            reservation_id="res-001",
        )
        respx.put(f"{BASE_URL}{status_path}").mock(
            return_value=Response(422, json={"error": "nope"}),
        )

        http = httpx.AsyncClient()
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await _v3_client(http).set_reservation_status(
                    "res-001",
                    "checked_in",
                )
        finally:
            await http.aclose()

        message = str(exc_info.value)
        assert status_path in message
        assert "nope" in message

    @respx.mock
    async def test_read_rejection_names_resource(self) -> None:
        """The note read error carries the v3 search resource."""
        _mock_token()
        respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
            return_value=Response(400, json={"error": "bad"}),
        )

        http = httpx.AsyncClient()
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await _v3_client(http).add_reservation_note(
                    "res-001",
                    "New note",
                )
        finally:
            await http.aclose()

        assert RESERVATIONS_V3_ENDPOINT in str(exc_info.value)


class TestLegacyPathWritesUnchanged:
    """The legacy path keeps the previous requests (T080)."""

    @respx.mock
    async def test_note_uses_legacy_catch_all(self) -> None:
        """The note action reads and writes /reservations/{id}."""
        _mock_token()
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        read_route = respx.get(f"{BASE_URL}{res_path}").mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": "Existing"},
            ),
        )
        write_route = respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(200, json={}),
        )
        v3_route = respx.put(
            f"{BASE_URL}{RESERVATION_NOTES_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            await _make_actions_client(
                http,
                RESERVATIONS_API_LEGACY,
            ).add_reservation_note("res-001", "New note")
        finally:
            await http.aclose()

        assert read_route.called
        assert not v3_route.called
        payload = json.loads(write_route.calls[0].request.content)
        assert payload == {"note": f"Existing{NOTE_SEPARATOR}New note"}

    @respx.mock
    async def test_status_uses_legacy_catch_all(self) -> None:
        """The status action writes /reservations/{id}."""
        _mock_token()
        res_path = f"{RESERVATIONS_ENDPOINT}/res-001"
        write_route = respx.put(f"{BASE_URL}{res_path}").mock(
            return_value=Response(200, json={}),
        )
        v3_route = respx.put(
            f"{BASE_URL}{RESERVATION_STATUS_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            await _make_actions_client(
                http,
                RESERVATIONS_API_LEGACY,
            ).set_reservation_status("res-001", "checked_in")
        finally:
            await http.aclose()

        assert not v3_route.called
        payload = json.loads(write_route.calls[0].request.content)
        assert payload == {"status": "checked_in"}


# ── Phase 7: legacy write routing (T128, T132) ──────────────────────


def _legacy_url(reservation_id: str = "res-001") -> str:
    """Build the legacy catch-all URL for a reservation.

    Args:
        reservation_id: The reservation addressed.

    Returns:
        The absolute legacy reservation URL.
    """
    path = RESERVATION_BY_ID_LEGACY_PATH.format(
        reservation_id=reservation_id,
    )
    return f"{BASE_URL}{path}"


class TestLegacyRoutedWrites:
    """A fallback-served target is written through legacy (T128)."""

    @respx.mock
    async def test_note_uses_the_legacy_read_before_write(
        self,
    ) -> None:
        """The routed note issues the previous release's requests."""
        _mock_token()
        read_route = respx.get(_legacy_url()).mock(
            return_value=Response(
                200,
                json={"_id": "res-001", "note": "Existing"},
            ),
        )
        write_route = respx.put(_legacy_url()).mock(
            return_value=Response(200, json={}),
        )
        v3_notes = respx.put(
            f"{BASE_URL}{RESERVATION_NOTES_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))
        v3_read = respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
            return_value=Response(200, json={"results": []}),
        )

        http = httpx.AsyncClient()
        try:
            result = await _v3_client(http).add_reservation_note(
                "res-001",
                "New note",
                route=WRITE_ROUTE_LEGACY,
            )
        finally:
            await http.aclose()

        assert result.success is True
        assert read_route.called
        assert len(write_route.calls) == 1
        assert not v3_notes.called
        assert not v3_read.called
        assert json.loads(write_route.calls[0].request.content) == {
            "note": f"Existing{NOTE_SEPARATOR}New note",
        }

    @respx.mock
    async def test_status_uses_the_legacy_catch_all(self) -> None:
        """The routed status write is the previous release's."""
        _mock_token()
        write_route = respx.put(_legacy_url()).mock(
            return_value=Response(200, json={}),
        )
        v3_status = respx.put(
            f"{BASE_URL}{RESERVATION_STATUS_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            await _v3_client(http).set_reservation_status(
                "res-001",
                "checked_in",
                route=WRITE_ROUTE_LEGACY,
            )
        finally:
            await http.aclose()

        assert len(write_route.calls) == 1
        assert not v3_status.called
        assert json.loads(write_route.calls[0].request.content) == {
            "status": "checked_in",
        }

    @respx.mock
    async def test_v3_route_issues_no_legacy_request(self) -> None:
        """A v3-sourced target never touches the legacy resource."""
        _mock_token()
        respx.put(
            f"{BASE_URL}{RESERVATION_STATUS_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))
        legacy = respx.route(url=_legacy_url()).mock(
            return_value=Response(200, json={}),
        )

        http = httpx.AsyncClient()
        try:
            await _v3_client(http).set_reservation_status(
                "res-001",
                "checked_in",
                route=WRITE_ROUTE_V3,
            )
        finally:
            await http.aclose()

        assert not legacy.called


class TestRecoveredReservationWrites:
    """Transport-level one-shot controls without enabling service recovery."""

    @pytest.mark.parametrize("route_name", [WRITE_ROUTE_V3, WRITE_ROUTE_LEGACY])
    async def test_real_prerequisite_proxy_logs_are_redacted(
        self,
        route_name: str,
        caplog: pytest.LogCaptureFixture,
        socket_enabled: None,
    ) -> None:
        """A real proxy refusal during the GET is safe even at DEBUG level."""
        secret = "private-note-proxy-sentinel"
        requests: list[bytes] = []
        finished = asyncio.Event()

        async def reject_proxy(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            """Reject the local HTTPS CONNECT using an untrusted reason."""
            try:
                requests.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(
                    f"HTTP/1.1 407 {secret}\r\nContent-Length: 0\r\n\r\n".encode()
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                finished.set()

        server = await asyncio.start_server(reject_proxy, "127.0.0.1", 0)
        assert server.sockets is not None
        port = server.sockets[0].getsockname()[1]
        try:
            async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}") as http:
                client = _v3_client(http)
                with (
                    caplog.at_level(logging.DEBUG),
                    patch.object(
                        client._api_client._token_manager,
                        "get_token",
                        return_value="token",
                    ),
                    pytest.raises(GuestyPrerequisiteReadError) as raised,
                ):
                    await client.add_reservation_note(
                        "res-001", "hello", route=route_name, single_attempt=True
                    )
            assert secret not in caplog.text
            assert secret not in "".join(traceback.format_exception(raised.value))
            assert "write was not sent" in str(raised.value).lower()
            await asyncio.wait_for(finished.wait(), 2)
            assert len(requests) == 1
            assert requests[0].startswith(b"CONNECT ")
        finally:
            server.close()
            await server.wait_closed()

    @respx.mock
    @pytest.mark.parametrize("route_name", [WRITE_ROUTE_V3, WRITE_ROUTE_LEGACY])
    @pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.RemoteProtocolError])
    async def test_prerequisite_transport_error_is_redacted(
        self,
        route_name: str,
        error_type: type[httpx.TransportError],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A failed real note GET cannot leak transport data or issue a PUT."""
        secret = "private-note-transport-sentinel"
        _mock_token()
        read_url = (
            _legacy_url()
            if route_name == WRITE_ROUTE_LEGACY
            else f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}"
        )
        read = respx.get(read_url).mock(side_effect=error_type(secret))
        write = respx.route(method="PUT").mock(return_value=Response(200))
        async with httpx.AsyncClient() as http:
            client = _v3_client(http)
            with (
                caplog.at_level(logging.DEBUG),
                pytest.raises(GuestyApiError) as raised,
            ):
                await client.add_reservation_note(
                    "res-001", "hello", route=route_name, single_attempt=True
                )
        assert len(read.calls) == 1
        assert not write.called
        assert isinstance(raised.value, GuestyPrerequisiteReadError)
        assert raised.value.stage == "read"
        assert raised.value.category == "connection"
        assert "write was not sent" in str(raised.value).lower()
        assert secret not in str(raised.value)
        assert secret not in "".join(traceback.format_exception(raised.value))
        assert secret not in caplog.text

    @respx.mock
    @pytest.mark.parametrize("operation", ["note-read", "note-write", "status"])
    @pytest.mark.parametrize("status", [404, 410])
    async def test_legacy_primary_recovery_retirement_guidance(
        self, operation: str, status: int
    ) -> None:
        """A primary legacy failure remains actionable without leaking its body."""
        secret = "private-primary-legacy-error"
        _mock_token()
        read = respx.get(_legacy_url()).mock(
            return_value=Response(
                status if operation == "note-read" else 200,
                json={"error": secret} if operation == "note-read" else {"note": ""},
            )
        )
        write = respx.put(_legacy_url()).mock(
            return_value=Response(status, json={"error": secret})
        )
        async with httpx.AsyncClient() as http:
            client = _make_actions_client(http, RESERVATIONS_API_LEGACY)
            method = (
                client.set_reservation_status
                if operation == "status"
                else client.add_reservation_note
            )
            with pytest.raises(GuestyApiError) as raised:
                await method(
                    "res-001",
                    "checked_in" if operation == "status" else "hello",
                    single_attempt=True,
                )
        assert secret not in str(raised.value)
        assert "2027-03-01" in str(raised.value)
        assert "v3" in str(raised.value).lower()
        assert len(read.calls) == (0 if operation == "status" else 1)
        assert len(write.calls) == (0 if operation == "note-read" else 1)
        assert ("write was not sent" in str(raised.value).lower()) == (
            operation == "note-read"
        )

    @respx.mock
    @pytest.mark.parametrize("status", [404, 410])
    async def test_retired_legacy_note_read_reports_no_write(self, status: int) -> None:
        """A retirement verdict during the note GET must not imply a PUT."""
        _mock_token()
        read = respx.get(_legacy_url()).mock(return_value=Response(status, json={}))
        write = respx.put(_legacy_url()).mock(return_value=Response(200))
        async with httpx.AsyncClient() as http:
            client = _v3_client(http)
            with pytest.raises(GuestyActionError) as raised:
                await client.add_reservation_note(
                    "res-001",
                    "hello",
                    route=WRITE_ROUTE_LEGACY,
                    single_attempt=True,
                )
            assert not client._api_client.legacy_write_routing_available
        assert len(read.calls) == 1
        assert not write.called
        assert "write was not sent" in str(raised.value).lower()
        assert "request was sent" not in str(raised.value).lower()

    @respx.mock
    async def test_recovered_legacy_retirement_has_distinct_refusal_type(self) -> None:
        """A retirement verdict remains distinguishable from generic write errors."""
        _mock_token()
        write = respx.put(_legacy_url()).mock(return_value=Response(410, json={}))
        async with httpx.AsyncClient() as http:
            client = _v3_client(http)
            with pytest.raises(GuestyActionError) as raised:
                await client.set_reservation_status(
                    "res-001",
                    "checked_in",
                    route=WRITE_ROUTE_LEGACY,
                    single_attempt=True,
                )
            assert client._api_client.legacy_write_routing_available is False
        assert len(write.calls) == 1
        assert type(raised.value) is not GuestyActionError
        assert "2027-03-01" in str(raised.value)

    @respx.mock
    @pytest.mark.parametrize("route_name", [WRITE_ROUTE_V3, WRITE_ROUTE_LEGACY])
    @pytest.mark.parametrize("operation", ["note", "status"])
    @pytest.mark.parametrize("status", [422, 500])
    async def test_recovered_rejection_is_safe(
        self, route_name: str, operation: str, status: int
    ) -> None:
        """A failed actual recovered PUT never reports untrusted body data."""
        secret = "private-write-response-sentinel"
        _mock_token()
        if operation == "note":
            read_url = (
                _legacy_url()
                if route_name == WRITE_ROUTE_LEGACY
                else f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}"
            )
            document = (
                {"_id": "res-001", "note": ""}
                if route_name == WRITE_ROUTE_LEGACY
                else {"results": [{"reservationId": "res-001", "notes": {}}]}
            )
            respx.get(read_url).mock(return_value=Response(200, json=document))
        put = respx.route(method="PUT").mock(return_value=Response(status, text=secret))
        async with httpx.AsyncClient() as http:
            client = _v3_client(http)
            method = (
                client.add_reservation_note
                if operation == "note"
                else client.set_reservation_status
            )
            with pytest.raises(GuestyApiError) as raised:
                await method(
                    "res-001",
                    "hello" if operation == "note" else "checked_in",
                    route=route_name,
                    single_attempt=True,
                )
        assert len(put.calls) == 1
        assert secret not in str(raised.value)
        assert getattr(raised.value, "stage", None) == "write"
        assert "not retried" in str(raised.value).lower()

    @respx.mock
    @pytest.mark.parametrize("route_name", [WRITE_ROUTE_V3, WRITE_ROUTE_LEGACY])
    @pytest.mark.parametrize("failure", ["401", "403", "422", "token"])
    async def test_failed_note_prerequisite_is_safe_and_unsent(
        self, route_name: str, failure: str
    ) -> None:
        """A failed note read is distinguishable from an attempted write."""
        secret = "private-note-read-response-sentinel"
        _mock_token()
        read_url = (
            _legacy_url()
            if route_name == WRITE_ROUTE_LEGACY
            else f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}"
        )
        read = respx.get(read_url).mock(
            return_value=Response(
                200 if failure == "token" else int(failure), text=secret
            )
        )
        write = respx.route(method="PUT").mock(return_value=Response(200))
        async with httpx.AsyncClient() as http:
            client = _v3_client(http)
            manager = client._api_client._token_manager
            token_error = GuestyAuthError(secret) if failure == "token" else None
            with (
                patch.object(
                    manager, "get_token", side_effect=token_error, return_value="token"
                ),
                pytest.raises(GuestyApiError) as raised,
            ):
                await client.add_reservation_note(
                    "res-001", "hello", route=route_name, single_attempt=True
                )
        assert not write.called
        assert len(read.calls) == (
            0 if failure == "token" else 2 if failure == "401" else 1
        )
        category = (
            "authentication"
            if failure in ("401", "token")
            else ("permission" if failure == "403" else "read")
        )
        assert getattr(raised.value, "stage", None) == "read"
        assert getattr(raised.value, "category", None) == category
        assert secret not in str(raised.value)
        assert "write was not sent" in str(raised.value).lower()

    @respx.mock
    @pytest.mark.parametrize(
        ("source", "route_name"),
        [
            (RESERVATIONS_API_V3, WRITE_ROUTE_V3),
            (RESERVATIONS_API_V3, WRITE_ROUTE_LEGACY),
            (RESERVATIONS_API_LEGACY, WRITE_ROUTE_V3),
        ],
    )
    @pytest.mark.parametrize("operation", ["note", "status"])
    @pytest.mark.parametrize("failure", [401, 429, 500, "connection"])
    async def test_recovered_write_is_single_attempt(
        self, source: str, route_name: str, operation: str, failure: int | str
    ) -> None:
        """A recovered write is sent at most once regardless of route."""
        _mock_token()
        legacy = source == RESERVATIONS_API_LEGACY or route_name == WRITE_ROUTE_LEGACY
        if operation == "note":
            if legacy:
                respx.get(_legacy_url()).mock(
                    return_value=Response(200, json={"_id": "res-001", "note": ""})
                )
            else:
                respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
                    return_value=Response(
                        200,
                        json={"results": [{"reservationId": "res-001", "notes": {}}]},
                    )
                )
        path = (
            RESERVATION_NOTES_PATH if operation == "note" else RESERVATION_STATUS_PATH
        )
        url = (
            _legacy_url()
            if legacy
            else f"{BASE_URL}{path.format(reservation_id='res-001')}"
        )
        write = respx.put(url)
        if failure == "connection":
            write.mock(side_effect=httpx.ConnectError("unavailable"))
        else:
            assert isinstance(failure, int)
            write.mock(return_value=Response(failure, json={"error": "no"}))
        async with httpx.AsyncClient() as http:
            client = _make_actions_client(http, source)
            method = (
                client.add_reservation_note
                if operation == "note"
                else client.set_reservation_status
            )
            options: dict[str, Any] = (
                {"single_attempt": True}
                if "single_attempt" in inspect.signature(method).parameters
                else {}
            )
            value = "note" if operation == "note" else "checked_in"
            with (
                patch("asyncio.sleep", new_callable=AsyncMock),
                pytest.raises(GuestyApiError),
            ):
                await method("res-001", value, route=route_name, **options)
        assert len(write.calls) == 1

    @respx.mock
    @pytest.mark.parametrize("route_name", [WRITE_ROUTE_V3, WRITE_ROUTE_LEGACY])
    async def test_note_prerequisite_read_retains_retry_policy(
        self, route_name: str
    ) -> None:
        """A recovered write must not constrain the note prerequisite GET."""
        _mock_token()
        if route_name == WRITE_ROUTE_LEGACY:
            read_url = write_url = _legacy_url()
            document: dict[str, object] = {"_id": "res-001", "note": ""}
        else:
            read_url = f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}"
            write_url = (
                f"{BASE_URL}{RESERVATION_NOTES_PATH.format(reservation_id='res-001')}"
            )
            document = {"results": [{"reservationId": "res-001", "notes": {}}]}
        read = respx.get(read_url).mock(
            side_effect=[Response(500), Response(200, json=document)]
        )
        write = respx.put(write_url).mock(return_value=Response(200, json={}))
        async with httpx.AsyncClient() as http:
            method = _v3_client(http).add_reservation_note
            options: dict[str, Any] = (
                {"single_attempt": True}
                if "single_attempt" in inspect.signature(method).parameters
                else {}
            )
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await method("res-001", "note", route=route_name, **options)
        assert result.success
        assert len(read.calls) == 2
        assert len(write.calls) == 1

    @respx.mock
    @pytest.mark.parametrize("route_name", [WRITE_ROUTE_V3, WRITE_ROUTE_LEGACY])
    @pytest.mark.parametrize("operation", ["note", "status"])
    async def test_guard_after_token_prevents_dispatch(
        self, route_name: str, operation: str
    ) -> None:
        """A changed guard during token acquisition prevents the actual PUT."""
        _mock_token()
        if operation == "note":
            if route_name == WRITE_ROUTE_LEGACY:
                respx.get(_legacy_url()).mock(
                    return_value=Response(200, json={"_id": "res-001", "note": ""})
                )
            else:
                respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
                    return_value=Response(
                        200,
                        json={"results": [{"reservationId": "res-001", "notes": {}}]},
                    )
                )
        put = respx.route(method="PUT").mock(return_value=Response(200, json={}))
        state = {"valid": True}
        calls = 0

        def recheck() -> None:
            """Reject the write once the token request invalidates authorization."""
            if not state["valid"]:
                raise GuestyActionError("changed")

        async with httpx.AsyncClient() as http:
            client = _v3_client(http)
            method = (
                client.add_reservation_note
                if operation == "note"
                else client.set_reservation_status
            )
            original = client._api_client._token_manager.get_token

            async def token() -> str:
                """Invalidate authorization while preparing the PUT token."""
                nonlocal calls
                calls += 1
                value = await original()
                if operation == "status" or calls == 2:
                    state["valid"] = False
                return value

            options = _recovery_options(method)
            if "before_write" in inspect.signature(method).parameters:
                options["before_write"] = recheck
            with patch.object(client._api_client._token_manager, "get_token", token):
                try:
                    value = "note" if operation == "note" else "checked_in"
                    await method("res-001", value, route=route_name, **options)
                except GuestyActionError as exc:
                    assert str(exc) == "changed"
        assert not put.called

    @respx.mock
    @pytest.mark.parametrize("route_name", [WRITE_ROUTE_V3, WRITE_ROUTE_LEGACY])
    async def test_guard_after_note_read_prevents_write_token(
        self, route_name: str
    ) -> None:
        """A changed guard after the note GET refuses before write authentication."""
        _mock_token()
        state = {"valid": True}
        calls = 0

        def note_response(_: httpx.Request) -> Response:
            """Invalidate authorization as the prerequisite GET completes."""
            state["valid"] = False
            if route_name == WRITE_ROUTE_LEGACY:
                return Response(200, json={"_id": "res-001", "note": ""})
            return Response(
                200, json={"results": [{"reservationId": "res-001", "notes": {}}]}
            )

        def recheck() -> None:
            """Reject the changed authorization."""
            if not state["valid"]:
                raise GuestyActionError("changed")

        read_url = (
            _legacy_url()
            if route_name == WRITE_ROUTE_LEGACY
            else (f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}")
        )
        respx.get(read_url).mock(side_effect=note_response)
        put = respx.route(method="PUT").mock(return_value=Response(200, json={}))
        async with httpx.AsyncClient() as http:
            client = _v3_client(http)
            original = client._api_client._token_manager.get_token

            async def token() -> str:
                """Count token retrievals for the GET and potential PUT."""
                nonlocal calls
                calls += 1
                return await original()

            method = client.add_reservation_note
            options = _recovery_options(method)
            if "before_write" in inspect.signature(method).parameters:
                options["before_write"] = recheck
            with patch.object(client._api_client._token_manager, "get_token", token):
                try:
                    await method("res-001", "note", route=route_name, **options)
                except GuestyActionError as exc:
                    assert str(exc) == "changed"
        assert not put.called
        assert calls == 1


class TestLegacyRoutedWriteFailures:
    """A routed write fails loudly and exactly once (T132)."""

    @respx.mock
    async def test_rejection_names_resource_and_detail(self) -> None:
        """A non-2xx raises and is never reported as success."""
        _mock_token()
        write_route = respx.put(_legacy_url()).mock(
            return_value=Response(422, json={"error": "nope"}),
        )
        v3_status = respx.put(
            f"{BASE_URL}{RESERVATION_STATUS_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await _v3_client(http).set_reservation_status(
                    "res-001",
                    "checked_in",
                    route=WRITE_ROUTE_LEGACY,
                )
        finally:
            await http.aclose()

        message = str(exc_info.value)
        assert RESERVATIONS_ENDPOINT in message
        assert "nope" in message
        assert len(write_route.calls) == 1
        assert not v3_status.called

    @respx.mock
    @pytest.mark.parametrize("status_code", [429, 401, 500])
    async def test_retryable_answers_issue_one_request(
        self,
        status_code: int,
    ) -> None:
        """Single-attempt mode suppresses the shared retries."""
        _mock_token()
        write_route = respx.put(_legacy_url()).mock(
            return_value=Response(status_code, json={"error": "no"}),
        )

        http = httpx.AsyncClient()
        try:
            with (
                patch("asyncio.sleep", new_callable=AsyncMock),
                pytest.raises(GuestyActionError),
            ):
                await _v3_client(http).set_reservation_status(
                    "res-001",
                    "checked_in",
                    route=WRITE_ROUTE_LEGACY,
                )
        finally:
            await http.aclose()

        assert len(write_route.calls) == 1

    @respx.mock
    async def test_transient_failure_issues_one_request(self) -> None:
        """A connection failure is not retried either."""
        _mock_token()
        write_route = respx.put(_legacy_url()).mock(
            side_effect=httpx.ConnectError("boom"),
        )

        http = httpx.AsyncClient()
        try:
            with (
                patch("asyncio.sleep", new_callable=AsyncMock),
                pytest.raises(GuestyConnectionError),
            ):
                await _v3_client(http).set_reservation_status(
                    "res-001",
                    "checked_in",
                    route=WRITE_ROUTE_LEGACY,
                )
        finally:
            await http.aclose()

        assert len(write_route.calls) == 1

    @respx.mock
    async def test_other_callers_keep_their_retries(self) -> None:
        """The default mode is unchanged for every other caller."""
        _mock_token()
        route = respx.put(
            f"{BASE_URL}{RESERVATION_STATUS_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(500, json={"error": "no"}))

        http = httpx.AsyncClient()
        try:
            with (
                patch("asyncio.sleep", new_callable=AsyncMock),
                pytest.raises(GuestyApiError),
            ):
                await _v3_client(http).set_reservation_status(
                    "res-001",
                    "checked_in",
                )
        finally:
            await http.aclose()

        assert len(route.calls) > 1

    @respx.mock
    async def test_not_found_retires_the_candidate(self) -> None:
        """A legacy absence is FR-052 confirmed absence."""
        _mock_token()
        respx.put(_legacy_url()).mock(
            return_value=Response(
                404,
                json={"message": "Reservation not found"},
            ),
        )

        http = httpx.AsyncClient()
        client = _v3_client(http)
        api_client = client._api_client
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await client.set_reservation_status(
                    "res-001",
                    "checked_in",
                    route=WRITE_ROUTE_LEGACY,
                )
        finally:
            await http.aclose()

        assert "no longer exists" in str(exc_info.value)
        assert "res-001" in api_client.confirmed_absent_reservations

    @respx.mock
    async def test_retirement_shaped_answer_disables_routing(
        self,
    ) -> None:
        """A retirement answer self-disables and names the date."""
        _mock_token()
        respx.put(_legacy_url()).mock(
            return_value=Response(410, json={"error": "gone"}),
        )

        http = httpx.AsyncClient()
        client = _v3_client(http)
        api_client = client._api_client
        assert api_client.legacy_write_routing_available is True
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await client.set_reservation_status(
                    "res-001",
                    "checked_in",
                    route=WRITE_ROUTE_LEGACY,
                )
        finally:
            await http.aclose()

        assert LEGACY_RESERVATIONS_RETIREMENT_DATE in str(exc_info.value)
        assert api_client.legacy_write_routing_available is False


class TestV3NoteReadIdentity:
    """A v3 note read accepts only the record asked for."""

    @respx.mock
    async def test_a_foreign_record_is_not_written_to(self) -> None:
        """Another reservation's notes never reach the write."""
        _mock_token()
        respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
            return_value=Response(
                200,
                json={
                    "results": [
                        {
                            "_id": "res-999",
                            "notes": {"other": "Someone else"},
                        },
                    ],
                },
            ),
        )
        write = respx.put(
            f"{BASE_URL}{RESERVATION_NOTES_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await _v3_client(http).add_reservation_note(
                    "res-001",
                    "New note",
                )
        finally:
            await http.aclose()

        assert not write.called
        assert "not found" in str(exc_info.value)

    @respx.mock
    async def test_a_null_results_envelope_writes_nothing(self) -> None:
        """A results key that is not a list carries no record."""
        _mock_token()
        respx.get(f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}").mock(
            return_value=Response(200, json={"results": None}),
        )
        write = respx.put(
            f"{BASE_URL}{RESERVATION_NOTES_PATH.format(reservation_id='res-001')}",
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            with pytest.raises(GuestyActionError):
                await _v3_client(http).add_reservation_note(
                    "res-001",
                    "New note",
                )
        finally:
            await http.aclose()

        assert not write.called


class TestRoutingRetiredMidWrite:
    """Routing is re-read after the routed note read."""

    @respx.mock
    async def test_a_self_disable_during_the_read_stops_the_write(
        self,
    ) -> None:
        """No PUT follows a retirement seen while reading."""
        _mock_token()
        http = httpx.AsyncClient()
        client = _v3_client(http)

        def _retire(request: httpx.Request) -> Response:
            """Disable routing as the read is answered.

            Args:
                request: The legacy read request.

            Returns:
                A successful legacy reservation document.
            """
            client._api_client.disable_legacy_write_routing()
            return Response(200, json={"_id": "res-001", "note": "Hi"})

        respx.get(_legacy_url()).mock(side_effect=_retire)
        write = respx.put(_legacy_url()).mock(
            return_value=Response(200, json={}),
        )

        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await client.add_reservation_note(
                    "res-001",
                    "New note",
                    route=WRITE_ROUTE_LEGACY,
                )
        finally:
            await http.aclose()

        assert not write.called
        assert "2027-03-01" in str(exc_info.value)

    @respx.mock
    async def test_a_retirement_answer_says_the_request_was_sent(
        self,
    ) -> None:
        """The post-request message never claims nothing was sent."""
        _mock_token()
        respx.put(_legacy_url()).mock(
            return_value=Response(410, json={"error": "gone"}),
        )

        http = httpx.AsyncClient()
        try:
            with pytest.raises(GuestyActionError) as exc_info:
                await _v3_client(http).set_reservation_status(
                    "res-001",
                    "checked_in",
                    route=WRITE_ROUTE_LEGACY,
                )
        finally:
            await http.aclose()

        message = str(exc_info.value)
        assert "No request was sent" not in message
        assert "410" in message
        assert "2027-03-01" in message


# ── Feature 014: strict by-ID read (T004, T005) ─────────────────────

_BY_ID_URL = f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}"


async def _read_by_id(reservation_id: str = "res-001") -> dict[str, object]:
    """Run one strict by-ID read against a fresh v3 actions client.

    Args:
        reservation_id: The reservation to read.

    Returns:
        The single matching raw record.
    """
    http = httpx.AsyncClient()
    try:
        return await _v3_client(http).read_reservation_by_id(reservation_id)
    finally:
        await http.aclose()


async def _read_by_id_error(reservation_id: str = "res-001") -> GuestyByIdReadError:
    """Run one strict by-ID read that must fail.

    Args:
        reservation_id: The reservation to read.

    Returns:
        The raised by-ID read error.
    """
    with pytest.raises(GuestyByIdReadError) as exc_info:
        await _read_by_id(reservation_id)
    return exc_info.value


class TestReadReservationById:
    """The by-ID read proves exactly one record or fails (FR-004, FR-005)."""

    @respx.mock
    async def test_sends_one_get_with_only_reservation_ids_param(self) -> None:
        """Exactly one GET carries reservationIds and nothing else."""
        _mock_token()
        route = respx.get(_BY_ID_URL).mock(
            return_value=Response(
                200, json={"results": [{"reservationId": "res-001"}]}
            ),
        )

        await _read_by_id()

        assert route.call_count == 1
        assert dict(route.calls[0].request.url.params) == {"reservationIds": "res-001"}

    @respx.mock
    async def test_returns_the_single_matching_raw_record(self) -> None:
        """The raw record comes back untouched."""
        _mock_token()
        record = {"reservationId": "res-001", "status": "confirmed", "x": [1]}
        respx.get(_BY_ID_URL).mock(
            return_value=Response(200, json={"results": [record]}),
        )

        assert await _read_by_id() == record

    @respx.mock
    async def test_empty_results_is_not_found(self) -> None:
        """An empty result list is not found."""
        _mock_token()
        respx.get(_BY_ID_URL).mock(return_value=Response(200, json={"results": []}))

        assert (await _read_by_id_error()).category == "not_found"

    @respx.mock
    async def test_single_other_id_is_mismatched(self) -> None:
        """One record for another reservation is mismatched."""
        _mock_token()
        respx.get(_BY_ID_URL).mock(
            return_value=Response(
                200, json={"results": [{"reservationId": "res-999"}]}
            ),
        )

        assert (await _read_by_id_error()).category == "mismatched"

    @pytest.mark.parametrize(
        "item",
        [
            {"id": "res-001"},
            {"_id": "res-001"},
            {"reservationId": 1},
            {"reservationId": None, "id": "res-001"},
        ],
    )
    @respx.mock
    async def test_missing_or_non_string_reservation_id_is_malformed(
        self, item: dict[str, object]
    ) -> None:
        """Only reservationId identifies the record; id and _id never do."""
        _mock_token()
        respx.get(_BY_ID_URL).mock(return_value=Response(200, json={"results": [item]}))

        assert (await _read_by_id_error()).category == "malformed"

    @pytest.mark.parametrize(
        "body",
        [
            {"results": [{"reservationId": "res-001"}, {"reservationId": "res-2"}]},
            {"results": [{"reservationId": "res-001"}, {"reservationId": "res-001"}]},
            {"reservationId": "res-001"},
            [{"reservationId": "res-001"}],
            {"data": [{"reservationId": "res-001"}]},
            {"results": {"reservationId": "res-001"}},
            {"results": ["res-001"]},
            b"not json",
            {"results": [{"reservationId": "res-001"}], "pagination": None},
            {"results": [{"reservationId": "res-001"}], "pagination": []},
            {"results": [{"reservationId": "res-001"}], "pagination": {}},
            {
                "results": [{"reservationId": "res-001"}],
                "pagination": {"hasMore": None},
            },
            {
                "results": [{"reservationId": "res-001"}],
                "pagination": {"hasMore": "true"},
            },
            {
                "results": [{"reservationId": "res-001"}],
                "pagination": {"hasMore": True},
            },
        ],
        ids=[
            "two-records",
            "duplicate-matches",
            "bare-dict",
            "bare-list",
            "results-missing",
            "results-not-list",
            "non-object-item",
            "non-json",
            "pagination-null",
            "pagination-list",
            "pagination-empty",
            "has-more-null",
            "has-more-string",
            "has-more-true",
        ],
    )
    @respx.mock
    async def test_unprovable_envelope_is_malformed(self, body: object) -> None:
        """An envelope that cannot prove one complete record is malformed."""
        _mock_token()
        response = (
            Response(200, content=body)
            if isinstance(body, bytes)
            else Response(200, json=body)
        )
        respx.get(_BY_ID_URL).mock(return_value=response)

        assert (await _read_by_id_error()).category == "malformed"

    @pytest.mark.parametrize(
        "extra",
        [
            {},
            {"pagination": {"hasMore": False}},
            {"pagination": {"hasMore": False, "n": 1}},
        ],
    )
    @respx.mock
    async def test_absent_pagination_or_has_more_false_is_accepted(
        self, extra: dict[str, object]
    ) -> None:
        """No pagination key, or hasMore exactly false, proves completeness."""
        _mock_token()
        respx.get(_BY_ID_URL).mock(
            return_value=Response(
                200, json={"results": [{"reservationId": "res-001"}], **extra}
            ),
        )

        assert await _read_by_id() == {"reservationId": "res-001"}

    @pytest.mark.parametrize("reservation_id", ["", "   ", None, 7])
    @respx.mock
    async def test_blank_reservation_id_sends_nothing(
        self, reservation_id: Any
    ) -> None:
        """A blank or non-string target is refused before any request."""
        token = respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        route = respx.get(_BY_ID_URL).mock(return_value=Response(200, json={}))

        with pytest.raises(ValueError, match="reservation_id"):
            await _read_by_id(reservation_id)

        assert not route.called
        assert not token.called


class TestReadReservationByIdFailures:
    """Every by-ID read failure is one attempt and a fixed category (R5)."""

    @pytest.mark.parametrize(
        ("status", "category"),
        [
            (400, "http"),
            (404, "http"),
            (500, "http"),
            (503, "http"),
            (401, "authentication"),
            (403, "authentication"),
            (429, "rate_limit"),
        ],
    )
    @respx.mock
    async def test_http_status_maps_to_category(
        self, status: int, category: str
    ) -> None:
        """A non-2xx status maps to its category with no retry."""
        token = respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        route = respx.get(_BY_ID_URL).mock(return_value=Response(status, json={}))

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            error = await _read_by_id_error()

        assert error.category == category
        assert route.call_count == 1
        assert token.call_count == 1
        sleep.assert_not_awaited()

    @respx.mock
    async def test_401_does_not_renew_token(self) -> None:
        """A 401 neither invalidates nor re-requests the token."""
        token = respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        route = respx.get(_BY_ID_URL).mock(return_value=Response(401, json={}))

        error = await _read_by_id_error()

        assert error.category == "authentication"
        assert token.call_count == 1
        assert route.call_count == 1

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    @respx.mock
    async def test_429_and_5xx_do_not_sleep_or_retry(self, status: int) -> None:
        """Rate limits and server errors are never retried or slept on."""
        _mock_token()
        route = respx.get(_BY_ID_URL).mock(return_value=Response(status, json={}))

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            await _read_by_id_error()

        assert route.call_count == 1
        sleep.assert_not_awaited()

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("boom"),
            httpx.ReadTimeout("slow"),
            httpx.ConnectTimeout("slow"),
            httpx.ReadError("reset"),
            httpx.RemoteProtocolError("bad"),
        ],
    )
    @respx.mock
    async def test_timeout_connect_and_other_transport_errors_are_transport(
        self, exc: Exception
    ) -> None:
        """Every transport failure is one attempt in the transport category."""
        _mock_token()
        route = respx.get(_BY_ID_URL).mock(side_effect=exc)

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            error = await _read_by_id_error()

        assert error.category == "transport"
        assert route.call_count == 1
        sleep.assert_not_awaited()

    @pytest.mark.parametrize(
        ("token_response", "raised", "category"),
        [
            (Response(401, json={}), None, "authentication"),
            (None, GuestyPermissionError("denied"), "authentication"),
            (None, GuestyRateLimitError("budget"), "rate_limit"),
            (Response(429, json={}), None, "http"),
            (Response(500, json={}), None, "http"),
            (Response(200, content=b"not json"), None, "http"),
            (None, httpx.ConnectError("down"), "transport"),
            (None, GuestyConnectionError("down"), "transport"),
            (None, httpx.ReadError("reset"), "transport"),
            (None, RuntimeError("other"), "authentication"),
        ],
        ids=[
            "token-401",
            "token-permission",
            "local-budget",
            "token-429",
            "token-500",
            "token-bad-body",
            "token-connect",
            "token-connection-error",
            "token-transport",
            "token-other",
        ],
    )
    @respx.mock
    async def test_token_failure_category(
        self,
        token_response: Response | None,
        raised: Exception | None,
        category: str,
    ) -> None:
        """A token failure maps to its category and sends no by-ID GET."""
        token = respx.post(TOKEN_URL)
        if token_response is not None:
            token.mock(return_value=token_response)
        elif isinstance(raised, httpx.ConnectError):
            token.mock(side_effect=raised)
        route = respx.get(_BY_ID_URL).mock(
            return_value=Response(200, json={"results": []}),
        )
        http = httpx.AsyncClient()
        client = _v3_client(http)
        try:
            if raised is not None and not isinstance(raised, httpx.ConnectError):
                with (
                    patch.object(
                        client._api_client._token_manager,
                        "get_token",
                        AsyncMock(side_effect=raised),
                    ),
                    pytest.raises(GuestyByIdReadError) as exc_info,
                ):
                    await client.read_reservation_by_id("res-001")
            else:
                with pytest.raises(GuestyByIdReadError) as exc_info:
                    await client.read_reservation_by_id("res-001")
        finally:
            await http.aclose()

        assert exc_info.value.category == category
        assert route.call_count == 0

    @pytest.mark.parametrize(
        "response",
        [
            Response(500, text="SENTINEL-BODY-MARKER"),
            Response(400, text="SENTINEL-BODY-MARKER"),
            Response(200, text="SENTINEL-BODY-MARKER"),
            Response(
                200,
                json={
                    "results": [{"reservationId": "SENTINEL-BODY-MARKER"}],
                },
            ),
        ],
    )
    @respx.mock
    async def test_errors_and_logs_carry_no_body_or_exception_text(
        self, response: Response, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No body, status text or exception detail reaches errors or logs."""
        _mock_token()
        respx.get(_BY_ID_URL).mock(return_value=response)

        with caplog.at_level(logging.DEBUG):
            error = await _read_by_id_error()

        assert "SENTINEL-BODY-MARKER" not in str(error)
        assert "SENTINEL-BODY-MARKER" not in caplog.text
        assert error.__cause__ is None

    @respx.mock
    async def test_transport_exception_text_is_not_carried(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A transport exception's text never reaches the error or logs."""
        _mock_token()
        respx.get(_BY_ID_URL).mock(side_effect=httpx.ReadError("SENTINEL-EXC-TEXT"))

        with caplog.at_level(logging.DEBUG):
            error = await _read_by_id_error()

        assert "SENTINEL-EXC-TEXT" not in str(error)
        assert "SENTINEL-EXC-TEXT" not in caplog.text
        assert error.__cause__ is None


# ── Feature 014: prefetched note write (T007) ───────────────────────

_NOTES_URL = f"{BASE_URL}{RESERVATION_NOTES_PATH.format(reservation_id='res-001')}"


class TestPrefetchedNoteWrite:
    """A verified record's notes are written without a second read (R11)."""

    @respx.mock
    async def test_prefetched_notes_issue_no_read(self) -> None:
        """Prefetched notes skip the GET and send one PUT."""
        _mock_token()
        read = respx.get(_BY_ID_URL).mock(return_value=Response(200, json={}))
        write = respx.put(_NOTES_URL).mock(return_value=Response(200, json={}))
        http = httpx.AsyncClient()
        try:
            result = await _v3_client(http).add_reservation_note(
                "res-001", "New", prefetched_notes={"other": "Old"}
            )
        finally:
            await http.aclose()

        assert result.success is True
        assert read.call_count == 0
        assert write.call_count == 1

    @respx.mock
    async def test_prefetched_notes_echo_every_field_and_append(self) -> None:
        """Every prefetched field is echoed and the text is appended."""
        _mock_token()
        write = respx.put(_NOTES_URL).mock(return_value=Response(200, json={}))
        http = httpx.AsyncClient()
        try:
            await _v3_client(http).add_reservation_note(
                "res-001",
                "New",
                prefetched_notes={"other": "Old", "guest": "G", "cleaning": None},
            )
        finally:
            await http.aclose()

        assert json.loads(write.calls[0].request.content) == {
            "other": f"Old{NOTE_SEPARATOR}New",
            "guest": "G",
            "cleaning": None,
        }

    @respx.mock
    async def test_empty_prefetched_notes_write_only_other(self) -> None:
        """An empty mapping writes the new text alone."""
        _mock_token()
        write = respx.put(_NOTES_URL).mock(return_value=Response(200, json={}))
        http = httpx.AsyncClient()
        try:
            await _v3_client(http).add_reservation_note(
                "res-001", "New", prefetched_notes={}
            )
        finally:
            await http.aclose()

        assert json.loads(write.calls[0].request.content) == {"other": "New"}

    @respx.mock
    async def test_before_write_runs_once_before_put(self) -> None:
        """The recheck runs exactly once, before the PUT is sent."""
        _mock_token()
        write = respx.put(_NOTES_URL).mock(return_value=Response(200, json={}))
        seen: list[int] = []

        def check() -> None:
            """Record how many PUTs were sent when the check ran."""
            seen.append(write.call_count)

        http = httpx.AsyncClient()
        try:
            await _v3_client(http).add_reservation_note(
                "res-001",
                "New",
                prefetched_notes={},
                single_attempt=True,
                before_write=check,
            )
        finally:
            await http.aclose()

        assert seen == [0]
        assert write.call_count == 1

    @pytest.mark.parametrize("legacy_client", [False, True])
    @respx.mock
    async def test_prefetched_notes_refuse_legacy_route(
        self, legacy_client: bool
    ) -> None:
        """Prefetched notes with any legacy route send nothing at all."""
        token = respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        everything = respx.route().mock(return_value=Response(200, json={}))
        http = httpx.AsyncClient()
        client = _make_actions_client(
            http, RESERVATIONS_API_LEGACY if legacy_client else RESERVATIONS_API_V3
        )
        kwargs: dict[str, Any] = {} if legacy_client else {"route": WRITE_ROUTE_LEGACY}
        # A retired legacy generation would otherwise raise a routing
        # refusal; the prefetched check must come first.
        client._api_client.disable_legacy_write_routing()
        try:
            with pytest.raises(ValueError, match="prefetched"):
                await client.add_reservation_note(
                    "res-001", "New", prefetched_notes={}, **kwargs
                )
        finally:
            await http.aclose()

        assert not token.called
        assert not everything.called

    @respx.mock
    async def test_none_prefetched_notes_keeps_the_read(self) -> None:
        """Baseline control: None keeps today's read-modify-write."""
        _mock_token()
        read = respx.get(_BY_ID_URL).mock(
            return_value=Response(
                200,
                json={"results": [{"reservationId": "res-001", "notes": {}}]},
            ),
        )
        write = respx.put(_NOTES_URL).mock(return_value=Response(200, json={}))
        http = httpx.AsyncClient()
        try:
            await _v3_client(http).add_reservation_note(
                "res-001", "New", prefetched_notes=None
            )
        finally:
            await http.aclose()

        assert read.call_count == 1
        assert write.call_count == 1

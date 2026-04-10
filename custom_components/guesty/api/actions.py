# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Client for Guesty write-operation actions."""

from __future__ import annotations

import json
import re

import httpx

from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    CALENDAR_ENDPOINT,
    LISTINGS_ENDPOINT,
    MAX_DESCRIPTION_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_TASK_TITLE_LENGTH,
    NOTE_SEPARATOR,
    RESERVATIONS_ENDPOINT,
    TASKS_ENDPOINT,
    VALID_CALENDAR_OPS,
    VALID_LISTING_STATUSES,
)
from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import ActionResult

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_MAX_DETAIL_LENGTH = 200


class GuestyActionsClient:
    """Client for Guesty write-operation actions.

    Orchestrates input validation, API calls, and result
    construction for automation actions. This class has zero
    Home Assistant imports.

    Attributes:
        _api_client: Guesty API client for HTTP communication.
    """

    def __init__(self, api_client: GuestyApiClient) -> None:
        """Initialize GuestyActionsClient.

        Args:
            api_client: Guesty API client for HTTP requests.
        """
        self._api_client = api_client

    async def add_reservation_note(
        self,
        reservation_id: str,
        note_text: str,
    ) -> ActionResult:
        """Add a note to a reservation via read-modify-write.

        Fetches the current note, appends the new text with a
        separator, then writes the combined note back.

        Args:
            reservation_id: Target reservation identifier.
            note_text: Note text to append.

        Returns:
            ActionResult with the reservation identifier.

        Raises:
            ValueError: If inputs fail validation.
            GuestyActionError: If the API returns a non-success
                HTTP response.
            GuestyResponseError: If the response is malformed.
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
        """
        self._validate_reservation_note(reservation_id, note_text)

        get_path = f"{RESERVATIONS_ENDPOINT}/{reservation_id}"
        get_response = await self._api_client._request(
            "GET",
            get_path,
        )

        if not get_response.is_success:
            raise GuestyActionError(
                self._error_detail(
                    "Failed to fetch reservation",
                    reservation_id,
                    get_response,
                ),
                target_id=reservation_id,
                action_type="add_reservation_note",
            )

        try:
            reservation_data = get_response.json()
        except Exception as exc:
            raise GuestyResponseError(
                "Reservation response is not valid JSON",
            ) from exc

        if not isinstance(reservation_data, dict):
            raise GuestyResponseError(
                "Reservation response must be a JSON object",
            )

        existing_note = reservation_data.get("note", "") or ""
        if existing_note:
            combined_note = existing_note + NOTE_SEPARATOR + note_text
        else:
            combined_note = note_text

        put_path = f"{RESERVATIONS_ENDPOINT}/{reservation_id}"
        put_response = await self._api_client._request(
            "PUT",
            put_path,
            json_data={"note": combined_note},
        )

        if not put_response.is_success:
            raise GuestyActionError(
                self._error_detail(
                    "Failed to update reservation note",
                    reservation_id,
                    put_response,
                ),
                target_id=reservation_id,
                action_type="add_reservation_note",
            )

        return ActionResult(
            success=True,
            target_id=reservation_id,
        )

    async def set_listing_status(
        self,
        listing_id: str,
        status: str,
    ) -> ActionResult:
        """Set the active/listed status of a listing.

        Args:
            listing_id: Target listing identifier.
            status: New status ('active' or 'inactive').

        Returns:
            ActionResult with the listing identifier.

        Raises:
            ValueError: If inputs fail validation.
            GuestyActionError: If the API returns a non-success
                HTTP response.
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
        """
        self._validate_listing_status(listing_id, status)

        if status == "active":
            body: dict[str, object] = {
                "active": True,
                "listed": True,
            }
        else:
            body = {"active": False}

        path = f"{LISTINGS_ENDPOINT}/{listing_id}"
        response = await self._api_client._request(
            "PUT",
            path,
            json_data=body,
        )

        if not response.is_success:
            raise GuestyActionError(
                self._error_detail(
                    "Failed to set listing status",
                    listing_id,
                    response,
                ),
                target_id=listing_id,
                action_type="set_listing_status",
            )

        return ActionResult(
            success=True,
            target_id=listing_id,
        )

    async def create_task(
        self,
        listing_id: str,
        task_title: str,
        *,
        description: str | None = None,
        assignee: str | None = None,
    ) -> ActionResult:
        """Create an operational task for a listing.

        Args:
            listing_id: Target listing identifier.
            task_title: Title for the new task.
            description: Optional task description.
            assignee: Optional assignee identifier.

        Returns:
            ActionResult with the new task identifier.

        Raises:
            ValueError: If inputs fail validation.
            GuestyActionError: If the API returns a non-success
                HTTP response.
            GuestyResponseError: If the response is malformed.
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
        """
        self._validate_create_task(
            listing_id,
            task_title,
            description=description,
            assignee=assignee,
        )

        body: dict[str, object] = {
            "listingId": listing_id,
            "title": task_title,
        }
        if description is not None:
            body["description"] = description
        if assignee is not None:
            body["assigneeId"] = assignee

        response = await self._api_client._request(
            "POST",
            TASKS_ENDPOINT,
            json_data=body,
        )

        if not response.is_success:
            raise GuestyActionError(
                self._error_detail(
                    "Failed to create task",
                    listing_id,
                    response,
                ),
                target_id=listing_id,
                action_type="create_task",
            )

        try:
            data = response.json()
        except Exception as exc:
            raise GuestyResponseError(
                "Create task response is not valid JSON",
            ) from exc

        if not isinstance(data, dict):
            raise GuestyResponseError(
                "Create task response must be a JSON object",
            )

        task_id = data.get("_id", listing_id)

        return ActionResult(
            success=True,
            target_id=task_id,
        )

    async def set_calendar_availability(
        self,
        listing_id: str,
        start_date: str,
        end_date: str,
        operation: str,
    ) -> ActionResult:
        """Set calendar availability for a listing date range.

        Args:
            listing_id: Target listing identifier.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            operation: Calendar operation ('block' or 'unblock').

        Returns:
            ActionResult with the listing identifier.

        Raises:
            ValueError: If inputs fail validation.
            GuestyActionError: If the API returns a non-success
                HTTP response.
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
        """
        self._validate_calendar_availability(
            listing_id,
            start_date,
            end_date,
            operation,
        )

        status = "unavailable" if operation == "block" else "available"
        body: dict[str, object] = {
            "dateFrom": start_date,
            "dateTo": end_date,
            "status": status,
        }

        path = CALENDAR_ENDPOINT.format(listing_id=listing_id)
        response = await self._api_client._request(
            "PUT",
            path,
            json_data=body,
        )

        if not response.is_success:
            raise GuestyActionError(
                self._error_detail(
                    "Failed to set calendar availability",
                    listing_id,
                    response,
                ),
                target_id=listing_id,
                action_type="set_calendar_availability",
            )

        return ActionResult(
            success=True,
            target_id=listing_id,
        )

    # ── Error detail helper ────────────────────────────────────

    @staticmethod
    def _error_detail(
        prefix: str,
        target_id: str,
        response: httpx.Response,
    ) -> str:
        """Build an actionable error message with response detail.

        Includes a safely-truncated response payload for
        diagnosing 400/404/422 errors.

        Args:
            prefix: Error message prefix.
            target_id: Targeted resource identifier.
            response: The HTTP response with the error.

        Returns:
            Formatted error message with detail.
        """
        try:
            detail = json.dumps(response.json())
        except Exception:
            detail = response.text
        if len(detail) > _MAX_DETAIL_LENGTH:
            detail = detail[:_MAX_DETAIL_LENGTH] + "..."
        return f"{prefix} '{target_id}' ({response.status_code}): {detail}"

    # ── Validation helpers ──────────────────────────────────────

    @staticmethod
    def _validate_reservation_note(
        reservation_id: str,
        note_text: str,
    ) -> None:
        """Validate add_reservation_note inputs.

        Args:
            reservation_id: Reservation identifier to validate.
            note_text: Note text to validate.

        Raises:
            ValueError: If any input is invalid.
        """
        if not reservation_id:
            msg = "reservation_id must be non-empty"
            raise ValueError(msg)
        if not note_text:
            msg = "note_text must be non-empty"
            raise ValueError(msg)
        if len(note_text) > MAX_NOTE_LENGTH:
            msg = f"note_text exceeds maximum length of {MAX_NOTE_LENGTH} characters"
            raise ValueError(msg)

    @staticmethod
    def _validate_listing_status(
        listing_id: str,
        status: str,
    ) -> None:
        """Validate set_listing_status inputs.

        Args:
            listing_id: Listing identifier to validate.
            status: Status value to validate.

        Raises:
            ValueError: If any input is invalid.
        """
        if not listing_id:
            msg = "listing_id must be non-empty"
            raise ValueError(msg)
        if status not in VALID_LISTING_STATUSES:
            msg = (
                f"invalid status '{status}'; "
                f"expected one of "
                f"{sorted(VALID_LISTING_STATUSES)}"
            )
            raise ValueError(msg)

    @staticmethod
    def _validate_create_task(
        listing_id: str,
        task_title: str,
        *,
        description: str | None,
        assignee: str | None,
    ) -> None:
        """Validate create_task inputs.

        Args:
            listing_id: Listing identifier to validate.
            task_title: Task title to validate.
            description: Optional description to validate.
            assignee: Optional assignee to validate.

        Raises:
            ValueError: If any input is invalid.
        """
        if not listing_id:
            msg = "listing_id must be non-empty"
            raise ValueError(msg)
        if not task_title:
            msg = "task_title must be non-empty"
            raise ValueError(msg)
        if len(task_title) > MAX_TASK_TITLE_LENGTH:
            msg = (
                f"task_title exceeds maximum length "
                f"of {MAX_TASK_TITLE_LENGTH} characters"
            )
            raise ValueError(msg)
        if description is not None:
            if not description:
                msg = "description must be non-empty when provided"
                raise ValueError(msg)
            if len(description) > MAX_DESCRIPTION_LENGTH:
                msg = (
                    f"description exceeds maximum length "
                    f"of {MAX_DESCRIPTION_LENGTH} characters"
                )
                raise ValueError(msg)
        if assignee is not None and not assignee:
            msg = "assignee must be non-empty when provided"
            raise ValueError(msg)

    @staticmethod
    def _validate_calendar_availability(
        listing_id: str,
        start_date: str,
        end_date: str,
        operation: str,
    ) -> None:
        """Validate set_calendar_availability inputs.

        Args:
            listing_id: Listing identifier to validate.
            start_date: Start date to validate.
            end_date: End date to validate.
            operation: Calendar operation to validate.

        Raises:
            ValueError: If any input is invalid.
        """
        if not listing_id:
            msg = "listing_id must be non-empty"
            raise ValueError(msg)
        if not _DATE_RE.match(start_date):
            msg = f"start_date must be YYYY-MM-DD format, got '{start_date}'"
            raise ValueError(msg)
        if not _DATE_RE.match(end_date):
            msg = f"end_date must be YYYY-MM-DD format, got '{end_date}'"
            raise ValueError(msg)
        if end_date < start_date:
            msg = f"end_date '{end_date}' must be >= start_date '{start_date}'"
            raise ValueError(msg)
        if operation not in VALID_CALENDAR_OPS:
            msg = (
                f"invalid operation '{operation}'; "
                f"expected one of "
                f"{sorted(VALID_CALENDAR_OPS)}"
            )
            raise ValueError(msg)

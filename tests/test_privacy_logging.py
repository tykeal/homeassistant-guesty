# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Privacy tests for the v3 reservation and guest paths.

Guest names, phone numbers, email addresses and confirmation codes
must not reach a log record at **any** level, debug included, so
that enabling debug logging to confirm a v3 assumption can never
expose guest details (FR-021, NFR-009).
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from httpx import Response

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    BASE_URL,
    RESERVATIONS_API_V3,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    TOKEN_URL,
)
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FakeTokenStorage,
    make_token_response,
    make_v3_reservation,
    make_v3_search_page,
)

SEARCH_URL = f"{BASE_URL}{RESERVATIONS_V3_SEARCH_ENDPOINT}"

GUEST_NAME = "Jane Traveller"
GUEST_PHONE = "+15551234567"
GUEST_EMAIL = "jane@example.com"
CONFIRMATION_CODE = "CONF-SECRET-42"

PII = (GUEST_NAME, GUEST_PHONE, GUEST_EMAIL, CONFIRMATION_CODE)


def _mock_token() -> None:
    """Register the OAuth token route used by every request."""
    respx.post(TOKEN_URL).mock(
        return_value=Response(200, json=make_token_response()),
    )


def _make_client() -> GuestyApiClient:
    """Create a v3-selected API client for tests.

    Returns:
        A GuestyApiClient configured for the v3 reservation path.
    """
    http = httpx.AsyncClient()
    token_manager = GuestyTokenManager(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        http_client=http,
        storage=FakeTokenStorage(),
        refresh_buffer=0,
    )
    return GuestyApiClient(
        token_manager=token_manager,
        http_client=http,
        reservations_api=RESERVATIONS_API_V3,
    )


def _guest_body() -> dict[str, Any]:
    """Build a guests-crud body carrying every sensitive field.

    Returns:
        A synthetic guests-crud response body.
    """
    return {
        "_id": "guest-001",
        "fullName": GUEST_NAME,
        "phone": GUEST_PHONE,
        "email": GUEST_EMAIL,
    }


async def _refresh(client: GuestyApiClient) -> None:
    """Run a v3 refresh: the search and its guest lookups.

    Args:
        client: The client under test.
    """
    result = await client.get_reservations()
    await client.resolve_guests(
        [
            reservation.guest.guest_id
            for reservation in result.reservations
            if reservation.guest is not None and reservation.guest.guest_id is not None
        ],
    )


class TestPrivacyLogging:
    """Tests that no guest data is logged at any level (T057)."""

    @respx.mock
    async def test_successful_refresh_logs_no_guest_data(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A full v3 refresh logs no personal data at DEBUG."""
        _mock_token()
        reservation = make_v3_reservation(
            "res-000001",
            confirmationCode=CONFIRMATION_CODE,
        )
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page([reservation], skip=0),
            ),
        )
        respx.get(f"{BASE_URL}/guests-crud/guest-001").mock(
            return_value=Response(200, json=_guest_body()),
        )

        client = _make_client()
        with caplog.at_level(logging.DEBUG):
            await _refresh(client)

        for secret in PII:
            assert secret not in caplog.text

    @respx.mock
    async def test_guest_lookup_failure_logs_no_guest_data(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A failed lookup logs the id and nothing else."""
        _mock_token()
        reservation = make_v3_reservation(
            "res-000001",
            confirmationCode=CONFIRMATION_CODE,
        )
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page([reservation], skip=0),
            ),
        )
        respx.get(f"{BASE_URL}/guests-crud/guest-001").mock(
            return_value=Response(500, json=_guest_body()),
        )

        client = _make_client()
        with (
            caplog.at_level(logging.DEBUG),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await _refresh(client)

        assert "guest-001" in caplog.text
        for secret in PII:
            assert secret not in caplog.text

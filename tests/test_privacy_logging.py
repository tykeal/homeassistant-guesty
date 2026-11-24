# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Privacy tests for the v3 reservation and guest paths.

Guest names, phone numbers, email addresses and confirmation codes
must not reach a log record at **any** level, debug included, so
that enabling debug logging to confirm a v3 assumption can never
expose guest details (FR-021, NFR-009).
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from httpx import Response
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    BASE_URL,
    RESERVATIONS_API_V3,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    TOKEN_URL,
)
from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyFilterRejectedError,
)
from custom_components.guesty.api.guest_cache import GuestLookupResult
from custom_components.guesty.api.models import (
    ActionResult,
    GuestyGuest,
    GuestyListing,
    GuestyReservation,
    GuestyStaySegment,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RESERVATIONS_API,
    DOMAIN,
)
from custom_components.guesty.coordinator import (
    ListingsCoordinator,
    ReservationsCoordinator,
)
from custom_components.guesty.diagnostics import async_get_config_entry_diagnostics
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FakeTokenStorage,
    make_fetch_result,
    make_identity_reservations,
    make_listing_dict,
    make_token_response,
    make_v3_reservation,
    make_v3_search_page,
)
from tests.test_actions import _setup_entry

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


def _private_reservation(reservation_id: str) -> GuestyReservation:
    """Build a reservation carrying every sensitive field.

    Args:
        reservation_id: Guesty reservation identifier.

    Returns:
        A reservation whose guest data must never be logged.
    """
    now = datetime.now(UTC)
    return GuestyReservation(
        id=reservation_id,
        listing_id="listing-001",
        status="confirmed",
        check_in=now + timedelta(days=1),
        check_out=now + timedelta(days=3),
        confirmation_code=CONFIRMATION_CODE,
        guest=GuestyGuest(
            full_name=GUEST_NAME,
            phone=GUEST_PHONE,
            email=GUEST_EMAIL,
            guest_id="guest-001",
        ),
    )


class TestPrivacyLogging:
    """Tests that no guest data is logged at any level (T057)."""

    @pytest.mark.parametrize("timezone", ["", "Invalid/Private"])
    async def test_unknown_timing_diagnostics_never_log_private_data(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        timezone: str,
    ) -> None:
        """Missing or invalid room zones never expose guest/timing data."""
        today = datetime.now(UTC).date()
        segment = GuestyStaySegment(
            listing_id="listing-001",
            check_in=None,
            check_out=None,
            check_in_local=(today + timedelta(days=1)).isoformat(),
            check_out_local=(today + timedelta(days=3)).isoformat(),
            raw_position=0,
        )
        reservation = GuestyReservation(
            id="res-private-timing",
            listing_id="listing-001",
            status="confirmed",
            check_in=None,
            check_out=None,
            confirmation_code=CONFIRMATION_CODE,
            guest=GuestyGuest(
                full_name=GUEST_NAME,
                phone=GUEST_PHONE,
                email=GUEST_EMAIL,
                guest_id="guest-001",
            ),
            is_v3=True,
            raw_stay_count=1,
            stay=(segment,),
        )
        listing = GuestyListing.from_api_dict(
            make_listing_dict(
                _id="listing-001",
                timezone=timezone,
                defaultCheckInTime="16:00",
                defaultCheckOutTime="11:00",
            ),
        )
        assert listing is not None
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Guesty (test)",
            data={
                CONF_CLIENT_ID: FAKE_CLIENT_ID,
                CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
            },
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
            unique_id=FAKE_CLIENT_ID,
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([reservation]),
        )
        api_client.resolve_guests = AsyncMock(
            return_value=GuestLookupResult(),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {listing.id: listing}
        listings_coordinator.timing_listings = {listing.id: listing}
        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty",
        ):
            result = await coordinator._async_update_data()

        assert result["listing-001"][0].check_in is None
        assert result["listing-001"][0].check_out is None
        integration_logs = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name.startswith("custom_components.guesty")
        )
        for value in PII:
            assert value not in integration_logs

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

    @respx.mock
    async def test_transport_error_text_is_never_logged(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A retry logs the failure's class, not its message."""
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
            side_effect=httpx.ConnectError(
                f"no route while resolving {GUEST_EMAIL}",
            ),
        )

        client = _make_client()
        with (
            caplog.at_level(logging.DEBUG),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await _refresh(client)

        assert "ConnectError" in caplog.text
        for secret in PII:
            assert secret not in caplog.text


class TestIdentityAbortPrivacy:
    """The identity-guard abort path logs no guest data (T066a)."""

    async def test_aborted_refresh_logs_no_guest_data(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An ambiguous identity abort exposes no PII at DEBUG."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Guesty (test)",
            data={
                CONF_CLIENT_ID: FAKE_CLIENT_ID,
                CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
            },
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
            unique_id=FAKE_CLIENT_ID,
            entry_id="entry-privacy-identity",
        )
        entry.add_to_hass(hass)

        api_client = AsyncMock()
        api_client.reservations_api = RESERVATIONS_API_V3
        api_client.resolve_guests = AsyncMock(return_value=GuestLookupResult())
        api_client.get_reservations = AsyncMock(
            side_effect=[
                make_fetch_result([_private_reservation("res-1")]),
                make_fetch_result([_private_reservation("res-9")]),
            ],
        )
        listings = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )
        listing = GuestyListing.from_api_dict(make_listing_dict())
        assert listing is not None
        listings.data = {listing.id: listing}
        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings,
        )

        await coordinator.async_refresh()
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            await coordinator.async_refresh()

        assert not coordinator.last_update_success
        assert "res-1" in caplog.text
        for secret in PII:
            assert secret not in caplog.text


class TestWriteMissPrivacy:
    """Recovery must not expose guest details, confirmation codes, or PINs."""

    @pytest.mark.parametrize("scheduled", [False, True])
    async def test_filter_failure_reports_safe_refresh_details(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture, scheduled: bool
    ) -> None:
        """Scheduled and direct refreshes safely report rejected searches."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        previous = coordinator.data
        secrets = ("sample-sensitive-pin-0072", *PII, FAKE_CLIENT_SECRET)
        secret = " ".join(secrets)
        rejection = GuestyFilterRejectedError(
            secret,
            endpoint="/untrusted-endpoint",
            status_code=400,
            body_excerpt=secret,
        )
        with (
            caplog.at_level(logging.DEBUG),
            patch.object(
                coordinator.api_client,
                "get_reservations",
                new_callable=AsyncMock,
                side_effect=rejection,
            ) as fetch,
            patch.object(
                coordinator.api_client, "resolve_guests", new_callable=AsyncMock
            ) as guests,
        ):
            if scheduled:
                await coordinator._async_refresh(scheduled=True)
            else:
                await coordinator.async_refresh()
            diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        fetch.assert_awaited_once()
        guests.assert_not_awaited()
        assert coordinator.last_update_success is False
        assert coordinator.data is previous
        rendered_tracebacks = "".join(
            "".join(traceback.format_exception(*record.exc_info))
            for record in caplog.records
            if record.exc_info
        )
        for value in secrets:
            assert value not in str(coordinator.last_exception)
            assert value not in caplog.text
            assert value not in rendered_tracebacks
            assert value not in json.dumps(diagnostics, default=str)
        assert "/untrusted-endpoint" not in str(coordinator.last_exception)
        detail = str(coordinator.last_exception)
        assert "/reservations-v3/search" in detail
        assert "400" in detail
        assert "search settings" in detail
        assert "contact support" in detail
        assert "/reservations-v3/search" in caplog.text
        assert "HTTP 400" in caplog.text
        assert "search settings" in caplog.text
        assert "contact support" in caplog.text

    async def test_write_miss_filter_error_reports_safe_details(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A recovery-owned rejected search refuses with safe guidance."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        previous = coordinator.data
        client = hass.data[DOMAIN][entry.entry_id]["actions_client"]
        client.set_reservation_status = AsyncMock()
        secrets = ("sample-sensitive-pin-0072", *PII, FAKE_CLIENT_SECRET)
        secret = " ".join(secrets)
        with (
            caplog.at_level(logging.DEBUG),
            patch.object(
                coordinator.api_client,
                "get_reservations",
                new_callable=AsyncMock,
                side_effect=GuestyFilterRejectedError(
                    secret,
                    endpoint="/untrusted-endpoint",
                    status_code=400,
                    body_excerpt=secret,
                ),
            ) as fetch,
        ):
            with pytest.raises(HomeAssistantError) as raised:
                await hass.services.async_call(
                    DOMAIN,
                    "set_reservation_status",
                    {"reservation_id": "res-new", "status": "checked_in"},
                    blocking=True,
                    return_response=True,
                )
            diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        fetch.assert_awaited_once()
        client.set_reservation_status.assert_not_awaited()
        assert coordinator.data is previous
        assert coordinator.last_update_success is False
        rendered_tracebacks = "".join(
            "".join(traceback.format_exception(*record.exc_info))
            for record in caplog.records
            if record.exc_info
        )
        for value in secrets:
            assert value not in str(raised.value)
            assert value not in caplog.text
            assert value not in rendered_tracebacks
            assert value not in json.dumps(diagnostics, default=str)
        detail = str(raised.value)
        assert "/reservations-v3/search" in detail
        assert "400" in detail
        assert "search settings" in detail
        assert "contact support" in detail

    @pytest.mark.parametrize("mode", ["scheduled", "direct", "recovery", "joined"])
    async def test_rejected_v3_search_has_no_followup(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture, mode: str
    ) -> None:
        """Each real v3 refresh stops after the controlled first-page rejection."""
        entry = await _setup_entry(hass, options={CONF_RESERVATIONS_API: "v3"})
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        client = coordinator.api_client
        actions = hass.data[DOMAIN][entry.entry_id]["actions_client"]
        actions.set_reservation_status = AsyncMock()
        secrets = ("sample-sensitive-pin-0072", *PII, FAKE_CLIENT_SECRET)
        secret = " ".join(secrets)
        previous = coordinator.data
        started = asyncio.Event()
        joined = asyncio.Event()
        release = asyncio.Event()
        service_detail: str | None = None
        original_recovery = coordinator.async_refresh_for_write_miss

        async def reject(*args: object, **kwargs: object) -> Response:
            """Pause the already-running request until the service joins it."""
            if mode == "joined":
                started.set()
                await release.wait()
            return Response(400, text=secret)

        async def observe_recovery(generation: int) -> bool:
            """Observe recovery joining the refresh before releasing it."""
            joined.set()
            return bool(await original_recovery(generation))

        with (
            caplog.at_level(logging.DEBUG),
            patch.object(
                client,
                "_request",
                new_callable=AsyncMock,
                side_effect=reject,
            ) as request,
            patch.object(coordinator, "async_refresh_for_write_miss", observe_recovery),
        ):

            async def write() -> None:
                """Request a missing reservation write."""
                await hass.services.async_call(
                    DOMAIN,
                    "set_reservation_status",
                    {"reservation_id": "res-new", "status": "checked_in"},
                    blocking=True,
                    return_response=True,
                )

            existing: asyncio.Task[None] | None = None
            action: asyncio.Task[None] | None = None
            try:
                if mode == "scheduled":
                    await coordinator._async_refresh(scheduled=True)
                elif mode == "direct":
                    await coordinator.async_refresh()
                else:
                    if mode == "joined":
                        existing = asyncio.create_task(coordinator.async_refresh())
                        await asyncio.wait_for(started.wait(), 2)
                        action = asyncio.create_task(write())
                        await asyncio.wait_for(joined.wait(), 2)
                        release.set()
                    with pytest.raises(HomeAssistantError) as raised:
                        if action is None:
                            await write()
                        else:
                            await asyncio.wait_for(action, 2)
                    service_detail = str(raised.value)
                diagnostics = await async_get_config_entry_diagnostics(hass, entry)
            finally:
                release.set()
                if existing is not None:
                    await asyncio.gather(existing, return_exceptions=True)
                if action is not None:
                    await asyncio.gather(action, return_exceptions=True)
        request.assert_awaited_once()
        assert request.await_args is not None
        assert request.await_args.args[:2] == ("GET", RESERVATIONS_V3_SEARCH_ENDPOINT)
        assert coordinator.data is previous
        assert coordinator.last_update_success is False
        actions.set_reservation_status.assert_not_awaited()
        if service_detail is not None:
            assert "/reservations-v3/search" in service_detail
            assert "HTTP 400" in service_detail
            assert "search settings" in service_detail
            assert "contact support" in service_detail
            for value in secrets:
                assert value not in service_detail
        detail = str(coordinator.last_exception)
        assert "/reservations-v3/search" in detail
        assert "HTTP 400" in detail
        assert "search settings" in detail
        assert "contact support" in detail
        rendered_tracebacks = "".join(
            "".join(traceback.format_exception(*record.exc_info))
            for record in caplog.records
            if record.exc_info
        )
        for value in secrets:
            assert value not in detail
            assert value not in caplog.text
            assert value not in rendered_tracebacks
            assert value not in json.dumps(diagnostics, default=str)

    @pytest.mark.parametrize("error", ["api", "filter", "unexpected"])
    async def test_joined_refresh_error_is_redacted(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture, error: str
    ) -> None:
        """A pre-existing refresh cannot expose an API response when joined."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        previous = coordinator.data
        client = hass.data[DOMAIN][entry.entry_id]["actions_client"]
        client.set_reservation_status = AsyncMock()
        started = asyncio.Event()
        joined = asyncio.Event()
        release = asyncio.Event()
        secrets = ("sample-sensitive-pin-0072", *PII, FAKE_CLIENT_SECRET)
        secret = " ".join(secrets)
        original_recovery = coordinator.async_refresh_for_write_miss

        async def fetch(*args: object, **kwargs: object) -> object:
            """Pause a regular refresh, then raise an API-derived error."""
            started.set()
            await release.wait()
            if error == "filter":
                raise GuestyFilterRejectedError(
                    "rejected",
                    endpoint="/reservations-v3/search",
                    status_code=400,
                    body_excerpt=secret,
                )
            if error == "api":
                raise GuestyApiError(secret)
            raise RuntimeError(secret)

        async def recovery(generation: int) -> bool:
            """Observe that the service joins the pre-existing fetch."""
            joined.set()
            return bool(await original_recovery(generation))

        with (
            caplog.at_level(logging.DEBUG),
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                side_effect=fetch,
            ) as mocked_fetch,
            patch.object(coordinator, "async_refresh_for_write_miss", recovery),
        ):
            existing = asyncio.create_task(coordinator.async_refresh())
            action: asyncio.Task[Any] | None = None
            try:
                await asyncio.wait_for(started.wait(), 2)
                action = asyncio.create_task(
                    hass.services.async_call(
                        DOMAIN,
                        "set_reservation_status",
                        {"reservation_id": "res-new", "status": "checked_in"},
                        blocking=True,
                        return_response=True,
                    )
                )
                await asyncio.wait_for(joined.wait(), 2)
                release.set()
                await asyncio.wait_for(existing, 2)
                with pytest.raises(HomeAssistantError) as raised:
                    await asyncio.wait_for(action, 2)
                diagnostics = await async_get_config_entry_diagnostics(hass, entry)
                rendered_tracebacks = "".join(
                    "".join(traceback.format_exception(*record.exc_info))
                    for record in caplog.records
                    if record.exc_info
                )
                for value in secrets:
                    assert value not in str(raised.value)
                    assert value not in caplog.text
                    assert value not in rendered_tracebacks
                    assert value not in json.dumps(diagnostics, default=str)
                mocked_fetch.assert_awaited_once()
                assert coordinator.data is previous
                assert coordinator.last_update_success is False
                client.set_reservation_status.assert_not_awaited()
                if error == "filter":
                    detail = str(raised.value)
                    assert "/reservations-v3/search" in detail
                    assert "400" in detail
                    assert "search settings" in detail
                    assert "contact support" in detail
                    assert "/reservations-v3/search" in caplog.text
                    assert "HTTP 400" in caplog.text
                    assert "search settings" in caplog.text
                    assert "contact support" in caplog.text
            finally:
                release.set()
                await asyncio.gather(existing, return_exceptions=True)
                if action is not None:
                    await asyncio.gather(action, return_exceptions=True)

    @pytest.mark.parametrize("fails", [False, True])
    async def test_recovery_logs_errors_and_diagnostics_are_redacted(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture, fails: bool
    ) -> None:
        """Successful and failed recovered writes omit sensitive response data."""
        entry = await _setup_entry(hass)
        client = hass.data[DOMAIN][entry.entry_id]["actions_client"]
        private = _private_reservation("res-private")
        base = make_identity_reservations("res-001", "res-bad", "res-e2e", "x")
        pin = "sample-pin-0072"
        secret_text = f"{pin} {CONFIRMATION_CODE} {GUEST_EMAIL} {FAKE_CLIENT_SECRET}"
        client.set_reservation_status = AsyncMock(
            side_effect=GuestyApiError(secret_text) if fails else None,
            return_value=ActionResult(success=True, target_id="res-private"),
        )
        with (
            caplog.at_level(logging.DEBUG),
            patch(
                "custom_components.guesty.GuestyApiClient.get_reservations",
                new_callable=AsyncMock,
                return_value=make_fetch_result([*base, private]),
            ) as fetch,
        ):
            if fails:
                with pytest.raises(HomeAssistantError) as error:
                    await hass.services.async_call(
                        DOMAIN,
                        "set_reservation_status",
                        {"reservation_id": "res-private", "status": "checked_in"},
                        blocking=True,
                        return_response=True,
                    )
                assert all(secret not in str(error.value) for secret in (*PII, pin))
            else:
                result = await hass.services.async_call(
                    DOMAIN,
                    "set_reservation_status",
                    {"reservation_id": "res-private", "status": "checked_in"},
                    blocking=True,
                    return_response=True,
                )
                assert result is not None and result["success"] is True
            diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        fetch.assert_awaited_once()
        client.set_reservation_status.assert_awaited_once()
        logs = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name != "pytest_homeassistant_custom_component.common"
        )
        evidence = logs + json.dumps(diagnostics)
        for secret in (*PII, pin, FAKE_CLIENT_SECRET):
            assert secret not in evidence

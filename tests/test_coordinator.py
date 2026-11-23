# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the ListingsCoordinator."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import cast
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    BASE_URL,
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
    RESERVATIONS_ENDPOINT,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    TOKEN_URL,
)
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyFilterRejectedError,
    GuestyRateLimitError,
    GuestyResponseError,
)
from custom_components.guesty.api.guest_cache import GuestLookupResult
from custom_components.guesty.api.models import (
    GuestyCustomFieldDefinition,
    GuestyGuest,
    GuestyListing,
    GuestyReservation,
    GuestyStaySegment,
)
from custom_components.guesty.const import (
    CONF_CF_SCAN_INTERVAL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FUTURE_DAYS,
    CONF_PAST_DAYS,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_RESERVATIONS_API,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    DEFAULT_CF_SCAN_INTERVAL,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.guesty.coordinator import (
    CustomFieldsDefinitionCoordinator,
    ListingsCoordinator,
    ReservationsCoordinator,
    _apply_same_date_handoffs,
    _reservation_sort_key,
    _within_window,
)
from tests.conftest import (
    FakeTokenStorage,
    make_fetch_result,
    make_listing_dict,
    make_token_response,
    make_v3_reservation,
    make_v3_search_page,
    make_v3_segment,
)


def _rel(days: int, hour: int, minute: int = 0) -> datetime:
    """Build a datetime offset from now, so fixtures stay in window.

    The coordinator trims reservations outside the configured
    instant window, so fixture dates must track the clock.

    Args:
        days: Day offset from today.
        hour: Hour of day, UTC.
        minute: Minute of hour, UTC.

    Returns:
        The offset datetime, in UTC.
    """
    return (datetime.now(UTC) + timedelta(days=days)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
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


async def _request_write_miss_refresh(
    coordinator: ReservationsCoordinator,
    generation: int,
) -> bool:
    """Request recovery, using the existing direct refresh on the baseline."""
    recovery = getattr(coordinator, "async_refresh_for_write_miss", None)
    if recovery is not None:
        return bool(await recovery(generation))
    await coordinator.async_refresh()
    return coordinator.last_update_success


@asynccontextmanager
async def _recovery_dispatch_lock(
    coordinator: ReservationsCoordinator,
) -> AsyncIterator[None]:
    """Use the recovery lock when implemented, or baseline unlocked dispatch."""
    factory = getattr(coordinator, "async_write_miss_dispatch_lock", None)
    if factory is None:
        yield
    else:
        async with factory():
            yield


class TestWriteMissHomeAssistantSemantics:
    """Control tests for unchanged HA refresh and debouncer behavior."""

    async def test_request_returns_early_and_direct_refresh_fetches_again(
        self, hass: HomeAssistant
    ) -> None:
        """A debounced request returns while a direct refresh waits and refetches."""
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch() -> int:
            """Pause the first fetch so both refresh paths overlap."""
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
            return calls

        coordinator = DataUpdateCoordinator(
            hass,
            logging.getLogger(__name__),
            config_entry=None,
            name="write_miss_semantics",
            update_method=fetch,
        )
        first = asyncio.create_task(coordinator.async_refresh())
        second: asyncio.Task[None] | None = None
        try:
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.wait_for(coordinator.async_request_refresh(), 2)
            assert calls == 1
            assert not first.done()
            second = asyncio.create_task(coordinator.async_refresh())
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), 2)
            assert calls == 2
            assert coordinator.data == 2
            assert coordinator.last_update_success is True
        finally:
            release.set()
            await coordinator.async_shutdown()

    async def test_failure_retains_data_and_shutdown_skips(
        self, hass: HomeAssistant
    ) -> None:
        """A swallowed failure retains data and a shutdown call is not a fetch."""
        fetch = AsyncMock(side_effect=[{"old": 1}, UpdateFailed("failed")])
        coordinator = DataUpdateCoordinator(
            hass,
            logging.getLogger(__name__),
            config_entry=None,
            name="write_miss_semantics",
            update_method=fetch,
        )
        await coordinator.async_refresh()
        assert coordinator.data == {"old": 1}
        assert coordinator.last_update_success is True
        await coordinator.async_refresh()
        assert coordinator.data == {"old": 1}
        assert coordinator.last_update_success is False
        await coordinator.async_shutdown()
        await coordinator.async_refresh()
        assert fetch.await_count == 2


class TestWriteMissAttemptLifecycle:
    """Specify recovery attempts against the real HA coordinator path."""

    async def test_dispatch_lock_blocks_an_intervening_refresh(
        self, hass: HomeAssistant
    ) -> None:
        """A coordinator refresh cannot begin until recovered dispatch completes."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())
        with patch.object(coordinator, "_async_update_data", return_value={}) as fetch:
            try:
                async with _recovery_dispatch_lock(coordinator):
                    pending = hass.async_create_task(
                        coordinator.async_refresh(),
                        "competing reservation refresh",
                        eager_start=True,
                    )
                    fetch.assert_not_awaited()
                    assert not pending.done()
                await pending
                fetch.assert_awaited_once()
            finally:
                await coordinator.async_shutdown()

    async def test_attempt_generation_starts_before_fetch(
        self, hass: HomeAssistant
    ) -> None:
        """A real attempt has its own generation before the fetch completes."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())
        initial = getattr(coordinator, "write_miss_attempt_generation", 0)
        observed: list[int] = []

        async def fetch() -> dict[str, list[GuestyReservation]]:
            """Record the attempt generation during the in-progress fetch."""
            observed.append(getattr(coordinator, "write_miss_attempt_generation", 0))
            return {}

        with patch.object(coordinator, "_async_update_data", side_effect=fetch):
            try:
                await coordinator.async_refresh()
                assert observed == [initial + 1]
                assert coordinator.write_miss_attempt_generation == observed[0]
            finally:
                await coordinator.async_shutdown()

    @pytest.mark.parametrize("fails", [False, True])
    async def test_inflight_attempt_is_consumed_once(
        self, hass: HomeAssistant, fails: bool
    ) -> None:
        """Joining an in-flight attempt never starts another, even on failure."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())
        started = asyncio.Event()
        waiter_started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch() -> dict[str, list[GuestyReservation]]:
            """Pause the first fetch; an unwanted second fetch would succeed."""
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                if fails:
                    raise UpdateFailed("failed")
            return {}

        async def recover(generation: int) -> bool:
            """Start the action's refresh while the scheduled fetch is active."""
            waiter_started.set()
            return await _request_write_miss_refresh(coordinator, generation)

        with patch.object(coordinator, "_async_update_data", side_effect=fetch):
            first = asyncio.create_task(coordinator.async_refresh())
            waiter: asyncio.Task[bool] | None = None
            try:
                await asyncio.wait_for(started.wait(), 2)
                generation = getattr(coordinator, "write_miss_generation", 0)
                waiter = asyncio.create_task(recover(generation))
                await asyncio.wait_for(waiter_started.wait(), 2)
                assert not waiter.done()
                release.set()
                result = await asyncio.wait_for(waiter, 2)
                await asyncio.wait_for(first, 2)
                assert result is not fails
                assert calls == 1
            finally:
                release.set()
                await coordinator.async_shutdown()

    async def test_cancelled_waiter_does_not_cancel_shared_attempt(
        self, hass: HomeAssistant
    ) -> None:
        """One cancelled action must not cancel or duplicate another's refresh."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())
        started = asyncio.Event()
        second_started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch() -> dict[str, list[GuestyReservation]]:
            """Pause the shared coordinator work."""
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {}

        async def recover_second(generation: int) -> bool:
            """Register a second waiter before cancelling the first."""
            second_started.set()
            return await _request_write_miss_refresh(coordinator, generation)

        with patch.object(coordinator, "_async_update_data", side_effect=fetch):
            generation = getattr(coordinator, "write_miss_generation", 0)
            first = asyncio.create_task(
                _request_write_miss_refresh(coordinator, generation)
            )
            second: asyncio.Task[bool] | None = None
            try:
                await asyncio.wait_for(started.wait(), 2)
                second = asyncio.create_task(recover_second(generation))
                await asyncio.wait_for(second_started.wait(), 2)
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                release.set()
                assert await asyncio.wait_for(second, 2) is True
                assert calls == 1
            finally:
                release.set()
                await coordinator.async_shutdown()

    async def test_later_success_does_not_replace_joined_failure(
        self, hass: HomeAssistant
    ) -> None:
        """A waiter consumes the first post-detection outcome, not a later success."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())
        started = asyncio.Event()
        later_started = asyncio.Event()
        waiter_started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch() -> dict[str, list[GuestyReservation]]:
            """Fail the first update; a separately requested later update succeeds."""
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                raise UpdateFailed("failed")
            return {}

        async def later_refresh() -> None:
            """Queue a later update before the action requests recovery."""
            later_started.set()
            await coordinator.async_refresh()

        async def recover(generation: int) -> bool:
            """Queue the action behind the existing updates."""
            waiter_started.set()
            return await _request_write_miss_refresh(coordinator, generation)

        with patch.object(coordinator, "_async_update_data", side_effect=fetch):
            first = asyncio.create_task(coordinator.async_refresh())
            later: asyncio.Task[None] | None = None
            waiter: asyncio.Task[bool] | None = None
            try:
                await asyncio.wait_for(started.wait(), 2)
                generation = getattr(coordinator, "write_miss_generation", 0)
                later = asyncio.create_task(later_refresh())
                await asyncio.wait_for(later_started.wait(), 2)
                waiter = asyncio.create_task(recover(generation))
                await asyncio.wait_for(waiter_started.wait(), 2)
                release.set()
                assert await asyncio.wait_for(waiter, 2) is False
                await asyncio.wait_for(asyncio.gather(first, later), 2)
                assert calls == 2
            finally:
                release.set()
                await coordinator.async_shutdown()

    async def test_cancelled_joined_attempt_cannot_authorize(
        self, hass: HomeAssistant
    ) -> None:
        """A cancelled in-flight refresh consumes the attempt without a write."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())
        started = asyncio.Event()
        waiter_started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch() -> dict[str, list[GuestyReservation]]:
            """Pause the first update until its owning task is cancelled."""
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {}

        async def recover(generation: int) -> bool:
            """Join the in-flight update before it is cancelled."""
            waiter_started.set()
            return await _request_write_miss_refresh(coordinator, generation)

        with patch.object(coordinator, "_async_update_data", side_effect=fetch):
            first = asyncio.create_task(coordinator.async_refresh())
            waiter: asyncio.Task[bool] | None = None
            try:
                await asyncio.wait_for(started.wait(), 2)
                generation = getattr(coordinator, "write_miss_generation", 0)
                waiter = asyncio.create_task(recover(generation))
                await asyncio.wait_for(waiter_started.wait(), 2)
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                release.set()
                assert await asyncio.wait_for(waiter, 2) is False
                assert calls == 1
            finally:
                release.set()
                await coordinator.async_shutdown()

    async def test_later_failure_invalidates_joined_success(
        self, hass: HomeAssistant
    ) -> None:
        """A newer failed refresh cannot leave an older success authorized."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())
        started = asyncio.Event()
        later_started = asyncio.Event()
        waiter_started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch() -> dict[str, list[GuestyReservation]]:
            """Complete the joined refresh, then fail an already queued refresh."""
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                return {}
            raise UpdateFailed("failed")

        async def later_refresh() -> None:
            """Queue a later update before the action requests recovery."""
            later_started.set()
            await coordinator.async_refresh()

        async def recover(generation: int) -> bool:
            """Queue the action behind the existing updates."""
            waiter_started.set()
            return await _request_write_miss_refresh(coordinator, generation)

        with patch.object(coordinator, "_async_update_data", side_effect=fetch):
            first = asyncio.create_task(coordinator.async_refresh())
            try:
                await asyncio.wait_for(started.wait(), 2)
                generation = getattr(coordinator, "write_miss_generation", 0)
                later = asyncio.create_task(later_refresh())
                await asyncio.wait_for(later_started.wait(), 2)
                waiter = asyncio.create_task(recover(generation))
                await asyncio.wait_for(waiter_started.wait(), 2)
                release.set()
                assert await asyncio.wait_for(waiter, 2) is False
                await asyncio.wait_for(asyncio.gather(first, later), 2)
                assert calls == 2
                assert coordinator.last_update_success is False
            finally:
                release.set()
                await coordinator.async_shutdown()

    async def test_skipped_update_cannot_authorize_retained_data(
        self, hass: HomeAssistant
    ) -> None:
        """A shut-down coordinator cannot treat its old success as a new fetch."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())
        coordinator.data = {"old": []}
        await coordinator.async_shutdown()
        generation = getattr(coordinator, "write_miss_generation", 0)
        with patch.object(coordinator, "_async_update_data") as update:
            assert await _request_write_miss_refresh(coordinator, generation) is False
            update.assert_not_awaited()
        assert coordinator.data == {"old": []}

    async def test_skipped_refresh_preserves_ha_cleanup(
        self, hass: HomeAssistant
    ) -> None:
        """A skipped refresh still lets HA perform its normal timer cleanup."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())
        await coordinator.async_shutdown()
        timer = hass.loop.call_later(3600, lambda: None)
        coordinator._unsub_refresh = timer.cancel
        try:
            await coordinator.async_refresh()
            assert timer.cancelled()
        finally:
            timer.cancel()

    async def test_token_data_update_does_not_invalidate_options(
        self, hass: HomeAssistant
    ) -> None:
        """A token-only data update is not an options or source change."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())

        async def fetch() -> dict[str, list[GuestyReservation]]:
            """Update token storage while a normal refresh is in progress."""
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, "cached_token": {"access_token": "test"}}
            )
            return {}

        with patch.object(coordinator, "_async_update_data", side_effect=fetch):
            try:
                generation = getattr(coordinator, "write_miss_generation", 0)
                assert (
                    await _request_write_miss_refresh(coordinator, generation) is True
                )
            finally:
                await coordinator.async_shutdown()

    @pytest.mark.parametrize("error_type", [GuestyResponseError, RuntimeError])
    async def test_recovery_failure_does_not_log_error_contents(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        error_type: type[Exception],
    ) -> None:
        """A recovery fetch never logs sensitive API or unexpected error text."""
        secret = "sample-confirmation-009472"
        entry = _make_entry()
        entry.add_to_hass(hass)
        client = AsyncMock()
        client.get_reservations.side_effect = error_type(secret)
        coordinator = ReservationsCoordinator(hass, entry, client, AsyncMock())
        generation = getattr(coordinator, "write_miss_generation", 0)
        try:
            with caplog.at_level(logging.DEBUG):
                assert (
                    await _request_write_miss_refresh(coordinator, generation) is False
                )
            assert secret not in caplog.text
        finally:
            await coordinator.async_shutdown()

    async def test_mutated_selection_snapshot_refuses(
        self, hass: HomeAssistant
    ) -> None:
        """A changed selection is detected despite an unchanged options object."""
        selection = ["listing-1"]
        entry = _make_entry(options={CONF_SELECTED_LISTINGS: selection})
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())

        async def fetch() -> dict[str, list[GuestyReservation]]:
            """Mutate a nested selection during the request."""
            selection.append("listing-2")
            return {}

        generation = getattr(coordinator, "write_miss_generation", 0)
        with patch.object(coordinator, "_async_update_data", side_effect=fetch):
            try:
                assert (
                    await _request_write_miss_refresh(coordinator, generation) is False
                )
            finally:
                await coordinator.async_shutdown()

    async def test_client_replacement_invalidates_refresh(
        self, hass: HomeAssistant
    ) -> None:
        """A fetch started by a replaced client cannot authorize recovery."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())

        async def fetch() -> dict[str, list[GuestyReservation]]:
            """Replace the coordinator client during its refresh."""
            coordinator.api_client = AsyncMock()
            return {}

        generation = getattr(coordinator, "write_miss_generation", 0)
        with patch.object(coordinator, "_async_update_data", side_effect=fetch):
            try:
                assert (
                    await _request_write_miss_refresh(coordinator, generation) is False
                )
            finally:
                await coordinator.async_shutdown()

    async def test_evicted_attempt_outcome_fails_closed(
        self, hass: HomeAssistant
    ) -> None:
        """A missing first completion cannot be replaced by a later success."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())
        generation = getattr(coordinator, "write_miss_generation", 0)
        with patch.object(coordinator, "_async_update_data", return_value={}) as update:
            try:
                for _ in range(257):
                    await coordinator.async_refresh()
                assert (
                    await _request_write_miss_refresh(coordinator, generation) is False
                )
                assert update.await_count == 257
            finally:
                await coordinator.async_shutdown()

    @pytest.mark.parametrize(
        ("key", "changed"),
        [
            (CONF_RESERVATIONS_API, RESERVATIONS_API_LEGACY),
            (CONF_PAST_DAYS, 77),
            (CONF_FUTURE_DAYS, 88),
            (CONF_SELECTED_LISTINGS, ["another-listing"]),
        ],
    )
    async def test_inflight_option_change_and_revert_refuses(
        self, hass: HomeAssistant, key: str, changed: object
    ) -> None:
        """A changed-and-reverted setting cannot authorize an in-flight attempt."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        coordinator = ReservationsCoordinator(hass, entry, AsyncMock(), AsyncMock())
        started = asyncio.Event()
        waiter_started = asyncio.Event()
        release = asyncio.Event()

        async def fetch() -> dict[str, list[GuestyReservation]]:
            """Hold the update across both synchronous HA option changes."""
            started.set()
            await release.wait()
            return {}

        async def recover(generation: int) -> bool:
            """Join the refresh started before the options changed."""
            waiter_started.set()
            return await _request_write_miss_refresh(coordinator, generation)

        with patch.object(coordinator, "_async_update_data", side_effect=fetch):
            first = asyncio.create_task(coordinator.async_refresh())
            waiter: asyncio.Task[bool] | None = None
            try:
                await asyncio.wait_for(started.wait(), 2)
                generation = getattr(coordinator, "write_miss_generation", 0)
                waiter = asyncio.create_task(recover(generation))
                await asyncio.wait_for(waiter_started.wait(), 2)
                options = dict(entry.options)
                hass.config_entries.async_update_entry(
                    entry, options={**options, key: changed}
                )
                hass.config_entries.async_update_entry(entry, options=options)
                release.set()
                assert await asyncio.wait_for(waiter, 2) is False
                await asyncio.wait_for(first, 2)
            finally:
                release.set()
                await coordinator.async_shutdown()


class TestListingsCoordinator:
    """Tests for ListingsCoordinator._async_update_data."""

    async def test_update_data_returns_dict_keyed_by_id(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """_async_update_data returns dict keyed by listing.id."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=[sample_listing])

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()
        assert isinstance(data, dict)
        assert sample_listing.id in data
        assert data[sample_listing.id] is sample_listing
        api_client.get_listings.assert_awaited_once()

    async def test_update_interval_from_options(
        self,
        hass: HomeAssistant,
    ) -> None:
        """update_interval matches scan_interval from entry.options."""
        entry = _make_entry(options={CONF_SCAN_INTERVAL: 10})
        entry.add_to_hass(hass)
        api_client = AsyncMock()

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        assert coordinator.update_interval == timedelta(minutes=10)

    async def test_update_interval_default(
        self,
        hass: HomeAssistant,
    ) -> None:
        """update_interval uses DEFAULT_SCAN_INTERVAL when not set."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        assert coordinator.update_interval == timedelta(
            minutes=DEFAULT_SCAN_INTERVAL,
        )

    async def test_update_raises_update_failed_on_connection_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on GuestyConnectionError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_update_raises_update_failed_on_auth_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on GuestyAuthError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            side_effect=GuestyAuthError("auth failed"),
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_update_raises_update_failed_on_response_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on GuestyResponseError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            side_effect=GuestyResponseError("malformed"),
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_empty_listing_list_returns_empty_dict(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data returns empty dict for no listings."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=[])

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()
        assert data == {}


class TestListingsCoordinatorFiltering:
    """Tests for ListingsCoordinator selected-listings filtering."""

    @staticmethod
    def _models_from_dicts(
        dicts: list[dict[str, object]],
    ) -> list[GuestyListing]:
        """Convert multi-listing API dicts to GuestyListing models.

        Args:
            dicts: Raw API listing dictionaries.

        Returns:
            List of parsed GuestyListing instances.
        """
        models: list[GuestyListing] = []
        for d in dicts:
            model = GuestyListing.from_api_dict(d)
            assert model is not None
            models.append(model)
        return models

    async def test_returns_only_selected_listings(
        self,
        hass: HomeAssistant,
        multi_listing_dicts: list[dict[str, object]],
    ) -> None:
        """Coordinator returns only selected listings (T005).

        When CONF_SELECTED_LISTINGS is set in options, only listings
        whose IDs appear in that list are included in coordinator
        data.
        """
        models = self._models_from_dicts(multi_listing_dicts)
        selected = ["lst_miami_beach", "lst_tampa_bay"]

        entry = _make_entry(
            options={CONF_SELECTED_LISTINGS: selected},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=models)

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()

        assert set(data.keys()) == {"lst_miami_beach", "lst_tampa_bay"}
        assert len(data) == 2

    async def test_retains_unselected_exact_room_for_timing_only(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The fetched lookup includes unselected rooms without selecting them."""
        selected = GuestyListing.from_api_dict(
            make_listing_dict(_id="selected-room", timezone="UTC"),
        )
        child = GuestyListing.from_api_dict(
            make_listing_dict(
                _id="unselected-child",
                timezone="America/New_York",
                defaultCheckInTime="16:00",
                defaultCheckOutTime="11:00",
            ),
        )
        assert selected is not None
        assert child is not None
        entry = _make_entry(options={CONF_SELECTED_LISTINGS: [selected.id]})
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=[selected, child])
        coordinator = ListingsCoordinator(hass, entry, api_client)

        data = await coordinator._async_update_data()

        assert set(data) == {selected.id}
        assert set(coordinator.timing_listings) == {
            selected.id,
            child.id,
        }
        assert coordinator.timing_listings[child.id] is child
        api_client.get_listings.assert_awaited_once()

    async def test_returns_all_when_selected_absent(
        self,
        hass: HomeAssistant,
        multi_listing_dicts: list[dict[str, object]],
    ) -> None:
        """Coordinator returns all listings when option absent (T006).

        When CONF_SELECTED_LISTINGS is not present in entry options
        the coordinator must return every listing from the API
        (backward-compatible default).
        """
        models = self._models_from_dicts(multi_listing_dicts)

        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=models)

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()

        expected_ids = {m.id for m in models}
        assert set(data.keys()) == expected_ids
        assert len(data) == 5

    async def test_ignores_nonexistent_selected_ids(
        self,
        hass: HomeAssistant,
        multi_listing_dicts: list[dict[str, object]],
    ) -> None:
        """Coordinator silently ignores unknown selected IDs (T007).

        IDs in CONF_SELECTED_LISTINGS that do not appear in the API
        response are silently dropped — no error is raised.
        """
        models = self._models_from_dicts(multi_listing_dicts)
        selected = ["lst_miami_beach", "nonexistent_id"]

        entry = _make_entry(
            options={CONF_SELECTED_LISTINGS: selected},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=models)

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()

        assert set(data.keys()) == {"lst_miami_beach"}
        assert len(data) == 1

    async def test_empty_result_all_selected_missing(
        self,
        hass: HomeAssistant,
        multi_listing_dicts: list[dict[str, object]],
    ) -> None:
        """Coordinator returns empty dict when all IDs missing (T008).

        When every ID in CONF_SELECTED_LISTINGS is absent from the
        API response, coordinator data is an empty dict with no error.
        """
        models = self._models_from_dicts(multi_listing_dicts)
        selected = ["nonexistent1", "nonexistent2"]

        entry = _make_entry(
            options={CONF_SELECTED_LISTINGS: selected},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=models)

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()

        assert data == {}

    async def test_filter_does_not_trigger_disappeared(
        self,
        hass: HomeAssistant,
        multi_listing_dicts: list[dict[str, object]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Filtering does not mark unselected listings disappeared.

        Listings still present in the API but excluded by the
        selected-listings filter must not appear in
        ``disappeared_listing_ids`` or produce warnings.
        """
        models = self._models_from_dicts(multi_listing_dicts)
        selected = ["lst_miami_beach", "lst_tampa_bay"]

        entry = _make_entry(
            options={CONF_SELECTED_LISTINGS: selected},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=models)

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First call sets _previous_listing_ids
        await coordinator._async_update_data()

        # Second call should not mark unselected as disappeared
        with caplog.at_level(logging.WARNING):
            data = await coordinator._async_update_data()

        assert set(data.keys()) == set(selected)
        assert coordinator.disappeared_listing_ids == set()
        assert not [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "disappeared" in r.getMessage().lower()
        ]


class TestDisappearedListings:
    """Tests for disappeared listing tracking (T028)."""

    async def test_disappeared_listing_ids_initially_empty(
        self,
        hass: HomeAssistant,
    ) -> None:
        """disappeared_listing_ids is empty on init."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        assert coordinator.disappeared_listing_ids == set()

    async def test_disappeared_listing_detected(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Listing absent from current fetch is marked disappeared."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First fetch — establishes baseline
        await coordinator._async_update_data()

        # Second fetch — listing disappears
        api_client.get_listings = AsyncMock(return_value=[])
        await coordinator._async_update_data()

        assert sample_listing.id in coordinator.disappeared_listing_ids

    async def test_disappeared_listing_cleared_on_reappear(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Disappeared listing ID cleared when listing reappears."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First fetch
        await coordinator._async_update_data()

        # Listing disappears
        api_client.get_listings = AsyncMock(return_value=[])
        await coordinator._async_update_data()
        assert sample_listing.id in coordinator.disappeared_listing_ids

        # Listing reappears
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )
        await coordinator._async_update_data()
        assert sample_listing.id not in coordinator.disappeared_listing_ids

    async def test_disappeared_listing_warning_logged(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Warning logged for each disappeared listing ID."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First fetch
        await coordinator._async_update_data()

        # Listing disappears
        api_client.get_listings = AsyncMock(return_value=[])
        caplog.clear()
        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.guesty.coordinator",
        ):
            await coordinator._async_update_data()

        matching = [
            r
            for r in caplog.records
            if r.name == "custom_components.guesty.coordinator"
            and r.levelno == logging.WARNING
            and sample_listing.id in r.getMessage()
        ]
        assert len(matching) == 1

    async def test_last_known_good_data_retained_on_error(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Coordinator retains last-known-good data after error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # Successful first refresh through the public coordinator flow
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data == {sample_listing.id: sample_listing}

        # Now API fails during a coordinator refresh
        api_client.get_listings = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )
        await coordinator.async_refresh()

        # DataUpdateCoordinator preserves last-known-good data
        assert coordinator.last_update_success is False
        assert coordinator.data == {sample_listing.id: sample_listing}

    async def test_recovery_updates_data_after_error(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Data updated on recovery after API error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First successful refresh
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True

        # API error during refresh
        api_client.get_listings = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

        # Recovery with new listing
        new_listing = GuestyListing(
            id="listing-002",
            title="Mountain Cabin",
            nickname="cabin",
            status="active",
            address=None,
            property_type="house",
            room_type="entire_home",
            listing_type="SINGLE",
            bedrooms=3,
            bathrooms=2.0,
            accommodates=6,
            timezone="America/Denver",
            check_in_time="16:00",
            check_out_time="10:00",
            tags=(),
            custom_fields=MappingProxyType({}),
        )
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing, new_listing],
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is not None
        assert new_listing.id in coordinator.data
        assert sample_listing.id in coordinator.data

    async def test_disappeared_not_set_on_first_fetch(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """No disappeared IDs on first successful fetch."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        await coordinator._async_update_data()
        assert coordinator.disappeared_listing_ids == set()

    async def test_disappeared_ids_preserved_on_api_error(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """disappeared_listing_ids unchanged after UpdateFailed."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First fetch
        await coordinator._async_update_data()

        # Listing disappears
        api_client.get_listings = AsyncMock(return_value=[])
        await coordinator._async_update_data()
        assert sample_listing.id in coordinator.disappeared_listing_ids

        # API error — disappeared set should be unchanged
        api_client.get_listings = AsyncMock(
            side_effect=GuestyResponseError("bad response"),
        )
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

        assert sample_listing.id in coordinator.disappeared_listing_ids


class TestReservationsCoordinator:
    """Tests for ReservationsCoordinator._async_update_data."""

    async def test_update_data_returns_dict_keyed_by_listing_id(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        sample_reservation: GuestyReservation,
    ) -> None:
        """_async_update_data returns dict keyed by listing_id."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([sample_reservation]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()
        assert isinstance(data, dict)
        assert sample_listing.id in data
        assert data[sample_listing.id] == [sample_reservation]
        api_client.get_reservations.assert_awaited_once()

    async def test_groups_reservations_by_listing_id(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Reservations are grouped by listing_id."""

        entry = _make_entry()
        entry.add_to_hass(hass)

        res_a = GuestyReservation(
            id="res-a",
            listing_id="listing-001",
            status="confirmed",
            check_in=_rel(0, 15),
            check_out=_rel(4, 11),
        )
        listing_b = GuestyListing(
            id="listing-002",
            title="Mountain Cabin",
            nickname="cabin",
            status="active",
            address=None,
            property_type="house",
            room_type="entire_home",
            listing_type="SINGLE",
            bedrooms=3,
            bathrooms=2.0,
            accommodates=6,
            timezone="America/Denver",
            check_in_time="16:00",
            check_out_time="10:00",
            tags=(),
            custom_fields=MappingProxyType({}),
        )
        res_b = GuestyReservation(
            id="res-b",
            listing_id="listing-002",
            status="checked_in",
            check_in=_rel(1, 15),
            check_out=_rel(5, 11),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([res_a, res_b]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {
            sample_listing.id: sample_listing,
            listing_b.id: listing_b,
        }

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()
        assert "listing-001" in data
        assert "listing-002" in data
        assert len(data["listing-001"]) == 1
        assert len(data["listing-002"]) == 1

    async def test_filters_unknown_listing_ids(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Reservations for unknown listings are filtered out."""

        entry = _make_entry()
        entry.add_to_hass(hass)

        known_res = GuestyReservation(
            id="res-known",
            listing_id="listing-001",
            status="confirmed",
            check_in=_rel(0, 15),
            check_out=_rel(4, 11),
        )
        unknown_res = GuestyReservation(
            id="res-unknown",
            listing_id="unknown-listing",
            status="confirmed",
            check_in=_rel(0, 15),
            check_out=_rel(4, 11),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([known_res, unknown_res]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.guesty.coordinator",
        ):
            data = await coordinator._async_update_data()

        assert "listing-001" in data
        assert "unknown-listing" not in data
        matching = [
            r
            for r in caplog.records
            if r.name == "custom_components.guesty.coordinator"
            and r.levelno == logging.WARNING
            and "unknown-listing" in r.getMessage()
        ]
        assert len(matching) == 1

    async def test_single_segment_fallback_unknown_listing_is_not_inferred(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Fallback dates do not attribute an unknown segment to a known listing.

        Args:
            hass: The Home Assistant test instance.
            sample_listing: The only tracked listing.
            caplog: Log capture fixture.
        """
        check_in = _rel(1, 15)
        check_out = _rel(4, 11)
        parsed = GuestyReservation.from_v3_api_dict(
            make_v3_reservation(
                "res-fallback-unknown",
                segments=[
                    make_v3_segment(
                        listingId="unknown-listing",
                        checkIn=None,
                        checkOut=None,
                    ),
                ],
                checkIn=check_in.isoformat(),
                checkOut=check_out.isoformat(),
                listingId=sample_listing.id,
            ),
        )
        assert parsed is not None
        assert parsed.listing_id == "unknown-listing"

        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([parsed]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}
        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.guesty.coordinator",
        ):
            result = await coordinator._async_update_data()

        assert result == {}
        assert "unknown-listing" in caplog.text

    async def test_v3_resolves_from_exact_listing_local_schedule(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """V3 endpoints use exact-room localized settings, not raw suffixes."""
        listing = replace(
            sample_listing,
            timezone="America/New_York",
            timezone_explicit=True,
            check_in_time="16:00",
            check_out_time="11:00",
        )
        local_check_in = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        local_check_out = (datetime.now(UTC).date() + timedelta(days=4)).isoformat()
        parsed = GuestyReservation.from_v3_api_dict(
            make_v3_reservation(
                guest_id=None,
                segments=[
                    make_v3_segment(
                        checkIn="2026-01-01T00:00:00Z",
                        checkOut="2026-01-02T00:00:00Z",
                        checkInDateLocalized=local_check_in,
                        checkOutDateLocalized=local_check_out,
                    ),
                ],
            ),
        )
        assert parsed is not None
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([parsed]),
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

        result = await coordinator._async_update_data()

        reservation = result[listing.id][0]
        zone = ZoneInfo("America/New_York")
        expected_check_in = (
            datetime.fromisoformat(
                f"{local_check_in}T16:00:00",
            )
            .replace(tzinfo=zone)
            .astimezone(UTC)
        )
        expected_check_out = (
            datetime.fromisoformat(
                f"{local_check_out}T11:00:00",
            )
            .replace(tzinfo=zone)
            .astimezone(UTC)
        )
        assert reservation.check_in == expected_check_in
        assert reservation.check_out == expected_check_out
        api_client.get_reservations.assert_awaited_once()

    async def test_moved_stay_uses_each_rooms_distinct_schedule(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A different-zone handoff keeps the destination room's defaults."""
        origin = GuestyListing.from_api_dict(
            make_listing_dict(
                _id="room-origin",
                timezone="UTC",
                defaultCheckInTime="16:00",
                defaultCheckOutTime="11:00",
            ),
        )
        destination = GuestyListing.from_api_dict(
            make_listing_dict(
                _id="room-destination",
                timezone="America/Los_Angeles",
                defaultCheckInTime="15:00",
                defaultCheckOutTime="10:00",
            ),
        )
        assert origin is not None
        assert destination is not None
        today = datetime.now(UTC).date()
        origin_date = (today + timedelta(days=1)).isoformat()
        handoff_date = (today + timedelta(days=2)).isoformat()
        final_date = (today + timedelta(days=4)).isoformat()
        reservation = GuestyReservation.from_v3_api_dict(
            make_v3_reservation(
                "res-two-room-zones",
                guest_id=None,
                segments=[
                    make_v3_segment(
                        listingId=origin.id,
                        checkInDateLocalized=origin_date,
                        checkOutDateLocalized=handoff_date,
                    ),
                    make_v3_segment(
                        listingId=destination.id,
                        checkInDateLocalized=handoff_date,
                        checkOutDateLocalized=final_date,
                    ),
                ],
            ),
        )
        assert reservation is not None
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([reservation]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {
            origin.id: origin,
            destination.id: destination,
        }
        listings_coordinator.timing_listings = dict(
            listings_coordinator.data,
        )
        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()

        origin_projection = data[origin.id][0]
        destination_projection = data[destination.id][0]
        expected_origin_checkout = datetime.fromisoformat(
            f"{handoff_date}T11:00:00+00:00",
        )
        expected_destination_checkin = (
            datetime.fromisoformat(
                f"{handoff_date}T15:00:00",
            )
            .replace(
                tzinfo=ZoneInfo("America/Los_Angeles"),
            )
            .astimezone(UTC)
        )
        assert origin_projection.check_out == expected_origin_checkout
        assert destination_projection.check_in == expected_destination_checkin
        assert destination_projection.check_in != expected_origin_checkout

    async def test_missing_child_schedule_never_uses_parent_defaults(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Parent attribution does not authorize parent schedule substitution."""
        parent = GuestyListing.from_api_dict(
            make_listing_dict(
                _id="parent-room",
                timezone="UTC",
                defaultCheckInTime="16:00",
                defaultCheckOutTime="11:00",
            ),
        )
        assert parent is not None
        check_in_date = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        check_out_date = (datetime.now(UTC).date() + timedelta(days=3)).isoformat()
        reservation = GuestyReservation.from_v3_api_dict(
            make_v3_reservation(
                "res-child-without-settings",
                guest_id=None,
                segments=[
                    make_v3_segment(
                        listingId="child-room",
                        parentListingId=parent.id,
                        checkInDateLocalized=check_in_date,
                        checkOutDateLocalized=check_out_date,
                    ),
                ],
            ),
        )
        assert reservation is not None
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([reservation]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {parent.id: parent}
        listings_coordinator.timing_listings = {parent.id: parent}
        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()

        projection = data[parent.id][0]
        assert projection.listing_id == parent.id
        assert projection.check_in is None
        assert projection.check_out is None
        assert projection.check_in_local == check_in_date
        assert projection.check_out_local == check_out_date
        assert projection.status == "unknown"

    async def test_unknown_v3_keeps_local_dates_and_source_status(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Missing timezone provenance retains the bounded result as unknown."""
        check_in_date = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        check_out_date = (datetime.now(UTC).date() + timedelta(days=4)).isoformat()
        parsed = GuestyReservation.from_v3_api_dict(
            make_v3_reservation(
                guest_id=None,
                segments=[
                    make_v3_segment(
                        checkInDateLocalized=check_in_date,
                        checkOutDateLocalized=check_out_date,
                    ),
                ],
            ),
        )
        assert parsed is not None
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([parsed]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}
        listings_coordinator.timing_listings = {
            sample_listing.id: sample_listing,
        }
        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        result = await coordinator._async_update_data()

        projected = result[sample_listing.id][0]
        assert projected.check_in is None
        assert projected.check_out is None
        assert projected.check_in_local == check_in_date
        assert projected.check_out_local == check_out_date
        assert projected.status == "unknown"
        assert projected.reservation_status == "confirmed"

    async def test_timing_certainty_survives_refresh_and_reload(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Known→partial-unknown→known never reuses stale instants."""
        listing = GuestyListing.from_api_dict(
            make_listing_dict(
                _id="listing-001",
                timezone="UTC",
                defaultCheckInTime="16:00",
                defaultCheckOutTime="11:00",
            ),
        )
        assert listing is not None
        today = datetime.now(UTC).date()
        check_in_date = (today + timedelta(days=1)).isoformat()
        check_out_date = (today + timedelta(days=4)).isoformat()

        def reservation(*, checkout_date: str | None) -> GuestyReservation:
            """Build one source observation with current date evidence."""
            segment = GuestyStaySegment(
                listing_id=listing.id,
                check_in=None,
                check_out=None,
                check_in_local=check_in_date,
                check_out_local=checkout_date,
                raw_position=0,
            )
            return GuestyReservation(
                id="res-timing-reload",
                listing_id=listing.id,
                status="confirmed",
                check_in=None,
                check_out=None,
                confirmation_code="CONF-TIMING",
                check_in_local=check_in_date,
                check_out_local=checkout_date,
                stay=(segment,),
                is_v3=True,
                raw_stay_count=1,
            )

        entry = _make_entry(
            entry_id="entry-timing-reload",
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            side_effect=[
                make_fetch_result([reservation(checkout_date=check_out_date)]),
                make_fetch_result([reservation(checkout_date=None)]),
                make_fetch_result([reservation(checkout_date=check_out_date)]),
            ],
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
        await coordinator.async_refresh()
        first = coordinator.data[listing.id][0]
        assert first.check_in is not None
        assert first.check_out is not None
        assert first.status == "confirmed"
        assert coordinator.identity.baseline is not None
        assert (
            coordinator.identity.baseline.candidates["res-timing-reload"].timing_status
            == "known"
        )
        initial_write_refusal = coordinator.identity.write_refusal(
            "res-timing-reload",
        )

        await coordinator.async_refresh()
        partial = coordinator.data[listing.id][0]
        assert partial.check_in is not None
        assert partial.check_out is None
        assert partial.check_in_local == check_in_date
        assert partial.check_out_local is None
        assert partial.status == "unknown"
        assert partial.reservation_status == "confirmed"
        assert coordinator.identity.baseline is not None
        assert (
            coordinator.identity.baseline.candidates["res-timing-reload"].timing_status
            == "unknown"
        )
        assert coordinator.identity.baseline.codes == {
            "CONF-TIMING": "res-timing-reload",
        }
        assert (
            coordinator.identity.write_refusal("res-timing-reload")
            == initial_write_refusal
        )

        reloaded = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )
        await reloaded.async_refresh()
        restored = reloaded.data[listing.id][0]
        assert restored.check_in is not None
        assert restored.check_out is not None
        assert restored.status == "confirmed"
        assert reloaded.identity.baseline is not None
        assert (
            reloaded.identity.baseline.candidates["res-timing-reload"].timing_status
            == "known"
        )
        assert reloaded.identity.baseline.codes == {
            "CONF-TIMING": "res-timing-reload",
        }
        assert (
            reloaded.identity.write_refusal("res-timing-reload")
            == initial_write_refusal
        )
        assert api_client.get_reservations.await_count == 3
        assert api_client.get_reservations.await_args_list[2].kwargs["candidates"] == {}

    async def test_adjacent_same_zone_handoff_uses_prior_exact_checkout(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Only an adjacent same-local-date same-zone move shares checkout."""
        zone = ZoneInfo("America/New_York")
        local_today = datetime.now(zone).date()
        first_date = (local_today + timedelta(days=1)).isoformat()
        handoff_date = (local_today + timedelta(days=2)).isoformat()
        final_date = (local_today + timedelta(days=4)).isoformat()
        rooms = {
            listing_id: GuestyListing.from_api_dict(
                make_listing_dict(
                    _id=listing_id,
                    timezone="America/New_York",
                    defaultCheckInTime="16:00",
                    defaultCheckOutTime="11:00",
                ),
            )
            for listing_id in ("room-a", "room-b")
        }
        assert all(room is not None for room in rooms.values())
        reservation = GuestyReservation.from_v3_api_dict(
            make_v3_reservation(
                guest_id=None,
                segments=[
                    make_v3_segment(
                        listingId="room-a",
                        checkInDateLocalized=first_date,
                        checkOutDateLocalized=handoff_date,
                    ),
                    make_v3_segment(
                        listingId="room-b",
                        checkInDateLocalized=handoff_date,
                        checkOutDateLocalized=final_date,
                    ),
                ],
            ),
        )
        assert reservation is not None
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([reservation]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = rooms
        listings_coordinator.timing_listings = rooms
        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        result = await coordinator._async_update_data()

        prior_checkout = (
            datetime.fromisoformat(
                f"{handoff_date}T11:00:00",
            )
            .replace(tzinfo=zone)
            .astimezone(UTC)
        )
        destination = next(
            item for item in result["room-b"] if item.id == reservation.id
        )
        assert destination.check_in == prior_checkout

    @pytest.mark.parametrize(
        "blocked_reason",
        [
            "different-date",
            "different-zone",
            "implicit-zone",
            "missing-settings",
            "invalid-settings",
            "unknown-prior-checkout",
            "nonadjacent-raw-position",
            "destination-checkout-not-later",
        ],
    )
    def test_nonqualifying_handoffs_keep_destination_schedule(
        self,
        blocked_reason: str,
    ) -> None:
        """Every failed handoff predicate leaves the destination unchanged."""
        boundary = datetime(2026, 1, 2, 11, tzinfo=UTC)
        destination_date = "2026-01-02"
        destination_checkout = datetime(2026, 1, 4, 11, tzinfo=UTC)
        destination_zone = "UTC"
        raw_position = 1
        prior_checkout: datetime | None = boundary
        explicit_destination_zone = True
        destination_check_in_time: str | None = "16:00"
        destination_check_out_time: str | None = "11:00"

        if blocked_reason == "different-date":
            destination_date = "2026-01-03"
        elif blocked_reason == "different-zone":
            destination_zone = "America/New_York"
            destination_checkout = datetime(
                2026,
                1,
                4,
                16,
                tzinfo=UTC,
            )
        elif blocked_reason == "implicit-zone":
            explicit_destination_zone = False
        elif blocked_reason == "missing-settings":
            destination_check_out_time = None
        elif blocked_reason == "invalid-settings":
            destination_check_in_time = "25:00"
        elif blocked_reason == "unknown-prior-checkout":
            prior_checkout = None
        elif blocked_reason == "nonadjacent-raw-position":
            raw_position = 2
        elif blocked_reason == "destination-checkout-not-later":
            destination_checkout = boundary

        prior = GuestyStaySegment(
            listing_id="room-a",
            check_in=datetime(2026, 1, 1, 16, tzinfo=UTC),
            check_out=prior_checkout,
            check_in_local="2026-01-01",
            check_out_local="2026-01-02",
            raw_position=0,
        )
        destination_check_in = (
            datetime.fromisoformat(f"{destination_date}T16:00:00")
            .replace(tzinfo=ZoneInfo(destination_zone))
            .astimezone(UTC)
        )
        destination = GuestyStaySegment(
            listing_id="room-b",
            check_in=destination_check_in,
            check_out=destination_checkout,
            check_in_local=destination_date,
            check_out_local="2026-01-04",
            raw_position=raw_position,
        )

        prior_listing = GuestyListing.from_api_dict(
            make_listing_dict(
                _id="room-a",
                timezone="UTC",
                defaultCheckInTime="16:00",
                defaultCheckOutTime="11:00",
            ),
        )
        destination_listing_data = make_listing_dict(
            _id="room-b",
            timezone=destination_zone if explicit_destination_zone else "",
            defaultCheckInTime=destination_check_in_time,
            defaultCheckOutTime=destination_check_out_time,
        )
        destination_listing = GuestyListing.from_api_dict(
            destination_listing_data,
        )
        assert prior_listing is not None
        assert destination_listing is not None
        zones: dict[str, ZoneInfo | None] = {
            "UTC": ZoneInfo("UTC"),
            "America/New_York": ZoneInfo("America/New_York"),
        }

        adjusted = _apply_same_date_handoffs(
            (prior, destination),
            {"room-a": prior_listing, "room-b": destination_listing},
            zones,
        )

        assert adjusted[1].check_in == destination_check_in
        assert adjusted[1].check_out == destination_checkout

    async def test_sorts_reservations_by_check_in(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Reservations per listing are sorted by check_in date."""

        entry = _make_entry()
        entry.add_to_hass(hass)

        res_late = GuestyReservation(
            id="res-late",
            listing_id="listing-001",
            status="confirmed",
            check_in=_rel(31, 15),
            check_out=_rel(35, 11),
        )
        res_early = GuestyReservation(
            id="res-early",
            listing_id="listing-001",
            status="confirmed",
            check_in=_rel(0, 15),
            check_out=_rel(4, 11),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([res_late, res_early]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()
        listing_res = data["listing-001"]
        assert listing_res[0].id == "res-early"
        assert listing_res[1].id == "res-late"

    async def test_update_interval_from_options(
        self,
        hass: HomeAssistant,
    ) -> None:
        """update_interval matches reservation_scan_interval option."""
        entry = _make_entry(
            options={CONF_RESERVATION_SCAN_INTERVAL: 10},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        assert coordinator.update_interval == timedelta(minutes=10)

    async def test_update_interval_default(
        self,
        hass: HomeAssistant,
    ) -> None:
        """update_interval uses default when option not set."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        assert coordinator.update_interval == timedelta(
            minutes=DEFAULT_RESERVATION_SCAN_INTERVAL,
        )

    async def test_raises_update_failed_on_connection_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on connection error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_raises_update_failed_on_auth_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on auth error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyAuthError("auth failed"),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_raises_update_failed_on_response_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on response error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyResponseError("malformed"),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_empty_reservation_list_returns_empty_dict(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data returns empty dict for no reservations."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(return_value=make_fetch_result([]))
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()
        assert data == {}

    async def test_passes_date_range_to_api(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data passes configured date range to API."""
        from custom_components.guesty.const import (
            CONF_FUTURE_DAYS,
            CONF_PAST_DAYS,
        )

        entry = _make_entry(
            options={CONF_PAST_DAYS: 7, CONF_FUTURE_DAYS: 30},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(return_value=make_fetch_result([]))
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        await coordinator._async_update_data()
        api_client.get_reservations.assert_awaited_once_with(
            past_days=7,
            future_days=30,
            candidates={},
        )

    async def test_uses_default_date_range(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data uses default date range when not set."""
        from custom_components.guesty.api.const import (
            DEFAULT_FUTURE_DAYS,
            DEFAULT_PAST_DAYS,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(return_value=make_fetch_result([]))
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        await coordinator._async_update_data()
        api_client.get_reservations.assert_awaited_once_with(
            past_days=DEFAULT_PAST_DAYS,
            future_days=DEFAULT_FUTURE_DAYS,
            candidates={},
        )

    async def test_empty_listings_skips_all_reservations(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Empty listings data skips all reservations gracefully."""

        entry = _make_entry()
        entry.add_to_hass(hass)

        reservation = GuestyReservation(
            id="res-orphan",
            listing_id="listing-orphan",
            status="confirmed",
            check_in=_rel(0, 15),
            check_out=_rel(4, 11),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([reservation]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = None

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()
        assert data == {}


class TestReservationsCoordinatorErrorResilience:
    """Tests for US5 — reservations coordinator error resilience (T024)."""

    async def test_raises_update_failed_on_rate_limit_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on GuestyRateLimitError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyRateLimitError("rate limited"),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with pytest.raises(UpdateFailed, match="rate limited"):
            await coordinator._async_update_data()

    async def test_filter_rejection_surfaces_detail(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A rejected filter reaches the user with its detail (FR-008)."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyFilterRejectedError(
                "Guesty rejected the reservation search filters",
                endpoint="/reservations-v3/search",
                status_code=400,
                body_excerpt="unsupported filter checkIn",
            ),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with pytest.raises(UpdateFailed) as excinfo:
            await coordinator._async_update_data()

        detail = str(excinfo.value)
        assert "/reservations-v3/search" in detail
        assert "400" in detail
        assert "unsupported filter checkIn" in detail
        api_client.get_reservations.assert_awaited_once()

    async def test_filter_rejection_retains_last_known_good(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        sample_reservation: GuestyReservation,
    ) -> None:
        """A rejected filter leaves last-known-good data intact."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([sample_reservation]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )
        await coordinator.async_refresh()

        api_client.get_reservations = AsyncMock(
            side_effect=GuestyFilterRejectedError(
                "Guesty rejected the reservation search filters",
                endpoint="/reservations-v3/search",
                status_code=400,
            ),
        )
        await coordinator.async_refresh()

        assert coordinator.last_update_success is False
        assert coordinator.data is not None
        assert coordinator.data[sample_listing.id] == [sample_reservation]

    async def test_retains_last_known_good_data_after_error(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        sample_reservation: GuestyReservation,
    ) -> None:
        """Coordinator retains last-known-good data after failure (FR-014)."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([sample_reservation]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        # Successful first refresh
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is not None
        assert sample_listing.id in coordinator.data

        # API fails during next refresh
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )
        await coordinator.async_refresh()

        # Last-known-good data retained
        assert coordinator.last_update_success is False
        assert coordinator.data is not None
        assert sample_listing.id in coordinator.data
        assert coordinator.data[sample_listing.id] == [
            sample_reservation,
        ]

    async def test_recovery_updates_data_on_successful_fetch(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        sample_reservation: GuestyReservation,
    ) -> None:
        """Recovery updates data on next successful fetch."""

        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([sample_reservation]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        # Successful first refresh
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True

        # API error during refresh
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyRateLimitError("rate limited"),
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

        # Recovery with new reservation data
        new_reservation = GuestyReservation(
            id="res-new",
            listing_id="listing-001",
            status="checked_in",
            check_in=_rel(31, 15),
            check_out=_rel(35, 11),
        )
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([new_reservation]),
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is not None
        assert len(coordinator.data[sample_listing.id]) == 1
        assert coordinator.data[sample_listing.id][0].id == "res-new"

    async def test_error_logged_on_api_failure(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Error logged on API failure with error context."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(return_value=make_fetch_result([]))
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        # Successful first refresh
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True

        # API failure with specific error context
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyResponseError("malformed JSON body"),
        )
        caplog.clear()
        with caplog.at_level(
            logging.ERROR,
            logger="custom_components.guesty.coordinator",
        ):
            await coordinator.async_refresh()

        assert coordinator.last_update_success is False
        matching = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "malformed JSON body" in r.getMessage()
        ]
        assert len(matching) >= 1


# ── T016 + T028 + T029: CustomFieldsDefinitionCoordinator Tests ─────

_SAMPLE_DEFS = [
    GuestyCustomFieldDefinition(
        field_id="cf-region",
        name="Region",
        field_type="text",
        applicable_to=frozenset({"listing"}),
    ),
    GuestyCustomFieldDefinition(
        field_id="cf-door-code",
        name="Door Code",
        field_type="text",
        applicable_to=frozenset({"reservation"}),
    ),
    GuestyCustomFieldDefinition(
        field_id="cf-priority",
        name="Priority",
        field_type="number",
        applicable_to=frozenset({"listing", "reservation"}),
    ),
]


class TestCustomFieldsDefinitionCoordinator:
    """Tests for CustomFieldsDefinitionCoordinator (T016)."""

    async def test_update_data_calls_get_definitions(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data calls get_definitions."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        assert coordinator.data == _SAMPLE_DEFS
        cf_client.get_definitions.assert_awaited_once()

    async def test_definitions_expose_name_id_type_applicability(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Coordinator data has name, field_id, field_type, applicable_to."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        assert coordinator.data is not None
        for defn in coordinator.data:
            assert defn.field_id is not None
            assert defn.name is not None
            assert defn.field_type is not None
            assert isinstance(defn.applicable_to, frozenset)
            assert len(defn.applicable_to) > 0

    async def test_get_field_returns_matching(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_field returns matching definition."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        field = coordinator.get_field("cf-region")
        assert field is not None
        assert field.name == "Region"

    async def test_get_field_returns_none_not_found(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_field returns None when not found."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        assert coordinator.get_field("cf-nonexistent") is None

    async def test_get_field_none_data_returns_none(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_field returns None when data is None."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        # Don't refresh — data is None
        assert coordinator.get_field("cf-region") is None

    async def test_get_fields_for_listing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_fields_for_target('listing') filters correctly."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        listing_fields = coordinator.get_fields_for_target("listing")
        ids = [f.field_id for f in listing_fields]
        assert "cf-region" in ids
        assert "cf-priority" in ids
        assert "cf-door-code" not in ids

    async def test_get_fields_for_reservation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_fields_for_target('reservation') filters correctly."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        res_fields = coordinator.get_fields_for_target("reservation")
        ids = [f.field_id for f in res_fields]
        assert "cf-door-code" in ids
        assert "cf-priority" in ids
        assert "cf-region" not in ids

    async def test_both_target_appears_in_both(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Field applicable to 'both' appears in both queries."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        listing_ids = [f.field_id for f in coordinator.get_fields_for_target("listing")]
        res_ids = [f.field_id for f in coordinator.get_fields_for_target("reservation")]
        assert "cf-priority" in listing_ids
        assert "cf-priority" in res_ids

    async def test_empty_definitions_returns_empty(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Empty definitions returns empty list without error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(return_value=[])
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        assert coordinator.data == []
        assert coordinator.get_fields_for_target("listing") == []

    async def test_get_fields_none_data_returns_empty(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_fields_for_target returns empty when data is None."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        assert coordinator.get_fields_for_target("listing") == []

    async def test_update_interval_default(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Update interval defaults to DEFAULT_CF_SCAN_INTERVAL."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        assert coordinator.update_interval == timedelta(
            minutes=DEFAULT_CF_SCAN_INTERVAL,
        )

    async def test_update_interval_from_options(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Update interval respects CONF_CF_SCAN_INTERVAL."""
        entry = _make_entry(
            options={CONF_CF_SCAN_INTERVAL: 30},
        )
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        assert coordinator.update_interval == timedelta(minutes=30)

    async def test_api_error_raises_update_failed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """GuestyApiError raises UpdateFailed."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False


class TestCustomFieldDefinitionRefresh:
    """Tests for definition refresh behavior (T028-T029)."""

    async def test_new_field_appears_after_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """New field added in Guesty appears after refresh."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS[:1],
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()
        assert len(coordinator.data) == 1

        # Simulate new field added
        cf_client.get_definitions.return_value = _SAMPLE_DEFS
        await coordinator.async_refresh()
        assert len(coordinator.data) == 3

    async def test_deleted_field_removed_after_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Deleted field removed after coordinator refresh."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()
        assert len(coordinator.data) == 3

        # Simulate field deleted
        cf_client.get_definitions.return_value = _SAMPLE_DEFS[:1]
        await coordinator.async_refresh()
        assert len(coordinator.data) == 1


# ── Phase 4: attribution, projection, state and trim (T041-T047) ────

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _listing(listing_id: str) -> GuestyListing:
    """Build a GuestyListing with the given identifier.

    Args:
        listing_id: The listing identifier.

    Returns:
        A parsed GuestyListing for the identifier.
    """
    listing = GuestyListing.from_api_dict(
        make_listing_dict(_id=listing_id),
    )
    assert listing is not None
    return listing


def _seg(
    listing_id: str,
    start_days: float,
    end_days: float,
    *,
    parent: str | None = None,
    guests: int | None = 2,
) -> GuestyStaySegment:
    """Build a stay segment relative to the frozen test instant.

    Args:
        listing_id: Listing the interval belongs to.
        start_days: Days from ``_NOW`` the interval starts.
        end_days: Days from ``_NOW`` the interval ends.
        parent: Parent listing identifier, when any.
        guests: Party size for the interval.

    Returns:
        A GuestyStaySegment for the interval.
    """
    check_in = _NOW + timedelta(days=start_days)
    check_out = _NOW + timedelta(days=end_days)
    return GuestyStaySegment(
        listing_id=listing_id,
        check_in=check_in,
        check_out=check_out,
        parent_listing_id=parent,
        check_in_local=check_in.date().isoformat(),
        check_out_local=check_out.date().isoformat(),
        guests_count=guests,
    )


def _v3_res(
    res_id: str,
    segments: list[GuestyStaySegment],
    *,
    status: str = "confirmed",
    **overrides: object,
) -> GuestyReservation:
    """Build a v3-shaped reservation carrying stay segments.

    Args:
        res_id: Reservation identifier.
        segments: The stay segments of the reservation.
        status: Reservation-level status.
        **overrides: Further reservation fields.

    Returns:
        A GuestyReservation with the segments attached.
    """
    fields: dict[str, object] = {
        "confirmation_code": f"CONF-{res_id}",
        "source": "airbnb",
        "guests_count": segments[0].guests_count,
        "nights_count": 1,
    }
    fields.update(overrides)
    return GuestyReservation(
        id=res_id,
        listing_id=segments[0].listing_id,
        status=status,
        check_in=segments[0].check_in,
        check_out=segments[-1].check_out,
        check_in_local=segments[0].check_in_local,
        check_out_local=segments[-1].check_out_local,
        stay=tuple(segments),
        **fields,  # type: ignore[arg-type]
    )


def _legacy_res(
    res_id: str,
    listing_id: str,
    start_days: float,
    end_days: float,
    *,
    status: str = "confirmed",
) -> GuestyReservation:
    """Build a legacy-shaped reservation without stay segments.

    Args:
        res_id: Reservation identifier.
        listing_id: Listing the reservation belongs to.
        start_days: Days from ``_NOW`` the stay starts.
        end_days: Days from ``_NOW`` the stay ends.
        status: Reservation status.

    Returns:
        A GuestyReservation with an empty ``stay``.
    """
    return GuestyReservation(
        id=res_id,
        listing_id=listing_id,
        status=status,
        check_in=_NOW + timedelta(days=start_days),
        check_out=_NOW + timedelta(days=end_days),
    )


def _build_coordinator(
    hass: HomeAssistant,
    reservations: list[GuestyReservation],
    known_listing_ids: list[str],
    *,
    entry: MockConfigEntry | None = None,
    fetch_counters: dict[str, object] | None = None,
    guest_result: GuestLookupResult | None = None,
    guest_error: Exception | None = None,
) -> ReservationsCoordinator:
    """Build a ReservationsCoordinator over canned reservations.

    Passing ``guest_result`` or ``guest_error`` also selects the v3
    path, because guest lookups exist only there.

    Args:
        hass: Home Assistant instance.
        reservations: Reservations the API returns.
        known_listing_ids: Listing IDs the listings coordinator knows.
        entry: Config entry to use; a default one when None.
        fetch_counters: Diagnostics counters for the fetch result.
        guest_result: Result the guest cache returns, if any.
        guest_error: Error the guest lookup raises, if any.

    Returns:
        A ReservationsCoordinator wired to mocks.
    """
    if entry is None:
        entry = _make_entry()
        entry.add_to_hass(hass)
    api_client = AsyncMock()
    api_client.get_reservations = AsyncMock(
        return_value=make_fetch_result(
            reservations,
            **(fetch_counters or {}),
        ),
    )
    if guest_result is not None or guest_error is not None:
        api_client.reservations_api = RESERVATIONS_API_V3
        api_client.resolve_guests = AsyncMock(
            return_value=guest_result,
            side_effect=guest_error,
        )
    listings_coordinator = AsyncMock()
    listings_coordinator.data = {
        listing_id: _listing(listing_id) for listing_id in known_listing_ids
    }
    return ReservationsCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
        listings_coordinator=listings_coordinator,
    )


class TestSegmentAttribution:
    """Tests for stay-segment attribution (T041, T045, T046)."""

    async def test_timing_listing_lookup_is_shared_for_refresh(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """The fetched listing map is prepared once per reservation refresh."""
        freezer.move_to(_NOW)
        reservations = [
            _legacy_res(f"res-{index}", "listing-001", 1, 2) for index in range(3)
        ]
        coordinator = _build_coordinator(
            hass,
            reservations,
            ["listing-001"],
        )

        with patch.object(
            coordinator,
            "_timing_listings",
            wraps=coordinator._timing_listings,
        ) as timing_listings:
            await coordinator._async_update_data()

        timing_listings.assert_called_once()

    async def test_single_segment_attributed_to_its_listing(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A single-segment reservation lands on its listing."""
        freezer.move_to(_NOW)
        reservation = _v3_res("res-1", [_seg("listing-001", -1, 3)])
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        data = await coordinator._async_update_data()

        assert list(data) == ["listing-001"]
        assert [r.id for r in data["listing-001"]] == ["res-1"]

    async def test_two_segments_attributed_to_both_listings(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A mid-stay move is visible on origin and destination."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-move",
            [_seg("listing-001", -2, 0), _seg("listing-002", 0, 3)],
            status="checked_in",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )
        data = await coordinator._async_update_data()

        assert set(data) == {"listing-001", "listing-002"}
        assert data["listing-001"][0].id == "res-move"
        assert data["listing-002"][0].id == "res-move"

    async def test_segment_resolves_through_parent_listing(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """An unknown sub-unit resolves through its parent."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-sub",
            [_seg("sub-unit-9", -1, 2, parent="listing-001")],
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        data = await coordinator._async_update_data()

        assert list(data) == ["listing-001"]
        assert data["listing-001"][0].listing_id == "listing-001"

    async def test_partly_unknown_reservation_is_published(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An unknown segment is logged at debug, not dropped."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-partial",
            [_seg("listing-001", -2, 0), _seg("listing-999", 0, 3)],
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.coordinator",
        ):
            data = await coordinator._async_update_data()

        assert list(data) == ["listing-001"]
        debug_messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "custom_components.guesty.coordinator"
            and record.levelno == logging.DEBUG
        ]
        assert any("listing-999" in message for message in debug_messages)
        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        ]
        assert not any("res-partial" in message for message in warnings)

    async def test_wholly_unknown_reservation_is_skipped(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A reservation with no known segment is skipped."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-orphan",
            [_seg("listing-998", -1, 2), _seg("listing-999", 2, 4)],
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.guesty.coordinator",
        ):
            data = await coordinator._async_update_data()

        assert data == {}
        assert any(
            "res-orphan" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        )

    async def test_reservation_without_stay_uses_listing_id(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A legacy reservation keeps its top-level attribution."""
        freezer.move_to(_NOW)
        reservation = _legacy_res("res-legacy", "listing-001", -1, 2)
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        data = await coordinator._async_update_data()

        assert data["listing-001"] == [reservation]

    async def test_reservation_without_stay_unknown_is_skipped(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A stay-less reservation off a known listing is skipped."""
        freezer.move_to(_NOW)
        reservation = _legacy_res("res-nostay", "listing-999", -1, 2)
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.guesty.coordinator",
        ):
            data = await coordinator._async_update_data()

        assert data == {}
        assert any(
            "res-nostay" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        )

    async def test_alteration_reservation_is_dropped(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An alteration never reaches the published data (FR-024)."""
        freezer.move_to(_NOW)
        published = _v3_res("res-ok", [_seg("listing-001", -1, 2)])
        alteration = _v3_res(
            "res-alt",
            [_seg("listing-001", 1, 4)],
            status="alteration",
        )
        coordinator = _build_coordinator(
            hass,
            [published, alteration],
            ["listing-001"],
        )

        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.coordinator",
        ):
            data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-ok"]
        assert any(
            "res-alt" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.DEBUG
        )
        assert any(
            "alteration_drops=1" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.DEBUG
        )

    async def test_filtered_out_listing_publishes_nothing(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Feature 007 filtering alone decides tracking (FR-014)."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-filtered",
            [_seg("listing-001", -1, 2), _seg("listing-002", 2, 4)],
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-002"],
        )

        data = await coordinator._async_update_data()

        assert list(data) == ["listing-002"]
        fetch = cast("AsyncMock", coordinator.api_client.get_reservations)
        fetch.assert_awaited_once_with(
            past_days=DEFAULT_PAST_DAYS,
            future_days=DEFAULT_FUTURE_DAYS,
            candidates={},
        )


class TestPerListingProjection:
    """Tests for the per-listing projection (T042)."""

    async def test_single_segment_projects_to_itself(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A single-segment projection keeps every own field."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-single",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(full_name="Jane", guest_id="guest-1"),
            custom_fields=MappingProxyType({"cf_a": "A"}),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        projection = (await coordinator._async_update_data())["listing-001"][0]

        assert projection.id == reservation.id
        assert projection.listing_id == "listing-001"
        assert projection.status == reservation.status
        assert projection.check_in == reservation.check_in
        assert projection.check_out == reservation.check_out
        assert projection.check_in_local == reservation.check_in_local
        assert projection.check_out_local == reservation.check_out_local
        assert projection.guests_count == reservation.guests_count
        assert projection.nights_count == reservation.nights_count
        assert projection.confirmation_code == reservation.confirmation_code
        assert projection.source == reservation.source
        assert projection.guest == reservation.guest
        assert projection.custom_fields == reservation.custom_fields
        assert projection.stay == ()

    async def test_multi_segment_uses_that_listings_segment(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Each listing gets its own dates, guests and nights."""
        freezer.move_to(_NOW)
        first = _seg("listing-001", -2, 0, guests=2)
        second = _seg("listing-002", 0, 3, guests=4)
        reservation = _v3_res(
            "res-multi",
            [first, second],
            status="checked_in",
            guest=GuestyGuest(full_name="Jane", guest_id="guest-1"),
            custom_fields=MappingProxyType({"cf_a": "A"}),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        data = await coordinator._async_update_data()
        origin = data["listing-001"][0]
        destination = data["listing-002"][0]

        assert origin.check_in == first.check_in
        assert origin.check_out == first.check_out
        assert origin.check_in_local == first.check_in_local
        assert origin.check_out_local == first.check_out_local
        assert origin.guests_count == 2
        assert origin.nights_count == 2
        assert destination.check_in == second.check_in
        assert destination.check_out == second.check_out
        assert destination.guests_count == 4
        assert destination.nights_count == 3
        for projection in (origin, destination):
            assert projection.id == "res-multi"
            assert projection.confirmation_code == "CONF-res-multi"
            assert projection.source == "airbnb"
            assert projection.guest == reservation.guest
            assert projection.custom_fields == reservation.custom_fields
            assert projection.stay == ()

    async def test_source_night_count_survives_unknown_local_checkout(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Timing resolution does not erase a valid source night count."""
        listing_data = make_listing_dict(
            _id="listing-001",
            timezone="UTC",
            defaultCheckInTime="16:00",
            defaultCheckOutTime="11:00",
        )
        listing = GuestyListing.from_api_dict(listing_data)
        assert listing is not None
        local_check_in = "2026-06-20"
        segment = GuestyStaySegment(
            listing_id="listing-001",
            check_in=None,
            check_out=None,
            check_in_local=local_check_in,
            check_out_local=None,
            raw_position=0,
        )
        reservation = GuestyReservation(
            id="res-night-count",
            listing_id="listing-001",
            status="confirmed",
            check_in=None,
            check_out=None,
            check_in_local=local_check_in,
            check_out_local=None,
            nights_count=4,
            is_v3=True,
            raw_stay_count=1,
            stay=(segment,),
        )
        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=_make_entry(),
            api_client=AsyncMock(),
            listings_coordinator=AsyncMock(
                data={"listing-001": listing},
                timing_listings={"listing-001": listing},
            ),
        )

        resolved = await coordinator._resolve_v3_reservations((reservation,))

        assert resolved[0].nights_count == 4

    async def test_repeat_listing_selects_current_interval(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """An A → B → A stay keeps A's intervals disjoint."""
        freezer.move_to(_NOW)
        first_a = _seg("listing-001", -6, -4)
        stay_b = _seg("listing-002", -4, -1)
        second_a = _seg("listing-001", -1, 2)
        reservation = _v3_res(
            "res-aba",
            [first_a, stay_b, second_a],
            status="checked_in",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        projection = (await coordinator._async_update_data())["listing-001"][0]

        assert projection.check_in == second_a.check_in
        assert projection.check_out == second_a.check_out

    async def test_repeat_listing_selects_earliest_future_interval(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """With no current interval the earliest future one wins."""
        freezer.move_to(_NOW)
        first_a = _seg("listing-001", 2, 4)
        stay_b = _seg("listing-002", 4, 6)
        second_a = _seg("listing-001", 6, 8)
        reservation = _v3_res("res-future", [first_a, stay_b, second_a])
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        projection = (await coordinator._async_update_data())["listing-001"][0]

        assert projection.check_in == first_a.check_in
        assert projection.check_out == first_a.check_out

    async def test_repeat_listing_selects_latest_past_interval(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """With only past intervals the latest one wins."""
        freezer.move_to(_NOW)
        first_a = _seg("listing-001", -8, -6)
        stay_b = _seg("listing-002", -6, -4)
        second_a = _seg("listing-001", -4, -2)
        reservation = _v3_res(
            "res-past",
            [first_a, stay_b, second_a],
            status="checked_out",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        projection = (await coordinator._async_update_data())["listing-001"][0]

        assert projection.check_in == second_a.check_in
        assert projection.check_out == second_a.check_out


class TestSegmentAwareState:
    """Tests for segment-aware status derivation (T043)."""

    async def test_before_move_origin_in_destination_upcoming(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Before the move the guest is only in the origin."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-move",
            [_seg("listing-001", -1, 1), _seg("listing-002", 1, 4)],
            status="checked_in",
        )
        occupant = _v3_res(
            "res-occupant",
            [_seg("listing-002", -2, 1)],
            status="checked_in",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation, occupant],
            ["listing-001", "listing-002"],
        )

        data = await coordinator._async_update_data()

        assert data["listing-001"][0].status == "checked_in"
        moving = next(r for r in data["listing-002"] if r.id == "res-move")
        current = next(r for r in data["listing-002"] if r.id == "res-occupant")
        assert moving.status == "confirmed"
        assert current.status == "checked_in"

    async def test_after_move_origin_checked_out(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """After the move the origin is past and destination live."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-move",
            [_seg("listing-001", -4, -1), _seg("listing-002", -1, 2)],
            status="checked_in",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        data = await coordinator._async_update_data()

        assert data["listing-001"][0].status == "checked_out"
        assert data["listing-002"][0].status == "checked_in"

    async def test_canceled_multi_segment_stays_canceled(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A canceled stay is canceled on every listing it touches."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-canceled",
            [_seg("listing-001", -1, 1), _seg("listing-002", 1, 4)],
            status="canceled",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        data = await coordinator._async_update_data()

        assert data["listing-001"][0].status == "canceled"
        assert data["listing-002"][0].status == "canceled"

    async def test_unknown_multi_segment_status_stays_temporally_unknown(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Localized dates do not turn unknown intervals into live states."""
        freezer.move_to(_NOW)
        stay = (
            GuestyStaySegment(
                listing_id="listing-001",
                check_in=None,
                check_out=None,
                check_in_local=_NOW.date().isoformat(),
                check_out_local=(_NOW + timedelta(days=2)).date().isoformat(),
                raw_position=0,
            ),
            GuestyStaySegment(
                listing_id="listing-002",
                check_in=None,
                check_out=None,
                check_in_local=(_NOW + timedelta(days=2)).date().isoformat(),
                check_out_local=(_NOW + timedelta(days=4)).date().isoformat(),
                raw_position=1,
            ),
        )
        reservation = GuestyReservation(
            id="res-unknown-move",
            listing_id="listing-001",
            status="checked_in",
            check_in=None,
            check_out=None,
            confirmation_code="CONF-UNKNOWN-MOVE",
            check_in_local=stay[0].check_in_local,
            check_out_local=stay[-1].check_out_local,
            stay=stay,
            is_v3=True,
            raw_stay_count=2,
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )
        coordinator.listings_coordinator.timing_listings = {
            listing_id: replace(_listing(listing_id), timezone_explicit=False)
            for listing_id in ("listing-001", "listing-002")
        }

        data = await coordinator._async_update_data()

        assert set(data) == {"listing-001", "listing-002"}
        for projections in data.values():
            projection = projections[0]
            assert projection.check_in is None
            assert projection.check_out is None
            assert projection.status == "unknown"
            assert projection.reservation_status == "checked_in"


class TestWindowTrim:
    """Tests for the instant-based window trim (T044)."""

    async def test_padded_boundary_results_are_dropped(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Results outside the configured window are not published."""
        freezer.move_to(_NOW)
        entry = _make_entry(
            options={CONF_PAST_DAYS: 2, CONF_FUTURE_DAYS: 5},
        )
        entry.add_to_hass(hass)
        too_old = _v3_res(
            "res-old",
            [_seg("listing-001", -6, -3)],
            status="checked_out",
        )
        too_far = _v3_res("res-far", [_seg("listing-001", 6, 8)])
        inside = _v3_res("res-inside", [_seg("listing-001", -1, 1)])
        coordinator = _build_coordinator(
            hass,
            [too_old, too_far, inside],
            ["listing-001"],
            entry=entry,
        )

        data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-inside"]

    async def test_in_progress_stay_is_never_dropped(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A long in-progress stay survives however old (FR-004)."""
        freezer.move_to(_NOW)
        entry = _make_entry(
            options={CONF_PAST_DAYS: 2, CONF_FUTURE_DAYS: 5},
        )
        entry.add_to_hass(hass)
        long_stay = _v3_res(
            "res-long",
            [_seg("listing-001", -400, 1)],
            status="checked_in",
        )
        coordinator = _build_coordinator(
            hass,
            [long_stay],
            ["listing-001"],
            entry=entry,
        )

        data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-long"]

    async def test_trim_applies_to_legacy_shaped_reservations(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """The trim also narrows the legacy path (FR-002a)."""
        freezer.move_to(_NOW)
        entry = _make_entry(
            options={CONF_PAST_DAYS: 2, CONF_FUTURE_DAYS: 5},
        )
        entry.add_to_hass(hass)
        too_old = _legacy_res(
            "res-old",
            "listing-001",
            -9,
            -6,
            status="checked_out",
        )
        inside = _legacy_res("res-inside", "listing-001", -1, 1)
        coordinator = _build_coordinator(
            hass,
            [too_old, inside],
            ["listing-001"],
            entry=entry,
        )

        data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-inside"]

    def test_unknown_v3_window_uses_inclusive_local_date_boundaries(self) -> None:
        """Date-only candidates include both configured local-date edges."""
        now = datetime(2026, 1, 10, 12, tzinfo=UTC)
        listing = GuestyListing.from_api_dict(
            make_listing_dict(
                _id="listing-001",
                timezone="UTC",
                defaultCheckInTime="16:00",
                defaultCheckOutTime="11:00",
            ),
        )
        assert listing is not None
        reservations = (
            GuestyReservation(
                id="res-lower-bound",
                listing_id=listing.id,
                status="confirmed",
                check_in=None,
                check_out=None,
                is_v3=True,
                raw_stay_count=1,
                stay=(
                    GuestyStaySegment(
                        listing_id=listing.id,
                        check_in=None,
                        check_out=None,
                        check_in_local="2026-01-07",
                        check_out_local="2026-01-08",
                        raw_position=0,
                    ),
                ),
            ),
            GuestyReservation(
                id="res-upper-bound",
                listing_id=listing.id,
                status="confirmed",
                check_in=None,
                check_out=None,
                is_v3=True,
                raw_stay_count=1,
                stay=(
                    GuestyStaySegment(
                        listing_id=listing.id,
                        check_in=None,
                        check_out=None,
                        check_in_local="2026-01-15",
                        check_out_local="2026-01-16",
                        raw_position=0,
                    ),
                ),
            ),
        )

        assert all(
            _within_window(
                reservation,
                now=now,
                past_days=2,
                future_days=5,
                timing_listings={listing.id: listing},
                zones={"UTC": ZoneInfo("UTC")},
            )
            for reservation in reservations
        )

    def test_unverifiable_date_window_retains_server_bounded_v3_result(
        self,
    ) -> None:
        """Missing date or explicit zone never guesses an instant."""
        now = datetime(2026, 1, 10, 12, tzinfo=UTC)
        implicit_zone = GuestyListing.from_api_dict(
            make_listing_dict(
                _id="listing-001",
                timezone="",
                defaultCheckInTime="16:00",
                defaultCheckOutTime="11:00",
            ),
        )
        assert implicit_zone is not None
        reservation = GuestyReservation(
            id="res-unbounded",
            listing_id=implicit_zone.id,
            status="confirmed",
            check_in=None,
            check_out=None,
            is_v3=True,
            raw_stay_count=1,
            stay=(
                GuestyStaySegment(
                    listing_id=implicit_zone.id,
                    check_in=None,
                    check_out=None,
                    check_in_local="invalid",
                    check_out_local="2025-01-01",
                    raw_position=0,
                ),
            ),
        )

        assert _within_window(
            reservation,
            now=now,
            past_days=2,
            future_days=5,
            timing_listings={implicit_zone.id: implicit_zone},
            zones={"UTC": ZoneInfo("UTC")},
        )

    def test_unknown_timing_order_is_deterministic_after_known_instants(self) -> None:
        """Known instants lead; unknown dates and absent dates remain stable."""
        known = GuestyReservation(
            id="res-known",
            listing_id="listing-001",
            status="confirmed",
            check_in=datetime(2026, 1, 1, tzinfo=UTC),
            check_out=datetime(2026, 1, 2, tzinfo=UTC),
        )
        unknown_later = GuestyReservation(
            id="res-z",
            listing_id="listing-001",
            status="unknown",
            check_in=None,
            check_out=None,
            check_in_local="2026-01-03",
            is_v3=True,
        )
        unknown_earlier_id = replace(unknown_later, id="res-a")
        unknown_earlier_date = replace(
            unknown_later,
            id="res-b",
            check_in_local="2026-01-02",
        )
        unknown_without_date = replace(
            unknown_later,
            id="res-no-date",
            check_in_local=None,
        )
        partial = replace(
            unknown_later,
            id="res-partial",
            check_in=datetime(2020, 1, 1, tzinfo=UTC),
            check_out=None,
        )

        ordered = sorted(
            [
                unknown_without_date,
                unknown_later,
                known,
                unknown_earlier_id,
                unknown_earlier_date,
                partial,
            ],
            key=_reservation_sort_key,
        )

        assert [reservation.id for reservation in ordered] == [
            "res-known",
            "res-b",
            "res-a",
            "res-partial",
            "res-z",
            "res-no-date",
        ]


class TestRefreshDiagnostics:
    """Tests for the per-refresh DEBUG diagnostics (T052)."""

    async def test_debug_line_carries_counters_without_guest_data(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The refresh line reports counters and no personal data."""
        freezer.move_to(_NOW)
        entry = _make_entry(
            options={CONF_PAST_DAYS: 2, CONF_FUTURE_DAYS: 5},
        )
        entry.add_to_hass(hass)
        guest = GuestyGuest(
            full_name="Jane Traveller",
            phone="+15551234567",
            email="jane@example.com",
            guest_id="guest-1",
        )
        published = _v3_res(
            "res-ok",
            [_seg("listing-001", -1, 1), _seg("listing-999", 1, 3)],
            guest=guest,
        )
        alteration = _v3_res(
            "res-alt",
            [_seg("listing-001", 1, 2)],
            status="alteration",
        )
        trimmed = _v3_res(
            "res-far",
            [_seg("listing-001", 9, 11)],
        )
        coordinator = _build_coordinator(
            hass,
            [published, alteration, trimmed],
            ["listing-001"],
            entry=entry,
            fetch_counters={
                "pages": 2,
                "status_counts": MappingProxyType(
                    {"confirmed": 2, "alteration": 1},
                ),
                "segment_histogram": MappingProxyType({1: 2, 2: 1}),
            },
        )

        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.coordinator",
        ):
            await coordinator._async_update_data()

        lines = [
            record.getMessage()
            for record in caplog.records
            if record.name == "custom_components.guesty.coordinator"
            and record.levelno == logging.DEBUG
            and "pages=2" in record.getMessage()
        ]
        assert len(lines) == 1
        line = lines[0]
        assert "'confirmed': 2" in line
        assert "segments=" in line
        assert "attributed=1" in line
        assert "partially_attributed=1" in line
        assert "skipped=0" in line
        assert "alteration_drops=1" in line
        assert "trim_drops=1" in line
        for secret in ("Jane Traveller", "+15551234567", "jane@example.com"):
            assert secret not in caplog.text


class TestGuestLookupScope:
    """Tests for which reservations cost a guest lookup (T055)."""

    async def test_only_attributed_reservations_are_looked_up(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Untracked, dropped and guest-less stays cost nothing."""
        freezer.move_to(_NOW)
        tracked = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        untracked = _v3_res(
            "res-2",
            [_seg("listing-999", -1, 3)],
            guest=GuestyGuest(guest_id="guest-2"),
        )
        altered = _v3_res(
            "res-3",
            [_seg("listing-001", 1, 2)],
            status="alteration",
            guest=GuestyGuest(guest_id="guest-3"),
        )
        far_away = _v3_res(
            "res-4",
            [_seg("listing-001", 400, 402)],
            guest=GuestyGuest(guest_id="guest-4"),
        )
        no_guest = _v3_res("res-5", [_seg("listing-001", -1, 3)])
        coordinator = _build_coordinator(
            hass,
            [tracked, untracked, altered, far_away, no_guest],
            ["listing-001"],
            guest_result=GuestLookupResult(
                guests={"guest-1": GuestyGuest(guest_id="guest-1")},
            ),
        )

        await coordinator._async_update_data()

        resolve = cast(AsyncMock, coordinator.api_client.resolve_guests)
        resolve.assert_awaited_once()
        assert resolve.await_args is not None
        assert set(resolve.await_args.args[0]) == {"guest-1"}

    async def test_no_lookup_when_no_reservation_has_a_guest(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A refresh with no guest ids issues no lookup at all."""
        freezer.move_to(_NOW)
        reservation = _v3_res("res-1", [_seg("listing-001", -1, 3)])
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_result=GuestLookupResult(),
        )

        await coordinator._async_update_data()

        cast(
            AsyncMock,
            coordinator.api_client.resolve_guests,
        ).assert_not_awaited()

    async def test_resolved_guest_is_published(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A resolved guest replaces the id-only placeholder."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        resolved = GuestyGuest(
            full_name="Jane Traveller",
            phone="+15551234567",
            email="jane@example.com",
            guest_id="guest-1",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_result=GuestLookupResult(guests={"guest-1": resolved}),
        )

        data = await coordinator._async_update_data()

        assert data["listing-001"][0].guest == resolved

    async def test_unresolved_guest_publishes_the_reservation(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A deferred or missing guest still publishes the stay."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_result=GuestLookupResult(deferrals=1),
        )

        data = await coordinator._async_update_data()

        published = data["listing-001"][0]
        assert published.id == "res-1"
        assert published.guest == GuestyGuest(guest_id="guest-1")

    async def test_guest_lookup_error_never_fails_the_refresh(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """An exception from the cache degrades only the guest."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_error=GuestyConnectionError("offline"),
        )

        data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-1"]

    async def test_untranslated_guest_error_is_contained(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """An error the client never wraps still cannot escape."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_error=httpx.ReadError("connection reset"),
        )

        data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-1"]

    async def test_legacy_path_issues_no_guest_lookup(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """The legacy path keeps its embedded guest untouched."""
        freezer.move_to(_NOW)
        embedded = GuestyGuest(
            full_name="Jane Traveller",
            guest_id="guest-1",
        )
        reservation = _legacy_res("res-1", "listing-001", -1, 3)
        reservation = replace(reservation, guest=embedded)
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )
        cast(
            AsyncMock,
            coordinator.api_client,
        ).reservations_api = RESERVATIONS_API_LEGACY

        data = await coordinator._async_update_data()

        assert data["listing-001"][0].guest == embedded
        cast(
            AsyncMock,
            coordinator.api_client.resolve_guests,
        ).assert_not_awaited()

    async def test_diagnostics_report_guest_counters(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The FR-042 line carries the guest cache counters."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_result=GuestLookupResult(
                guests={
                    "guest-1": GuestyGuest(
                        full_name="Jane Traveller",
                        phone="+15551234567",
                        email="jane@example.com",
                        guest_id="guest-1",
                    ),
                },
                hits=3,
                lookups=1,
                failures=2,
                deferrals=4,
            ),
        )

        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.coordinator",
        ):
            await coordinator._async_update_data()

        lines = [
            record.getMessage()
            for record in caplog.records
            if "guest_hits=" in record.getMessage()
        ]
        assert len(lines) == 1
        assert "guest_hits=3" in lines[0]
        assert "guest_lookups=1" in lines[0]
        assert "guest_failures=2" in lines[0]
        assert "guest_deferrals=4" in lines[0]
        for secret in ("Jane Traveller", "+15551234567", "jane@example.com"):
            assert secret not in caplog.text


class TestNoFolioOrPaymentsRequests:
    """No financial resource is ever contacted (T088, FR-043)."""

    @staticmethod
    def _client(path: str) -> GuestyApiClient:
        """Build a real API client on one reservation path.

        Args:
            path: The reservation path to select.

        Returns:
            A client whose HTTP calls respx can observe.
        """
        http = httpx.AsyncClient()
        return GuestyApiClient(
            token_manager=GuestyTokenManager(
                client_id="test-client-id",
                client_secret="test-client-secret",
                http_client=http,
                storage=FakeTokenStorage(),
                refresh_buffer=0,
            ),
            http_client=http,
            reservations_api=path,
        )

    async def _refresh(
        self,
        hass: HomeAssistant,
        path: str,
        page: dict[str, object],
        endpoint: str,
    ) -> list[str]:
        """Run one full refresh and report the URLs it requested.

        Args:
            hass: Home Assistant instance.
            path: The reservation path to run.
            page: The reservation page the endpoint answers with.
            endpoint: The reservation endpoint of that path.

        Returns:
            Every URL the refresh requested, as a string.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {"listing-001": _listing("listing-001")}
        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=self._client(path),
            listings_coordinator=listings_coordinator,
        )
        with respx.mock(assert_all_called=False) as router:
            router.post(TOKEN_URL).mock(
                return_value=httpx.Response(200, json=make_token_response()),
            )
            router.get(f"{BASE_URL}{endpoint}").mock(
                return_value=httpx.Response(200, json=page),
            )
            catch_all = router.route().mock(
                return_value=httpx.Response(200, json={}),
            )
            await coordinator._async_update_data()
            assert not catch_all.called
            return [str(call.request.url) for call in router.calls]

    async def test_v3_refresh_touches_no_financial_resource(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The v3 path asks for no folio and no payments."""
        urls = await self._refresh(
            hass,
            RESERVATIONS_API_V3,
            make_v3_search_page([make_v3_reservation("res-001")]),
            RESERVATIONS_V3_SEARCH_ENDPOINT,
        )

        assert urls
        for url in urls:
            assert "guest-folio" not in url
            assert "payments" not in url

    async def test_legacy_refresh_touches_no_financial_resource(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The rollback target asks for nothing extra either."""
        urls = await self._refresh(
            hass,
            RESERVATIONS_API_LEGACY,
            {"results": [], "count": 0, "limit": 100, "skip": 0},
            RESERVATIONS_ENDPOINT,
        )

        assert urls
        for url in urls:
            assert "guest-folio" not in url
            assert "payments" not in url

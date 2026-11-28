# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared test fixtures for the Guesty integration test suite."""

from __future__ import annotations

import re
from collections.abc import Generator, Mapping
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry, DeviceRegistry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.models import (
    CachedToken,
    GuestyAddress,
    GuestyCustomFieldDefinition,
    GuestyGuest,
    GuestyListing,
    GuestyMoney,
    GuestyReservation,
    ReservationFetchResult,
    TokenStorage,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
)

# Common test constants
FAKE_CLIENT_ID = "test-client-id"
FAKE_CLIENT_SECRET = "test-client-secret"
FAKE_TOKEN = "test-access-token-jwt"
FAKE_TOKEN_URL = "https://open-api.guesty.com/oauth2/token"
FAKE_BASE_URL = "https://open-api.guesty.com/v1"


def make_fetch_result(
    reservations: list[GuestyReservation] | None = None,
    **counters: Any,
) -> ReservationFetchResult:
    """Wrap reservations in a ReservationFetchResult for mocks.

    Args:
        reservations: Reservations the fetch returned.
        **counters: Diagnostics counters to override.

    Returns:
        A ReservationFetchResult carrying the reservations.
    """
    return ReservationFetchResult(
        reservations=tuple(reservations or ()),
        **counters,
    )


@contextmanager
def stub_guesty_network(
    *,
    listings: list[GuestyListing] | None = None,
    reservations: list[GuestyReservation] | None = None,
    bootstrap: list[GuestyReservation] | None = None,
) -> Generator[None]:
    """Patch every network call a config entry setup makes.

    An entry that already has reservation entities runs the
    one-time legacy identity bootstrap sweep (FR-031d), which talks
    to the legacy source directly rather than through
    ``get_reservations``, so a test loading an upgraded entry has to
    stub that too.

    Args:
        listings: Listings the listings coordinator receives.
        reservations: Reservations each refresh receives.
        bootstrap: Reservations the bootstrap sweep receives.

    Yields:
        None, with the Guesty client patched for the duration.
    """
    legacy_source = MagicMock()
    legacy_source.return_value.fetch = AsyncMock(
        return_value=make_fetch_result(list(bootstrap or ())),
    )
    with (
        patch(
            "custom_components.guesty.GuestyApiClient.test_connection",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "custom_components.guesty.GuestyApiClient.get_account_id",
            new_callable=AsyncMock,
            return_value="account-001",
        ),
        patch(
            "custom_components.guesty.GuestyApiClient.get_listings",
            new_callable=AsyncMock,
            return_value=list(listings or ()),
        ),
        patch(
            "custom_components.guesty.GuestyApiClient.get_reservations",
            new_callable=AsyncMock,
            return_value=make_fetch_result(list(reservations or ())),
        ),
        patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "custom_components.guesty.identity.LegacyReservationSource",
            legacy_source,
        ),
    ):
        yield


def make_identity_reservation(
    reservation_id: str,
    confirmation_code: str | None = None,
) -> GuestyReservation:
    """Build a reservation the identity baseline can record.

    Args:
        reservation_id: Guesty reservation identifier.
        confirmation_code: Booking confirmation code, if any.

    Returns:
        A reservation carrying only identity-relevant fields.
    """
    now = datetime.now(UTC)
    return GuestyReservation(
        id=reservation_id,
        listing_id="listing-001",
        status="confirmed",
        check_in=now + timedelta(days=1),
        check_out=now + timedelta(days=3),
        confirmation_code=confirmation_code,
    )


def make_identity_reservations(
    *reservation_ids: str,
) -> list[GuestyReservation]:
    """Build coded reservations for every write target given.

    Writes are refused unless the identity baseline knows the target
    and its confirmation code, so tests exercising reservation writes
    seed the first refresh with these.

    Args:
        *reservation_ids: Identifiers the tests target.

    Returns:
        One coded reservation per identifier.
    """
    return [
        make_identity_reservation(target, f"CONF-{target}")
        for target in reservation_ids
    ]


def device_for_listing(
    dev_reg: DeviceRegistry,
    entry_id: str,
    listing_id: str,
) -> DeviceEntry | None:
    """Look up the device registered for a listing.

    Uses ``async_entries_for_config_entry`` rather than
    ``DeviceRegistry.async_get_device``, which Home Assistant 2026.10
    turned into a ``RuntimeError``. The suggested replacements are not
    available in every supported Home Assistant version, while this
    helper works across all of them.

    Args:
        dev_reg: Device registry to search.
        entry_id: Config entry the device belongs to.
        listing_id: Guesty listing identifier to match.

    Returns:
        The matching device entry, or None when no device exists.
    """
    for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
        if (DOMAIN, listing_id) in device.identifiers:
            return device
    return None


def device_in_registry(
    dev_reg: DeviceRegistry,
    listing_id: str,
) -> DeviceEntry | None:
    """Look up a listing's device anywhere in the device registry.

    Unlike :func:`device_for_listing`, this is not scoped to a config
    entry, so it distinguishes a device that was fully removed from one
    that merely lost its config entry association.

    Home Assistant 2026.10 forbids using ``DeviceRegistry.devices`` as a
    mapping and yields device entries when iterating it, while earlier
    releases still yield device identifiers. Both shapes are handled so
    the helper works across every supported version.

    Args:
        dev_reg: Device registry to search.
        listing_id: Guesty listing identifier to match.

    Returns:
        The matching device entry, or None when no device exists.
    """
    for item in dev_reg.devices:
        device = dev_reg.async_get(item) if isinstance(item, str) else item
        if device is not None and (DOMAIN, listing_id) in device.identifiers:
            return device
    return None


def make_token_response(**overrides: Any) -> dict[str, Any]:
    """Create a mock Guesty token endpoint response.

    Args:
        **overrides: Fields to override on the default response.

    Returns:
        Dictionary matching the Guesty token endpoint response format.
    """
    defaults: dict[str, Any] = {
        "token_type": "Bearer",
        "access_token": FAKE_TOKEN,
        "expires_in": 86400,
        "scope": "open-api",
    }
    defaults.update(overrides)
    return defaults


class FakeConflictEvidence:
    """Configurable test double for the feature 013 evidence port.

    Production uses ``IdentityConflictEvidence`` over feature 013's
    real interfaces; this double isolates the by-ID write fallback
    from 013 so states the real evidence reaches only indirectly can
    be driven directly.

    Attributes:
        is_available: What ``available()`` reports.
        codes: Conflict-only reservation ID to codes evidence.
        ids: Conflict-only code to reservation IDs evidence.
        verdict: What ``validate_record`` returns.
        calls: Every call made, as ``(method, argument)`` pairs.
    """

    def __init__(
        self,
        *,
        available: bool = True,
        codes: dict[str, frozenset[str]] | None = None,
        ids: dict[str, frozenset[str]] | None = None,
        verdict: str = "valid",
    ) -> None:
        """Configure the double.

        Args:
            available: What ``available()`` reports.
            codes: Conflict-only ID to codes evidence.
            ids: Conflict-only code to IDs evidence.
            verdict: What ``validate_record`` returns.
        """
        self.is_available = available
        self.codes = codes or {}
        self.ids = ids or {}
        self.verdict = verdict
        self.calls: list[tuple[str, object]] = []

    def available(self) -> bool:
        """Report whether the evidence can be consulted.

        Returns:
            The configured availability.
        """
        self.calls.append(("available", None))
        return self.is_available

    def codes_for(self, reservation_id: str) -> frozenset[str]:
        """Return conflict-only codes recorded for an identifier.

        Args:
            reservation_id: The identifier to look up.

        Returns:
            The recorded codes, possibly empty.
        """
        self.calls.append(("codes_for", reservation_id))
        return self.codes.get(reservation_id, frozenset())

    def ids_for(self, code: str) -> frozenset[str]:
        """Return conflict-only identifiers recorded for a code.

        Args:
            code: The code to look up.

        Returns:
            The recorded identifiers, possibly empty.
        """
        self.calls.append(("ids_for", code))
        return self.ids.get(code, frozenset())

    def validate_record(self, raw: Mapping[str, Any], action_type: str) -> str:
        """Return the configured single-record verdict.

        Args:
            raw: The raw by-ID record.
            action_type: The action the record would authorize.

        Returns:
            The configured verdict.
        """
        self.calls.append(("validate_record", action_type))
        return self.verdict


# The reservation the by-ID write fallback tests target.
BY_ID_TARGET = "res-new"

_PATCH_EVIDENCE = "custom_components.guesty.actions._conflict_evidence"
_PATCH_FETCH = "custom_components.guesty.GuestyApiClient.get_reservations"


def make_by_id_record(
    reservation_id: str = BY_ID_TARGET,
    **overrides: Any,
) -> dict[str, Any]:
    """Create one valid coded raw v3 record for a by-ID read.

    Args:
        reservation_id: The ``reservationId`` value.
        **overrides: Fields to override on the v3 defaults.

    Returns:
        A raw v3 reservation dict carrying a fresh code.
    """
    fields: dict[str, Any] = {"confirmationCode": f"CONF-{reservation_id}"}
    fields.update(overrides)
    return make_v3_reservation(reservation_id, **fields)


@dataclass
class ByIdRoutes:
    """The mocked endpoints of one by-ID write fallback flow.

    Attributes:
        fetch: The patched qualifying refresh search.
        token: The token endpoint.
        read: The by-ID ``GET /reservations-v3`` endpoint.
        notes: The v3 note write endpoint.
        status: The v3 status write endpoint.
        fields: The v3 custom-field write endpoint.
    """

    fetch: AsyncMock
    token: respx.Route
    read: respx.Route
    notes: respx.Route
    status: respx.Route
    fields: respx.Route

    @property
    def writes(self) -> int:
        """Count every write request sent.

        Returns:
            The number of PUT requests across all write endpoints.
        """
        return self.notes.call_count + self.status.call_count + self.fields.call_count


@contextmanager
def by_id_network(
    *,
    refresh: ReservationFetchResult,
    read: Any = None,
    evidence: FakeConflictEvidence | None = None,
    reservation_id: str = BY_ID_TARGET,
    write: Any = None,
) -> Generator[ByIdRoutes]:
    """Mock the refresh, token, by-ID read and v3 write endpoints.

    Args:
        refresh: What the qualifying refresh returns.
        read: The by-ID response, an exception or a side effect;
            one valid coded record when None.
        evidence: The 013 evidence double to install, or None to
            keep the unpatched production adapter.
        reservation_id: The reservation the writes target.
        write: The write response, exception or side effect for
            every write endpoint; HTTP 200 when None.

    Yields:
        The mocked routes.
    """
    with ExitStack() as stack:
        router = stack.enter_context(respx.mock(assert_all_called=False))
        fetch = stack.enter_context(
            patch(_PATCH_FETCH, new_callable=AsyncMock, return_value=refresh)
        )
        if evidence is not None:
            stack.enter_context(patch(_PATCH_EVIDENCE, return_value=evidence))
        token = router.post(FAKE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=make_token_response())
        )
        read_route = router.get(f"{FAKE_BASE_URL}/reservations-v3")
        if read is None:
            read = httpx.Response(
                200, json={"results": [make_by_id_record(reservation_id)]}
            )
        _mock_route(read_route, read)
        routes = []
        for suffix in ("notes", "status", "custom-fields"):
            route = router.put(
                f"{FAKE_BASE_URL}/reservations-v3/{reservation_id}/{suffix}"
            )
            _mock_route(route, httpx.Response(200, json={}) if write is None else write)
            routes.append(route)
        yield ByIdRoutes(fetch, token, read_route, *routes)


@contextmanager
def by_id_not_found() -> Generator[respx.Route]:
    """Mock only the token and an empty by-ID read.

    Used where a feature 012 test's absent or codeless target now
    qualifies for the feature 014 by-ID read (T052): the read finds
    nothing, so the write is still refused, with one read and no
    write. Every write route is mocked so a write would be counted.

    Yields:
        The by-ID read route.
    """
    with respx.mock(assert_all_called=False) as router:
        router.post(FAKE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=make_token_response())
        )
        read = router.get(f"{FAKE_BASE_URL}/reservations-v3").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        writes = router.put(
            url__regex=rf"^{re.escape(FAKE_BASE_URL)}/reservations-v3/.+"
        ).mock(return_value=httpx.Response(200, json={}))
        yield read
        assert writes.call_count == 0


def capture_by_id_retention(
    coordinator: Any, evidence: FakeConflictEvidence | None
) -> dict[str, Any]:
    """Capture every piece of state a by-ID write must leave alone.

    Args:
        coordinator: The entry's coordinator.
        evidence: The 013 double in use, or None for the real 013
            conflict-only evidence, whose entries, generation and
            state are captured instead.

    Returns:
        A comparable copy of the retained state.
    """
    identity = coordinator.identity
    baseline = identity.baseline
    client = coordinator.api_client
    return {
        "data": deepcopy(coordinator.data),
        "current": baseline.current_ids(),
        "maps": deepcopy(
            (
                baseline.codes,
                baseline.legacy_codes,
                baseline.bootstrap_codes,
                baseline.fallback_codes,
                baseline.candidates,
            )
        ),
        "continuity": baseline.continuity,
        "ambiguous": identity._ambiguous,
        "unstored": identity._unstored,
        "session": (
            client.legacy_observed_reservations,
            client.legacy_retired_reservations,
        ),
        "fallback": (coordinator.fallback_served_ids, coordinator.fallback_sourced_ids),
        "absent": client.confirmed_absent_reservations,
        "snapshot": id(coordinator._accepted_snapshot),
        "evidence": _capture_evidence(coordinator, evidence),
    }


def _capture_evidence(
    coordinator: Any, evidence: FakeConflictEvidence | None
) -> object:
    """Capture the 013 evidence a by-ID write must leave alone.

    Args:
        coordinator: The entry's coordinator.
        evidence: The 013 double in use, or None for the real one.

    Returns:
        A comparable copy of the evidence.
    """
    if evidence is not None:
        return deepcopy((evidence.codes, evidence.ids, evidence.is_available))
    real = coordinator.identity.conflict_evidence
    return (deepcopy(real._entries), real.generation, real.state, real.count)


def _mock_route(route: respx.Route, outcome: Any) -> None:
    """Attach a response, exception or side effect to a route.

    Args:
        route: The route to configure.
        outcome: An ``httpx.Response``, or anything respx accepts as
            a side effect.
    """
    if isinstance(outcome, httpx.Response):
        route.mock(return_value=outcome)
    else:
        route.mock(side_effect=outcome)


class FakeTokenStorage:
    """In-memory TokenStorage implementation for tests.

    Implements the TokenStorage protocol without any HA dependencies,
    suitable for unit testing the API layer in isolation.
    """

    def __init__(self) -> None:
        """Initialize with empty state."""
        self._token: CachedToken | None = None
        self._count: int = 0
        self._window: datetime | None = None

    async def load_token(self) -> CachedToken | None:
        """Load the stored token.

        Returns:
            The stored CachedToken, or None if not set.
        """
        return self._token

    async def save_token(self, token: CachedToken) -> None:
        """Store a token.

        Args:
            token: The CachedToken to store.
        """
        self._token = token

    async def load_request_count(self) -> tuple[int, datetime | None]:
        """Load the token request counter.

        Returns:
            Tuple of (count, window_start).
        """
        return (self._count, self._window)

    async def save_request_count(
        self,
        count: int,
        window_start: datetime,
    ) -> None:
        """Store the token request counter.

        Args:
            count: Number of token requests in current window.
            window_start: Start time of the current window.
        """
        self._count = count
        self._window = window_start


# Verify FakeTokenStorage satisfies the protocol at module level
_storage_check: TokenStorage = FakeTokenStorage()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Automatically enable custom integrations for all tests.

    This fixture ensures HA's loader discovers the custom_components/
    directory for integration tests.
    """


@pytest.fixture(autouse=True)
def _auto_mock_cf_definitions(
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """Auto-mock custom field definitions for integration tests.

    Prevents real API calls during setup. Skipped for tests in the
    api/ subdirectory. Tests that need specific definitions can
    override with their own patch.

    Args:
        request: Pytest fixture request for path inspection.

    Yields:
        None after applying the patch.
    """
    # Skip only for API-level tests under tests/api/
    path_parts = request.path.parts
    if "tests" in path_parts:
        idx = path_parts.index("tests")
        if len(path_parts) > idx + 1 and path_parts[idx + 1] == "api":
            yield
            return

    with (
        patch(
            "custom_components.guesty.GuestyApiClient.get_account_id",
            new_callable=AsyncMock,
            return_value="acc-test-auto",
        ),
        patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        yield


@pytest.fixture
def fake_storage() -> FakeTokenStorage:
    """Provide a clean FakeTokenStorage instance.

    Returns:
        A new FakeTokenStorage for the test.
    """
    return FakeTokenStorage()


@pytest.fixture
def mock_token() -> CachedToken:
    """Provide a valid CachedToken for tests.

    Returns:
        A CachedToken with default test values.
    """
    return CachedToken(
        access_token=FAKE_TOKEN,
        token_type="Bearer",
        expires_in=86400,
        scope="open-api",
        issued_at=datetime.now(UTC),
    )


# ── Listing test helpers ────────────────────────────────────────────


def make_listing_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API listing dict with sensible defaults.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching the Guesty API listing response format.
    """
    defaults: dict[str, Any] = {
        "_id": "listing-001",
        "title": "Beach House",
        "nickname": "beach",
        "listed": True,
        "active": True,
        "address": {
            "full": "123 Ocean Dr, Miami, FL 33139, US",
            "street": "123 Ocean Dr",
            "city": "Miami",
            "state": "FL",
            "zipcode": "33139",
            "country": "US",
        },
        "propertyType": "apartment",
        "roomType": "entire_home",
        "type": "SINGLE",
        "bedrooms": 2,
        "bathrooms": 1.5,
        "accommodates": 5,
        "timezone": "America/New_York",
        "defaultCheckInTime": "15:00",
        "defaultCheckOutTime": "11:00",
        "tags": ["premium", "beachfront"],
        "customFields": [
            {"fieldId": "cf_region", "value": "southeast"},
        ],
    }
    defaults.update(overrides)
    return defaults


def make_listings_page_response(
    listings: list[dict[str, Any]],
    count: int,
    limit: int = 100,
    skip: int = 0,
) -> dict[str, Any]:
    """Create a Guesty API listings page response dict.

    Args:
        listings: List of listing dictionaries.
        count: Total count reported by API.
        limit: Page size.
        skip: Offset.

    Returns:
        Dictionary matching the Guesty listings endpoint response.
    """
    return {
        "results": listings,
        "count": count,
        "limit": limit,
        "skip": skip,
    }


def make_listing_dict_with(
    listing_id: str = "listing-001",
    title: str = "Beach House",
    nickname: str | None = "beach",
    tags: list[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Create a Guesty listing API response dict for testing.

    Convenience wrapper around :func:`make_listing_dict` that exposes
    commonly varied fields as keyword arguments for readability in
    multi-listing test setups.

    Args:
        listing_id: The Guesty listing ID (``_id`` field).
        title: Human-readable listing title.
        nickname: Optional listing nickname.
        tags: Listing tags; defaults to ``["premium", "beachfront"]``.
        **overrides: Additional API-level fields to override.

    Returns:
        Dictionary matching the Guesty API listing response format.
    """
    if tags is None:
        tags = ["premium", "beachfront"]
    return make_listing_dict(
        _id=listing_id,
        title=title,
        nickname=nickname,
        tags=tags,
        **overrides,
    )


# Pre-built multi-listing data for filtering tests.
MULTI_LISTING_DICTS: list[dict[str, Any]] = [
    make_listing_dict_with(
        listing_id="lst_miami_beach",
        title="Miami Beach Villa",
        nickname="miami-villa",
        tags=["miami-beach", "premium"],
        address={
            "full": "100 Collins Ave, Miami Beach, FL 33139, US",
            "street": "100 Collins Ave",
            "city": "Miami Beach",
            "state": "FL",
            "zipcode": "33139",
            "country": "US",
        },
        bedrooms=3,
        bathrooms=2.0,
        accommodates=6,
    ),
    make_listing_dict_with(
        listing_id="lst_downtown_orlando",
        title="Downtown Orlando Loft",
        nickname="orlando-loft",
        tags=["downtown-orlando"],
        address={
            "full": "200 Orange Ave, Orlando, FL 32801, US",
            "street": "200 Orange Ave",
            "city": "Orlando",
            "state": "FL",
            "zipcode": "32801",
            "country": "US",
        },
        propertyType="house",
        bedrooms=1,
        bathrooms=1.0,
        accommodates=2,
        listingType="SINGLE",
        defaultCheckInTime="16:00",
        defaultCheckOutTime="10:00",
        timezone="America/New_York",
    ),
    make_listing_dict_with(
        listing_id="lst_tampa_bay",
        title="Tampa Bay Cottage",
        nickname="tampa-cottage",
        tags=["tampa-bay", "premium"],
        address={
            "full": "300 Bayshore Blvd, Tampa, FL 33606, US",
            "street": "300 Bayshore Blvd",
            "city": "Tampa",
            "state": "FL",
            "zipcode": "33606",
            "country": "US",
        },
        bedrooms=2,
        bathrooms=1.5,
        accommodates=4,
    ),
    make_listing_dict_with(
        listing_id="lst_miami_downtown",
        title="Miami Downtown Studio",
        nickname="miami-studio",
        tags=["miami-beach", "downtown-orlando"],
        address={
            "full": "400 Brickell Ave, Miami, FL 33131, US",
            "street": "400 Brickell Ave",
            "city": "Miami",
            "state": "FL",
            "zipcode": "33131",
            "country": "US",
        },
        propertyType="apartment",
        bedrooms=0,
        bathrooms=1.0,
        accommodates=2,
        listingType="SINGLE",
        defaultCheckInTime="14:00",
        defaultCheckOutTime="11:00",
        timezone="America/New_York",
    ),
    make_listing_dict_with(
        listing_id="lst_no_tags",
        title="Untagged Retreat",
        nickname="untagged",
        tags=[],
        address={
            "full": "500 Palm St, Key West, FL 33040, US",
            "street": "500 Palm St",
            "city": "Key West",
            "state": "FL",
            "zipcode": "33040",
            "country": "US",
        },
        bedrooms=1,
        bathrooms=1.0,
        accommodates=3,
    ),
]


@pytest.fixture
def multi_listing_dicts() -> list[dict[str, Any]]:
    """Provide 5 distinct Guesty API listing dicts for filtering tests.

    The listings cover:
      - ``lst_miami_beach``: tags ``miami-beach``, ``premium``
      - ``lst_downtown_orlando``: tag ``downtown-orlando``
      - ``lst_tampa_bay``: tags ``tampa-bay``, ``premium``
      - ``lst_miami_downtown``: tags ``miami-beach``,
        ``downtown-orlando``
      - ``lst_no_tags``: no tags

    Returns:
        A list of five listing API response dicts.
    """
    return [deepcopy(d) for d in MULTI_LISTING_DICTS]


@pytest.fixture
def sample_listing() -> GuestyListing:
    """Provide a valid GuestyListing instance for tests.

    Returns:
        A GuestyListing with default test values.
    """
    return GuestyListing(
        id="listing-001",
        title="Beach House",
        nickname="beach",
        status="active",
        address=GuestyAddress(
            full="123 Ocean Dr, Miami, FL 33139, US",
            street="123 Ocean Dr",
            city="Miami",
            state="FL",
            zipcode="33139",
            country="US",
        ),
        property_type="apartment",
        room_type="entire_home",
        listing_type="SINGLE",
        bedrooms=2,
        bathrooms=1.5,
        accommodates=5,
        timezone="America/New_York",
        check_in_time="15:00",
        check_out_time="11:00",
        tags=("premium", "beachfront"),
        custom_fields=MappingProxyType({"cf_region": "southeast"}),
    )


@pytest.fixture
def mock_coordinator(
    hass: HomeAssistant,
    sample_listing: GuestyListing,
) -> AsyncMock:
    """Provide a mocked ListingsCoordinator with sample data.

    Args:
        hass: Home Assistant instance.
        sample_listing: The sample listing for coordinator data.

    Returns:
        An AsyncMock mimicking ListingsCoordinator.
    """
    coordinator = AsyncMock()
    coordinator.data = {sample_listing.id: sample_listing}
    coordinator.hass = hass
    coordinator.config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Guesty (test)",
        data={
            CONF_CLIENT_ID: FAKE_CLIENT_ID,
            CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
        },
        unique_id=FAKE_CLIENT_ID,
    )
    return coordinator


# ── Reservation test helpers ────────────────────────────────────────


def make_guest_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API guest dict with sensible defaults.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching the Guesty API guest object format.
    """
    defaults: dict[str, Any] = {
        "fullName": "John Doe",
        "phone": "+15551234567",
        "email": "john@example.com",
        "_id": "guest-001",
    }
    defaults.update(overrides)
    return defaults


def make_money_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API money dict with sensible defaults.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching the Guesty API money object format.
    """
    defaults: dict[str, Any] = {
        "totalPaid": 500.00,
        "balanceDue": 100.00,
        "currency": "USD",
    }
    defaults.update(overrides)
    return defaults


def make_reservation_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API reservation dict with sensible defaults.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching the Guesty API reservation response.
    """
    defaults: dict[str, Any] = {
        "_id": "res-001",
        "listingId": "listing-001",
        "status": "confirmed",
        "checkIn": "2025-08-01T15:00:00+00:00",
        "checkOut": "2025-08-05T11:00:00+00:00",
        "confirmationCode": "CONF-ABC",
        "checkInDateLocalized": "2025-08-01",
        "checkOutDateLocalized": "2025-08-05",
        "plannedArrival": "15:00",
        "plannedDeparture": "11:00",
        "nightsCount": 4,
        "guestsCount": 2,
        "source": "airbnb",
        "note": "Late arrival",
        "guest": make_guest_dict(),
        "money": make_money_dict(),
    }
    defaults.update(overrides)
    return defaults


def make_v3_segment(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty v3 stay segment dict with defaults.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching a ``stay[]`` entry of the v3 search.
    """
    defaults: dict[str, Any] = {
        "listingId": "listing-001",
        "checkIn": "2025-08-01T15:00:00+00:00",
        "checkOut": "2025-08-05T11:00:00+00:00",
        "checkInDateLocalized": "2025-08-01",
        "checkOutDateLocalized": "2025-08-05",
        "guestsCount": 2,
    }
    defaults.update(overrides)
    return defaults


def make_v3_reservation(
    reservation_id: str = "res-001",
    *,
    status: str = "confirmed",
    segments: list[dict[str, Any]] | None = None,
    guest_id: str | None = "guest-001",
    **overrides: Any,
) -> dict[str, Any]:
    """Create a Guesty v3 reservation dict with defaults.

    ``reservation_id`` values must be unique and sortable because the
    v3 page guard requires strictly ascending identifiers.

    Args:
        reservation_id: The ``reservationId`` value.
        status: The v3 status value.
        segments: The ``stay`` segments; one default segment if None.
        guest_id: The ``guestId`` value, omitted when None.
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching a v3 search ``results[]`` entry.
    """
    stay = segments if segments is not None else [make_v3_segment()]
    first = stay[0] if stay else {}
    last = stay[-1] if stay else {}
    defaults: dict[str, Any] = {
        "reservationId": reservation_id,
        "status": status,
        "confirmationCode": "CONF-ABC",
        "source": "airbnb",
        "checkIn": first.get("checkIn"),
        "checkOut": last.get("checkOut"),
        "checkInDateLocalized": first.get("checkInDateLocalized"),
        "checkOutDateLocalized": last.get("checkOutDateLocalized"),
        "plannedArrival": "15:00",
        "plannedDeparture": "11:00",
        "notes": {"other": "Late arrival"},
        "stay": stay,
    }
    if guest_id is not None:
        defaults["guestId"] = guest_id
    defaults.update(overrides)
    return defaults


def make_v3_search_page(
    results: list[dict[str, Any]],
    *,
    skip: int = 0,
    limit: int = 100,
    has_more: bool = False,
) -> dict[str, Any]:
    """Create a Guesty v3 search page response dict.

    Args:
        results: The reservation dicts for this page.
        skip: The pagination offset echoed by the server.
        limit: The pagination page size echoed by the server.
        has_more: Whether a further page follows.

    Returns:
        Dictionary matching the v3 search response envelope.
    """
    return {
        "results": results,
        "pagination": {
            "skip": skip,
            "limit": limit,
            "hasMore": has_more,
        },
    }


def make_legacy_reservation_by_id(
    reservation_id: str = "res-001",
    **overrides: Any,
) -> dict[str, Any]:
    """Build the legacy by-id answer for one reservation.

    ``GET /v1/reservations/{reservationId}`` answers with the
    reservation document itself rather than a paginated envelope,
    so this is deliberately not wrapped in ``results``. The payload
    is synthetic, like every other builder here (NFR-008).

    Args:
        reservation_id: The ``_id`` the document reports.
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching a legacy reservation document.
    """
    document = make_reservation_dict(_id=reservation_id)
    document.update(overrides)
    return document


def make_legacy_not_found_body(
    reservation_id: str = "res-001",
) -> dict[str, Any]:
    """Build a reservation-scoped legacy not-found body.

    This is the shape Guesty Support described on issue #223: the
    error names the reservation, which is what lets the fallback
    read it as confirmed absence rather than as retirement.

    Args:
        reservation_id: The reservation the error names.

    Returns:
        Dictionary matching a reservation-scoped 404 body.
    """
    return {
        "statusCode": 404,
        "error": "Not Found",
        "message": f"Reservation {reservation_id} not found",
    }


def make_legacy_retired_body() -> dict[str, Any]:
    """Build a retirement-shaped legacy answer body.

    Retirement is about the resource rather than one reservation,
    so the body names no reservation at all.

    Returns:
        Dictionary matching a retirement-shaped error body.
    """
    return {
        "statusCode": 410,
        "error": "Gone",
        "message": ("This endpoint has been deprecated and is no longer available"),
    }


def _relative_day(days: int, hour: int) -> datetime:
    """Build a datetime offset from now, so fixtures stay in window.

    The coordinator trims reservations outside the configured
    instant window, so fixture dates must track the clock.

    Args:
        days: Day offset from today.
        hour: Hour of day, UTC.

    Returns:
        The offset datetime, in UTC.
    """
    return (datetime.now(UTC) + timedelta(days=days)).replace(
        hour=hour,
        minute=0,
        second=0,
        microsecond=0,
    )


@pytest.fixture
def sample_reservation() -> GuestyReservation:
    """Provide a valid GuestyReservation instance for tests.

    Returns:
        A GuestyReservation with default test values.
    """
    return GuestyReservation(
        id="res-001",
        listing_id="listing-001",
        status="confirmed",
        check_in=_relative_day(0, 15),
        check_out=_relative_day(4, 11),
        confirmation_code="CONF-ABC",
        check_in_local="2025-08-01",
        check_out_local="2025-08-05",
        planned_arrival="15:00",
        planned_departure="11:00",
        nights_count=4,
        guests_count=2,
        source="airbnb",
        note="Late arrival",
        guest=GuestyGuest(
            full_name="John Doe",
            phone="+15551234567",
            email="john@example.com",
            guest_id="guest-001",
        ),
        money=GuestyMoney(
            total_paid=500.00,
            balance_due=100.00,
            currency="USD",
        ),
    )


@pytest.fixture
def mock_reservations_coordinator(
    hass: HomeAssistant,
    sample_listing: GuestyListing,
    sample_reservation: GuestyReservation,
) -> AsyncMock:
    """Provide a mocked ReservationsCoordinator with sample data.

    Args:
        hass: Home Assistant instance.
        sample_listing: The sample listing for coordinator data.
        sample_reservation: The sample reservation for data.

    Returns:
        An AsyncMock mimicking ReservationsCoordinator.
    """
    coordinator = AsyncMock()
    coordinator.data = {
        sample_listing.id: [sample_reservation],
    }
    coordinator.hass = hass
    coordinator.config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Guesty (test)",
        data={
            CONF_CLIENT_ID: FAKE_CLIENT_ID,
            CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
        },
        unique_id=FAKE_CLIENT_ID,
    )
    coordinator.last_update_success = True
    return coordinator


# ── Custom field test helpers ───────────────────────────────────────


def make_custom_field_definition_dict(
    **overrides: Any,
) -> dict[str, Any]:
    """Create a Guesty API custom field definition dict.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching the Guesty custom field definition format.
    """
    defaults: dict[str, Any] = {
        "fieldId": "cf-door-code",
        "key": "Door Code",
        "type": "text",
        "object": "reservation",
        "displayName": "door_code",
        "isPublic": False,
        "isRequired": False,
        "options": [],
    }
    defaults.update(overrides)
    return defaults


def sample_custom_field_definitions() -> list[GuestyCustomFieldDefinition]:
    """Provide a list of sample custom field definitions.

    Returns:
        List with listing, reservation, and both-target fields.
    """
    return [
        GuestyCustomFieldDefinition(
            field_id="cf-region",
            name="Region",
            field_type="text",
            applicable_to=frozenset({"listing"}),
            display_name="region",
            is_public=False,
            is_required=False,
            options=(),
        ),
        GuestyCustomFieldDefinition(
            field_id="cf-door-code",
            name="Door Code",
            field_type="text",
            applicable_to=frozenset({"reservation"}),
            display_name="door_code",
            is_public=False,
            is_required=False,
            options=(),
        ),
        GuestyCustomFieldDefinition(
            field_id="cf-priority",
            name="Priority",
            field_type="number",
            applicable_to=frozenset({"listing", "reservation"}),
            display_name="priority",
            is_public=True,
            is_required=False,
            options=(),
        ),
    ]


@pytest.fixture
def mock_cf_definitions() -> list[GuestyCustomFieldDefinition]:
    """Provide sample custom field definitions.

    Returns:
        List of sample GuestyCustomFieldDefinition instances.
    """
    return sample_custom_field_definitions()


@pytest.fixture
def mock_cf_coordinator(
    hass: HomeAssistant,
) -> AsyncMock:
    """Provide a mocked CustomFieldsDefinitionCoordinator.

    Args:
        hass: Home Assistant instance.

    Returns:
        An AsyncMock mimicking CustomFieldsDefinitionCoordinator.
    """
    coordinator = AsyncMock()
    coordinator.data = sample_custom_field_definitions()
    coordinator.hass = hass
    coordinator.config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Guesty (test)",
        data={
            CONF_CLIENT_ID: FAKE_CLIENT_ID,
            CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
        },
        unique_id=FAKE_CLIENT_ID,
    )
    coordinator.last_update_success = True

    def _get_field(
        field_id: str,
    ) -> GuestyCustomFieldDefinition | None:
        """Look up a field by ID from mock data."""
        data: list[GuestyCustomFieldDefinition] = coordinator.data
        for f in data:
            if f.field_id == field_id:
                return f
        return None

    def _get_fields_for_target(
        target_type: str,
    ) -> list[GuestyCustomFieldDefinition]:
        """Filter fields by target type from mock data."""
        data: list[GuestyCustomFieldDefinition] = coordinator.data
        return [f for f in data if target_type in f.applicable_to]

    coordinator.get_field = _get_field
    coordinator.get_fields_for_target = _get_fields_for_target
    return coordinator


@pytest.fixture
def mock_cf_client() -> AsyncMock:
    """Provide a mock GuestyCustomFieldsClient.

    Returns:
        An AsyncMock configured as a GuestyCustomFieldsClient.
    """
    client = AsyncMock()
    client.get_definitions = AsyncMock(
        return_value=sample_custom_field_definitions(),
    )
    client.set_field = AsyncMock()
    client.validate_value = lambda v, t: None
    return client


# ── Action test helpers ─────────────────────────────────────────────


@pytest.fixture
def mock_actions_client() -> AsyncMock:
    """Provide a mock GuestyActionsClient for action tests.

    Returns:
        An AsyncMock configured as a GuestyActionsClient stand-in.
    """
    from custom_components.guesty.api.models import ActionResult

    client = AsyncMock()
    client.add_reservation_note = AsyncMock(
        return_value=ActionResult(success=True, target_id="res-001"),
    )
    client.set_listing_status = AsyncMock(
        return_value=ActionResult(success=True, target_id="listing-001"),
    )
    client.create_task = AsyncMock(
        return_value=ActionResult(success=True, target_id="task-001"),
    )
    client.set_calendar_availability = AsyncMock(
        return_value=ActionResult(
            success=True,
            target_id="listing-001",
        ),
    )
    client.set_reservation_status = AsyncMock(
        return_value=ActionResult(
            success=True,
            target_id="res-001",
        ),
    )
    return client

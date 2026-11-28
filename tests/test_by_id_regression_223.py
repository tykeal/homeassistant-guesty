# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""End-to-end by-ID write fallback through the real v3 source (T052).

These tests drive the synthetic issue #223 scenario through every
real layer: Home Assistant service handlers, the reservations
coordinator, the real v3 search source (``_map_results``, its
returned and rejected identifiers, fallback provenance), the real
feature 013 conflict-only evidence behind the production adapter,
and the real HTTP clients. Only ``httpx`` is mocked, with ``respx``,
so the qualifying refresh and the by-ID read are genuine requests.

Every payload is synthetic (NFR-008). A same-day reservation created
after its scheduled check-in is absent from the v3 search but
present when read by ID; the write must go out exactly once, and
every unproven variant must be refused without any write.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.const import (
    BASE_URL,
    RESERVATIONS_V3_ENDPOINT,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    TOKEN_URL,
)
from custom_components.guesty.api.models import GuestyListing, RejectedCode
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
    SERVICE_SET_CUSTOM_FIELD,
)
from custom_components.guesty.identity import (
    EVIDENCE_READY,
    REFUSAL_NO_CODE,
)
from tests.conftest import (
    make_legacy_not_found_body,
    make_token_response,
    make_v3_reservation,
    make_v3_search_page,
    make_v3_segment,
    sample_custom_field_definitions,
)

# ── Synthetic scenario ──────────────────────────────────────────────

_NOW = "2026-11-23 18:00:00+00:00"
_TARGET = "res-new"
_TARGET_CODE = "SENTINEL-BYID-CODE-7Q"
_PIN = "SENTINEL-PIN-4821"
_GUEST = "Sentinel Guestname"
_NOTE = "SENTINEL-NOTE-TEXT"
_SECRETS = (_TARGET_CODE, _PIN, _GUEST, _NOTE)
_LISTING = "listing-001"
_UNIT_A = "unit-a"
_UNIT_B = "unit-b"
_KNOWN = ("res-001", "res-002")
_ACTIONS_LOGGER = "custom_components.guesty.actions"
_HARNESS_LOGGER = "pytest_homeassistant_custom_component"

_Answer = Callable[[httpx.Request], httpx.Response]

_SEARCH_URL = f"{BASE_URL}{RESERVATIONS_V3_SEARCH_ENDPOINT}"
_BY_ID_URL = f"{BASE_URL}{RESERVATIONS_V3_ENDPOINT}"


def _segment(
    listing_id: str = _LISTING,
    parent: str | None = None,
    *,
    check_in: str = "2026-11-23",
    check_out: str = "2026-11-25",
) -> dict[str, Any]:
    """Build one same-day stay segment.

    Args:
        listing_id: The segment's listing (unit) identifier.
        parent: The multi-unit parent listing, if any.
        check_in: Local check-in date.
        check_out: Local check-out date.

    Returns:
        A raw v3 ``stay[]`` entry.
    """
    fields: dict[str, Any] = {
        "listingId": listing_id,
        "checkIn": f"{check_in}T15:00:00+00:00",
        "checkOut": f"{check_out}T11:00:00+00:00",
        "checkInDateLocalized": check_in,
        "checkOutDateLocalized": check_out,
    }
    if parent is not None:
        fields["parentListingId"] = parent
    return make_v3_segment(**fields)


def _known(reservation_id: str) -> dict[str, Any]:
    """Build one coded, in-window reservation the search returns.

    Args:
        reservation_id: Its identifier.

    Returns:
        The raw v3 search record.
    """
    return make_v3_reservation(
        reservation_id,
        segments=[_segment(check_in="2026-11-22", check_out="2026-11-24")],
        guest_id=None,
        confirmationCode=f"CONF-{reservation_id}",
    )


def _target(**overrides: Any) -> dict[str, Any]:
    """Build the #223 reservation as a by-ID read returns it.

    It was created at 17:30 UTC, after its 15:00 UTC scheduled
    check-in the same day, and carries guest data that must never be
    logged.

    Args:
        **overrides: Fields to override.

    Returns:
        The raw v3 record.
    """
    fields: dict[str, Any] = {
        "segments": [_segment()],
        "guest_id": None,
        "confirmationCode": _TARGET_CODE,
        "createdAt": "2026-11-23T17:30:00+00:00",
        "guest": {"fullName": _GUEST, "phone": "+1-555-0100"},
        "guestName": _GUEST,
    }
    fields.update(overrides)
    return make_v3_reservation(_TARGET, **fields)


def _found(*records: dict[str, Any], **envelope: Any) -> httpx.Response:
    """Build a by-ID response.

    Args:
        *records: The records it returns.
        **envelope: Extra envelope fields, such as ``pagination``.

    Returns:
        The response.
    """
    return httpx.Response(200, json={"results": list(records), **envelope})


# ── Harness ─────────────────────────────────────────────────────────


@dataclass
class _Network:
    """The respx routes and mutable answers of one scenario.

    Attributes:
        search_results: What the next v3 search page returns.
        by_id: The by-ID answer: a response or a side effect.
        legacy: The legacy by-ID answer: a response or side effect.
        token: The token route.
        search: The v3 search route.
        read: The v3 by-ID read route.
        legacy_read: The legacy by-ID route.
        notes: The v3 note write route.
        status: The v3 status write route.
        fields: The v3 custom-field write route.
    """

    search_results: list[dict[str, Any]]
    by_id: Any = None
    legacy: Any = None
    token: respx.Route = field(init=False)
    search: respx.Route = field(init=False)
    read: respx.Route = field(init=False)
    legacy_read: respx.Route = field(init=False)
    notes: respx.Route = field(init=False)
    status: respx.Route = field(init=False)
    fields: respx.Route = field(init=False)

    @property
    def writes(self) -> int:
        """Count every v3 write request.

        Returns:
            The number of PUT requests.
        """
        return self.notes.call_count + self.status.call_count + self.fields.call_count

    def install(self, router: respx.MockRouter) -> None:
        """Register every route on a router.

        Args:
            router: The active respx router.
        """
        self.token = router.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json=make_token_response())
        )
        self.search = router.get(_SEARCH_URL).mock(side_effect=self._search)
        self.read = router.get(_BY_ID_URL).mock(side_effect=self._read)
        self.legacy_read = router.get(
            url__regex=rf"^{BASE_URL}/reservations/[^/]+$"
        ).mock(side_effect=self._legacy)
        self.notes, self.status, self.fields = (
            router.put(f"{_BY_ID_URL}/{_TARGET}/{suffix}").mock(
                return_value=httpx.Response(200, json={})
            )
            for suffix in ("notes", "status", "custom-fields")
        )

    def _search(self, request: httpx.Request) -> httpx.Response:
        """Answer one v3 search page.

        Args:
            request: The search request.

        Returns:
            One complete, ascending page.
        """
        results = sorted(self.search_results, key=lambda r: r["reservationId"])
        return httpx.Response(200, json=make_v3_search_page(results))

    def _read(self, request: httpx.Request) -> httpx.Response:
        """Answer the by-ID read.

        Args:
            request: The by-ID request.

        Returns:
            The configured answer.
        """
        if callable(self.by_id):
            answer: httpx.Response = self.by_id(request)
            return answer
        if isinstance(self.by_id, httpx.Response):
            return self.by_id
        return _found(_target())

    def _legacy(self, request: httpx.Request) -> httpx.Response:
        """Answer a legacy by-ID lookup.

        Args:
            request: The legacy request.

        Returns:
            The configured answer, a server error by default.
        """
        if isinstance(self.legacy, httpx.Response):
            return self.legacy
        return httpx.Response(500, json={"error": "unavailable"})


@dataclass
class _Scenario:
    """A loaded entry over the real v3 source.

    Attributes:
        hass: Home Assistant instance.
        entry: The loaded entry.
        net: The mocked network.
    """

    hass: HomeAssistant
    entry: MockConfigEntry
    net: _Network

    @property
    def coordinator(self) -> Any:
        """Return the entry's reservations coordinator.

        Returns:
            The coordinator.
        """
        return self.hass.data[DOMAIN][self.entry.entry_id]["reservations_coordinator"]

    async def call(self, service: str, **fields: Any) -> Any:
        """Call one reservation write service on the target.

        Args:
            service: The service name.
            **fields: Service fields beyond the target.

        Returns:
            The service response.
        """
        payload: dict[str, Any]
        if service == SERVICE_SET_CUSTOM_FIELD:
            payload = {
                "target_type": "reservation",
                "target_id": _TARGET,
                "field_id": "cf-door-code",
                "value": _PIN,
            }
        else:
            payload = {"reservation_id": _TARGET}
        payload.update(fields)
        return await self.hass.services.async_call(
            DOMAIN, service, payload, blocking=True, return_response=True
        )

    async def refused(self, service: str = SERVICE_SET_CUSTOM_FIELD) -> str:
        """Call a write expected to be refused.

        Args:
            service: The service name.

        Returns:
            The surfaced error text, checked to carry no code, PIN,
            note or guest data.
        """
        with pytest.raises(HomeAssistantError) as error:
            await self.call(service, **_SERVICE_FIELDS[service])
        assert self.net.writes == 0
        text = str(error.value)
        for secret in _SECRETS:
            assert secret not in text
        return text


_SERVICE_FIELDS: dict[str, dict[str, Any]] = {
    SERVICE_SET_CUSTOM_FIELD: {},
    "add_reservation_note": {"note_text": _NOTE},
    "set_reservation_status": {"status": "checked_in"},
}


def _listing(listing_id: str, parent: str | None = None) -> GuestyListing:
    """Build one tracked UTC listing.

    Args:
        listing_id: The listing identifier.
        parent: Its multi-unit parent, if any.

    Returns:
        The listing.
    """
    raw: dict[str, Any] = {
        "_id": listing_id,
        "title": f"Synthetic {listing_id}",
        "timezone": "UTC",
        "defaultCheckInTime": "15:00",
        "defaultCheckOutTime": "11:00",
    }
    if parent is not None:
        raw["parentId"] = parent
    listing = GuestyListing.from_api_dict(raw)
    assert listing is not None
    return listing


@pytest.fixture
async def scenario(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> AsyncGenerator[_Scenario]:
    """Load an entry whose setup refresh runs the real v3 search.

    Only the connection test, the listings and the custom-field
    definitions are patched; every reservation request is HTTP.

    Args:
        hass: Home Assistant instance.
        freezer: The frozen clock.

    Yields:
        The loaded scenario.
    """
    freezer.move_to(_NOW)
    net = _Network(search_results=[_known(rid) for rid in _KNOWN])
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Guesty (synthetic #223)",
        data={CONF_CLIENT_ID: "test-client-id", CONF_CLIENT_SECRET: "test-secret"},
        unique_id="test-client-id",
    )
    entry.add_to_hass(hass)
    listings = [
        _listing(_LISTING),
        _listing(_UNIT_A, _LISTING),
        _listing(_UNIT_B, _LISTING),
    ]
    with (
        respx.mock(assert_all_mocked=True, assert_all_called=False) as router,
        patch(
            "custom_components.guesty.GuestyApiClient.test_connection",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "custom_components.guesty.GuestyApiClient.get_listings",
            new_callable=AsyncMock,
            return_value=listings,
        ),
        patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=sample_custom_field_definitions(),
        ),
    ):
        net.install(router)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        loaded = _Scenario(hass, entry, net)
        coordinator = loaded.coordinator
        assert coordinator.last_update_success
        assert coordinator.identity.conflict_evidence.state == EVIDENCE_READY
        assert net.search.call_count == 1
        yield loaded
        assert net.legacy_read.call_count == 0 or net.legacy is not None
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


def _assert_private(caplog: pytest.LogCaptureFixture) -> None:
    """Assert no code, PIN, note or guest data reached any log.

    The test harness's own storage mock, which echoes what feature
    013 deliberately persists, is not integration logging and is
    excluded.

    Args:
        caplog: The log capture, at DEBUG for every logger.
    """
    text = "\n".join(
        record.getMessage()
        for record in caplog.records
        if not record.name.startswith(_HARNESS_LOGGER)
    )
    assert text
    for secret in _SECRETS:
        assert secret not in text


def _written(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Collect the by-ID written INFO lines.

    Args:
        caplog: The log capture.

    Returns:
        The formatted messages.
    """
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == _ACTIONS_LOGGER
        and record.levelno == logging.INFO
        and "found by ID" in record.getMessage()
    ]


def _not_retained(scenario: _Scenario) -> None:
    """Assert nothing learned by ID was published or kept.

    Args:
        scenario: The scenario.
    """
    coordinator = scenario.coordinator
    published = {
        reservation.id
        for reservations in (coordinator.data or {}).values()
        for reservation in reservations
    }
    assert _TARGET not in published
    baseline = coordinator.identity.baseline
    for mapping in (
        baseline.codes,
        baseline.legacy_codes or {},
        baseline.bootstrap_codes or {},
        baseline.fallback_codes or {},
    ):
        assert _TARGET_CODE not in mapping
        assert _TARGET not in mapping.values()
    evidence = coordinator.identity.conflict_evidence
    assert evidence.ids_for(_TARGET_CODE) == frozenset()
    snapshot = coordinator.accepted_refresh_snapshot()
    assert snapshot is not None
    assert _TARGET_CODE not in snapshot.observed_codes


# ── The #223 regression ─────────────────────────────────────────────


class TestIssue223Regression:
    """The reported scenario is written once, through every real layer."""

    @pytest.mark.parametrize(
        "service",
        [SERVICE_SET_CUSTOM_FIELD, "add_reservation_note", "set_reservation_status"],
    )
    async def test_same_day_reservation_absent_from_search_is_written_by_id(
        self,
        scenario: _Scenario,
        caplog: pytest.LogCaptureFixture,
        service: str,
    ) -> None:
        """Exactly one by-ID read and one v3 write; nothing retained."""
        caplog.set_level(logging.DEBUG)
        snapshot = scenario.coordinator.accepted_refresh_snapshot()
        assert snapshot is not None
        assert _TARGET not in snapshot.returned_ids
        assert _TARGET not in snapshot.rejected_ids
        tokens = scenario.net.token.call_count

        result = await scenario.call(service, **_SERVICE_FIELDS[service])

        net = scenario.net
        assert net.token.call_count == tokens
        assert net.search.call_count == 2
        assert net.read.call_count == 1
        assert net.read.calls[0].request.url.params["reservationIds"] == _TARGET
        assert net.legacy_read.call_count == 0
        assert net.writes == 1
        if service == SERVICE_SET_CUSTOM_FIELD:
            assert result is not None
            assert result["result"] == "success"
            assert net.fields.call_count == 1
            body = json.loads(net.fields.calls[0].request.content)
            assert _PIN in json.dumps(body)
        elif service == "add_reservation_note":
            assert result == {"success": True, "target_id": _TARGET}
            assert net.notes.call_count == 1
        else:
            assert result == {"success": True, "target_id": _TARGET}
            assert net.status.call_count == 1
        lines = _written(caplog)
        assert len(lines) == 1
        assert "absent" in lines[0]
        _not_retained(scenario)
        _assert_private(caplog)

    async def test_multi_unit_codeless_target_is_written_by_id(
        self, scenario: _Scenario, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A valid multi-unit code-less record matches its by-ID listings.

        The search returns the target with both unit segments and no
        code; the by-ID read returns the same segments with a code.
        The listing-pair check must accept it rather than refuse.
        """
        stay = [
            _segment(_UNIT_A, _LISTING),
            _segment(_UNIT_B, _LISTING, check_in="2026-11-24", check_out="2026-11-26"),
        ]
        searched = _target(segments=stay)
        del searched["confirmationCode"]
        scenario.net.search_results.append(searched)
        scenario.net.by_id = _found(_target(segments=stay))
        await scenario.coordinator.async_refresh()
        snapshot = scenario.coordinator.accepted_refresh_snapshot()
        assert snapshot is not None
        assert _TARGET in snapshot.returned_ids
        assert snapshot.records[_TARGET].listing_ids == frozenset(
            {(_UNIT_A, _LISTING), (_UNIT_B, _LISTING)}
        )
        caplog.set_level(logging.DEBUG)
        tokens = scenario.net.token.call_count

        result = await scenario.call(SERVICE_SET_CUSTOM_FIELD)

        assert result is not None
        assert result["result"] == "success"
        assert scenario.net.token.call_count == tokens
        assert scenario.net.search.call_count == 3
        assert scenario.net.legacy_read.call_count == 0
        assert scenario.net.read.call_count == 1
        assert scenario.net.fields.call_count == 1
        assert scenario.net.writes == 1
        lines = _written(caplog)
        assert len(lines) == 1
        assert "codeless" in lines[0]
        _assert_private(caplog)


def _partial(reservation_id: str, code: str) -> dict[str, Any]:
    """Build a partially valid search record carrying a code.

    One segment is valid and one lacks its listing, so feature 013
    rejects the record as partial and records its code as
    conflict-only evidence.

    Args:
        reservation_id: Its identifier.
        code: Its confirmation code.

    Returns:
        The raw v3 search record.
    """
    return make_v3_reservation(
        reservation_id,
        segments=[_segment(), _segment(listing_id="")],
        guest_id=None,
        confirmationCode=code,
    )


def _with_concurrent_evidence(scenario: _Scenario) -> _Answer:
    """Answer the read after a concurrent refresh rejects the target.

    Args:
        scenario: The scenario.

    Returns:
        A by-ID side effect that records conflict-only evidence for
        the target under another code, then returns the record.
    """

    def answer(request: httpx.Request) -> httpx.Response:
        """Record the competing evidence, then answer.

        Args:
            request: The by-ID request.

        Returns:
            The target record.
        """
        evidence = scenario.coordinator.identity.conflict_evidence
        evidence.record(
            [RejectedCode(_TARGET, "CONF-OLD")],
            {},
            "v3",
            datetime.now(UTC),
        )
        assert evidence.available()
        return _found(_target())

    return answer


_READ_REFUSALS = [
    ("not-found", lambda _s: _found(), "not found by ID", "not_found"),
    (
        "mismatched-id",
        lambda _s: _found(_target(reservationId="res-other")),
        "not found by ID",
        "mismatched",
    ),
    (
        "multiple-records",
        lambda _s: _found(_target(), _target()),
        "found but refused",
        "malformed",
    ),
    (
        "has-more",
        lambda _s: _found(_target(), pagination={"hasMore": True}),
        "found but refused",
        "malformed",
    ),
    (
        "hold-status",
        lambda _s: _found(_target(status="alteration")),
        "found but refused",
        "disallowed_status",
    ),
    (
        "no-code",
        lambda _s: _found(_target(confirmationCode=None)),
        "found but refused",
        "no_code",
    ),
    (
        "code-of-another-reservation",
        lambda _s: _found(_target(confirmationCode="CONF-res-001")),
        "found but refused",
        "identity_conflict",
    ),
    (
        "target-under-another-code",
        _with_concurrent_evidence,
        "found but refused",
        "identity_conflict",
    ),
    (
        "server-error",
        lambda _s: httpx.Response(500, json={"error": "unavailable"}),
        "by-ID read failed",
        "(http)",
    ),
    (
        "rate-limited",
        lambda _s: httpx.Response(429, headers={"Retry-After": "1"}),
        "by-ID read failed",
        "rate_limit",
    ),
]


class TestIssue223Refusals:
    """Every unproven variant is refused with no write and no retry."""

    @pytest.mark.parametrize(
        ("answer", "stem", "category"),
        [case[1:] for case in _READ_REFUSALS],
        ids=[case[0] for case in _READ_REFUSALS],
    )
    async def test_read_answer_refused(
        self,
        scenario: _Scenario,
        caplog: pytest.LogCaptureFixture,
        answer: Callable[[_Scenario], Any],
        stem: str,
        category: str,
    ) -> None:
        """The single read decides the refusal; nothing is written."""
        caplog.set_level(logging.DEBUG)
        scenario.net.by_id = answer(scenario)

        text = await scenario.refused()

        assert text.startswith(stem)
        assert category in text
        assert _TARGET in text
        assert scenario.net.read.call_count == 1
        assert scenario.net.search.call_count == 2
        _not_retained(scenario)
        _assert_private(caplog)

    async def test_conflict_only_evidence_for_the_found_code(
        self, scenario: _Scenario, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A code 013 saw on a rejected record refuses the found record."""
        caplog.set_level(logging.DEBUG)
        scenario.net.search_results.append(_partial("res-zzz", _TARGET_CODE))
        await scenario.coordinator.async_refresh()
        evidence = scenario.coordinator.identity.conflict_evidence
        assert evidence.available()
        assert evidence.ids_for(_TARGET_CODE) == frozenset({"res-zzz"})
        caplog.set_level(logging.DEBUG)

        text = await scenario.refused()

        assert text.startswith("found but refused")
        assert "identity_conflict" in text
        assert scenario.net.read.call_count == 1
        _assert_private(caplog)

    @pytest.mark.parametrize("still_rejected", [True, False])
    async def test_rejected_target_is_never_read(
        self,
        scenario: _Scenario,
        caplog: pytest.LogCaptureFixture,
        still_rejected: bool,
    ) -> None:
        """A rejected ID, or conflict-only evidence for it, refuses unread.

        The target first appears as a partially valid record. If the
        qualifying search still rejects it, ``rejected_ids`` names
        it; if it has dropped out, 013's conflict-only evidence still
        does. Either way the 012 refusal stands with no by-ID read.
        """
        caplog.set_level(logging.DEBUG)
        partial = _partial(_TARGET, "CONF-OLD")
        scenario.net.search_results.append(partial)
        await scenario.coordinator.async_refresh()
        evidence = scenario.coordinator.identity.conflict_evidence
        assert evidence.codes_for(_TARGET) == frozenset({"CONF-OLD"})
        if not still_rejected:
            scenario.net.search_results.remove(partial)

        text = await scenario.refused()

        assert text == REFUSAL_NO_CODE.format(reservation_id=_TARGET)
        assert scenario.net.read.call_count == 0
        snapshot = scenario.coordinator.accepted_refresh_snapshot()
        assert snapshot is not None
        assert (_TARGET in snapshot.rejected_ids) is still_rejected
        assert [
            record.args
            for record in caplog.records
            if record.name == _ACTIONS_LOGGER and "not eligible" in str(record.msg)
        ] == [("rejected", _TARGET)]
        _assert_private(caplog)

    async def test_confirmed_absent_target_is_never_read(
        self, scenario: _Scenario, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Legacy-confirmed absence refuses with no by-ID read."""
        caplog.set_level(logging.DEBUG)
        listed = _target(confirmationCode=f"CONF-{_TARGET}")
        scenario.net.search_results.append(listed)
        await scenario.coordinator.async_refresh()
        scenario.net.search_results.remove(listed)
        scenario.net.legacy = httpx.Response(
            404, json=make_legacy_not_found_body(_TARGET)
        )
        await scenario.coordinator.async_refresh()
        client = scenario.coordinator.api_client
        assert _TARGET in client.confirmed_absent_reservations

        text = await scenario.refused()

        assert scenario.net.read.call_count == 0
        assert scenario.net.legacy_read.call_count >= 1
        assert text == REFUSAL_NO_CODE.format(reservation_id=_TARGET)
        assert [
            record.args
            for record in caplog.records
            if record.name == _ACTIONS_LOGGER and "not eligible" in str(record.msg)
        ] == [("confirmed_absent", _TARGET)]
        _assert_private(caplog)

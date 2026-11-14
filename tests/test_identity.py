# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the reservation identity guard (FR-031 to FR-035).

Guesty Support would not confirm that a legacy reservation ``_id``
equals the v3 ``reservationId``. The guard exists so that an
unannounced re-key can never route a write — a door access code,
in practice — to the wrong booking.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty import async_remove_entry
from custom_components.guesty.api.const import (
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
)
from custom_components.guesty.api.models import GuestyListing, GuestyReservation
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
from custom_components.guesty.identity import (
    CONTINUITY_NOT_APPLICABLE,
    CONTINUITY_UNCONFIRMED,
    CONTINUITY_VERIFIED,
    MAX_BASELINE_CODES,
    REFUSAL_UNSTORED,
    STORAGE_VERSION,
    CandidateHint,
    IdentityBaseline,
    IdentityComparison,
    ReservationIdentityGuard,
    _bounded,
    _next_continuity,
    _retained_legacy,
    observe_codes,
    storage_key,
)
from tests.conftest import make_fetch_result, make_listing_dict

CODE_A = "CONF-AAA"
CODE_B = "CONF-BBB"


def _rel(days: int) -> datetime:
    """Build a UTC datetime offset from now by whole days.

    Args:
        days: Day offset from now.

    Returns:
        The offset datetime, in UTC.
    """
    return datetime.now(UTC) + timedelta(days=days)


def _reservation(
    reservation_id: str,
    confirmation_code: str | None = None,
    *,
    listing_id: str = "listing-001",
    status: str = "confirmed",
    check_in_days: int = 1,
    check_out_days: int = 3,
) -> GuestyReservation:
    """Build a reservation fixture for identity tests.

    Args:
        reservation_id: Guesty reservation identifier.
        confirmation_code: Booking confirmation code, if any.
        listing_id: Owning listing identifier.
        status: Reservation lifecycle state.
        check_in_days: Check-in offset from now, in days.
        check_out_days: Check-out offset from now, in days.

    Returns:
        A GuestyReservation carrying only identity-relevant fields.
    """
    return GuestyReservation(
        id=reservation_id,
        listing_id=listing_id,
        status=status,
        check_in=_rel(check_in_days),
        check_out=_rel(check_out_days),
        confirmation_code=confirmation_code,
    )


def _listing(listing_id: str = "listing-001") -> GuestyListing:
    """Build a minimal tracked listing.

    Args:
        listing_id: Listing identifier.

    Returns:
        A GuestyListing the coordinator will treat as tracked.
    """
    listing = GuestyListing.from_api_dict(make_listing_dict(_id=listing_id))
    assert listing is not None
    return listing


def _make_entry(
    *,
    reservations_api: str = RESERVATIONS_API_V3,
    **overrides: Any,
) -> MockConfigEntry:
    """Create a MockConfigEntry with test defaults.

    Args:
        reservations_api: Reservation path the entry selects.
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
        options={CONF_RESERVATIONS_API: reservations_api},
        unique_id="test-client-id",
        **overrides,
    )


def _make_coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    api_client: AsyncMock,
    listing_ids: tuple[str, ...] = ("listing-001",),
) -> ReservationsCoordinator:
    """Build a reservations coordinator over a stub listings source.

    Args:
        hass: Home Assistant instance.
        entry: The config entry under test.
        api_client: Mocked API client.
        listing_ids: Listing identifiers to treat as tracked.

    Returns:
        A ReservationsCoordinator ready for a refresh.
    """
    listings = ListingsCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
    )
    listings.data = {lid: _listing(lid) for lid in listing_ids}
    return ReservationsCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
        listings_coordinator=listings,
    )


def _api_client(
    *results: Any,
    reservations_api: str = RESERVATIONS_API_V3,
) -> AsyncMock:
    """Build a mocked API client returning the given fetch results.

    Args:
        *results: Fetch results to return, one per refresh.
        reservations_api: Reservation path the client reports.

    Returns:
        An AsyncMock standing in for GuestyApiClient.
    """
    client = AsyncMock()
    client.reservations_api = reservations_api
    client.get_reservations = AsyncMock(side_effect=list(results))
    return client


class TestIdentityBaselineStore:
    """Persistence of the identity baseline (T063)."""

    async def test_round_trip_through_store(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A saved baseline reloads with every field intact."""
        guard = ReservationIdentityGuard(hass, "entry-1")
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_V3,
                continuity=CONTINUITY_VERIFIED,
                updated="2026-01-01T00:00:00+00:00",
                codes={CODE_A: "res-1"},
                bootstrap_codes={CODE_B: "res-2"},
                fallback_codes={"CONF-CCC": "res-3"},
                candidates={
                    "res-1": CandidateHint(
                        check_in="2026-01-01T00:00:00+00:00",
                        check_out="2026-01-04T00:00:00+00:00",
                    ),
                },
            ),
        )

        reloaded = ReservationIdentityGuard(hass, "entry-1")
        await reloaded.async_load()
        baseline = reloaded.baseline

        assert baseline is not None
        assert baseline.path == RESERVATIONS_API_V3
        assert baseline.continuity == CONTINUITY_VERIFIED
        assert baseline.codes == {CODE_A: "res-1"}
        assert baseline.bootstrap_codes == {CODE_B: "res-2"}
        assert baseline.fallback_codes == {"CONF-CCC": "res-3"}
        assert baseline.candidates["res-1"].check_out == ("2026-01-04T00:00:00+00:00")

    async def test_storage_key_and_version(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        """The baseline is stored at the documented key and version."""
        guard = ReservationIdentityGuard(hass, "entry-xyz")
        await guard.async_load()
        await guard.async_store(IdentityBaseline(path=RESERVATIONS_API_V3))

        key = storage_key("entry-xyz")
        assert key == "guesty.entry-xyz.reservations_identity"
        assert hass_storage[key]["version"] == STORAGE_VERSION

    async def test_baseline_survives_restart_and_reload(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A refresh baseline is readable by a later coordinator."""
        entry = _make_entry(entry_id="entry-reload")
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()
        assert coordinator.last_update_success

        fresh = ReservationIdentityGuard(hass, entry.entry_id)
        await fresh.async_load()

        assert fresh.baseline is not None
        assert fresh.baseline.codes == {CODE_A: "res-1"}

    async def test_cap_evicts_earliest_check_out_first(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Over the cap, the earliest check-out is evicted first."""
        total = MAX_BASELINE_CODES + 2
        reservations = [
            _reservation(
                f"res-{index}",
                f"CONF-{index}",
                check_in_days=index - total,
                check_out_days=index - total + 1,
            )
            for index in range(total)
        ]
        entry = _make_entry(entry_id="entry-cap")
        entry.add_to_hass(hass)
        api_client = _api_client(make_fetch_result(reservations))
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        baseline = coordinator.identity.baseline
        assert baseline is not None
        assert len(baseline.codes) == MAX_BASELINE_CODES
        assert "CONF-0" not in baseline.codes
        assert "CONF-1" not in baseline.codes
        assert f"CONF-{total - 1}" in baseline.codes

    async def test_path_recorded_is_the_refreshing_path(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-031b: the baseline records the refreshing path."""
        entry = _make_entry(
            entry_id="entry-path",
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        baseline = coordinator.identity.baseline
        assert baseline is not None
        assert baseline.path == RESERVATIONS_API_LEGACY
        assert baseline.continuity == CONTINUITY_NOT_APPLICABLE

    async def test_candidates_cover_codeless_reservations(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Candidate hints are keyed by id, not by code (FR-049a)."""
        entry = _make_entry(entry_id="entry-cand")
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result(
                [
                    _reservation("res-1", CODE_A),
                    _reservation("res-2", None),
                ],
            ),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        baseline = coordinator.identity.baseline
        assert baseline is not None
        assert set(baseline.candidates) == {"res-1", "res-2"}
        assert baseline.candidates["res-2"].check_in


class TestIdentityComparison:
    """The per-code comparison itself (T064)."""

    def test_exact_match_is_not_ambiguous(self) -> None:
        """An unchanged mapping yields a match and no conflict."""
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_V3,
            codes={CODE_A: "res-1"},
        )
        comparison = baseline.compare({CODE_A: "res-1"}, RESERVATIONS_API_V3)

        assert comparison.matched == 1
        assert not comparison.is_ambiguous

    def test_changed_mapping_is_ambiguous(self) -> None:
        """A code pointing at a new identifier conflicts."""
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_V3,
            codes={CODE_A: "res-1"},
        )
        comparison = baseline.compare({CODE_A: "res-9"}, RESERVATIONS_API_V3)

        assert comparison.is_ambiguous
        assert comparison.conflicts[0].known_id == "res-1"
        assert comparison.conflicts[0].observed_id == "res-9"

    def test_swap_between_present_identifiers_is_ambiguous(self) -> None:
        """FR-032's wholesale re-key must fail closed."""
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_V3,
            codes={CODE_A: "res-1", CODE_B: "res-2"},
        )
        comparison = baseline.compare(
            {CODE_A: "res-2", CODE_B: "res-1"}, RESERVATIONS_API_V3
        )

        assert len(comparison.conflicts) == 2
        assert comparison.matched == 0

    def test_absent_code_is_neither_match_nor_conflict(self) -> None:
        """A code missing from the refresh is not evidence."""
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_V3,
            codes={CODE_A: "res-1"},
        )
        comparison = baseline.compare({}, RESERVATIONS_API_V3)

        assert comparison.matched == 0
        assert not comparison.is_ambiguous

    def test_bootstrap_and_fallback_maps_are_compared(self) -> None:
        """Every retained map takes part in the comparison."""
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_V3,
            codes={},
            bootstrap_codes={CODE_A: "res-1"},
            fallback_codes={CODE_B: "res-2"},
        )
        comparison = baseline.compare(
            {CODE_A: "res-9", CODE_B: "res-8"}, RESERVATIONS_API_V3
        )

        assert {c.source for c in comparison.conflicts} == {"fallback_codes"}
        assert {c.source for c in comparison.cross_path_conflicts} == {
            "bootstrap_codes",
        }

    def test_alteration_and_codeless_rows_are_not_evidence(self) -> None:
        """Proposed changes never supply identity evidence."""
        observed = observe_codes(
            [
                _reservation("res-1", CODE_A),
                _reservation("res-alt", CODE_A, status="alteration"),
                _reservation("res-2", None),
            ],
        )

        assert observed.mapping == {CODE_A: "res-1"}
        assert observed.collisions == ()

    def test_fallback_alone_never_verifies_continuity(self) -> None:
        """Legacy-sourced fallback evidence cannot self-confirm."""
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_V3,
            fallback_codes={CODE_A: "res-1"},
        )
        comparison = baseline.compare({CODE_A: "res-1"}, RESERVATIONS_API_V3)

        assert comparison.matched == 1
        assert comparison.cross_path_matched == 0


class TestCoordinatorIdentityGuard:
    """Coordinator integration of the guard (T064, T070)."""

    async def test_matching_refresh_publishes_normally(
        self,
        hass: HomeAssistant,
    ) -> None:
        """An exact match publishes and keeps the baseline."""
        entry = _make_entry(entry_id="entry-match")
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
            make_fetch_result([_reservation("res-1", CODE_A)]),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()
        await coordinator.async_refresh()

        assert coordinator.last_update_success
        assert coordinator.data["listing-001"][0].id == "res-1"
        assert not coordinator.identity.last_refresh_ambiguous

    async def test_conflict_aborts_and_retains_last_known_good(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A re-key aborts with one error and keeps prior data."""
        entry = _make_entry(entry_id="entry-conflict")
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
            make_fetch_result([_reservation("res-9", CODE_A)]),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()
        first = coordinator.data

        with caplog.at_level(logging.ERROR):
            await coordinator.async_refresh()
            errors = [
                record
                for record in caplog.records
                if record.levelno == logging.ERROR
                and "Set the 'Reservations API'" in record.getMessage()
            ]

        assert len(errors) == 1
        message = errors[0].getMessage()
        assert "res-1" in message
        assert "res-9" in message
        assert "legacy" in message
        assert CODE_A not in message
        assert not coordinator.last_update_success
        assert coordinator.data is first
        assert coordinator.identity.last_refresh_ambiguous
        assert coordinator.identity.baseline is not None
        assert coordinator.identity.baseline.codes == {CODE_A: "res-1"}

    async def test_swap_aborts_the_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Both identifiers still present must still abort."""
        entry = _make_entry(entry_id="entry-swap")
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result(
                [
                    _reservation("res-1", CODE_A),
                    _reservation("res-2", CODE_B),
                ],
            ),
            make_fetch_result(
                [
                    _reservation("res-2", CODE_A),
                    _reservation("res-1", CODE_B),
                ],
            ),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_comparison_costs_no_extra_request(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-034: the guard issues no request of its own."""
        entry = _make_entry(entry_id="entry-free")
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
            make_fetch_result([_reservation("res-1", CODE_A)]),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()
        await coordinator.async_refresh()

        assert api_client.get_reservations.await_count == 2
        assert {call[0] for call in api_client.mock_calls} == {"get_reservations"}

    async def test_codeless_reservation_still_publishes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-033: no code means publishable but not writable."""
        entry = _make_entry(entry_id="entry-nocode")
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result([_reservation("res-2", None)]),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        assert coordinator.data["listing-001"][0].id == "res-2"
        assert coordinator.identity.write_refusal("res-2") is not None

    async def test_legacy_baseline_verifies_a_later_v3_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A legacy baseline is the cross-path evidence (FR-031b)."""
        entry = _make_entry(
            entry_id="entry-cross",
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        entry.add_to_hass(hass)
        legacy_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        await _make_coordinator(hass, entry, legacy_client).async_refresh()
        hass.config_entries.async_update_entry(
            entry,
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
        )

        v3_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
        )
        coordinator = _make_coordinator(hass, entry, v3_client)
        await coordinator.async_refresh()

        baseline = coordinator.identity.baseline
        assert baseline is not None
        assert baseline.path == RESERVATIONS_API_V3
        assert baseline.continuity == CONTINUITY_VERIFIED
        assert coordinator.identity.write_refusal("res-1") is None

    async def test_v3_without_cross_path_evidence_blocks_writes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A v3-only baseline cannot confirm itself (FR-031d)."""
        entry = _make_entry(entry_id="entry-v3only")
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        baseline = coordinator.identity.baseline
        assert baseline is not None
        assert baseline.continuity == CONTINUITY_UNCONFIRMED
        assert coordinator.identity.write_refusal("res-1") is not None


class TestIdentityMatchReporting:
    """FR-035 match log and FR-042 diagnostics (T066, T076)."""

    async def test_first_v3_refresh_logs_match_count_at_info(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The match count is announced once per path activation."""
        entry = _make_entry(
            entry_id="entry-info",
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        entry.add_to_hass(hass)
        legacy_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        await _make_coordinator(hass, entry, legacy_client).async_refresh()
        hass.config_entries.async_update_entry(
            entry,
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
        )

        v3_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
            make_fetch_result([_reservation("res-1", CODE_A)]),
        )
        coordinator = _make_coordinator(hass, entry, v3_client)
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await coordinator.async_refresh()
            await coordinator.async_refresh()

        matches = [
            record
            for record in caplog.records
            if record.levelno == logging.INFO and "identity" in record.getMessage()
        ]
        assert len(matches) == 1
        assert "1" in matches[0].getMessage()
        assert CODE_A not in matches[0].getMessage()

    async def test_diagnostics_carry_counts_without_codes(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The DEBUG line carries the match count, never a code."""
        entry = _make_entry(entry_id="entry-diag")
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
            make_fetch_result([_reservation("res-1", CODE_A)]),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()
        with caplog.at_level(logging.DEBUG):
            await coordinator.async_refresh()

        lines = [
            record.getMessage()
            for record in caplog.records
            if "Reservation refresh:" in record.getMessage()
        ]
        assert lines
        assert "identity_matched=1" in lines[-1]
        assert "identity_ambiguous=False" in lines[-1]
        assert CODE_A not in lines[-1]


class TestIdentityReviewHardening:
    """Behaviours the Phase 6 review surfaced as defects."""

    def test_within_refresh_code_collision_is_ambiguous(self) -> None:
        """Two live rows sharing a code cannot be collapsed."""
        observed = observe_codes(
            [
                _reservation("res-1", CODE_A),
                _reservation("res-9", CODE_A),
            ],
        )

        assert observed.collisions == (("res-1", "res-9"),)
        assert observed.mapping == {CODE_A: "res-1"}

    async def test_collision_aborts_even_without_a_baseline(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A colliding code must not establish a first baseline."""
        guard = ReservationIdentityGuard(hass, "entry-collide")
        await guard.async_load()

        comparison = guard.compare(
            [
                _reservation("res-9", CODE_A),
                _reservation("res-1", CODE_A),
            ],
            RESERVATIONS_API_V3,
        )

        assert comparison.is_ambiguous

    def test_cross_path_disagreement_does_not_abort(self) -> None:
        """Rollback must not deadlock on expected divergence."""
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_V3,
            codes={CODE_A: "v3-1"},
        )

        comparison = baseline.compare(
            {CODE_A: "legacy-1"},
            RESERVATIONS_API_LEGACY,
        )

        assert not comparison.is_ambiguous
        assert len(comparison.cross_path_conflicts) == 1

    def test_cross_path_disagreement_blocks_writes(self) -> None:
        """Divergence is still never written through."""
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_V3,
            codes={CODE_A: "v3-1"},
            continuity=CONTINUITY_VERIFIED,
        )
        comparison = baseline.compare(
            {CODE_A: "legacy-1"},
            RESERVATIONS_API_LEGACY,
        )

        assert (
            _next_continuity(
                baseline,
                RESERVATIONS_API_LEGACY,
                comparison,
            )
            == CONTINUITY_UNCONFIRMED
        )

    async def test_rollback_to_legacy_completes(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A v3 baseline cannot strand a legacy refresh."""
        v3_entry = _make_entry()
        v3_entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result([_reservation("v3-1", CODE_A)]),
        )
        coordinator = _make_coordinator(hass, v3_entry, api_client)
        await coordinator.async_refresh()
        assert coordinator.last_update_success

        hass.config_entries.async_update_entry(
            v3_entry,
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_LEGACY},
        )
        legacy_client = _api_client(
            make_fetch_result([_reservation("legacy-1", CODE_A)]),
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        rolled_back = _make_coordinator(hass, v3_entry, legacy_client)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            await rolled_back.async_refresh()

        assert rolled_back.last_update_success
        assert any(
            "differs between the legacy and v3" in record.getMessage()
            for record in caplog.records
        )
        baseline = rolled_back.identity.baseline
        assert baseline is not None
        assert baseline.continuity == CONTINUITY_UNCONFIRMED
        assert rolled_back.identity.write_refusal("legacy-1") is not None

    async def test_unpersisted_baseline_refuses_writes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A baseline that never reached storage is not trusted."""
        guard = ReservationIdentityGuard(hass, "entry-unstored")
        await guard.async_load()
        await guard.async_record_refresh(
            path=RESERVATIONS_API_LEGACY,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
        )
        assert guard.write_refusal("res-1") is None

        with patch.object(
            guard._store,
            "async_save",
            side_effect=OSError("disk full"),
        ):
            await guard.async_record_refresh(
                path=RESERVATIONS_API_LEGACY,
                reservations=[_reservation("res-1", CODE_A)],
                published={},
                comparison=IdentityComparison(),
                updated="2026-01-02T00:00:00+00:00",
            )

        assert guard.write_refusal("res-1") == REFUSAL_UNSTORED

    async def test_failed_store_keeps_the_refresh_alive(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Storage must never cost a refresh."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.identity.async_load()

        with patch.object(
            coordinator.identity._store,
            "async_save",
            side_effect=OSError("disk full"),
        ):
            await coordinator.async_refresh()

        assert coordinator.last_update_success

    async def test_legacy_evidence_survives_a_disjoint_v3_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The only cross-path evidence must not be discarded."""
        entry = _make_entry(reservations_api=RESERVATIONS_API_LEGACY)
        entry.add_to_hass(hass)
        legacy_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        legacy = _make_coordinator(hass, entry, legacy_client)
        await legacy.async_refresh()

        hass.config_entries.async_update_entry(
            entry,
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
        )
        v3_client = _api_client(
            make_fetch_result([_reservation("res-2", CODE_B)]),
            make_fetch_result(
                [
                    _reservation("res-1", CODE_A),
                    _reservation("res-2", CODE_B),
                ],
            ),
        )
        upgraded = _make_coordinator(hass, entry, v3_client)
        await upgraded.async_refresh()

        baseline = upgraded.identity.baseline
        assert baseline is not None
        assert baseline.legacy_codes == {CODE_A: "res-1"}
        assert baseline.continuity == CONTINUITY_UNCONFIRMED

        await upgraded.async_refresh()
        recovered = upgraded.identity.baseline
        assert recovered is not None
        assert recovered.continuity == CONTINUITY_VERIFIED

    async def test_unpublished_reservations_keep_stay_hints(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Retained identifiers need dates for the 6a fallback."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result(
                [
                    _reservation("res-1", CODE_A),
                    _reservation(
                        "res-untracked",
                        CODE_B,
                        listing_id="listing-999",
                    ),
                ],
            ),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        baseline = coordinator.identity.baseline
        assert baseline is not None
        assert "res-untracked" in baseline.candidates
        assert baseline.candidates["res-untracked"].check_out

    def test_eviction_orders_by_instant_not_by_text(self) -> None:
        """Offsets make ISO text ordering disagree with time."""
        earlier = "2026-11-14T00:30:00+02:00"
        later = "2026-11-13T23:00:00+00:00"
        assert earlier > later

        filler = MAX_BASELINE_CODES - 1
        codes = {f"CODE-{index}": f"res-{index}" for index in range(filler)}
        codes["CODE-early"] = "res-early"
        codes["CODE-late"] = "res-late"
        check_outs = dict.fromkeys(
            (f"res-{index}" for index in range(filler)),
            "2026-11-20T00:00:00+00:00",
        )
        check_outs["res-early"] = earlier
        check_outs["res-late"] = later

        bounded = _bounded(codes, check_outs)

        assert "CODE-early" not in bounded
        assert "CODE-late" in bounded

    async def test_removing_the_entry_deletes_the_baseline(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Confirmation codes must not outlive their entry."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = _api_client(
            make_fetch_result([_reservation("res-1", CODE_A)]),
        )
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        await async_remove_entry(hass, entry)

        reloaded = ReservationIdentityGuard(hass, entry.entry_id)
        await reloaded.async_load()
        assert reloaded.baseline is None

    def test_non_string_confirmation_codes_are_not_evidence(self) -> None:
        """Neither parser constrains the field's type."""
        bad = _reservation("res-1")
        object.__setattr__(bad, "confirmation_code", ["CONF-AAA"])
        numeric = _reservation("res-2")
        object.__setattr__(numeric, "confirmation_code", 12345)

        observed = observe_codes([bad, numeric, _reservation("res-3", CODE_A)])

        assert observed.mapping == {CODE_A: "res-3"}

    async def test_codeless_target_is_refused_despite_history(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-033 asks what the target reports now, not before."""
        guard = ReservationIdentityGuard(hass, "entry-codeless")
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_V3,
                continuity=CONTINUITY_VERIFIED,
                codes={CODE_B: "res-2"},
                legacy_codes={CODE_A: "res-1"},
            ),
        )

        assert guard.write_refusal("res-2") is None
        assert guard.write_refusal("res-1") is not None

    async def test_divergence_survives_later_refreshes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Replacing the evidence must not re-authorize writes."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        v3_client = _api_client(
            make_fetch_result([_reservation("v3-1", CODE_A)]),
        )
        await _make_coordinator(hass, entry, v3_client).async_refresh()

        hass.config_entries.async_update_entry(
            entry,
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_LEGACY},
        )
        legacy_client = _api_client(
            make_fetch_result([_reservation("legacy-1", CODE_A)]),
            make_fetch_result([_reservation("legacy-1", CODE_A)]),
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        rolled_back = _make_coordinator(hass, entry, legacy_client)
        await rolled_back.async_refresh()
        await rolled_back.async_refresh()

        baseline = rolled_back.identity.baseline
        assert baseline is not None
        assert baseline.divergent
        assert baseline.continuity == CONTINUITY_UNCONFIRMED
        assert rolled_back.identity.write_refusal("legacy-1") is not None

    async def test_divergence_survives_a_reload(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The verdict must outlive the process that reached it."""
        guard = ReservationIdentityGuard(hass, "entry-divergent")
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_LEGACY,
                continuity=CONTINUITY_UNCONFIRMED,
                divergent=True,
                codes={CODE_A: "res-1"},
            ),
        )

        reloaded = ReservationIdentityGuard(hass, "entry-divergent")
        await reloaded.async_load()
        await reloaded.async_record_refresh(
            path=RESERVATIONS_API_LEGACY,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
        )

        baseline = reloaded.baseline
        assert baseline is not None
        assert baseline.divergent
        assert baseline.continuity == CONTINUITY_UNCONFIRMED

    async def test_retention_dates_stored_hints(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Eviction must date absent reservations by stored hints."""
        guard = ReservationIdentityGuard(hass, "entry-retain")
        await guard.async_load()
        total = MAX_BASELINE_CODES + 10
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_LEGACY,
                codes={f"CODE-{index}": f"res-{index}" for index in range(total)},
                candidates={
                    f"res-{index}": CandidateHint(
                        check_in=_rel(total - index).isoformat(),
                        check_out=_rel(total - index + 2).isoformat(),
                    )
                    for index in range(total)
                },
            ),
        )

        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[_reservation("res-new", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
        )

        baseline = guard.baseline
        assert baseline is not None
        retained = baseline.legacy_codes
        assert retained is not None
        assert len(retained) == MAX_BASELINE_CODES
        # res-0 checks out latest, so it must survive while the
        # earliest-dated entries are evicted.
        assert "CODE-0" in retained
        assert f"CODE-{total - 1}" not in retained

    def test_retained_legacy_evidence_is_bounded(self) -> None:
        """Repeated transitions must not grow the map forever."""
        previous = IdentityBaseline(
            path=RESERVATIONS_API_LEGACY,
            codes={
                f"CODE-{index}": f"res-{index}"
                for index in range(MAX_BASELINE_CODES + 10)
            },
        )
        check_outs = {
            f"res-{index}": _rel(index).isoformat()
            for index in range(MAX_BASELINE_CODES + 10)
        }

        retained = _retained_legacy(previous, RESERVATIONS_API_V3, check_outs)

        assert retained is not None
        assert len(retained) == MAX_BASELINE_CODES
        assert "CODE-0" not in retained

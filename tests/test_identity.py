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
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import httpx
import pytest
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty import (
    ISSUE_IDENTITY_UNCONFIRMED,
    async_notify_identity_continuity,
    async_remove_entry,
    async_track_identity_continuity,
)
from custom_components.guesty.api.const import (
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
)
from custom_components.guesty.api.exceptions import GuestyRateLimitError
from custom_components.guesty.api.models import (
    GuestyListing,
    GuestyReservation,
    GuestyStaySegment,
    RejectedCode,
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
from custom_components.guesty.identity import (
    BOOTSTRAP_ALREADY_DONE,
    BOOTSTRAP_AMBIGUOUS,
    BOOTSTRAP_FRESH_INSTALL,
    BOOTSTRAP_LEGACY_PATH,
    BOOTSTRAP_SWEPT,
    BOOTSTRAP_UNAVAILABLE,
    CONFLICT_EVIDENCE_STORAGE_VERSION,
    CONTINUITY_NOT_APPLICABLE,
    CONTINUITY_UNCONFIRMED,
    CONTINUITY_VERIFIED,
    EVIDENCE_COMPROMISED,
    EVIDENCE_READY,
    EVIDENCE_REVALIDATING,
    EVIDENCE_SATURATED,
    EVIDENCE_UNLOADED,
    EVIDENCE_UNSTORED,
    IDENTITY_STORAGE_MINOR_VERSION,
    MAX_BASELINE_CODES,
    MAX_CONFLICT_ONLY_EVIDENCE,
    REFUSAL_AMBIGUOUS,
    REFUSAL_EVIDENCE_REPAIR,
    REFUSAL_EVIDENCE_UNAVAILABLE,
    REFUSAL_NO_BASELINE,
    REFUSAL_REVALIDATING,
    REFUSAL_UNCONFIRMED,
    REFUSAL_UNSTORED,
    SOURCE_BOOTSTRAP,
    SOURCE_CONFLICT_ONLY,
    STORAGE_VERSION,
    CandidateHint,
    ConflictOnlyEvidence,
    IdentityBaseline,
    IdentityComparison,
    ReservationIdentityGuard,
    _bounded,
    _bounded_candidates,
    _IdentityStore,
    _next_continuity,
    _retained_legacy,
    _retrieved_hints,
    _verified_candidate_interval,
    async_bootstrap_identity,
    conflict_evidence_storage_key,
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


def _register_reservation_entity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> None:
    """Register a reservation entity so the entry looks upgraded.

    Args:
        hass: Home Assistant instance.
        entry: The config entry the entity belongs to.
    """
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.unique_id}_listing-001_reservation_status",
        config_entry=entry,
    )


@contextmanager
def _legacy_sweep(
    reservations: list[GuestyReservation] | None = None,
    *,
    error: Exception | None = None,
    pages: int = 2,
    **fields: Any,
) -> Iterator[AsyncMock]:
    """Stand in for the legacy reservation source of the sweep.

    Args:
        reservations: Reservations the sweep should return.
        error: Exception the sweep should raise instead.
        pages: Page count the stubbed retrieval reports.
        **fields: Further fetch result fields to set.

    Yields:
        The mocked ``fetch`` coroutine, for call assertions.
    """
    fetch = AsyncMock(
        return_value=make_fetch_result(reservations or [], pages=pages, **fields),
        side_effect=error,
    )
    source = AsyncMock()
    source.fetch = fetch
    with patch(
        "custom_components.guesty.identity.LegacyReservationSource",
        return_value=source,
    ):
        yield fetch


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
                        timing_status="known",
                        check_in="2026-01-01T00:00:00+00:00",
                        check_out="2026-01-04T00:00:00+00:00",
                        observed_at="2026-01-01T00:00:00+00:00",
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

    def test_old_candidate_shape_migrates_without_timing_evidence(self) -> None:
        """Old endpoints migrate to unknown using baseline recency only."""
        baseline = IdentityBaseline.from_dict(
            {
                "path": RESERVATIONS_API_V3,
                "updated": "2026-02-01T12:00:00+00:00",
                "candidates": {
                    "res-old": {
                        "check_in": "2026-01-01T00:00:00+00:00",
                        "check_out": "2026-01-04T00:00:00+00:00",
                    },
                },
            },
        )

        hint = baseline.candidates["res-old"]
        assert hint.timing_status == "unknown"
        assert hint.check_in is None
        assert hint.check_out is None
        assert hint.observed_at == "2026-02-01T12:00:00+00:00"

    def test_old_candidate_without_valid_baseline_time_uses_sentinel(self) -> None:
        """Malformed enclosing timestamps use the fixed oldest sentinel."""
        baseline = IdentityBaseline.from_dict(
            {
                "updated": "not-a-timestamp",
                "candidates": {
                    "res-old": {
                        "check_in": "2026-01-01T00:00:00+00:00",
                        "check_out": "2026-01-04T00:00:00+00:00",
                    },
                },
            },
        )

        hint = baseline.candidates["res-old"]
        assert hint.timing_status == "unknown"
        assert hint.observed_at == "0001-01-01T00:00:00+00:00"

    def test_candidate_capacity_uses_recency_then_reservation_id(self) -> None:
        """Equal-recent observations evict the smallest identifier first."""
        observed_at = "2026-01-01T00:00:00+00:00"
        candidates = {
            f"res-{index:05d}": CandidateHint(
                timing_status="unknown",
                observed_at=observed_at,
            )
            for index in range(MAX_BASELINE_CODES + 1)
        }

        retained = _bounded_candidates(candidates)

        assert len(retained) == MAX_BASELINE_CODES
        assert "res-00000" not in retained
        assert "res-05000" in retained

    async def test_candidate_recency_requires_persisted_source_observation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Only persisted primary/by-ID observations advance candidate age."""
        guard = ReservationIdentityGuard(hass, "entry-observation-recency")
        await guard.async_load()
        original_observation = "2026-01-01T00:00:00+00:00"
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_V3,
                updated=original_observation,
                codes={CODE_A: "res-1"},
                candidates={
                    "res-1": CandidateHint(
                        timing_status="known",
                        check_in="2026-01-02T16:00:00+00:00",
                        check_out="2026-01-04T11:00:00+00:00",
                        observed_at=original_observation,
                    ),
                },
            ),
        )
        current_unknown = GuestyReservation(
            id="res-1",
            listing_id="listing-001",
            status="confirmed",
            check_in=None,
            check_out=None,
            confirmation_code=CODE_A,
            is_v3=True,
        )
        primary_observation = "2026-01-02T00:00:00+00:00"
        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[current_unknown],
            published={},
            comparison=IdentityComparison(),
            updated=primary_observation,
            observed_ids={"res-1"},
        )

        baseline = guard.baseline
        assert baseline is not None
        assert baseline.candidates["res-1"].observed_at == primary_observation
        assert baseline.candidates["res-1"].timing_status == "unknown"
        assert baseline.candidates["res-1"].check_in is None
        assert baseline.candidates["res-1"].check_out is None
        assert baseline.codes == {CODE_A: "res-1"}
        reloaded = ReservationIdentityGuard(
            hass,
            "entry-observation-recency",
        )
        await reloaded.async_load()
        assert reloaded.baseline is not None
        assert reloaded.baseline.candidates["res-1"].observed_at == primary_observation
        assert reloaded.baseline.candidates["res-1"].timing_status == "unknown"
        assert reloaded.baseline.candidates["res-1"].check_in is None
        assert reloaded.baseline.codes == {CODE_A: "res-1"}

        # A retained/cache-only representation is not source evidence,
        # even when it carries an ID and current timing fields.
        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[current_unknown],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-03T00:00:00+00:00",
            observed_ids=(),
        )
        assert guard.baseline is not None
        assert guard.baseline.candidates["res-1"].observed_at == primary_observation

        await guard.async_record_bootstrap(reservations=(), failed=True)
        assert guard.baseline is not None
        assert guard.baseline.candidates["res-1"].observed_at == primary_observation

        # A qualifying observation that cannot be persisted must not
        # replace the last durable timestamp in memory or storage.
        with patch.object(
            guard._store,
            "async_save",
            side_effect=OSError("disk full"),
        ):
            stored = await guard.async_record_refresh(
                path=RESERVATIONS_API_V3,
                reservations=[current_unknown],
                published={},
                comparison=IdentityComparison(),
                updated="2026-01-04T00:00:00+00:00",
                observed_ids={"res-1"},
            )
        assert stored is False
        assert guard.baseline is not None
        assert guard.baseline.candidates["res-1"].observed_at == primary_observation

        # An aborted identity comparison never records a source refresh.
        guard.mark_ambiguous()
        assert guard.baseline.candidates["res-1"].observed_at == primary_observation

    async def test_filtered_by_id_observation_replaces_candidate_timing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A successful but unpublished lookup cannot leave stale hints."""
        guard = ReservationIdentityGuard(hass, "entry-filtered-observation")
        await guard.async_load()
        prior_observation = "2026-01-01T00:00:00+00:00"
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_V3,
                updated=prior_observation,
                candidates={
                    "res-filtered": CandidateHint(
                        timing_status="known",
                        check_in="2026-01-02T16:00:00+00:00",
                        check_out="2026-01-04T11:00:00+00:00",
                        observed_at=prior_observation,
                    ),
                },
            ),
        )
        observed_at = "2026-02-01T12:00:00+00:00"
        observed = GuestyReservation(
            id="res-filtered",
            listing_id="listing-001",
            status="cancelled",
            check_in=datetime(2026, 2, 2, 16, tzinfo=UTC),
            check_out=datetime(2026, 2, 5, 11, tzinfo=UTC),
        )

        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=(),
            observed_reservations=(observed,),
            published={},
            comparison=IdentityComparison(),
            updated=observed_at,
            observed_ids={"res-filtered"},
        )

        assert guard.baseline is not None
        hint = guard.baseline.candidates["res-filtered"]
        assert hint.timing_status == "known"
        assert hint.check_in == "2026-02-02T16:00:00+00:00"
        assert hint.check_out == "2026-02-05T11:00:00+00:00"
        assert hint.observed_at == observed_at

    def test_v3_candidate_requires_every_raw_segment_in_order(self) -> None:
        """Only a complete, verified, non-overlapping raw sequence is timed."""
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end_first = datetime(2026, 1, 2, tzinfo=UTC)
        end_last = datetime(2026, 1, 4, tzinfo=UTC)
        complete = GuestyReservation(
            id="res-complete",
            listing_id="listing-001",
            status="confirmed",
            check_in=None,
            check_out=None,
            is_v3=True,
            raw_stay_count=2,
            stay=(
                GuestyStaySegment(
                    listing_id="listing-001",
                    check_in=start,
                    check_out=end_first,
                    raw_position=0,
                ),
                GuestyStaySegment(
                    listing_id="listing-002",
                    check_in=end_first,
                    check_out=end_last,
                    raw_position=1,
                ),
            ),
        )
        assert _verified_candidate_interval(complete) == (start, end_last)

        # A usable first and last segment cannot hide an unusable raw
        # middle entry, which the model records as a positional gap.
        around_bad_middle = GuestyReservation(
            id="res-middle-gap",
            listing_id="listing-001",
            status="confirmed",
            check_in=None,
            check_out=None,
            is_v3=True,
            raw_stay_count=3,
            stay=(
                complete.stay[0],
                GuestyStaySegment(
                    listing_id="listing-003",
                    check_in=end_first,
                    check_out=end_last,
                    raw_position=2,
                ),
            ),
        )
        assert _verified_candidate_interval(around_bad_middle) is None
        assert (
            _retrieved_hints(
                [around_bad_middle],
                observed_at="2026-01-05T00:00:00+00:00",
            )["res-middle-gap"].timing_status
            == "unknown"
        )

        invalid_sequences = (
            # Missing exact room identity.
            (
                GuestyStaySegment(
                    listing_id="",
                    check_in=start,
                    check_out=end_first,
                    raw_position=0,
                ),
            ),
            # Partial endpoint.
            (
                GuestyStaySegment(
                    listing_id="listing-001",
                    check_in=start,
                    check_out=None,
                    raw_position=0,
                ),
            ),
            # Reverse raw chronology.
            (
                GuestyStaySegment(
                    listing_id="listing-002",
                    check_in=end_first,
                    check_out=end_last,
                    raw_position=0,
                ),
                GuestyStaySegment(
                    listing_id="listing-001",
                    check_in=start,
                    check_out=end_first,
                    raw_position=1,
                ),
            ),
            # Positive individual intervals that overlap.
            (
                GuestyStaySegment(
                    listing_id="listing-001",
                    check_in=start,
                    check_out=end_last,
                    raw_position=0,
                ),
                GuestyStaySegment(
                    listing_id="listing-002",
                    check_in=end_first,
                    check_out=end_last,
                    raw_position=1,
                ),
            ),
        )
        for index, stay in enumerate(invalid_sequences):
            reservation = GuestyReservation(
                id=f"res-invalid-{index}",
                listing_id="listing-001",
                status="confirmed",
                check_in=None,
                check_out=None,
                is_v3=True,
                raw_stay_count=len(stay),
                stay=stay,
            )
            assert _verified_candidate_interval(reservation) is None

    async def test_unknown_candidate_does_not_expire_identity_evidence(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Missing timing is not evidence that identity maps may expire."""
        guard = ReservationIdentityGuard(hass, "entry-unknown-no-expiry")
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_LEGACY,
                updated="2026-01-01T00:00:00+00:00",
                bootstrap_attempted=True,
                codes={CODE_B: "res-1"},
                bootstrap_codes={CODE_A: "res-1"},
                candidates={
                    "res-1": CandidateHint(
                        timing_status="unknown",
                        observed_at="2026-01-01T00:00:00+00:00",
                    ),
                },
            ),
        )

        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[],
            published={},
            comparison=IdentityComparison(),
            updated=datetime.now(UTC).isoformat(),
            past_days=0,
            observed_ids=(),
        )

        baseline = guard.baseline
        assert baseline is not None
        assert baseline.candidates["res-1"].timing_status == "unknown"
        assert baseline.candidates["res-1"].check_out is None
        assert baseline.bootstrap_codes == {CODE_A: "res-1"}
        assert baseline.legacy_codes == {CODE_B: "res-1"}

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
        """Every retained map takes part in the comparison.

        A bootstrap disagreement is ambiguity, not drift: the sweep
        exists to be contradicted, and FR-032 requires the refresh
        to abort rather than merely demote continuity.
        """
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_V3,
            codes={},
            bootstrap_codes={CODE_A: "res-1"},
            fallback_codes={CODE_B: "res-2"},
        )
        comparison = baseline.compare(
            {CODE_A: "res-9", CODE_B: "res-8"}, RESERVATIONS_API_V3
        )

        assert {c.source for c in comparison.conflicts} == {
            "bootstrap_codes",
            "fallback_codes",
        }
        assert comparison.cross_path_conflicts == ()
        assert comparison.is_ambiguous

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
        assert coordinator.identity.baseline is not None
        observed_at_before_abort = coordinator.identity.baseline.candidates[
            "res-1"
        ].observed_at

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
        assert (
            coordinator.identity.baseline.candidates["res-1"].observed_at
            == observed_at_before_abort
        )

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

    async def test_unstored_absence_is_not_acknowledged(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A failed save leaves the deletion to be reported again."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        absent_result = make_fetch_result(
            [_reservation("res-1", CODE_A)],
            fallback_absent=("res-gone",),
        )
        api_client = _api_client(absent_result, absent_result)
        api_client.acknowledge_absent_reservations = MagicMock()
        coordinator = _make_coordinator(hass, entry, api_client)
        await coordinator.identity.async_load()

        with patch.object(
            coordinator.identity._store,
            "async_save",
            side_effect=OSError("disk full"),
        ):
            await coordinator.async_refresh()

        assert api_client.acknowledge_absent_reservations.call_count == 0

        await coordinator.async_refresh()

        api_client.acknowledge_absent_reservations.assert_called_once_with(
            ("res-gone",),
        )

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
        """FR-033 asks what the target reports now, not before.

        Updated for feature 013 (research R18): every load revalidates
        the conflict-only evidence, so writes are refused until an
        accepted refresh stores the evidence and the baseline (steps 1
        and 2). The refresh runs before the refusals are compared; it
        reports ``res-2`` with its code and leaves ``res-1`` known only
        through history.
        """
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
        assert await _accept(
            guard,
            [_reservation("res-2", CODE_B)],
            path=RESERVATIONS_API_V3,
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
                        timing_status="known",
                        check_in=_rel(total - index).isoformat(),
                        check_out=_rel(total - index + 2).isoformat(),
                        observed_at=_rel(index).isoformat(),
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


class TestBootstrapSweep:
    """The one-time legacy bootstrap sweep (T067, T073-T075)."""

    async def test_fresh_install_is_not_applicable_and_writes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """No baseline and no entities means nothing to contradict."""
        entry = _make_entry(entry_id="entry-fresh")
        entry.add_to_hass(hass)
        guard = ReservationIdentityGuard(hass, entry.entry_id)
        api_client = _api_client()

        with patch(
            "custom_components.guesty.identity.LegacyReservationSource",
        ) as source:
            result = await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=api_client,
            )

        assert result.reason == BOOTSTRAP_FRESH_INSTALL
        assert source.call_count == 0
        baseline = guard.baseline
        assert baseline is not None
        assert baseline.continuity == CONTINUITY_NOT_APPLICABLE
        assert baseline.bootstrap_attempted is True

        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
        )

        assert guard.baseline is not None
        assert guard.baseline.continuity == CONTINUITY_NOT_APPLICABLE
        assert guard.write_refusal("res-1") is None

    async def test_upgrade_sweeps_once_and_stores_bootstrap(
        self,
        hass: HomeAssistant,
    ) -> None:
        """An upgrading entry records legacy evidence as its own map."""
        entry = _make_entry(entry_id="entry-upgrade")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)
        guard = ReservationIdentityGuard(hass, entry.entry_id)

        before_sweep = datetime.now(UTC)
        with _legacy_sweep([_reservation("legacy-1", CODE_A)]) as fetch:
            result = await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=_api_client(),
            )

        assert result.reason == BOOTSTRAP_SWEPT
        assert fetch.await_count == 1
        baseline = guard.baseline
        assert baseline is not None
        assert baseline.path == RESERVATIONS_API_LEGACY
        assert baseline.bootstrap_codes == {CODE_A: "legacy-1"}
        assert baseline.continuity == CONTINUITY_UNCONFIRMED
        assert baseline.candidates["legacy-1"].check_out != ""
        bootstrap_observed_at = datetime.fromisoformat(
            baseline.candidates["legacy-1"].observed_at,
        )
        assert bootstrap_observed_at.tzinfo is not None
        assert bootstrap_observed_at >= before_sweep

    async def test_sweep_runs_exactly_once_across_a_reload(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The permitted legacy call must never be repeated."""
        entry = _make_entry(entry_id="entry-once")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)

        with _legacy_sweep([_reservation("legacy-1", CODE_A)]) as fetch:
            await async_bootstrap_identity(
                hass,
                entry,
                guard=ReservationIdentityGuard(hass, entry.entry_id),
                api_client=_api_client(),
            )
            reloaded = ReservationIdentityGuard(hass, entry.entry_id)
            second = await async_bootstrap_identity(
                hass,
                entry,
                guard=reloaded,
                api_client=_api_client(),
            )

        assert fetch.await_count == 1
        assert second.reason == BOOTSTRAP_ALREADY_DONE
        assert reloaded.baseline is not None
        assert reloaded.baseline.bootstrap_codes == {CODE_A: "legacy-1"}

    async def test_failed_sweep_is_never_retried(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A failed sweep stays one-time and keeps writes refused."""
        entry = _make_entry(entry_id="entry-fail")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)
        guard = ReservationIdentityGuard(hass, entry.entry_id)

        with _legacy_sweep(
            error=GuestyRateLimitError("rate limited"),
        ) as fetch:
            result = await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=_api_client(),
            )
            retried = await async_bootstrap_identity(
                hass,
                entry,
                guard=ReservationIdentityGuard(hass, entry.entry_id),
                api_client=_api_client(),
            )

        assert result.reason == BOOTSTRAP_UNAVAILABLE
        assert retried.reason == BOOTSTRAP_ALREADY_DONE
        assert fetch.await_count == 1

        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
        )

        assert guard.baseline is not None
        assert guard.baseline.continuity == CONTINUITY_UNCONFIRMED
        assert guard.write_refusal("res-1") == REFUSAL_UNCONFIRMED

    async def test_sweep_is_skipped_on_the_legacy_path(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A legacy entry has no cross-path question to answer."""
        entry = _make_entry(
            entry_id="entry-legacy",
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)

        with _legacy_sweep([_reservation("legacy-1", CODE_A)]) as fetch:
            result = await async_bootstrap_identity(
                hass,
                entry,
                guard=ReservationIdentityGuard(hass, entry.entry_id),
                api_client=_api_client(
                    reservations_api=RESERVATIONS_API_LEGACY,
                ),
            )

        assert result.reason == BOOTSTRAP_LEGACY_PATH
        assert fetch.await_count == 0

    async def test_disjoint_comparison_retains_and_blocks(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Evidence that never overlaps proves nothing and is kept."""
        entry = _make_entry(entry_id="entry-disjoint")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)
        guard = ReservationIdentityGuard(hass, entry.entry_id)

        with _legacy_sweep([_reservation("legacy-1", CODE_A)]):
            await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=_api_client(),
            )

        reservations = [_reservation("v3-2", CODE_B)]
        comparison = guard.compare(reservations, RESERVATIONS_API_V3)
        assert not comparison.is_ambiguous
        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=reservations,
            published={},
            comparison=comparison,
            updated="2026-01-01T00:00:00+00:00",
        )

        baseline = guard.baseline
        assert baseline is not None
        assert baseline.bootstrap_codes == {CODE_A: "legacy-1"}
        assert baseline.continuity == CONTINUITY_UNCONFIRMED
        assert guard.write_refusal("v3-2") == REFUSAL_UNCONFIRMED

    async def test_overlap_verifies_continuity_and_permits_writes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """One agreeing shared code is informative evidence."""
        entry = _make_entry(entry_id="entry-overlap")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)
        guard = ReservationIdentityGuard(hass, entry.entry_id)

        with _legacy_sweep([_reservation("shared-1", CODE_A)]):
            await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=_api_client(),
            )

        reservations = [_reservation("shared-1", CODE_A)]
        comparison = guard.compare(reservations, RESERVATIONS_API_V3)
        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=reservations,
            published={},
            comparison=comparison,
            updated="2026-01-01T00:00:00+00:00",
        )

        baseline = guard.baseline
        assert baseline is not None
        assert baseline.continuity == CONTINUITY_VERIFIED
        assert guard.write_refusal("shared-1") is None

    async def test_partial_overlap_retires_only_covered_codes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A partial overlap must not discharge what it never saw."""
        entry = _make_entry(entry_id="entry-partial")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)
        guard = ReservationIdentityGuard(hass, entry.entry_id)

        with _legacy_sweep(
            [
                _reservation("shared-1", CODE_A),
                _reservation("shared-2", CODE_B),
            ],
        ):
            await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=_api_client(),
            )

        reservations = [_reservation("shared-1", CODE_A)]
        comparison = guard.compare(reservations, RESERVATIONS_API_V3)
        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=reservations,
            published={},
            comparison=comparison,
            updated="2026-01-01T00:00:00+00:00",
        )

        baseline = guard.baseline
        assert baseline is not None
        assert baseline.continuity == CONTINUITY_VERIFIED
        assert baseline.bootstrap_codes == {CODE_B: "shared-2"}

        # Across a reload, the uncovered code still catches a
        # conflict it was never checked against.
        reloaded = ReservationIdentityGuard(hass, entry.entry_id)
        await reloaded.async_load()
        later = reloaded.compare(
            [_reservation("v3-9", CODE_B)],
            RESERVATIONS_API_V3,
        )

        assert later.is_ambiguous
        assert {c.source for c in later.conflicts} == {SOURCE_BOOTSTRAP}

    async def test_disagreeing_bootstrap_aborts_the_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A bootstrap disagreement is FR-032 ambiguity, not drift."""
        entry = _make_entry(entry_id="entry-disagree")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)
        guard = ReservationIdentityGuard(hass, entry.entry_id)

        with _legacy_sweep([_reservation("legacy-1", CODE_A)]):
            await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=_api_client(),
            )

        comparison = guard.compare(
            [_reservation("v3-1", CODE_A)],
            RESERVATIONS_API_V3,
        )

        assert comparison.is_ambiguous
        assert comparison.conflicts[0].known_id == "legacy-1"
        assert comparison.conflicts[0].observed_id == "v3-1"

    async def test_unchecked_entry_retires_once_safely_past(
        self,
        hass: HomeAssistant,
    ) -> None:
        """An entry that can never recur is retired without a check."""
        guard = ReservationIdentityGuard(hass, "entry-retire")
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_LEGACY,
                bootstrap_attempted=True,
                bootstrap_codes={CODE_A: "old-1", CODE_B: "recent-1"},
                candidates={
                    "old-1": CandidateHint(
                        timing_status="known",
                        check_in=_rel(-120).isoformat(),
                        check_out=_rel(-119).isoformat(),
                        observed_at=_rel(-120).isoformat(),
                    ),
                    "recent-1": CandidateHint(
                        timing_status="known",
                        check_in=_rel(-2).isoformat(),
                        check_out=_rel(-1).isoformat(),
                        observed_at=_rel(-2).isoformat(),
                    ),
                },
            ),
        )

        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
            past_days=30,
        )

        baseline = guard.baseline
        assert baseline is not None
        assert baseline.bootstrap_codes == {CODE_B: "recent-1"}

    async def test_sweep_issues_one_legacy_source_call(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The permitted exception is bounded to one retrieval."""
        entry = _make_entry(entry_id="entry-bound")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)

        with _legacy_sweep([_reservation("legacy-1", CODE_A)]) as fetch:
            result = await async_bootstrap_identity(
                hass,
                entry,
                guard=ReservationIdentityGuard(hass, entry.entry_id),
                api_client=_api_client(),
                past_days=11,
                future_days=22,
            )

        assert fetch.await_count == 1
        assert fetch.await_args is not None
        assert fetch.await_args.kwargs == {
            "past_days": 11,
            "future_days": 22,
        }
        assert result.pages == 2

    async def test_colliding_sweep_is_unusable_evidence(
        self,
        hass: HomeAssistant,
    ) -> None:
        """One code naming two legacy rows cannot be a baseline."""
        entry = _make_entry(entry_id="entry-collide")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)
        guard = ReservationIdentityGuard(hass, entry.entry_id)

        with _legacy_sweep(
            [
                _reservation("legacy-1", CODE_A),
                _reservation("legacy-2", CODE_A),
            ],
        ):
            result = await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=_api_client(),
            )

        assert result.reason == BOOTSTRAP_AMBIGUOUS
        assert result.informative is False
        baseline = guard.baseline
        assert baseline is not None
        assert baseline.bootstrap_attempted is True
        assert not baseline.bootstrap_codes

        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[_reservation("legacy-1", CODE_A)],
            published={},
            comparison=guard.compare(
                [_reservation("legacy-1", CODE_A)],
                RESERVATIONS_API_V3,
            ),
            updated="2026-01-01T00:00:00+00:00",
        )

        assert guard.baseline is not None
        assert guard.baseline.continuity == CONTINUITY_UNCONFIRMED
        assert guard.write_refusal("legacy-1") is not None

    async def test_existing_baseline_is_never_a_fresh_install(
        self,
        hass: HomeAssistant,
    ) -> None:
        """An entry with no entities may still have a baseline."""
        entry = _make_entry(entry_id="entry-nolistings")
        entry.add_to_hass(hass)
        guard = ReservationIdentityGuard(hass, entry.entry_id)
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_V3,
                continuity=CONTINUITY_UNCONFIRMED,
            ),
        )

        with _legacy_sweep([_reservation("legacy-1", CODE_A)]) as fetch:
            result = await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=_api_client(),
            )

        assert fetch.await_count == 1
        assert result.reason == BOOTSTRAP_SWEPT
        assert guard.baseline is not None
        assert guard.baseline.continuity == CONTINUITY_UNCONFIRMED
        assert guard.write_refusal("legacy-1") is not None

    async def test_unstored_sweep_still_compares_its_evidence(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Lost-to-storage evidence must not be lost to the guard."""
        entry = _make_entry(entry_id="entry-unstored-sweep")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)
        guard = ReservationIdentityGuard(hass, entry.entry_id)
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_V3,
                continuity=CONTINUITY_UNCONFIRMED,
                codes={CODE_B: "shared-2"},
            ),
        )

        with (
            _legacy_sweep([_reservation("legacy-1", CODE_A)]),
            patch.object(
                guard._store,
                "async_save",
                side_effect=OSError("disk full"),
            ),
        ):
            await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=_api_client(),
            )

        assert guard.baseline is not None
        assert guard.baseline.bootstrap_codes == {CODE_A: "legacy-1"}
        assert guard.write_refusal("shared-2") == REFUSAL_UNSTORED

        # A later refresh that matches an older code must not be
        # able to verify continuity past the conflicting sweep.
        conflicting = [
            _reservation("shared-2", CODE_B),
            _reservation("v3-other", CODE_A),
        ]
        comparison = guard.compare(conflicting, RESERVATIONS_API_V3)

        assert comparison.is_ambiguous is True

    async def test_rejected_legacy_pair_collision_is_ambiguous(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A rejected legacy record sharing a code spoils the sweep."""
        entry = _make_entry(entry_id="entry-sweep-rejected")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)
        guard = ReservationIdentityGuard(hass, entry.entry_id)

        with _legacy_sweep(
            [_reservation("legacy-1", CODE_A)],
            rejected_ids=("legacy-2",),
            rejected_codes=(RejectedCode("legacy-2", CODE_A),),
        ):
            result = await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=_api_client(),
            )

        assert result.reason == BOOTSTRAP_AMBIGUOUS
        assert guard.baseline is not None
        assert guard.baseline.bootstrap_codes is None

    async def test_rejected_legacy_pairs_are_recorded_before_identity(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
    ) -> None:
        """Rejected sweep pairs are saved first, as legacy evidence."""
        entry = _make_entry(entry_id="entry-sweep-order")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)
        guard = ReservationIdentityGuard(hass, entry.entry_id)
        await guard.async_load()
        order: list[str] = []
        evidence_save = guard.conflict_evidence._store.async_save
        identity_save = guard._store.async_save

        async def _evidence_save(data: Any) -> None:
            """Record an evidence save."""
            order.append("evidence")
            await evidence_save(data)

        async def _identity_save(data: Any) -> None:
            """Record an identity save."""
            order.append("identity")
            await identity_save(data)

        with (
            _legacy_sweep(
                [_reservation("legacy-1", CODE_A)],
                rejected_ids=("legacy-2",),
                rejected_codes=(RejectedCode("legacy-2", CODE_B),),
                rejected_check_out_bounds={"legacy-2": "2026-01-10"},
            ),
            patch.object(guard.conflict_evidence._store, "async_save", _evidence_save),
            patch.object(guard._store, "async_save", _identity_save),
        ):
            result = await async_bootstrap_identity(
                hass,
                entry,
                guard=guard,
                api_client=_api_client(),
            )

        assert result.reason == BOOTSTRAP_SWEPT
        assert order == ["evidence", "identity"]
        baseline = guard.baseline
        assert baseline is not None
        assert baseline.bootstrap_codes == {CODE_A: "legacy-1"}
        assert "legacy-2" not in baseline.known_ids()
        stored = hass_storage[conflict_evidence_storage_key(entry.entry_id)]["data"]
        assert [
            (e["reservation_id"], e["code"], e["path"], e["fallback"])
            for e in stored["entries"]
        ] == [("legacy-2", CODE_B, RESERVATIONS_API_LEGACY, False)]
        assert stored["generation"] == baseline.conflict_evidence_generation == 1
        maps = guard.conflict_evidence.compare_maps(RESERVATIONS_API_V3)
        assert all(not stored_map.confirms for stored_map in maps)

    async def test_missing_id_gives_unavailable(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A sweep record with no ``_id`` fails the sweep closed."""
        entry = _make_entry(entry_id="entry-sweep-noid")
        entry.add_to_hass(hass)
        _register_reservation_entity(hass, entry)
        guard = ReservationIdentityGuard(hass, entry.entry_id)
        client = _api_client()
        client._request = AsyncMock(
            return_value=httpx.Response(
                200, json={"results": [{"status": "confirmed"}]}
            ),
        )

        result = await async_bootstrap_identity(
            hass,
            entry,
            guard=guard,
            api_client=client,
        )

        assert result.reason == BOOTSTRAP_UNAVAILABLE
        assert client._request.await_count == 1
        assert guard.write_refusal("legacy-1") is not None


class TestContinuityNotice:
    """The unconfirmed-continuity notice (T075, FR-031c)."""

    async def test_notice_is_raised_when_continuity_unconfirmed(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An uninformative bootstrap must be visible, not silent."""
        entry = _make_entry(entry_id="entry-notice")
        entry.add_to_hass(hass)
        guard = ReservationIdentityGuard(hass, entry.entry_id)
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_LEGACY,
                continuity=CONTINUITY_UNCONFIRMED,
                bootstrap_attempted=True,
            ),
        )

        with caplog.at_level(logging.WARNING):
            async_notify_identity_continuity(hass, entry, guard)

        issue = ir.async_get(hass).async_get_issue(
            DOMAIN,
            f"{ISSUE_IDENTITY_UNCONFIRMED}_{entry.entry_id}",
        )
        assert issue is not None
        assert issue.is_fixable is False
        assert issue.severity == ir.IssueSeverity.WARNING
        assert issue.translation_key == ISSUE_IDENTITY_UNCONFIRMED
        assert "identity continuity" in caplog.text.lower()

    async def test_notice_is_cleared_once_continuity_holds(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A resolved question must not leave a stale repair."""
        entry = _make_entry(entry_id="entry-clear")
        entry.add_to_hass(hass)
        guard = ReservationIdentityGuard(hass, entry.entry_id)
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_LEGACY,
                continuity=CONTINUITY_UNCONFIRMED,
                bootstrap_attempted=True,
            ),
        )
        async_notify_identity_continuity(hass, entry, guard)

        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_V3,
                continuity=CONTINUITY_VERIFIED,
                bootstrap_attempted=True,
                codes={CODE_A: "res-1"},
            ),
        )
        async_notify_identity_continuity(hass, entry, guard)

        assert (
            ir.async_get(hass).async_get_issue(
                DOMAIN,
                f"{ISSUE_IDENTITY_UNCONFIRMED}_{entry.entry_id}",
            )
            is None
        )

    async def test_no_notice_on_the_legacy_path(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A legacy entry has no cross-path question to report."""
        entry = _make_entry(
            entry_id="entry-nolegacy",
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        entry.add_to_hass(hass)
        guard = ReservationIdentityGuard(hass, entry.entry_id)
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_LEGACY,
                continuity=CONTINUITY_UNCONFIRMED,
            ),
        )

        async_notify_identity_continuity(hass, entry, guard)

        assert (
            ir.async_get(hass).async_get_issue(
                DOMAIN,
                f"{ISSUE_IDENTITY_UNCONFIRMED}_{entry.entry_id}",
            )
            is None
        )

    async def test_repeated_notice_warns_only_once(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A standing question must not spam every refresh."""
        entry = _make_entry(entry_id="entry-once-warn")
        entry.add_to_hass(hass)
        guard = ReservationIdentityGuard(hass, entry.entry_id)
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_LEGACY,
                continuity=CONTINUITY_UNCONFIRMED,
                bootstrap_attempted=True,
            ),
        )

        with caplog.at_level(logging.WARNING):
            async_notify_identity_continuity(hass, entry, guard)
            caplog.clear()
            async_notify_identity_continuity(hass, entry, guard)

        assert "identity continuity" not in caplog.text.lower()
        assert (
            ir.async_get(hass).async_get_issue(
                DOMAIN,
                f"{ISSUE_IDENTITY_UNCONFIRMED}_{entry.entry_id}",
            )
            is not None
        )

    async def test_later_refreshes_update_the_notice(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Continuity transitions after setup must reach the user."""
        entry = _make_entry(entry_id="entry-listener")
        entry.add_to_hass(hass)
        guard = ReservationIdentityGuard(hass, entry.entry_id)
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_V3,
                continuity=CONTINUITY_VERIFIED,
                bootstrap_attempted=True,
            ),
        )
        coordinator = MagicMock()
        coordinator.identity = guard
        unsub = MagicMock()
        coordinator.async_add_listener = MagicMock(return_value=unsub)

        async_track_identity_continuity(hass, entry, coordinator)

        assert coordinator.async_add_listener.call_count == 1
        listener = coordinator.async_add_listener.call_args.args[0]
        issue_id = f"{ISSUE_IDENTITY_UNCONFIRMED}_{entry.entry_id}"
        registry = ir.async_get(hass)
        assert registry.async_get_issue(DOMAIN, issue_id) is None

        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_V3,
                continuity=CONTINUITY_UNCONFIRMED,
                bootstrap_attempted=True,
            ),
        )
        listener()

        assert registry.async_get_issue(DOMAIN, issue_id) is not None

    async def test_removing_the_entry_withdraws_the_notice(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A deleted entry must not leave an unclearable repair."""
        entry = _make_entry(entry_id="entry-removed")
        entry.add_to_hass(hass)
        guard = ReservationIdentityGuard(hass, entry.entry_id)
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_LEGACY,
                continuity=CONTINUITY_UNCONFIRMED,
                bootstrap_attempted=True,
            ),
        )
        async_notify_identity_continuity(hass, entry, guard)
        issue_id = f"{ISSUE_IDENTITY_UNCONFIRMED}_{entry.entry_id}"
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

        await async_remove_entry(hass, entry)

        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


class TestFallbackCodeIsolation:
    """Evidence from the legacy fallback is kept apart (T123)."""

    async def test_fallback_code_never_lands_in_codes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A fallback-sourced code is recorded separately."""
        guard = ReservationIdentityGuard(hass, "entry-fb-isolation")
        await guard.async_load()

        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[
                _reservation("res-1", CODE_A),
                _reservation("res-2", CODE_B),
            ],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
            fallback_ids=("res-2",),
        )

        baseline = guard.baseline
        assert baseline is not None
        assert baseline.codes == {CODE_A: "res-1"}
        assert baseline.fallback_codes == {CODE_B: "res-2"}
        assert baseline.known_ids() == {"res-1", "res-2"}
        assert baseline.current_ids() == {"res-1", "res-2"}

    async def test_fallback_alone_cannot_verify_continuity(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Fallback evidence is not a v3 confirmation."""
        guard = ReservationIdentityGuard(hass, "entry-fb-continuity")
        await guard.async_load()
        await guard.async_record_refresh(
            path=RESERVATIONS_API_LEGACY,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
        )

        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-02T00:00:00+00:00",
            fallback_ids=("res-1",),
        )

        baseline = guard.baseline
        assert baseline is not None
        assert baseline.codes == {}
        assert baseline.continuity != CONTINUITY_VERIFIED

    async def test_conflicting_fallback_code_aborts_the_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A changed fallback mapping is an identity conflict."""
        guard = ReservationIdentityGuard(hass, "entry-fb-conflict")
        await guard.async_load()
        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
            fallback_ids=("res-1",),
        )
        assert guard.baseline is not None
        assert guard.baseline.fallback_codes == {CODE_A: "res-1"}

        comparison = guard.compare(
            [_reservation("res-9", CODE_A)],
            RESERVATIONS_API_V3,
        )

        assert comparison.is_ambiguous
        assert comparison.conflicts

    async def test_fallback_match_cannot_confirm_continuity(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A legacy-sourced observation is not v3 evidence."""
        guard = ReservationIdentityGuard(hass, "entry-fb-cross-path")
        await guard.async_load()
        await guard.async_record_refresh(
            path=RESERVATIONS_API_LEGACY,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
        )

        comparison = guard.compare(
            [_reservation("res-1", CODE_A)],
            RESERVATIONS_API_V3,
            fallback_ids=("res-1",),
        )
        assert comparison.cross_path_matched == 0
        assert not comparison.is_ambiguous

        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=comparison,
            updated="2026-01-02T00:00:00+00:00",
            fallback_ids=("res-1",),
        )

        assert guard.baseline is not None
        assert guard.baseline.continuity != CONTINUITY_VERIFIED

    async def test_v3_match_still_confirms_continuity(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Excluding fallback evidence keeps genuine matches."""
        guard = ReservationIdentityGuard(hass, "entry-fb-cross-ok")
        await guard.async_load()
        await guard.async_record_refresh(
            path=RESERVATIONS_API_LEGACY,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
        )

        comparison = guard.compare(
            [_reservation("res-1", CODE_A)],
            RESERVATIONS_API_V3,
        )

        assert comparison.cross_path_matched == 1

    async def test_fallback_conflict_is_still_detected(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Excluding confirmation never hides a disagreement."""
        guard = ReservationIdentityGuard(hass, "entry-fb-cross-conflict")
        await guard.async_load()
        await guard.async_record_refresh(
            path=RESERVATIONS_API_LEGACY,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
        )

        comparison = guard.compare(
            [_reservation("res-9", CODE_A)],
            RESERVATIONS_API_V3,
            fallback_ids=("res-9",),
        )

        assert comparison.is_ambiguous
        assert comparison.conflicts
        assert not comparison.cross_path_conflicts
        assert comparison.cross_path_matched == 0

    async def test_confirmed_absence_retires_candidacy(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A reservation legacy denies stops being a candidate."""
        guard = ReservationIdentityGuard(hass, "entry-fb-absent")
        await guard.async_load()
        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[
                _reservation("res-1", CODE_A),
                _reservation("res-2", CODE_B),
            ],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-01T00:00:00+00:00",
        )
        assert guard.baseline is not None
        assert "res-2" in guard.baseline.candidates

        await guard.async_record_refresh(
            path=RESERVATIONS_API_V3,
            reservations=[_reservation("res-1", CODE_A)],
            published={},
            comparison=IdentityComparison(),
            updated="2026-01-02T00:00:00+00:00",
            absent_ids=("res-2",),
        )

        assert guard.baseline is not None
        assert "res-2" not in guard.baseline.candidates


# ── Feature 014: read-only identity conflict query (T014) ───────────

_MAP_NAMES = ("codes", "legacy_codes", "bootstrap_codes", "fallback_codes")


async def _stored_guard(
    hass: HomeAssistant,
    **maps: dict[str, str] | None,
) -> ReservationIdentityGuard:
    """Build a loaded guard holding a stored baseline.

    Args:
        hass: Home Assistant instance.
        **maps: Baseline code maps to set.

    Returns:
        The loaded guard.
    """
    guard = ReservationIdentityGuard(hass, "entry-conflict-query")
    await guard.async_load()
    await guard.async_store(
        IdentityBaseline(
            path=RESERVATIONS_API_V3,
            continuity=CONTINUITY_VERIFIED,
            **maps,  # type: ignore[arg-type]
        ),
    )
    return guard


class TestIdentityConflictQuery:
    """The read-only identity conflict query (FR-006, FR-009)."""

    @pytest.mark.parametrize("map_name", _MAP_NAMES)
    async def test_code_mapped_to_other_id_conflicts(
        self, hass: HomeAssistant, map_name: str
    ) -> None:
        """A by-ID code already naming another ID is a conflict."""
        guard = await _stored_guard(hass, **{map_name: {CODE_A: "res-other"}})

        assert guard.check_identity_conflict("res-1", CODE_A) == "conflict"

    @pytest.mark.parametrize("map_name", _MAP_NAMES)
    async def test_target_mapped_to_other_code_conflicts(
        self, hass: HomeAssistant, map_name: str
    ) -> None:
        """A different code already naming the target is a conflict."""
        guard = await _stored_guard(hass, **{map_name: {CODE_B: "res-1"}})

        assert guard.check_identity_conflict("res-1", CODE_A) == "conflict"

    async def test_same_id_same_code_is_consistent(self, hass: HomeAssistant) -> None:
        """The same pairing in every map is consistent."""
        guard = await _stored_guard(
            hass,
            **{name: {CODE_A: "res-1"} for name in _MAP_NAMES},
        )

        assert guard.check_identity_conflict("res-1", CODE_A) == "none"

    async def test_unmapped_code_is_acceptable(self, hass: HomeAssistant) -> None:
        """A code no map knows, on an unmapped target, is acceptable."""
        guard = await _stored_guard(hass, codes={CODE_B: "res-2"})

        assert guard.check_identity_conflict("res-1", CODE_A) == "none"

    async def test_none_sub_maps_count_as_empty(self, hass: HomeAssistant) -> None:
        """A None legacy, bootstrap or fallback map is no evidence."""
        guard = await _stored_guard(
            hass,
            codes={CODE_B: "res-2"},
            legacy_codes=None,
            bootstrap_codes=None,
            fallback_codes=None,
        )

        assert guard.check_identity_conflict("res-1", CODE_A) == "none"

    async def test_unloaded_unusable_or_unstored_baseline_is_unavailable(
        self, hass: HomeAssistant
    ) -> None:
        """A baseline that cannot answer gives unavailable, not none."""
        unloaded = ReservationIdentityGuard(hass, "entry-unloaded")
        assert unloaded.check_identity_conflict("res-1", CODE_A) == "unavailable"

        empty = ReservationIdentityGuard(hass, "entry-empty")
        await empty.async_load()
        assert empty.check_identity_conflict("res-1", CODE_A) == "unavailable"

        unusable = await _stored_guard(hass)
        assert unusable.check_identity_conflict("res-1", CODE_A) == "unavailable"

        unstored = await _stored_guard(hass, codes={CODE_B: "res-2"})
        unstored._unstored = True
        assert unstored.check_identity_conflict("res-1", CODE_A) == "unavailable"

        ambiguous = await _stored_guard(hass, codes={CODE_B: "res-2"})
        ambiguous.mark_ambiguous()
        assert ambiguous.check_identity_conflict("res-1", CODE_A) == "unavailable"

    async def test_query_mutates_nothing(self, hass: HomeAssistant) -> None:
        """The query changes no baseline state and saves nothing."""
        guard = await _stored_guard(
            hass,
            codes={CODE_A: "res-other"},
            legacy_codes={CODE_B: "res-1"},
            bootstrap_codes={},
            fallback_codes=None,
        )
        baseline = guard.baseline
        assert baseline is not None
        baseline.candidates["res-1"] = CandidateHint(timing_status="unknown")
        before = baseline.to_dict()
        flags = (guard._unstored, guard._ambiguous, guard._loaded)

        with patch.object(guard._store, "async_save") as save:
            for target, code in (("res-1", CODE_A), ("res-9", "CONF-ZZZ")):
                guard.check_identity_conflict(target, code)

        assert save.call_count == 0
        assert guard.baseline is baseline
        assert baseline.to_dict() == before
        assert (guard._unstored, guard._ambiguous, guard._loaded) == flags


# ── Feature 013: conflict-only identity evidence (T015-T018) ────────

_EVIDENCE_NOW = datetime(2026, 3, 1, 12, tzinfo=UTC)
_IDENTITY_LOGGER = "custom_components.guesty.identity"


def _pair(reservation_id: str, code: str) -> RejectedCode:
    """Build one rejected ``(reservation_id, code)`` pair.

    Args:
        reservation_id: The rejected record's identifier.
        code: Its confirmation code.

    Returns:
        The rejected pair.
    """
    return RejectedCode(reservation_id, code)


def _evidence(
    hass: HomeAssistant,
    entry_id: str = "entry-evidence",
    past_days: int = 30,
) -> ConflictOnlyEvidence:
    """Build an unloaded conflict-only evidence store.

    Args:
        hass: Home Assistant instance.
        entry_id: The owning config entry.
        past_days: The configured past-day window.

    Returns:
        The evidence store.
    """
    return ConflictOnlyEvidence(hass, entry_id, past_days=lambda: past_days)


async def _ready_evidence(
    evidence: ConflictOnlyEvidence,
    past_days: int = 30,
) -> None:
    """Load an evidence store if needed and adopt one saved generation.

    A store already loaded keeps its unsaved pairs, which this save
    then persists.

    Args:
        evidence: The evidence store.
        past_days: The window the save covers.
    """
    if evidence.state == EVIDENCE_UNLOADED:
        await evidence.async_load(0)
    evidence.advance(past_days)
    assert await evidence.async_save()
    evidence.adopt()


def _entry_payload(**overrides: Any) -> dict[str, Any]:
    """Build one well-formed persisted evidence entry.

    Args:
        **overrides: Fields to replace.

    Returns:
        The entry payload.
    """
    entry: dict[str, Any] = {
        "code": CODE_A,
        "reservation_id": "res-1",
        "path": RESERVATIONS_API_V3,
        "fallback": False,
        "observed_at": "2026-01-01T00:00:00+00:00",
        "check_out_bound": "2026-01-10",
    }
    entry.update(overrides)
    return entry


def _evidence_payload(
    entries: Any = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a persisted evidence payload.

    Args:
        entries: The entries, possibly malformed; one well-formed
            entry when None.
        **overrides: Top-level fields to replace.

    Returns:
        The payload.
    """
    payload: dict[str, Any] = {
        "entries": [_entry_payload()] if entries is None else entries,
        "saturated": False,
        "generation": 1,
        "covered_past_days": 30,
    }
    payload.update(overrides)
    return payload


def _preset(
    hass_storage: dict[str, Any],
    key: str,
    data: Any,
    *,
    minor_version: int = 1,
) -> None:
    """Place a payload in Home Assistant storage before a load.

    Args:
        hass_storage: The storage fixture.
        key: The storage key.
        data: The stored payload.
        minor_version: The stored minor version.
    """
    hass_storage[key] = {
        "version": 1,
        "minor_version": minor_version,
        "key": key,
        "data": data,
    }


_REAL_WRITE_DATA = Store._async_write_data


@contextmanager
def _disk_full(store: Store[Any]) -> Iterator[None]:
    """Make one store's real write fail as a full disk does.

    The test harness replaces ``Store._async_write_data`` with an
    in-memory writer for every store. Only the target store is sent
    down Home Assistant's real write path, where the file layer under
    ``write_utf8_file`` raises ``OSError``. Home Assistant turns that
    into a ``WriteError`` and only logs it, so ``Store.async_save``
    itself returns normally. Nothing reaches the disk.

    Args:
        store: The store whose writes fail.

    Yields:
        None, while the store's writes fail.
    """
    harness_write = Store._async_write_data

    async def _write(self: Store[Any], data: dict[str, Any]) -> None:
        """Route the target store to the real writer, others to the mock."""
        if self is store:
            await _REAL_WRITE_DATA(self, data)
            return
        await harness_write(self, data)

    with (
        patch.object(Store, "_async_write_data", _write),
        patch.dict(store.__dict__, {"path": __file__ + ".unwritable"}),
        patch(
            "homeassistant.util.file.tempfile.NamedTemporaryFile",
            side_effect=OSError(28, "No space left on device"),
        ),
    ):
        yield


async def _accept(
    guard: ReservationIdentityGuard,
    reservations: list[GuestyReservation],
    *,
    path: str = RESERVATIONS_API_LEGACY,
    past_days: int = 30,
    rejected_codes: tuple[RejectedCode, ...] = (),
    **fields: Any,
) -> bool:
    """Run one accepted refresh through the guard.

    Args:
        guard: A loaded guard.
        reservations: The fully verified records of the refresh.
        path: The reservation path of the refresh.
        past_days: The configured past-day window.
        rejected_codes: The refresh's rejected pairs.
        **fields: Further ``async_record_refresh`` arguments.

    Returns:
        Whether the identity baseline was stored.
    """
    comparison = guard.compare(
        reservations,
        path,
        fields.get("fallback_ids", ()),
        conflict_only=rejected_codes,
    )
    assert not comparison.is_ambiguous
    return await guard.async_record_refresh(
        path=path,
        reservations=reservations,
        published={},
        comparison=comparison,
        updated=datetime.now(UTC).isoformat(),
        past_days=past_days,
        rejected_codes=rejected_codes,
        **fields,
    )


async def _ready_guard(
    hass: HomeAssistant,
    entry_id: str = "entry-ready",
    *,
    past_days: int = 30,
) -> ReservationIdentityGuard:
    """Build a guard whose evidence is ready after one refresh.

    Args:
        hass: Home Assistant instance.
        entry_id: The owning config entry.
        past_days: The configured past-day window.

    Returns:
        A guard that permits writes to ``res-1``.
    """
    guard = ReservationIdentityGuard(hass, entry_id, past_days=lambda: past_days)
    await guard.async_load()
    assert await _accept(guard, [_reservation("res-1", CODE_A)], past_days=past_days)
    return guard


class TestConflictOnlyObservation:
    """Rejected pairs feed conflict detection only (T015, FR-008b)."""

    def test_rejected_pairs_sharing_a_code_collide(self) -> None:
        """Two rejected records naming one code are ambiguous."""
        observed = observe_codes(
            [],
            conflict_only=[_pair("res-1", CODE_A), _pair("res-2", CODE_A)],
        )

        assert observed.collisions == (("res-1", "res-2"),)

    def test_rejected_and_accepted_sharing_a_code_collide(self) -> None:
        """A rejected record cannot share an accepted record's code."""
        observed = observe_codes(
            [_reservation("res-1", CODE_A)],
            conflict_only=[_pair("res-2", CODE_A)],
        )

        assert observed.collisions == (("res-1", "res-2"),)
        assert observed.mapping == {CODE_A: "res-1"}

    def test_conflict_only_map_is_returned_apart(self) -> None:
        """Rejected pairs never enter the accepted mapping."""
        observed = observe_codes(
            [_reservation("res-1", CODE_A)],
            conflict_only=[_pair("res-2", CODE_B), _pair("res-1", CODE_A)],
        )

        assert observed.collisions == ()
        assert observed.mapping == {CODE_A: "res-1"}
        assert observed.conflict_only == {CODE_B: "res-2", CODE_A: "res-1"}

    def test_same_path_stored_conflict_aborts(self) -> None:
        """A rejected record re-keying a stored code aborts."""
        baseline = IdentityBaseline(path=RESERVATIONS_API_V3, codes={CODE_A: "res-1"})

        comparison = baseline.compare(
            {}, RESERVATIONS_API_V3, conflict_only={CODE_A: "res-2"}
        )

        assert comparison.is_ambiguous is True
        assert comparison.conflicts[0].observed_id == "res-2"

    def test_cross_path_conflict_stays_soft(self) -> None:
        """Carried-over evidence disagreeing is only divergence."""
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_LEGACY,
            codes={CODE_A: "res-1"},
        )

        comparison = baseline.compare(
            {}, RESERVATIONS_API_V3, conflict_only={CODE_A: "res-2"}
        )

        assert comparison.is_ambiguous is False
        assert len(comparison.cross_path_conflicts) == 1

    def test_conflict_only_pairs_never_match(self) -> None:
        """Agreement from a rejected record proves nothing."""
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_V3,
            codes={CODE_A: "res-1"},
            legacy_codes={CODE_A: "res-1"},
        )

        comparison = baseline.compare(
            {}, RESERVATIONS_API_V3, conflict_only={CODE_A: "res-1"}
        )

        assert comparison.matched == 0
        assert comparison.cross_path_matched == 0
        assert comparison.conflicts == ()

    def test_fallback_observation_stays_hard(self) -> None:
        """A fallback-invalid ID cannot soften a disagreement."""
        baseline = IdentityBaseline(
            path=RESERVATIONS_API_LEGACY,
            codes={CODE_A: "res-1"},
        )

        comparison = baseline.compare(
            {},
            RESERVATIONS_API_V3,
            fallback_ids={"res-2"},
            conflict_only={CODE_A: "res-2"},
        )

        assert comparison.is_ambiguous is True

    async def test_rejected_pairs_never_enter_the_baseline(
        self, hass: HomeAssistant
    ) -> None:
        """A rejected pair is never current or known identity."""
        guard = ReservationIdentityGuard(hass, "entry-co-baseline")
        await guard.async_load()

        await _accept(
            guard,
            [_reservation("res-1", CODE_A)],
            rejected_codes=(_pair("res-9", CODE_B),),
        )

        baseline = guard.baseline
        assert baseline is not None
        assert CODE_B not in baseline.codes
        assert "res-9" not in baseline.current_ids()
        assert "res-9" not in baseline.known_ids()

    async def test_stored_fallback_pair_is_hard_and_never_confirms(
        self, hass: HomeAssistant
    ) -> None:
        """A stored fallback pair is neither soft nor confirming."""
        evidence = _evidence(hass)
        await evidence.async_load(0)
        evidence.record(
            [_pair("res-1", CODE_A), _pair("res-2", CODE_B)],
            {},
            RESERVATIONS_API_LEGACY,
            _EVIDENCE_NOW,
            {"res-1"},
        )

        maps = {
            (pair, stored.soft, stored.confirms)
            for stored in evidence.compare_maps(RESERVATIONS_API_V3)
            for pair in stored.pairs
        }

        assert maps == {
            ((CODE_A, "res-1"), False, False),
            ((CODE_B, "res-2"), True, False),
        }
        assert all(
            stored.source == SOURCE_CONFLICT_ONLY
            for stored in evidence.compare_maps(RESERVATIONS_API_V3)
        )

    async def test_stored_code_with_several_ids_checks_each(
        self, hass: HomeAssistant
    ) -> None:
        """Every stored ID of a shared code is compared."""
        guard = ReservationIdentityGuard(hass, "entry-co-shared")
        await guard.async_load()
        guard.conflict_evidence.record(
            [_pair("res-1", CODE_A), _pair("res-2", CODE_A)],
            {},
            RESERVATIONS_API_V3,
            _EVIDENCE_NOW,
            (),
        )

        comparison = guard.compare([_reservation("res-1", CODE_A)], RESERVATIONS_API_V3)

        assert comparison.is_ambiguous is True
        assert [
            (c.known_id, c.observed_id, c.source) for c in comparison.conflicts
        ] == [("res-2", "res-1", SOURCE_CONFLICT_ONLY)]
        assert comparison.matched == 0

    async def test_stored_pair_checks_rejected_observations(
        self, hass: HomeAssistant
    ) -> None:
        """A stored pair conflicts with a later rejected record too."""
        guard = ReservationIdentityGuard(hass, "entry-co-rejected")
        await guard.async_load()
        guard.conflict_evidence.record(
            [_pair("res-1", CODE_A)], {}, RESERVATIONS_API_V3, _EVIDENCE_NOW, ()
        )

        comparison = guard.compare(
            [], RESERVATIONS_API_V3, conflict_only=[_pair("res-2", CODE_A)]
        )

        assert comparison.is_ambiguous is True

    async def test_pair_seen_on_both_paths_is_hard_on_each(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """A pair re-observed on v3 is same-path evidence for v3 (R7)."""
        guard = ReservationIdentityGuard(hass, "entry-co-paths")
        await guard.async_load()
        rejected = (_pair("res-9", CODE_B),)
        assert await _accept(
            guard,
            [_reservation("res-1", CODE_A)],
            path=RESERVATIONS_API_LEGACY,
            rejected_codes=rejected,
        )
        assert await _accept(
            guard,
            [_reservation("res-1", CODE_A)],
            path=RESERVATIONS_API_V3,
            rejected_codes=rejected,
        )
        later = [_reservation("res-2", CODE_B)]

        assert guard.compare(later, RESERVATIONS_API_V3).is_ambiguous is True
        assert guard.compare(later, RESERVATIONS_API_LEGACY).is_ambiguous is True
        stored = hass_storage[conflict_evidence_storage_key("entry-co-paths")]
        (entry,) = stored["data"]["entries"]
        assert entry["path"] == RESERVATIONS_API_LEGACY
        assert entry["paths"] == sorted([RESERVATIONS_API_LEGACY, RESERVATIONS_API_V3])

        restarted = ReservationIdentityGuard(hass, "entry-co-paths")
        await restarted.async_load()
        assert restarted.compare(later, RESERVATIONS_API_V3).is_ambiguous is True

    async def test_pair_seen_on_one_path_stays_soft_on_the_other(
        self, hass: HomeAssistant
    ) -> None:
        """A pair only ever recorded on legacy is still cross-path for v3."""
        guard = ReservationIdentityGuard(hass, "entry-co-one-path")
        await guard.async_load()
        assert await _accept(
            guard,
            [_reservation("res-1", CODE_A)],
            path=RESERVATIONS_API_LEGACY,
            rejected_codes=(_pair("res-9", CODE_B),),
        )

        comparison = guard.compare([_reservation("res-2", CODE_B)], RESERVATIONS_API_V3)

        assert comparison.is_ambiguous is False
        assert len(comparison.cross_path_conflicts) == 1


class TestConflictOnlyEvidenceStore:
    """The persisted conflict-only evidence store (T016, FR-008a/c)."""

    async def test_store_key_and_payload_shape(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """The separate version-1 store holds the documented payload."""
        evidence = _evidence(hass, "entry-shape")
        await evidence.async_load(0)
        evidence.record(
            [_pair("res-1", CODE_A)],
            {"res-1": "2026-01-10"},
            RESERVATIONS_API_V3,
            _EVIDENCE_NOW,
            (),
        )
        evidence.advance(30)
        assert await evidence.async_save()

        key = conflict_evidence_storage_key("entry-shape")
        assert key == "guesty.entry-shape.reservations_conflict_evidence"
        assert hass_storage[key]["version"] == CONFLICT_EVIDENCE_STORAGE_VERSION == 1
        assert hass_storage[key]["data"] == {
            "entries": [
                {
                    "code": CODE_A,
                    "reservation_id": "res-1",
                    "path": RESERVATIONS_API_V3,
                    "paths": [RESERVATIONS_API_V3],
                    "fallback": False,
                    "observed_at": _EVIDENCE_NOW.isoformat(),
                    "check_out_bound": "2026-01-10",
                }
            ],
            "saturated": False,
            "generation": 1,
            "covered_past_days": 30,
        }

    async def test_record_keeps_shared_codes_and_several_codes(
        self, hass: HomeAssistant
    ) -> None:
        """Every pair is kept, by code and by ID."""
        evidence = _evidence(hass)
        await evidence.async_load(0)
        evidence.record(
            [_pair("res-1", CODE_A), _pair("res-2", CODE_A), _pair("res-1", CODE_B)],
            {},
            RESERVATIONS_API_V3,
            _EVIDENCE_NOW,
            (),
        )
        evidence.advance(30)
        assert await evidence.async_save()
        evidence.adopt()

        assert evidence.count == 3
        assert evidence.ids_for(CODE_A) == frozenset({"res-1", "res-2"})
        assert evidence.codes_for("res-1") == frozenset({CODE_A, CODE_B})

    async def test_fallback_only_ever_becomes_true(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """A pair once seen through the fallback stays fallback."""
        evidence = _evidence(hass, "entry-fallback-flag")
        await evidence.async_load(0)
        for fallback_ids in ({"res-1"}, set()):
            evidence.record(
                [_pair("res-1", CODE_A)],
                {},
                RESERVATIONS_API_V3,
                _EVIDENCE_NOW,
                fallback_ids,
            )
        evidence.advance(30)
        await evidence.async_save()

        key = conflict_evidence_storage_key("entry-fallback-flag")
        assert hass_storage[key]["data"]["entries"][0]["fallback"] is True

    async def test_bound_moves_only_later_or_to_null(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """A re-observed pair's bound never moves earlier."""
        evidence = _evidence(hass, "entry-bounds")
        await evidence.async_load(0)
        key = conflict_evidence_storage_key("entry-bounds")
        expected = (
            ("2026-01-10", "2026-01-10"),
            ("2026-01-05", "2026-01-10"),
            ("2026-01-20", "2026-01-20"),
            (None, None),
            ("2026-02-01", None),
        )
        for bound, kept in expected:
            evidence.record(
                [_pair("res-1", CODE_A)],
                {} if bound is None else {"res-1": bound},
                RESERVATIONS_API_V3,
                _EVIDENCE_NOW,
                (),
            )
            evidence.advance(30)
            await evidence.async_save()
            assert hass_storage[key]["data"]["entries"][0]["check_out_bound"] == kept

    async def test_retire_by_supersession_and_absence(
        self, hass: HomeAssistant
    ) -> None:
        """Verified or confirmed-absent IDs drop every pair."""
        evidence = _evidence(hass)
        await evidence.async_load(0)
        evidence.record(
            [_pair("res-1", CODE_A), _pair("res-1", CODE_B), _pair("res-2", CODE_B)],
            {},
            RESERVATIONS_API_V3,
            _EVIDENCE_NOW,
            (),
        )
        evidence.retire(
            superseded={"res-1"},
            absent=(),
            returned=(),
            past_days=30,
            now=_EVIDENCE_NOW,
        )
        assert evidence.count == 1
        evidence.retire(
            superseded=(),
            absent={"res-2"},
            returned=(),
            past_days=30,
            now=_EVIDENCE_NOW,
        )
        assert evidence.count == 0

    async def test_retire_by_horizon(self, hass: HomeAssistant) -> None:
        """Only a dated, unreturned pair beyond the horizon retires."""
        evidence = _evidence(hass)
        await evidence.async_load(0)
        # now - 30 days is 2026-01-30, so the horizon date is 2026-01-29.
        evidence.record(
            [
                _pair("old", CODE_A),
                _pair("edge", CODE_B),
                _pair("returned", "CONF-RET"),
                _pair("undated", "CONF-UND"),
            ],
            {
                "old": "2026-01-28",
                "edge": "2026-01-29",
                "returned": "2025-01-01",
            },
            RESERVATIONS_API_V3,
            _EVIDENCE_NOW,
            (),
        )

        evidence.retire(
            superseded=(),
            absent=(),
            returned={"returned"},
            past_days=30,
            now=_EVIDENCE_NOW,
        )
        await _ready_evidence(evidence)

        assert evidence.ids_for(CODE_A) == frozenset()
        assert evidence.ids_for(CODE_B) == frozenset({"edge"})
        assert evidence.ids_for("CONF-RET") == frozenset({"returned"})
        assert evidence.ids_for("CONF-UND") == frozenset({"undated"})
        assert (evidence.dated, evidence.undated) == (2, 1)

    async def test_saturation_is_sticky_and_persisted(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A full store never evicts, and stays saturated."""
        evidence = _evidence(hass, "entry-saturated")
        await evidence.async_load(0)
        full = [
            _pair(f"res-{i}", f"CODE-{i}") for i in range(MAX_CONFLICT_ONLY_EVIDENCE)
        ]
        assert evidence.record(full, {}, RESERVATIONS_API_V3, _EVIDENCE_NOW, ())

        with caplog.at_level(logging.WARNING, logger=_IDENTITY_LOGGER):
            added = evidence.record(
                [_pair("res-new", "CODE-NEW"), _pair("res-0", "CODE-0")],
                {},
                RESERVATIONS_API_V3,
                _EVIDENCE_NOW,
                (),
            )
        evidence.retire(
            superseded={"res-1"},
            absent=(),
            returned=(),
            past_days=30,
            now=_EVIDENCE_NOW,
        )
        evidence.advance(30)
        await evidence.async_save()

        assert added is False
        assert evidence.count == MAX_CONFLICT_ONLY_EVIDENCE - 1
        assert evidence.state == EVIDENCE_SATURATED
        assert (
            hass_storage[conflict_evidence_storage_key("entry-saturated")]["data"][
                "saturated"
            ]
            is True
        )
        reloaded = _evidence(hass, "entry-saturated")
        await reloaded.async_load(1)
        assert reloaded.state == EVIDENCE_SATURATED
        assert "CODE-NEW" not in caplog.text

    async def test_near_capacity_thresholds(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The near-capacity flag sets at 80% and clears below 70%."""
        evidence = _evidence(hass)
        await evidence.async_load(0)
        high = MAX_CONFLICT_ONLY_EVIDENCE * 4 // 5
        low = MAX_CONFLICT_ONLY_EVIDENCE * 7 // 10
        evidence.record(
            [_pair(f"res-{i}", f"C-{i}") for i in range(high - 1)],
            {},
            RESERVATIONS_API_V3,
            _EVIDENCE_NOW,
            (),
        )
        assert evidence.near_capacity is False

        with caplog.at_level(logging.WARNING, logger=_IDENTITY_LOGGER):
            evidence.record(
                [_pair("res-x", "C-x"), _pair("res-y", "C-y")],
                {},
                RESERVATIONS_API_V3,
                _EVIDENCE_NOW,
                (),
            )
        assert evidence.near_capacity is True
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

        def _retire_to(count: int) -> None:
            """Retire the oldest pairs until ``count`` remain."""
            excess = high + 1 - count
            evidence.retire(
                superseded={f"res-{i}" for i in range(excess)},
                absent=(),
                returned=(),
                past_days=30,
                now=_EVIDENCE_NOW,
            )

        _retire_to(low)
        assert evidence.near_capacity is True
        _retire_to(low - 1)
        assert evidence.near_capacity is False

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param([], id="not-a-dict"),
            pytest.param(_evidence_payload(entries={}), id="entries-not-list"),
            pytest.param(_evidence_payload(entries=["x"]), id="entry-not-dict"),
            pytest.param(_evidence_payload([_entry_payload(code="")]), id="empty-code"),
            pytest.param(_evidence_payload([_entry_payload(code=5)]), id="int-code"),
            pytest.param(
                _evidence_payload([_entry_payload(reservation_id="")]), id="empty-id"
            ),
            pytest.param(
                _evidence_payload([_entry_payload(path="other")]), id="unknown-path"
            ),
            pytest.param(
                _evidence_payload([_entry_payload(paths=RESERVATIONS_API_V3)]),
                id="paths-not-list",
            ),
            pytest.param(
                _evidence_payload([_entry_payload(paths=[])]), id="paths-empty"
            ),
            pytest.param(
                _evidence_payload([_entry_payload(paths=[RESERVATIONS_API_LEGACY])]),
                id="path-not-in-paths",
            ),
            pytest.param(
                _evidence_payload(
                    [_entry_payload(paths=[RESERVATIONS_API_V3, "other"])]
                ),
                id="paths-unknown",
            ),
            pytest.param(
                _evidence_payload(
                    [_entry_payload(paths=[RESERVATIONS_API_V3, RESERVATIONS_API_V3])]
                ),
                id="paths-duplicate",
            ),
            pytest.param(
                _evidence_payload([_entry_payload(fallback="no")]), id="fallback-type"
            ),
            pytest.param(
                _evidence_payload([_entry_payload(observed_at="nope")]),
                id="observed-unparsable",
            ),
            pytest.param(
                _evidence_payload([_entry_payload(observed_at="2026-01-01T00:00:00")]),
                id="observed-naive",
            ),
            pytest.param(
                _evidence_payload(
                    [_entry_payload(observed_at="9999-12-31T23:00:00-05:00")]
                ),
                id="observed-beyond-utc",
            ),
            pytest.param(
                _evidence_payload([_entry_payload(check_out_bound="2026-1-10")]),
                id="bound-not-canonical",
            ),
            pytest.param(
                _evidence_payload(
                    [_entry_payload(check_out_bound="2026-01-10T00:00:00+00:00")]
                ),
                id="bound-not-date",
            ),
            pytest.param(
                _evidence_payload([_entry_payload(), _entry_payload()]),
                id="duplicate-pair",
            ),
            pytest.param(_evidence_payload(covered_past_days="x"), id="covered-str"),
            pytest.param(_evidence_payload(covered_past_days=-1), id="covered-neg"),
            pytest.param(_evidence_payload(covered_past_days=True), id="covered-bool"),
            pytest.param(_evidence_payload(generation="1"), id="generation-str"),
            pytest.param(_evidence_payload(saturated="no"), id="saturated-type"),
        ],
    )
    async def test_malformed_store_is_compromised(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
        payload: Any,
    ) -> None:
        """A malformed store is never repaired by dropping entries."""
        _preset(hass_storage, conflict_evidence_storage_key("entry-bad"), payload)
        evidence = _evidence(hass, "entry-bad")

        await evidence.async_load(0)

        assert evidence.state == EVIDENCE_COMPROMISED
        assert evidence.available() is False

    @pytest.mark.parametrize("error", [OSError, ValueError])
    async def test_unreadable_store_is_compromised(
        self, hass: HomeAssistant, error: type[Exception]
    ) -> None:
        """A store that cannot be read is compromised."""
        evidence = _evidence(hass, "entry-unreadable")
        with patch.object(evidence._store, "async_load", AsyncMock(side_effect=error)):
            await evidence.async_load(0)

        assert evidence.state == EVIDENCE_COMPROMISED

    async def test_generation_below_expected_is_compromised(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """A lagging store is compromised but still compared."""
        _preset(
            hass_storage,
            conflict_evidence_storage_key("entry-lag"),
            _evidence_payload(generation=1),
        )
        evidence = _evidence(hass, "entry-lag")

        await evidence.async_load(2)

        assert evidence.state == EVIDENCE_COMPROMISED
        pairs = {p for m in evidence.compare_maps(RESERVATIONS_API_V3) for p in m.pairs}
        assert pairs == {(CODE_A, "res-1")}

    async def test_lagging_store_reports_its_capacity(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """A lagging store still compared also reports its occupancy."""
        _preset(
            hass_storage,
            conflict_evidence_storage_key("entry-lag-full"),
            _evidence_payload(
                [
                    _entry_payload(code=f"C-{i}", reservation_id=f"res-{i}")
                    for i in range(MAX_CONFLICT_ONLY_EVIDENCE * 4 // 5)
                ],
                generation=1,
            ),
        )
        evidence = _evidence(hass, "entry-lag-full")

        await evidence.async_load(2)

        assert evidence.state == EVIDENCE_COMPROMISED
        assert evidence.near_capacity is True

    async def test_missing_store_counts_as_generation_zero(
        self, hass: HomeAssistant
    ) -> None:
        """No store is fine only when none was expected."""
        fresh = _evidence(hass, "entry-missing-0")
        await fresh.async_load(0)
        lost = _evidence(hass, "entry-missing-1")
        await lost.async_load(1)

        assert fresh.state == EVIDENCE_REVALIDATING
        assert lost.state == EVIDENCE_COMPROMISED

    async def test_higher_generation_is_accepted(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """A store ahead of the baseline holds a superset."""
        _preset(
            hass_storage,
            conflict_evidence_storage_key("entry-ahead"),
            _evidence_payload(generation=5),
        )
        evidence = _evidence(hass, "entry-ahead")

        await evidence.async_load(2)

        assert evidence.state == EVIDENCE_REVALIDATING
        assert evidence.count == 1

    async def test_state_precedence(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Compromised, saturated, unstored, revalidating, ready."""
        unloaded = _evidence(hass, "entry-unloaded")
        assert unloaded.state == EVIDENCE_UNLOADED

        _preset(
            hass_storage,
            conflict_evidence_storage_key("entry-both"),
            _evidence_payload(saturated=True, generation=1),
        )
        both = _evidence(hass, "entry-both")
        await both.async_load(3)
        assert both.state == EVIDENCE_COMPROMISED

        saturated = _evidence(hass, "entry-sat-unstored")
        await saturated.async_load(0)
        saturated.record(
            [_pair(f"r-{i}", f"C-{i}") for i in range(MAX_CONFLICT_ONLY_EVIDENCE + 1)],
            {},
            RESERVATIONS_API_V3,
            _EVIDENCE_NOW,
            (),
        )
        saturated.advance(30)
        with patch.object(
            saturated._store, "async_save", AsyncMock(side_effect=OSError)
        ):
            assert await saturated.async_save() is False
        assert saturated.state == EVIDENCE_SATURATED

        unstored = _evidence(hass, "entry-unstored")
        await unstored.async_load(0)
        unstored.advance(30)
        with patch.object(
            unstored._store, "async_save", AsyncMock(side_effect=OSError)
        ):
            assert await unstored.async_save() is False
        assert unstored.state == EVIDENCE_UNSTORED
        assert await unstored.async_save() is True
        assert unstored.state == EVIDENCE_REVALIDATING
        unstored.adopt()
        assert unstored.state == EVIDENCE_READY

    async def test_lookups_answer_only_when_ready(self, hass: HomeAssistant) -> None:
        """Lookups raise unless the evidence is available."""
        evidence = _evidence(hass)
        for _ in range(2):
            assert evidence.available() is False
            with pytest.raises(RuntimeError):
                evidence.codes_for("res-1")
            with pytest.raises(RuntimeError):
                evidence.ids_for(CODE_A)
            await evidence.async_load(0)

        await _ready_evidence(evidence)
        assert evidence.available() is True
        assert evidence.codes_for("res-1") == frozenset()

    async def test_lookups_never_read_accepted_maps(self, hass: HomeAssistant) -> None:
        """Only persisted conflict-only pairs answer a lookup."""
        guard = await _ready_guard(hass, "entry-lookup-maps")
        evidence = guard.conflict_evidence

        assert evidence.available() is True
        assert evidence.codes_for("res-1") == frozenset()
        assert evidence.ids_for(CODE_A) == frozenset()

    async def test_async_remove_deletes_the_store(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Removing the entry deletes the evidence store."""
        evidence = _evidence(hass, "entry-remove")
        await _ready_evidence(evidence)
        key = conflict_evidence_storage_key("entry-remove")
        assert key in hass_storage

        await evidence.async_remove()

        assert key not in hass_storage

    async def test_listener_hears_state_changes(self, hass: HomeAssistant) -> None:
        """A listener is told when the state changes, until removed."""
        evidence = _evidence(hass)
        calls: list[str] = []
        remove = evidence.add_listener(lambda: calls.append(evidence.state))

        await _ready_evidence(evidence)
        remove()
        evidence.advance(30)

        assert calls == [EVIDENCE_REVALIDATING, EVIDENCE_READY]


class TestIdentityStoreMigration:
    """The identity store's minor-version 2 revalidation (T017)."""

    async def test_store_is_version_one_minor_two(self, hass: HomeAssistant) -> None:
        """The identity store migrates through ``_IdentityStore``."""
        guard = ReservationIdentityGuard(hass, "entry-minor")

        assert isinstance(guard._store, _IdentityStore)
        assert guard._store.version == STORAGE_VERSION == 1
        assert guard._store.minor_version == IDENTITY_STORAGE_MINOR_VERSION == 2

    async def test_migrate_func_sets_revalidation_pending(
        self, hass: HomeAssistant
    ) -> None:
        """A minor-version 1 payload is marked for revalidation."""
        guard = ReservationIdentityGuard(hass, "entry-migrate")

        migrated = await guard._store._async_migrate_func(
            1, 1, {"path": RESERVATIONS_API_V3}
        )

        assert migrated == {"path": RESERVATIONS_API_V3, "revalidation_pending": True}

    async def test_minor_one_payload_downgrades_every_known_hint(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Upgrade stops trusting stored timing until revalidated."""
        _preset(
            hass_storage,
            storage_key("entry-upgrade-hints"),
            {
                "path": RESERVATIONS_API_V3,
                "continuity": CONTINUITY_VERIFIED,
                "codes": {CODE_A: "res-1"},
                "candidates": {
                    "res-1": {
                        "timing_status": "known",
                        "check_in": "2026-01-01T00:00:00+00:00",
                        "check_out": "2026-01-04T00:00:00+00:00",
                        "observed_at": "2026-01-01T00:00:00+00:00",
                    },
                },
            },
        )
        guard = ReservationIdentityGuard(hass, "entry-upgrade-hints")

        await guard.async_load()

        baseline = guard.baseline
        assert baseline is not None
        assert baseline.revalidation_pending is True
        hint = baseline.candidates["res-1"]
        assert hint.timing_status == "unknown"
        assert hint.check_out is None
        assert hint.observed_at == "2026-01-01T00:00:00+00:00"
        assert hass_storage[storage_key("entry-upgrade-hints")]["minor_version"] == 2
        assert guard.write_refusal("res-1") == REFUSAL_REVALIDATING

    async def test_out_of_range_hint_instants_fail_closed(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Instants beyond the UTC range drop or downgrade their hint."""
        beyond = "9999-12-31T23:00:00-05:00"
        _preset(
            hass_storage,
            storage_key("entry-beyond-utc"),
            {
                "path": RESERVATIONS_API_V3,
                "continuity": CONTINUITY_VERIFIED,
                "codes": {CODE_A: "res-1"},
                "candidates": {
                    "res-1": {
                        "timing_status": "known",
                        "check_in": "2026-01-01T00:00:00+00:00",
                        "check_out": beyond,
                        "observed_at": "2026-01-01T00:00:00+00:00",
                    },
                    "res-2": {"timing_status": "unknown", "observed_at": beyond},
                },
            },
            minor_version=2,
        )
        guard = ReservationIdentityGuard(hass, "entry-beyond-utc")

        await guard.async_load()

        baseline = guard.baseline
        assert baseline is not None
        assert set(baseline.candidates) == {"res-1"}
        hint = baseline.candidates["res-1"]
        assert hint.timing_status == "unknown"
        assert hint.check_out is None

    async def test_migrated_bootstrap_pair_is_retained(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Downgraded timing can no longer retire bootstrap evidence."""
        old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
        older = (datetime.now(UTC) - timedelta(days=403)).isoformat()
        _preset(
            hass_storage,
            storage_key("entry-upgrade-boot"),
            {
                "path": RESERVATIONS_API_V3,
                "continuity": CONTINUITY_UNCONFIRMED,
                "codes": {CODE_A: "res-1"},
                "bootstrap_codes": {CODE_B: "boot-1"},
                "candidates": {
                    "boot-1": {
                        "timing_status": "known",
                        "check_in": older,
                        "check_out": old,
                        "observed_at": old,
                    },
                },
            },
        )
        guard = ReservationIdentityGuard(hass, "entry-upgrade-boot")
        await guard.async_load()

        await _accept(guard, [_reservation("res-1", CODE_A)], path=RESERVATIONS_API_V3)

        baseline = guard.baseline
        assert baseline is not None
        assert baseline.bootstrap_codes == {CODE_B: "boot-1"}

    def test_minor_two_payload_keeps_valid_timing(self) -> None:
        """A revalidated payload keeps only provably valid hints."""
        baseline = IdentityBaseline.from_dict(
            {
                "path": RESERVATIONS_API_V3,
                "updated": "2026-02-01T00:00:00+00:00",
                "revalidation_pending": False,
                "candidates": {
                    "valid": {
                        "timing_status": "known",
                        "check_in": "2026-01-01T00:00:00+00:00",
                        "check_out": "2026-01-04T00:00:00+00:00",
                        "observed_at": "2026-01-01T00:00:00+00:00",
                    },
                    "inverted": {
                        "timing_status": "known",
                        "check_in": "2026-01-04T00:00:00+00:00",
                        "check_out": "2026-01-01T00:00:00+00:00",
                        "observed_at": "2026-01-01T00:00:00+00:00",
                    },
                    "garbled": {
                        "timing_status": "unknown",
                        "observed_at": "not-a-time",
                    },
                    "not-a-dict": "x",
                },
            },
        )

        assert baseline.candidates["valid"].timing_status == "known"
        assert baseline.candidates["inverted"].timing_status == "unknown"
        assert "garbled" not in baseline.candidates
        assert "not-a-dict" not in baseline.candidates

    def test_code_maps_keep_only_string_pairs(self) -> None:
        """Only non-empty string code to ID entries survive."""
        baseline = IdentityBaseline.from_dict(
            {
                "path": RESERVATIONS_API_V3,
                "codes": {CODE_A: "res-1", CODE_B: 5, "CONF-C": "", "": "res-4"},
                "legacy_codes": {CODE_A: "res-1", CODE_B: ["x"]},
                "fallback_codes": "not-a-map",
            },
        )

        assert baseline.codes == {CODE_A: "res-1"}
        assert baseline.legacy_codes == {CODE_A: "res-1"}
        assert baseline.fallback_codes == {}

    def test_structural_fields_are_kept(self) -> None:
        """Validation keeps the continuity evidence intact."""
        baseline = IdentityBaseline.from_dict(
            {
                "path": RESERVATIONS_API_V3,
                "continuity": CONTINUITY_UNCONFIRMED,
                "divergent": True,
                "revalidation_pending": True,
                "legacy_codes": {CODE_A: "res-1"},
                "bootstrap_codes": {CODE_B: "res-2"},
            },
        )

        assert baseline.continuity == CONTINUITY_UNCONFIRMED
        assert baseline.divergent is True
        assert baseline.legacy_codes == {CODE_A: "res-1"}
        assert baseline.bootstrap_codes == {CODE_B: "res-2"}

    def test_new_fields_round_trip_with_unknown_keys(self) -> None:
        """The new fields persist and unknown keys are tolerated."""
        original = IdentityBaseline(
            path=RESERVATIONS_API_V3,
            codes={CODE_A: "res-1"},
            revalidation_pending=True,
            conflict_evidence_generation=4,
        )
        payload = {**original.to_dict(), "future_key": {"x": 1}}

        restored = IdentityBaseline.from_dict(payload)

        assert payload["revalidation_pending"] is True
        assert payload["conflict_evidence_generation"] == 4
        assert restored.revalidation_pending is True
        assert restored.conflict_evidence_generation == 4

    @pytest.mark.parametrize("generation", ["1", -1, True, 1.5])
    async def test_malformed_generation_fails_closed(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
        generation: Any,
    ) -> None:
        """An unreadable generation leaves no usable baseline."""
        with pytest.raises(ValueError):
            IdentityBaseline.from_dict(
                {
                    "path": RESERVATIONS_API_V3,
                    "conflict_evidence_generation": generation,
                }
            )
        _preset(
            hass_storage,
            storage_key("entry-bad-gen"),
            {
                "path": RESERVATIONS_API_V3,
                "codes": {CODE_A: "res-1"},
                "conflict_evidence_generation": generation,
            },
            minor_version=2,
        )
        guard = ReservationIdentityGuard(hass, "entry-bad-gen")
        await guard.async_load()

        assert guard.baseline is None
        # Without the baseline's generation a missing or lagging
        # evidence store cannot be told apart from a current one.
        assert guard.conflict_evidence.state == EVIDENCE_COMPROMISED

    async def test_unparsable_identity_payload_compromises_evidence(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """An unreadable baseline never lets current-looking evidence load."""
        _preset(
            hass_storage,
            conflict_evidence_storage_key("entry-bad-identity"),
            _evidence_payload(generation=3),
        )
        _preset(
            hass_storage,
            storage_key("entry-bad-identity"),
            ["not", "a", "baseline"],
            minor_version=2,
        )
        guard = ReservationIdentityGuard(hass, "entry-bad-identity")

        await guard.async_load()

        assert guard.baseline is None
        assert guard.conflict_evidence.state == EVIDENCE_COMPROMISED
        assert guard.conflict_evidence.count == 1

    @pytest.mark.parametrize(
        "payload",
        [["not", "a", "baseline"], "corrupt", 7],
        ids=["list", "string", "number"],
    )
    async def test_unparsable_minor_one_payload_fails_closed(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
        payload: Any,
    ) -> None:
        """A non-mapping pre-013 payload loads as no baseline."""
        _preset(hass_storage, storage_key("entry-bad-minor-one"), payload)
        guard = ReservationIdentityGuard(hass, "entry-bad-minor-one")

        await guard.async_load()

        assert guard.baseline is None
        assert guard.conflict_evidence.state == EVIDENCE_COMPROMISED

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("path", "reservations_v4"),
            ("path", 3),
            ("continuity", "trusted"),
            ("continuity", None),
        ],
        ids=["unknown-path", "non-string-path", "unknown-continuity", "null"],
    )
    async def test_unknown_path_or_continuity_fails_closed(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
        field: str,
        value: Any,
    ) -> None:
        """Only a known path and continuity value load a baseline."""
        data = {
            "path": RESERVATIONS_API_V3,
            "continuity": CONTINUITY_VERIFIED,
            "codes": {CODE_A: "res-1"},
            field: value,
        }
        with pytest.raises(ValueError):
            IdentityBaseline.from_dict(data)
        _preset(hass_storage, storage_key("entry-bad-path"), data, minor_version=2)
        guard = ReservationIdentityGuard(hass, "entry-bad-path")

        await guard.async_load()

        assert guard.baseline is None
        assert guard.conflict_evidence.state == EVIDENCE_COMPROMISED

    async def test_payload_stays_loadable_by_a_minor_one_store(
        self, hass: HomeAssistant
    ) -> None:
        """A rollback to a minor-version 1 release still loads it."""
        guard = await _ready_guard(hass, "entry-rollback")

        older = Store[dict[str, Any]](hass, 1, storage_key("entry-rollback"))
        data = await older.async_load()

        assert data is not None
        assert data["codes"] == {CODE_A: "res-1"}
        assert IdentityBaseline.from_dict(data).codes == {CODE_A: "res-1"}
        assert guard.baseline is not None


class TestEvidenceSaveOrder:
    """The crash-consistent three-step save order (T018, R7 d8)."""

    async def test_step_one_runs_on_every_refresh(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Generations advance together even with no pairs."""
        guard = await _ready_guard(hass, "entry-step-one")
        await _accept(guard, [_reservation("res-1", CODE_A)])

        evidence_data = hass_storage[conflict_evidence_storage_key("entry-step-one")]
        identity_data = hass_storage[storage_key("entry-step-one")]
        assert evidence_data["data"]["generation"] == 2
        assert identity_data["data"]["conflict_evidence_generation"] == 2

    async def test_additions_are_saved_before_identity(
        self, hass: HomeAssistant
    ) -> None:
        """Evidence additions reach storage before the baseline."""
        guard = ReservationIdentityGuard(hass, "entry-order")
        await guard.async_load()
        order: list[str] = []
        evidence_save = guard.conflict_evidence._store.async_save
        identity_save = guard._store.async_save

        async def _evidence_save(data: Any) -> None:
            """Record an evidence save."""
            order.append("evidence")
            await evidence_save(data)

        async def _identity_save(data: Any) -> None:
            """Record an identity save."""
            order.append("identity")
            await identity_save(data)

        with (
            patch.object(guard.conflict_evidence._store, "async_save", _evidence_save),
            patch.object(guard._store, "async_save", _identity_save),
        ):
            await _accept(
                guard,
                [_reservation("res-1", CODE_A), _reservation("res-2", CODE_B)],
                rejected_codes=(_pair("res-9", "CONF-9"),),
            )
            await _accept(
                guard,
                [_reservation("res-1", CODE_A), _reservation("res-9", "CONF-9")],
            )

        assert order == ["evidence", "identity", "evidence", "identity", "evidence"]

    async def test_failed_evidence_save_loads_compromised(
        self, hass: HomeAssistant
    ) -> None:
        """Evidence the baseline expects but never stored fails closed."""
        guard = await _ready_guard(hass, "entry-step1-fail")
        with patch.object(
            guard.conflict_evidence._store,
            "async_save",
            AsyncMock(side_effect=OSError),
        ):
            await _accept(
                guard,
                [_reservation("res-1", CODE_A)],
                rejected_codes=(_pair("res-9", CODE_B),),
            )
        assert guard.conflict_evidence.state == EVIDENCE_UNSTORED

        restarted = ReservationIdentityGuard(hass, "entry-step1-fail")
        await restarted.async_load()

        assert restarted.conflict_evidence.state == EVIDENCE_COMPROMISED
        assert restarted.write_refusal("res-1") == (
            f"{REFUSAL_EVIDENCE_UNAVAILABLE} {REFUSAL_EVIDENCE_REPAIR}"
        )

    async def test_logged_disk_failure_in_step_one_is_unstored(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """A write failure Home Assistant only logs still fails step 1."""
        _preset(
            hass_storage,
            storage_key("entry-disk-1"),
            {
                "path": RESERVATIONS_API_LEGACY,
                "continuity": CONTINUITY_NOT_APPLICABLE,
                "codes": {CODE_A: "res-1"},
            },
        )
        guard = ReservationIdentityGuard(hass, "entry-disk-1")
        await guard.async_load()
        evidence = guard.conflict_evidence
        key = conflict_evidence_storage_key("entry-disk-1")

        with _disk_full(evidence._store):
            assert await _accept(
                guard,
                [_reservation("res-1", CODE_A)],
                rejected_codes=(_pair("res-9", CODE_B),),
            )
            assert await evidence.async_save() is False

        assert key not in hass_storage
        assert evidence.state == EVIDENCE_UNSTORED
        assert guard.baseline is not None
        assert guard.baseline.revalidation_pending is True
        assert guard.write_refusal("res-1") == REFUSAL_EVIDENCE_UNAVAILABLE
        assert guard.compare(
            [_reservation("res-2", CODE_B)], RESERVATIONS_API_LEGACY
        ).is_ambiguous

        restarted = ReservationIdentityGuard(hass, "entry-disk-1")
        await restarted.async_load()
        assert restarted.conflict_evidence.state == EVIDENCE_COMPROMISED

    async def test_logged_disk_failure_in_step_two_is_unstored(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """A write failure Home Assistant only logs leaves the guard unstored."""
        guard = await _ready_guard(hass, "entry-disk-2")

        with _disk_full(guard._store):
            stored = await _accept(guard, [_reservation("res-1", CODE_A)])

        assert stored is False
        assert guard.write_refusal("res-1") == REFUSAL_UNSTORED
        identity_data = hass_storage[storage_key("entry-disk-2")]["data"]
        assert identity_data["conflict_evidence_generation"] == 1

    async def test_save_deferred_while_stopping_is_unstored(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """A save Home Assistant defers to its final write is not stored."""
        guard = await _ready_guard(hass, "entry-stopping")
        before = hass_storage[storage_key("entry-stopping")]["data"]
        hass.set_state(CoreState.stopping)
        try:
            with pytest.raises(OSError):
                await guard._store.async_save_checked({"path": RESERVATIONS_API_V3})
            stored = await _accept(guard, [_reservation("res-1", CODE_A)])
        finally:
            hass.set_state(CoreState.running)

        assert stored is False
        assert guard.write_refusal("res-1") == REFUSAL_UNSTORED
        assert guard.conflict_evidence.state == EVIDENCE_UNSTORED
        assert hass_storage[storage_key("entry-stopping")]["data"] == before

    async def test_save_deferred_while_stopping_logs_no_warning(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A deferred save is not reported as a storage failure.

        Home Assistant's final write persists the deferred data, so a
        warning that storage failed would be untrue. The guard still
        fails closed exactly as for a failed write.
        """
        guard = await _ready_guard(hass, "entry-stopping-log")
        baseline = guard.baseline
        assert baseline is not None
        caplog.clear()
        hass.set_state(CoreState.stopping)
        try:
            with caplog.at_level(logging.DEBUG):
                stored = await _accept(
                    guard,
                    [_reservation("res-1", CODE_A)],
                    rejected_codes=(_pair("res-9", CODE_B),),
                )
                await guard._store_bootstrap(baseline)
        finally:
            hass.set_state(CoreState.running)

        assert stored is False
        assert guard.write_refusal("res-1") == REFUSAL_UNSTORED
        assert guard.conflict_evidence.state == EVIDENCE_UNSTORED
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        deferred = [
            r.getMessage()
            for r in caplog.records
            if "deferred to Home Assistant's final write" in r.getMessage()
        ]
        assert len(deferred) == 3

    async def test_unsaved_pairs_stay_pending_until_saved(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Pairs a failed save missed still compare and are saved later."""
        guard = await _ready_guard(hass, "entry-pending")
        with patch.object(
            guard.conflict_evidence._store,
            "async_save",
            AsyncMock(side_effect=OSError),
        ):
            await _accept(
                guard,
                [_reservation("res-1", CODE_A)],
                rejected_codes=(_pair("res-9", CODE_B),),
            )

        assert guard.compare(
            [_reservation("res-2", CODE_B)], RESERVATIONS_API_LEGACY
        ).is_ambiguous
        await _accept(guard, [_reservation("res-1", CODE_A)])

        stored = hass_storage[conflict_evidence_storage_key("entry-pending")]["data"]
        assert [e["reservation_id"] for e in stored["entries"]] == ["res-9"]
        assert guard.conflict_evidence.state == EVIDENCE_READY

    async def test_failed_identity_save_keeps_stored_pairs(
        self, hass: HomeAssistant
    ) -> None:
        """A failed step 2 leaves step 1's pairs stored and compared."""
        guard = await _ready_guard(hass, "entry-step2-fail")
        with patch.object(guard._store, "async_save", AsyncMock(side_effect=OSError)):
            stored = await _accept(
                guard,
                [_reservation("res-1", CODE_A)],
                rejected_codes=(_pair("res-9", CODE_B),),
            )
        assert stored is False

        restarted = ReservationIdentityGuard(hass, "entry-step2-fail")
        await restarted.async_load()

        assert restarted.conflict_evidence.state == EVIDENCE_REVALIDATING
        assert restarted.compare(
            [_reservation("res-2", CODE_B)], RESERVATIONS_API_LEGACY
        ).is_ambiguous

    async def test_both_saves_failing_loads_revalidating(
        self, hass: HomeAssistant
    ) -> None:
        """Nothing changed on disk, so a restart revalidates."""
        guard = await _ready_guard(hass, "entry-both-fail")
        with (
            patch.object(
                guard.conflict_evidence._store,
                "async_save",
                AsyncMock(side_effect=OSError),
            ),
            patch.object(guard._store, "async_save", AsyncMock(side_effect=OSError)),
        ):
            await _accept(guard, [_reservation("res-1", CODE_A)])

        restarted = ReservationIdentityGuard(hass, "entry-both-fail")
        await restarted.async_load()

        assert restarted.conflict_evidence.state == EVIDENCE_REVALIDATING
        assert restarted.write_refusal("res-1") == REFUSAL_REVALIDATING

    async def test_restart_revalidates_until_steps_one_and_two(
        self, hass: HomeAssistant
    ) -> None:
        """A clean restart refuses until a refresh is fully stored."""
        await _ready_guard(hass, "entry-restart")

        restarted = ReservationIdentityGuard(hass, "entry-restart")
        await restarted.async_load()
        assert restarted.conflict_evidence.state == EVIDENCE_REVALIDATING
        assert restarted.write_refusal("res-1") == REFUSAL_REVALIDATING

        await _accept(restarted, [_reservation("res-1", CODE_A)])

        assert restarted.conflict_evidence.state == EVIDENCE_READY
        assert restarted.write_refusal("res-1") is None

    async def test_compromised_store_is_never_written(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """A compromised store survives refreshes and restarts."""
        key = conflict_evidence_storage_key("entry-never-write")
        _preset(hass_storage, key, _evidence_payload(generation="bad"))
        guard = ReservationIdentityGuard(hass, "entry-never-write")
        await guard.async_load()
        before = dict(hass_storage[key])

        await _accept(
            guard,
            [_reservation("res-1", CODE_A)],
            rejected_codes=(_pair("res-9", CODE_B),),
        )

        assert hass_storage[key] == before
        assert guard.conflict_evidence.state == EVIDENCE_COMPROMISED
        restarted = ReservationIdentityGuard(hass, "entry-never-write")
        await restarted.async_load()
        assert restarted.conflict_evidence.state == EVIDENCE_COMPROMISED

    async def test_identity_is_frozen_while_compromised(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """No continuity, retirement or revalidation is concluded."""
        _preset(
            hass_storage,
            storage_key("entry-frozen"),
            {
                "path": RESERVATIONS_API_V3,
                "continuity": CONTINUITY_UNCONFIRMED,
                "bootstrap_attempted": True,
                "codes": {"CONF-V3": "res-v3"},
                "bootstrap_codes": {CODE_A: "res-1", CODE_B: "res-2"},
            },
        )
        _preset(
            hass_storage,
            conflict_evidence_storage_key("entry-frozen"),
            _evidence_payload(entries="bad"),
        )
        guard = ReservationIdentityGuard(hass, "entry-frozen")
        await guard.async_load()

        await _accept(
            guard,
            [_reservation("res-1", CODE_A), _reservation("res-v3", "CONF-V3")],
            path=RESERVATIONS_API_V3,
        )

        baseline = guard.baseline
        assert baseline is not None
        assert baseline.continuity == CONTINUITY_UNCONFIRMED
        assert baseline.bootstrap_codes == {CODE_A: "res-1", CODE_B: "res-2"}
        assert baseline.revalidation_pending is True

    async def test_identity_is_frozen_while_saturated(
        self, hass: HomeAssistant
    ) -> None:
        """A saturated store cannot confirm continuity either."""
        guard = ReservationIdentityGuard(hass, "entry-frozen-sat")
        await guard.async_load()
        await guard.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_V3,
                continuity=CONTINUITY_UNCONFIRMED,
                bootstrap_attempted=True,
                codes={"CONF-V3": "res-v3"},
                bootstrap_codes={CODE_A: "res-1"},
            )
        )
        guard.conflict_evidence.record(
            [_pair(f"r-{i}", f"C-{i}") for i in range(MAX_CONFLICT_ONLY_EVIDENCE + 1)],
            {},
            RESERVATIONS_API_V3,
            _EVIDENCE_NOW,
            (),
        )

        await _accept(
            guard,
            [_reservation("res-1", CODE_A), _reservation("res-v3", "CONF-V3")],
            path=RESERVATIONS_API_V3,
        )

        baseline = guard.baseline
        assert baseline is not None
        assert guard.conflict_evidence.state == EVIDENCE_SATURATED
        assert baseline.continuity == CONTINUITY_UNCONFIRMED
        assert baseline.bootstrap_codes == {CODE_A: "res-1"}


class TestEvidenceWriteRefusals:
    """The two pre-membership refusals (T018, C15, C16, FR-008)."""

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (EVIDENCE_UNLOADED, "unavailable"),
            (EVIDENCE_UNSTORED, "unavailable"),
            (EVIDENCE_SATURATED, "repair"),
            (EVIDENCE_COMPROMISED, "repair"),
            (EVIDENCE_REVALIDATING, "revalidating"),
        ],
    )
    async def test_non_ready_state_refuses_before_membership(
        self, hass: HomeAssistant, state: str, expected: str
    ) -> None:
        """Each non-ready state refuses known and unknown targets."""
        guard = await _ready_guard(hass, f"entry-state-{state}")
        messages = {
            "unavailable": REFUSAL_EVIDENCE_UNAVAILABLE,
            "repair": f"{REFUSAL_EVIDENCE_UNAVAILABLE} {REFUSAL_EVIDENCE_REPAIR}",
            "revalidating": REFUSAL_REVALIDATING,
        }

        with patch.object(
            ConflictOnlyEvidence, "state", new_callable=PropertyMock
        ) as mocked:
            mocked.return_value = state
            known = guard.classify_write_refusal("res-1")
            unknown = guard.classify_write_refusal("res-unknown")

        for refusal in (known, unknown):
            assert refusal is not None
            assert refusal.message == messages[expected]
            assert refusal.missing_current_code is False

    async def test_existing_refusals_keep_their_order(
        self, hass: HomeAssistant
    ) -> None:
        """Baseline, storage, continuity and ambiguity refuse first."""
        empty = ReservationIdentityGuard(hass, "entry-order-empty")
        await empty.async_load()
        assert empty.write_refusal("res-1") == REFUSAL_NO_BASELINE

        unstored = await _ready_guard(hass, "entry-order-unstored")
        unstored._unstored = True
        assert unstored.write_refusal("res-1") == REFUSAL_UNSTORED

        ambiguous = await _ready_guard(hass, "entry-order-ambiguous")
        ambiguous.mark_ambiguous()
        assert ambiguous.write_refusal("res-1") == REFUSAL_AMBIGUOUS

    async def test_ready_evidence_keeps_the_membership_refusal(
        self, hass: HomeAssistant
    ) -> None:
        """Ready evidence leaves the membership path unchanged."""
        guard = await _ready_guard(hass, "entry-ready-membership")

        assert guard.write_refusal("res-1") is None
        refusal = guard.classify_write_refusal("res-unknown")
        assert refusal is not None
        assert refusal.missing_current_code is True

    async def test_compromised_during_pending_revalidation(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Compromise outranks a pending revalidation."""
        _preset(
            hass_storage,
            storage_key("entry-compromised-pending"),
            {
                "path": RESERVATIONS_API_LEGACY,
                "continuity": CONTINUITY_VERIFIED,
                "codes": {CODE_A: "res-1"},
            },
        )
        _preset(
            hass_storage,
            conflict_evidence_storage_key("entry-compromised-pending"),
            [],
        )
        guard = ReservationIdentityGuard(hass, "entry-compromised-pending")
        await guard.async_load()

        assert guard.write_refusal("res-1") == (
            f"{REFUSAL_EVIDENCE_UNAVAILABLE} {REFUSAL_EVIDENCE_REPAIR}"
        )

    async def test_unloaded_guard_refuses_as_unavailable(
        self, hass: HomeAssistant
    ) -> None:
        """Evidence that was never loaded authorizes nothing."""
        guard = await _ready_guard(hass, "entry-unloaded-evidence")
        guard._evidence = ConflictOnlyEvidence(hass, "entry-unloaded-evidence")

        assert guard.write_refusal("res-1") == REFUSAL_EVIDENCE_UNAVAILABLE

    async def test_write_during_identity_save_is_revalidating(
        self, hass: HomeAssistant
    ) -> None:
        """A newly rejected current ID cannot slip through step 2."""
        guard = await _ready_guard(hass, "entry-mid-save")
        seen: list[str | None] = []
        save = guard._store.async_save

        async def _save(data: Any) -> None:
            """Observe the refusal while step 2 awaits storage."""
            seen.append(guard.write_refusal("res-1"))
            await save(data)

        with patch.object(guard._store, "async_save", _save):
            await _accept(
                guard,
                [_reservation("res-2", CODE_B)],
                rejected_codes=(_pair("res-1", CODE_A),),
            )

        assert seen == [REFUSAL_REVALIDATING]
        refusal = guard.classify_write_refusal("res-1")
        assert refusal is not None
        assert refusal.missing_current_code is True

    async def test_growing_window_revalidates(self, hass: HomeAssistant) -> None:
        """A wider window refuses until it has been covered."""
        window = {"past_days": 30}
        guard = ReservationIdentityGuard(
            hass, "entry-window", past_days=lambda: window["past_days"]
        )
        await guard.async_load()
        old_stay = {
            "res-old": (datetime.now(UTC) - timedelta(days=45)).date().isoformat()
        }
        await _accept(
            guard,
            [_reservation("res-1", CODE_A)],
            past_days=30,
            rejected_codes=(_pair("res-old", CODE_B),),
            rejected_check_out_bounds=old_stay,
        )
        assert guard.write_refusal("res-1") is None
        assert guard.conflict_evidence.ids_for(CODE_B) == frozenset()

        window["past_days"] = 60
        assert guard.write_refusal("res-1") == REFUSAL_REVALIDATING

        await _accept(
            guard,
            [_reservation("res-1", CODE_A)],
            past_days=60,
            rejected_codes=(_pair("res-old", CODE_B),),
            rejected_check_out_bounds=old_stay,
        )
        assert guard.write_refusal("res-1") is None
        assert guard.conflict_evidence.ids_for(CODE_B) == frozenset({"res-old"})

        window["past_days"] = 10
        assert guard.write_refusal("res-1") is None

    def test_messages_are_fixed_and_carry_no_code(self) -> None:
        """The refusals are constants with no placeholder."""
        for message in (
            REFUSAL_EVIDENCE_UNAVAILABLE,
            REFUSAL_EVIDENCE_REPAIR,
            REFUSAL_REVALIDATING,
        ):
            assert message
            assert "{" not in message
            assert "STUB" not in message

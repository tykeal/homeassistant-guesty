# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the config entry diagnostics download (FR-042).

The download is the second of the two diagnostics surfaces the
legacy not-found fallback has to report through: a user asking why
a reservation is still visible, or why a write was refused, needs
to see the fallback counters and the served identifiers without
turning on DEBUG logging.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.const import RESERVATIONS_API_V3
from custom_components.guesty.api.exceptions import GuestyConnectionError
from custom_components.guesty.api.guest_cache import GuestLookupResult
from custom_components.guesty.api.models import (
    FallbackCounters,
    GuestyGuest,
    GuestyListing,
    GuestyReservation,
    GuestyStaySegment,
    RejectedCode,
    ValidationSummary,
)
from custom_components.guesty.api.validation import ValidationReason
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RESERVATIONS_API,
    DOMAIN,
)
from custom_components.guesty.coordinator import AcceptedRefreshSnapshot
from custom_components.guesty.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.guesty.identity import (
    MAX_CONFLICT_ONLY_EVIDENCE,
    IdentityComparison,
    IdentityConflict,
)
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    make_fetch_result,
    make_identity_reservations,
    stub_guesty_network,
)

_PATCH_TEST = "custom_components.guesty.GuestyApiClient.test_connection"
_PATCH_LISTINGS = "custom_components.guesty.GuestyApiClient.get_listings"
_PATCH_RESERVATIONS = "custom_components.guesty.GuestyApiClient.get_reservations"

_WRITE_TARGETS = ("res-001", "res-002")


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Load a config entry with its network calls stubbed.

    Args:
        hass: Home Assistant instance.

    Returns:
        The loaded config entry.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CLIENT_ID: FAKE_CLIENT_ID,
            CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
        },
        unique_id=FAKE_CLIENT_ID,
    )
    entry.add_to_hass(hass)
    with (
        patch(_PATCH_TEST, new_callable=AsyncMock, return_value=True),
        patch(_PATCH_LISTINGS, new_callable=AsyncMock, return_value=[]),
        patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                make_identity_reservations(*_WRITE_TARGETS),
            ),
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


class TestReservationDiagnostics:
    """The reservations block of the download (T120)."""

    async def test_reports_the_fallback_counters_and_served_ids(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Both the counts and the served identifiers appear."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.last_fallback_counters = FallbackCounters(
            attempted=3,
            served=2,
            succeeded=1,
            confirmed_absent=1,
            failed=1,
            deferred=4,
        )
        coordinator.fallback_served_ids = frozenset({"res-001"})
        coordinator.fallback_sourced_ids = frozenset({"res-001", "res-002"})

        result = await async_get_config_entry_diagnostics(hass, entry)

        fallback: dict[str, Any] = result["reservations"]["fallback"]
        assert fallback["attempted"] == 3
        assert fallback["served"] == 2
        assert fallback["succeeded"] == 1
        assert fallback["confirmed_absent"] == 1
        assert fallback["failed"] == 1
        assert fallback["deferred"] == 4
        assert fallback["served_ids"] == ["res-001"]
        assert fallback["sourced_ids"] == ["res-001", "res-002"]
        assert result["reservations"]["path"] == coordinator.reservations_path
        assert result["reservations"]["continuity"] is not None

    async def test_carries_no_personal_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Identifiers only: no names, contacts or codes."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.fallback_served_ids = frozenset({"res-001"})

        rendered = repr(await async_get_config_entry_diagnostics(hass, entry))

        # Guest lookup *counts* are required by FR-042, so the guard
        # is on guest detail rather than on the word "guest".
        for forbidden in ("CONF-", "@", "full_name", "phone", "email"):
            assert forbidden not in rendered

    async def test_snapshot_absent_from_diagnostics(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The accepted-refresh snapshot never reaches the download.

        ``validation.stale.snapshot_stale`` (feature 013) names the
        verified display snapshot, not this one, and carries a bool;
        it is the only permitted occurrence of "snapshot".
        """
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator._accepted_snapshot = AcceptedRefreshSnapshot(
            attempt=1,
            path=coordinator.reservations_path,
            returned_ids=frozenset({"res-snapshot-only"}),
            rejected_ids=frozenset({"res-snapshot-rejected"}),
            records=MappingProxyType({}),
            observed_codes=MappingProxyType({"SNAPCODE-1": "res-snapshot-only"}),
            fallback_observed_codes=MappingProxyType(
                {"SNAPCODE-2": "res-snapshot-observed"}
            ),
        )

        rendered = repr(await async_get_config_entry_diagnostics(hass, entry))

        for forbidden in ("SNAPCODE", "res-snapshot", "accepted"):
            assert forbidden not in rendered
        assert "snapshot" not in rendered.replace("snapshot_stale", "")

    async def test_unloaded_entry_reports_nothing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A download must not fail on an unloaded entry."""
        entry = MockConfigEntry(domain=DOMAIN, data={})
        entry.add_to_hass(hass)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result == {"reservations": {"loaded": False}}


class TestLegacyWriteDiagnostics:
    """Legacy-routed write counters are downloadable (T131)."""

    async def test_counters_appear_in_the_download(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Every operation and outcome is reported by name."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.record_legacy_write("note", "succeeded")
        coordinator.record_legacy_write("status", "failed")
        coordinator.record_legacy_write("custom_field", "refused")

        result = await async_get_config_entry_diagnostics(hass, entry)

        legacy_writes: dict[str, Any] = result["reservations"]["legacy_writes"]
        assert legacy_writes["note_succeeded"] == 1
        assert legacy_writes["status_failed"] == 1
        assert legacy_writes["custom_field_refused"] == 1
        assert legacy_writes["total"] == 3
        assert legacy_writes["routing_available"] is True

    async def test_retired_routing_is_reported(
        self,
        hass: HomeAssistant,
    ) -> None:
        """An operator can see routing has self-disabled."""
        entry = await _setup_entry(hass)
        api_client = hass.data[DOMAIN][entry.entry_id]["api_client"]
        with patch.object(
            type(api_client),
            "legacy_write_routing_available",
            property(lambda _self: False),
        ):
            result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["reservations"]["legacy_writes"]["routing_available"] is False


class TestRefreshDiagnostics:
    """The FR-042 refresh counters in the download (T091)."""

    @staticmethod
    def _reservation(
        reservation_id: str,
        listing_id: str,
        *,
        days_out: int,
        guest_id: str | None = None,
    ) -> GuestyReservation:
        """Build a reservation with one stay segment.

        Args:
            reservation_id: Guesty reservation identifier.
            listing_id: Listing the stay segment names.
            days_out: Check-in offset from now, in days.
            guest_id: Guest identifier to resolve, if any.

        Returns:
            A reservation the coordinator can attribute.
        """
        now = datetime.now(UTC)
        check_in = now + timedelta(days=days_out)
        check_out = check_in + timedelta(days=2)
        return GuestyReservation(
            id=reservation_id,
            listing_id=listing_id,
            status="confirmed",
            check_in=check_in,
            check_out=check_out,
            confirmation_code=f"CONF-{reservation_id}",
            guest=(
                GuestyGuest(full_name="", guest_id=guest_id)
                if guest_id is not None
                else None
            ),
            stay=(
                GuestyStaySegment(
                    listing_id=listing_id,
                    check_in=check_in,
                    check_out=check_out,
                ),
            ),
        )

    async def _diagnose(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> dict[str, Any]:
        """Load an entry, refresh it and download diagnostics.

        Args:
            hass: Home Assistant instance.
            sample_listing: The listing the coordinator tracks.

        Returns:
            The diagnostics mapping of the loaded entry.
        """
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_CLIENT_ID: FAKE_CLIENT_ID,
                CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
            },
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
            unique_id=FAKE_CLIENT_ID,
        )
        entry.add_to_hass(hass)
        with stub_guesty_network(listings=[sample_listing]):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        with (
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [
                        self._reservation(
                            "res-ok",
                            "listing-001",
                            days_out=1,
                            guest_id="guest-1",
                        ),
                        self._reservation(
                            "res-unknown",
                            "listing-999",
                            days_out=1,
                        ),
                        self._reservation(
                            "res-far",
                            "listing-001",
                            days_out=400,
                        ),
                    ],
                    pages=2,
                    status_counts=MappingProxyType({"confirmed": 3}),
                    segment_histogram=MappingProxyType({1: 3}),
                ),
            ),
            patch(
                "custom_components.guesty.GuestyApiClient.resolve_guests",
                new_callable=AsyncMock,
                return_value=GuestLookupResult(hits=1, lookups=2, failures=1),
            ),
        ):
            await coordinator.async_refresh()
        return await async_get_config_entry_diagnostics(hass, entry)

    async def test_every_counter_is_reported(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """The block answers why a reservation did not publish."""
        result = await self._diagnose(hass, sample_listing)

        reservations = result["reservations"]
        assert reservations["path"] == RESERVATIONS_API_V3
        assert isinstance(reservations["bootstrap_pages"], int)
        refresh = reservations["refresh"]
        assert refresh["pages"] == 2
        assert refresh["received"] == 3
        assert refresh["attributed"] == 1
        assert refresh["trimmed"] == 1
        assert refresh["skipped"] == 1
        assert refresh["status_counts"] == {"confirmed": 3}
        assert refresh["segment_histogram"] == {1: 3}
        assert refresh["unknown_listings"] == 1
        assert refresh["guest_hits"] == 1
        assert refresh["guest_lookups"] == 2
        assert refresh["guest_failures"] == 1
        assert refresh["identity_ambiguous"] is False

    async def test_an_aborted_refresh_is_what_gets_reported(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """An ambiguous refresh is exactly what needs explaining."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_CLIENT_ID: FAKE_CLIENT_ID,
                CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
            },
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
            unique_id=FAKE_CLIENT_ID,
        )
        entry.add_to_hass(hass)
        with stub_guesty_network(listings=[sample_listing]):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        with (
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [
                        self._reservation("res-a", "listing-001", days_out=1),
                        self._reservation("res-b", "listing-001", days_out=2),
                    ],
                    pages=3,
                ),
            ),
            patch.object(
                coordinator.identity,
                "compare",
                return_value=IdentityComparison(
                    conflicts=(
                        IdentityConflict(
                            known_id="res-old",
                            observed_id="res-new",
                            source="codes",
                        ),
                    ),
                ),
            ),
        ):
            await coordinator.async_refresh()

        refresh = (await async_get_config_entry_diagnostics(hass, entry))[
            "reservations"
        ]["refresh"]
        assert refresh["identity_ambiguous"] is True
        assert refresh["pages"] == 3
        assert refresh["received"] == 2
        assert refresh["attributed"] == 0

    async def test_the_counters_carry_no_personal_data(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """No guest detail and no confirmation code appears."""
        rendered = repr(await self._diagnose(hass, sample_listing))

        for forbidden in ("CONF-", "@", "phone", "email", "full_name"):
            assert forbidden not in rendered


class TestValidationDiagnostics:
    """The ``reservations.validation`` block (T036)."""

    @staticmethod
    async def _entry(
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> MockConfigEntry:
        """Load a v3 entry tracking the sample listing.

        Args:
            hass: Home Assistant instance.
            sample_listing: The listing the coordinator tracks.

        Returns:
            The loaded entry.
        """
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_CLIENT_ID: FAKE_CLIENT_ID,
                CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
            },
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
            unique_id=FAKE_CLIENT_ID,
        )
        entry.add_to_hass(hass)
        with stub_guesty_network(listings=[sample_listing]):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        return entry

    @staticmethod
    async def _refresh(
        hass: HomeAssistant,
        entry: MockConfigEntry,
        **fields: Any,
    ) -> dict[str, Any]:
        """Run one accepted refresh and return the validation block.

        Args:
            hass: Home Assistant instance.
            entry: The loaded entry.
            **fields: Fetch result fields to set.

        Returns:
            The ``reservations.validation`` diagnostics mapping.
        """
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        reservation = TestRefreshDiagnostics._reservation(
            "res-ok", "listing-001", days_out=1
        )
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result([reservation], **fields),
        ):
            await coordinator.async_refresh()
        assert coordinator.last_update_success
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        return cast(dict[str, Any], diagnostics["reservations"]["validation"])

    async def test_block_reports_the_refresh_counts(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Counts, reasons, IDs, evidence and staleness in one block."""
        entry = await self._entry(hass, sample_listing)
        validation = await self._refresh(
            hass,
            entry,
            rejected_ids=("res-b", "res-a"),
            validation=ValidationSummary(
                rejected_records=1,
                partial_records=1,
                rejected_segments=2,
                reasons=MappingProxyType(
                    {
                        ValidationReason.UNRECOGNIZED_STATUS: 1,
                        ValidationReason.SEGMENT_MISSING_LISTING: 2,
                    }
                ),
            ),
        )

        assert validation == {
            "rejected_records": 1,
            "partial_records": 1,
            "rejected_segments": 2,
            "reasons": {
                "segment_missing_listing": 2,
                "unrecognized_status": 1,
            },
            "rejected_ids": ["res-a", "res-b"],
            "rejected_ids_truncated": False,
            "conflict_evidence": {
                "state": "ready",
                "available": True,
                "count": 0,
                "dated": 0,
                "undated": 0,
                "bound": MAX_CONFLICT_ONLY_EVIDENCE,
                "near_capacity": False,
            },
            "stale": {
                "snapshot_stale": False,
                "stale_count": 0,
                "last_verified_at": None,
            },
        }

    async def test_rejected_ids_are_capped(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """At most 100 sorted identifiers, with a truncation flag."""
        entry = await self._entry(hass, sample_listing)
        ids = tuple(f"res-{n:03d}" for n in range(150))
        validation = await self._refresh(hass, entry, rejected_ids=ids[::-1])

        assert validation["rejected_ids"] == list(ids[:100])
        assert validation["rejected_ids_truncated"] is True

    async def test_fallback_invalid_ids_are_listed(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """A fallback-invalid record counted as rejected is listed too."""
        entry = await self._entry(hass, sample_listing)
        validation = await self._refresh(
            hass,
            entry,
            rejected_ids=("res-b",),
            fallback_invalid_ids=("res-c", "res-b"),
            validation=ValidationSummary(
                rejected_records=2,
                reasons=MappingProxyType(
                    {
                        ValidationReason.UNRECOGNIZED_STATUS: 1,
                        ValidationReason.UNVERIFIED_INSTANT: 1,
                    }
                ),
            ),
        )

        assert validation["rejected_records"] == 2
        assert validation["rejected_ids"] == ["res-b", "res-c"]
        assert validation["rejected_ids_truncated"] is False

    async def test_fallback_invalid_ids_share_the_cap(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """The combined identifiers are capped at 100 with the flag."""
        entry = await self._entry(hass, sample_listing)
        ids = tuple(f"res-{n:03d}" for n in range(101))
        validation = await self._refresh(
            hass, entry, rejected_ids=ids[:100], fallback_invalid_ids=ids[100:]
        )

        assert validation["rejected_ids"] == list(ids[:100])
        assert validation["rejected_ids_truncated"] is True

    async def test_next_refresh_clears_fallback_invalid_ids(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """An accepted refresh with nothing invalid lists no IDs."""
        entry = await self._entry(hass, sample_listing)
        first = await self._refresh(hass, entry, fallback_invalid_ids=("res-c",))
        assert first["rejected_ids"] == ["res-c"]

        validation = await self._refresh(hass, entry)

        assert validation["rejected_ids"] == []
        assert validation["rejected_ids_truncated"] is False

    async def test_unfetched_failure_keeps_the_last_batch(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """A refresh that fetched no batch leaves the block unchanged."""
        entry = await self._entry(hass, sample_listing)
        before = await self._refresh(
            hass,
            entry,
            rejected_ids=("res-b",),
            fallback_invalid_ids=("res-c",),
            validation=ValidationSummary(
                rejected_records=2,
                reasons=MappingProxyType({ValidationReason.UNRECOGNIZED_STATUS: 2}),
            ),
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            side_effect=GuestyConnectionError("down"),
        ):
            await coordinator.async_refresh()
        assert not coordinator.last_update_success

        after = (await async_get_config_entry_diagnostics(hass, entry))["reservations"][
            "validation"
        ]
        for key in ("rejected_records", "reasons", "rejected_ids"):
            assert after[key] == before[key]
        assert after["rejected_ids"] == ["res-b", "res-c"]

    async def test_failed_refresh_reports_its_fetched_batch(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """A batch fetched before a later failure is what gets reported.

        The stale display drops what that batch disproved, so the
        block names the same batch rather than an earlier one.
        """
        entry = await self._entry(hass, sample_listing)
        await self._refresh(
            hass,
            entry,
            rejected_ids=("res-old",),
            validation=ValidationSummary(
                rejected_records=1,
                reasons=MappingProxyType({ValidationReason.UNRECOGNIZED_STATUS: 1}),
            ),
        )
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        reservation = TestRefreshDiagnostics._reservation(
            "res-ok", "listing-001", days_out=1
        )
        with (
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [reservation],
                    rejected_ids=("res-new",),
                    fallback_invalid_ids=("res-c",),
                    validation=ValidationSummary(
                        rejected_records=2,
                        reasons=MappingProxyType(
                            {ValidationReason.UNVERIFIED_INSTANT: 2}
                        ),
                    ),
                ),
            ),
            patch.object(
                coordinator,
                "_resolve_v3_reservations",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            await coordinator.async_refresh()
        assert not coordinator.last_update_success

        validation = (await async_get_config_entry_diagnostics(hass, entry))[
            "reservations"
        ]["validation"]
        assert validation["rejected_records"] == 2
        assert validation["reasons"] == {"unverified_instant": 2}
        assert validation["rejected_ids"] == ["res-c", "res-new"]

    async def test_failed_refresh_reports_the_stale_snapshot(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """The stale block names when the shown data was verified.

        The connection failure fetches no batch, so nothing is
        disproven and the snapshot keeps its one stale record.
        """
        entry = await self._entry(hass, sample_listing)
        await self._refresh(hass, entry, stale_ids=("res-ok",))
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        with patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            side_effect=GuestyConnectionError("down"),
        ):
            await coordinator.async_refresh()
        assert not coordinator.last_update_success
        assert coordinator.last_verified_at is not None

        stale = (await async_get_config_entry_diagnostics(hass, entry))["reservations"][
            "validation"
        ]["stale"]
        assert stale == {
            "snapshot_stale": True,
            "stale_count": 1,
            "last_verified_at": coordinator.last_verified_at.isoformat(),
        }

    async def test_status_counts_are_sanitized(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """An unrecognized raw status never becomes a diagnostics key."""
        entry = await self._entry(hass, sample_listing)
        await self._refresh(
            hass,
            entry,
            status_counts=MappingProxyType(
                {"confirmed": 1, "SENTINEL-STATUS-VALUE": 2}
            ),
        )

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        refresh = diagnostics["reservations"]["refresh"]
        assert refresh["status_counts"] == {"confirmed": 1, "unrecognized": 2}
        assert "SENTINEL-STATUS-VALUE" not in repr(diagnostics)

    async def test_evidence_block_reveals_no_codes_or_ids(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Only counts are shown; never which IDs have stored pairs."""
        entry = await self._entry(hass, sample_listing)
        validation = await self._refresh(
            hass,
            entry,
            rejected_codes=(RejectedCode("res-hidden-pair", "CODE-SECRET"),),
        )

        evidence = validation["conflict_evidence"]
        assert set(evidence) == {
            "state",
            "available",
            "count",
            "dated",
            "undated",
            "bound",
            "near_capacity",
        }
        assert evidence["count"] == 1
        rendered = repr(await async_get_config_entry_diagnostics(hass, entry))
        assert "CODE-SECRET" not in rendered
        assert "res-hidden-pair" not in rendered

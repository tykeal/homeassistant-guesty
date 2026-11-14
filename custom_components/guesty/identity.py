# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Reservation identity baseline and guard (FR-031 to FR-035).

Guesty Support would not confirm that a legacy reservation ``_id``
equals the v3 ``reservationId`` for every reservation. Until that is
settled on a real account, a wrong-identity write could put a working
door access code on a stranger's booking, so this module keeps a
persisted confirmation-code to reservation-identifier baseline,
compares every refresh against it without spending a request, and
refuses writes whose identity cannot be confirmed.

The confirmation code is a cross-check only. It is never used as a
lookup key for any resource (FR-031e) and its **value** is never
logged (FR-021, NFR-009), although it is persisted because it is the
key of the check itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers.storage import Store

from custom_components.guesty.api.const import (
    HOLD_STATUSES,
    RESERVATIONS_API_LEGACY,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from homeassistant.core import HomeAssistant

    from custom_components.guesty.api.models import GuestyReservation

# Schema version of the persisted identity baseline.
STORAGE_VERSION: Final[int] = 1

# Upper bound on each persisted code map (data-model.md section 4).
MAX_BASELINE_CODES: Final[int] = 5000

# Cross-path evidence exists and agrees.
CONTINUITY_VERIFIED: Final[str] = "verified"

# A cross-path question is unanswered; writes are refused.
CONTINUITY_UNCONFIRMED: Final[str] = "unconfirmed"

# No cross-path question arises for this baseline.
CONTINUITY_NOT_APPLICABLE: Final[str] = "not_applicable"

# Map names, used to report which map a conflict came from.
SOURCE_CODES: Final[str] = "codes"
SOURCE_BOOTSTRAP: Final[str] = "bootstrap_codes"
SOURCE_FALLBACK: Final[str] = "fallback_codes"

# Refusal texts for each conjunct of the write predicate
# (FR-031c, FR-032, FR-033).
REFUSAL_NO_BASELINE: Final[str] = (
    "Guesty has not established a reservation identity baseline yet, "
    "so this write is refused rather than risk acting on the wrong "
    "reservation. Wait for a successful reservation refresh, then "
    "try again."
)
REFUSAL_UNCONFIRMED: Final[str] = (
    "Guesty reservation identity continuity is unconfirmed, so this "
    "write is refused rather than risk acting on the wrong "
    "reservation."
)
REFUSAL_AMBIGUOUS: Final[str] = (
    "The last Guesty reservation refresh was aborted because a "
    "confirmation code identified a different reservation than "
    "before. Writes stay refused until that is resolved; set the "
    "'Reservations API' option to 'legacy' and reload the entry to "
    "roll back."
)
REFUSAL_NO_CODE: Final[str] = (
    "Reservation '{reservation_id}' has no known confirmation code, "
    "so its identity cannot be cross-checked and the write is "
    "refused."
)


def storage_key(entry_id: str) -> str:
    """Build the ``Store`` key for one config entry's baseline.

    Args:
        entry_id: The config entry identifier.

    Returns:
        The documented storage key for that entry.
    """
    return f"guesty.{entry_id}.reservations_identity"


def observed_code_map(
    reservations: Iterable[GuestyReservation],
) -> dict[str, str]:
    """Build this refresh's confirmation code to identifier map.

    Reservations held at an alteration status are excluded: a
    proposed change is a distinct record that may carry the
    confirmation code of the reservation it proposes to amend, so
    admitting one would manufacture a conflict the guard is not
    meant to catch. A reservation without a confirmation code
    contributes nothing (FR-033).

    Args:
        reservations: Reservations the refresh retrieved.

    Returns:
        Mapping of confirmation code to reservation identifier.
    """
    mapping: dict[str, str] = {}
    for reservation in reservations:
        if reservation.status in HOLD_STATUSES:
            continue
        if not reservation.confirmation_code:
            continue
        mapping[reservation.confirmation_code] = reservation.id
    return mapping


@dataclass(frozen=True)
class CandidateHint:
    """Durable fallback candidacy record for one reservation.

    Holds identifiers and dates only — never guest data (FR-049a).

    Attributes:
        check_in: ISO-8601 UTC check-in instant.
        check_out: ISO-8601 UTC check-out instant.
    """

    check_in: str
    check_out: str

    def to_dict(self) -> dict[str, str]:
        """Serialize the hint for storage.

        Returns:
            A JSON-serializable mapping of the hint.
        """
        return {"check_in": self.check_in, "check_out": self.check_out}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CandidateHint:
        """Deserialize a hint from storage.

        Args:
            data: The stored mapping.

        Returns:
            The reconstructed hint.
        """
        return cls(
            check_in=str(data.get("check_in", "")),
            check_out=str(data.get("check_out", "")),
        )


@dataclass(frozen=True)
class IdentityConflict:
    """One confirmation code whose identifier changed.

    The confirmation code itself is deliberately absent so that no
    caller can log it (FR-021).

    Attributes:
        known_id: Reservation identifier the baseline recorded.
        observed_id: Reservation identifier the refresh reported.
        source: Name of the baseline map that disagreed.
    """

    known_id: str
    observed_id: str
    source: str


@dataclass(frozen=True)
class IdentityComparison:
    """Outcome of comparing one refresh against the baseline.

    Attributes:
        matched: Distinct previously known identifiers that matched.
        cross_path_matched: Matches drawn from legacy-sourced
            evidence, which alone can confirm continuity (FR-031d).
        compared: Confirmation codes the comparison could test.
        conflicts: Every disagreement found.
    """

    matched: int = 0
    cross_path_matched: int = 0
    compared: int = 0
    conflicts: tuple[IdentityConflict, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        """Report whether the comparison found a disagreement.

        Returns:
            True when at least one mapping changed.
        """
        return bool(self.conflicts)


@dataclass
class IdentityBaseline:
    """The persisted identity baseline of one config entry.

    Attributes:
        path: Reservation path that produced the baseline.
        continuity: Cross-path continuity state.
        updated: ISO timestamp of the last successful refresh.
        codes: Confirmation code to reservation identifier.
        bootstrap_codes: Retained legacy bootstrap evidence.
        fallback_codes: Evidence learned through the fallback.
        candidates: Reservation identifier to stay dates.
    """

    path: str
    continuity: str = CONTINUITY_UNCONFIRMED
    updated: str = ""
    codes: dict[str, str] = field(default_factory=dict)
    bootstrap_codes: dict[str, str] | None = None
    fallback_codes: dict[str, str] | None = None
    candidates: dict[str, CandidateHint] = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        """Report whether the baseline can answer a comparison.

        An empty baseline must never be read as "no conflict found"
        (FR-031c).

        Returns:
            True when at least one code mapping is known.
        """
        return bool(self.codes or self.bootstrap_codes or self.fallback_codes)

    def known_ids(self) -> set[str]:
        """Collect every reservation identifier the maps carry.

        Returns:
            Identifiers known to carry a confirmation code.
        """
        known: set[str] = set(self.codes.values())
        known.update((self.bootstrap_codes or {}).values())
        known.update((self.fallback_codes or {}).values())
        return known

    def _maps(self) -> tuple[tuple[str, dict[str, str], bool], ...]:
        """Enumerate the code maps with their provenance.

        ``fallback_codes`` is legacy-sourced but is never treated as
        confirming evidence, because a fallback record would let the
        legacy path confirm itself (data-model.md §4).

        Returns:
            Tuples of map name, mapping and cross-path eligibility.
        """
        return (
            (
                SOURCE_CODES,
                self.codes,
                self.path == RESERVATIONS_API_LEGACY,
            ),
            (SOURCE_BOOTSTRAP, self.bootstrap_codes or {}, True),
            (SOURCE_FALLBACK, self.fallback_codes or {}, False),
        )

    def compare(
        self,
        observed: Mapping[str, str],
    ) -> IdentityComparison:
        """Compare an observed code map against every stored map.

        Runs purely on data the refresh already fetched, so it costs
        no request (FR-034). A code the refresh did not report is
        neither a match nor a conflict; a code that now names a
        different identifier is a conflict even when its previous
        identifier is still present, which is the swap case FR-032
        exists to catch.

        Args:
            observed: This refresh's code to identifier mapping.

        Returns:
            The comparison outcome.
        """
        matched_ids: set[str] = set()
        cross_path_ids: set[str] = set()
        conflicts: list[IdentityConflict] = []
        compared = 0

        for source, mapping, is_cross_path in self._maps():
            for code, known_id in mapping.items():
                observed_id = observed.get(code)
                if observed_id is None:
                    continue
                compared += 1
                if observed_id == known_id:
                    matched_ids.add(known_id)
                    if is_cross_path:
                        cross_path_ids.add(known_id)
                    continue
                conflicts.append(
                    IdentityConflict(
                        known_id=known_id,
                        observed_id=observed_id,
                        source=source,
                    ),
                )

        return IdentityComparison(
            matched=len(matched_ids),
            cross_path_matched=len(cross_path_ids),
            compared=compared,
            conflicts=tuple(conflicts),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the baseline for storage.

        Returns:
            A JSON-serializable mapping of the baseline.
        """
        return {
            "version": STORAGE_VERSION,
            "path": self.path,
            "continuity": self.continuity,
            "updated": self.updated,
            "codes": dict(self.codes),
            "bootstrap_codes": (
                dict(self.bootstrap_codes) if self.bootstrap_codes is not None else None
            ),
            "fallback_codes": (
                dict(self.fallback_codes) if self.fallback_codes is not None else None
            ),
            "candidates": {
                reservation_id: hint.to_dict()
                for reservation_id, hint in self.candidates.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IdentityBaseline:
        """Deserialize a baseline from storage.

        Args:
            data: The stored mapping.

        Returns:
            The reconstructed baseline.
        """
        return cls(
            path=str(data.get("path", RESERVATIONS_API_LEGACY)),
            continuity=str(data.get("continuity", CONTINUITY_UNCONFIRMED)),
            updated=str(data.get("updated", "")),
            codes=dict(data.get("codes") or {}),
            bootstrap_codes=_optional_map(data.get("bootstrap_codes")),
            fallback_codes=_optional_map(data.get("fallback_codes")),
            candidates={
                reservation_id: CandidateHint.from_dict(hint)
                for reservation_id, hint in (data.get("candidates") or {}).items()
            },
        )


def _optional_map(value: Any) -> dict[str, str] | None:
    """Normalize an optional stored code map.

    Args:
        value: The stored value, which may be absent or null.

    Returns:
        The mapping, or None when it was never written.
    """
    if value is None:
        return None
    return dict(value)


class ReservationIdentityGuard:
    """Owns one config entry's identity baseline and verdicts.

    Attributes:
        baseline: The loaded baseline, or None before first use.
        last_refresh_ambiguous: Whether the last refresh aborted.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the guard for one config entry.

        Args:
            hass: Home Assistant instance.
            entry_id: The config entry identifier.
        """
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            storage_key(entry_id),
        )
        self._baseline: IdentityBaseline | None = None
        self._loaded = False
        self._ambiguous = False

    @property
    def loaded(self) -> bool:
        """Report whether the baseline has been read from storage.

        Returns:
            True once ``async_load`` has run for this setup.
        """
        return self._loaded

    @property
    def baseline(self) -> IdentityBaseline | None:
        """Return the loaded baseline.

        Returns:
            The baseline, or None when none is stored.
        """
        return self._baseline

    @property
    def last_refresh_ambiguous(self) -> bool:
        """Report whether the last refresh aborted as ambiguous.

        Returns:
            True while an unresolved ambiguity blocks writes.
        """
        return self._ambiguous

    async def async_load(self) -> None:
        """Read the baseline from storage once per setup.

        A corrupt or unreadable payload is treated as no baseline,
        which fails writes closed rather than opening them.
        """
        raw = await self._store.async_load()
        if raw:
            try:
                self._baseline = IdentityBaseline.from_dict(raw)
            except AttributeError, TypeError, ValueError:
                self._baseline = None
        self._loaded = True

    async def async_store(self, baseline: IdentityBaseline) -> None:
        """Persist a baseline and adopt it in memory.

        Args:
            baseline: The baseline to persist.
        """
        self._baseline = baseline
        await self._store.async_save(baseline.to_dict())

    async def async_remove(self) -> None:
        """Delete the persisted baseline with its config entry."""
        self._baseline = None
        self._loaded = False
        await self._store.async_remove()

    def compare(
        self,
        reservations: Iterable[GuestyReservation],
    ) -> IdentityComparison:
        """Compare a refresh against the baseline, free of requests.

        Args:
            reservations: Reservations the refresh retrieved.

        Returns:
            The comparison outcome; empty when no baseline exists.
        """
        if self._baseline is None:
            return IdentityComparison()
        return self._baseline.compare(observed_code_map(reservations))

    def mark_ambiguous(self) -> None:
        """Record that a refresh aborted on an ambiguous identity."""
        self._ambiguous = True

    async def async_record_refresh(
        self,
        *,
        path: str,
        reservations: Iterable[GuestyReservation],
        published: Mapping[str, CandidateHint],
        comparison: IdentityComparison,
        updated: str,
    ) -> None:
        """Persist the baseline produced by a successful refresh.

        ``codes`` is replaced wholesale while ``bootstrap_codes`` and
        ``fallback_codes`` are left untouched, so a disjoint refresh
        can never discard the only cross-path evidence
        (data-model.md §4).

        Args:
            path: Reservation path that produced this refresh.
            reservations: Reservations the refresh retrieved.
            published: Stay hints for every reservation the refresh
                published, keyed by reservation identifier.
            comparison: The comparison this refresh already ran.
            updated: ISO timestamp of this refresh.
        """
        previous = self._baseline
        retrieved = tuple(reservations)
        check_outs = {
            reservation.id: reservation.check_out.isoformat()
            for reservation in retrieved
        }
        baseline = IdentityBaseline(
            path=path,
            continuity=_next_continuity(previous, path, comparison),
            updated=updated,
            codes=_bounded(observed_code_map(retrieved), check_outs),
            bootstrap_codes=previous.bootstrap_codes if previous else None,
            fallback_codes=previous.fallback_codes if previous else None,
            candidates=_bounded_candidates(dict(published), previous),
        )
        self._ambiguous = False
        await self.async_store(baseline)

    def write_refusal(self, reservation_id: str) -> str | None:
        """Evaluate the four-conjunct write predicate (FR-031c).

        Every conjunct is tested independently so that a refusal can
        explain itself: a usable baseline must exist, its continuity
        must not be unconfirmed, the last refresh must not have
        aborted as ambiguous, and the target must carry a known
        confirmation code.

        Args:
            reservation_id: The reservation a write would target.

        Returns:
            An explanatory refusal, or None when the write may go
            ahead.
        """
        baseline = self._baseline
        if baseline is None or not baseline.is_usable:
            return REFUSAL_NO_BASELINE
        if baseline.continuity == CONTINUITY_UNCONFIRMED:
            return REFUSAL_UNCONFIRMED
        if self._ambiguous:
            return REFUSAL_AMBIGUOUS
        if reservation_id not in baseline.known_ids():
            return REFUSAL_NO_CODE.format(reservation_id=reservation_id)
        return None


def _next_continuity(
    previous: IdentityBaseline | None,
    path: str,
    comparison: IdentityComparison,
) -> str:
    """Decide the continuity state a successful refresh leaves.

    On the legacy path no cross-path question arises: identifiers
    come from the legacy resource and writes go back to it. On the
    v3 path, continuity is confirmed only by legacy-sourced
    evidence, because a baseline built from v3 alone is
    self-consistent by construction (FR-031d). The one-time
    bootstrap sweep of T073 supplies that evidence for an upgrading
    entry that has none; until then, and whenever the evidence is
    uninformative, continuity stays unconfirmed and writes stay
    blocked.

    Args:
        previous: The baseline this refresh replaces, if any.
        path: Reservation path that produced this refresh.
        comparison: The comparison this refresh ran.

    Returns:
        The continuity state to persist.
    """
    if path == RESERVATIONS_API_LEGACY:
        return CONTINUITY_NOT_APPLICABLE
    if previous is not None and previous.continuity == CONTINUITY_VERIFIED:
        return CONTINUITY_VERIFIED
    if comparison.cross_path_matched > 0:
        return CONTINUITY_VERIFIED
    return CONTINUITY_UNCONFIRMED


def _bounded(
    codes: dict[str, str],
    check_outs: Mapping[str, str],
) -> dict[str, str]:
    """Apply the code-map size bound, evicting earliest check-out.

    Args:
        codes: The code map to bound.
        check_outs: ISO check-out per reservation identifier.

    Returns:
        The map, trimmed to at most ``MAX_BASELINE_CODES`` entries.
    """
    if len(codes) <= MAX_BASELINE_CODES:
        return codes
    ordered = sorted(
        codes.items(),
        key=lambda item: check_outs.get(item[1], ""),
    )
    return dict(ordered[-MAX_BASELINE_CODES:])


def _bounded_candidates(
    candidates: dict[str, CandidateHint],
    previous: IdentityBaseline | None,
) -> dict[str, CandidateHint]:
    """Apply the same size bound to the candidate hints.

    Hints retained from the previous baseline keep a reservation
    that has temporarily vanished from a refresh eligible for the
    Phase 6a fallback (FR-049a).

    Args:
        candidates: Hints for the reservations just published.
        previous: The baseline this refresh replaces, if any.

    Returns:
        The merged hints, trimmed to ``MAX_BASELINE_CODES``.
    """
    merged = dict(previous.candidates) if previous else {}
    merged.update(candidates)
    if len(merged) <= MAX_BASELINE_CODES:
        return merged
    ordered = sorted(merged.items(), key=lambda item: item[1].check_out)
    return dict(ordered[-MAX_BASELINE_CODES:])

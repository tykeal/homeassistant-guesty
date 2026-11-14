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

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
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

_LOGGER = logging.getLogger(__name__)

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
SOURCE_LEGACY: Final[str] = "legacy_codes"
SOURCE_OBSERVED: Final[str] = "observed"

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
REFUSAL_UNSTORED: Final[str] = (
    "The Guesty reservation identity baseline could not be written "
    "to storage, so it cannot be trusted and this write is refused. "
    "Check the Home Assistant storage directory, then reload the "
    "entry."
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


@dataclass(frozen=True)
class ObservedCodes:
    """One refresh's confirmation code evidence.

    Attributes:
        mapping: Confirmation code to reservation identifier.
        collisions: Identifier pairs that reported the same
            confirmation code within this one refresh.
    """

    mapping: dict[str, str] = field(default_factory=dict)
    collisions: tuple[tuple[str, str], ...] = ()


def observe_codes(
    reservations: Iterable[GuestyReservation],
) -> ObservedCodes:
    """Read this refresh's confirmation code evidence.

    Reservations held at an alteration status are excluded: a
    proposed change is a distinct record that may carry the
    confirmation code of the reservation it proposes to amend, so
    admitting one would manufacture a conflict the guard is not
    meant to catch. A reservation without a confirmation code
    contributes nothing (FR-033).

    Two live reservations reporting the same confirmation code are
    recorded as a collision rather than silently collapsed. The
    later row would otherwise overwrite the earlier one and could
    hide a changed mapping entirely, and a code naming two
    reservations at once is itself an identity the guard cannot
    trust.

    Args:
        reservations: Reservations the refresh retrieved.

    Returns:
        The mapping and any within-refresh collisions.
    """
    mapping: dict[str, str] = {}
    collisions: list[tuple[str, str]] = []
    for reservation in reservations:
        if reservation.status in HOLD_STATUSES:
            continue
        if not reservation.confirmation_code:
            continue
        previous = mapping.get(reservation.confirmation_code)
        if previous is not None and previous != reservation.id:
            collisions.append((previous, reservation.id))
            continue
        mapping[reservation.confirmation_code] = reservation.id
    return ObservedCodes(mapping=mapping, collisions=tuple(collisions))


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
        conflicts: Disagreements within one reservation path, which
            can only mean a reservation was re-keyed.
        cross_path_conflicts: Disagreements between evidence from
            different reservation paths, which is the identifier
            divergence this migration cannot rule out.
    """

    matched: int = 0
    cross_path_matched: int = 0
    compared: int = 0
    conflicts: tuple[IdentityConflict, ...] = ()
    cross_path_conflicts: tuple[IdentityConflict, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        """Report whether the refresh must be aborted.

        Only a same-path disagreement aborts. Evidence from the
        other path disagreeing is the uncertainty this migration
        carries rather than a re-key: it means the two paths number
        the same booking differently, which must block writes but
        must not strand the integration — otherwise rolling back to
        the legacy path could never complete, and the setup refresh
        would leave even the listings unloaded.

        Returns:
            True when at least one same-path mapping changed.
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
        legacy_codes: Legacy-path evidence retained when the entry
            moved to the v3 path, so one disjoint refresh cannot
            discard the only cross-path evidence there is.
        bootstrap_codes: Retained legacy bootstrap evidence.
        fallback_codes: Evidence learned through the fallback.
        candidates: Reservation identifier to stay dates.
    """

    path: str
    continuity: str = CONTINUITY_UNCONFIRMED
    updated: str = ""
    codes: dict[str, str] = field(default_factory=dict)
    legacy_codes: dict[str, str] | None = None
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
        return bool(
            self.codes
            or self.legacy_codes
            or self.bootstrap_codes
            or self.fallback_codes
        )

    def known_ids(self) -> set[str]:
        """Collect every reservation identifier the maps carry.

        Returns:
            Identifiers known to carry a confirmation code.
        """
        known: set[str] = set(self.codes.values())
        known.update((self.legacy_codes or {}).values())
        known.update((self.bootstrap_codes or {}).values())
        known.update((self.fallback_codes or {}).values())
        return known

    def _maps(
        self,
        path: str,
    ) -> tuple[tuple[str, dict[str, str], bool], ...]:
        """Enumerate the code maps with their provenance.

        Provenance is relative to the path now in use: evidence this
        entry gathered on the other path is what can confirm
        continuity, and what disagreeing proves nothing about a
        re-key. ``fallback_codes`` is legacy-sourced but is never
        treated as confirming evidence, because a fallback record
        would let the legacy path confirm itself (data-model.md §4).

        Args:
            path: Reservation path the compared refresh used.

        Returns:
            Tuples of map name, mapping and cross-path provenance.
        """
        legacy = RESERVATIONS_API_LEGACY
        return (
            (SOURCE_CODES, self.codes, self.path != path),
            (SOURCE_LEGACY, self.legacy_codes or {}, path != legacy),
            (SOURCE_BOOTSTRAP, self.bootstrap_codes or {}, path != legacy),
            (SOURCE_FALLBACK, self.fallback_codes or {}, False),
        )

    def compare(
        self,
        observed: Mapping[str, str],
        path: str,
    ) -> IdentityComparison:
        """Compare an observed code map against every stored map.

        Runs purely on data the refresh already fetched, so it costs
        no request (FR-034). A code the refresh did not report is
        neither a match nor a conflict; a code that now names a
        different identifier is a conflict even when its previous
        identifier is still present, which is the swap case FR-032
        exists to catch.

        Conflicts are separated by provenance; see
        ``IdentityComparison.is_ambiguous``.

        Args:
            observed: This refresh's code to identifier mapping.
            path: Reservation path that produced the observation.

        Returns:
            The comparison outcome.
        """
        matched_ids: set[str] = set()
        cross_path_ids: set[str] = set()
        conflicts: list[IdentityConflict] = []
        cross_path_conflicts: list[IdentityConflict] = []
        compared = 0

        for source, mapping, is_cross_path in self._maps(path):
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
                conflict = IdentityConflict(
                    known_id=known_id,
                    observed_id=observed_id,
                    source=source,
                )
                if is_cross_path:
                    cross_path_conflicts.append(conflict)
                else:
                    conflicts.append(conflict)

        return IdentityComparison(
            matched=len(matched_ids),
            cross_path_matched=len(cross_path_ids),
            compared=compared,
            conflicts=tuple(conflicts),
            cross_path_conflicts=tuple(cross_path_conflicts),
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
            "legacy_codes": (
                dict(self.legacy_codes) if self.legacy_codes is not None else None
            ),
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
            legacy_codes=_optional_map(data.get("legacy_codes")),
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
        self._unstored = False

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
        """Persist a baseline, adopting it only once it is written.

        An unwritten baseline must not authorize a write: a restart
        would read the previous one back, so the evidence a write
        was judged against would silently differ from the evidence
        that survives.

        Args:
            baseline: The baseline to persist.

        Raises:
            OSError: If the baseline could not be written.
        """
        await self._store.async_save(baseline.to_dict())
        self._baseline = baseline

    async def async_remove(self) -> None:
        """Delete the persisted baseline with its config entry."""
        self._baseline = None
        self._loaded = False
        await self._store.async_remove()

    def compare(
        self,
        reservations: Iterable[GuestyReservation],
        path: str,
    ) -> IdentityComparison:
        """Compare a refresh against the baseline, free of requests.

        A within-refresh collision is reported even when there is no
        baseline yet, so a confirmation code naming two reservations
        at once cannot establish the very first baseline either.

        Args:
            reservations: Reservations the refresh retrieved.
            path: Reservation path that produced the refresh.

        Returns:
            The comparison outcome.
        """
        observed = observe_codes(reservations)
        collisions = tuple(
            IdentityConflict(
                known_id=first,
                observed_id=second,
                source=SOURCE_OBSERVED,
            )
            for first, second in observed.collisions
        )
        if self._baseline is None:
            return IdentityComparison(conflicts=collisions)
        comparison = self._baseline.compare(observed.mapping, path)
        if not collisions:
            return comparison
        return replace(
            comparison,
            conflicts=collisions + comparison.conflicts,
        )

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

        ``codes`` is replaced wholesale while the retained maps are
        left untouched, so a disjoint refresh can never discard the
        only cross-path evidence (data-model.md §4). Leaving the
        legacy path retains its mappings for the same reason: a
        first v3 refresh that happens not to overlap them would
        otherwise destroy the evidence that can still confirm
        continuity once those reservations reappear.

        A storage failure never costs a refresh — the published data
        is already correct — but it does leave writes refused until
        the baseline is written.

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
        hints = _retrieved_hints(retrieved)
        hints.update(published)
        check_outs = {
            reservation_id: hint.check_out for reservation_id, hint in hints.items()
        }
        baseline = IdentityBaseline(
            path=path,
            continuity=_next_continuity(previous, path, comparison),
            updated=updated,
            codes=_bounded(observe_codes(retrieved).mapping, check_outs),
            legacy_codes=_retained_legacy(previous, path),
            bootstrap_codes=previous.bootstrap_codes if previous else None,
            fallback_codes=previous.fallback_codes if previous else None,
            candidates=_bounded_candidates(hints, previous),
        )
        try:
            await self.async_store(baseline)
        except OSError as exc:
            self._unstored = True
            _LOGGER.warning(
                "Could not persist the reservation identity "
                "baseline (%s); writes stay refused until it is "
                "stored",
                type(exc).__name__,
            )
            return
        self._unstored = False
        self._ambiguous = False

    def write_refusal(self, reservation_id: str) -> str | None:
        """Evaluate the four-conjunct write predicate (FR-031c).

        Every conjunct is tested independently so that a refusal can
        explain itself: a usable baseline must exist and must have
        reached storage, its continuity must not be unconfirmed, the
        last refresh must not have aborted as ambiguous, and the
        target must carry a known confirmation code.

        Args:
            reservation_id: The reservation a write would target.

        Returns:
            An explanatory refusal, or None when the write may go
            ahead.
        """
        baseline = self._baseline
        if baseline is None or not baseline.is_usable:
            return REFUSAL_NO_BASELINE
        if self._unstored:
            return REFUSAL_UNSTORED
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

    Evidence from the other path that positively disagrees demotes
    continuity on either path: the two paths then number the same
    booking differently, which is the one thing that must never be
    written through.

    Args:
        previous: The baseline this refresh replaces, if any.
        path: Reservation path that produced this refresh.
        comparison: The comparison this refresh ran.

    Returns:
        The continuity state to persist.
    """
    if comparison.cross_path_conflicts:
        return CONTINUITY_UNCONFIRMED
    if path == RESERVATIONS_API_LEGACY:
        return CONTINUITY_NOT_APPLICABLE
    if previous is not None and previous.continuity == CONTINUITY_VERIFIED:
        return CONTINUITY_VERIFIED
    if comparison.cross_path_matched > 0:
        return CONTINUITY_VERIFIED
    return CONTINUITY_UNCONFIRMED


def _retained_legacy(
    previous: IdentityBaseline | None,
    path: str,
) -> dict[str, str] | None:
    """Retain legacy mappings when the entry leaves that path.

    Args:
        previous: The baseline this refresh replaces, if any.
        path: Reservation path that produced this refresh.

    Returns:
        The retained legacy evidence, or None when there is none.
    """
    if previous is None:
        return None
    retained = dict(previous.legacy_codes or {})
    if previous.path == RESERVATIONS_API_LEGACY and path != RESERVATIONS_API_LEGACY:
        retained.update(previous.codes)
    return retained or None


def _retrieved_hints(
    reservations: Iterable[GuestyReservation],
) -> dict[str, CandidateHint]:
    """Record stay dates for every reservation retrieved.

    Hints are taken before attribution and the window trim, so a
    reservation held back from publication — for an untracked
    listing, or because its stay fell outside the window — still
    gains the durable dates the Phase 6a fallback judges eligibility
    by (data-model.md §4.1).

    Args:
        reservations: Reservations the refresh retrieved.

    Returns:
        Stay hints keyed by reservation identifier.
    """
    return {
        reservation.id: CandidateHint(
            check_in=reservation.check_in.isoformat(),
            check_out=reservation.check_out.isoformat(),
        )
        for reservation in reservations
    }


def _instant(value: str) -> datetime:
    """Parse a stored ISO instant for ordering.

    Stored instants keep the offset Guesty reported, so ordering
    them as text would rank a later stay before an earlier one.
    Anything unparsable sorts earliest, which evicts it first.

    Args:
        value: The stored ISO-8601 instant.

    Returns:
        A timezone-aware datetime.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


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
        key=lambda item: _instant(check_outs.get(item[1], "")),
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
    ordered = sorted(
        merged.items(),
        key=lambda item: _instant(item[1].check_out),
    )
    return dict(ordered[-MAX_BASELINE_CODES:])

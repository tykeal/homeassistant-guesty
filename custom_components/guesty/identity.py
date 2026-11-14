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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from custom_components.guesty.api.const import (
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
    HOLD_STATUSES,
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
)
from custom_components.guesty.api.exceptions import GuestyApiError
from custom_components.guesty.api.reservations_legacy import (
    LegacyReservationSource,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.guesty.api.client import GuestyApiClient
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

# Outcomes of the one-time bootstrap sweep (FR-031d).
BOOTSTRAP_SWEPT: Final[str] = "swept"
BOOTSTRAP_UNAVAILABLE: Final[str] = "unavailable"
BOOTSTRAP_FRESH_INSTALL: Final[str] = "fresh_install"
BOOTSTRAP_ALREADY_DONE: Final[str] = "already_done"
BOOTSTRAP_AMBIGUOUS: Final[str] = "ambiguous"
BOOTSTRAP_LEGACY_PATH: Final[str] = "legacy_path"

# Unique-id marker of a reservation-backed entity, used to tell an
# upgrading entry from a fresh install.
RESERVATION_ENTITY_MARKER: Final[str] = "_reservation"

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

    The payload is accepted as evidence only when the code is a
    non-empty string. Neither parser constrains the field, and a
    number would change type across JSON storage while a list or
    object is not a key at all.

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
        code = reservation.confirmation_code
        if not isinstance(code, str) or not code:
            continue
        previous = mapping.get(code)
        if previous is not None and previous != reservation.id:
            collisions.append((previous, reservation.id))
            continue
        mapping[code] = reservation.id
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
        divergent: Whether the two reservation paths were ever seen
            to number the same booking differently.
        bootstrap_attempted: Whether the one-time legacy bootstrap
            sweep has already been decided for this entry, however
            it turned out. It is what makes the sweep genuinely
            one-time across restarts and reloads (FR-031d).
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
    divergent: bool = False
    bootstrap_attempted: bool = False
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

    def current_ids(self) -> set[str]:
        """Collect identifiers that carry a code *right now*.

        Retained evidence proves what a code once named, not that
        the reservation still reports one, so FR-033 is answered by
        the current refresh plus any reservation the fallback is
        actively serving — never by history.

        Returns:
            Identifiers eligible to be written to.
        """
        current: set[str] = set(self.codes.values())
        current.update((self.fallback_codes or {}).values())
        return current

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
    ) -> tuple[tuple[str, dict[str, str], bool, bool], ...]:
        """Enumerate the code maps with their provenance.

        Two independent properties are reported per map, because
        they are not the same question.

        *Confirms* says whether a match in that map is cross-path
        evidence, which is the only thing that can establish
        continuity. ``fallback_codes`` is legacy-sourced but never
        confirms, because a fallback record would let the legacy
        path confirm itself (data-model.md §4).

        *Soft* says whether a **conflict** in that map should demote
        continuity instead of aborting the refresh. Evidence carried
        over from an earlier path — ``codes`` written by the other
        path, and ``legacy_codes`` retained across a switch — is
        soft, so that rolling back to the legacy path can always
        complete rather than stranding the entry.

        ``bootstrap_codes`` is deliberately **not** soft. The sweep
        exists precisely to be contradicted, and FR-032, plan.md
        ("bootstrap disagrees ... treated exactly as FR-032") and
        T067 all require a disagreeing bootstrap to abort. Rollback
        still completes, because on the legacy path the bootstrap
        map is same-path evidence that agrees by construction.

        Args:
            path: Reservation path the compared refresh used.

        Returns:
            Tuples of map name, mapping, confirms and soft.
        """
        legacy = RESERVATIONS_API_LEGACY
        carried_over = self.path != path
        return (
            (SOURCE_CODES, self.codes, carried_over, carried_over),
            (
                SOURCE_LEGACY,
                self.legacy_codes or {},
                path != legacy,
                path != legacy,
            ),
            (
                SOURCE_BOOTSTRAP,
                self.bootstrap_codes or {},
                path != legacy,
                False,
            ),
            (SOURCE_FALLBACK, self.fallback_codes or {}, False, False),
        )

    def compare(
        self,
        observed: Mapping[str, str],
        path: str,
        fallback_ids: Collection[str] = (),
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
            fallback_ids: Identifiers this refresh observed through
                the legacy not-found fallback. Such an observation
                can never *confirm* continuity, because it came
                from the legacy resource itself and so agreeing
                with legacy-sourced evidence says nothing about v3
                (FR-055). By the same token a disagreement is never
                merely cross-path: both sides came from legacy, so
                it can only be a re-key and it aborts the refresh
                (FR-032).

        Returns:
            The comparison outcome.
        """
        matched_ids: set[str] = set()
        cross_path_ids: set[str] = set()
        conflicts: list[IdentityConflict] = []
        cross_path_conflicts: list[IdentityConflict] = []
        compared = 0
        from_fallback = frozenset(fallback_ids)

        for source, mapping, confirms, soft in self._maps(path):
            for code, known_id in mapping.items():
                observed_id = observed.get(code)
                if observed_id is None:
                    continue
                compared += 1
                if observed_id == known_id:
                    matched_ids.add(known_id)
                    if confirms and observed_id not in from_fallback:
                        cross_path_ids.add(known_id)
                    continue
                conflict = IdentityConflict(
                    known_id=known_id,
                    observed_id=observed_id,
                    source=source,
                )
                if soft and observed_id not in from_fallback:
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
            "divergent": self.divergent,
            "bootstrap_attempted": self.bootstrap_attempted,
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
            divergent=bool(data.get("divergent", False)),
            bootstrap_attempted=bool(
                data.get("bootstrap_attempted", False),
            ),
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
        fallback_ids: Collection[str] = (),
    ) -> IdentityComparison:
        """Compare a refresh against the baseline, free of requests.

        A within-refresh collision is reported even when there is no
        baseline yet, so a confirmation code naming two reservations
        at once cannot establish the very first baseline either.

        Args:
            reservations: Reservations the refresh retrieved.
            path: Reservation path that produced the refresh.
            fallback_ids: Identifiers this refresh published from
                the legacy not-found fallback, which may not confirm
                continuity (FR-055).

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
        comparison = self._baseline.compare(
            observed.mapping,
            path,
            fallback_ids,
        )
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
        past_days: int = DEFAULT_PAST_DAYS,
        fallback_ids: Collection[str] = (),
        absent_ids: Collection[str] = (),
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
            past_days: The configured past-day window, which dates
                the safe retirement policy of T074.
            fallback_ids: Identifiers this refresh published from
                the legacy not-found fallback rather than from the
                configured path. Their codes are recorded apart, in
                ``fallback_codes``, so that evidence retrieved from
                a legacy resource is never mistaken for evidence the
                configured path produced (FR-055).
            absent_ids: Identifiers the legacy resource confirmed no
                longer exist. Their candidacy is retired for good,
                which is what stops the fallback resurrecting a
                genuinely cancelled reservation (FR-052).
        """
        previous = self._baseline
        retrieved = tuple(reservations)
        from_fallback = frozenset(fallback_ids)
        absent = frozenset(absent_ids)
        all_observed = observe_codes(retrieved).mapping
        observed = {
            code: reservation_id
            for code, reservation_id in all_observed.items()
            if reservation_id not in from_fallback
        }
        fallback_observed = {
            code: reservation_id
            for code, reservation_id in all_observed.items()
            if reservation_id in from_fallback
        }
        hints = _retrieved_hints(retrieved)
        hints.update(published)
        merged_hints = dict(previous.candidates) if previous else {}
        merged_hints.update(hints)
        for reservation_id in absent:
            merged_hints.pop(reservation_id, None)
        check_outs = {
            reservation_id: hint.check_out
            for reservation_id, hint in merged_hints.items()
        }
        divergent = bool(comparison.cross_path_conflicts) or bool(
            previous and previous.divergent,
        )
        baseline = IdentityBaseline(
            path=path,
            continuity=_next_continuity(previous, path, comparison, divergent),
            updated=updated,
            divergent=divergent,
            bootstrap_attempted=bool(previous and previous.bootstrap_attempted),
            codes=_bounded(observed, check_outs),
            legacy_codes=_retained_legacy(previous, path, check_outs),
            bootstrap_codes=_retired_bootstrap(
                previous,
                observed,
                check_outs,
                past_days,
            ),
            fallback_codes=_bounded(fallback_observed, check_outs),
            candidates=_bounded_candidates(merged_hints),
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

    async def async_record_fresh_install(self, path: str) -> None:
        """Settle a fresh install's continuity without a request.

        An entry with no baseline and no reservation entities has no
        earlier identity to contradict, so there is nothing for a
        sweep to reconcile and the legacy resource is never touched.
        The verdict is persisted rather than re-derived, because the
        entity registry will be populated by the time of the next
        start and the entry would then look like an upgrade.

        Args:
            path: Reservation path the entry is using.
        """
        current = self._baseline or IdentityBaseline(path=path)
        await self._store_bootstrap(
            replace(
                current,
                continuity=CONTINUITY_NOT_APPLICABLE,
                bootstrap_attempted=True,
            ),
        )

    async def async_record_bootstrap(
        self,
        *,
        reservations: Iterable[GuestyReservation],
        failed: bool,
    ) -> None:
        """Persist the outcome of the one-time sweep (FR-031d).

        The attempt is recorded whether or not it produced evidence,
        so that an unavailable legacy resource cannot turn the single
        permitted legacy call into a standing dependency that retries
        on every start.

        Stay dates are kept alongside the codes because T074's safe
        retirement policy needs to know when a swept reservation
        checked out; they carry identifiers and dates only (FR-049a).

        Args:
            reservations: Reservations the sweep retrieved.
            failed: Whether the sweep could not be completed.
        """
        if failed:
            current = self._baseline or IdentityBaseline(
                path=RESERVATIONS_API_V3,
            )
            await self._store_bootstrap(
                replace(current, bootstrap_attempted=True),
            )
            return

        retrieved = tuple(reservations)
        current = self._baseline or IdentityBaseline(
            path=RESERVATIONS_API_LEGACY,
        )
        merged = dict(current.candidates)
        merged.update(_retrieved_hints(retrieved))
        await self._store_bootstrap(
            replace(
                current,
                bootstrap_attempted=True,
                bootstrap_codes=_bounded(
                    observe_codes(retrieved).mapping,
                    {res_id: hint.check_out for res_id, hint in merged.items()},
                ),
                candidates=_bounded_candidates(merged),
            ),
        )

    async def _store_bootstrap(self, baseline: IdentityBaseline) -> None:
        """Persist a bootstrap verdict, tolerating a storage failure.

        A failure leaves ``bootstrap_attempted`` unwritten, so the
        sweep is reattempted at the next start. That is the only
        honest outcome when nothing can be persisted: the alternative
        is an in-memory flag that silently forgets itself anyway.
        Writes stay refused throughout, because an unstored baseline
        cannot authorize one.

        The verdict is nevertheless adopted in memory. The permitted
        legacy call has already been made, and discarding what it
        returned would let a later refresh verify continuity through
        an older matching code while the swept evidence that
        contradicts it went unexamined. Held in memory it is still
        compared, and the first refresh that reaches storage persists
        it; until then the unstored conjunct keeps writes refused.

        Args:
            baseline: The baseline to persist.
        """
        try:
            await self.async_store(baseline)
        except OSError as exc:
            self._baseline = baseline
            self._unstored = True
            _LOGGER.warning(
                "Could not persist the reservation identity "
                "bootstrap (%s); its evidence is still compared, but "
                "writes stay refused until a baseline reaches storage",
                type(exc).__name__,
            )

    def write_refusal(self, reservation_id: str) -> str | None:
        """Evaluate the four-conjunct write predicate (FR-031c).

        Every conjunct is tested independently so that a refusal can
        explain itself: a usable baseline must exist and must have
        reached storage, its continuity must not be unconfirmed, the
        last refresh must not have aborted as ambiguous, and the
        target must carry a confirmation code *now* — historical
        evidence that it once did is not enough (FR-033).

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
        if reservation_id not in baseline.current_ids():
            return REFUSAL_NO_CODE.format(reservation_id=reservation_id)
        return None


def _retired_bootstrap(
    previous: IdentityBaseline | None,
    observed: Mapping[str, str],
    check_outs: Mapping[str, str],
    past_days: int,
) -> dict[str, str] | None:
    """Retire bootstrap evidence one entry at a time (T074).

    A successful refresh replaces ``codes`` but never replaces this
    map wholesale, because a disjoint refresh would then discard the
    only cross-path evidence there is. Instead each entry is
    discharged on its own terms:

    * a code this refresh covered without conflict has done its job
      and is removed — the comparison that read it ran before this
      call, so its match is already counted;
    * a code this refresh never mentioned is retained, so a partial
      overlap cannot discharge what it did not cover, and reaching
      ``verified`` does not clear the remainder;
    * a retained code is retired unchecked only under the safe
      policy that its reservation checked out more than ``past_days``
      ago and so can never appear in a future window, which also
      keeps the map bounded.

    A conflicting code is kept. It cannot normally reach here — a
    bootstrap conflict aborts the refresh — but keeping it means the
    disagreement stays derivable from the stored evidence rather
    than only remembered in a flag.

    Args:
        previous: The baseline this refresh replaces, if any.
        observed: This refresh's code to identifier mapping.
        check_outs: ISO check-out per reservation identifier.
        past_days: The configured past-day window.

    Returns:
        The surviving bootstrap evidence, or None when the sweep
        never ran.
    """
    if previous is None or previous.bootstrap_codes is None:
        return None
    horizon = datetime.now(UTC) - timedelta(days=past_days)
    retained: dict[str, str] = {}
    for code, known_id in previous.bootstrap_codes.items():
        observed_id = observed.get(code)
        if observed_id == known_id:
            continue
        if observed_id is None:
            checked_out = check_outs.get(known_id)
            if checked_out is not None and _instant(checked_out) < horizon:
                continue
        retained[code] = known_id
    return retained


def _next_continuity(
    previous: IdentityBaseline | None,
    path: str,
    comparison: IdentityComparison,
    divergent: bool = False,
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
    written through. That verdict is sticky, because the next
    refresh replaces the evidence that produced it and would
    otherwise re-authorize writes while the disagreement stands.
    Only re-establishing the baseline, or the reconciliation the
    bootstrap sweep performs, can clear it.

    A ``not_applicable`` verdict is carried forward only while it
    remains true. It records that nothing from the other path exists
    to contradict, which is a fresh install's permanent situation but
    stops being true the moment legacy-sourced evidence is recorded —
    by the bootstrap sweep, or by a legacy refresh the entry later
    switched away from. See ``_still_not_applicable``.

    Args:
        previous: The baseline this refresh replaces, if any.
        path: Reservation path that produced this refresh.
        comparison: The comparison this refresh ran.
        divergent: Whether a path disagreement was ever recorded.

    Returns:
        The continuity state to persist.
    """
    if divergent or comparison.cross_path_conflicts:
        return CONTINUITY_UNCONFIRMED
    if path == RESERVATIONS_API_LEGACY:
        return CONTINUITY_NOT_APPLICABLE
    if previous is not None and previous.continuity == CONTINUITY_VERIFIED:
        return CONTINUITY_VERIFIED
    if previous is not None and _still_not_applicable(previous):
        return CONTINUITY_NOT_APPLICABLE
    if comparison.cross_path_matched > 0:
        return CONTINUITY_VERIFIED
    return CONTINUITY_UNCONFIRMED


def _still_not_applicable(previous: IdentityBaseline) -> bool:
    """Report whether "nothing to contradict" still holds.

    A fresh install settles at ``not_applicable`` because it has no
    earlier identity at all, and a later v3 refresh cannot
    manufacture one — so the verdict must survive, or writes would
    silently start being refused on the second refresh.

    It must **not** survive once legacy-sourced evidence exists. An
    entry that ran on the legacy path, or that was swept, does have
    an earlier identity, and whether the two paths agree about it is
    exactly the open question the guard carries.

    Args:
        previous: The baseline this refresh replaces.

    Returns:
        True when the verdict may be carried forward.
    """
    return (
        previous.continuity == CONTINUITY_NOT_APPLICABLE
        and previous.path != RESERVATIONS_API_LEGACY
        and not previous.legacy_codes
        and not previous.bootstrap_codes
    )


def _retained_legacy(
    previous: IdentityBaseline | None,
    path: str,
    check_outs: Mapping[str, str],
) -> dict[str, str] | None:
    """Retain legacy mappings when the entry leaves that path.

    Repeated transitions would otherwise accumulate one full window
    of legacy evidence each time, so the merged map takes the same
    bound as every other, evicting the earliest check-out it can
    date. Dates come from the hints this refresh is about to store,
    which merge the retained hints of earlier refreshes over the
    current ones, so a reservation absent from this refresh is
    still dated by its stored check-out.

    Args:
        previous: The baseline this refresh replaces, if any.
        path: Reservation path that produced this refresh.
        check_outs: ISO check-out per reservation identifier.

    Returns:
        The retained legacy evidence, or None when there is none.
    """
    if previous is None:
        return None
    retained = dict(previous.legacy_codes or {})
    if previous.path == RESERVATIONS_API_LEGACY and path != RESERVATIONS_API_LEGACY:
        retained.update(previous.codes)
    if not retained:
        return None
    return _bounded(retained, check_outs)


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
) -> dict[str, CandidateHint]:
    """Apply the same size bound to the candidate hints.

    Hints retained from the previous baseline keep a reservation
    that has temporarily vanished from a refresh eligible for the
    Phase 6a fallback (FR-049a); the caller merges them in before
    bounding so the stored dates also date code-map eviction.

    Args:
        candidates: Merged hints for every reservation still known.

    Returns:
        The hints, trimmed to ``MAX_BASELINE_CODES``.
    """
    merged = candidates
    if len(merged) <= MAX_BASELINE_CODES:
        return merged
    ordered = sorted(
        merged.items(),
        key=lambda item: _instant(item[1].check_out),
    )
    return dict(ordered[-MAX_BASELINE_CODES:])


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of deciding the one-time bootstrap sweep.

    Attributes:
        reason: Which of the six bootstrap outcomes applied.
        pages: Legacy pages the sweep fetched, zero when it did not
            run. This is the FR-001 exception-1 counter.
        codes: Confirmation codes the sweep recorded as evidence.
        error: Operator-facing reason the sweep was unavailable.
    """

    reason: str
    pages: int = 0
    codes: int = 0
    error: str | None = None

    @property
    def informative(self) -> bool:
        """Report whether the sweep produced usable evidence.

        Returns:
            True when the sweep ran and recorded at least one code.
        """
        return self.reason == BOOTSTRAP_SWEPT and self.codes > 0


def has_reservation_entities(hass: HomeAssistant, entry_id: str) -> bool:
    """Report whether this entry already published reservations.

    This is how an upgrade is told from a fresh install: a release
    that only ever spoke the legacy reservation API left reservation
    entities in the registry, and those entities are the identities
    a first v3 refresh could silently contradict. A fresh install
    has none, so it has nothing to reconcile and never touches the
    deprecated resource.

    Args:
        hass: Home Assistant instance.
        entry_id: The config entry identifier.

    Returns:
        True when a reservation-backed entity is registered.
    """
    registry = er.async_get(hass)
    return any(
        RESERVATION_ENTITY_MARKER in entity.unique_id
        for entity in er.async_entries_for_config_entry(registry, entry_id)
    )


async def async_bootstrap_identity(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    guard: ReservationIdentityGuard,
    api_client: GuestyApiClient,
    past_days: int = DEFAULT_PAST_DAYS,
    future_days: int = DEFAULT_FUTURE_DAYS,
) -> BootstrapResult:
    """Decide and, if required, perform the bootstrap sweep.

    This is **permitted legacy call 1 of FR-001** and the only
    request the identity guard may ever issue (FR-034). It runs at
    setup, before any v3 data is published, for an upgrading entry
    only, and the decision is persisted so it is taken once per
    config entry and never again — across restarts and reloads
    alike.

    A sweep whose own evidence is ambiguous — one confirmation code
    naming two legacy reservations — is discarded whole rather than
    stored with an arbitrary winner, because a v3 refresh returning
    only that winner would otherwise verify continuity and permit
    writes on a mapping the legacy side never agreed on. It still
    counts as attempted: the permitted call has been spent.

    The sweep is a single ``LegacyReservationSource`` retrieval over
    the configured window. It is not retried here: the HTTP client
    already retries a rate limit or a transient failure internally,
    and a sweep that retried on top of that would spend a scarce
    daily token allowance on an outcome the caller is required to
    tolerate anyway.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.
        guard: The entry's identity guard.
        api_client: API client the sweep issues its requests on.
        past_days: Days in the past for the sweep's window.
        future_days: Days in the future for the sweep's window.

    Returns:
        The bootstrap outcome, for logging and the FR-031c notice.
    """
    if not guard.loaded:
        await guard.async_load()

    path = api_client.reservations_api
    if path != RESERVATIONS_API_V3:
        return BootstrapResult(reason=BOOTSTRAP_LEGACY_PATH)

    baseline = guard.baseline
    if baseline is not None and baseline.bootstrap_attempted:
        return BootstrapResult(reason=BOOTSTRAP_ALREADY_DONE)

    if baseline is None and not has_reservation_entities(hass, entry.entry_id):
        await guard.async_record_fresh_install(path)
        _LOGGER.debug(
            "No baseline and no reservation entities for this entry; "
            "treating it as a fresh install and skipping the legacy "
            "identity bootstrap sweep",
        )
        return BootstrapResult(reason=BOOTSTRAP_FRESH_INSTALL)

    source = LegacyReservationSource(api_client)
    try:
        fetched = await source.fetch(
            past_days=past_days,
            future_days=future_days,
        )
    except GuestyApiError as exc:
        await guard.async_record_bootstrap(reservations=(), failed=True)
        _LOGGER.warning(
            "The one-time legacy identity bootstrap sweep could not "
            "be completed (%s); reservation identity continuity "
            "stays unconfirmed and reservation writes are refused",
            exc.message,
        )
        return BootstrapResult(
            reason=BOOTSTRAP_UNAVAILABLE,
            error=exc.message,
        )

    collisions = observe_codes(fetched.reservations).collisions
    if collisions:
        await guard.async_record_bootstrap(reservations=(), failed=True)
        _LOGGER.warning(
            "The one-time legacy identity bootstrap sweep returned %d "
            "confirmation code(s) naming more than one reservation, so "
            "its evidence cannot establish an identity baseline; "
            "reservation identity continuity stays unconfirmed and "
            "reservation writes are refused",
            len(collisions),
        )
        return BootstrapResult(
            reason=BOOTSTRAP_AMBIGUOUS,
            pages=fetched.pages,
        )

    await guard.async_record_bootstrap(
        reservations=fetched.reservations,
        failed=False,
    )
    recorded = len((guard.baseline.bootstrap_codes or {}) if guard.baseline else {})
    _LOGGER.info(
        "One-time legacy identity bootstrap sweep complete: %d page(s), "
        "%d reservation(s), %d confirmation code(s) recorded as "
        "cross-path evidence. This is the only legacy reservation "
        "search this entry will ever perform",
        fetched.pages,
        len(fetched.reservations),
        recorded,
    )
    return BootstrapResult(
        reason=BOOTSTRAP_SWEPT,
        pages=fetched.pages,
        codes=recorded,
    )

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
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from homeassistant.core import CoreState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError

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
    from collections.abc import Callable, Collection, Iterable, Mapping

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.guesty.api.client import GuestyApiClient
    from custom_components.guesty.api.models import GuestyReservation, RejectedCode

_LOGGER = logging.getLogger(__name__)

# Schema version of the persisted identity baseline.
STORAGE_VERSION: Final[int] = 1

# Minor version 2 marks a payload revalidated by feature 013 (R9).
IDENTITY_STORAGE_MINOR_VERSION: Final[int] = 2

# Schema version of the persisted conflict-only evidence (R7).
CONFLICT_EVIDENCE_STORAGE_VERSION: Final[int] = 1

# Hard bound on persisted conflict-only pairs; never evicted (R7).
MAX_CONFLICT_ONLY_EVIDENCE: Final[int] = 5000

# Near-capacity hysteresis: set at 80% and cleared below 70%.
_NEAR_CAPACITY_SET: Final[int] = MAX_CONFLICT_ONLY_EVIDENCE * 4 // 5
_NEAR_CAPACITY_CLEAR: Final[int] = MAX_CONFLICT_ONLY_EVIDENCE * 7 // 10

# Conflict-only evidence states (data-model.md, ConflictOnlyEvidence).
EVIDENCE_UNLOADED: Final[str] = "unloaded"
EVIDENCE_REVALIDATING: Final[str] = "revalidating"
EVIDENCE_READY: Final[str] = "ready"
EVIDENCE_UNSTORED: Final[str] = "unstored"
EVIDENCE_SATURATED: Final[str] = "saturated"
EVIDENCE_COMPROMISED: Final[str] = "compromised"

# Upper bound on each persisted code map (data-model.md section 4).
MAX_BASELINE_CODES: Final[int] = 5000

# Stable ordering for legacy hints whose enclosing baseline has no
# valid observation timestamp.
OLDEST_OBSERVATION: Final[str] = "0001-01-01T00:00:00+00:00"

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
SOURCE_CONFLICT_ONLY: Final[str] = "conflict_only"

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
REFUSAL_EVIDENCE_UNAVAILABLE: Final[str] = (
    "Guesty reservation identity evidence is unavailable, so this "
    "write is refused rather than risk acting on the wrong "
    "reservation."
)
REFUSAL_EVIDENCE_REPAIR: Final[str] = (
    "Remove and re-add the Guesty integration entry to rebuild it."
)
REFUSAL_REVALIDATING: Final[str] = (
    "Guesty reservation identity evidence is being revalidated, so "
    "this write is refused. Wait for a successful reservation "
    "refresh, then try again."
)
REFUSAL_NO_CODE: Final[str] = (
    "Reservation '{reservation_id}' has no known confirmation code, "
    "so its identity cannot be cross-checked and the write is "
    "refused."
)


@dataclass(frozen=True)
class IdentityWriteRefusal:
    """A write refusal and whether current identity evidence is missing."""

    message: str
    missing_current_code: bool = False


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
        conflict_only: Confirmation code to reservation identifier of
            the refresh's rejected or partial records. It feeds
            conflict detection only (FR-008b).
    """

    mapping: dict[str, str] = field(default_factory=dict)
    collisions: tuple[tuple[str, str], ...] = ()
    conflict_only: dict[str, str] = field(default_factory=dict)


def observe_codes(
    reservations: Iterable[GuestyReservation],
    conflict_only: Iterable[RejectedCode] = (),
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

    Rejected pairs join the same collision scan after the accepted
    records, so a code shared by a rejected and an accepted record,
    or by two rejected records, is ambiguous too. They are returned
    apart and never enter ``mapping``.

    Args:
        reservations: Reservations the refresh retrieved.
        conflict_only: The refresh's rejected ``(reservation_id,
            code)`` pairs.

    Returns:
        The mapping, the conflict-only mapping and any
        within-refresh collisions.
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
    seen = dict(mapping)
    rejected: dict[str, str] = {}
    for reservation_id, code in conflict_only:
        if not isinstance(code, str) or not code:
            continue
        previous = seen.get(code)
        if previous is not None and previous != reservation_id:
            collisions.append((previous, reservation_id))
            continue
        seen[code] = reservation_id
        rejected[code] = reservation_id
    return ObservedCodes(
        mapping=mapping,
        collisions=tuple(collisions),
        conflict_only=rejected,
    )


@dataclass(frozen=True)
class CandidateHint:
    """Durable fallback candidacy record for one reservation.

    Holds identifiers and dates only — never guest data (FR-049a).

    Attributes:
        timing_status: Whether the complete current candidate interval is known.
        check_in: Verified UTC check-in instant, or None.
        check_out: Verified UTC check-out instant, or None.
        observed_at: UTC time of the qualifying source observation.
    """

    timing_status: str = "unknown"
    check_in: str | None = None
    check_out: str | None = None
    observed_at: str = OLDEST_OBSERVATION

    def __post_init__(self) -> None:
        """Normalize observation time and reject incomplete intervals."""
        observed_at = _utc_iso(self.observed_at) or OLDEST_OBSERVATION
        object.__setattr__(self, "observed_at", observed_at)
        if self.timing_status != "known":
            object.__setattr__(self, "timing_status", "unknown")
            object.__setattr__(self, "check_in", None)
            object.__setattr__(self, "check_out", None)
            return
        start = _aware_instant(self.check_in)
        end = _aware_instant(self.check_out)
        if start is None or end is None or start >= end:
            object.__setattr__(self, "timing_status", "unknown")
            object.__setattr__(self, "check_in", None)
            object.__setattr__(self, "check_out", None)
            return
        object.__setattr__(self, "check_in", start.isoformat())
        object.__setattr__(self, "check_out", end.isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Serialize the hint for storage.

        Returns:
            A JSON-serializable mapping of the hint.
        """
        return {
            "timing_status": self.timing_status,
            "check_in": self.check_in,
            "check_out": self.check_out,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        baseline_updated: str = "",
    ) -> CandidateHint:
        """Deserialize a hint from storage.

        Args:
            data: The stored mapping.
            baseline_updated: Enclosing baseline update time for legacy entries.

        Returns:
            The reconstructed hint.
        """
        observed_at = _utc_iso(data.get("observed_at"))
        if observed_at is None:
            observed_at = _utc_iso(baseline_updated) or OLDEST_OBSERVATION
        if data.get("timing_status") != "known":
            return cls(observed_at=observed_at)
        check_in = data.get("check_in")
        check_out = data.get("check_out")
        return cls(
            timing_status="known",
            check_in=check_in if isinstance(check_in, str) else None,
            check_out=check_out if isinstance(check_out, str) else None,
            observed_at=observed_at,
        )


def _aware_instant(value: str | None) -> datetime | None:
    """Parse an ISO datetime only when it carries an explicit timezone.

    Args:
        value: Candidate timestamp.

    Returns:
        The UTC-normalized instant, or None when invalid, naive or
        outside the representable UTC range.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return parsed.astimezone(UTC)
    except OverflowError:
        return None


def _utc_iso(value: Any) -> str | None:
    """Normalize a valid timezone-aware ISO value to UTC text.

    Args:
        value: Candidate ISO datetime value.

    Returns:
        Normalized UTC timestamp, or None when invalid.
    """
    instant = _aware_instant(value if isinstance(value, str) else None)
    return instant.isoformat() if instant is not None else None


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
        revalidation_pending: Whether a migrated payload still awaits
            its first fully stored accepted refresh (R9).
        conflict_evidence_generation: The conflict-only evidence
            generation this baseline was saved with (R7 decision 8).
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
    revalidation_pending: bool = False
    conflict_evidence_generation: int = 0

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
        conflict_only: Mapping[str, str] | None = None,
        extra_maps: Iterable[_StoredMap] = (),
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
            conflict_only: Code to identifier of this refresh's
                rejected records. They take part in conflict checks
                only: agreement is never a match (FR-008b).
            extra_maps: Stored conflict-only pairs, checked pair by
                pair against both observations.

        Returns:
            The comparison outcome.
        """
        matched_ids: set[str] = set()
        cross_path_ids: set[str] = set()
        conflicts: list[IdentityConflict] = []
        cross_path_conflicts: list[IdentityConflict] = []
        compared = 0
        from_fallback = frozenset(fallback_ids)
        rejected = conflict_only or {}

        def _disagree(known_id: str, observed_id: str, source: str, soft: bool) -> None:
            """Record one disagreement by provenance."""
            conflict = IdentityConflict(
                known_id=known_id,
                observed_id=observed_id,
                source=source,
            )
            if soft and observed_id not in from_fallback:
                cross_path_conflicts.append(conflict)
            else:
                conflicts.append(conflict)

        for source, mapping, confirms, soft in self._maps(path):
            for code, known_id in mapping.items():
                observed_id = observed.get(code)
                if observed_id is None:
                    rejected_id = rejected.get(code)
                    if rejected_id is not None and rejected_id != known_id:
                        _disagree(known_id, rejected_id, source, soft)
                    continue
                compared += 1
                if observed_id == known_id:
                    matched_ids.add(known_id)
                    if confirms and observed_id not in from_fallback:
                        cross_path_ids.add(known_id)
                    continue
                _disagree(known_id, observed_id, source, soft)

        for stored in extra_maps:
            for code, known_id in stored.pairs:
                seen_ids = (observed.get(code), rejected.get(code))
                for observed_id in dict.fromkeys(seen_ids):
                    if observed_id is not None and observed_id != known_id:
                        _disagree(known_id, observed_id, stored.source, stored.soft)

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
            "revalidation_pending": self.revalidation_pending,
            "conflict_evidence_generation": self.conflict_evidence_generation,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IdentityBaseline:
        """Deserialize a baseline from storage.

        Every code map keeps only non-empty string pairs, and a hint
        that is not a mapping or carries an unreadable observation
        time is dropped (R9). A payload still pending revalidation
        trusts no stored stay interval, so every known hint is
        downgraded to unknown timing.

        Args:
            data: The stored mapping.

        Returns:
            The reconstructed baseline.

        Raises:
            ValueError: When the evidence generation is unreadable or
                the path or continuity is not a known value, so the
                caller loads no baseline at all.
        """
        generation = data.get("conflict_evidence_generation", 0)
        if not _non_negative_int(generation):
            raise ValueError("conflict_evidence_generation")
        path = data.get("path", RESERVATIONS_API_LEGACY)
        if path not in (RESERVATIONS_API_LEGACY, RESERVATIONS_API_V3):
            raise ValueError("path")
        continuity = data.get("continuity", CONTINUITY_UNCONFIRMED)
        if continuity not in (
            CONTINUITY_VERIFIED,
            CONTINUITY_UNCONFIRMED,
            CONTINUITY_NOT_APPLICABLE,
        ):
            raise ValueError("continuity")
        pending = bool(data.get("revalidation_pending", False))
        updated = str(data.get("updated", ""))
        raw_hints = data.get("candidates")
        candidates: dict[str, CandidateHint] = {}
        for reservation_id, raw in (
            raw_hints.items() if isinstance(raw_hints, dict) else ()
        ):
            if not isinstance(reservation_id, str) or not isinstance(raw, dict):
                continue
            if "observed_at" in raw and _utc_iso(raw["observed_at"]) is None:
                continue
            hint = CandidateHint.from_dict(raw, baseline_updated=updated)
            if pending and hint.timing_status == "known":
                hint = CandidateHint(observed_at=hint.observed_at)
            candidates[reservation_id] = hint
        return cls(
            path=path,
            continuity=continuity,
            updated=str(data.get("updated", "")),
            divergent=bool(data.get("divergent", False)),
            bootstrap_attempted=bool(
                data.get("bootstrap_attempted", False),
            ),
            codes=_optional_map(data.get("codes")) or {},
            legacy_codes=_optional_map(data.get("legacy_codes")),
            bootstrap_codes=_optional_map(data.get("bootstrap_codes")),
            fallback_codes=_optional_map(data.get("fallback_codes")),
            candidates=candidates,
            revalidation_pending=pending,
            conflict_evidence_generation=generation,
        )


def _optional_map(value: Any) -> dict[str, str] | None:
    """Normalize an optional stored code map.

    Args:
        value: The stored value, which may be absent or null.

    Returns:
        The mapping keeping only non-empty string pairs, an empty
        mapping when the stored value is not a mapping, or None when
        it was never written.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        return {}
    return {
        code: reservation_id
        for code, reservation_id in value.items()
        if isinstance(code, str)
        and code
        and isinstance(reservation_id, str)
        and reservation_id
    }


def conflict_evidence_storage_key(entry_id: str) -> str:
    """Build the ``Store`` key for one entry's conflict-only evidence.

    Args:
        entry_id: The config entry identifier.

    Returns:
        The documented storage key for that entry.
    """
    return f"guesty.{entry_id}.reservations_conflict_evidence"


class _DeferredWrite(OSError):
    """A save Home Assistant deferred to its final write while stopping.

    The data is not yet stored, so callers fail closed exactly as for
    a failed write, but Home Assistant's final write persists it, so
    it is not reported as a storage failure.
    """


class _ReportingStore(Store[dict[str, Any]]):
    """A ``Store`` whose failed writes reach the caller.

    ``Store.async_save`` never raises on a failed write: Home
    Assistant's ``_async_handle_write_data`` catches the
    ``WriteError`` that ``write_utf8_file`` and
    ``write_utf8_file_atomic`` raise for any ``OSError``, and the
    ``SerializationError`` of a payload that cannot be encoded, and
    only logs them. A save that silently failed would count as
    stored, so the evidence would look current and writes would be
    allowed against it (R7 decision 8).

    ``_async_write_data`` is the coroutine Home Assistant awaits
    inside that handler for every write, immediate, delayed or
    final; its name and signature are identical in Home Assistant
    2026.4.0 and 2026.10.x. Overriding it records each write's
    outcome without changing what Home Assistant logs, and
    ``async_save_checked`` raises ``OSError`` when the write that
    carried its data failed, and the ``_DeferredWrite`` subclass when
    the save was deferred to Home Assistant's final write. Plain
    ``async_save`` keeps Home Assistant's semantics, so the migration
    save inside ``async_load`` behaves as before.
    """

    _writes: int = 0
    _write_failed: bool = False

    async def _async_write_data(self, data: dict[str, Any]) -> None:
        """Write the data, recording whether the write failed.

        Args:
            data: The prepared storage envelope.

        Raises:
            SerializationError: When the payload cannot be encoded.
            WriteError: When the file layer failed.
        """
        self._writes += 1
        try:
            await super()._async_write_data(data)
        except SerializationError, WriteError:
            self._write_failed = True
            raise
        self._write_failed = False

    async def async_save_checked(self, data: dict[str, Any]) -> None:
        """Save data, raising when the write did not reach storage.

        Writes are serialized by the store's lock, and a write always
        carries the newest data saved, so the last write completed
        since this call started is the one that carried this data.
        While Home Assistant is stopping, ``Store.async_save`` only
        schedules the data for the final write and returns without
        writing, so that save is reported as failed: the data is not
        yet stored and must not be adopted as if it were.

        Args:
            data: The payload to save.

        Raises:
            OSError: When the write failed.
            _DeferredWrite: When the write was deferred to Home
                Assistant's final write.
        """
        writes = self._writes
        deferred = self.hass.state is CoreState.stopping
        await self.async_save(data)
        if deferred:
            raise _DeferredWrite("Storage write deferred")
        if self._writes != writes and self._write_failed:
            raise OSError("Storage write failed")


class _IdentityStore(_ReportingStore):
    """The identity ``Store``, migrating minor version 1 to 2 (R9)."""

    async def _async_migrate_func(  # type: ignore[override]
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: Any,
    ) -> Any:
        """Mark a pre-013 payload for revalidation.

        The payload keeps every field, so a rollback to a release
        that only knows minor version 1 still loads it. The flag
        makes the load downgrade every known stay interval and keeps
        writes refused until an accepted refresh revalidates it.

        Args:
            old_major_version: The stored major version.
            old_minor_version: The stored minor version.
            old_data: The stored payload.

        Returns:
            The migrated payload, or an unreadable non-mapping payload
            unchanged so the load fails closed with no baseline.

        Raises:
            NotImplementedError: For any other version, so Home
                Assistant keeps a newer minor payload as is.
        """
        if old_major_version == STORAGE_VERSION and old_minor_version == 1:
            if not isinstance(old_data, dict):
                # Left for ``async_load`` to treat as no baseline.
                return old_data
            return {**old_data, "revalidation_pending": True}
        raise NotImplementedError


def _default_past_days() -> int:
    """Supply the default past-day window.

    Returns:
        ``DEFAULT_PAST_DAYS``.
    """
    return DEFAULT_PAST_DAYS


def _never() -> bool:
    """Report that no identity revalidation is pending.

    Returns:
        False.
    """
    return False


def _canonical_date(value: Any) -> bool:
    """Report whether a value is a canonical ``YYYY-MM-DD`` date.

    Args:
        value: The candidate value.

    Returns:
        True for a string that round-trips through ``date``.
    """
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _non_negative_int(value: Any) -> bool:
    """Report whether a value is a non-negative, non-boolean integer.

    Args:
        value: The candidate value.

    Returns:
        True when the value is a usable counter.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class _StoredMap:
    """One group of stored pairs taking part in the comparison.

    Attributes:
        source: The map name reported on a conflict.
        pairs: ``(code, reservation_id)`` pairs, several IDs per code
            allowed.
        confirms: Whether a match may confirm continuity; never for
            conflict-only evidence.
        soft: Whether a conflict only demotes continuity.
    """

    source: str
    pairs: tuple[tuple[str, str], ...]
    confirms: bool = False
    soft: bool = False


@dataclass
class _EvidenceEntry:
    """The stored facts about one conflict-only pair.

    ``path`` keeps the data-model meaning, the first path that
    observed the pair. ``paths`` extends it: every path that ever
    observed the pair, ``path`` included. A pair re-observed on a
    second path is then same-path evidence for both, so a later
    conflict on either path aborts instead of only demoting (R7
    decision 4, "soft" means "recorded on another path"). A payload
    without ``paths`` loads as ``[path]``.

    Attributes:
        path: The reservation path that first observed the pair.
        fallback: Whether the pair was ever seen through the legacy
            not-found fallback.
        observed_at: Offset-aware ISO time of the first observation.
        check_out_bound: Retirement-only date bound, or None.
        paths: Every reservation path that observed the pair.
    """

    path: str
    fallback: bool
    observed_at: str
    check_out_bound: str | None
    paths: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        """Include the first path in the observing paths."""
        self.paths.add(self.path)


def _later_bound(stored: str | None, current: str | None) -> str | None:
    """Merge a re-observed pair's retirement bound.

    The bound only ever moves later, and an observation without one
    makes it unbounded for good (research R7 decision 4).

    Args:
        stored: The stored bound.
        current: This observation's bound.

    Returns:
        The merged bound.
    """
    if stored is None or current is None:
        return None
    return max(stored, current)


def _parse_evidence_entry(raw: Any) -> tuple[tuple[str, str], _EvidenceEntry]:
    """Validate one persisted evidence entry.

    Args:
        raw: The stored entry.

    Returns:
        The ``(code, reservation_id)`` key and the entry.

    Raises:
        ValueError: When the entry is malformed in any way.
    """
    if not isinstance(raw, dict):
        raise ValueError("entry")
    code = raw.get("code")
    reservation_id = raw.get("reservation_id")
    path = raw.get("path")
    fallback = raw.get("fallback")
    observed_at = raw.get("observed_at")
    bound = raw.get("check_out_bound")
    if not isinstance(code, str) or not code:
        raise ValueError("code")
    if not isinstance(reservation_id, str) or not reservation_id:
        raise ValueError("reservation_id")
    known_paths = (RESERVATIONS_API_V3, RESERVATIONS_API_LEGACY)
    if path not in known_paths:
        raise ValueError("path")
    paths = raw.get("paths", [path])
    if (
        not isinstance(paths, list)
        or path not in paths
        or len(set(paths)) != len(paths)
        or any(item not in known_paths for item in paths)
    ):
        raise ValueError("paths")
    if not isinstance(fallback, bool):
        raise ValueError("fallback")
    if not isinstance(observed_at, str) or _aware_instant(observed_at) is None:
        raise ValueError("observed_at")
    if bound is not None and not _canonical_date(bound):
        raise ValueError("check_out_bound")
    return (code, reservation_id), _EvidenceEntry(
        path=path,
        fallback=fallback,
        observed_at=observed_at,
        check_out_bound=bound,
        paths=set(paths),
    )


class ConflictOnlyEvidence:
    """Persisted conflict-only identity evidence (FR-008a to FR-008c).

    Holds the ``(code, reservation_id)`` pairs of rejected or partial
    records. They take part in conflict detection only: they never
    confirm continuity, never match, never enter the current IDs and
    never authorize a write. Codes are never logged.

    Feature 014 reads it as ``coordinator.identity.conflict_evidence``
    through ``available``, ``codes_for`` and ``ids_for``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        past_days: Callable[[], int] = _default_past_days,
        revalidation_pending: Callable[[], bool] = _never,
    ) -> None:
        """Initialize the unloaded evidence of one config entry.

        Args:
            hass: Home Assistant instance.
            entry_id: The config entry identifier.
            past_days: Supplier of the configured past-day window.
            revalidation_pending: Supplier of the identity store's
                pending revalidation flag.
        """
        self._store = _ReportingStore(
            hass,
            CONFLICT_EVIDENCE_STORAGE_VERSION,
            conflict_evidence_storage_key(entry_id),
        )
        self._past_days = past_days
        self._revalidation_pending = revalidation_pending
        self._listeners: list[Callable[[], None]] = []
        self._notified: tuple[str, int, bool] = (EVIDENCE_UNLOADED, 0, False)
        self._reset()
        self._loaded = False

    def _reset(self) -> None:
        """Forget everything held in memory."""
        self._entries: dict[tuple[str, str], _EvidenceEntry] = {}
        self._compromised = False
        self._saturated = False
        self._unstored = False
        self._revalidated = False
        self._near_capacity = False
        self._generation = 0
        self._covered: int | None = None
        self._covered_pending: int | None = None

    @property
    def state(self) -> str:
        """Return the evidence state, highest precedence first.

        Returns:
            One of the ``EVIDENCE_*`` states.
        """
        if not self._loaded:
            return EVIDENCE_UNLOADED
        if self._compromised:
            return EVIDENCE_COMPROMISED
        if self._saturated:
            return EVIDENCE_SATURATED
        if self._unstored:
            return EVIDENCE_UNSTORED
        if (
            self._revalidation_pending()
            or not self._revalidated
            or self._covered is None
            or self._past_days() > self._covered
        ):
            return EVIDENCE_REVALIDATING
        return EVIDENCE_READY

    @property
    def frozen(self) -> bool:
        """Report whether a refresh may conclude nothing new.

        Returns:
            True while compromised or saturated (R7 decision 7).
        """
        return self._compromised or self._saturated

    @property
    def count(self) -> int:
        """Return the number of pairs held.

        Returns:
            The pair count.
        """
        return len(self._entries)

    @property
    def dated(self) -> int:
        """Return the number of pairs with a retirement bound.

        Returns:
            The dated pair count.
        """
        return sum(1 for e in self._entries.values() if e.check_out_bound)

    @property
    def undated(self) -> int:
        """Return the number of pairs without a retirement bound.

        Returns:
            The undated pair count.
        """
        return self.count - self.dated

    @property
    def near_capacity(self) -> bool:
        """Report whether the store is close to its bound.

        Returns:
            True from 80% until it falls below 70%.
        """
        return self._near_capacity

    @property
    def generation(self) -> int:
        """Return the save generation held in memory.

        Returns:
            The generation.
        """
        return self._generation

    def available(self) -> bool:
        """Report whether the evidence may answer lookups.

        Returns:
            True only in the ``ready`` state.
        """
        return self.state == EVIDENCE_READY

    def codes_for(self, reservation_id: str) -> frozenset[str]:
        """Return every stored code a reservation was rejected with.

        Args:
            reservation_id: The reservation identifier.

        Returns:
            The codes.

        Raises:
            RuntimeError: When the evidence is not available.
        """
        self._require_available()
        return frozenset(
            code for code, stored_id in self._entries if stored_id == reservation_id
        )

    def ids_for(self, code: str) -> frozenset[str]:
        """Return every stored reservation a code was rejected on.

        Args:
            code: The confirmation code.

        Returns:
            The reservation identifiers.

        Raises:
            RuntimeError: When the evidence is not available.
        """
        self._require_available()
        return frozenset(
            stored_id for stored_code, stored_id in self._entries if stored_code == code
        )

    def _require_available(self) -> None:
        """Refuse a lookup unless the evidence is ready.

        Raises:
            RuntimeError: When the evidence is not available.
        """
        if not self.available():
            raise RuntimeError("Conflict-only evidence is not available")

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call a listener whenever the state, count or capacity flag changes.

        Args:
            listener: The callback.

        Returns:
            A callable that removes the listener.
        """
        self._listeners.append(listener)

        def _remove() -> None:
            """Remove the listener."""
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    def _notify(self) -> None:
        """Tell listeners about a state, count or capacity change."""
        notified = (self.state, self.count, self._near_capacity)
        if notified == self._notified:
            return
        self._notified = notified
        for listener in tuple(self._listeners):
            listener()

    def compare_maps(self, path: str) -> tuple[_StoredMap, ...]:
        """Return every held pair, grouped for the comparison.

        Pairs persisted and pairs still pending a save are both
        compared (R7 decision 8). A pair no observation on this path
        produced is soft unless the fallback ever produced it.

        Args:
            path: Reservation path of the compared refresh.

        Returns:
            One map per soft and hard group.
        """
        groups: dict[bool, list[tuple[str, str]]] = {}
        for pair, entry in self._entries.items():
            soft = path not in entry.paths and not entry.fallback
            groups.setdefault(soft, []).append(pair)
        return tuple(
            _StoredMap(
                source=SOURCE_CONFLICT_ONLY,
                pairs=tuple(pairs),
                confirms=False,
                soft=soft,
            )
            for soft, pairs in groups.items()
        )

    def record(
        self,
        pairs: Iterable[RejectedCode],
        bounds: Mapping[str, str],
        path: str,
        now: datetime,
        fallback_ids: Collection[str] = (),
    ) -> bool:
        """Hold this refresh's rejected pairs in memory.

        A new pair beyond ``MAX_CONFLICT_ONLY_EVIDENCE`` is not kept
        and makes the store saturated for good; nothing is evicted.

        Args:
            pairs: The rejected ``(reservation_id, code)`` pairs.
            bounds: Retirement bound per reservation identifier.
            path: Reservation path that observed them.
            now: The observation time.
            fallback_ids: Identifiers observed through the fallback.

        Returns:
            False when a new pair was dropped on saturation.
        """
        from_fallback = frozenset(fallback_ids)
        observed_at = now.astimezone(UTC).isoformat()
        kept = True
        for reservation_id, code in pairs:
            if not isinstance(code, str) or not code:
                continue
            bound = bounds.get(reservation_id)
            if not _canonical_date(bound):
                bound = None
            fallback = reservation_id in from_fallback
            entry = self._entries.get((code, reservation_id))
            if entry is not None:
                entry.paths.add(path)
                entry.fallback = entry.fallback or fallback
                entry.check_out_bound = _later_bound(entry.check_out_bound, bound)
                continue
            if len(self._entries) >= MAX_CONFLICT_ONLY_EVIDENCE:
                kept = False
                if not self._saturated:
                    self._saturated = True
                    _LOGGER.warning(
                        "Conflict-only reservation identity evidence reached "
                        "its limit of %d pairs; reservation writes are "
                        "refused until the entry is removed and re-added",
                        MAX_CONFLICT_ONLY_EVIDENCE,
                    )
                continue
            self._entries[(code, reservation_id)] = _EvidenceEntry(
                path=path,
                fallback=fallback,
                observed_at=observed_at,
                check_out_bound=bound,
            )
        self._update_capacity()
        self._notify()
        return kept

    def retire(
        self,
        *,
        superseded: Collection[str],
        absent: Collection[str],
        returned: Collection[str],
        past_days: int,
        now: datetime,
    ) -> bool:
        """Retire pairs no longer needed (research R7 decision 5).

        Every pair of an identifier now fully verified or confirmed
        absent goes. A dated pair the source did not return goes once
        its bound falls a day before the oldest local date the window
        could still cover.

        Args:
            superseded: Identifiers this refresh fully verified.
            absent: Identifiers confirmed to no longer exist.
            returned: Every identifier this refresh returned.
            past_days: The configured past-day window.
            now: The refresh time.

        Returns:
            True when anything was retired.
        """
        gone = frozenset(superseded) | frozenset(absent)
        seen = frozenset(returned)
        horizon = (
            (now.astimezone(UTC) - timedelta(days=past_days)).date() - timedelta(days=1)
        ).isoformat()
        retired = [
            pair
            for pair, entry in self._entries.items()
            if pair[1] in gone
            or (
                entry.check_out_bound is not None
                and pair[1] not in seen
                and entry.check_out_bound < horizon
            )
        ]
        for pair in retired:
            del self._entries[pair]
        self._update_capacity()
        self._notify()
        return bool(retired)

    def _update_capacity(self) -> None:
        """Apply the near-capacity hysteresis, warning once on entry."""
        count = len(self._entries)
        if count >= _NEAR_CAPACITY_SET:
            if not self._near_capacity:
                self._near_capacity = True
                _LOGGER.warning(
                    "Conflict-only reservation identity evidence holds %d "
                    "of at most %d pairs",
                    count,
                    MAX_CONFLICT_ONLY_EVIDENCE,
                )
        elif count < _NEAR_CAPACITY_CLEAR:
            self._near_capacity = False

    def resume_near_capacity(self) -> None:
        """Resume the hysteresis an earlier run had already entered.

        The flag lives in memory, but the repair issue it raised is
        persisted by Home Assistant. When that issue survived a
        restart, the store stays near capacity until it falls below
        70%, so a restart inside the 70% to 80% band neither clears
        it nor warns again (R7 decision 6).
        """
        if (
            self._loaded
            and not self._near_capacity
            and len(self._entries) >= _NEAR_CAPACITY_CLEAR
        ):
            self._near_capacity = True
            self._notify()

    def advance(self, past_days: int | None) -> None:
        """Start a new save generation for an accepted refresh.

        Until ``adopt`` the evidence revalidates, so a write during
        the saves never sees ``ready`` (R7 decision 8).

        Args:
            past_days: The window this refresh covers, or None to
                keep the covered window unchanged.
        """
        self._generation += 1
        self._covered_pending = self._covered if past_days is None else past_days
        self._revalidated = False
        self._notify()

    def adopt(self) -> None:
        """Adopt the saved generation once the baseline is stored."""
        self._revalidated = True
        self._covered = self._covered_pending
        self._notify()

    def _payload(self) -> dict[str, Any]:
        """Serialize the held evidence.

        Returns:
            The persisted payload.
        """
        return {
            "entries": [
                {
                    "code": code,
                    "reservation_id": reservation_id,
                    "path": entry.path,
                    "paths": sorted(entry.paths),
                    "fallback": entry.fallback,
                    "observed_at": entry.observed_at,
                    "check_out_bound": entry.check_out_bound,
                }
                for (code, reservation_id), entry in self._entries.items()
            ],
            "saturated": self._saturated,
            "generation": self._generation,
            "covered_past_days": self._covered_pending,
        }

    async def async_save(self) -> bool:
        """Persist every held pair.

        A compromised store is never written, so a restart loads it
        compromised again.

        Returns:
            True when the evidence reached storage.
        """
        if self._compromised:
            return False
        try:
            await self._store.async_save_checked(self._payload())
        except OSError as exc:
            if isinstance(exc, _DeferredWrite):
                _LOGGER.debug(
                    "Conflict-only reservation identity evidence save "
                    "deferred to Home Assistant's final write"
                )
            elif not self._unstored:
                _LOGGER.warning(
                    "Could not persist conflict-only reservation identity "
                    "evidence (%s); reservation writes stay refused until "
                    "it is stored",
                    type(exc).__name__,
                )
            self._unstored = True
            self._notify()
            return False
        self._unstored = False
        self._notify()
        return True

    async def async_load(self, expected_generation: int | None = 0) -> None:
        """Read and validate the stored evidence.

        A malformed store is compromised as a whole and is never
        repaired by dropping entries. A well-formed store behind the
        baseline's generation is compromised too, but its pairs are
        still compared and its occupancy is reported.

        Args:
            expected_generation: The baseline's recorded generation;
                a missing store counts as generation 0. None means
                the identity payload exists but could not be read,
                so no store can be shown current and it loads
                compromised.
        """
        self._reset()
        try:
            raw = await self._store.async_load()
        except Exception as exc:
            # Any read failure fails closed.
            self._fail_load(type(exc).__name__)
            return
        if raw is not None:
            try:
                self._parse(raw)
            except ValueError:
                self._reset()
                self._fail_load("malformed")
                return
        if expected_generation is None or self._generation < expected_generation:
            self._update_capacity()
            self._fail_load(
                "stale" if expected_generation is not None else "baseline unreadable"
            )
            return
        self._loaded = True
        self._update_capacity()
        self._notify()

    def _fail_load(self, reason: str) -> None:
        """Mark the loaded evidence compromised.

        Args:
            reason: A code-free description for the log.
        """
        self._compromised = True
        self._loaded = True
        _LOGGER.warning(
            "Conflict-only reservation identity evidence is unusable "
            "(%s); reservation writes are refused until the entry is "
            "removed and re-added",
            reason,
        )
        self._notify()

    def _parse(self, raw: Any) -> None:
        """Adopt a stored payload after validating all of it.

        Args:
            raw: The stored payload.

        Raises:
            ValueError: When anything is malformed.
        """
        if not isinstance(raw, dict):
            raise ValueError("payload")
        entries = raw.get("entries")
        saturated = raw.get("saturated", False)
        generation = raw.get("generation", 0)
        covered = raw.get("covered_past_days")
        if not isinstance(entries, list) or len(entries) > MAX_CONFLICT_ONLY_EVIDENCE:
            raise ValueError("entries")
        if not isinstance(saturated, bool) or not _non_negative_int(generation):
            raise ValueError("header")
        if covered is not None and not _non_negative_int(covered):
            raise ValueError("covered_past_days")
        parsed: dict[tuple[str, str], _EvidenceEntry] = {}
        for item in entries:
            pair, entry = _parse_evidence_entry(item)
            if pair in parsed:
                raise ValueError("duplicate")
            parsed[pair] = entry
        self._entries = parsed
        self._saturated = saturated
        self._generation = generation
        self._covered = covered
        self._covered_pending = covered

    async def async_remove(self) -> None:
        """Delete the stored evidence with its config entry."""
        self._reset()
        self._loaded = False
        await self._store.async_remove()
        self._notify()


class ReservationIdentityGuard:
    """Owns one config entry's identity baseline and verdicts.

    Attributes:
        baseline: The loaded baseline, or None before first use.
        last_refresh_ambiguous: Whether the last refresh aborted.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        past_days: Callable[[], int] = _default_past_days,
    ) -> None:
        """Initialize the guard for one config entry.

        Args:
            hass: Home Assistant instance.
            entry_id: The config entry identifier.
            past_days: Supplier of the configured past-day window,
                read live so an options change is honored at once.
        """
        self._store = _IdentityStore(
            hass,
            STORAGE_VERSION,
            storage_key(entry_id),
            minor_version=IDENTITY_STORAGE_MINOR_VERSION,
        )
        self._baseline: IdentityBaseline | None = None
        self._loaded = False
        self._ambiguous = False
        self._unstored = False
        self._evidence = ConflictOnlyEvidence(
            hass,
            entry_id,
            past_days=past_days,
            revalidation_pending=self._revalidation_pending,
        )

    def _revalidation_pending(self) -> bool:
        """Report whether the baseline still awaits revalidation.

        Returns:
            True while a migrated payload is not yet revalidated.
        """
        return bool(self._baseline and self._baseline.revalidation_pending)

    @property
    def conflict_evidence(self) -> ConflictOnlyEvidence:
        """Return the conflict-only evidence feature 014 consults.

        Returns:
            The evidence store of this entry.
        """
        return self._evidence

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
        which fails writes closed rather than opening them. The
        conflict-only evidence loads after it, checked against the
        generation the baseline was saved with; when a payload is
        present but unreadable that generation is unknown, so the
        evidence loads compromised.
        """
        raw = await self._store.async_load()
        self._baseline = None
        if raw:
            try:
                self._baseline = IdentityBaseline.from_dict(raw)
            except AttributeError, TypeError, ValueError:
                self._baseline = None
        expected: int | None = 0
        if self._baseline is not None:
            expected = self._baseline.conflict_evidence_generation
        elif raw is not None:
            # The baseline's generation is unknown, so no evidence
            # store can be shown to hold what it expects.
            expected = None
        await self._evidence.async_load(expected)
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
        await self._store.async_save_checked(baseline.to_dict())
        self._baseline = baseline

    async def async_remove(self) -> None:
        """Delete the persisted baseline and evidence with the entry."""
        self._baseline = None
        self._loaded = False
        await self._store.async_remove()
        await self._evidence.async_remove()

    def compare(
        self,
        reservations: Iterable[GuestyReservation],
        path: str,
        fallback_ids: Collection[str] = (),
        conflict_only: Iterable[RejectedCode] = (),
    ) -> IdentityComparison:
        """Compare a refresh against the baseline, free of requests.

        A within-refresh collision is reported even when there is no
        baseline yet, so a confirmation code naming two reservations
        at once cannot establish the very first baseline either.
        Rejected pairs and the held conflict-only evidence take part
        in conflict detection only (FR-008b).

        Args:
            reservations: Reservations the refresh retrieved.
            path: Reservation path that produced the refresh.
            fallback_ids: Identifiers this refresh published from
                the legacy not-found fallback, which may not confirm
                continuity (FR-055).
            conflict_only: This refresh's rejected ``(reservation_id,
                code)`` pairs.

        Returns:
            The comparison outcome.
        """
        observed = observe_codes(reservations, conflict_only)
        collisions = tuple(
            IdentityConflict(
                known_id=first,
                observed_id=second,
                source=SOURCE_OBSERVED,
            )
            for first, second in observed.collisions
        )
        comparison = (self._baseline or IdentityBaseline(path=path)).compare(
            observed.mapping,
            path,
            fallback_ids,
            conflict_only=observed.conflict_only,
            extra_maps=self._evidence.compare_maps(path),
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
        observed_ids: Collection[str] | None = None,
        observed_reservations: Iterable[GuestyReservation] = (),
        past_days: int = DEFAULT_PAST_DAYS,
        fallback_ids: Collection[str] = (),
        absent_ids: Collection[str] = (),
        rejected_ids: Collection[str] = (),
        fallback_invalid_ids: Collection[str] = (),
        rejected_codes: Iterable[RejectedCode] = (),
        rejected_check_out_bounds: Mapping[str, str] | None = None,
        on_baseline_stored: Callable[[], None] | None = None,
    ) -> bool:
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

        Saving follows the crash-consistent order of research R7
        decision 8: (1) the conflict-only evidence with every new
        pair and a new generation, (2) the identity baseline naming
        that generation, then ``on_baseline_stored``, and only after
        both (3) retirement of pairs no longer needed. The evidence
        revalidates for the whole window, so no write is judged
        against a half-saved state. While the evidence is
        compromised or saturated the refresh concludes nothing new:
        no continuity is confirmed, no bootstrap evidence retires
        and a pending revalidation stays pending.

        Args:
            path: Reservation path that produced this refresh.
            reservations: Reservations the refresh retrieved.
            published: Stay hints for every reservation the refresh
                published, keyed by reservation identifier.
            comparison: The comparison this refresh already ran.
            updated: ISO timestamp of this refresh.
            observed_ids: IDs returned by the primary source or a
                successful by-ID retrieval. None preserves the legacy
                caller behavior that every supplied record was observed.
            observed_reservations: Successful by-ID records that were
                not returned for publication, but still provide current
                candidate evidence.
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
            rejected_ids: Identifiers the refresh rejected or kept
                only partially. Their stay hints are dropped, never
                retired (FR-008c).
            fallback_invalid_ids: Fallback-sourced identifiers that
                failed validation; their hints are dropped too.
            rejected_codes: The refresh's rejected ``(reservation_id,
                code)`` pairs, recorded as conflict-only evidence.
            rejected_check_out_bounds: Retirement-only check-out
                bound per rejected identifier.
            on_baseline_stored: Called synchronously once the
                baseline reaches storage, before retirement; the
                coordinator assigns its routing sets there.

        Returns:
            True when the baseline reached storage. A False answer
            means ``absent_ids`` has not been durably retired and
            must be reported again by the next refresh.
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
        hint_records = {reservation.id: reservation for reservation in retrieved}
        hint_records.update(
            (reservation.id, reservation) for reservation in observed_reservations
        )
        hints = _retrieved_hints(
            hint_records.values(),
            observed_at=updated,
            observed_ids=observed_ids,
        )
        hints.update(published)
        merged_hints = dict(previous.candidates) if previous else {}
        merged_hints.update(hints)
        invalid = frozenset(rejected_ids) | frozenset(fallback_invalid_ids)
        for reservation_id in absent | invalid:
            merged_hints.pop(reservation_id, None)
        now = datetime.now(UTC)
        evidence = self._evidence
        evidence.record(
            rejected_codes,
            rejected_check_out_bounds or {},
            path,
            now,
            from_fallback | frozenset(fallback_invalid_ids),
        )
        frozen = evidence.frozen
        check_outs = {
            reservation_id: hint.check_out
            for reservation_id, hint in merged_hints.items()
            if hint.timing_status == "known" and hint.check_out is not None
        }
        divergent = bool(comparison.cross_path_conflicts) or bool(
            previous and previous.divergent,
        )
        continuity = _next_continuity(previous, path, comparison, divergent)
        bootstrap_codes = _retired_bootstrap(
            previous,
            observed,
            check_outs,
            past_days,
        )
        if frozen:
            held = previous.continuity if previous else CONTINUITY_UNCONFIRMED
            if continuity == CONTINUITY_VERIFIED and held != CONTINUITY_VERIFIED:
                continuity = held
            bootstrap_codes = previous.bootstrap_codes if previous else None
        evidence.advance(past_days)
        evidence_stored = await evidence.async_save()
        baseline = IdentityBaseline(
            path=path,
            continuity=continuity,
            updated=updated,
            divergent=divergent,
            bootstrap_attempted=bool(previous and previous.bootstrap_attempted),
            codes=_bounded(observed, check_outs),
            legacy_codes=_retained_legacy(previous, path, check_outs),
            bootstrap_codes=bootstrap_codes,
            fallback_codes=_bounded(fallback_observed, check_outs),
            candidates=_bounded_candidates(merged_hints),
            revalidation_pending=bool(previous and previous.revalidation_pending)
            and not (evidence_stored and not frozen),
            conflict_evidence_generation=evidence.generation,
        )
        try:
            await self.async_store(baseline)
        except OSError as exc:
            self._unstored = True
            if isinstance(exc, _DeferredWrite):
                _LOGGER.debug(
                    "Reservation identity baseline save deferred to "
                    "Home Assistant's final write"
                )
            else:
                _LOGGER.warning(
                    "Could not persist the reservation identity "
                    "baseline (%s); writes stay refused until it is "
                    "stored",
                    type(exc).__name__,
                )
            return False
        self._unstored = False
        self._ambiguous = False
        if evidence_stored and not frozen:
            evidence.adopt()
        if on_baseline_stored is not None:
            on_baseline_stored()
        if evidence_stored and evidence.retire(
            superseded=frozenset(r.id for r in retrieved) - from_fallback,
            absent=absent,
            returned=(
                frozenset(r.id for r in retrieved)
                | frozenset(observed_ids or ())
                | invalid
            ),
            past_days=past_days,
            now=now,
        ):
            await evidence.async_save()
        return True

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
        rejected_codes: Iterable[RejectedCode] = (),
        rejected_check_out_bounds: Mapping[str, str] | None = None,
    ) -> None:
        """Persist the outcome of the one-time sweep (FR-031d).

        The attempt is recorded whether or not it produced evidence,
        so that an unavailable legacy resource cannot turn the single
        permitted legacy call into a standing dependency that retries
        on every start.

        Stay dates are kept alongside the codes because T074's safe
        retirement policy needs to know when a swept reservation
        checked out; they carry identifiers and dates only (FR-049a).

        The sweep's rejected pairs are saved first as legacy
        conflict-only evidence, then the baseline names the new
        evidence generation (research R7 decision 8).

        Args:
            reservations: Fully verified reservations the sweep
                retrieved.
            failed: Whether the sweep could not be completed.
            rejected_codes: The sweep's rejected ``(reservation_id,
                code)`` pairs.
            rejected_check_out_bounds: Retirement-only check-out
                bound per rejected identifier.
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
        evidence = self._evidence
        evidence.record(
            rejected_codes,
            rejected_check_out_bounds or {},
            RESERVATIONS_API_LEGACY,
            datetime.now(UTC),
        )
        evidence.advance(None)
        await evidence.async_save()
        merged = dict(current.candidates)
        merged.update(
            _retrieved_hints(
                retrieved,
                observed_at=datetime.now(UTC).isoformat(),
            ),
        )
        await self._store_bootstrap(
            replace(
                current,
                bootstrap_attempted=True,
                bootstrap_codes=_bounded(
                    observe_codes(retrieved).mapping,
                    {
                        res_id: hint.check_out
                        for res_id, hint in merged.items()
                        if hint.timing_status == "known" and hint.check_out is not None
                    },
                ),
                candidates=_bounded_candidates(merged),
                conflict_evidence_generation=evidence.generation,
            ),
        )

    async def _store_bootstrap(self, baseline: IdentityBaseline) -> None:
        """Persist a bootstrap verdict, tolerating a storage failure.

        A failure leaves ``bootstrap_attempted`` unwritten, so the
        sweep is reattempted at the next start. A save deferred while
        Home Assistant stops is persisted by its final write instead,
        so it is logged at debug level only; writes stay refused
        either way. That is the only
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
            if isinstance(exc, _DeferredWrite):
                _LOGGER.debug(
                    "Reservation identity bootstrap save deferred to "
                    "Home Assistant's final write"
                )
            else:
                _LOGGER.warning(
                    "Could not persist the reservation identity "
                    "bootstrap (%s); its evidence is still compared, "
                    "but writes stay refused until a baseline reaches "
                    "storage",
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
        refusal = self.classify_write_refusal(reservation_id)
        return refusal.message if refusal is not None else None

    def classify_write_refusal(
        self, reservation_id: str
    ) -> IdentityWriteRefusal | None:
        """Return the write refusal with a typed missing-code marker.

        The evidence-state refusals come after the four baseline
        conjuncts and before membership, so while the conflict-only
        evidence is not ready no write proceeds and no recovery
        refresh is attempted (C15, C16).

        Args:
            reservation_id: The reservation a write would target.

        Returns:
            The refusal, or None when the write may go ahead.
        """
        baseline = self._baseline
        if baseline is None or not baseline.is_usable:
            return IdentityWriteRefusal(REFUSAL_NO_BASELINE)
        if self._unstored:
            return IdentityWriteRefusal(REFUSAL_UNSTORED)
        if baseline.continuity == CONTINUITY_UNCONFIRMED:
            return IdentityWriteRefusal(REFUSAL_UNCONFIRMED)
        if self._ambiguous:
            return IdentityWriteRefusal(REFUSAL_AMBIGUOUS)
        state = self._evidence.state
        if state != EVIDENCE_READY:
            _LOGGER.debug(
                "Refusing a reservation write: identity evidence is %s",
                state,
            )
            if state == EVIDENCE_REVALIDATING:
                return IdentityWriteRefusal(REFUSAL_REVALIDATING)
            if state in (EVIDENCE_SATURATED, EVIDENCE_COMPROMISED):
                return IdentityWriteRefusal(
                    f"{REFUSAL_EVIDENCE_UNAVAILABLE} {REFUSAL_EVIDENCE_REPAIR}"
                )
            return IdentityWriteRefusal(REFUSAL_EVIDENCE_UNAVAILABLE)
        if reservation_id not in baseline.current_ids():
            return IdentityWriteRefusal(
                REFUSAL_NO_CODE.format(reservation_id=reservation_id),
                missing_current_code=True,
            )
        return None

    def check_identity_conflict(
        self,
        reservation_id: str,
        code: str,
    ) -> Literal["none", "conflict", "unavailable"]:
        """Ask whether a by-ID code conflicts with held identity evidence.

        A pure read over the loaded baseline maps: nothing is
        recorded, marked or stored. A ``None`` legacy, bootstrap or
        fallback map is no evidence and counts as empty, as it does
        in the refresh comparison (research R8).

        Args:
            reservation_id: The reservation a write would target.
            code: The confirmation code its by-ID record carries.

        Returns:
            ``conflict`` when the code names another reservation in
            any map or any map names the target with another code,
            ``unavailable`` when the baseline is not loaded, not
            usable, not stored or left ambiguous, else ``none``.
        """
        baseline = self._baseline
        if (
            not self._loaded
            or baseline is None
            or not baseline.is_usable
            or self._unstored
            or self._ambiguous
        ):
            return "unavailable"
        for mapping in (
            baseline.codes,
            baseline.legacy_codes,
            baseline.bootstrap_codes,
            baseline.fallback_codes,
        ):
            for known_code, known_id in (mapping or {}).items():
                if (known_code == code) != (known_id == reservation_id):
                    return "conflict"
        return "none"


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
    *,
    observed_at: str | None = None,
    observed_ids: Collection[str] | None = None,
) -> dict[str, CandidateHint]:
    """Record stay dates for every reservation retrieved.

    Recency advances only for IDs the caller identifies as returned
    by a qualifying source observation. The candidate interval is
    known for V3 only when every raw segment is present, verified,
    positive, chronological, and non-overlapping.

    Args:
        reservations: Reservations the refresh retrieved.
        observed_at: UTC time shared by this successful source observation.
        observed_ids: Qualifying IDs; None means all records for legacy callers.

    Returns:
        Stay hints keyed by reservation identifier.
    """
    if observed_at is None:
        observed_at = datetime.now(UTC).isoformat()
    observed = None if observed_ids is None else set(observed_ids)
    hints: dict[str, CandidateHint] = {}
    for reservation in reservations:
        if observed is not None and reservation.id not in observed:
            continue
        interval = _verified_candidate_interval(reservation)
        if interval is None:
            hints[reservation.id] = CandidateHint(observed_at=observed_at)
        else:
            hints[reservation.id] = CandidateHint(
                timing_status="known",
                check_in=interval[0].isoformat(),
                check_out=interval[1].isoformat(),
                observed_at=observed_at,
            )
    return hints


def _verified_candidate_interval(
    reservation: GuestyReservation,
) -> tuple[datetime, datetime] | None:
    """Validate a complete current stay interval for fallback use.

    Args:
        reservation: Current source record.

    Returns:
        The first-to-last UTC envelope, or None when not fully verified.
    """
    if not reservation.is_v3:
        check_in = reservation.check_in
        check_out = reservation.check_out
        if (
            check_in is None
            or check_out is None
            or check_in.tzinfo is None
            or check_out.tzinfo is None
            or check_in >= check_out
        ):
            return None
        return check_in.astimezone(UTC), check_out.astimezone(UTC)

    if (
        reservation.raw_stay_count is None
        or reservation.raw_stay_count == 0
        or len(reservation.stay) != reservation.raw_stay_count
    ):
        return None
    if [segment.raw_position for segment in reservation.stay] != list(
        range(reservation.raw_stay_count),
    ):
        return None

    previous_checkout: datetime | None = None
    for segment in reservation.stay:
        check_in = segment.check_in
        check_out = segment.check_out
        if (
            not segment.listing_id
            or check_in is None
            or check_out is None
            or check_in.tzinfo is None
            or check_out.tzinfo is None
            or check_in >= check_out
        ):
            return None
        if previous_checkout is not None and check_in < previous_checkout:
            return None
        previous_checkout = check_out

    first = reservation.stay[0].check_in
    last = reservation.stay[-1].check_out
    if first is None or last is None:
        return None
    return first.astimezone(UTC), last.astimezone(UTC)


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
        key=lambda item: (
            _instant(check_outs[item[1]])
            if item[1] in check_outs
            else datetime.max.replace(tzinfo=UTC)
        ),
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
        key=lambda item: (_instant(item[1].observed_at), item[0]),
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

    rejected = frozenset(fetched.rejected_ids)
    verified = tuple(
        reservation
        for reservation in fetched.reservations
        if not reservation.partial and reservation.id not in rejected
    )
    collisions = observe_codes(verified, fetched.rejected_codes).collisions
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
        reservations=verified,
        failed=False,
        rejected_codes=fetched.rejected_codes,
        rejected_check_out_bounds=fetched.rejected_check_out_bounds,
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

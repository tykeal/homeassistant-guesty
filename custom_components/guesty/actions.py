# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Service handlers for Guesty automation actions.

Bridges the HA-independent GuestyActionsClient to Home Assistant's
service infrastructure. Registers five domain-level services via
hass.services.async_register() and translates API exceptions into
HomeAssistantError for clear automation feedback.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError

from custom_components.guesty.api.actions import GuestyActionsClient
from custom_components.guesty.api.client import resolve_reservations_api
from custom_components.guesty.api.const import (
    ACTIONABLE_STATUSES,
    HOLD_STATUSES,
    LEGACY_RESERVATIONS_RETIREMENT_DATE,
    LEGACY_WRITE_FAILED,
    LEGACY_WRITE_NOTE,
    LEGACY_WRITE_REFUSED,
    LEGACY_WRITE_STATUS,
    LEGACY_WRITE_SUCCEEDED,
    V3_STATUS_MAP,
    VALID_CALENDAR_OPS,
    VALID_LISTING_STATUSES,
    VALID_RESERVATION_STATUSES,
    WRITE_ROUTE_LEGACY,
    WRITE_ROUTE_V3,
)
from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyApiError,
    GuestyByIdReadError,
    GuestyPrerequisiteReadError,
    GuestyRecoveryAuthError,
    GuestyRecoveryConnectionError,
    GuestyRecoveryPermissionError,
    GuestyRecoveryPreSendConnectionError,
    GuestyRecoveryTokenConnectionError,
    GuestyRecoveryTokenError,
    GuestyRecoveryWriteError,
    GuestyRoutingError,
)
from custom_components.guesty.api.legacy_writes import (
    REFUSAL_LEGACY_RETIRED,
)
from custom_components.guesty.api.models import ActionResult, GuestyReservation
from custom_components.guesty.const import (
    CONF_RESERVATIONS_API,
    DEFAULT_RESERVATIONS_API,
    DOMAIN,
    RESERVATIONS_API_V3,
)
from custom_components.guesty.coordinator import (
    RESERVATION_SEARCH_REJECTED_MESSAGE,
    ReservationSearchRejected,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from custom_components.guesty.api.client import GuestyApiClient
    from custom_components.guesty.coordinator import (
        AcceptedRefreshSnapshot,
        ReservationsCoordinator,
    )

_LOGGER = logging.getLogger(__name__)

# ── Service names ───────────────────────────────────────────────────

SERVICE_ADD_NOTE = "add_reservation_note"
SERVICE_SET_STATUS = "set_listing_status"
SERVICE_CREATE_TASK = "create_task"
SERVICE_SET_CALENDAR = "set_calendar_availability"
SERVICE_SET_RESERVATION_STATUS = "set_reservation_status"
_ALL_SERVICES = (
    SERVICE_ADD_NOTE,
    SERVICE_SET_STATUS,
    SERVICE_CREATE_TASK,
    SERVICE_SET_CALENDAR,
    SERVICE_SET_RESERVATION_STATUS,
)

# ── Write routing (FR-058) ──────────────────────────────────────────

# The reservation resource generation a write is sent to. These
# are the api/ constants, re-exported so the service layer and the
# client cannot drift apart on the value they exchange.
ROUTE_V3: Final[str] = WRITE_ROUTE_V3
ROUTE_LEGACY: Final[str] = WRITE_ROUTE_LEGACY

REFUSAL_NOT_REACHABLE: Final[str] = (
    "Reservation {reservation_id} is currently being published from "
    "its last known values because it is missing from the "
    "reservations v3 results and the legacy lookup for it has not "
    "succeeded in this session. Its identifier has not been "
    "validated against either resource, so no write is sent. Retry "
    "after the next successful refresh."
)


class ReservationIdentityMissError(GuestyActionError):
    """A target lacks current identity evidence, not necessarily a route."""


class ReservationIdentityAbsentError(ReservationIdentityMissError):
    """A target lacks current identity evidence and is not published."""


class ReservationIdentityPresentError(ReservationIdentityMissError):
    """A published target lacks current identity evidence."""


# ── Feature 013 evidence port (feature 014) ────────────────────────


class ConflictEvidence(Protocol):
    """Read-only feature 013 evidence the by-ID write fallback needs.

    Feature 013 supplies the real implementation. Until it does,
    production uses ``UnavailableConflictEvidence`` and every
    qualifying write keeps its unchanged feature 012 refusal.
    """

    def available(self) -> bool:
        """Report whether the evidence can be consulted at all.

        Returns:
            False when any 013 interface is missing or its bound
            was exceeded.
        """
        ...

    def codes_for(self, reservation_id: str) -> frozenset[str]:
        """Return conflict-only codes recorded for an identifier.

        Args:
            reservation_id: The identifier to look up.

        Returns:
            The recorded codes.
        """
        ...

    def ids_for(self, code: str) -> frozenset[str]:
        """Return conflict-only identifiers recorded for a code.

        Args:
            code: The code to look up.

        Returns:
            The recorded identifiers.
        """
        ...

    def validate_record(self, raw: Mapping[str, Any], action_type: str) -> str:
        """Apply the accepted-refresh rules to one raw v3 record.

        Args:
            raw: The raw by-ID record.
            action_type: The action the record would authorize.

        Returns:
            ``valid``, or any other value naming a rejection.
        """
        ...


class UnavailableConflictEvidence:
    """The production adapter while feature 013 is not merged.

    It reports unavailable, and consulting it for anything else is a
    programming error, so every lookup raises.
    """

    def available(self) -> bool:
        """Report that no 013 evidence exists.

        Returns:
            Always False.
        """
        return False

    def codes_for(self, reservation_id: str) -> frozenset[str]:
        """Refuse a lookup on unavailable evidence.

        Args:
            reservation_id: The identifier that was asked about.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("Feature 013 conflict evidence is unavailable")

    def ids_for(self, code: str) -> frozenset[str]:
        """Refuse a lookup on unavailable evidence.

        Args:
            code: The code that was asked about.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("Feature 013 conflict evidence is unavailable")

    def validate_record(self, raw: Mapping[str, Any], action_type: str) -> str:
        """Refuse validation without the 013 validator.

        Args:
            raw: The raw record that was offered.
            action_type: The action that was asked about.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("Feature 013 record validation is unavailable")


def _conflict_evidence(coordinator: ReservationsCoordinator) -> ConflictEvidence:
    """Return the feature 013 evidence adapter for an entry.

    Feature 013 supplies the real implementation (its task T051).
    No 013 logic may be added here: until then this always returns
    ``UnavailableConflictEvidence``, which keeps the by-ID write
    fallback inert in production.

    Args:
        coordinator: The entry's reservations coordinator.

    Returns:
        The evidence adapter.
    """
    return UnavailableConflictEvidence()


# ── Voluptuous schemas ──────────────────────────────────────────────

_SCHEMA_ADD_NOTE = vol.Schema(
    {
        vol.Required("reservation_id"): str,
        vol.Required("note_text"): str,
        vol.Optional("config_entry_id"): str,
    },
)

_SCHEMA_SET_STATUS = vol.Schema(
    {
        vol.Required("listing_id"): str,
        vol.Required("status"): vol.In(
            sorted(VALID_LISTING_STATUSES),
        ),
        vol.Optional("config_entry_id"): str,
    },
)

_SCHEMA_CREATE_TASK = vol.Schema(
    {
        vol.Required("listing_id"): str,
        vol.Required("task_title"): str,
        vol.Optional("description"): str,
        vol.Optional("assignee"): str,
        vol.Optional("config_entry_id"): str,
    },
)

_SCHEMA_SET_CALENDAR = vol.Schema(
    {
        vol.Required("listing_id"): str,
        vol.Required("start_date"): str,
        vol.Required("end_date"): str,
        vol.Required("operation"): vol.In(
            sorted(VALID_CALENDAR_OPS),
        ),
        vol.Optional("config_entry_id"): str,
    },
)

_SCHEMA_SET_RESERVATION_STATUS = vol.Schema(
    {
        vol.Required("reservation_id"): str,
        vol.Required("status"): vol.In(
            sorted(VALID_RESERVATION_STATUSES),
        ),
        vol.Optional("config_entry_id"): str,
    },
)

# ── Helpers ─────────────────────────────────────────────────────────


def _get_entry_data(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Resolve the hass.data entry a service call addresses.

    Uses an explicit config_entry_id when provided, otherwise
    resolves the sole loaded entry automatically.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        The resolved entry's hass.data mapping.

    Raises:
        HomeAssistantError: If resolution fails.
    """
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})

    config_entry_id = call.data.get("config_entry_id")
    if config_entry_id:
        entry_data: dict[str, Any] | None = domain_data.get(config_entry_id)
        if entry_data is None:
            msg = f"Config entry '{config_entry_id}' not found"
            raise HomeAssistantError(msg)
        return entry_data

    entries = list(domain_data.keys())
    if len(entries) == 0:
        msg = "No Guesty config entries loaded"
        raise HomeAssistantError(msg)
    if len(entries) > 1:
        msg = "Multiple Guesty config entries found; specify config_entry_id"
        raise HomeAssistantError(msg)

    resolved: dict[str, Any] = domain_data[entries[0]]
    return resolved


def _get_actions_client(
    hass: HomeAssistant,
    call: ServiceCall,
) -> GuestyActionsClient:
    """Resolve the GuestyActionsClient for a service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        The GuestyActionsClient for the resolved entry.

    Raises:
        HomeAssistantError: If resolution fails.
    """
    client: GuestyActionsClient = _get_entry_data(hass, call)["actions_client"]
    return client


def guard_reservation_write(
    entry_data: Mapping[str, Any],
    reservation_id: str,
    action_type: str,
) -> None:
    """Fail a reservation write closed on an unsure identity.

    The guard lives in the Home Assistant service layer so that
    ``api/`` keeps zero Home Assistant imports (NFR-007). It is
    evaluated before the request is built, so a refused write sends
    nothing at all (FR-031c, FR-033).

    Args:
        entry_data: The addressed entry's hass.data mapping.
        reservation_id: The reservation the write would target.
        action_type: The action being refused, for the error.

    Raises:
        GuestyActionError: When the identity cannot be confirmed.
    """
    coordinator: ReservationsCoordinator = entry_data["reservations_coordinator"]
    refusal = coordinator.identity.classify_write_refusal(reservation_id)
    if refusal is not None:
        error_type = GuestyActionError
        if refusal.missing_current_code and reservation_id.strip():
            present = any(
                reservation.id == reservation_id
                for reservations in (coordinator.data or {}).values()
                for reservation in reservations
            )
            error_type = (
                ReservationIdentityPresentError
                if present
                else ReservationIdentityAbsentError
            )
        raise error_type(
            refusal.message,
            target_id=reservation_id,
            action_type=action_type,
        )


def select_reservation_write_route(
    entry_data: Mapping[str, Any],
    reservation_id: str,
    action_type: str,
) -> str:
    """Choose the resource generation a reservation write uses.

    Evaluated **after** ``guard_reservation_write``: identity comes
    first, and routing is only meaningful once the identifier is
    trusted (FR-060).

    The decision is read from the fallback's live served set at the
    moment of the write, never from a value captured at refresh
    time and never from anything the caller supplied. A reservation
    the fallback is serving exists only on the legacy resource
    right now, so a v3 write against it would fail; one that has
    reappeared in v3 since the last refresh is already out of that
    set and routes to v3.

    A target the fallback is publishing from retained values but
    has never successfully looked up in this session selects
    neither route. Its identifier has not been validated by either
    resource this session, and guessing a generation would mean
    writing to whichever one happens to accept the identifier
    (FR-055, FR-058).

    Args:
        entry_data: The addressed entry's hass.data mapping.
        reservation_id: The reservation the write would target.
        action_type: The action being routed, for the error.

    Returns:
        ``ROUTE_LEGACY`` or ``ROUTE_V3``.

    Raises:
        GuestyActionError: When the target is reachable on neither
            resource, or when the legacy generation it would need
            has retired (FR-061).
    """
    coordinator: ReservationsCoordinator = entry_data["reservations_coordinator"]
    if reservation_id in coordinator.fallback_served_ids:
        api_client: GuestyApiClient = entry_data["api_client"]
        if not api_client.legacy_write_routing_available:
            raise GuestyActionError(
                REFUSAL_LEGACY_RETIRED.format(
                    reservation_id=reservation_id,
                ),
                target_id=reservation_id,
                action_type=action_type,
            )
        return ROUTE_LEGACY
    if reservation_id in coordinator.fallback_sourced_ids:
        raise GuestyActionError(
            REFUSAL_NOT_REACHABLE.format(reservation_id=reservation_id),
            target_id=reservation_id,
            action_type=action_type,
        )
    return ROUTE_V3


def _retired_target(
    entry_data: Mapping[str, Any],
    reservation_id: str,
) -> bool:
    """Report whether only a retired legacy resource held this.

    Args:
        entry_data: The addressed entry's hass.data mapping.
        reservation_id: The reservation a write would target.

    Returns:
        True when the fallback was publishing this reservation and
        the legacy generation has since self-disabled.
    """
    coordinator: ReservationsCoordinator = entry_data["reservations_coordinator"]
    known = coordinator.fallback_served_ids | coordinator.fallback_sourced_ids
    if reservation_id not in known:
        return False
    api_client: GuestyApiClient = entry_data["api_client"]
    return not api_client.legacy_write_routing_available


@dataclass(frozen=True, slots=True)
class _RecoveryWitness:
    """Keep live references needed to authorize one recovered write."""

    entry_data: Mapping[str, Any]
    coordinator: ReservationsCoordinator
    entry: ConfigEntry
    api_client: GuestyApiClient
    actions_client: object
    cf_client: object
    options: Mapping[str, Any]
    settings: tuple[Any, ...]
    generation: int


def _capture_recovery_witness(entry_data: Mapping[str, Any]) -> _RecoveryWitness:
    """Capture entry, client, options and refresh generation before waiting."""
    coordinator: ReservationsCoordinator = entry_data["reservations_coordinator"]
    entry = coordinator.config_entry
    return _RecoveryWitness(
        entry_data=entry_data,
        coordinator=coordinator,
        entry=entry,
        api_client=coordinator.api_client,
        actions_client=entry_data.get("actions_client"),
        cf_client=entry_data.get("cf_client"),
        options=entry.options,
        settings=coordinator._write_miss_settings(),
        generation=coordinator.write_miss_generation,
    )


def _check_recovery_context(
    witness: _RecoveryWitness, reservation_id: str, action_type: str
) -> None:
    """Recheck live configuration, liveness and accepted outcomes.

    The identity guard and the route are deliberately not part of
    this check, so the by-ID write fallback can reuse it.

    Args:
        witness: The witness captured before the refresh.
        reservation_id: The reservation the write targets.
        action_type: The action being checked, for the error.

    Raises:
        GuestyActionError: When the context changed or the refresh
            failed or was superseded.
    """
    coordinator = witness.coordinator
    entry = witness.entry
    hass = coordinator.hass
    entry_data = witness.entry_data
    live_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if (
        entry.options is not witness.options
        or coordinator._write_miss_settings() != witness.settings
        or coordinator.reservations_path != witness.settings[0]
        or witness.api_client.reservations_api != witness.settings[0]
    ):
        raise GuestyActionError(
            "The reservation configuration changed; reload before retrying.",
            target_id=reservation_id,
            action_type=action_type,
        )
    if (
        entry.state is not ConfigEntryState.LOADED
        or hass.config_entries.async_get_entry(entry.entry_id) is not entry
        or live_data is not entry_data
        or entry_data.get("reservations_coordinator") is not coordinator
        or entry_data.get("api_client") is not witness.api_client
        or coordinator.config_entry is not entry
        or coordinator.api_client is not witness.api_client
        or entry_data.get("actions_client") is not witness.actions_client
        or entry_data.get("cf_client") is not witness.cf_client
        or coordinator._shutdown_requested
        or hass.is_stopping
    ):
        raise GuestyActionError(
            "The Guesty entry is no longer loaded; reload before retrying.",
            target_id=reservation_id,
            action_type=action_type,
        )
    outcomes = coordinator._write_miss_outcomes
    if (
        not coordinator.last_update_success
        or not outcomes
        or witness.generation < outcomes[0][0] - 1
        or not any(generation > witness.generation for generation, *_ in outcomes)
        or any(
            not success
            or options is not witness.options
            or settings != witness.settings
            or refresh_entry is not witness.entry
            or refresh_client is not witness.api_client
            for (
                generation,
                _,
                success,
                options,
                settings,
                refresh_entry,
                refresh_client,
            ) in outcomes
            if generation > witness.generation
        )
    ):
        raise GuestyActionError(
            "The reservation refresh failed or was superseded; no write was sent.",
            target_id=reservation_id,
            action_type=action_type,
        )


def _check_recovery_witness(
    witness: _RecoveryWitness, reservation_id: str, action_type: str
) -> str:
    """Recheck accepted refresh, live configuration, identity and route."""
    _check_recovery_context(witness, reservation_id, action_type)
    entry_data = witness.entry_data
    guard_reservation_write(entry_data, reservation_id, action_type)
    return select_reservation_write_route(entry_data, reservation_id, action_type)


class _RecoveryGuardRefusal(GuestyActionError):
    """A safe identity, configuration or route refusal during dispatch."""


class _RecoveryWrite[WriteResultT](Protocol):
    """One recovered write, optionally carrying prefetched note fields.

    The route, single-attempt flag and authorization recheck are
    positional-only so existing three-argument writers still fit;
    ``notes`` is keyword-only because ``Callable`` cannot express it.
    """

    def __call__(
        self,
        route: str,
        single: bool,
        check: Callable[[], None] | None,
        /,
        *,
        notes: Mapping[str, object] | None = None,
    ) -> Awaitable[WriteResultT]:
        """Send the write.

        Args:
            route: The resource generation to write to.
            single: Issue the write exactly once.
            check: The authorization recheck run before sending.
            notes: The verified note fields for a by-ID note write.

        Returns:
            An awaitable of the write's result.
        """
        ...


# ── By-ID write fallback (feature 014) ─────────────────────────────

_BY_ID_STEMS: Final[Mapping[str, str]] = {
    "found_write_failed": "found by ID, write failed",
    "not_found": "not found by ID",
    "found_refused": "found but refused",
    "read_failed": "by-ID read failed",
}
_BY_ID_QUALIFIERS: Final[Mapping[str, str]] = {
    "absent": "absent from search",
    "codeless": "present without a confirmation code",
}
_BY_ID_RETRY_HINT: Final[str] = "retry after the next refresh"
_BY_ID_HINTS: Final[Mapping[str, str]] = {
    "authentication": "check credentials",
    "rate_limit": "Guesty rate-limited the lookup; retry later",
    "identity_conflict": (
        "the reservation's identity conflicts with known evidence; review it in Guesty"
    ),
}
_BY_ID_UNKNOWN_WRITE: Final[str] = (
    "The reservation write may have reached Guesty; it was not retried. "
    "Check Guesty before retrying."
)
_BY_ID_NOT_FOUND_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"not_found", "mismatched"}
)


class ReservationByIdOutcomeError(GuestyActionError):
    """A by-ID write fallback outcome other than a successful write.

    The message is built only from fixed text, the outcome, the
    qualifier, the category, the action type and the reservation ID,
    so no code, response content or untrusted value can reach it.

    Attributes:
        outcome: ``found_write_failed``, ``not_found``,
            ``found_refused`` or ``read_failed``.
        qualifier: ``absent`` or ``codeless``.
        category: The outcome's safe category.
    """

    def __init__(
        self,
        outcome: str,
        *,
        qualifier: str,
        category: str,
        reservation_id: str,
        action_type: str,
        detail: str | None = None,
    ) -> None:
        """Build the fixed-text outcome message.

        Args:
            outcome: The outcome class.
            qualifier: ``absent`` or ``codeless``.
            category: The outcome's safe category.
            reservation_id: The reservation the action targeted.
            action_type: The action that was attempted.
            detail: A fixed, safe write-failure text replacing the
                corrective hint.
        """
        self.outcome = outcome
        self.qualifier = qualifier
        self.category = category
        hint = detail or _BY_ID_HINTS.get(category, _BY_ID_RETRY_HINT)
        super().__init__(
            f"{_BY_ID_STEMS[outcome]} ({category}): reservation "
            f"{reservation_id} for {action_type}, "
            f"{_BY_ID_QUALIFIERS[qualifier]}; {hint}",
            target_id=reservation_id,
            action_type=action_type,
        )


@dataclass(frozen=True, slots=True)
class _ByIdEvidence:
    """The authorized facts of one by-ID record, held for one write.

    Attributes:
        reservation_id: The authorized reservation.
        qualifier: ``absent`` or ``codeless``.
        snapshot: The accepted-refresh snapshot authorization used.
        notes: The normalized note fields for a note write, else None.
    """

    reservation_id: str
    qualifier: str
    snapshot: AcceptedRefreshSnapshot
    notes: Mapping[str, object] | None


class _NotEligible(Exception):
    """A pre-read qualification failure carrying its DEBUG category."""

    def __init__(self, category: str) -> None:
        """Record the category.

        Args:
            category: The safe not-eligible category.
        """
        super().__init__(category)
        self.category = category


def _qualify_by_id_target(
    witness: _RecoveryWitness, reservation_id: str
) -> tuple[str, AcceptedRefreshSnapshot, ConflictEvidence]:
    """Run the ordered pre-read qualification checks.

    Args:
        witness: The witness captured before the refresh.
        reservation_id: The reservation the write targets.

    Returns:
        The qualifier, the snapshot and the evidence adapter.

    Raises:
        _NotEligible: When any check fails or cannot be evaluated.
    """
    coordinator = witness.coordinator
    api_client = witness.api_client
    configured = resolve_reservations_api(
        witness.entry.options.get(CONF_RESERVATIONS_API, DEFAULT_RESERVATIONS_API)
    )
    if not (
        witness.settings[0]
        == coordinator.reservations_path
        == api_client.reservations_api
        == configured
        == RESERVATIONS_API_V3
    ):
        raise _NotEligible("not_v3")
    if reservation_id in (
        coordinator.fallback_served_ids | coordinator.fallback_sourced_ids
    ):
        raise _NotEligible("fallback_provenance")
    snapshot = coordinator.accepted_refresh_snapshot()
    if snapshot is not None and reservation_id in (
        snapshot.fallback_served
        | snapshot.fallback_sourced
        | snapshot.fallback_observed
    ):
        raise _NotEligible("fallback_provenance")
    observed = api_client.legacy_observed_reservations
    if observed is None:
        raise _NotEligible("evidence_unavailable")
    if reservation_id in observed:
        raise _NotEligible("fallback_provenance")
    if snapshot is None:
        raise _NotEligible("evidence_unavailable")
    if reservation_id in snapshot.rejected_ids:
        raise _NotEligible("rejected")
    if reservation_id not in snapshot.returned_ids:
        qualifier = "absent"
    else:
        record = snapshot.records.get(reservation_id)
        if (
            record is None
            or not record.v3
            or record.hold
            or record.code_kind not in ("none", "empty")
        ):
            raise _NotEligible("not_qualifying")
        qualifier = "codeless"
    retired = api_client.legacy_retired_reservations
    if reservation_id in api_client.confirmed_absent_reservations or (
        reservation_id in snapshot.fallback_absent
    ):
        raise _NotEligible("confirmed_absent")
    if retired is None:
        raise _NotEligible("evidence_unavailable")
    if reservation_id in retired:
        raise _NotEligible("confirmed_absent")
    evidence = _conflict_evidence(coordinator)
    if not isinstance(witness.actions_client, GuestyActionsClient) or (
        not evidence.available()
    ):
        raise _NotEligible("evidence_unavailable")
    if evidence.codes_for(reservation_id):
        raise _NotEligible("rejected")
    return qualifier, snapshot, evidence


def _by_id_listing_pairs(
    raw: Mapping[str, object],
) -> frozenset[tuple[str, str | None]] | None:
    """Build a raw by-ID record's listing association pairs.

    Args:
        raw: The raw by-ID record.

    Returns:
        The pairs, or None when any segment is unverifiable.
    """
    stay = raw.get("stay")
    if not isinstance(stay, list) or not stay:
        return None
    pairs: set[tuple[str, str | None]] = set()
    for segment in stay:
        if not isinstance(segment, Mapping):
            return None
        unit = segment.get("listingId")
        parent = segment.get("parentListingId")
        if not isinstance(unit, str) or (
            parent is not None and not isinstance(parent, str)
        ):
            return None
        pairs.add((unit, parent))
    return frozenset(pairs)


def _authorize_by_id_record(
    *,
    coordinator: ReservationsCoordinator,
    reservation_id: str,
    action_type: str,
    qualifier: str,
    raw: Mapping[str, object],
    snapshot: AcceptedRefreshSnapshot,
    evidence_port: ConflictEvidence,
) -> _ByIdEvidence:
    """Authorize one by-ID record, in contract order, sending nothing.

    Args:
        coordinator: The entry's reservations coordinator.
        reservation_id: The reservation the write targets.
        action_type: The action the record would authorize.
        qualifier: ``absent`` or ``codeless``.
        raw: The single raw record the read proved.
        snapshot: The snapshot qualification used.
        evidence_port: The feature 013 evidence adapter.

    Returns:
        The authorized evidence.

    Raises:
        ReservationByIdOutcomeError: On any refusal.
    """

    def refuse(category: str) -> ReservationByIdOutcomeError:
        """Build a found-but-refused outcome.

        Args:
            category: The refusal category.

        Returns:
            The outcome error.
        """
        return ReservationByIdOutcomeError(
            "found_refused",
            qualifier=qualifier,
            category=category,
            reservation_id=reservation_id,
            action_type=action_type,
        )

    status = raw.get("status")
    mapped = (
        V3_STATUS_MAP.get(status.strip().lower()) if isinstance(status, str) else None
    )
    if mapped is None or mapped not in ACTIONABLE_STATUSES or mapped in HOLD_STATUSES:
        raise refuse("disallowed_status")
    try:
        verdict = evidence_port.validate_record(raw, action_type)
    except Exception:
        raise refuse("malformed") from None
    if verdict != "valid":
        raise refuse("malformed")
    if GuestyReservation.from_v3_api_dict(dict(raw)) is None:
        raise refuse("malformed")
    notes: Mapping[str, object] | None = None
    if action_type == SERVICE_ADD_NOTE:
        raw_notes = raw.get("notes")
        if raw_notes is None:
            notes = {}
        elif not isinstance(raw_notes, Mapping):
            raise refuse("malformed")
        else:
            notes = {
                key: value for key, value in raw_notes.items() if isinstance(key, str)
            }
    code = raw.get("confirmationCode")
    if not isinstance(code, str) or not code:
        raise refuse("no_code")
    try:
        conflict = (
            coordinator.accepted_refresh_snapshot() is not snapshot
            or not evidence_port.available()
            or any(
                (known_code == code) != (known_id == reservation_id)
                for mapping in (
                    snapshot.observed_codes,
                    snapshot.fallback_observed_codes,
                )
                for known_code, known_id in mapping.items()
            )
            or coordinator.identity.check_identity_conflict(reservation_id, code)
            != "none"
            or bool(evidence_port.codes_for(reservation_id))
            or bool(evidence_port.ids_for(code))
        )
    except Exception:
        conflict = True
    if conflict:
        raise refuse("identity_conflict")
    if qualifier == "codeless":
        record = snapshot.records.get(reservation_id)
        pairs = _by_id_listing_pairs(raw)
        if (
            record is None
            or record.listing_ids is None
            or pairs is None
            or pairs != record.listing_ids
        ):
            raise refuse("identity_conflict")
    return _ByIdEvidence(
        reservation_id=reservation_id,
        qualifier=qualifier,
        snapshot=snapshot,
        notes=notes,
    )


def _check_by_id_write(
    witness: _RecoveryWitness,
    evidence: _ByIdEvidence,
    action_type: str,
) -> None:
    """Recheck a by-ID write immediately before it is sent.

    Args:
        witness: The witness captured before the refresh.
        evidence: The authorized evidence.
        action_type: The action being written.

    Raises:
        GuestyActionError: When anything the write relies on changed.
    """
    reservation_id = evidence.reservation_id
    coordinator = witness.coordinator
    api_client = witness.api_client

    def refuse() -> GuestyActionError:
        """Build the recheck refusal.

        Returns:
            The refusal.
        """
        return GuestyActionError(
            "The by-ID write authorization changed.",
            target_id=reservation_id,
            action_type=action_type,
        )

    _check_recovery_context(witness, reservation_id, action_type)
    refusal = coordinator.identity.classify_write_refusal(reservation_id)
    observed = api_client.legacy_observed_reservations
    if (
        (refusal is not None and not refusal.missing_current_code)
        or coordinator.reservations_path != RESERVATIONS_API_V3
        or api_client.reservations_api != RESERVATIONS_API_V3
        or reservation_id
        in (coordinator.fallback_served_ids | coordinator.fallback_sourced_ids)
        or observed is None
        or reservation_id in observed
    ):
        raise refuse()
    current = coordinator.accepted_refresh_snapshot()
    if current is evidence.snapshot:
        return
    if (
        current is None
        or current.attempt <= evidence.snapshot.attempt
        or action_type == SERVICE_ADD_NOTE
        or reservation_id
        in (
            current.fallback_served
            | current.fallback_sourced
            | current.fallback_observed
        )
    ):
        raise refuse()
    guard_reservation_write(witness.entry_data, reservation_id, action_type)
    if (
        select_reservation_write_route(witness.entry_data, reservation_id, action_type)
        != ROUTE_V3
    ):
        raise refuse()


def _by_id_write_failure(
    exc: GuestyApiError,
) -> tuple[str, str]:
    """Classify a failed by-ID write into a category and safe text.

    Args:
        exc: The write's API error.

    Returns:
        The category and the fixed text to report.
    """
    stage = getattr(exc, "stage", None)
    if isinstance(
        exc,
        (
            GuestyRecoveryTokenError,
            GuestyRecoveryTokenConnectionError,
            GuestyRecoveryPreSendConnectionError,
        ),
    ) or (
        isinstance(exc, (GuestyRecoveryAuthError, GuestyRecoveryPermissionError))
        and stage == "token"
    ):
        return "not_sent", str(exc)
    if isinstance(
        exc,
        (
            GuestyRecoveryWriteError,
            GuestyRecoveryAuthError,
            GuestyRecoveryPermissionError,
        ),
    ):
        return "rejected", str(exc)
    if isinstance(exc, GuestyRecoveryConnectionError):
        return "unknown_outcome", str(exc)
    return "unknown_outcome", _BY_ID_UNKNOWN_WRITE


async def _attempt_by_id_write[WriteResultT](
    witness: _RecoveryWitness,
    reservation_id: str,
    miss: ReservationIdentityMissError,
    *,
    action_type: str,
    write: _RecoveryWrite[WriteResultT],
) -> WriteResultT:
    """Write a qualifying missed reservation after one by-ID read.

    Runs with the dispatch lock already held. A target that does
    not qualify re-raises the original miss unchanged, so the 012
    refusal stands; until feature 013 supplies its evidence adapter
    no target qualifies.

    Args:
        witness: The witness captured before the refresh.
        reservation_id: The reservation the write targets.
        miss: The missing-code refusal the guard raised.
        action_type: The action being written.
        write: The one-shot recovered write.

    Returns:
        Whatever the write returned.

    Raises:
        ReservationIdentityMissError: When the target is not eligible.
        ReservationByIdOutcomeError: On every by-ID failure outcome.
    """
    try:
        qualifier, snapshot, evidence_port = _qualify_by_id_target(
            witness, reservation_id
        )
    except Exception as exc:
        category = (
            exc.category if isinstance(exc, _NotEligible) else "evidence_unavailable"
        )
        _LOGGER.debug(
            "By-ID write fallback not eligible (%s) for reservation %s",
            category,
            reservation_id,
        )
        raise miss from None

    def outcome(outcome: str, category: str) -> ReservationByIdOutcomeError:
        """Build a by-ID outcome error for this action.

        Args:
            outcome: The outcome class.
            category: The outcome's safe category.

        Returns:
            The outcome error.
        """
        return ReservationByIdOutcomeError(
            outcome,
            qualifier=qualifier,
            category=category,
            reservation_id=reservation_id,
            action_type=action_type,
        )

    actions_client = witness.actions_client
    assert isinstance(actions_client, GuestyActionsClient)
    try:
        raw = await actions_client.read_reservation_by_id(reservation_id)
    except GuestyByIdReadError as exc:
        if exc.category in _BY_ID_NOT_FOUND_CATEGORIES:
            raise outcome("not_found", exc.category) from None
        if exc.category == "malformed":
            raise outcome("found_refused", "malformed") from None
        raise outcome("read_failed", exc.category) from None
    try:
        _check_recovery_context(witness, reservation_id, action_type)
    except GuestyActionError:
        raise outcome("found_refused", "guard_witness") from None
    evidence = _authorize_by_id_record(
        coordinator=witness.coordinator,
        reservation_id=reservation_id,
        action_type=action_type,
        qualifier=qualifier,
        raw=raw,
        snapshot=snapshot,
        evidence_port=evidence_port,
    )
    del raw

    def recheck() -> None:
        """Refuse if anything the write relies on changed."""
        try:
            _check_by_id_write(witness, evidence, action_type)
        except GuestyActionError as exc:
            raise _RecoveryGuardRefusal(
                "The by-ID write authorization changed.",
                target_id=reservation_id,
                action_type=action_type,
            ) from exc

    try:
        result = await write(ROUTE_V3, True, recheck, notes=evidence.notes)
    except _RecoveryGuardRefusal:
        raise outcome("found_refused", "guard_witness") from None
    except GuestyApiError as exc:
        category, detail = _by_id_write_failure(exc)
        raise ReservationByIdOutcomeError(
            "found_write_failed",
            qualifier=qualifier,
            category=category,
            reservation_id=reservation_id,
            action_type=action_type,
            detail=detail,
        ) from None
    _LOGGER.info(
        "By-ID write fallback: found by ID, written (%s) for %s on reservation %s",
        qualifier,
        action_type,
        reservation_id,
    )
    return result


async def _execute_recovered_reservation_write[WriteResultT](
    witness: _RecoveryWitness,
    reservation_id: str,
    *,
    operation: str,
    action_type: str,
    write: _RecoveryWrite[WriteResultT],
) -> WriteResultT:
    """Authorize and serialize one recovered write against live state."""
    coordinator = witness.coordinator
    async with coordinator.async_write_miss_dispatch_lock():
        _check_recovery_context(witness, reservation_id, action_type)
        try:
            guard_reservation_write(witness.entry_data, reservation_id, action_type)
        except ReservationIdentityMissError as miss:
            return await _attempt_by_id_write(
                witness,
                reservation_id,
                miss,
                action_type=action_type,
                write=write,
            )
        route = select_reservation_write_route(
            witness.entry_data, reservation_id, action_type
        )

        def recheck() -> None:
            """Refuse if authorization or route changed after an awaited step."""
            try:
                latest_route = _check_recovery_witness(
                    witness, reservation_id, action_type
                )
                if latest_route != route:
                    raise GuestyActionError(
                        "Reservation routing changed; the write was refused.",
                        target_id=reservation_id,
                        action_type=action_type,
                    )
            except GuestyActionError as exc:
                raise _RecoveryGuardRefusal(
                    f"{exc.message} The write was refused; no write was sent.",
                    target_id=reservation_id,
                    action_type=action_type,
                ) from None

        if route == ROUTE_LEGACY:
            _LOGGER.warning(
                "Sending %s for reservation %s to the legacy Guesty "
                "reservation resource: it is missing from the reservations "
                "v3 results and is currently being served by the "
                "not-found fallback. The legacy resources retire on %s.",
                action_type,
                reservation_id,
                LEGACY_RESERVATIONS_RETIREMENT_DATE,
            )
        try:
            result = await write(route, True, recheck)
        except GuestyApiError as exc:
            if route == ROUTE_LEGACY and not isinstance(exc, _RecoveryGuardRefusal):
                coordinator.record_legacy_write(operation, LEGACY_WRITE_FAILED)
            if isinstance(
                exc,
                (
                    _RecoveryGuardRefusal,
                    GuestyRoutingError,
                    GuestyPrerequisiteReadError,
                    GuestyRecoveryAuthError,
                    GuestyRecoveryPermissionError,
                    GuestyRecoveryConnectionError,
                    GuestyRecoveryTokenConnectionError,
                    GuestyRecoveryTokenError,
                    GuestyRecoveryWriteError,
                ),
            ):
                raise
            raise GuestyActionError(
                "The reservation write may have reached Guesty; it was not "
                "retried. Check Guesty before retrying.",
                target_id=reservation_id,
                action_type=action_type,
            ) from None
        if route == ROUTE_LEGACY:
            coordinator.record_legacy_write(operation, LEGACY_WRITE_SUCCEEDED)
        return result


async def execute_reservation_write[WriteResultT](
    entry_data: Mapping[str, Any],
    reservation_id: str,
    *,
    operation: str,
    action_type: str,
    write: Callable[[str], Awaitable[WriteResultT]],
    recovery_write: _RecoveryWrite[WriteResultT] | None = None,
) -> WriteResultT:
    """Guard, route, announce and count one reservation write.

    Every reservation write goes through here so the three FR-001
    exceptions stay in one place: the identity guard runs first,
    then the route is chosen, and only a legacy-routed write is
    announced and counted (FR-060, FR-062).

    Args:
        entry_data: The addressed entry's hass.data mapping.
        reservation_id: The reservation the write targets.
        operation: One of ``LEGACY_WRITE_OPERATIONS``, for counting.
        action_type: The service name, for error reporting.
        write: Coroutine function taking the chosen route.
        recovery_write: One-shot write with an authorization recheck.

    Returns:
        Whatever the write returned.

    Raises:
        GuestyActionError: When the write is refused before it is
            sent.
    """
    if not reservation_id.strip():
        raise GuestyActionError(
            "reservation_id must be non-empty",
            target_id=reservation_id,
            action_type=action_type,
        )
    coordinator: ReservationsCoordinator = entry_data["reservations_coordinator"]
    try:
        guard_reservation_write(entry_data, reservation_id, action_type)
    except GuestyActionError as exc:
        # The guard still decides, and still refuses. A refresh
        # after the self-disable can drop the identity evidence for
        # a reservation only the retired legacy resource held, and
        # the bare identity message would not explain why it can
        # never succeed again (FR-061, FR-062).
        if _retired_target(entry_data, reservation_id):
            coordinator.record_legacy_write(operation, LEGACY_WRITE_REFUSED)
            raise GuestyActionError(
                f"{exc.message} "
                + REFUSAL_LEGACY_RETIRED.format(
                    reservation_id=reservation_id,
                ),
                target_id=reservation_id,
                action_type=action_type,
            ) from exc
        if isinstance(exc, ReservationIdentityMissError):
            _LOGGER.debug(
                "Refreshing reservation write miss (%s)",
                "present-without-identity"
                if isinstance(exc, ReservationIdentityPresentError)
                else "absent-from-cache",
            )
            try:
                select_reservation_write_route(entry_data, reservation_id, action_type)
            except GuestyActionError:
                coordinator.record_legacy_write(operation, LEGACY_WRITE_REFUSED)
                raise
            configured_source = resolve_reservations_api(
                coordinator.config_entry.options.get(
                    CONF_RESERVATIONS_API, DEFAULT_RESERVATIONS_API
                )
            )
            if configured_source != coordinator.reservations_path:
                raise GuestyActionError(
                    "The reservation source has changed; reload the integration "
                    "before retrying.",
                    target_id=reservation_id,
                    action_type=action_type,
                ) from None
            live_data = coordinator.hass.data.get(DOMAIN, {}).get(
                coordinator.config_entry.entry_id
            )
            if (
                live_data is not entry_data
                or live_data.get("reservations_coordinator") is not coordinator
                or coordinator.config_entry.state is not ConfigEntryState.LOADED
                or coordinator._shutdown_requested
                or coordinator.hass.is_stopping
            ):
                raise GuestyActionError(
                    "The Guesty entry is no longer loaded; reload before retrying.",
                    target_id=reservation_id,
                    action_type=action_type,
                ) from None
            witness = _capture_recovery_witness(entry_data)
            if not await coordinator.async_refresh_for_write_miss(witness.generation):
                if isinstance(coordinator.last_exception, ReservationSearchRejected):
                    raise GuestyActionError(
                        f"{RESERVATION_SEARCH_REJECTED_MESSAGE} No write was sent.",
                        target_id=reservation_id,
                        action_type=action_type,
                    ) from None
                raise GuestyActionError(
                    "The reservation refresh failed or was not accepted; "
                    "no write was sent. Retry after a successful refresh.",
                    target_id=reservation_id,
                    action_type=action_type,
                ) from None
            if recovery_write is None:
                raise GuestyActionError(
                    "The reservation refresh completed, but this write cannot yet "
                    "be safely recovered; no write was sent.",
                    target_id=reservation_id,
                    action_type=action_type,
                ) from None
            return await _execute_recovered_reservation_write(
                witness,
                reservation_id,
                operation=operation,
                action_type=action_type,
                write=recovery_write,
            )
        raise
    try:
        route = select_reservation_write_route(
            entry_data,
            reservation_id,
            action_type,
        )
    except GuestyActionError:
        coordinator.record_legacy_write(operation, LEGACY_WRITE_REFUSED)
        raise

    if route != ROUTE_LEGACY:
        return await write(route)

    _LOGGER.warning(
        "Sending %s for reservation %s to the legacy Guesty "
        "reservation resource: it is missing from the reservations "
        "v3 results and is currently being served by the "
        "not-found fallback. The legacy resources retire on %s.",
        action_type,
        reservation_id,
        LEGACY_RESERVATIONS_RETIREMENT_DATE,
    )
    try:
        result = await write(route)
    except Exception:
        coordinator.record_legacy_write(operation, LEGACY_WRITE_FAILED)
        raise
    coordinator.record_legacy_write(operation, LEGACY_WRITE_SUCCEEDED)
    return result


def _result_to_dict(result: ActionResult) -> dict[str, Any]:
    """Convert an ActionResult to a service response dictionary.

    Args:
        result: The ActionResult from the actions client.

    Returns:
        Dictionary suitable for service response data.
    """
    response: dict[str, Any] = {
        "success": result.success,
        "target_id": result.target_id,
    }
    if result.error is not None:
        response["error"] = result.error
    return response


# ── Service handlers ────────────────────────────────────────────────


async def _handle_add_note(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle guesty.add_reservation_note service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        ActionResult as a dictionary.

    Raises:
        HomeAssistantError: On API or validation failure.
    """
    entry_data = _get_entry_data(hass, call)
    client: GuestyActionsClient = entry_data["actions_client"]
    reservation_id: str = call.data["reservation_id"]
    note_text: str = call.data["note_text"]
    try:
        GuestyActionsClient._validate_reservation_note(reservation_id, note_text)
        result = await execute_reservation_write(
            entry_data,
            reservation_id,
            operation=LEGACY_WRITE_NOTE,
            action_type=SERVICE_ADD_NOTE,
            write=lambda route: client.add_reservation_note(
                reservation_id=reservation_id,
                note_text=note_text,
                route=route,
            ),
            recovery_write=lambda route, single, check, notes=None: (
                client.add_reservation_note(
                    reservation_id=reservation_id,
                    note_text=note_text,
                    route=route,
                    single_attempt=single,
                    before_write=check,
                    # Only the by-ID path carries verified notes; the
                    # 012 recovered write keeps its own note read.
                    **({} if notes is None else {"prefetched_notes": notes}),
                )
            ),
        )
    except (GuestyActionError, GuestyApiError, ValueError) as exc:
        raise HomeAssistantError(str(exc)) from exc
    return _result_to_dict(result)


async def _handle_set_status(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle guesty.set_listing_status service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        ActionResult as a dictionary.

    Raises:
        HomeAssistantError: On API or validation failure.
    """
    client = _get_actions_client(hass, call)
    try:
        result = await client.set_listing_status(
            listing_id=call.data["listing_id"],
            status=call.data["status"],
        )
    except (GuestyActionError, GuestyApiError, ValueError) as exc:
        raise HomeAssistantError(str(exc)) from exc
    return _result_to_dict(result)


async def _handle_create_task(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle guesty.create_task service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        ActionResult as a dictionary.

    Raises:
        HomeAssistantError: On API or validation failure.
    """
    client = _get_actions_client(hass, call)
    try:
        result = await client.create_task(
            listing_id=call.data["listing_id"],
            task_title=call.data["task_title"],
            description=call.data.get("description"),
            assignee=call.data.get("assignee"),
        )
    except (GuestyActionError, GuestyApiError, ValueError) as exc:
        raise HomeAssistantError(str(exc)) from exc
    return _result_to_dict(result)


async def _handle_set_calendar(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle guesty.set_calendar_availability service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        ActionResult as a dictionary.

    Raises:
        HomeAssistantError: On API or validation failure.
    """
    client = _get_actions_client(hass, call)
    try:
        result = await client.set_calendar_availability(
            listing_id=call.data["listing_id"],
            start_date=call.data["start_date"],
            end_date=call.data["end_date"],
            operation=call.data["operation"],
        )
    except (GuestyActionError, GuestyApiError, ValueError) as exc:
        raise HomeAssistantError(str(exc)) from exc
    return _result_to_dict(result)


async def _handle_set_reservation_status(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle guesty.set_reservation_status service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        ActionResult as a dictionary.

    Raises:
        HomeAssistantError: On API or validation failure.
    """
    entry_data = _get_entry_data(hass, call)
    client: GuestyActionsClient = entry_data["actions_client"]
    reservation_id: str = call.data["reservation_id"]
    status: str = call.data["status"]
    try:
        result = await execute_reservation_write(
            entry_data,
            reservation_id,
            operation=LEGACY_WRITE_STATUS,
            action_type=SERVICE_SET_RESERVATION_STATUS,
            write=lambda route: client.set_reservation_status(
                reservation_id=reservation_id,
                status=status,
                route=route,
            ),
            recovery_write=lambda route, single, check, notes=None: (
                client.set_reservation_status(
                    reservation_id=reservation_id,
                    status=status,
                    route=route,
                    single_attempt=single,
                    before_write=check,
                )
            ),
        )
    except (GuestyActionError, GuestyApiError, ValueError) as exc:
        raise HomeAssistantError(str(exc)) from exc
    return _result_to_dict(result)


# ── Lifecycle ───────────────────────────────────────────────────────

_HandlerFn = Callable[
    [HomeAssistant, ServiceCall],
    Coroutine[Any, Any, dict[str, Any]],
]

_SERVICE_DEFS: tuple[
    tuple[str, vol.Schema, _HandlerFn],
    ...,
] = (
    (SERVICE_ADD_NOTE, _SCHEMA_ADD_NOTE, _handle_add_note),
    (SERVICE_SET_STATUS, _SCHEMA_SET_STATUS, _handle_set_status),
    (
        SERVICE_CREATE_TASK,
        _SCHEMA_CREATE_TASK,
        _handle_create_task,
    ),
    (
        SERVICE_SET_CALENDAR,
        _SCHEMA_SET_CALENDAR,
        _handle_set_calendar,
    ),
    (
        SERVICE_SET_RESERVATION_STATUS,
        _SCHEMA_SET_RESERVATION_STATUS,
        _handle_set_reservation_status,
    ),
)


async def async_setup_actions(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Register Guesty action services on first entry load.

    Skips registration if services are already present (multi-entry).

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.
    """
    if hass.services.has_service(DOMAIN, SERVICE_ADD_NOTE):
        return

    for name, schema, handler in _SERVICE_DEFS:

        async def _make_handler(
            call: ServiceCall,
            _fn: _HandlerFn = handler,
        ) -> dict[str, Any]:
            """Wrap handler with hass binding.

            Args:
                call: The incoming service call.
                _fn: The handler function (captured via default).

            Returns:
                Service response dictionary.
            """
            return await _fn(hass, call)

        hass.services.async_register(
            DOMAIN,
            name,
            _make_handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )

    _LOGGER.debug("Registered %d action services", len(_SERVICE_DEFS))


async def async_unload_actions(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Remove Guesty action services on last entry unload.

    Called after the entry has been removed from hass.data[DOMAIN],
    so remaining entries are simply len(domain_data).

    Args:
        hass: Home Assistant instance.
        entry: The config entry being unloaded.
    """
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    if len(domain_data) > 0:
        return

    for name in _ALL_SERVICES:
        hass.services.async_remove(DOMAIN, name)

    _LOGGER.debug("Removed %d action services", len(_ALL_SERVICES))

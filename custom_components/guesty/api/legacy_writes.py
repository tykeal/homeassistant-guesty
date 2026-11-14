# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Legacy write routing for fallback-served reservations.

Permitted exception 3 of FR-001, specified by FR-058 to FR-063. A
reservation the legacy not-found fallback is currently serving does
not exist on the v3 resources at all, so a v3 write against it would
be rejected as "reservation not found". While the legacy generation
still answers, such a write is routed to the legacy catch-all update
instead of being refused.

The routing is deliberately narrow:

* only the three operations of FR-059 may use it, and only for a
  reservation a legacy by-id lookup has actually returned in this
  session, so the identifier the write carries is one the legacy
  resource itself validated;
* the request is issued in the single-attempt mode of FR-063, so one
  invocation can never become several writes; and
* an answer consistent with retirement self-disables the routing for
  the session, after which such a write is refused with a message
  naming the retirement date rather than sent anywhere.

This module holds zero Home Assistant imports (NFR-007).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from custom_components.guesty.api.const import (
    LEGACY_RESERVATIONS_RETIREMENT_DATE,
    RESERVATION_BY_ID_LEGACY_PATH,
)
from custom_components.guesty.api.reservations_fallback import (
    classify_legacy_response,
    is_absent,
    is_retired,
)

if TYPE_CHECKING:
    import httpx

    from custom_components.guesty.api.client import GuestyApiClient

# Refusal shown when the legacy resource can no longer be used for a
# reservation v3 does not yet hold (FR-061).
REFUSAL_LEGACY_RETIRED: str = (
    "Reservation {reservation_id} is not available in the Guesty "
    "reservations v3 API yet, and the legacy reservation resource "
    "that could still reach it is no longer answering. The legacy "
    f"Guesty reservation resources retire on "
    f"{LEGACY_RESERVATIONS_RETIREMENT_DATE}. No request was sent "
    "and nothing was changed. The write can be retried once the "
    "reservation appears in a v3 refresh."
)

# Error raised when both generations agree the reservation is gone.
ERROR_RESERVATION_ABSENT: str = (
    "Reservation {reservation_id} no longer exists: the Guesty "
    "legacy reservation resource reports it is not found, and the "
    "reservations v3 API does not return it either. Nothing was "
    "written."
)


def legacy_reservation_path(reservation_id: str) -> str:
    """Build the legacy catch-all path for one reservation.

    Args:
        reservation_id: The identifier the fallback lookup
            succeeded with, never a confirmation code (FR-060).

    Returns:
        The legacy reservation resource path.
    """
    return RESERVATION_BY_ID_LEGACY_PATH.format(
        reservation_id=reservation_id,
    )


def routing_refusal(
    client: GuestyApiClient,
    reservation_id: str,
) -> str | None:
    """Report why a legacy-routed write cannot be issued.

    Args:
        client: The API client the write would use.
        reservation_id: The reservation the write targets.

    Returns:
        The refusal message, or None when routing is available.
    """
    if client.legacy_write_routing_available:
        return None
    return REFUSAL_LEGACY_RETIRED.format(
        reservation_id=reservation_id,
    )


def read_legacy_answer(
    client: GuestyApiClient,
    reservation_id: str,
    response: httpx.Response,
) -> str | None:
    """Apply the FR-052 and FR-061 side effects of one answer.

    A retirement-shaped answer disables the routing for the rest of
    the session; an answer naming this reservation as not found is
    confirmed absence and retires the fallback candidate. Neither
    verdict is ever retried, against either generation.

    Args:
        client: The API client that issued the request.
        reservation_id: The reservation that was addressed.
        response: The answer the legacy resource gave.

    Returns:
        The message the caller must fail with, or None when the
        answer carries no routing verdict.
    """
    if response.is_success:
        return None

    verdict = classify_legacy_response(response, reservation_id)
    if is_retired(verdict):
        client.disable_legacy_write_routing()
        return REFUSAL_LEGACY_RETIRED.format(
            reservation_id=reservation_id,
        )
    if is_absent(verdict):
        client.retire_fallback_candidate(reservation_id)
        return ERROR_RESERVATION_ABSENT.format(
            reservation_id=reservation_id,
        )
    return None

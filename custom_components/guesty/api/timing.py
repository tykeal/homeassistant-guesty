# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Pure helpers for resolving listing-local reservation wall times."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo


def is_valid_local_wall_time(wall_time: str | None) -> bool:
    """Report whether a value is a local wall time without an offset.

    Args:
        wall_time: Candidate ISO local time.

    Returns:
        True when it parses without timezone information.
    """
    if not isinstance(wall_time, str):
        return False
    try:
        parsed_time = time.fromisoformat(wall_time)
    except ValueError:
        return False
    return parsed_time.tzinfo is None


def resolve_local_datetime(
    local_date: str | None,
    wall_time: str | None,
    timezone: ZoneInfo | None,
) -> datetime | None:
    """Resolve a local date and wall time only when its instant is unique.

    Candidate folds are round-tripped through UTC. A gap produces no
    valid candidate, while a fold produces two distinct instants; both
    cases remain unknown instead of adopting an implicit DST policy.

    Args:
        local_date: Canonical ``YYYY-MM-DD`` calendar date.
        wall_time: Local wall-clock time without a UTC offset.
        timezone: Explicitly prepared IANA timezone, or None.

    Returns:
        The unique aware UTC instant, or None when inputs are invalid,
        missing, ambiguous, or nonexistent.
    """
    if not isinstance(local_date, str) or not isinstance(wall_time, str):
        return None
    if timezone is None:
        return None

    try:
        parsed_date = date.fromisoformat(local_date)
        parsed_time = time.fromisoformat(wall_time)
    except ValueError:
        return None
    if parsed_date.isoformat() != local_date or parsed_time.tzinfo is not None:
        return None

    naive_local = datetime.combine(parsed_date, parsed_time)
    candidates: set[datetime] = set()
    for fold in (0, 1):
        local_candidate = naive_local.replace(tzinfo=timezone, fold=fold)
        utc_candidate = local_candidate.astimezone(UTC)
        round_trip = utc_candidate.astimezone(timezone)
        if round_trip.replace(tzinfo=None) == naive_local:
            candidates.add(utc_candidate)

    if len(candidates) != 1:
        return None
    return next(iter(candidates))

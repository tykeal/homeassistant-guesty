# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for deterministic listing-local wall-time conversion."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.guesty.api.timing import resolve_local_datetime


@pytest.mark.parametrize(
    ("local_date", "wall_time", "expected"),
    [
        (
            "2025-01-15",
            "16:00",
            datetime(2025, 1, 15, 21, 0, tzinfo=UTC),
        ),
        (
            "2025-07-15",
            "16:00",
            datetime(2025, 7, 15, 20, 0, tzinfo=UTC),
        ),
        (
            "2025-03-08",
            "16:00",
            datetime(2025, 3, 8, 21, 0, tzinfo=UTC),
        ),
        (
            "2025-03-10",
            "11:00",
            datetime(2025, 3, 10, 15, 0, tzinfo=UTC),
        ),
    ],
)
def test_resolves_each_date_with_its_applicable_offset(
    local_date: str,
    wall_time: str,
    expected: datetime,
) -> None:
    """Each endpoint resolves with the zone offset on that local date."""
    assert (
        resolve_local_datetime(
            local_date,
            wall_time,
            ZoneInfo("America/New_York"),
        )
        == expected
    )


def test_unambiguous_time_resolves_once() -> None:
    """A normal wall time has one distinct UTC instant."""
    assert resolve_local_datetime(
        "2025-11-02",
        "00:30",
        ZoneInfo("America/New_York"),
    ) == datetime(2025, 11, 2, 4, 30, tzinfo=UTC)


@pytest.mark.parametrize("wall_time", ["01:30", "01:45"])
def test_fold_is_unknown(wall_time: str) -> None:
    """Both candidates in a fall-back fold are ambiguous and unknown."""
    assert (
        resolve_local_datetime(
            "2025-11-02",
            wall_time,
            ZoneInfo("America/New_York"),
        )
        is None
    )


def test_spring_gap_is_unknown() -> None:
    """A nonexistent spring-forward wall time remains unknown."""
    assert (
        resolve_local_datetime(
            "2025-03-09",
            "02:30",
            ZoneInfo("America/New_York"),
        )
        is None
    )


@pytest.mark.parametrize(
    ("local_date", "wall_time"),
    [
        ("not-a-date", "16:00"),
        ("2025-02-30", "16:00"),
        ("2025-01-15", "not-a-time"),
        ("2025-01-15", "16:00-05:00"),
        ("2025-01-15", None),
        (None, "16:00"),
    ],
)
def test_invalid_or_missing_local_inputs_are_unknown(
    local_date: str | None,
    wall_time: str | None,
) -> None:
    """Invalid calendar and offset-bearing time inputs are rejected."""
    assert (
        resolve_local_datetime(
            local_date,
            wall_time,
            ZoneInfo("America/New_York"),
        )
        is None
    )

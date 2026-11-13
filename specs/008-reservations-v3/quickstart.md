<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Reservations v3 API Migration

**Feature**: 008-reservations-v3 **Date**: 2026-11-13

## Prerequisites

- Python ≥3.14.2
- `uv` installed for dependency management
- Pre-commit hooks installed (`pre-commit install`)
- Working dev environment: `uv sync --group dev`

No Guesty account with v3 access is required — and none is available.
All development is against synthetic dictionary fixtures.

## Development Environment Setup

```bash
cd <repo-root>
git checkout -b 008-reservations-v3  # or switch if the branch exists
uv sync --group dev
```

## Running Tests

```bash
uv run pytest tests/

# Modules this feature touches
uv run pytest tests/api/test_reservations_v3.py -v
uv run pytest tests/api/test_guest_cache.py -v
uv run pytest tests/api/test_actions.py -v
uv run pytest tests/test_coordinator.py -v
uv run pytest tests/test_reservation_sensor.py -v
uv run pytest tests/test_identity.py -v

uv run pytest tests/ --cov=custom_components/guesty --cov-report=term-missing
```

## Running Linting

```bash
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/
uv run mypy custom_components/guesty/
uv run interrogate custom_components/guesty/
prek run --all-files
```

## Where the code lives

| Path | Role |
| --- | --- |
| `api/reservations_legacy.py` | today's `get_reservations`, moved verbatim |
| `api/reservations_v3.py` | the v3 source: requests, paging, mapping |
| `api/guest_cache.py` | guest lookups and their TTL/LRU cache |
| `api/models.py` | `GuestyStaySegment`, `GuestyReservation.stay` |
| `api/const.py` | v3 resources, status maps, budget constants |
| `api/actions.py` | the two v3 write resources |
| `identity.py` | confirmation-code baseline, `Store`-backed |
| `coordinator.py` | trim, attribution, projection, diagnostics |
| `config_flow.py` | the `reservations_api` option |

`api/` keeps zero Home Assistant imports; the identity guard and the
repair issue live outside it.

## Flipping the path

Settings → Devices & Services → Guesty → Configure → **Reservations
API** → `v3` or `legacy`. Changing it reloads the entry. Nothing in the
entity registry changes either way; only the data source does.

In tests, set it directly:

```python
hass.config_entries.async_update_entry(
    entry, options={**entry.options, "reservations_api": "legacy"}
)
await hass.config_entries.async_reload(entry.entry_id)
```

## Building fixtures

There are no captured live payloads. Build v3 payloads with a helper in
`tests/conftest.py` alongside the existing dictionary builders
(`tests/conftest.py:535-660`), not as JSON files:

```python
def make_v3_reservation(
    reservation_id: str,
    *,
    status: str = "confirmed",
    segments: list[dict] | None = None,
    guest_id: str | None = "guest-1",
) -> dict: ...


def make_v3_page(
    results: list[dict], *, skip: int = 0, has_more: bool = False
) -> dict: ...
```

Keep `reservationId` values sortable and unique: the page guard requires
strictly ascending ids across pages, so a fixture that violates it is
expected to fail.

Fixtures you will need at minimum: a single-segment stay; a two-segment
mid-stay move; a segment referencing an untracked listing; a segment
with only `parentListingId`; a `completed` stay; an `alteration` stay; an
unknown status; a reservation with no `stay`; a reservation with no
`guestId`; a duplicate id across pages; a short page with `hasMore`
true.

## Reading diagnostics

The config-entry diagnostics download gains a `reservations` block
(FR-042): the active path, pages fetched, reservations received, trimmed
and skipped, the raw status histogram, the segment-count histogram,
unknown-listing counts, guest lookups issued, cache hits and failures,
and whether an identity mismatch aborted the last refresh. No guest
details and no confirmation codes appear there.

## Things that will bite you

- **No `fields` projection in v3.** Pages are whole documents; do not
  reintroduce a projection parameter.
- **No total count.** Paging stops on `pagination.hasMore`, never on a
  short page alone.
- **No `filter[listingId]`.** It matches the first segment only, so it
  would silently drop mid-stay moves. FR-015 forbids it.
- **Attribution comes from `stay[]`,** never from a top-level listing id.
- **Requesting `checked_out` must also request `completed`,** or
  completed stays are filtered out server-side before mapping.
- **Never log a guest name, phone, email or confirmation code,** at any
  level.
- **Every v3 behaviour here is documentation-derived.** Before a stable
  release, work through the verification checklist in `research.md`.

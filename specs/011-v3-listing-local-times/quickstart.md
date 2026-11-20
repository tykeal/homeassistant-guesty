<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: V3 Listing-Local Reservation Times

This feature changes only planning and later V3 timing behavior. Do not query
or write to a live Guesty account to validate it. Use the existing fixture
based tests and fake clients.

## Developer setup

Run commands from the repository root with the project's locked environment:

```bash
uv sync --all-groups
uv run pytest tests/api/test_models.py tests/test_coordinator.py \
  tests/test_reservation_sensor.py tests/test_identity.py \
  tests/api/test_reservations_fallback.py tests/test_reservations_parity.py
```

The implementation must add focused tests before production changes. For
example, a winter New York arrival at 16:00 local resolves to 21:00 UTC,
while a summer arrival at the same local wall time resolves to 20:00 UTC.
These examples verify conversion only; the resolver must use each endpoint's
own date and must return unknown for ambiguous or nonexistent wall times.

## Required regression checks

1. Use exact-room metadata from the existing fake listings response. Assert
   that no additional Guesty request occurs and that parent settings do not
   supply a missing child's time or zone.
2. Keep raw `stay` array count and order in fixtures. Include malformed and
   filtered entries so Feature 010 fallback and same-date handoff eligibility
   cannot be inferred from only surviving segments.
3. Exercise known and unknown endpoints through coordinator window filtering,
   listing projection, state/status selection, final sensor attributes and
   upcoming summaries. Verify no prior-refresh instant appears after current
   timing becomes unknown.
4. Reload persisted identity fixtures in both existing two-endpoint and new
   unknown-candidate shapes. Verify a current unknown record neither triggers
   fallback nor changes identity conflict or write routing.
5. Run legacy parity tests and the eligible/ineligible Feature 010 cases.
   The legacy parser and fallback/write behavior must stay unchanged.

After focused tests, run the full project gates:

```bash
uv run pytest tests/
uv run ruff check custom_components/ tests/ scripts/
uv run --group releasenotes mypy custom_components/ tests/ scripts/
uv run pre-commit run --all-files
```

All automated checks and CI must pass before any permitted manual or
exploratory validation. Planning completion does not authorize implementation,
live queries/writes, release publication, or release tagging.

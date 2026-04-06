<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Listing Filtering

**Branch**: `007-listing-filtering` | **Date**: 2025-07-24 | **Spec**:
[spec.md](./spec.md) **Input**: Feature specification from
`/specs/007-listing-filtering/spec.md`

## Summary

Add listing filtering to the Guesty integration via the options flow. Two
filtering mechanisms are introduced: (1) a multi-select listing selector that
lets users choose which Guesty listings are tracked by Home Assistant, and (2)
an optional tag-based pre-filter that narrows the listing selector by Guesty
tags before selection. The filtering is applied at the coordinator level —
`ListingsCoordinator` returns only selected listings, which cascades to
`ReservationsCoordinator` (already filters by listings coordinator data). Device
and entity cleanup for deselected listings is handled via the HA device
registry. Backward compatibility is preserved: existing installations with no
filter configuration continue tracking all listings.

## Technical Context

**Language/Version**: Python ≥3.14.2 (mypy target 3.14, ruff target py314)
**Primary Dependencies**: homeassistant ≥2026.4.0, httpx ≥0.28, voluptuous (via
HA) **Storage**: HA config entry options (persistent key-value via
`entry.options`) **Testing**: pytest with pytest-asyncio (asyncio_mode=auto),
pytest-cov, respx, pytest-homeassistant-custom-component **Target Platform**:
Home Assistant (custom component via HACS) **Project Type**: HA custom
integration (hub, cloud_polling) **Performance Goals**: Filter changes reflected
in <30 seconds (SC-001); must not block HA event loop (Constitution VIII)
**Constraints**: api/ package must remain library-extractable (zero HA imports);
100% docstring coverage (interrogate); 100% test coverage; all pre-commit hooks
must pass **Scale/Scope**: Supports accounts with 50–100+ listings; tag-based
narrowing for large accounts (SC-003)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | ✅ | Notes |
| --- | --- | --- | --- |
| I | Quality & Testing | ✅ | TDD enforced; 100% docstrings; typed |
| II | API Client | ✅ | api/ unchanged; tags ready; no HA imports |
| III | Atomic Commits | ✅ | consts, coord, flow, cleanup, tests |
| IV | Licensing | ✅ | SPDX headers; REUSE pre-commit verified |
| V | Pre-Commit | ✅ | All hooks pass; no --no-verify |
| VI | Co-Authorship & DCO | ✅ | Co-authored-by + Signed-off-by trailers |
| VII | UX Consistency | ✅ | HA flow patterns; SelectSelector used |
| VIII | Performance | ✅ | Async I/O; existing client; on-demand |
| IX | Phased Dev | ✅ | consts→coord→flow→cleanup→refresh |
| X | Security | ✅ | No new creds; client reused; IDs safe |

**Gate Result**: ✅ ALL PASS — proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/007-listing-filtering/
├── plan.md              # This file
├── research.md          # Phase 0: Design research and decisions
├── data-model.md        # Phase 1: Data model and state changes
├── quickstart.md        # Phase 1: Development quickstart guide
├── contracts/           # Phase 1: Interface contracts
│   └── options-flow.md  # Options flow step contract
└── tasks.md             # Phase 2: Task breakdown (created by /speckit.tasks)
```

### Source Code (repository root)

```text
custom_components/guesty/
├── api/                    # NO CHANGES; zero HA imports
│   └── models.py           # tags field already present
├── __init__.py             # Mod: filter + entity cleanup
├── config_flow.py          # Mod: multi-step options flow
├── const.py                # Mod: filter constants added
├── coordinator.py          # Mod: filter by selected IDs
├── entity.py               # No changes
├── sensor.py               # No changes (coord-driven)
├── strings.json            # Mod: options flow strings
└── translations/
    └── en.json             # Mod: translation strings

tests/
├── conftest.py             # Mod: filter fixtures
├── test_config_flow.py     # Mod: options flow tests
├── test_coordinator.py     # Mod: filter tests
├── test_init.py            # Mod: cleanup tests
└── test_sensor.py          # Mod: lifecycle tests
```

**Structure Decision**: Existing HA custom component structure. All source under
`custom_components/guesty/`, all tests under `tests/`. The api/ package is NOT
modified (it already provides all needed data including tags). Changes are
concentrated in the HA integration layer: config flow, coordinator, init,
constants, and translations.

## Complexity Tracking

No constitution violations. No complexity justifications needed.

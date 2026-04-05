<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Listings/Properties

**Branch**: `002-listings-properties` | **Date**: 2025-07-18
**Spec**: `specs/002-listings-properties/spec.md`
**Input**: Feature specification from
`/specs/002-listings-properties/spec.md`

## Summary

Fetch all Guesty listings via the Open API v1 `/listings` endpoint
and expose each listing as a Home Assistant device with sensor
entities for operational status, property details, tags, and custom
fields. A `DataUpdateCoordinator` provides periodic polling (default
15 minutes, configurable via options flow) with graceful degradation
on API errors. The `api/` sub-package gains a paginated
`get_listings()` method with zero HA imports to preserve library
extractability. Listing data models live in `api/models.py` as
frozen dataclasses; the HA-specific coordinator, entity descriptions,
and sensor platform bridge the gap between the API layer and the HA
entity registry.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant >=2026.4.0, httpx >=0.28
**Storage**: HA config entry storage (via `hass.config_entries`)
**Testing**: pytest (via `uv run pytest tests/`)
**Target Platform**: Home Assistant custom component (HACS)
**Project Type**: HA integration (custom component)
**Performance Goals**: Handle 500+ listings without timeout;
no measurable HA event loop degradation
**Constraints**: All I/O async; no blocking calls on event loop;
`api/` sub-package must have zero HA imports; Guesty API rate
limits respected (existing backoff/retry from Feature 001)
**Scale/Scope**: Hundreds of listings per account; 10+ sensor
entities per listing device; single coordinator per config entry

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after
Phase 1 design.*

| # | Principle | Status |
| - | --------- | ------ |
| I | Code Quality & Testing | ✅ PASS |
| II | API Client Design | ✅ PASS |
| III | Atomic Commit Discipline | ✅ PASS |
| IV | Licensing & Attribution | ✅ PASS |
| V | Pre-Commit Integrity | ✅ PASS |
| VI | Agent Co-Authorship & DCO | ✅ PASS |
| VII | User Experience Consistency | ✅ PASS |
| VIII | Performance Requirements | ✅ PASS |
| IX | Phased Development | ✅ PASS |
| X | Security & Credentials | ✅ PASS |

**Notes**:

- **I**: TDD mandated; 100% docstrings; mypy, ruff
- **II**: `get_listings()` in `api/client.py`; no HA imports
- **III**: Atomic commits per phase
- **IV**: All new files get SPDX headers
- **V**: All hooks run; no `--no-verify`
- **VI**: `Co-authored-by` + `git commit -s`
- **VII**: Standard HA device/entity patterns
- **VIII**: Async I/O; configurable polling
- **IX**: Three phases: API→Coordinator→Sensors
- **X**: Existing token management; no new creds

**Gate result**: ✅ ALL PASS — proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/002-listings-properties/
├── plan.md              # This file
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: entity/field definitions
├── quickstart.md        # Phase 1: developer onboarding
├── contracts/           # Phase 1: API contracts
│   └── guesty-listings-api.md
└── tasks.md             # Phase 2: task breakdown
```

### Source Code (repository root)

```text
custom_components/guesty/
├── api/                          # HA-independent API layer
│   ├── __init__.py               # (existing)
│   ├── auth.py                   # (existing) GuestyTokenManager
│   ├── client.py                 # (modify) add get_listings()
│   ├── const.py                  # (modify) add listing constants
│   ├── exceptions.py             # (existing) exception hierarchy
│   └── models.py                 # (modify) add listing models
├── __init__.py                   # (modify) create coordinator
├── config_flow.py                # (modify) add options flow
├── const.py                      # (modify) add PLATFORMS, options
├── coordinator.py                # (new) ListingsCoordinator
├── entity.py                     # (new) GuestyEntity base class
├── manifest.json                 # (existing)
├── sensor.py                     # (new) sensor platform setup
├── strings.json                  # (modify) add sensor translations
└── translations/
    └── en.json                   # (new) English translations

tests/
├── conftest.py                   # (modify) add listing fixtures
├── test_api_client.py            # (existing, extend)
├── test_config_flow.py           # (existing, extend)
├── test_coordinator.py           # (new) coordinator tests
├── test_init.py                  # (existing, extend)
├── test_models.py                # (new) listing model tests
└── test_sensor.py                # (new) sensor entity tests
```

**Structure Decision**: HA custom component single-project layout.
The `api/` sub-package remains a standalone library layer (zero HA
imports). New coordinator, entity base, and sensor platform modules
follow standard HA integration patterns. Tests mirror source
structure at the repository root.

## Complexity Tracking

No constitution violations detected. Table omitted.

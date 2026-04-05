<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Guesty Notify Service

**Branch**: `005-notify-service` | **Date**: 2025-07-24 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`/specs/005-notify-service/spec.md`

## Summary

Implement a notify service for the Guesty Home Assistant integration
that enables property managers to send messages to guests via Guesty's
conversation-based communication API. The service uses the modern HA
`NotifyEntity` platform pattern, accepting a reservation ID as the
message target with optional channel selection and template variable
substitution.

The implementation extends the existing **library-shim architecture**:
a messaging client is added to the HA-independent `api/` sub-package
(zero `homeassistant.*` imports) containing conversation resolution,
message sending, and response models. The HA-specific `notify.py`
platform bridges the `api/` messaging client to Home Assistant's
`NotifyEntity` interface, using the existing authenticated
`GuestyApiClient` established in Feature 001 for all API communication.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: httpx (async HTTP), homeassistant (>=2026.4.0)
**Storage**: N/A — stateless message delivery; no persistent storage
beyond existing Feature 001 token/config entry storage
**Testing**: pytest with pytest-homeassistant-custom-component, respx
(httpx mocking)
**Target Platform**: Home Assistant custom component (HACS-installable)
**Project Type**: HA custom integration with extractable API library
**Performance Goals**: Message delivery in <10s from service call to
Guesty acceptance (SC-001); non-blocking async operations; zero
measurable overhead on HA event loop (SC-007)
**Constraints**: API rate limits 15/s, 120/min, 5000/hr; Guesty
channel availability determined by booking source; outbound only (no
inbound message handling)
**Scale/Scope**: 1 notify platform entity, ~5 new source files, ~5
new test modules; extends existing api/ package with messaging client

## Constitution Check

*GATE: Must pass before research. Re-check after Phase 1
design. Phase 0 (research) is captured in research.md.*

- **I. Code Quality & Testing**: ✅ PASS — TDD enforced
  within each phase; 100% docstring coverage; full type
  annotations on all public APIs.
- **II. API Client Design**: ✅ PASS — Messaging client in
  `api/` sub-package with zero HA imports; reuses existing
  `GuestyApiClient` for authenticated requests; rate limit
  handling inherited from Feature 001.
- **III. Atomic Commit Discipline**: ✅ PASS — Each phase
  broken into atomic commits; task updates separate.
- **IV. Licensing & Attribution**: ✅ PASS — SPDX headers
  on all new files; REUSE.toml covers JSON assets.
- **V. Pre-Commit Integrity**: ✅ PASS — All hooks run;
  no bypass.
- **VI. Agent Co-Authorship & DCO**: ✅ PASS —
  `git commit -s` with Co-authored-by trailer.
- **VII. UX Consistency**: ✅ PASS — Modern `NotifyEntity`
  platform; standard `notify.send_message` service call;
  service call data for reservation ID and channel.
- **VIII. Performance Requirements**: ✅ PASS — Full async
  via `async_send_message`; no event loop blocking; rate
  limit awareness inherited from existing API client.
- **IX. Phased Development**: ✅ PASS — 4 phases; unit TDD
  in each; integration tests in Phase 4.
- **X. Security & Credentials**: ✅ PASS — No message
  content or guest PII in logs; tokens managed by existing
  infrastructure; input validation before API calls.

**Gate result**: ✅ ALL PASS — proceed to Phase 0.

**Post-Phase 1 re-check**: ✅ ALL PASS — design confirmed
compliant. Messaging client maintains zero HA imports. Notify
entity uses standard HA patterns. No constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/005-notify-service/
├── plan.md              # This file
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: entity/data model
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/           # Phase 1: API contracts
│   └── guesty-messaging.md  # Messaging API contract
└── tasks.md             # Phase 2: task breakdown (separate command)
```

### Source Code (repository root)

```text
custom_components/guesty/
├── __init__.py          # HA integration setup (EXISTING — updated)
├── config_flow.py       # HA config flow (EXISTING — unchanged)
├── const.py             # HA-level constants (EXISTING — updated)
├── strings.json         # Localized UI strings (EXISTING — unchanged)
├── manifest.json        # Integration metadata (EXISTING — unchanged)
├── notify.py            # NEW: HA notify platform entity
└── api/                 # LIBRARY-EXTRACTABLE PACKAGE
    ├── __init__.py       # Public API exports (EXISTING — updated)
    ├── client.py         # GuestyApiClient (EXISTING — extended)
    ├── auth.py           # GuestyTokenManager (EXISTING — unchanged)
    ├── models.py         # DTOs (EXISTING — extended with messaging)
    ├── exceptions.py     # Exception hierarchy (EXISTING — extended)
    ├── const.py          # API constants (EXISTING — extended)
    └── messaging.py      # NEW: GuestyMessagingClient

tests/
├── conftest.py              # Shared fixtures (EXISTING — extended)
├── api/
│   ├── __init__.py          # (EXISTING)
│   ├── test_auth.py         # (EXISTING — unchanged)
│   ├── test_client.py       # (EXISTING — unchanged)
│   ├── test_exceptions.py   # (EXISTING — unchanged)
│   ├── test_models.py       # (EXISTING — extended)
│   └── test_messaging.py    # NEW: messaging client tests
├── test_config_flow.py      # (EXISTING — unchanged)
├── test_init.py             # (EXISTING — unchanged)
├── test_notify.py           # NEW: notify platform tests
└── test_token_persistence.py  # (EXISTING — unchanged)
```

**Structure Decision**: Extends the existing Home Assistant custom
component layout established in Feature 001. New messaging logic is
added to the `api/` sub-package as `messaging.py` (maintains zero HA
imports). The HA-side `notify.py` bridges the messaging client to the
`NotifyEntity` platform. Existing files are extended minimally
(constants, exports, models) rather than restructured.

## Phase Overview

### Phase 1: Messaging API Client (`api/` package)

Build the library-extractable messaging client layer with zero HA
dependencies, extending the existing `api/` package.

**Deliverables**:

- `api/const.py` — Extended with messaging endpoint paths and
  validation constants (max message length, known channel types)
- `api/models.py` — Extended with messaging DTOs:
  `Conversation` (frozen dataclass: id, reservation_id,
  available_channels), `MessageRequest` (frozen dataclass:
  conversation_id, body, channel), `MessageDeliveryResult`
  (frozen dataclass: success, message_id, error_details)
- `api/exceptions.py` — Extended with `GuestyMessageError`
  (message delivery failure with reservation context)
- `api/messaging.py` — `GuestyMessagingClient`: resolves
  reservation ID → conversation, sends messages via
  `GuestyApiClient`, validates inputs (reservation ID format,
  message body length, channel availability), handles template
  variable substitution
- `api/__init__.py` — Updated exports for messaging types
- Unit tests for all new code (TDD: tests written first)

**Key design decisions**:

- `GuestyMessagingClient` receives `GuestyApiClient` via
  dependency injection (same DI pattern as existing code)
- Template variable substitution uses Python `str.format_map()`
  with strict validation (missing variables raise error, not
  partial render)
- Conversation resolution is a separate method from message
  sending to enable caching and reuse
- Channel validation occurs locally before API call to provide
  fast, clear error messages
- All DTOs are frozen dataclasses consistent with existing
  `CachedToken` pattern

### Phase 2: HA Notify Platform

Wire the messaging client into Home Assistant's notify entity
platform.

**Deliverables**:

- `const.py` — Updated `PLATFORMS` list to include
  `Platform.NOTIFY`
- `__init__.py` — Updated `async_setup_entry` to create
  `GuestyMessagingClient` and store in runtime data;
  updated `async_unload_entry` if cleanup needed
- `notify.py` — `GuestyNotifyEntity(NotifyEntity)`:
  - Implements `async_send_message(message, title)` as the
    standard HA interface
  - Extracts reservation ID, channel, and template variables
    from service call extra data via `self.hass` context
  - Delegates to `GuestyMessagingClient` for actual delivery
  - Records notification timestamp via
    `_async_record_notification()`
  - Maps messaging exceptions to HA-appropriate error handling
- `async_setup_entry` in `notify.py` — Creates entity and
  registers with `async_add_entities`
- Unit tests for notify platform (TDD: tests written first)

### Phase 3: Template & Channel Features

Implement template variable substitution and channel selection
features.

**Deliverables**:

- Template variable substitution integration tests
  (end-to-end through notify entity → messaging client)
- Channel selection integration tests (valid channel, invalid
  channel, default channel)
- Edge case tests: expired reservation, archived conversation,
  oversized message, concurrent sends, unexpected API response
  format
- Error message quality tests: verify actionable error messages
  for all failure modes

### Phase 4: Integration Testing & Validation

Comprehensive test coverage and cross-phase validation.

**Deliverables**:

- Integration tests: notify service call → API client →
  mocked Guesty response flow
- Automation compatibility tests: verify notify entity works
  with HA automation and script service calls
- Rate limit handling tests: 429 response → retry → success
  path; 429 → max retries → failure path
- Security tests: verify no message content or guest PII in
  logs at any level
- Success criteria validation:
  - SC-001: Message delivery <10s
  - SC-005: Invalid calls produce errors <2s
  - SC-008: No sensitive data in logs
  - SC-009: All scenarios testable without live Guesty
  - SC-010: Template substitution correct

## Complexity Tracking

> No constitution violations identified. The notify platform adds
> one new source file (`notify.py`) and one new API module
> (`messaging.py`), both following established patterns from
> Feature 001. The messaging client reuses the existing
> `GuestyApiClient` rather than implementing separate HTTP logic.

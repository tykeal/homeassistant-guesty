<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Guesty Notify Service

**Input**: Design documents from `/specs/005-notify-service/`
**Prerequisites**: plan.md (required), spec.md (required for user
stories), research.md, data-model.md, contracts/

**Tests**: TDD is mandatory per project constitution. All test
tasks MUST be completed (Red phase) and verified FAILING before
their corresponding implementation tasks (Green phase). Each phase
becomes an independent PR.

**Organization**: Tasks are grouped by implementation phase aligned
with plan.md. User stories are mapped to the phase where their
functionality is delivered. Phase boundaries align with
independently deployable increments.

## Format: `[ID] [P?] [Story] Description`

- **\[P]**: Can run in parallel (different files, no deps)
- **\[Story]**: Which user story this task belongs to (US1–US5)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/guesty/` (HA custom component)
- **API library**: `custom_components/guesty/api/` (zero HA
  imports)
- **Tests**: `tests/` and `tests/api/` (pytest with respx
  mocking)

---

## Phase 1: Foundational — Messaging API Client

**Purpose**: Build the library-extractable messaging client in
the `api/` package with zero HA dependencies. Extends existing
Feature 001 API client infrastructure with conversation
resolution, message sending, template rendering, and input
validation.

**⚠️ CRITICAL**: No user story work can begin until this phase
is complete. All messaging domain logic lives in `api/`.

### Phase 1 Tests (Red) — Write First, Verify They FAIL

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

<!-- markdownlint-disable MD013 -->

- [ ] T001 [P] Write unit tests for `Conversation` frozen
  dataclass (construction with valid fields, empty id raises
  `ValueError`, empty `reservation_id` raises `ValueError`, empty
  `available_channels` raises `ValueError`) and `MessageRequest`
  frozen dataclass (construction with valid fields, empty
  `conversation_id` raises `ValueError`, empty body raises
  `ValueError`, body exceeding `MAX_MESSAGE_LENGTH` raises
  `ValueError`, unknown channel raises `ValueError`, `None`
  channel accepted) and `MessageDeliveryResult` frozen dataclass
  (success result with `message_id`, failure result with
  `error_details` and `reservation_id`) in
  tests/api/test\_models.py
- [ ] T002 [P] Write unit tests for `GuestyMessageError`
  (construction with message only, construction with
  `reservation_id` context, construction with
  `available_channels` context, inherits from `GuestyApiError`,
  attributes accessible after construction) in
  tests/api/test\_exceptions.py
- [ ] T003 Write unit tests for
  `GuestyMessagingClient.resolve_conversation` (successful
  resolution returns `Conversation` with correct fields parsed
  from API response, empty results raises `GuestyMessageError`
  with reservation context, API error propagation from
  `GuestyApiClient`) using respx-mocked HTTP in
  tests/api/test\_messaging.py
- [ ] T004 Write unit tests for
  `GuestyMessagingClient.send_message` (full success path:
  resolve conversation then send message returns
  `MessageDeliveryResult` with `success=True` and `message_id`,
  conversation resolution failure propagates
  `GuestyMessageError`, message send API failure raises
  `GuestyMessageError`, channel passed through to API request
  body module field) using respx-mocked HTTP in
  tests/api/test\_messaging.py
- [ ] T005 Write unit tests for
  `GuestyMessagingClient.render_template` (successful variable
  substitution with `str.format_map`, missing variable raises
  `KeyError` with variable name, template without placeholders
  returns unchanged, empty variables dict with no placeholders
  succeeds) and input validation (empty `reservation_id` raises
  `ValueError`, empty body raises `ValueError`, body exceeding
  `MAX_MESSAGE_LENGTH` raises `ValueError`, unknown channel
  string raises `ValueError`, valid known channels accepted) in
  tests/api/test\_messaging.py

<!-- markdownlint-enable MD013 -->

### Phase 1 Implementation (Green) — Make Tests Pass

<!-- markdownlint-disable MD013 -->

- [ ] T006 [P] Add messaging endpoint paths
  (`CONVERSATIONS_PATH`, `SEND_MESSAGE_PATH`) and validation
  constants (`MAX_MESSAGE_LENGTH`, `KNOWN_CHANNEL_TYPES`
  frozenset) to custom\_components/guesty/api/const.py
- [ ] T007 [P] Implement `GuestyMessageError` subclassing
  `GuestyApiError` with `reservation_id` (`str | None`) and
  `available_channels` (`tuple[str, ...] | None`) context
  attributes in custom\_components/guesty/api/exceptions.py
- [ ] T008 Implement `Conversation` (frozen dataclass: id,
  `reservation_id`, `available_channels` with post-init
  non-empty validation), `MessageRequest` (frozen dataclass:
  `conversation_id`, body, channel with post-init validation
  against `MAX_MESSAGE_LENGTH` and `KNOWN_CHANNEL_TYPES`), and
  `MessageDeliveryResult` (frozen dataclass: success,
  `message_id`, `error_details`, `reservation_id`) in
  custom\_components/guesty/api/models.py
- [ ] T009 Implement `GuestyMessagingClient` class with
  constructor accepting `GuestyApiClient`, async
  `resolve_conversation(reservation_id)` that calls GET
  conversations endpoint with reservation filter and returns
  `Conversation`, async `send_message(reservation_id, body,
  channel, template_variables)` pipeline (validate, render,
  resolve, build `MessageRequest`, POST send-message, return
  `MessageDeliveryResult`), and `render_template(template,
  variables)` using `str.format_map` with strict missing-key
  detection in custom\_components/guesty/api/messaging.py
- [ ] T010 Update public API exports to include
  `GuestyMessagingClient`, `GuestyMessageError`,
  `Conversation`, `MessageRequest`, and
  `MessageDeliveryResult` in
  custom\_components/guesty/api/\_\_init\_\_.py

<!-- markdownlint-enable MD013 -->

**Checkpoint**: All api/ messaging tests pass. The messaging
client can resolve conversations, send messages, render
templates, and validate inputs — all independently of Home
Assistant.

---

## Phase 2: US1 and US2 — Send Message + Automation (P1) 🎯 MVP

**Goal**: Wire the messaging client into Home Assistant's
`NotifyEntity` platform, enabling property managers to send
messages to guests from HA service calls, automations, and
scripts.

**Independent Test**: Call `notify.send_message` targeting the
Guesty notify entity with a reservation ID and message body.
Verify the messaging client receives the correct parameters and
returns success. Verify HA automation service calls dispatch
messages without blocking.

### Phase 2 Tests (Red) — Write First, Verify They FAIL

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

<!-- markdownlint-disable MD013 -->

- [ ] T011 [US1] Write unit tests for `GuestyNotifyEntity`:
  `async_send_message` delivers message via
  `GuestyMessagingClient` with correct `reservation_id` and
  body; missing `reservation_id` in service call data raises
  `HomeAssistantError` with actionable message; empty message
  body raises `HomeAssistantError`; `GuestyMessageError` from
  messaging client maps to `HomeAssistantError`; entity
  attributes (`unique_id`, name, `device_info`) are set
  correctly; `async_setup_entry` creates entity via
  `async_add_entities` in tests/test\_notify.py
- [ ] T012 [US2] Write automation compatibility tests: HA
  service call to `notify.send_message` dispatches message
  through messaging client; service call with template-rendered
  message body sends resolved text; `async_send_message` does
  not block the HA event loop; messaging client failure is
  logged and does not crash the automation in
  tests/test\_notify.py

<!-- markdownlint-enable MD013 -->

### Phase 2 Implementation (Green) — Make Tests Pass

<!-- markdownlint-disable MD013 -->

- [ ] T013 [P] [US1] Add `Platform.NOTIFY` to `PLATFORMS` list
  in custom\_components/guesty/const.py
- [ ] T014 [US1] Update `async_setup_entry` to create
  `GuestyMessagingClient(api_client)` and store in runtime
  data; verify `async_unload_entry` cleans up correctly in
  custom\_components/guesty/\_\_init\_\_.py
- [ ] T015 [US1] Implement `GuestyNotifyEntity(NotifyEntity)`
  with constructor accepting `messaging_client` and entry,
  `async_send_message(message, title)` that extracts
  `reservation_id`/channel/`template_variables` from service
  call data and delegates to `GuestyMessagingClient`, entity
  attributes (has entity name, name, `unique_id`,
  `device_info`), and `async_setup_entry` platform function in
  custom\_components/guesty/notify.py
- [ ] T016 [US1] Add messaging client mock fixture and notify
  entity test fixtures (mock `GuestyMessagingClient`, config
  entry setup with notify platform) to tests/conftest.py

<!-- markdownlint-enable MD013 -->

**Checkpoint**: Users can send messages to guests by reservation
ID via `notify.send_message` service call. Automations and
scripts can call the service without blocking. This is the MVP.

---

## Phase 3: US3 and US4 — Channel + Templates (P2)

**Goal**: Validate channel selection and template variable
substitution work end-to-end through the full notify entity to
messaging client to mocked Guesty API stack. Core logic was
built in Phase 1 (api/) and Phase 2 (notify.py); this phase
adds integration coverage and edge case handling.

**Independent Test**: Call `notify.send_message` with a
`channel` parameter and verify the API request uses the
specified channel. Call with `template_variables` and verify the
sent message contains substituted values. Verify actionable
errors for unavailable channels and missing template variables.

### Phase 3 Tests for US3 — Channel Selection

<!-- markdownlint-disable MD013 -->

- [ ] T017 [US3] Write integration tests for channel
  selection: specify "email" channel and verify `module.type`
  in API request body; specify "sms" channel and verify
  routing; omit channel and verify default channel used from
  conversation; verify channel parameter flows from service
  call data through notify entity to messaging client to API
  request in tests/test\_notify.py
- [ ] T018 [US3] Write unavailable channel error tests: request
  channel not present in conversation `availableModules` and
  verify `GuestyMessageError` lists available channels; verify
  error message is actionable (includes requested channel and
  available alternatives) in tests/test\_notify.py

<!-- markdownlint-enable MD013 -->

### Phase 3 Tests for US4 — Message Templates

<!-- markdownlint-disable MD013 -->

- [ ] T019 [US4] Write integration tests for template variable
  substitution: provide `template_variables` dict in service
  call data with message body containing `{guest_name}` and
  `{access_code}` placeholders; verify rendered message sent
  to Guesty API has substituted values; verify
  `template_variables` flows from service call data through
  notify entity to messaging client in tests/test\_notify.py
- [ ] T020 [US4] Write missing template variable error tests:
  message body contains `{guest_name}` placeholder but
  `template_variables` omits `guest_name`; verify error
  identifies the missing variable name; verify no partially
  rendered message is sent to the API in
  tests/test\_notify.py

<!-- markdownlint-enable MD013 -->

### Phase 3 Edge Case Tests

<!-- markdownlint-disable MD013 -->

- [ ] T021 Write edge case tests: conversation for
  expired/checked-out reservation returns API error and
  surfaces it clearly; oversized message body (exceeding
  `MAX_MESSAGE_LENGTH`) rejected before API call with
  validation error; concurrent `send_message` calls to the
  same reservation execute independently without interference;
  unexpected API response format (missing ID field) raises
  `GuestyResponseError` in tests/test\_notify.py

<!-- markdownlint-enable MD013 -->

**Checkpoint**: Channel selection routes messages through
specified channels with clear errors for unavailable channels.
Template substitution renders all variables with strict
missing-variable rejection. Edge cases produce actionable error
messages.

---

## Phase 4: US5 + Polish — Error Handling (P3)

**Purpose**: Comprehensive error scenario coverage through the
full stack, security validation, and success criteria
verification.

### Phase 4 Tests for US5 — Error and Rate Limit Handling

<!-- markdownlint-disable MD013 -->

- [ ] T022 [US5] Write rate limit retry integration tests: mock
  429 response with `Retry-After` header on first send-message
  call followed by 200 success on retry; verify message
  eventually delivered; verify retry used exponential backoff
  via `GuestyApiClient` in tests/test\_notify.py
- [ ] T023 [US5] Write transient failure retry tests: mock
  network error (connection refused) on first attempt followed
  by success on retry; mock persistent network failure and
  verify `GuestyConnectionError` raised after max retries;
  verify error includes reservation context in
  tests/test\_notify.py
- [ ] T024 [US5] Write error detail quality tests: invalid
  reservation ID (not found in Guesty) returns error with
  reservation ID in message; delivery failure after retries
  includes failure reason and targeted `reservation_id`; errors
  logged at appropriate severity (warning for retries, error
  for final failure) in tests/test\_notify.py

<!-- markdownlint-enable MD013 -->

### Phase 4 Cross-Cutting Validation

<!-- markdownlint-disable MD013 -->

- [ ] T025 Write security tests verifying no message body
  content, guest PII, or OAuth tokens appear in log output at
  any log level (DEBUG through CRITICAL) during successful
  send, failed send, and retry scenarios using caplog fixture
  in tests/test\_notify.py
- [ ] T026 Write success criteria validation tests: SC-005
  invalid service calls (missing `reservation_id`, empty body)
  produce errors synchronously; SC-009 all test scenarios run
  without live Guesty connection (verify respx mock coverage);
  SC-010 template substitution resolves all provided variables
  and rejects missing variables in tests/test\_notify.py
- [ ] T027 Run quickstart.md validation: verify all code
  examples in specs/005-notify-service/quickstart.md compile
  and execute correctly against mocked API fixtures; verify
  documented error handling patterns work as described

<!-- markdownlint-enable MD013 -->

**Checkpoint**: All error scenarios produce actionable messages
with reservation context. Rate limits and transient failures
handled transparently with retry. No sensitive data in logs. All
success criteria validated. Feature complete.

---

## Dependencies and Execution Order

### Phase Dependencies

- **Phase 1 (Foundational)**: No dependencies — extends existing
  Feature 001 api/ package
- **Phase 2 (US1 + US2 MVP)**: Depends on Phase 1 — BLOCKED
  until messaging client ready
- **Phase 3 (US3 + US4)**: Depends on Phase 2 — requires notify
  entity for integration tests
- **Phase 4 (US5 + Polish)**: Depends on Phase 2 — Phase 3
  recommended but not strictly required

### User Story Dependencies

- **US1 (P1)**: Phase 1 foundational then Phase 2 implementation
- **US2 (P1)**: Co-delivered with US1 in Phase 2 (same artifact)
- **US3 (P2)**: Phase 2 MVP then Phase 3 integration tests
- **US4 (P2)**: Phase 2 MVP then Phase 3 integration tests
- **US5 (P3)**: Phase 2 MVP then Phase 4 error scenario tests

### Within Each Phase (TDD Order)

1. Tests MUST be written and verified FAILING before
   implementation
2. Constants and exceptions before DTOs (DTOs use constants)
3. DTOs before messaging client (client uses DTOs)
4. Messaging client before notify entity (entity delegates to
   client)
5. Implementation commits separate from task tracking updates

### Parallel Opportunities

**Phase 1 — Tests (Red)**:

- T001 (test\_models.py) parallel with T002
  (test\_exceptions.py) — different files
- T003, T004, T005 — sequential in test\_messaging.py

**Phase 1 — Implementation (Green)**:

- T006 (api/const.py) parallel with T007
  (api/exceptions.py) — different files
- T008 (api/models.py) after T006 (references constants)
- T009 (api/messaging.py) after T006, T007, T008
- T010 (api/init) after T007, T008, T009

**Phase 2**:

- T011, T012 — sequential in test\_notify.py
- T013 (const.py) parallel with other Phase 2 tasks
- T014 (init) then T015 (notify.py) then T016 (conftest.py)

---

## Parallel Example: Phase 1

```text
# Launch test writing for Phase 1 (Red):
# Parallel pair:
Task: "DTO validation tests in tests/api/test_models.py"   T001
Task: "Exception tests in tests/api/test_exceptions.py"     T002

# Sequential in test_messaging.py:
Task: "resolve_conversation tests"                           T003
Task: "send_message tests"                                   T004
Task: "render_template + validation tests"                   T005

# Launch implementation for Phase 1 (Green):
# Parallel pair:
Task: "Messaging constants in api/const.py"                  T006
Task: "GuestyMessageError in api/exceptions.py"              T007

# Sequential chain:
Task: "Messaging DTOs in api/models.py"                      T008
Task: "GuestyMessagingClient in api/messaging.py"            T009
Task: "Updated exports in api/__init__.py"                   T010
```

---

## Implementation Strategy

### MVP First (Phase 1 + Phase 2)

1. Complete Phase 1: Messaging API Client (api/ layer) — PR 1
2. Complete Phase 2: HA Notify Platform (US1 + US2) — PR 2
3. **STOP and VALIDATE**: Send message via
   `notify.send_message`
4. Deploy/demo if ready — basic guest messaging works

### Incremental Delivery

1. Phase 1 — Messaging client ready (library-extractable)
2. Phase 2 — MVP: Send messages from HA (US1 + US2) ✅
3. Phase 3 — Channel selection + templates (US3 + US4) ✅
4. Phase 4 — Production-grade error handling (US5) ✅
5. Each phase adds capability without breaking previous phases

### Single Developer Strategy

1. Phase 1 tests (T001–T005) then impl (T006–T010) — PR 1
2. Phase 2 tests (T011–T012) then impl (T013–T016) — PR 2
3. Phase 3 tests (T017–T021) — PR 3
4. Phase 4 tests + validation (T022–T027) — PR 4

---

## Notes

- \[P] tasks = different files, no dependencies on incomplete
  tasks
- \[US#] label maps task to its user story for traceability
- TDD is mandatory: write failing tests before implementation
- Each phase is an independent PR with its own test coverage
- Commit after each task or logical group (tests separate from
  implementation)
- Task tracking updates (tasks.md) committed separately from
  code
- All new files require SPDX headers per constitution
- Use `git commit -s` with `Co-authored-by` trailer per
  AGENTS.md

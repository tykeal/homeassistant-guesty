<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Guesty Auth & Config Flow (MVP)

**Feature Branch**: `001-auth-config-flow`
**Created**: 2025-07-18
**Status**: Draft
**Input**: User description: "Guesty Auth & Config Flow (MVP)"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Initial Integration Setup (Priority: P1)

A Home Assistant user wants to connect their Guesty property management
account to Home Assistant so they can monitor and automate their rental
properties. The user navigates to Settings → Devices & Services → Add
Integration, searches for "Guesty," and is prompted to enter their
Guesty API credentials (client ID and client secret). The system
validates the credentials by attempting to authenticate with the Guesty
service. If successful, the integration is added and ready for use. If
the credentials are invalid, the user sees a clear error message
explaining what went wrong and can try again.

**Why this priority**: Without a working configuration flow, no other
integration features can function. This is the foundational entry point
for all Guesty functionality in Home Assistant.

**Independent Test**: Can be fully tested by adding the integration
through the HA UI with valid and invalid credentials. Delivers the
ability to establish a verified connection to the Guesty service.

**Acceptance Scenarios**:

1. **Given** the Guesty integration is not yet configured, **When** the
   user navigates to Add Integration and searches for "Guesty,"
   **Then** the Guesty integration appears in the search results.
2. **Given** the user is on the Guesty setup form, **When** the user
   enters a valid client ID and client secret and submits, **Then** the
   system validates the credentials and the integration is successfully
   added.
3. **Given** the user is on the Guesty setup form, **When** the user
   enters an invalid client ID or client secret and submits, **Then**
   the system displays a clear, user-friendly error message indicating
   the credentials are invalid and allows the user to correct them.
4. **Given** a Guesty integration instance already exists, **When** the
   user attempts to add a second instance with the same credentials,
   **Then** the system prevents duplicate configuration and informs the
   user that this account is already configured.

---

### User Story 2 - Seamless Token Persistence Across Restarts (Priority: P2)

A Home Assistant user has already configured the Guesty integration. When
Home Assistant restarts (due to updates, power events, or manual
restart), the integration resumes operation without requiring the user to
re-enter credentials or manually re-authenticate. The previously
acquired authentication token is preserved and reused if still valid,
avoiding unnecessary authentication requests against the Guesty service.

**Why this priority**: Home Assistant restarts frequently (updates,
configuration changes, hardware events). If the integration loses its
authentication state on every restart, it wastes limited authentication
requests and degrades the user experience.

**Independent Test**: Can be tested by configuring the integration,
restarting Home Assistant, and verifying the integration reconnects
without prompting for credentials or making a new authentication
request.

**Acceptance Scenarios**:

1. **Given** the Guesty integration is configured and authenticated,
   **When** Home Assistant restarts, **Then** the integration resumes
   using the stored token without making a new authentication request.
2. **Given** the stored token has expired during a Home Assistant
   restart, **When** Home Assistant starts up, **Then** the integration
   automatically acquires a new token without user intervention.
3. **Given** the stored token is corrupted or missing after a restart,
   **When** Home Assistant starts up, **Then** the integration acquires
   a new token gracefully without errors visible to the user.

---

### User Story 3 - Transparent Token Refresh (Priority: P2)

A Home Assistant user has the Guesty integration running. As the
authentication token approaches its 24-hour expiry, the integration
proactively refreshes the token before it expires, ensuring uninterrupted
service. If a request unexpectedly fails due to an expired token (e.g.,
clock drift or server-side revocation), the integration automatically
acquires a new token and retries the failed request, all transparently
to the user.

**Why this priority**: Token expiry is inevitable (24-hour lifetime).
Without automatic refresh, the integration would silently stop working
every 24 hours, requiring user intervention or causing missed
automations.

**Independent Test**: Can be tested by simulating token expiry (both
proactive near-expiry and reactive on authentication failure) and
verifying the integration continues to operate without interruption.

**Acceptance Scenarios**:

1. **Given** the current token is within a configurable buffer of its
   expiry time, **When** any request to the Guesty service is needed,
   **Then** the integration acquires a new token before making the
   request.
2. **Given** a valid token is in use, **When** a request to Guesty
   returns an authentication failure (indicating the token is no longer
   valid), **Then** the integration acquires a new token and retries the
   original request.
3. **Given** token refresh is needed, **When** multiple concurrent
   requests trigger refresh simultaneously, **Then** only one
   authentication request is made and all waiting requests use the
   newly acquired token.

---

### User Story 4 - Graceful Rate Limit Handling (Priority: P3)

A Home Assistant user has the Guesty integration running during a period
of heavy activity (e.g., multiple listing updates, calendar syncs). The
integration respects Guesty's rate limits (15 requests per second, 120
per minute, 5000 per hour) and handles rate limit responses gracefully
by waiting the appropriate time before retrying. The user experiences
brief delays rather than errors. Additionally, the integration enforces
the 5-token-request-per-24-hours limit to prevent lockout from the
authentication endpoint.

**Why this priority**: Rate limit violations can result in extended
lockouts from the Guesty API, potentially affecting all integrations
sharing the same Guesty account. Proper handling prevents service
disruptions but is not needed for basic setup functionality.

**Independent Test**: Can be tested by simulating rate limit responses
and verifying the integration backs off appropriately and eventually
succeeds without user intervention.

**Acceptance Scenarios**:

1. **Given** the integration receives a rate limit response from
   Guesty, **When** the response includes a retry-after indicator,
   **Then** the integration waits the specified duration before
   retrying the request.
2. **Given** the integration receives a rate limit response without a
   specific retry-after indicator, **When** retrying, **Then** the
   integration uses exponential backoff with randomized jitter to
   avoid thundering herd effects.
3. **Given** the integration has made 4 authentication requests in the
   current 24-hour window, **When** a 5th authentication request is
   needed, **Then** the integration issues a warning and tracks that it
   is at the limit.
4. **Given** the integration has reached the 5-request authentication
   limit, **When** a 6th authentication request would be needed,
   **Then** the integration delays the request until the 24-hour window
   resets rather than exceeding the limit.

---

### User Story 5 - Clear Error Communication (Priority: P3)

A Home Assistant user encounters an issue with the Guesty integration
(network failure, Guesty service outage, credential revocation). The
integration communicates the problem clearly through Home Assistant's
standard notification mechanisms, providing actionable guidance. Errors
are categorized so the user understands whether the problem is
temporary (retry later) or requires action (re-enter credentials).

**Why this priority**: Without clear error communication, users cannot
diagnose or resolve integration issues, leading to frustration and
support burden. However, this builds on top of the core connection
functionality.

**Independent Test**: Can be tested by simulating various failure
conditions (network down, invalid credentials, service unavailable) and
verifying appropriate error messages appear in the HA UI.

**Acceptance Scenarios**:

1. **Given** the Guesty service is unreachable, **When** the
   integration attempts to communicate with it, **Then** the user sees
   a notification indicating a connection problem with a suggestion to
   check network connectivity.
2. **Given** the user's credentials have been revoked on the Guesty
   side, **When** the integration fails to authenticate, **Then** the
   user is prompted to re-authenticate through the config flow.
3. **Given** any error occurs during integration operation, **When**
   the error is logged, **Then** sensitive information (tokens,
   credentials) is never included in log messages.

---

### Edge Cases

- What happens when the user's Guesty account has no API access
  enabled? The config flow should display a clear message indicating
  the credentials were rejected and suggest verifying API access is
  enabled in the Guesty dashboard.
- What happens when the Guesty API endpoint is unreachable during
  initial setup? The config flow should display a connection error
  with guidance to check network connectivity and try again later.
- What happens when the system clock is significantly out of sync?
  Token expiry calculations may be inaccurate; the integration should
  handle unexpected authentication failures by reactively refreshing.
- What happens when Home Assistant restarts multiple times in rapid
  succession? The integration must not exhaust the 5-token-request
  limit through repeated startup authentication attempts; it should
  reuse the persisted token.
- What happens when two integration instances (different Guesty
  accounts) are configured simultaneously? Each instance must maintain
  independent authentication state and rate limit tracking.
- What happens when the Guesty API returns an unexpected response
  format? The integration should raise a clear error rather than
  crashing or silently malfunctioning.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The integration MUST provide a standard Home Assistant
  configuration flow that prompts the user for a Guesty client ID and
  client secret.
- **FR-002**: The configuration flow MUST validate the provided
  credentials by acquiring an authentication token during setup, before
  accepting the configuration.
- **FR-003**: The configuration flow MUST display user-friendly,
  localized error messages for invalid credentials, connection failures,
  and unexpected errors.
- **FR-004**: The integration MUST prevent duplicate configuration of
  the same Guesty account (identified by client ID).
- **FR-005**: The integration MUST acquire authentication tokens using
  the OAuth 2.0 Client Credentials grant type with the appropriate
  scope for the Guesty open API.
- **FR-006**: The integration MUST cache authentication tokens in memory
  for the duration of their validity (24-hour lifetime) to minimize
  authentication requests.
- **FR-007**: The integration MUST persist authentication tokens and
  their metadata (expiry time, request timestamps) through Home
  Assistant's configuration entry storage so they survive restarts.
- **FR-008**: On startup, the integration MUST check if a persisted
  token is still valid before requesting a new one.
- **FR-009**: The integration MUST proactively refresh tokens before
  they expire, using a configurable safety buffer (default: 5 minutes
  before expiry).
- **FR-010**: The integration MUST reactively refresh tokens when an
  API request receives an authentication failure response, then retry
  the original request.
- **FR-011**: The integration MUST ensure that concurrent requests
  needing a token refresh result in only a single authentication
  request, with all waiters receiving the new token.
- **FR-012**: The integration MUST track the number of authentication
  requests made within each 24-hour rolling window and enforce the
  5-request limit per client ID.
- **FR-013**: The integration MUST respect Guesty API rate limits (15
  per second, 120 per minute, 5000 per hour) by monitoring rate limit
  response headers.
- **FR-014**: The integration MUST implement retry with exponential
  backoff and randomized jitter when receiving rate limit responses.
- **FR-015**: When a rate limit response includes a retry-after
  indicator, the integration MUST wait at least that duration before
  retrying.
- **FR-016**: The integration MUST translate all API errors into typed,
  descriptive error categories: authentication errors, rate limit
  errors, connection errors, and unexpected errors.
- **FR-017**: The integration MUST never include sensitive data (tokens,
  client secrets) in log messages or error reports.
- **FR-018**: All network operations MUST be asynchronous and MUST NOT
  block the Home Assistant event loop.
- **FR-019**: The integration MUST include a properly configured
  manifest declaring the integration domain, config flow support, and
  minimum Home Assistant version compatibility.
- **FR-020**: The integration MUST provide localized user-facing strings
  for the configuration flow (step titles, field labels, error
  messages).
- **FR-021**: The integration MUST support a re-authentication flow that
  allows the user to update credentials without removing and re-adding
  the integration.

### Key Entities

- **Integration Configuration**: Represents a single Guesty account
  connection. Contains the client ID, client secret, and user-provided
  display name. One configuration per Guesty account.
- **Authentication Token**: Represents an active session with the
  Guesty API. Contains the access token value, the time it was issued,
  the duration it remains valid, and the scope of access it grants.
  Associated with exactly one Integration Configuration.
- **Token Request Record**: Tracks authentication requests made within
  a rolling 24-hour window. Contains the timestamp of each request.
  Used to enforce the 5-request-per-24-hours limit. Associated with
  exactly one Integration Configuration.
- **Rate Limit State**: Tracks the current rate limit consumption
  across the three tiers (per-second, per-minute, per-hour). Updated
  from response headers on every API call. Associated with exactly one
  Integration Configuration.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can complete the Guesty integration setup in under
  2 minutes, from navigating to Add Integration to having a validated
  connection.
- **SC-002**: The integration survives 10 consecutive Home Assistant
  restarts without exhausting the 5-token-request authentication limit,
  by reusing persisted tokens.
- **SC-003**: Token refresh occurs transparently with zero user-visible
  interruptions during continuous 72-hour operation.
- **SC-004**: 100% of API rate limit responses are handled gracefully
  (retried with backoff) with zero unhandled errors surfaced to the
  user.
- **SC-005**: Invalid credential attempts during setup produce a clear,
  actionable error message within 5 seconds.
- **SC-006**: The integration never exceeds 5 authentication requests
  per 24-hour period per Guesty account under any operating condition.
- **SC-007**: All network operations complete without blocking the Home
  Assistant event loop (zero synchronous I/O calls).
- **SC-008**: Sensitive credentials and tokens never appear in any log
  output at any log level.
- **SC-009**: All source code achieves 100% docstring coverage and
  complete type annotation coverage with zero linting errors.
- **SC-010**: All external service interactions are fully testable
  without live API calls; test suite achieves 100% branch coverage of
  authentication and retry logic.

## Assumptions

- Users have an active Guesty account with API access enabled and have
  already generated their client ID and client secret from the Guesty
  developer dashboard.
- Users have stable internet connectivity from their Home Assistant
  instance to the Guesty API endpoints.
- The Guesty API token endpoint and rate limit behavior remain
  consistent with the documented specifications (24-hour token
  lifetime, 5 requests per 24 hours, 15/sec + 120/min + 5000/hour
  rate limits).
- This MVP feature covers authentication and API client foundation
  only; entity creation, data polling, and platform setup are out of
  scope and will be addressed in subsequent features.
- Only one Guesty account per integration instance is supported;
  multiple accounts require multiple integration instances.
- The Home Assistant configuration entry storage mechanism provides
  reliable persistence across restarts and updates.
- The minimum supported Home Assistant version will be determined
  during implementation planning based on the APIs used (assumed to be
  a recent stable release).
- The integration is installed as a custom component (via HACS or
  manual installation) rather than a core HA integration.

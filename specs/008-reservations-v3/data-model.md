<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Reservations v3 API Migration

**Feature Branch**: `008-reservations-v3` | **Date**: 2026-11-13

No entity, device or unique-id change. Everything below is either a
library-layer dataclass, an in-memory cache, a config entry option, or a
per-entry storage record.

## 1. API-layer models (`custom_components/guesty/api/models.py`)

### 1.1 `GuestyStaySegment` (new, frozen dataclass)

| Field | Type | Source (v3) | Notes |
| --- | --- | --- | --- |
| `listing_id` | `str` | `stay[].listingId` | `unitId ?? unitTypeId`; required |
| `parent_listing_id` | `str \| None` | `stay[].parentListingId` | optional |
| `check_in` | `datetime` (UTC) | `stay[].checkIn` | required |
| `check_out` | `datetime` (UTC) | `stay[].checkOut` | required |
| `check_in_local` | `str \| None` | `stay[].checkInDateLocalized` | |
| `check_out_local` | `str \| None` | `stay[].checkOutDateLocalized` | |
| `guests_count` | `int \| None` | `stay[].guestsCount` | |

A segment missing `listingId`, `checkIn` or `checkOut` is dropped with a
debug log. A reservation left with no usable segment is skipped with a
warning, matching today's required-field behaviour
(`api/models.py:630-741`).

### 1.2 `GuestyReservation` (existing, extended)

Existing fields (`api/models.py:592-619`) keep their names, types and
meanings. Two changes:

| Field | Change |
| --- | --- |
| `stay` | **new**: `tuple[GuestyStaySegment, ...]`, default `()`; v3 only |
| `money` | unchanged type; always `None` on the v3 path (R9) |

`listing_id` stays a required `str`. On the v3 path it is populated from
the **first** segment at parse time so that the model remains valid
standalone; the coordinator never relies on it, attributing through
`stay` instead.

### 1.3 Legacy → v3 field mapping

Direct equivalents:

| Model field | Legacy source | v3 source |
| --- | --- | --- |
| `id` | `_id` | `reservationId` |
| `listing_id` | `listingId` | `stay[0].listingId` |
| `status` | `status` | `status`, mapped (§1.4) |
| `check_in` | `checkIn` | `checkIn` |
| `check_out` | `checkOut` | `checkOut` |
| `check_in_local` | `checkInDateLocalized` | same |
| `check_out_local` | `checkOutDateLocalized` | same |
| `confirmation_code` | `confirmationCode` | same |
| `source` | `source` | `source` |
| `custom_fields` | `customFields` | `customFields`, absent if unset |

Everything else needs a decision:

- **`planned_arrival`, `planned_departure`** (legacy `plannedArrival`,
  `plannedDeparture`): assumed present in v3 (OQ-003). If absent they
  publish `None` on the v3 path and the loss is declared a breaking
  change under NFR-004.
- **`nights_count`** (legacy `nightsCount`): assumed absent in v3, so it
  is derived as `check_out_local − check_in_local` in whole days, and is
  `None` when the dates cannot be parsed.
- **`guests_count`** (legacy `guestsCount`): taken from
  `stay[].guestsCount` for the interval being projected.
- **`note`** (legacy `note`): assumed to be `notes.other`
  (**[UNVERIFIED]**, R11); `None` when absent. The note write echoes
  every note field back, so sibling fields such as `notes.guest` cannot
  be lost.
- **`guest`** (legacy embedded `guest`): not present in v3; built from
  the guest cache keyed by `guestId`. Guest attributes publish `None`
  when no record is available.
- **`money`** (legacy `money.*`): not present in v3 and deliberately not
  re-sourced; the three financial sensors deprecate (R9).
- **`stay`**: new in v3; the legacy path leaves it empty.
- **`mtl`, `unitId`, `unitTypeId`, `lastUpdatedAt`**: present in legacy,
  absent in v3, and unused by this integration today, so there is no
  impact.

New in v3 and consumed: `guestId` (drives the guest lookup),
`stay[].parentListingId` (FR-012).

### 1.4 Status mapping (parse time)

| v3 status | Model status | Published |
| --- | --- | --- |
| `confirmed` | `confirmed` | yes |
| `checked_in` | `checked_in` | yes |
| `checked_out` | `checked_out` | yes |
| `completed` | `checked_out` | yes (FR-023) |
| `canceled` / `cancelled` | `canceled` | yes |
| `alteration` | `alteration` | **no**, dropped by the coordinator (FR-024) |
| `inquiry`, `reserved`, others | verbatim | yes, with an INFO log (FR-026) |

Comparison is case-insensitive and both spellings of cancelled are
accepted. Diagnostics count **raw** statuses, before mapping.

### 1.5 Status filter translation (FR-003a)

| Configured status | Requested in `filter[status][in]` |
| --- | --- |
| `confirmed` | `confirmed` |
| `checked_in` | `checked_in` |
| `checked_out` | `checked_out`, `completed` |
| `canceled` | `canceled` |
| anything else | itself |

The configured set the user sees (`api/const.py:116-124`) is unchanged.

### 1.6 `ReservationFetchResult` (new, frozen dataclass)

Returned by both sources so the coordinator is source-agnostic.

| Field | Type | Meaning |
| --- | --- | --- |
| `reservations` | `tuple[GuestyReservation, ...]` | validated, mapped results |
| `pages` | `int` | pages fetched (FR-042) |
| `status_counts` | `Mapping[str, int]` | raw status histogram |
| `segment_histogram` | `Mapping[int, int]` | segments per reservation |

## 2. Guest cache (`custom_components/guesty/api/guest_cache.py`)

### 2.1 `GuestCacheEntry` (new, frozen dataclass)

| Field | Type | Meaning |
| --- | --- | --- |
| `guest` | `GuestyGuest \| None` | `None` records a confirmed not-found |
| `fetched_at` | `float` | monotonic clock reading |
| `failed_at` | `float \| None` | last failed attempt, for backoff |

### 2.2 `GuestCache` behaviour

| Constant | Value | Purpose |
| --- | --- | --- |
| `GUEST_CACHE_TTL_SECONDS` | `86400` | hit freshness (FR-018) |
| `GUEST_CACHE_MISS_TTL_SECONDS` | `3600` | not-found freshness |
| `GUEST_CACHE_MAX_ENTRIES` | `512` | LRU bound |
| `MAX_GUEST_LOOKUPS_PER_REFRESH` | `50` | burst cap (NFR-001) |
| `GUEST_LOOKUP_CONCURRENCY` | `4` | bounded parallelism |

- **Hit within TTL**: served from the cache, no request.
- **Expired hit**: one lookup; on success the entry is replaced, on
  failure the old value is kept and the failure is logged without PII
  (FR-020).
- **Miss within the miss TTL**: no guest attributes and no request.
- **Unknown guest id**: one lookup.
- **Over the burst cap**: the oldest entries are refreshed first; the
  rest keep serving their previous values and are retried on the next
  refresh.
- **Reservation's `guest_id` changed**: the new id is a separate key, so
  the previous guest's values are never published (FR-019).
- **No `guest_id` on the reservation**: no lookup, and the guest
  attributes are `None`.

The cache lives on the API client instance, so it is discarded on reload
and never persisted — guest PII never reaches disk.

## 3. Coordinator data (`custom_components/guesty/coordinator.py`)

`ReservationsCoordinator.data` keeps its type,
`dict[str, list[GuestyReservation]]`, so the sensor layer is unchanged.
The values are now **per-listing projections**.

### 3.1 Projection rules

A single-segment reservation projects to itself, field for field. With
more than one segment, the projection for a tracked listing substitutes:

- `listing_id` — the tracked listing this projection is for.
- `check_in`, `check_out` — the selected interval for that listing.
  Segments in the same listing stay **disjoint**; they are never
  collapsed into one span, because an A → B → A stay would then cover
  the guest's time in B. The selected interval is the one containing the
  refresh instant, else the earliest future interval, else the latest
  past one.
- `check_in_local`, `check_out_local` — the selected interval.
- `guests_count` — the selected interval's segment.
- `nights_count` — derived from the selected interval's localized
  dates.
- `status` — temporal, per FR-011a, and only for `confirmed` and
  `checked_in`, evaluated against the selected interval.

`id`, `confirmation_code`, `source`, `guest` and `custom_fields` are
always the reservation's own values, and `stay` is always empty on a
projection.

### 3.2 Attribution outcomes

- **Segment listing id known**: attributed to it.
- **Segment listing id unknown, parent known**: attributed to the
  parent (FR-012).
- **Segment wholly unknown while others resolve**: ignored at debug
  level; the reservation is still published (FR-013).
- **No segment attributable**: skipped with a warning (FR-013).
- **No segments at all**: skipped with a warning.
- **Listing excluded by Feature 007**: never in `known_ids`, so nothing
  is published (FR-014).
- **Reservation status `alteration`**: dropped with a debug log
  (FR-024).
- **Reservation outside the instant window**: dropped by the trim
  (FR-002a).
- **Identity ambiguous**: the whole refresh aborts and nothing is
  published (FR-032).

## 4. Identity baseline (`custom_components/guesty/identity.py`)

Persisted with `homeassistant.helpers.storage.Store`, key
`guesty.{entry_id}.reservations_identity`, version 1.

| Field | Type | Meaning |
| --- | --- | --- |
| `version` | `int` | storage schema version |
| `path` | `"v3" \| "legacy"` | which path produced the baseline (FR-031b) |
| `continuity` | `verified`, `unconfirmed` or `not_applicable` | cross-path |
| `updated` | `str` | ISO timestamp of the last successful refresh |
| `codes` | `dict[str, str]` | confirmation code → reservation id |

| Rule | Behaviour |
| --- | --- |
| Size bound | at most 5,000 codes, earliest check-out evicted first |
| Write | after each successful, unambiguous refresh, replacing the map |
| Read | once per setup, into the coordinator |
| Missing | writes fail closed; reservations still publish (FR-031c) |
| Write permitted | see the four conditions below |
| Missing on upgrade | a one-time legacy bootstrap sweep seeds the map |
| Bootstrap uninformative | `continuity` stays `unconfirmed`; writes refused |
| Fresh install | `continuity` is `not_applicable`; writes permitted |
| Mismatch | refresh aborts; last-known-good retained; writes blocked (FR-032) |
| Removal | deleted with the config entry |

A write is permitted only when the baseline is usable, its `continuity`
is not `unconfirmed`, the last refresh was not aborted as ambiguous, and
the target reservation carries a confirmation code.

Confirmation code **values** are persisted (they are the key of the
check) but never logged (FR-021, NFR-009).

## 5. Configuration

| Property | Value |
| --- | --- |
| Key | `reservations_api` |
| Location | `entry.options` |
| Values | `"v3"`, `"legacy"` |
| Default | `"v3"` (FR-037) |
| Consumers | `GuestyApiClient`, `GuestyActionsClient` |

Existing entries carry no value, so the default applies on upgrade with
no migration step (FR-037). Changing it reloads the entry through the
existing options listener and changes no entity (FR-038, SC-008).

## 6. New constants (`custom_components/guesty/api/const.py`)

| Constant | Value |
| --- | --- |
| `RESERVATIONS_V3_SEARCH_ENDPOINT` | `/reservations-v3/search` |
| `RESERVATIONS_V3_ENDPOINT` | `/reservations-v3` |
| `RESERVATION_NOTES_PATH` | `/reservations-v3/{reservation_id}/notes` |
| `RESERVATION_STATUS_PATH` | `/reservations-v3/{reservation_id}/status` |
| `GUESTS_CRUD_PATH` | `/guests-crud/{guest_id}` |
| `GUEST_FIELDS` | `("fullName", "phone", "email")` |
| `MAX_V3_RESERVATION_PAGES` | `200` |
| `V3_DATE_PAD_DAYS` | `1` |
| `V3_STATUS_MAP` | see §1.4 |
| `V3_STATUS_FILTER_MAP` | see §1.5 |
| `HOLD_STATUSES` | `frozenset({"alteration"})` |
| `MAX_LISTING_IDS_PER_SEARCH` | `50` — reserved for OQ-008; unused |

`RESERVATIONS_ENDPOINT`, `RESERVATIONS_FIELDS`, `ACTIONABLE_STATUSES`,
`VALID_RESERVATION_STATUSES` and `NOTE_SEPARATOR` stay as they are; the
first two are used only by the legacy source and are deleted with it
after 2027-03-01.

## 7. New exception (`custom_components/guesty/api/exceptions.py`)

`GuestyFilterRejectedError`, deriving from `GuestyResponseError` and
carrying the endpoint, the HTTP status and a truncated response excerpt.

Raised on HTTP 400 from the v3 search. The coordinator converts it to
`UpdateFailed` with the detail intact, and never retries without filters
(FR-008).

## 8. Entity surface

Unchanged. For completeness, what exists today and continues to exist:

- `reservation_status` on the listing device — unchanged; the state
  vocabulary and every attribute are preserved.
- `reservation_total`, `reservation_balance` and `reservation_currency`,
  all diagnostic entities on the listing device — **deprecated**, and
  unavailable on the v3 path.

No entity is added, removed, renamed or re-created by this feature
(NFR-003).

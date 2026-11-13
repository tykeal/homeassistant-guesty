<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Reservations v3 API Migration

**Branch**: `008-reservations-v3` | **Date**: 2026-11-13 | **Spec**:
[spec.md](./spec.md)
**Input**: Feature specification from
`/specs/008-reservations-v3/spec.md`
**Tracking Issue**: [#223][issue-223] | **Resolves**: [#204][issue-204]

## Summary

Move reservation retrieval and the two reservation write actions off the
Guesty legacy Open API resources, which retire on **2027-03-01**, and onto
the Reservations v3 resources, without changing a single entity id, state
value or attribute name for existing users.

Retrieval moves from `GET /v1/reservations`
(`custom_components/guesty/api/const.py:88-89`) to
`GET /v1/reservations-v3/search`. The write actions move from the
retiring catch-all `PUT /v1/reservations/{id}`
(`custom_components/guesty/api/actions.py:84-137`,
`custom_components/guesty/api/actions.py:364-369`) to
`PUT /v1/reservations-v3/{id}/notes` and
`PUT /v1/reservations-v3/{id}/status`.

Four consequences drive the rest of the design:

1. v3 embeds no guest object, so guest name, phone and email are
   re-sourced from the `/guests` family through a bounded, deduplicated,
   TTL-based cache.
2. v3 embeds no `money`, so the three financial sensors are
   **deprecated** (owner decision, 2026-11-13; see
   [Financial sensors](#financial-sensor-deprecation)). The guest folio
   is **not** integrated.
3. v3 carries no top-level listing id, so attribution is derived from
   `stay[]` segments, with per-listing projections that keep the sensor
   layer's inputs shaped exactly as they are today.
4. Nothing about v3 can be verified without a live Guesty account, which
   this project does not have. The design therefore fails safe
   everywhere: loud errors instead of empty results, aborted refreshes
   instead of ambiguous publishes, and refused writes instead of writes
   to an unconfirmed identity.

A `reservations_api` configuration option (`v3` | `legacy`, default `v3`)
keeps the legacy code path alive, verbatim, as a rollback target until it
is deleted after 2027-03-01.

## Technical Context

**Language/Version**: Python ≥3.14.2 (mypy target 3.14, ruff target py314)
**Primary Dependencies**: homeassistant ≥2026.4.0, httpx ≥0.28,
voluptuous (via HA)
**Storage**: HA config entry options (`entry.options`) for the toggle;
`homeassistant.helpers.storage.Store`, scoped per config entry, for the
identity baseline; in-memory TTL cache for guest records
**Testing**: pytest with pytest-asyncio (`asyncio_mode=auto`), respx,
pytest-homeassistant-custom-component, freezegun-style time control via
`freeze_time` fixtures; dictionary fixtures only (no live payloads)
**Target Platform**: Home Assistant custom component (HACS)
**Project Type**: HA custom integration (hub, cloud_polling)
**Performance Goals**: warm-cache request count per refresh ≤ the legacy
path's (NFR-001, SC-003); no measurable event-loop impact (NFR-006)
**Constraints**: `api/` stays library-extractable (zero HA imports,
NFR-007); 100% docstring coverage (interrogate); entity and attribute
compatibility (NFR-003, NFR-004); no guest PII in logs at any level
(NFR-009); every v3 behaviour is an unverified assumption (FR-041)
**Scale/Scope**: accounts with 50–100+ listings and hundreds of
reservations in the configured window; 15-minute poll (minimum 5)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | ✅ | Notes |
| --- | --- | --- | --- |
| I | Quality & Testing | ✅ | TDD per phase; 100% docstrings; typed |
| II | API Client | ✅ | v3 source in `api/`; no HA imports |
| III | Atomic Commits | ✅ | one phase deliverable per commit |
| IV | Licensing | ✅ | SPDX headers on every new file |
| V | Pre-Commit | ✅ | all hooks; no `--no-verify` |
| VI | Co-Authorship & DCO | ✅ | `-s` plus `Co-authored-by` |
| VII | UX Consistency | ⚠️ | financial sensors deprecate; see below |
| VIII | Performance | ✅ | async; cached; bounded lookups |
| IX | Phased Dev | ✅ | 8 phases, each independently testable |
| X | Security | ✅ | no PII in logs; existing credentials |

**Gate Result**: ✅ PASS with one recorded deviation (principle VII),
tracked in [Complexity Tracking](#complexity-tracking).

**Post-design re-check**: ✅ unchanged. The v3 source, mapping and
validation live in `api/` with zero HA imports. The identity baseline and
the deprecation repair issue are HA-layer concerns and stay there.

## Project Structure

### Documentation (this feature)

```text
specs/008-reservations-v3/
├── plan.md              # This file
├── research.md          # Phase 0: decisions and rationale
├── data-model.md        # Phase 1: entities and field mapping
├── quickstart.md        # Phase 1: developer orientation
├── contracts/           # Phase 1: consumed API contracts
│   ├── guesty-reservations-v3-search.md
│   ├── guesty-reservation-writes-v3.md
│   ├── guesty-guest-lookup.md
│   └── options-flow-reservations-api.md
├── checklists/
│   └── requirements.md  # (existing)
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT here)
```

### Source Code (repository root)

```text
custom_components/guesty/
├── api/
│   ├── __init__.py              # (modify) export v3 types
│   ├── client.py                # (modify) delegate to a source
│   ├── const.py                 # (modify) v3 paths, caps, maps
│   ├── exceptions.py            # (modify) filter-rejected error
│   ├── models.py                # (modify) stay segments, v3 parse
│   ├── actions.py               # (modify) v3 note/status writes
│   ├── guest_cache.py           # (new) TTL + LRU guest cache
│   ├── reservations_legacy.py   # (new) verbatim legacy source
│   └── reservations_v3.py       # (new) v3 search source
├── actions.py                   # (modify) identity-guard writes
├── config_flow.py               # (modify) reservations_api option
├── const.py                     # (modify) option key and default
├── coordinator.py               # (modify) attribution, guard, diag
├── identity.py                  # (new) persisted identity baseline
├── sensor.py                    # (modify) status map, deprecation
├── strings.json                 # (modify) option + repair strings
├── translations/en.json         # (modify) same keys
└── __init__.py                  # (modify) wiring, repair issue

tests/
├── conftest.py                  # (modify) v3 dict fixtures
├── api/
│   ├── test_client.py           # (modify) source delegation
│   ├── test_models.py           # (modify) v3 mapping
│   ├── test_reservations_v3.py  # (new) search, paging, guards
│   ├── test_guest_cache.py      # (new) TTL, LRU, failures
│   └── test_actions.py          # (modify) v3 write paths
├── test_coordinator.py          # (modify) attribution, trim
├── test_identity.py             # (new) baseline and guard
├── test_reservation_sensor.py   # (modify) statuses, segments
├── test_reservations_parity.py  # (new) legacy/v3 differential
└── test_config_flow.py          # (modify) toggle
```

**Structure Decision**: the existing layout is kept. Reservation
retrieval gains a *strategy* seam in `api/`: `reservations_legacy.py`
holds today's code moved verbatim, `reservations_v3.py` holds the new
path, and `GuestyApiClient.get_reservations()` delegates to whichever the
config entry selects. Attribution and the identity guard are Home
Assistant concerns and live in `coordinator.py` and the new
`identity.py`. The sensor layer keeps consuming `GuestyReservation`
objects whose shape is unchanged, which is how NFR-003 and NFR-004 are
met structurally rather than by discipline alone.

## Technical Approach

### Coexistence of the two paths

`GuestyApiClient` gains a `reservations_api: Literal["v3", "legacy"]`
constructor argument supplied from `entry.options`. It selects one of two
source objects that expose an identical coroutine:

```text
ReservationSource.fetch(past_days, future_days, statuses)
    -> ReservationFetchResult
```

`ReservationFetchResult` carries the reservations plus the diagnostics
counters FR-042 requires. The legacy source wraps today's
`get_reservations()` body (`api/client.py:227-390`,
`api/client.py:621-648`) unchanged, so FR-040's "behaviourally identical"
requirement is satisfied by construction and the existing legacy tests
keep passing against it. `GuestyActionsClient` reads the same setting
from the client it already holds, so both read and write paths flip
together (FR-036, contracts/options-flow-reservations-api.md).

Changing the option goes through the existing options flow, but the
existing update listener is **not** sufficient: it reloads only when the
listing selection changed and returns early when the polling intervals
are unchanged (`__init__.py:723-770`). Because the reservation source is
selected in the client constructor, a changed `reservations_api` would
otherwise be persisted without switching the running client, making
rollback silently ineffective. The listener must therefore track the
option alongside `_prev_selected` and `_prev_intervals` and reload the
entry when it changes; a regression test asserts that flipping only this
option reloads.

Entity unique ids are derived from `entry.unique_id`, the listing id and
the sensor key (`sensor.py:748-750`) and are untouched by the toggle, so
a reload produces zero entity-registry changes (FR-038, SC-008).

Deleting the legacy path after 2027-03-01 means deleting
`api/reservations_legacy.py`, its tests, the `legacy` option value and
the two-branch selection — a single, reviewable removal (FR-040).

### v3 request construction

One search query per refresh (resolving OQ-005):

| Parameter | Value |
| --- | --- |
| `filter[checkOut][gte]` | `D_start` = date(now − past_days) − 1 d |
| `filter[checkIn][lte]` | `D_end` = date(now + future_days) + 1 d |
| `filter[status][in]` | translated status set, comma-joined, sorted |
| `sort` | `_id` |
| `limit` | `100` |
| `skip` | `0, 100, 200, …` |

A check-out lower bound combined with a check-in upper bound is an
*overlap* test: it returns every reservation whose stay intersects the
window, including a stay that began 200 days before the window and is
still in progress. That is exactly the intent of today's two queries —
the date-and-status window plus the unbounded `checked_in` sweep
(`api/client.py:277-287`) — expressed without sweeping account history
(FR-004, SC-005). The unbounded second query is therefore **not** ported.
Whether both filters can be combined and are ANDed is
**[UNVERIFIED]**; the fallback, if a live account rejects it, is two v3
queries (the window query plus a `filter[status][in]=checked_in` query
with only `filter[checkOut][gte]=today`), merged by `reservationId`. The
fallback is recorded in research.md R3 and on the pre-release checklist.

No `fields` projection is sent (FR-007). No server-side listing filter is
sent (FR-015): the documented first-segment-only matching would silently
drop a stay that begins in an untracked listing and moves into a tracked
one, and nothing client-side could recover or even name it.

**Listing-filter chunking is designed but deliberately not
implemented.** If OQ-008 is ever decided in favour of trading coverage
for request budget, the chunking rule is specified in
contracts/guesty-reservations-v3-search.md: group ids into families (a
multi-unit parent with its sub-units; a tracked child with its parent
unit type; a single-unit listing alone), pack whole families into chunks
of at most 50 ids so that a first segment's `unitId` and `unitTypeId`
always land in the same chunk, run the full page guard per chunk, and
merge by `reservationId`, failing the refresh on a conflicting duplicate.
Adopting it requires amending FR-015 first.

### Pagination, page validation and failure modes

Because v3 returns no total count, the only defence against a silently
truncated result is per-page validation (FR-005, FR-006). A page is
accepted only if all of the following hold; otherwise the refresh fails,
last-known-good data is retained, and nothing is published:

1. the body is an object with a `results` list and a `pagination` object
   whose `hasMore` is a boolean;
2. `pagination.skip`, when present, equals the requested `skip`, and
   `len(results) <= limit`;
3. `hasMore` is not true for an empty page;
4. `hasMore` is not true for a short page;
5. every item has a non-empty string `reservationId`;
6. no `reservationId` repeats across pages;
7. `reservationId` is strictly ascending across the whole sequence;
8. `hasMore` is not still true after `MAX_V3_RESERVATION_PAGES = 200`
   pages (20,000 reservations in-window);
9. no page request fails after the existing 429 and backoff retries.

Checks 6 and 7 are what a `sort=_id` ascending cursor buys: a record
created mid-search sorts after the cursor and cannot disturb earlier
pages, while a record moving behind the cursor surfaces as a duplicate or
an order violation. A record *leaving* the set behind the cursor is
undetectable by any paging check; the integration never deletes a
reservation on absence alone — a listing whose reservations vanish simply
reports `no_reservation` on the next refresh, exactly as today — so the
bridge project's confirmed-absence machinery is not needed here.

HTTP 400 is treated as a hard failure (FR-008). A new
`GuestyFilterRejectedError` carries the endpoint and a truncated response
excerpt, and the coordinator surfaces it through `UpdateFailed` so the
user sees *why*. The request is never retried with filters stripped: a
silently unfiltered fetch is precisely the #204 failure mode this
migration exists to end.

### Client-side date trim (FR-002a)

The server filter compares listing-localized `YYYY-MM-DD` values while
the configured window is instant-based, so the request is padded by one
day on each side to stay a **superset**. A new instant-based trim then
removes padded boundary results before publication:

```text
keep reservation R  ⇔  R.check_out > now − past_days
                   and R.check_in  < now + future_days
```

In-progress stays satisfy both terms by definition, so the trim can never
discard one (FR-002a, FR-004). No such trim exists today — neither
`get_reservations()` nor `ReservationsCoordinator` re-checks the window
(`api/client.py:227-390`, `coordinator.py:234-267`) — which is part of
why #204 went unnoticed. The trim applies on **both** paths, so the
legacy path also gains a correct window; this is the one deliberate
behavioural improvement to the legacy path, and it narrows rather than
widens what is published, so it cannot resurrect a dropped reservation.

### Listing attribution from `stay[]`

`GuestyReservation` gains `stay: tuple[GuestyStaySegment, ...]`. The
coordinator attributes a reservation to every tracked listing reachable
from its segments (FR-009, FR-010):

- a segment is attributed to `segment.listing_id` when that id is known;
- otherwise to `segment.parent_listing_id` when that id is known
  (FR-012, the multi-unit sub-unit and unassigned-parent cases);
- otherwise it is ignored with a debug log.

A reservation with at least one attributable segment is published; one
with none is skipped with a warning, preserving Feature 003 FR-017 and
today's behaviour (`coordinator.py:252-258`) (FR-013). Listing selection
and tag filtering stay entirely on the listing side (FR-014): the
coordinator intersects with `listings_coordinator.data`, which Feature
007 already narrows, so a filtered-out listing simply never appears in
`known_ids`.

**Per-listing projection.** For each (reservation, tracked listing) pair
the coordinator emits a projected `GuestyReservation` whose `id`,
`confirmation_code`, `source`, guest attributes and custom fields are the
reservation's, but whose `listing_id`, `check_in`, `check_out`,
localized dates, `guests_count` and `nights_count` come from that
listing's segment when the reservation has more than one segment
(FR-011). A single-segment reservation projects to itself, byte for byte.
When one reservation has several segments in the *same* tracked
listing, the segments are kept as **disjoint intervals**; they are not
merged into one continuous span. Merging would be wrong for an
A → B → A stay: the merged span would cover the guest's time in B and
the temporal rule would report A as `checked_in` while the guest is
elsewhere. One projection per listing is still emitted — no per-segment
entity is created, which is explicitly out of scope in the spec — and
the interval it represents is chosen as: the interval containing the
refresh instant, else the earliest interval still in the future, else
the latest past interval. An A → B → A fixture covers this.

Projection is what preserves NFR-003 and NFR-004 cheaply: `sensor.py`
keeps consuming a list of `GuestyReservation` per listing and keeps
emitting the same attribute keys (`sensor.py:384-424`). The reservation
`id` is **not** rewritten by projection, so reservation custom-field
writes stay keyed correctly.

**Segment-aware state (FR-011a).** For a multi-segment reservation whose
reservation-level status is `confirmed` or `checked_in`, the projection's
status is derived from the selected interval for that listing against the
refresh instant: before the segment's check-in it is `confirmed`
(surfaced as `awaiting_checkin`); between check-in and check-out a
reservation-level `checked_in` stays `checked_in`; after check-out it is
`checked_out`. Any other reservation-level status — `canceled`,
`checked_out`, `completed`, `alteration`, unrecognized — is carried to
every touched listing unchanged, so a canceled multi-segment reservation
can never surface as `awaiting_checkin` (spec edge case, FR-011a).
Single-segment reservations keep today's behaviour exactly.

Because an upcoming segment projects to `confirmed` rather than
`checked_in`, the existing priority order in `_select_reservation()`
(`sensor.py:305-336`) already prevents a future segment from displacing a
listing's current occupant; no change to the priority table is needed.

### Status handling

Mapping happens in `api/models.py` at parse time, so the sensor's state
vocabulary is untouched (FR-025):

| v3 status | Mapped to | Published? |
| --- | --- | --- |
| `confirmed` | `confirmed` → `awaiting_checkin` | yes |
| `checked_in` | `checked_in` | yes |
| `checked_out` | `checked_out` | yes |
| `completed` | `checked_out` (FR-023) | yes |
| `canceled` / `cancelled` | `canceled` | yes |
| `alteration` | `alteration` | **no** (FR-024) |
| anything else | verbatim, INFO log (FR-026) | yes |

`alteration` reservations are dropped by the coordinator with a debug log
and a diagnostics counter, so they can neither be selected as a state nor
displace a published reservation for the same listing (FR-024).

The configured status set is translated before it is sent (FR-003a):
`checked_out` additionally requests `completed`, otherwise the server
would filter out completed stays before the mapping above could run. The
user-visible configured set and the sensor vocabulary are unchanged.

### Guest lookup and cache

v3 carries only `guestId`, so guest attributes come from the `/guests`
family (FR-016). The chosen resource is
`GET /v1/guests-crud/{guestId}?fields=fullName phone email`
(contracts/guesty-guest-lookup.md), with
`GET /v1/guests/{guestId}` as the documented fallback if live
verification rejects it (OQ-004, **[UNVERIFIED]**). Values are published
under today's attribute names `guest_name`, `guest_phone`, `guest_email`
(FR-017, `sensor.py:384-424`).

`api/guest_cache.py` holds an HA-free cache:

- **Key**: `guest_id` only, so a cached record can never be published
  under another id (FR-019).
- **Hit TTL**: `GUEST_CACHE_TTL_SECONDS = 86400`. v3 has no
  `lastUpdatedAt`, so elapsed time is the only refresh trigger (FR-018).
- **Miss TTL**: `GUEST_CACHE_MISS_TTL_SECONDS = 3600`, so a deleted
  guest does not retrigger a lookup every refresh.
- **Bound**: `GUEST_CACHE_MAX_ENTRIES = 512`, LRU, so memory is bounded
  regardless of account size (constitution VIII).
- **Per refresh**: one lookup per distinct guest (FR-018, SC-010).
- **Burst cap**: `MAX_GUEST_LOOKUPS_PER_REFRESH = 50`, oldest entries
  first, so a cold start spreads over several refreshes instead of
  exhausting the rate budget (NFR-001, NFR-002).
- **Concurrency**: `GUEST_LOOKUP_CONCURRENCY = 4` — bounded
  parallelism, still fully async (NFR-006).
- **Failure**: keep the previous value for the same id, log without PII,
  never fail the refresh (FR-020, NFR-005, SC-009).

Lookups are issued **only** for reservations attributed to a tracked
listing, so reservations the account returns for untracked listings cost
nothing beyond the page they arrive on (NFR-001).

**Request volume.** Legacy costs *two* paginated sequences per refresh
(`api/client.py:277-287`), at least two requests. The v3 path costs one
sequence, so with a warm cache it is strictly ≤ legacy for the same
account (NFR-001, SC-003). Cold-cache excess is at most one request per
distinct guest needing refresh, capped at 50 per refresh; with a
24-hour TTL and a 15-minute poll, steady state is at most one lookup per
guest per day, spread across 96 refreshes.

**Privacy.** Guest values never reach a log at any level; only `guest_id`
and counts are loggable (FR-021, NFR-009). A test asserts no name, phone,
email or confirmation code appears in captured records at DEBUG.

### Financial sensor deprecation

**Owner decision, 2026-11-13: Option B — deprecate.** The three
diagnostic sensors `reservation_total`, `reservation_balance` and
`reservation_currency` (`sensor.py:150-184`, `sensor.py:740-830`) are
deprecated. `GET /v1/guest-folio/overview` is **not** integrated, and no
folio code, request budget or failure path is introduced.

On the `legacy` path nothing changes: `money` is embedded and the
sensors report values. On the `v3` path `GuestyReservation.money` is
always `None`, so the existing `available` property
(`sensor.py:772-800`) returns `False` and the entities report
unavailable — exactly the behaviour they already have today when the
values are missing.

The entities are **not** removed in this release. Removing them now would
delete entity-registry rows that a user could still need on a rollback to
the legacy path, and would make the toggle asymmetric.

User-facing notice:

- a Home Assistant **repair issue** (`is_fixable=False`, severity
  `warning`, translation key `financial_sensors_deprecated`) is raised on
  setup when the v3 path is active **and** at least one of the three
  entities already exists in the entity registry, so only installations
  that could actually be affected are notified;
- the issue text names the three entities, states that Guesty no longer
  returns reservation financial data, and states the removal date;
- one `WARNING` log line per setup with the same content;
- release notes record this as a **breaking change** under NFR-004 rather
  than a silent drop.

Removal timeline: the entities and their descriptions are removed in the
same release that deletes the legacy reservation path, which is the first
release after **2027-03-01**. Until then they remain registered and
unavailable on the v3 path, and functional on the legacy path. The repair
issue states that date.

This is the one deviation from constitution principle VII, recorded in
[Complexity Tracking](#complexity-tracking).

### Identity guard

The guard (FR-031 to FR-035) answers one unverifiable question — is a
legacy `_id` the same string as a v3 `reservationId`? — by refusing to
proceed when the evidence says otherwise. Reservation custom-field writes
are keyed by reservation id and at least one user publishes door-lock
codes through them, so acting on a re-keyed identity is the worst outcome
this feature could produce.

- **Baseline**: a map from `confirmationCode` to reservation id, plus the
  path that produced it, persisted through
  `homeassistant.helpers.storage.Store` scoped to the config entry
  (`guesty.{entry_id}.reservations_identity`, version 1) in the new
  `identity.py`. Store is used rather than writing into `entry.data`
  because an `async_update_entry` call fires the entry's update listener
  and would reload the integration on every refresh; Store satisfies
  FR-031a's requirement that the baseline survive a restart and a reload
  without that side effect.
- **Establishment**: rewritten wholesale after each successful,
  unambiguous refresh, from whichever path produced it (FR-031b). Entries
  are capped at 5,000 codes, oldest check-out first, so storage stays
  bounded.
- **Check**: before publishing a refresh, every reservation carrying a
  confirmation code is compared with the baseline. Any code whose mapping
  *differs* makes the identity ambiguous — including the swap case where
  two codes exchange two still-present ids (FR-032). The refresh aborts
  via `UpdateFailed`, last-known-good data is retained, no write is
  permitted, and one error names both ids, the code's presence (never its
  value), and the rollback instruction.
- **Cost**: zero additional requests; the comparison runs on data already
  fetched (FR-034).
- **Fail closed for writes**: a runtime flag records whether a baseline
  exists. Until it does, reservations are published but every reservation
  write aborts with an explanatory error (FR-031c, SC-013). A reservation
  with no confirmation code is publishable but is never a valid write
  target (FR-033).
- **Observability**: the first successful v3 refresh logs, at INFO, how
  many previously known identifiers matched (FR-035).

The guard is enforced in the HA service layer, which can see coordinator
state, so `api/` keeps zero HA imports (NFR-007). Every reservation-
targeted write must pass through it:

- `custom_components/guesty/actions.py:183-220` — `add_reservation_note`.
- `custom_components/guesty/actions.py:342` — the status action.
- `custom_components/guesty/__init__.py:395-401` — the separate
  `guesty.set_custom_field` handler, which calls `set_field()` directly.
  Only its `target_type="reservation"` branch is guarded; listing writes
  and the HTTP resource itself are unchanged (FR-030).

Omitting the third would leave door-code and other custom-field writes
unguarded after an identity mismatch, with no baseline, or against a
reservation with no confirmation code — precisely the writes most likely
to matter. Blocked-write tests cover all three entry points.

### Write actions

- `add_reservation_note` → `PUT /reservations-v3/{id}/notes`, preceded
  by a fresh read through `GET /reservations-v3?reservationIds={id}` so
  the existing note text is preserved (FR-027).
- `set_reservation_status` → `PUT /reservations-v3/{id}/status` with
  body `{"status": …}`; accepted values unchanged
  (`api/const.py:185-188`) (FR-028).

Whether the notes resource replaces or merges the note set is OQ-002 and
**[UNVERIFIED]**, so the read-before-write stays until live verification
settles it. Two consequences follow. First, the fresh read captures
**every** writable note field, not just `notes.other`, and the `PUT`
sends them all back, because a replacing resource would otherwise delete
`notes.guest` and any sibling field; a multi-category fixture asserts
this. Second, even a resource that merges note *objects* may still
replace the `notes.other` *string*, so the read/append/write sequence is
not dropped on the strength of merge semantics alone — only evidence of
server-side text append with the required separator would justify that.
The
coordinator's cached copy is deliberately *not* used as the read source:
it can be up to one poll interval stale and would silently discard a note
added in Guesty meanwhile, which is exactly the class of silent data loss
the constitution prohibits.

Both actions keep today's validation (`api/actions.py:418-470`) and
today's error shape (`api/actions.py:389-414`), so a rejection names the
resource and the rejection detail and is never reported as success
(FR-029). Reservation custom fields already use
`/reservations-v3/{id}/custom-fields` (`api/const.py:142-143`,
`api/custom_fields.py:116-141`, `api/custom_fields.py:200-260`) and are
not touched (FR-030).

### Backwards compatibility

What is at risk, and how it is held:

- **Entity ids and unique ids**: unchanged derivation from
  `entry.unique_id`, the listing id and the sensor key
  (`sensor.py:748-750`); the toggle changes no key (NFR-003, SC-008).
- **Device assignment**: reservation entities still attach to the
  Feature 002 listing devices (`sensor.py:803-812`).
- **State vocabulary**: mapped at parse time, so `completed` becomes
  `checked_out` and `alteration` is dropped (FR-023 to FR-025).
- **Attribute names**: the per-listing projection keeps
  `sensor.py:384-424` unchanged.
- **`guest_name`, `guest_phone`, `guest_email`**: re-sourced through the
  guest cache (FR-016, FR-017).
- **`nights_count`**: derived from the localized dates when v3 omits it.
- **`planned_arrival`, `planned_departure`**: **at risk**. OQ-003 is
  unverified; if v3 omits them they publish `None` on the v3 path and
  the loss is declared a breaking change under NFR-004.
- **`upcoming_reservations` summaries**: built from projections, with
  the same keys.
- **`custom_fields`**: unchanged resource, unchanged keys.
- **`reservation_total`, `reservation_balance`,
  `reservation_currency`**: **deprecated**, as above.

SC-002 is enforced by a differential test that runs one shared fixture
account through both paths and asserts identical entity ids, states and
attribute key sets, with the three financial sensors and any
unverified-attribute exception listed explicitly in the test rather than
tolerated by a loose comparison.

### Diagnostics (FR-042)

Per refresh, at DEBUG, with no personal data: pages fetched per resource;
reservation count by raw v3 status; a histogram of stay-segment counts;
counts of reservations attributed, partially attributed and skipped;
guest cache hits, lookups, failures and deferrals; `alteration` drops;
trim drops. At INFO, once per path activation: the identity match count
(FR-035) and the active reservation path. These are exactly the numbers a
user's log must contain for a maintainer to confirm or refute each
assumption on a real account without ever seeing a guest's details
(FR-021).

## Test Strategy

**Fixtures.** Python dictionary builders in `tests/conftest.py`, matching
the existing pattern (`tests/conftest.py:535-660`): `make_v3_segment()`,
`make_v3_reservation()` and `make_v3_search_page()` compose payloads from
the documented schema, since no live payload exists (NFR-008). The same
logical account is expressible in both legacy and v3 shapes, which is
what makes the differential test possible.

- **Request construction** — unit (respx): the exact query string,
  covering bracket filters, the date pad, `sort`, `limit`, `skip`, and
  the absence of `fields` and of any listing filter (FR-002, FR-003,
  FR-007, FR-015).
- **Status translation** — unit: `checked_out` also requests `completed`
  (FR-003a).
- **Page guard** — unit: each of the nine checks aborts, retains data
  and publishes nothing (FR-006).
- **HTTP 400** — unit: raises with detail, with no unfiltered retry
  (FR-008, US4-3).
- **Window** — unit with a frozen clock: out-of-window fixtures absent,
  a 200-day-old in-progress stay present (SC-004, SC-005).
- **Trim** — unit with a frozen clock: padded boundary results dropped,
  in-progress stays never dropped (FR-002a).
- **Attribution** — unit: single, multi-segment, parent and sub-unit,
  partly unknown, wholly unknown and group reservations (FR-009 to
  FR-013, SC-006).
- **Segment state** — unit with a frozen clock: before, during and after
  a mid-stay move, plus a canceled multi-segment stay (FR-011a, SC-012).
- **Listing filtering** — integration: a Feature 007 excluded listing
  publishes nothing (FR-014).
- **Guest cache** — unit: one lookup per distinct guest per refresh, TTL
  expiry refetches, failure retains the previous value, an id change
  never reuses a record, the LRU bound holds and the burst cap defers
  (FR-018 to FR-020, SC-010).
- **Request budget** — unit, counting transport calls: warm-cache v3 is
  no worse than legacy; a cold cache with ten guests costs pages plus
  ten (SC-003).
- **Identity guard** — unit: match, changed mapping, swap, missing code,
  empty baseline and persistence across a reload (FR-031 to FR-035,
  SC-007, SC-013).
- **Writes** — unit (respx): both v3 resources are hit, the
  read-before-write is preserved, a rejection raises and the guard
  blocks (FR-027 to FR-029, US3).
- **Toggle** — integration: legacy calls the legacy resources, v3 calls
  none of them, and the entity registry is identical across a toggle
  reload (SC-001, SC-008, FR-036 to FR-039).
- **Compatibility** — integration differential: entity id, state and
  attribute-key parity across both paths (SC-002, NFR-003, NFR-004).
- **Privacy** — unit: no name, phone, email or confirmation code appears
  in DEBUG records (FR-021, NFR-009).
- **Deprecation** — integration: the repair issue is raised only when
  the entities already exist and v3 is active.

The identity guard and the toggle are both tested through the
coordinator and service layers rather than in isolation, because both
exist to change *integration-level* behaviour: a blocked write and a
retained dataset are the observable outcomes that matter.

## Phasing

Each phase ends with green tests and is independently reviewable
(constitution IX). Phases 1–7 ship behind the option defaulting to
`legacy`; Phase 8 flips the default to `v3` (FR-037). No release is cut
between phases.

1. **Toggle and legacy isolation** — option key, default `legacy`,
   options-flow entry, the strategy seam, the legacy source moved
   verbatim and the startup deprecation warning (FR-036, FR-038 to
   FR-040). *Rollback*: a pure refactor; revert the commit.
2. **v3 models and mapping** — `GuestyStaySegment`, the v3 parse, the
   status map and the nights derivation (FR-022 to FR-026).
   *Rollback*: unused until Phase 3.
3. **v3 search source** — request construction, paging, the nine-check
   guard, the HTTP 400 error and the diagnostics counters (FR-001 to
   FR-008). *Rollback*: the option stays `legacy`.
4. **Attribution and trim** — segment attribution, the per-listing
   projection, segment-aware state and the instant-based trim (FR-002a,
   FR-009 to FR-015). *Rollback*: the option stays `legacy`.
5. **Guest cache** — the cache module, its wiring, and the budget and
   privacy tests (FR-016 to FR-021). *Rollback*: v3 path only.
6. **Identity guard** — the persisted baseline, the refresh check,
   fail-closed writes and match logging (FR-031 to FR-035).
   *Rollback*: v3 path only.
7. **v3 write actions** — the notes and status resources, guard
   enforcement and errors (FR-027 to FR-029). *Rollback*: the option
   stays `legacy`.
8. **Default flip, deprecation and docs** — default `v3`, the
   financial-sensor repair issue and strings, the differential and
   toggle tests, and the published pre-release checklist. *Rollback*:
   set the option to `legacy`.

**Rollback story.** At any point after release a user sets
`reservations_api` to `legacy` and reloads; retrieval and both write
actions return to the previous release's exact behaviour, entities are
untouched, and the financial sensors resume reporting values. The startup
warning and the retirement-shaped error message (FR-039) keep that state
from becoming permanent by accident.

## Scope Confirmations

- **Group reservations**: **in scope for attribution only**. Any segment
  referencing a tracked listing is published under that listing. No
  group-level entity is created, and `GET /reservations-v3/group/{id}` is
  not called. If a group booking is several reservations rather than one
  (OQ-006), they are handled as independent reservations, which needs no
  code change.
- **Mid-stay moves**: **in scope for attribution and state** (FR-010,
  FR-011, FR-011a). No per-segment entity is created.
- **Out of scope**, matching the spec: webhooks including
  `reservation.updated.v2`; every v3 write other than notes and status;
  comments, logs and reports; `GET /payments`; the entire guest-folio
  family, including the overview; new entity models; any change to
  default window, poll interval or status defaults; removal of the legacy
  path.

## Requirement Traceability

| Requirement | Where it is addressed |
| --- | --- |
| FR-001, FR-007 | v3 request construction; Phase 3 |
| FR-002, FR-003, FR-003a | request construction; status translation |
| FR-002a | client-side date trim |
| FR-004 | overlap filter replacing the two-query sweep |
| FR-005, FR-006 | pagination and the nine-check guard |
| FR-008 | `GuestyFilterRejectedError`; no unfiltered retry |
| FR-009 to FR-015 | listing attribution; data-model.md §3 |
| FR-016 to FR-021 | contracts/guesty-guest-lookup.md |
| FR-022 to FR-026 | status handling; data-model.md §1.4 |
| FR-027 to FR-030 | contracts/guesty-reservation-writes-v3.md |
| FR-031 to FR-035 | identity guard |
| FR-036 to FR-040 | contracts/options-flow-reservations-api.md |
| FR-041 | research.md pre-release checklist |
| FR-042 | diagnostics |
| NFR-001 to NFR-002 | request volume and cache bounds |
| NFR-003, NFR-004 | the backwards-compatibility section |
| NFR-005 | guest failures degrade attributes only |
| NFR-006 to NFR-009 | technical context and test strategy |
| SC-001 to SC-013 | the test strategy |

OQ-001 is settled by the owner (deprecate). OQ-002, OQ-003, OQ-004 and
OQ-005 are given a working decision plus a recorded fallback in
research.md. OQ-006 needs no code change either way. OQ-007 and OQ-008
remain deferred and unimplemented by FR-015.

## Complexity Tracking

**Principle VII: the three financial sensors lose their values on the
v3 path.** Needed because v3 removes the embedded `money` block and the
owner decided against integrating the guest folio. The simpler
alternative — sourcing the folio — is rejected because it adds an
unverified field mapping, a new per-refresh failure mode and extra rate
exposure, and could change the *meaning* of the published numbers while
keeping their names: a worse outcome than an explicit, announced
deprecation.

**A per-listing projection object in the coordinator.** Needed because
v3 attribution is per segment while every consumer expects one
reservation per listing. Teaching `sensor.py` about segments is rejected
because it would put migration logic in the entity layer and put every
attribute name at risk, directly against NFR-004.

**A persisted identity baseline.** Needed because the guard must survive
restarts and the coordinator is rebuilt on every setup
(`__init__.py:283-289`). An in-memory baseline is rejected because it is
empty at every start, so the guard would compare against nothing and
silently pass.

[issue-204]: https://github.com/tykeal/homeassistant-guesty/issues/204
[issue-223]: https://github.com/tykeal/homeassistant-guesty/issues/223

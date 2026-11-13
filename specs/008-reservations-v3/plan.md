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

Five consequences drive the rest of the design:

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
4. Nothing about v3 is verified yet. This project has no live Guesty
   account of its own; an external user has offered theirs, starting with
   test reservations, so live verification is **available but deferred to
   the pre-release gate** — there is nothing to exercise until an
   implementation exists (spec FR-041). Until then the design fails safe
   everywhere: loud errors instead of empty results, aborted refreshes
   instead of ambiguous publishes, and refused writes instead of writes
   to an unconfirmed identity.
5. Guesty's backfill into v3 is still running, so a reservation missing
   from a v3 response may simply not be there *yet*. A **legacy
   not-found fallback** (spec FR-050 to FR-057, designed in
   [Legacy not-found fallback](#legacy-not-found-fallback)) retrieves
   such a reservation by identifier from the legacy resource so that a
   live booking cannot silently vanish mid-migration.

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
├── analysis.md          # Analyze: cross-artifact findings
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
│   ├── reservations_fallback.py # (new) not-found fallback source
│   └── reservations_v3.py       # (new) v3 search source
├── actions.py                   # (modify) identity-guard writes
├── config_flow.py               # (modify) reservations_api option
├── const.py                     # (modify) option key and default
├── coordinator.py               # (modify) attribution, guard, diag
├── identity.py                  # (new) persisted identity baseline
├── diagnostics.py               # (new) config entry diagnostics
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
│   ├── test_reservations_fallback.py  # (new) not-found fallback
│   └── test_actions.py          # (modify) v3 write paths
├── test_coordinator.py          # (modify) attribution, trim
├── test_diagnostics.py          # (new) diagnostics download
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
- **Establishment**: the active map is rewritten wholesale after each
  successful, unambiguous refresh, from whichever path produced it
  (FR-031b). Entries are capped at 5,000 codes, oldest check-out first,
  so storage stays bounded. A legacy bootstrap map is held *separately*
  in `bootstrap_codes` and is never overwritten by a refresh, so a
  successful but disjoint v3 refresh cannot discard the only cross-path
  evidence there is. Every refresh is compared against both maps, and
  bootstrap entries are retired **individually**, not wholesale: a code
  checked without conflict against the refresh is removed, while a code
  the refresh never mentioned is retained — a partial overlap must not
  discharge the codes it did not cover. Reaching `verified` continuity
  therefore does not clear the map either; it only reflects that at
  least one code has been checked. A retained entry is retired without
  a check only under the explicit safe policy that its reservation
  checked out more than `past_days` ago, so it can no longer appear in
  any future window, which also keeps the map bounded. Tests cover a
  disjoint refresh followed by a conflicting overlap, and a partial
  overlap followed by a conflict on an uncovered code, both across a
  reload.
- **Bootstrap on upgrade**: because no release ships between phases, an
  existing installation meets v3 with no stored baseline at all. A
  baseline built from the first v3 refresh alone would then be
  self-consistent by construction, so swapped legacy and v3 identifiers
  would pass unexamined. The baseline therefore records a `continuity`
  state as well as its `path`, and the two are not conflated:
  - *No baseline and no pre-existing reservation entities* — a fresh
    install. Continuity is `not_applicable`: there is no earlier
    identity to contradict. The first v3 refresh establishes the
    baseline and writes are permitted.
  - *No baseline but reservation entities already in the registry* — an
    upgrade. Setup performs a **one-time legacy bootstrap sweep** over
    the same window, using the still-live legacy resource, and stores
    its code-to-id map with `path: "legacy"`. The first v3 refresh is
    then checked against it exactly as any later refresh would be.
    Continuity becomes `verified` only when the comparison is
    *informative*: at least one confirmation code appears in both maps
    and no code's mapping conflicts. An empty or disjoint comparison
    proves nothing, so the bootstrap evidence is retained, continuity
    stays `unconfirmed`, and writes stay blocked until a later refresh
    produces an overlap — mirroring the acceptance rule in the
    pre-release checklist (research.md, "Identity and continuity").
  - *Bootstrap sweep unavailable* — the legacy resource is retired,
    rate-limited or errors. Continuity stays `unconfirmed`: reservations
    publish normally, but every reservation write is refused with an
    explanatory error and a repair issue explains that cross-path
    identity could not be confirmed (FR-031c).
  - *Bootstrap disagrees with the first v3 refresh* — treated exactly
    as FR-032: the refresh aborts, writes stay blocked, and the error
    names both ids.

  The sweep costs one extra legacy page sequence, once per entry, and
  only for installations that actually have something to contradict.
  Tests cover the upgrade path with no Store, the fresh-install path,
  an unavailable bootstrap, a disagreeing bootstrap, and the empty and
  disjoint comparisons.

  **The sweep is permitted exception 1 of FR-001**, and is specified in
  its own right as FR-031d. The specification amendment this plan asked
  for landed in the analyze stage: FR-001 now prohibits the legacy
  *path* for routine operation while enumerating exactly two permitted
  legacy calls — this sweep and the not-found fallback below — SC-001
  asserts each one's bound separately, and FR-034 scopes zero-cost to
  the per-refresh comparison, naming this sweep as the only request the
  guard may ever issue. The exception remains bounded to one legacy page
  sequence, at the first v3 setup of an upgrading entry, before any v3
  data is published, and never again. The assertion "no request targets
  `/v1/reservations` while the v3 path is active" therefore reads: no
  such request in any refresh, exactly one bootstrap sequence for an
  upgrading entry's first setup and zero otherwise, and at most the
  per-refresh fallback cap of by-identifier lookups
  (contracts/guesty-reservations-v3-search.md).
- **Check**: before publishing a refresh, every reservation carrying a
  confirmation code is compared with the baseline. Any code whose mapping
  *differs* makes the identity ambiguous — including the swap case where
  two codes exchange two still-present ids (FR-032). The refresh aborts
  via `UpdateFailed`, last-known-good data is retained, no write is
  permitted, and one error names both ids, the code's presence (never its
  value), and the rollback instruction.
- **Cost**: zero additional requests; the comparison runs on data already
  fetched (FR-034).
- **Fail closed for writes**: a write is permitted only when *all* of
  the following hold — a usable baseline exists, its `continuity` is
  `verified` or `not_applicable`, the last refresh was not aborted as
  ambiguous, and the target reservation carries a confirmation code.
  Existence alone is **not** sufficient: after a failed or uninformative
  bootstrap a successful v3 refresh does create a baseline while
  continuity is still `unconfirmed`, and writes must stay blocked in
  that state. Reservations are published throughout; only writes abort,
  with an explanatory error (FR-031c, FR-033, SC-013). The predicate is
  re-evaluated from the Store after every reload, and the guard tests
  cover each failing conjunct, including across a reload.
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

### Legacy not-found fallback

Guesty Support states that a reservation missing from a v3 response may
simply not have been backfilled into v3 yet, and advised retrieving it
with `GET /v1/reservations/{reservationId}` in that case
([#223][issue-223]). Without that, a live booking would silently vanish
from calendars and sensors mid-migration, and a door-lock automation
would never fire. Spec FR-050 to FR-057 require the fallback; this is its
design.

**Where it sits.** Inside the `ReservationSource` seam, not above it. A
`ReservationFallbackSource` collaborator — a thin wrapper over the
existing legacy by-identifier retrieval in
`api/reservations_legacy.py` — is held by `V3ReservationSource` only.
The coordinator still awaits one `fetch()` and still receives one
`ReservationFetchResult`, so attribution, projection, the client-side
trim and the whole sensor layer are untouched. The legacy source does
not hold a fallback at all: under `reservations_api="legacy"` the legacy
resource is already primary, so the fallback is inert (FR-056) and needs
no separate switch. Keeping the fallback below the seam also keeps
`api/` free of Home Assistant imports (NFR-007).

**Candidate set.** The fallback is **never** a sweep. After a v3 search
completes, `V3ReservationSource` computes
`missing = previously_known - returned_v3_ids`, where
`previously_known` is the union of the identifiers published by the last
successful refresh and the identifiers in the identity baseline's active
and `bootstrap_codes` maps (FR-050). A candidate is dropped before any
request if its last known stay no longer overlaps the configured window
— it would be trimmed anyway — or if it has already been confirmed
absent. A reservation never seen before is not a candidate: there is no
identifier to look up (FR-051).

**Normalization and attribution.** A legacy document maps through the
existing legacy mapper, which leaves `stay` empty and populates
`listing_id` directly (`data-model.md` §1.3). Attribution is therefore
**source-aware**: a v3-sourced record is attributed from its stay
segments and a record with no segments is still skipped as malformed
(FR-013), while a fallback-sourced record is attributed from
`listing_id` under the Feature 003 rules the legacy path already uses.
Without that distinction the segment-based attribution would discard
every fallback record before it reached a sensor, so the fallback would
buy nothing. The skip-on-no-segments validation of the v3 path is not
weakened: it simply does not apply to a record that never had segments.
A fallback record is also subject to the same window trim and the same
configured status filter as any other reservation (FR-050a).

**Interaction with the identity guard.** A fallback record is
legacy-sourced, so it may not be allowed to confirm itself. Its
confirmation code is written to a separate `fallback_codes` map, never
merged into the active `codes` map, exactly as `bootstrap_codes` is kept
separate. The guard compares fallback records against the baseline as it
does any other: a code mapping to a different identifier is ambiguous
and aborts the refresh under FR-032. Continuity is never promoted to
`verified` on fallback evidence alone, because the fallback record and
the baseline entry can share a common legacy ancestor.

**Caching and negative caching.** Per config entry, in memory:

- A **confirmed-absent** set. A legacy not-found answer is proof the
  reservation is gone; the candidate is retired, never looked up again
  in that session, and disappears from the sensors exactly as today
  (FR-052). This is what stops the fallback masking a real deletion.
- A **served-record** map from identifier to `(reservation, fetched_at)`.
  Between re-checks the stored record is republished without a request.
  A candidate is re-looked-up at most once per
  `LEGACY_FALLBACK_RECHECK_SECONDS` (3600), so a 15-minute poll costs at
  most one lookup per candidate per hour rather than four, while a
  reservation that finally lands in v3 is picked up within the hour with
  no restart. A record that reappears in the v3 result is dropped from
  the map immediately and reverts to being a normal v3 reservation.

Neither cache is persisted — a persisted absence could permanently hide
a reservation that returns. Candidacy itself must survive a restart,
however, so the previously-known union includes the baseline's
`fallback_codes` map as well as `codes` and `bootstrap_codes`:
otherwise a reservation that only ever arrived through the fallback
would lose its candidacy on reload and vanish. The window filter needs
durable stay data that a code-to-identifier map does not carry, so the
baseline also persists `window_hints`, an identifier-to-check-out map
covering every identifier in the three code maps (`data-model.md` §4).
A restart therefore costs at most one bounded round of lookups, and
confirmed absence is re-established by that round rather than carried
across the restart.

**Request budget.** `MAX_LEGACY_FALLBACK_LOOKUPS_PER_REFRESH` (20) caps
the lookups a single refresh may issue; candidates over the cap are
deferred to the next refresh and counted as deferred in diagnostics.
Ordering is **fair**, not by check-out date: never-attempted candidates
come first, then the least-recently-attempted, breaking ties by
earliest check-out. Ordering by check-out alone would let the same
early candidates consume the cap, and a re-check becoming due would
displace a candidate that has never been looked up at all — with 100
missing reservations, a cap of 20, hourly re-checks and a 15-minute
poll the backlog would never drain. Fair ordering guarantees every
candidate is attempted within a bounded number of refreshes. Lookups
run through the existing
rate-limit-aware client with the same bounded concurrency as the guest
lookups, so they cannot exhaust the rate budget (NFR-002). Spec NFR-001
excludes them from the warm-cache steady-state comparison — a steady
state has no missing reservations — and FR-053 bounds them instead. The
request-budget test asserts zero legacy requests when nothing is
missing, exactly the cap when more than the cap are missing, and one
lookup per candidate per re-check interval (SC-017).

**Error handling.** A not-found answer is the confirmed-absence case
above. A retirement-shaped answer self-disables the fallback for the
session with one WARNING naming 2027-03-01 (FR-056). Anything else —
timeout, 5xx, rate limit, malformed body — degrades only that
reservation: the **last successfully published record for that
identifier is retained and republished read-only, whatever its source**
— a served fallback record if there is one, otherwise the values the v3
path last published — the failure is counted and logged without
personal data, and the refresh still publishes (FR-054). Retaining only
a served fallback record would drop a reservation on the *first* failed
lookup, recreating exactly the disappearance the fallback exists to
prevent. Retention is bounded by the same window trim and re-attempted
on the next refresh; a confirmed absence or a retirement answer still
ends it immediately. A fallback failure can never raise `UpdateFailed`.

Distinguishing "this reservation does not exist" from "this resource is
retired" is **[UNVERIFIED]**; the implementation treats a 404 carrying a
reservation-scoped error body as absence and a 404/410 on the collection
or a retirement-signalling body as retirement, and when it cannot tell,
it chooses *retirement* — self-disabling is the safer error, since it
degrades to today's behaviour rather than erasing a reservation.

**Writes.** Fallback-sourced reservations are published read-only. All
three write entry points refuse them with an error naming the cause
(FR-055); see [Write actions](#write-actions). This honours Support's
instruction that an identifier used against v3 must have come from a v3
response.

**Observability.** `ReservationFetchResult` gains fallback counters —
attempted, served-from-cache, succeeded, confirmed-absent, failed,
deferred — plus the identifiers currently served from the fallback.
These surface in both diagnostics surfaces of FR-042. They are
deliberately **not** exposed as a sensor attribute: a v3-only attribute
key would break SC-002's attribute-key parity and NFR-004 (FR-057).

**When legacy is gone.** After 2027-03-01 the fallback self-disables on
first use (FR-056) and is deleted with the rest of the legacy path
(FR-040) — `ReservationFallbackSource`, its constants, its counters and
its tests, leaving `V3ReservationSource` with the plain v3 result.

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

Both actions, and the `target_type="reservation"` branch of
`guesty.set_custom_field`, additionally refuse a **fallback-sourced**
target: a reservation currently served from the legacy not-found
fallback is not in v3, its identifier did not come from a v3 response,
and a v3 write against it would return "Reservation not found". The
refusal happens in the HA service layer alongside the identity guard,
before any request is constructed, and names the cause — the reservation
is not yet available in v3 — so it can never be mistaken for success
(FR-055, SC-016). Whether such a write should instead be routed to the
legacy write resource is spec OQ-010 and needs an owner decision; until
it is taken, the refusal stands.

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

FR-042 requires **two** surfaces, carrying the same numbers, and the plan
provides both. The repository ships no `diagnostics.py` today, so the
download surface is new; quickstart.md already documents it.

1. **Per-refresh DEBUG log**, with no personal data: pages fetched per
   resource; reservation count by raw v3 status; a histogram of
   stay-segment counts; counts of reservations attributed, partially
   attributed and skipped; guest cache hits, lookups, failures and
   deferrals; `alteration` drops; trim drops; the per-exception counts of
   permitted legacy calls required by FR-001 (bootstrap sweep pages,
   fallback lookups); and the fallback counters of FR-057.
2. **A `reservations` block in the config entry's diagnostics download**
   (`async_get_config_entry_diagnostics`), carrying the same counters
   plus the active reservation path, the identity baseline's
   `continuity` state and the identifiers currently served from the
   fallback. Nothing in the block is personal data: identifiers only,
   never names, contacts or confirmation code values.

At INFO, once per path activation: the identity match count (FR-035) and
the active reservation path. Together these are exactly the numbers a
user's log or download must contain for a maintainer to confirm or refute
each assumption on a real account without ever seeing a guest's details
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
- **Not-found fallback** — unit and integration: a previously published
  reservation missing from v3 but present in legacy keeps its state and
  attributes and is counted as fallback-sourced (SC-015); a legacy
  not-found retires it and stops further lookups (SC-015, FR-052); every
  write entry point refuses a fallback-sourced target with no request
  issued (SC-016); the per-refresh cap, the zero-legacy-request
  steady state and the re-check interval are asserted by a
  request-counting test (SC-017); a retirement-shaped answer
  self-disables the fallback with one warning while the refresh still
  succeeds (SC-018); and the differential test asserts that no sensor
  attribute key differs for a fallback-sourced reservation (FR-057,
  SC-002).

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
6. **Identity guard** — the persisted baseline, the bootstrap sweep, the
   refresh check, fail-closed writes and match logging (FR-031 to
   FR-035, FR-031d, FR-031e). *Rollback*: v3 path only.
6a. **Legacy not-found fallback** — the fallback source, candidate
   selection, negative caching, the re-check interval, the per-refresh
   cap, retirement self-disable and the fallback counters (FR-050 to
   FR-054, FR-056, FR-057). Depends on Phase 6 for the baseline it
   draws candidates from. *Rollback*: v3 path only; removing the
   collaborator restores plain v3 behaviour.
7. **v3 write actions** — the notes and status resources, guard
   enforcement, the fallback-sourced write refusal (FR-055) and errors
   (FR-027 to FR-029). *Rollback*: the option stays `legacy`.
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
| FR-031d, FR-031e | identity guard; bootstrap sweep and id provenance |
| FR-036 to FR-040 | contracts/options-flow-reservations-api.md |
| FR-041 | research.md pre-release checklist |
| FR-042 | diagnostics (both surfaces) |
| FR-050 to FR-054 | legacy not-found fallback |
| FR-055 | write actions; fallback-sourced refusal |
| FR-056, FR-057 | legacy not-found fallback; diagnostics |
| NFR-001 to NFR-002 | request volume and cache bounds |
| NFR-003, NFR-004 | the backwards-compatibility section |
| NFR-005 | guest failures degrade attributes only |
| NFR-006 to NFR-009 | technical context and test strategy |
| SC-001 to SC-014 | the test strategy |
| SC-015 to SC-018 | the test strategy; fallback tests |

OQ-001 is settled by the owner (deprecate) and OQ-009 is settled at the
analyze stage (both surfaces). OQ-002, OQ-003, OQ-004 and OQ-005 are
given a working decision plus a recorded fallback in research.md. OQ-006
needs no code change either way. OQ-007 and OQ-008 remain deferred and
unimplemented by FR-015. OQ-010 — whether a write to a reservation that
is not yet in v3 should be routed to the legacy write resource — is open
and needs an owner decision; FR-055's refusal is implemented meanwhile
and routing would be purely additive.

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

**A one-time legacy bootstrap sweep on the v3 path.** Needed because an
upgrading installation has no baseline to compare the first v3 refresh
against, and a baseline built from v3 alone is self-consistent by
construction. It is permitted exception 1 of the restated FR-001, is
specified as FR-031d, and is bounded by the restated SC-001 and FR-034;
the specification amendment this previously depended on landed in the
analyze stage. Enabling writes on a never-cross-checked identity is
rejected as the simpler alternative because reservation custom-field
writes carry door-lock codes.

**A second legacy call on the v3 path: the not-found fallback.** Needed
because Guesty's backfill is still running, so a v3 not-found may mean
"not migrated yet"; without the fallback a live reservation silently
disappears from calendars and sensors and a door-lock automation never
fires. It is permitted exception 2 of the restated FR-001, is bounded by
FR-053 and self-disables at retirement under FR-056. The simpler
alternative — treating absence as deletion, as the sibling project does
— is rejected because silent loss of a live booking is the worst
non-write outcome this feature can produce. The cost is one extra
collaborator, two in-memory caches and a read-only publication mode,
all deleted with the legacy path.

**A persisted identity baseline.** Needed because the guard must survive
restarts and the coordinator is rebuilt on every setup
(`__init__.py:283-289`). An in-memory baseline is rejected because it is
empty at every start, so the guard would compare against nothing and
silently pass.

[issue-204]: https://github.com/tykeal/homeassistant-guesty/issues/204
[issue-223]: https://github.com/tykeal/homeassistant-guesty/issues/223

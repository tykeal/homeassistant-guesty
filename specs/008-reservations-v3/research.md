<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Reservations v3 API Migration

**Feature Branch**: `008-reservations-v3` | **Date**: 2026-11-13
**Status**: Complete; amended at the analyze stage (R5 corrected, R13 to
R15 added) and again for the owner's OQ-010 decision (R16 added).
Documentation-derived; live confirmation is now **available and
deferred** to the
[pre-release checklist](#pre-release-live-verification-checklist)

## Sources and their standing

- [Guesty reservation migration guide][guesty-migration] — vendor
  documentation; authoritative for the endpoint mapping and the
  retirement date.
- [Guesty changelog, 2026-11-05][guesty-changelog] — vendor
  documentation.
- Issue [#223][issue-223], "Agreed scope decisions" — owner decisions.
- Issue [#223][issue-223], Guesty Support's written reply of 2026-11-13,
  obtained and posted by an external user — **vendor statement, still
  not evidence this project has reproduced**. It supplies the v3
  identifier rule (R14) and the not-found semantics that R13 is built
  on, and it contradicts the vendor's own retirement date (spec R-011).
- `tykeal/homeassistant-rentalsync-bridge`,
  `specs/004-guesty-reservations-v3/` — **corroborating documentation,
  not evidence**: its own `research.md:11` records that no live Guesty
  call was ever made.
- This repository's source — read directly; every citation below is a
  real file and line range.

**This project has no live Guesty v3 access of its own.** Every
statement about v3 request shapes, filters, response fields, statuses and
error behaviour is a hypothesis. Live verification has since been
*offered* by an external user, against their own account and starting
with test reservations (R14); it is deferred to the pre-release gate
because there is nothing to exercise until an implementation exists, and
until it happens no hypothesis below is promoted to fact. The design is
therefore built so that a wrong hypothesis produces a loud, diagnosable
failure rather than wrong published data (FR-006, FR-008, FR-032).

The sibling bridge is a FastAPI add-on with its own database, scheduler
and sync machinery. **None of its architecture is transferable.** Only
its HTTP contract reading and its field mappings were reused, and those
only as hypotheses.

---

## R1: Which resources the integration calls, before and after

Not retiring, and unchanged: `POST /oauth2/token`, `GET /listings`, and
the reservation custom-field resources
`GET`/`PUT /reservations-v3/{id}/custom-fields`
(`api/const.py:142-143`, FR-030).

Retiring 2027-03-01, and replaced:

- `GET /reservations` (`api/const.py:88-89`) →
  `GET /reservations-v3/search`.
- `GET /reservations/{id}` for the note read
  (`api/actions.py:84-91`) → `GET /reservations-v3?reservationIds={id}`.
- `PUT /reservations/{id}` for the note write
  (`api/actions.py:115-120`) → `PUT /reservations-v3/{id}/notes`.
- `PUT /reservations/{id}` for the status write
  (`api/actions.py:364-369`) → `PUT /reservations-v3/{id}/status`.
- The same three legacy requests remain reachable, for a
  **fallback-sourced** reservation only, as the legacy write routing of
  R16 — permitted exception 3 of FR-001, not routine operation.
- The embedded `money` block — **not replaced**; the financial sensors
  deprecate (R9).

New on the v3 path: `GET /guests-crud/{id}` for guest details.

### Decision

On the v3 path the integration calls no resource the migration guide
marks deprecated for routine operation, with the three enumerated and
individually bounded exceptions of FR-001: the bootstrap sweep, the
not-found fallback (R13) and the legacy write routing (R16) (FR-001,
SC-001). On the legacy path it calls exactly what the previous release
called, unchanged (FR-040).

---

## R2: Why the legacy filters were silently ignored (#204)

`_build_reservation_params()` sends top-level `checkIn`, `checkOut` and a
comma-joined `status` (`api/client.py:621-640`). The legacy API accepts
and ignores them. Nothing downstream re-checks: neither
`get_reservations()` (`api/client.py:227-390`) nor
`ReservationsCoordinator._async_update_data()`
(`coordinator.py:198-267`) tests a reservation against the configured
window. Two independent defects therefore compounded into silence.

### R2 decision

Both halves are fixed. The request uses v3's bracketed filters, which
return HTTP 400 rather than ignoring what they do not support (FR-002,
FR-003, FR-008), and a **new** client-side instant-based trim becomes the
authoritative window check on both paths (FR-002a). Either one alone
would have prevented #204; having both means a future filter change
cannot silently widen the result again.

---

## R3: Expressing the two-query intent in v3 filters (OQ-005)

### R3 context

Today's retrieval issues two paginated sequences and merges them
(`api/client.py:277-287`, `api/client.py:641-648`): a date-and-status
window query, and a `checked_in`-only query with **no** date window whose
sole purpose is to catch a long stay whose check-in predates the window.
The second query sweeps account history, which FR-004 explicitly forbids
carrying forward.

### R3 decision

One query, using an overlap test:

```text
filter[checkOut][gte] = D_start
filter[checkIn][lte]  = D_end
```

A stay is returned when it starts before the end of the window and ends
after the start of it. Any stay in progress at refresh time has a
check-out at or after today, which is at or after `D_start`, so it is
returned however long ago it began (FR-004, SC-005). No unbounded sweep
is needed.

`D_start` is the UTC date of `now − past_days` minus one day, `D_end` the
UTC date of `now + future_days` plus one day. The pad exists because the
filters are documented to compare listing-localized dates while the
configured window is instant-based, and real offsets stay inside ±14 h.
The pad makes the server result a **superset**; the client trim (R4) is
authoritative.

### R3 alternatives considered

- **Port the two queries verbatim.** Rejected: FR-004 forbids the
  unbounded sweep, and it would double the page count for no coverage
  the overlap filter does not already provide.
- **Check-in lower bound only.** Rejected: it drops exactly the long
  in-progress stays the second legacy query existed to catch.

### R3 fallback if live verification refutes it

It is **[UNVERIFIED]** that `filter[checkIn]` and `filter[checkOut]` can
be combined and are ANDed. If they cannot, the fallback is two v3
queries — the window query, plus `filter[status][in]=checked_in` with
`filter[checkOut][gte]=today` — merged by `reservationId`. That keeps
FR-004 and still avoids an unbounded sweep. The fallback costs one extra
page sequence and would need NFR-001 re-checked against the legacy
two-sequence baseline, which it still matches.

---

## R4: Client-side window trim (FR-002a)

### R4 decision

After mapping and before publication:

```text
keep ⇔ check_out > now − past_days  and  check_in < now + future_days
```

evaluated on instants, at reservation level, on **both** paths.

### R4 rationale

- It removes the one-day pad's boundary results, so the published set
  matches the configured window rather than the padded request.
- It cannot discard an in-progress stay: such a stay satisfies both terms
  by construction (FR-002a's explicit constraint).
- It is deliberately an *overlap* rule, not legacy's notional check-in
  window. At the past edge it is therefore slightly more inclusive than
  the legacy intent, which is the behaviour FR-004 asks for and which
  today's unbounded `checked_in` query already produced in practice.
- Applying it on the legacy path too means the rollback target also has a
  correct window. The trim only ever narrows what is published, so it
  cannot resurrect a reservation the legacy path dropped.

---

## R5: Page validation without a total count (FR-005, FR-006)

### R5 decision

`sort=_id` ascending, `limit=100`, `skip = page × 100`, page cap
`MAX_V3_RESERVATION_PAGES = 200`. A refresh is accepted only when every
check in `plan.md` §"Pagination, page validation and failure modes"
passes; otherwise it raises, last-known-good data is retained and nothing
is published.

### R5 rationale

Ascending ObjectId order means a reservation created mid-search sorts
after the cursor and cannot disturb a page already read. A reservation
moving *into* the filtered set behind the cursor shifts later records
forward and shows up as a duplicate (check 6) or an order violation
(check 7). A reservation leaving the set behind the cursor shifts later
records back and is undetectable by any paging check.

**Corrected at the analyze stage.** This section previously concluded
that absence was harmless here — "a listing whose reservations vanish
simply reports `no_reservation`" — and on that basis declined the
bridge's confirmed-absence machinery. Guesty Support has since stated
that a reservation missing from v3 may simply not have been backfilled
yet (R13), so absence is **not** proof of deletion and silently
reporting `no_reservation` would drop a live booking. The conclusion now
is: a paging-induced or backfill-induced absence is caught by the
not-found fallback (FR-050 to FR-057), which confirms the absence
against the legacy resource by identifier before letting a previously
known reservation disappear. The bridge's own confirmed-absence
machinery (`GET /reservations-v3?reservationIds=…` with a control id) is
still **not** adopted — it exists to protect a persistent database this
integration does not have, and it cannot distinguish "not backfilled"
from "deleted", which is the distinction that actually matters here.

The 200-page cap bounds a pathological account at 20,000 in-window
reservations before the refresh fails loudly. It is lower than the
bridge's 500 because this integration has no listing filter (FR-015) and
a runaway sweep on a 15-minute poll is a rate-budget hazard.

### R5 alternatives considered

- **Sort by `checkIn`.** Rejected: not unique, so page boundaries are not
  deterministic.
- **Trusting `hasMore` alone.** Rejected: with no total count, an
  unnoticed short page silently truncates every sensor on the account.

---

## R6: Listing attribution and the projection (FR-009 to FR-015)

### R6 findings

v3 carries no top-level `listingId`, `unitId`, `unitTypeId` or `mtl`.
Attribution comes from `stay[].listingId` (documented as
`unitId ?? unitTypeId`) and `stay[].parentListingId`. The documented
`filter[listingId]` matches only the **first** stay segment.

Today the coordinator groups on a single `reservation.listing_id` and
drops anything whose listing id is not in the listings coordinator's data
(`coordinator.py:234-267`).

### R6 decision

- Attribute through **every** segment, to `segment.listing_id`, falling
  back to `segment.parent_listing_id` (FR-010, FR-012).
- Publish a reservation with at least one attributable segment; skip with
  a warning only when no segment resolves, and log unresolved segments at
  debug (FR-013).
- Emit a per-listing **projection** of `GuestyReservation` rather than
  teaching the sensor layer about segments. The projection substitutes
  the segment's dates, localized dates and guest count when a reservation
  has more than one segment, keeps the reservation `id` so custom-field
  writes stay keyed correctly, and is byte-identical to the reservation
  for the single-segment case (FR-011).
- Emit one projection per tracked listing even when a reservation has
  several segments there, but keep those segments as **disjoint
  intervals** rather than collapsing them into one span. An A → B → A
  stay collapsed to earliest-in/latest-out would cover the guest's time
  in B, and the temporal rule would then report A as `checked_in` while
  the guest is in B. The projected interval is the one containing the
  refresh instant, else the earliest future interval, else the latest
  past one. The bridge's `#n` per-interval identities are still **not**
  adopted: they exist to key database rows, and a second entity-visible
  reservation for one listing would change the published attribute
  surface.
- Send **no** server-side listing filter (FR-015). The first-segment-only
  semantics would hide a stay beginning in an untracked listing and
  moving into a tracked one, unrecoverably and undiagnosably. Retrieval
  is completeness-preserving over the window and status set.

### R6 consequence for request volume

Pagination scales with the account's in-window reservations rather than
with the tracked listings. That is the accepted cost of completeness,
already recorded in NFR-001 and OQ-008. It is not a regression: today's
legacy retrieval is equally account-wide, and uses *two* sequences rather
than one.

### R6 chunking, specified but not implemented

If OQ-008 is ever decided the other way, the listing filter must be
chunked. The rule is recorded in
`contracts/guesty-reservations-v3-search.md`: group ids into families
(multi-unit parent with its sub-units; tracked child with its parent unit
type; single-unit listing alone), pack whole families into chunks of at
most **50** ids so a first segment's `unitId` and `unitTypeId` always
land in the same chunk, split only a family that alone exceeds 50, run
the full R5 guard per chunk, and merge by `reservationId` with a
conflicting duplicate failing the refresh. A 50-id chunk is roughly
1.4 KB of query string, which keeps the request line under 2 KB. None of
this is built in this feature; adopting it requires amending FR-015
first (OQ-007, OQ-008).

---

## R7: Segment-aware state for mid-stay moves (FR-011a)

### R7 findings

`_select_reservation()` and `_derive_state()` key on status alone
(`sensor.py:305-366`). Under v3, a multi-segment `checked_in`
reservation would make both the origin and the not-yet-occupied
destination listing report `checked_in`, and the future segment could
outrank the destination's real current occupant.

### R7 decision

The projection's status is derived temporally, but **only** when the
reservation-level status is `confirmed` or `checked_in` and the
reservation has more than one segment:

| Segment position at refresh time | Projected status |
| --- | --- |
| before segment check-in | `confirmed` → `awaiting_checkin` |
| within the segment | reservation status (`checked_in` stays so) |
| after segment check-out | `checked_out` |

Every other reservation-level status — `canceled`, `checked_out`,
`completed`, `alteration`, unrecognized — is carried unchanged to every
listing it touches, so a canceled multi-segment reservation reports
`canceled` on both listings and never `awaiting_checkin`.

Because an upcoming segment projects to `confirmed`, the existing
priority table already prevents it from displacing a current occupant;
no change to `_STATUS_PRIORITY` is required, which is the cheapest
possible way to satisfy FR-011a without touching the state vocabulary.

---

## R8: Guest lookup and caching (FR-016 to FR-021, OQ-004)

### R8 findings

v3 embeds no guest object and offers no `lastUpdatedAt`, so there is no
change signal at all for guest data. The migration guide says only "the
`/guests` endpoints" without naming a single-guest read.

### R8 decision

`GET /v1/guests-crud/{guestId}?fields=fullName phone email`, cached in
`api/guest_cache.py` with: key = guest id only; 24-hour hit TTL; 1-hour
miss TTL; 512-entry LRU bound; one lookup per distinct guest per refresh;
a 50-lookup-per-refresh burst cap refreshing the oldest entries first;
concurrency 4; failure retains the previous value for the same id and
never fails the refresh.

### R8 rationale

- **Key on guest id only** makes FR-019 structural: there is no code path
  by which one guest's record can be published for another.
- **A finite TTL** is the only available freshness trigger (FR-018). 24
  hours means at most one lookup per guest per day against a 15-minute
  poll, and an edit made in Guesty appears within a day.
- **A burst cap** is what keeps a cold start from issuing hundreds of
  lookups in one refresh on a large account (NFR-001, NFR-002). Deferred
  guests keep serving their previous values, or no guest attributes at
  all if they have none yet, which is NFR-005 behaviour.
- **Only tracked listings** trigger lookups, so untracked reservations
  returned by the completeness-preserving search cost nothing extra.

### R8 alternatives considered

- **`GET /v1/guests/{id}`.** Retained as the documented fallback if
  `guests-crud` proves wrong, but not the default: the migration guide
  points guest reads at the `/guests` family as part of the same
  modernisation, and silently keeping a resource that may retire would
  reproduce this migration in miniature.
- **No cache, one lookup per reservation per refresh.** Rejected
  outright: it breaks NFR-001 and would risk the account's rate budget.
- **Embedding guest details in the identity baseline store.** Rejected:
  it would persist guest PII to disk for no functional gain.

---

## R9: Financial sensors — deprecation (OQ-001, owner decision)

### R9 decision

**Option B: deprecate.** The repository owner decided on 2026-11-13
against the specification's original Option A recommendation.
`GET /guest-folio/overview` is **not** integrated. `GuestyMoney` remains
in the model for the legacy path, is always `None` on the v3 path, and
the three sensors report unavailable there through the existing
`available` property (`sensor.py:772-800`).

### R9 rationale as recorded by the owner

- The folio's field mapping to today's three values is unverified, and a
  mismatch would change the *meaning* of published numbers while keeping
  their names, which is worse for an automation author than an announced
  removal.
- It avoids a new resource, a new per-refresh failure path, a batching
  and partial-failure design, and extra rate exposure, on a migration
  that already carries an unusual amount of unverified surface.

### R9 consequences the plan must carry

- A declared breaking change under NFR-004, in release notes, not a
  silent drop.
- A repair issue plus a startup warning, raised only when the v3 path is
  active and at least one of the three entities already exists.
- Entities are **not** removed now: they must keep working on a rollback
  to the legacy path. They are removed together with the legacy path in
  the first release after 2027-03-01.

---

## R10: Identity guard and baseline persistence (FR-031 to FR-035)

### R10 findings

It is unverified whether a legacy `_id` equals the v3 `reservationId` for
the same stay. Reservation custom-field writes are keyed by reservation
id (`api/custom_fields.py:200-260`) and at least one user publishes
door-lock codes through them. The reservations coordinator is constructed
fresh on every setup (`__init__.py:283-289`) and persists nothing.

### R10 decision

A confirmation-code → reservation-id baseline, persisted with
`homeassistant.helpers.storage.Store` at
`guesty.{entry_id}.reservations_identity` (version 1), rewritten
wholesale after each successful unambiguous refresh, capped at 5,000
entries by earliest check-out. Any code whose mapping differs from the
baseline aborts the refresh, retains last-known-good data and blocks
writes. Writes fail closed until a baseline exists.

### R10 rationale for Store over `entry.data`

Writing the baseline into `entry.data` through `async_update_entry()`
fires the config entry's update listener, which this integration uses to
reload on options changes. A per-refresh write would therefore reload the
integration every 15 minutes. `Store` is Home Assistant's idiomatic
per-entry persistence, survives restarts and reloads as FR-031a requires,
and is removed with the entry.

### R10 why it catches the swap case

The comparison is per code, not per id. A wholesale re-key that exchanges
two codes between two still-present ids produces two differing mappings
and aborts, which a "previous id disappeared" heuristic would have missed
(FR-032's explicit requirement).

### R10 alternatives considered

- **Comparing id sets and tolerating churn below a threshold.** Rejected:
  no threshold distinguishes a genuine turnover on a small listing from a
  re-key.
- **Verifying identity with an extra API call.** Rejected by FR-034: the
  guard must cost nothing, and the data needed is already in the
  response.

---

## R11: Write actions (FR-027 to FR-029, OQ-002)

### R11 findings

The guide replaces the catch-all `PUT /v1/reservations/{id}` with one
resource per operation, including
`PUT /reservations-v3/{reservationId}/notes` and
`PUT /reservations-v3/{reservationId}/status`. Today's note action is a
read-modify-write that appends to the existing `note` with a separator
(`api/actions.py:56-137`, `api/const.py:190-191`).

### R11 decision

Keep the read-modify-write shape, with the read moved to
`GET /reservations-v3?reservationIds={id}` and the write to the notes
resource. Keep today's validation and error construction unchanged
(`api/actions.py:389-470`), so a rejection names the resource and the
detail and is never reported as success.

### R11 rationale

- Whether the notes resource replaces or merges the note set is OQ-002
  and unverified. Reading first is correct under both answers; not
  reading is correct only under one.
- Because a replacing resource would delete any field omitted from the
  body, the read captures **every** writable note field and the write
  sends them all back, not just `other`.
- Merging note *objects* would still not justify dropping the read: the
  endpoint can preserve `notes.guest` while replacing the `notes.other`
  string. Only evidence that the server appends text with the required
  separator would make the read droppable, and that is not something the
  documentation states.
- The coordinator's cached reservation is **not** used as the read
  source: it can be a full poll interval stale, and writing a stale note
  back would silently destroy a note added in Guesty meanwhile.
- The mapping from legacy `note` to the v3 `notes` object's field is
  **[UNVERIFIED]**; `notes.other` is the assumed target.
- A write whose target is fallback-sourced goes to the legacy resource
  instead, by the owner's OQ-010 decision (R16). The v3 decisions above
  are unaffected: they describe the path every other reservation takes.

---

## R12: Request budget (NFR-001, SC-003)

| Per refresh | Legacy path | v3 path, warm cache |
| --- | --- | --- |
| Reservation page sequences | 2 (`api/client.py:277-287`) | 1 |
| Reservation pages | 2 × ⌈R/100⌉ | ⌈R/100⌉ |
| Guest lookups | 0 (embedded) | 0 (cache hits) |
| Financial lookups | 0 (embedded) | 0 (deprecated, R9) |

The v3 path is strictly cheaper than legacy in warm steady state, because
it replaces two account-wide sequences with one and adds nothing (NFR-001
first sentence, SC-003 first assertion).

Cold cache, or TTL expiry, adds at most one lookup per distinct guest
needing a refresh, capped at 50 per refresh and issued only for
reservations attributed to tracked listings. A ten-reservation,
ten-distinct-guest fixture therefore costs exactly the search pages plus
ten, which SC-003 asserts as a pinned number rather than a comparison.
With a 24-hour TTL and a 15-minute poll, the steady-state amortised cost
is one lookup per guest per 96 refreshes.

Fallback lookups (R13) are outside this table: a steady state has no
missing reservations, so their steady-state cost is zero. When
reservations *are* missing, they add at most
`MAX_LEGACY_FALLBACK_LOOKUPS_PER_REFRESH` (20) requests to a refresh, and
at most one per candidate per `LEGACY_FALLBACK_RECHECK_SECONDS` (3600)
thereafter — one per candidate per four refreshes on a 15-minute poll.
Spec FR-053 bounds them and NFR-001 excludes them from the warm-cache
comparison.

---

## R13: Legacy not-found fallback (FR-050 to FR-057)

### R13 findings

Guesty Support, replying to an external user on [#223][issue-223],
stated that their migration is still backfilling reservations into v3,
that a reservation absent from v3 may therefore simply not be there yet,
and that `GET /v1/reservations/{reservationId}` should be used to
retrieve it in that case. This post-dates the merged spec, plan and
tasks, all of which treated a v3 absence as equivalent to deletion
(R5, now corrected).

### R13 decision

Add a `ReservationFallbackSource` collaborator **inside** the
`ReservationSource` seam, used only by the v3 source. For each
previously known reservation that is absent from a v3 result and whose
stay still overlaps the window, look it up by identifier on the legacy
resource; publish a successful result as a **fallback-sourced**
reservation whose writes route to the legacy resource (R16); treat a
legacy not-found as confirmed absence and retire the candidate; bound
the work with a per-refresh cap of 20 and a per-candidate re-check
interval of 3600 seconds; self-disable for the
session on a retirement-shaped answer. The full design, including error
handling, caching and the write routing, is in `plan.md`
§"Legacy not-found fallback" and §"Write actions".

### R13 rationale

Without it, a live reservation Guesty has not finished migrating
disappears from calendars and sensors with no error anywhere, and a
door-lock automation that depends on it never fires. Silent loss of a
live booking is worse than any cost this adds. The design is constrained
so the cure cannot be worse than the disease: by-identifier only (never
a legacy search), only for reservations already known, bounded per
refresh, negatively cached so a genuinely deleted reservation is looked
up once and then dropped, and never writing an identifier that did not
come from v3 to a v3 resource — such writes go to the legacy resource
instead (R16).

### R13 alternatives considered

- **Treat absence as deletion** (the previous position, and the sibling
  bridge's). Rejected: contradicted by Guesty Support and silently
  lossy.
- **Fall back to a legacy *search* when the v3 result looks short.**
  Rejected: unbounded, defeats the whole migration, and cannot tell
  which reservation is missing.
- **Keep publishing the last known reservation on any absence, with no
  legacy check.** Rejected: masks genuine cancellations indefinitely,
  which is the symmetric failure FR-052 exists to prevent.
- **Persist the confirmed-absent set.** Rejected: a stale persisted
  absence would permanently hide a reservation that returns; re-learning
  from the identity baseline after a restart costs at most one bounded
  round.

---

## R14: Live verification is available and deferred (FR-041)

### R14 findings

The external user who obtained Support's reply has offered to exercise
the migration against their own Guesty account, beginning with test
reservations rather than live bookings ([#223][issue-223]). Support's
reply additionally supplies two rules this project cannot derive from
the public documentation:

1. The identifier used against a v3 resource **must come from a v3
   response**. A channel confirmation code, an `originId` or another
   external reference returns "Reservation not found" (spec FR-031e).
2. A v3 not-found is **not** proof of deletion while the backfill runs
   (R13).

Support also tested exactly **one** reservation whose legacy and v3
`_id` matched and explicitly declined to confirm that this holds for
every reservation — which is precisely the gap the identity guard
exists to cover, and is why R10 is unchanged by this news.

### R14 decision

Change the posture of this document and of the specification from "live
verification is impossible" to "**available, deferred to the pre-release
gate**". Do not write the test plan yet: there is nothing to exercise
until an implementation exists, and a test plan written against a design
rather than an implementation would be rewritten anyway. Nothing in the
checklist below is downgraded or removed on the strength of
availability; the checklist is the test plan's input.

An audit at the analyze stage confirms the integration never uses a
confirmation code as a lookup key anywhere in this design — it appears
only as a sensor attribute and as the identity baseline's map key, both
cross-checks — so rule 1 is a guard-rail to keep, not a defect to fix.

---

## R15: Deprecation-notice mechanism (OQ-009)

### R15 decision

Ship **both** surfaces: a non-fixable Home Assistant repairs issue
(`is_fixable=False`, severity `warning`, translation key
`financial_sensors_deprecated`) raised exactly once when the v3 path
starts with the three entities present, **and** a WARNING log line
carrying the same information.

### R15 rationale

FR-046 calls the notice *user-visible*, and a log line is not visible to
the dashboard-only user that risk R-010 is about; the repairs issue is
the only Home Assistant surface that reaches them. The log line costs
nothing and is what a support request will quote. The objection to new
surface is bounded: one non-fixable issue and one translation string,
both deleted with the sensors under FR-048. Dismissal is acceptable
because the notice is informational — the sensors remain `unavailable`
whether or not it is dismissed. This resolves OQ-009 and unblocks the
deprecation tasks.

---

## R16: Legacy write routing for fallback-sourced reservations (OQ-010)

### R16 findings

R13 restores a missing reservation's *visibility*, but the reservation
that motivated it is the subject of a door-lock automation on
[#223][issue-223] that writes a four-digit access code to a reservation
custom field. The merged artifacts refused every write against a
fallback-sourced reservation, so that automation would fail — loudly,
but it would still leave the arriving guest without a code. Reservation
custom fields are already a v3 resource
(`/reservations-v3/{id}/custom-fields`, `api/const.py:142-143`) with no
legacy counterpart in this codebase, while the note and status writes
used the legacy catch-all `PUT /v1/reservations/{id}` in the previous
release (`api/actions.py:84-91`, `115-120`, `364-369`).

### R16 decision

**Owner decision, 2026-11-13**: route a write whose target is
fallback-sourced to the **legacy** write resource for as long as that
resource exists, rather than refusing it. This is permitted exception 3
of FR-001 and is specified by FR-058 to FR-063:

- it applies only to a target that is fallback-sourced at write time,
  never to a reservation v3 returns;
- it covers the three write operations the integration offers — note,
  status and the reservation branch of the custom-field service;
- the identity guard runs first and unchanged, and the confirmation code
  remains a cross-check, never a lookup key on either generation of
  resource;
- it self-disables with the fallback, refusing with an error that names
  the 2027-03-01 retirement;
- every routed write is logged once and counted by operation and outcome
  in both diagnostics surfaces;
- a failure raises the usual actionable error, is never reported as
  success, and is retried neither automatically nor against v3.

For the note and status operations the legacy branch is the previous
release's request, which FR-040 already requires to stay working and
tested. For custom fields there is no legacy counterpart to return to,
so the legacy catch-all is assumed to carry the field value; the body
shape is **[UNVERIFIED]** and is spec OQ-011.

### R16 rationale

The condition is transient and entirely vendor-side: Guesty's own
backfill. During that window the legacy resource is expected to answer —
it is the same window that makes R13's read fallback work at all — so
refusing the write trades a working door lock for a shorter exception
list. The routing does not weaken Support's rule, which is about calling
*v3* with a non-v3 identifier: a legacy identifier is used only against
a legacy resource. The exposure is bounded by the same mechanisms R13
already has, because the set of legacy-writable reservations is exactly
the set the fallback is serving.

### R16 alternatives considered

- **Refuse the write** (the previous specification, FR-055 as merged).
  Rejected by the owner: a loud refusal is honest but still leaves the
  guest locked out, and the whole point of the fallback is that
  automation.
- **Refuse only the custom-field write, route notes and status.**
  Rejected: the custom-field write is the door-lock case, so it is the
  one that matters most; and splitting the rule would make the exception
  list harder to state and to test.
- **Write to v3 anyway and tolerate the error.** Rejected outright: it
  violates Support's rule, produces "Reservation not found" and risks
  writing against a wrong identity, which is risk R-001.
- **Queue the write and replay it when the reservation appears in v3.**
  Rejected: a persisted write queue is new durable state carrying user
  data, with no bound on how long a backfill takes, and a door code
  delivered an unknown time later is not a working door code.

---

## Pre-release live-verification checklist

FR-041 requires every unverifiable assumption to be confirmed against a
live Guesty account before this feature is declared stable, and SC-011
requires every assumption in the specification to appear here. Each item
below is reproduced from the specification's Assumptions section or added
by a decision in this document.

**Status: this checklist is now executable.** An external user has
offered their Guesty account, starting with test reservations (R14), so
these items are no longer hypothetical exercises — they are the input to
a concrete pre-release test plan that is written once an implementation
exists. Until that run happens, every item remains unverified.

Two rules from Guesty Support govern how the run is performed, not
merely what it checks:

- Always take the reservation identifier used for a v3 call from a **v3
  response**; never a confirmation code, an `originId` or any other
  legacy or external reference (FR-031e). A tester seeing "Reservation
  not found" after using a channel code is observing this rule, not a
  bug in the integration.
- Before exercising `PUT /v1/reservations-v3/{reservationId}/notes`, do
  a v3 GET for the same identifier first. If that GET says not found,
  the reservation may simply not be backfilled yet — retrieve it with
  `GET /v1/reservations/{reservationId}` and record it as a fallback
  case (R13), not as a failure.

### Identity and continuity

1. A legacy reservation `_id` equals the v3 `reservationId` for the same
   stay, and the FR-031 guard never trips on a real account.
   **Acceptance**: at least one confirmation code appears on both paths
   for the same stay, and every such code maps to the same identifier on
   both. An empty or non-overlapping comparison does not confirm this
   item.
2. The confirmation code is stable across both APIs and unique enough
   among an account's active reservations to serve as the cross-check.
3. Reservation custom-field ids and values returned by the already
   migrated custom-fields resource are unaffected by this change.

### Search resource

1. The curated search is `GET /reservations-v3/search`; it takes
   bracketed filters, supports `sort`, `skip` and `limit` with a maximum
   limit of 100, and returns `{results, pagination:{skip, limit,
   hasMore}}` with no total count.
2. `filter[checkOut][gte]` and `filter[checkIn][lte]` can be **combined**
   and are **ANDed** (R3). If not, adopt the R3 two-query fallback.
3. Date filters compare listing-localized `YYYY-MM-DD` values, so the
   one-day pad is required and sufficient.
4. An unsupported filter, operator or sort returns HTTP 400 rather than
   being ignored.
5. No field projection is supported, and `status` is always present on a
   result.
6. A page is never shorter than the requested limit while `hasMore` is
   true, and `sort=_id` is strictly ascending.
7. `filter[status][in]` accepts `completed` alongside the other
   configured statuses (FR-003a).
8. The in-window page count for a large account stays within the
   200-page cap and within the rate budget on a 15-minute poll (OQ-008).

### Result shape

1. Every result carries a non-empty, chronological `stay[]`, each segment
   carrying `listingId` (= `unitId ?? unitTypeId`), an optional
   `parentListingId`, UTC check-in/check-out, localized dates and a guest
   count.
2. There is no top-level `listingId`, `unitId`, `unitTypeId` or `mtl`,
   and no `lastUpdatedAt`.
3. The result carries `guestId`, `confirmationCode`, `source`, `notes`
   and `customFields`, and `customFields` is absent rather than empty
   when no value is set.
4. The status set includes `confirmed`, `checked_in`, `checked_out`,
   `completed`, `canceled`, `inquiry`, `reserved` and `alteration`.
5. `plannedArrival` and `plannedDeparture` are available in some form
   (OQ-003). **If they are not, their loss is a breaking change under
   NFR-004 and must be declared, not absorbed.**
6. `nightsCount` is not returned and must be derived from the localized
   dates.

### Listing filter (only if OQ-008 is reopened)

1. The listing filter matches only the first stay segment.
2. A comma-joined list of listing ids is accepted and a 50-id chunk is
   not rejected for URL length (OQ-007).

### Guests

1. A guest record is retrievable by guest identifier from
   `GET /guests-crud/{id}` with `fields=fullName phone email`, and
   supplies values equivalent to today's embedded guest object (OQ-004).
2. A deleted or unknown guest id returns a not-found response rather than
   an error that would fail the refresh.

### Write resources

1. `PUT /reservations-v3/{reservationId}/notes` accepts the note content
   this integration writes, and its request body shape is as assumed
   (`notes.other`).
2. Whether that resource replaces the full note set, which decides
   whether the read-before-write can be dropped (OQ-002).
3. `GET /reservations-v3?reservationIds={id}` returns the current note
   text, which the read-before-write depends on.
4. `PUT /reservations-v3/{reservationId}/status` accepts `checked_in` and
   `checked_out` as this integration uses them today.

### Legacy write routing (R16)

1. The legacy catch-all `PUT /v1/reservations/{reservationId}` still
   accepts a note write and a status write for a reservation that is
   absent from v3, up to the 2027-03-01 retirement. **Acceptance**: a
   fallback case from "Legacy not-found fallback" item 1 accepts both
   writes, or the item is recorded as "not observed".
2. Which legacy resource and request body carries a **reservation
   custom-field** write, given that the custom-field resource this
   integration uses is v3-only (spec OQ-011). **Acceptance**: a value
   written through the assumed legacy body is read back unchanged,
   including a leading zero on a numeric-looking code. If it is refuted,
   FR-059's recorded fallback applies: the custom-field branch reverts
   to a loud refusal.
3. A write accepted by the legacy resource for a not-yet-backfilled
   reservation is carried into v3 by the backfill rather than lost
   (spec R-013). **Acceptance**: the written value is still present once
   the same reservation appears in the v3 result.

### Financials

1. **Not applicable.** The guest-folio assumption recorded in the
   specification is void: OQ-001 was decided in favour of deprecating the
   three financial sensors, and `GET /guest-folio/overview` is not
   integrated (R9). It is listed here only so that no specification
   assumption is silently dropped from this checklist.

### Group reservations

1. Whether a group booking is one reservation with segments across
   listings or several reservations under a group id (OQ-006). Either is
   handled; the answer only needs documenting.

### Legacy not-found fallback

1. A reservation present on the legacy path is genuinely absent from v3
   on a real account — that is, the backfill gap Support describes is
   observable at all. **Acceptance**: at least one identifier returns a
   result from `GET /v1/reservations/{id}` and not-found from the v3
   search and by-id read, or the item is recorded as "not observed".
2. `GET /v1/reservations/{reservationId}` accepts an identifier that was
   originally learned from a v3 response. Support's rule constrains the
   v3 direction only; the reverse is assumed, not stated.
3. A reservation that does not exist and a resource that has been
   retired are distinguishable from the legacy response alone (FR-056).
   If they are not, confirm that the implementation's
   bias toward self-disabling is the behaviour observed.
4. A reservation retrieved through the fallback maps to the same
   attribute values the v3 path would produce, so a fallback-sourced
   reservation is not observably different on the sensor.
5. Guesty's backfill actually completes for the observed reservations,
   rather than leaving them permanently absent from v3 (spec R-012).

### How the checklist is exercised

FR-042 diagnostics make most items checkable from a user's debug log
without a test account: pages per resource, counts by raw status, the
stay-segment histogram, attribution and skip counts, guest cache
behaviour, the identity match count, the per-exception counts of
permitted legacy calls, and the fallback counters. Items 2 and 3 of
"Write resources", and all three items of "Legacy write routing", need a
deliberate action invocation on a real account; the last two also need a
reservation that is genuinely mid-backfill, so "not observed" is a valid
recorded outcome for them as well.
Items 1 and 3 of "Legacy not-found fallback" need a real account **mid
backfill**: a reservation genuinely missing from v3 cannot be
conjured, so "not observed" is a valid recorded outcome for those two,
and item 5 can only be answered by watching an observed case over
time. Items 2 and 4 are **not** exempt. Both can be checked with any
reservation present in both APIs — item 2 by confirming a v3
identifier is accepted by the legacy by-id resource, item 4 by
comparing the attributes produced from the legacy document with those
produced from the v3 document for the same reservation — so both are
release gates with a pass or fail outcome (T127). Item 1
of "Identity and continuity" is **not** confirmed merely by two
successful refreshes: if the legacy and v3 results share no confirmation
codes, or either is empty, the guard compares nothing and cannot trip.
Confirmation requires a non-zero overlap of confirmation codes covering
the same stays on both paths, and equality of the reservation
identifiers those codes map to.

[guesty-migration]: https://open-api-docs.guesty.com/docs/reservation-migration
[guesty-changelog]: https://open-api-docs.guesty.com/changelog/2026-11-05
[issue-223]: https://github.com/tykeal/homeassistant-guesty/issues/223

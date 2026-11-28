<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Guesty

A [Home Assistant](https://www.home-assistant.io/) custom integration
for the [Guesty](https://www.guesty.com/) property management platform.

## Overview

This integration connects Home Assistant to the Guesty Open API using
OAuth 2.0 Client Credentials authentication. It provides property
management automation capabilities for short-term rental operators.

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "Guesty" and install
3. Restart Home Assistant
4. Go to Settings → Devices & Services → Add Integration → Guesty

### Manual

1. Copy `custom_components/guesty` to your HA `custom_components/`
   directory
2. Restart Home Assistant
3. Go to Settings → Devices & Services → Add Integration → Guesty

## Configuration

You will need Guesty API credentials (Client ID and Client Secret)
from the Guesty developer portal.

## Reservations: the v3 migration

Guesty is replacing the reservation resources this integration was
originally built on. Guesty has announced that the legacy Open API
reservation endpoints are **retired on 2027-03-01**, after which they
stop returning data.

From this release the integration talks to the **v3 reservation API
by default**, on new and existing installations alike. Nothing is
migrated and no entity is renamed: an existing entry simply picks up
the new default the next time it loads.

> **v3 support has not yet been verified against a live Guesty
> account.** It is covered by an extensive test suite built from
> Guesty's published API documentation, but no part of it has been
> exercised against real Guesty data yet. If something looks wrong,
> roll back (below) and please open an issue with the details.

### The `reservations_api` option

Open Settings → Devices & Services → Guesty → **Configure** →
**Reservations API** to change only the reservation API, without
fetching listings or changing any other options:

| Value | UI label | Meaning |
| --- | --- | --- |
| `v3` *(default)* | Reservations v3 API | The v3 reservation API. |
| `legacy` | Legacy reservations API | Legacy endpoints, until 2027-03-01. |

Changing the option reloads the entry.

### Rolling back

If v3 misbehaves for you, the rollback is immediate and complete:

1. Settings → Devices & Services → Guesty → **Configure**
2. Choose **Reservations API**
3. Set **Reservations API** to **Legacy reservations API**
4. Submit — the entry reloads itself

This path needs no Guesty connectivity to save the selection and
preserves your listing selection, tags, polling intervals and all
other options exactly. If no listing selection was previously saved,
new listings will continue to be discovered automatically.

To change listings or polling instead, choose **Listings and polling**
from **Configure**. This retains the **Filter by Tags** →
**Select Listings** → **Polling Configuration** wizard, including its
Reservations API field. Review the listing selection before saving:

- Listings excluded by the tag filter are not offered and are
  dropped from the saved selection.
- Without a saved selection, all currently returned listings are
  preselected. Saving pins that list; future listings need to be
  selected by running this wizard again.
- Any listing left unticked has its devices and entities removed.

This works even if the entry failed to start at all. When setup has
not completed, listings cannot be fetched, so
**Configure** goes straight to a single **Reservations API** step —
your listings and intervals are kept exactly as they were — so a
broken first v3 refresh cannot lock you out of the rollback.

Your entity IDs, history and automations are unaffected either way.
While on `legacy` the integration logs a warning on every start
naming the retirement date, so a temporary rollback cannot quietly
become permanent.

**The rollback expires.** On 2027-03-01 the legacy endpoints stop
working, and after that `legacy` is not a working configuration —
reservation reads fail and reservation writes refuse with an error
naming the retirement. Treat `legacy` as a way to buy time to report
a problem, not as a long-term setting.

### Reservation data is published only when verified

Both reservation APIs are checked record by record, and only data that
can be verified is published or used:

- A reservation with a missing or unrecognized status is left out.
  A v3 reservation is also left out when it has no stay segment with a
  listing and valid local check-in and check-out dates. A legacy
  reservation is left out unless it has a listing and check-in and
  check-out times with an explicit UTC offset, check-in before
  check-out; a time without an offset is rejected as unverified
  (`unverified_instant`). The rest of the refresh is still published. A
  warning names the reason and the reservation ID once per Home
  Assistant start or entry reload, and again if the problem goes away
  and later returns; beyond 256 exclusions the rest are summarized in
  one warning. A malformed reservation ID or page still fails the whole
  refresh.
- A v3 multi-unit reservation with some invalid stay segments publishes
  only its verified segments, and is never a target for reservation
  writes until every segment verifies.
- For v3 reservations, local dates come only from Guesty's local
  check-in and check-out dates. A UTC timestamp's calendar date is no
  longer used as a local date; a single-segment reservation with only
  timestamps is left out.
- **Unverified listing timezones**: a listing without a timezone that
  Home Assistant can load is shown by date only. No exact time is
  derived, a stay on its checkout day has an uncertain (`unknown`)
  status, and a stay is shown only when it is within the configured
  date window for every possible local date. A repair notice per
  listing clears once the timezone is set in Guesty.
- Stays that have ended are marked `checked_out` before the ten-item
  `upcoming` limit is applied, so they no longer crowd out future
  stays. A date-only stay on its checkout day is `unknown` rather than
  claimed occupied.

**Stale display after a failed refresh**: if a refresh fails, the
reservation sensors stay available and show the last verified data,
re-evaluated at the current time, with the attribute `stale: true` and
`last_verified_at` (when that data was last verified). Each `upcoming`
item also carries `stale`. Stale data is never treated as reverified
and never authorizes a write. A reservation whose fallback lookup
failed is marked `stale` individually. With no verified data the
sensors are unavailable.

The diagnostics download has a `validation` block with counts by
reason and up to 100 rejected or partially valid reservation IDs, never
codes, guest data or other payload values.

### Reservation writes are fail-closed

Writing to a reservation — adding a note, changing a status, setting
a reservation custom field — acts on a specific booking, so the
integration refuses to do it unless it can prove the two API
generations identify that booking the same way.

On the first start after upgrading, the integration performs a
single one-time read of the legacy reservation API to establish that
proof. If it cannot (for example the legacy read fails, or no
reservation is in common), reservations are still published normally
but **every reservation write is refused** and a repair notice
explains why. The notice clears by itself as soon as a later refresh
provides the evidence. Reads are never blocked by this — only
writes, and only until continuity is confirmed.

If an otherwise eligible reservation write targets a booking missing
from the current identity cache, the integration may refresh the
configured reservation source once and check again. The write still
requires current identity and routing evidence; a failed refresh or
insufficient evidence refuses it without sending a write. A refresh
cannot guarantee that a newly created booking is already indexed by
Guesty. A recovered write is sent at most once and is not automatically
retried. Listing custom-field writes do not trigger this refresh.

Two further refusals protect writes while the integration cannot prove
which booking a confirmation code belongs to:

- **Revalidating**: after an upgrade, after every Home Assistant
  restart or entry reload, and after the reservation history window
  (`past_days`) is increased, every reservation write is refused until
  the next successful reservation refresh has been verified and
  stored. Wait for one refresh, then try again.
- **Identity evidence unavailable**: the integration keeps a bounded
  record of the confirmation codes of reservations it could not
  verify, so a rejected reservation can never be mistaken for another.
  If that record cannot be saved, every write is refused until a later
  save succeeds. If it fills up or cannot be trusted, every write is
  refused and a repair notice asks you to correct the rejected
  reservations in Guesty and then remove and re-add the integration
  entry. A separate warning notice appears when the record is nearly
  full.

Neither refusal starts the one-time refresh described above, and
neither sends any request to Guesty.

If you need writes back immediately, roll back to `legacy`.

## Breaking changes

### Reservation financial sensors are deprecated

The v3 reservation API does not return reservation money. These three
sensors are therefore **no longer created** on the v3 path:

- `..._reservation_total` (`total_paid`)
- `..._reservation_balance` (`balance_due`)
- `..._reservation_currency` (`currency`)

**There is no replacement data source.** Guesty does not expose this
data on the v3 reservation resource, so the integration cannot
provide it by any other route.

Their entity registry entries are deliberately **kept**, so:

- the entity IDs do not change, and nothing else on your dashboards
  is renamed or re-suffixed;
- Home Assistant reports them as **unavailable** instead of showing
  the last value they ever had.

That is intentional. A financial sensor that confidently shows a
stale number is worse than one that is visibly unavailable.

A repair notice and a warning in the log tell you once, per
installation, per Home Assistant start.

Setting **Reservations API** to `legacy` (see
[Rolling back](#rolling-back) for the full step sequence) restores
all three to the same entity IDs with live values — until
2027-03-01, after which
reservation financial data is no longer available to this
integration at all. The sensors, their strings and the notice are
removed entirely in a release on or after that date.

### Everything else is unchanged

Every other reservation entity keeps its entity ID, its state and its
attribute keys on both paths; that equivalence is enforced by tests.

## License

This project is licensed under the Apache License 2.0. See
[LICENSE](LICENSE) for details.

## Release notes

Add a [reno](https://docs.openstack.org/reno/latest/) note in
`releasenotes/notes/` with each user-facing change:

```bash
uv run --only-group releasenotes reno new <slug>
uv run --only-group releasenotes reno lint
```

Keep only the sections that apply and write entries in Markdown.
Pull requests labeled `breaking-change` or titled with a conventional
breaking marker such as `Feat!: ...` must include an upgrade entry with
operator guidance. The draft GitHub release renders notes added since
the latest release tag above the pull-request changelog. Preview that
content locally after adding a new note to Git's index (reno uses Git
history to discover note files):

```bash
uv run --only-group releasenotes python scripts/render_release_notes.py
```

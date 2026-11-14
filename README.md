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

Settings → Devices & Services → Guesty → **Configure** →
**Reservations API**:

| Value | Meaning |
| --- | --- |
| `v3` *(default)* | Use the v3 reservation API. |
| `legacy` | Use the legacy endpoints, until 2027-03-01. |

Changing the option reloads the entry.

### Rolling back

If v3 misbehaves for you, the rollback is immediate and complete:

1. Settings → Devices & Services → Guesty → **Configure**
2. Set **Reservations API** to **legacy**
3. Submit — the entry reloads itself

Your entity IDs, history and automations are unaffected either way.
While on `legacy` the integration logs a warning on every start
naming the retirement date, so a temporary rollback cannot quietly
become permanent.

**The rollback expires.** On 2027-03-01 the legacy endpoints stop
working, and after that `legacy` is not a working configuration —
reservation reads fail and reservation writes refuse with an error
naming the retirement. Treat `legacy` as a way to buy time to report
a problem, not as a long-term setting.

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

Setting **Reservations API** to `legacy` restores all three to the
same entity IDs with live values — until 2027-03-01, after which
reservation financial data is no longer available to this
integration at all. The sensors, their strings and the notice are
removed entirely in a release on or after that date.

### Everything else is unchanged

Every other reservation entity keeps its entity ID, its state and its
attribute keys on both paths; that equivalence is enforced by tests.

## License

This project is licensed under the Apache License 2.0. See
[LICENSE](LICENSE) for details.

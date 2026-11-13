<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Reservations API Option (options flow)

**Consumer**: the existing options flow in
`custom_components/guesty/config_flow.py`
**Purpose**: let an operator move an entry between the v3 and legacy
reservation paths without reconfiguring anything else (FR-035)

## Option

| Item | Value |
| --- | --- |
| Key | `reservations_api` (`CONF_RESERVATIONS_API`) |
| Storage | `entry.options` |
| Type | selector with two values |
| Values | `v3`, `legacy` |
| Default | `v3` (FR-037) |
| Label | "Reservations API" |
| Description | legacy is a temporary rollback path, retiring 2027-03-01 |

Both values are localized in `strings.json` and every `translations/*`
file the repository already ships, so no language regresses.

## Semantics

- **An existing entry is upgraded**: no value is stored, so the default
  applies; there is no migration step and no reconfiguration prompt
  (FR-037).
- **The value is changed**: the update listener must reload the entry.
  Today's listener reloads only on a listing-selection change and
  returns early when the polling intervals are unchanged
  (`__init__.py:723-770`), so it is extended to track this option; a
  regression test asserts that flipping only this option reloads.
- **After a reload**: no entity is added, removed, renamed or
  re-created, and unique ids are untouched (FR-038, SC-008).
- **The first refresh after a switch**: a reload discards the
  coordinator (`__init__.py:818-833`) and setup builds a fresh one whose
  first refresh must succeed before the platforms load
  (`__init__.py:283-289`). There is therefore **no** carried-over
  snapshot: if the newly selected path fails, or the identity guard
  trips, setup is retried by Home Assistant and the entities are
  unavailable until a refresh succeeds. A failed-first-refresh test
  pins this behaviour. Only refreshes *after* a successful setup retain
  last-known-good data.
- **`legacy` is selected**: reservation reads **and** both write actions
  use the legacy resources together — the option is never split across
  read and write (FR-036) — and a WARNING naming the 2027-03-01
  retirement is logged once per setup (FR-039).
- **`legacy` fails in a retirement-shaped way (404 or 410)**: the error
  tells the user to set the option back to `v3` (FR-039).
- **An invalid value is stored**: it is treated as `v3`, with a
  WARNING.

The option controls the reservation path only. It does not affect
listings, calendars, custom fields or notifications, all of which are
unchanged by this feature.

## Retirement

The option, the legacy source module, `RESERVATIONS_ENDPOINT`,
`RESERVATIONS_FIELDS` and the three deprecated financial sensors are
removed together in the first release after 2027-03-01. At that point an
entry still storing `legacy` falls back to `v3` with a WARNING; no
breaking migration is needed because the key simply stops being read.

## Contract tests

- the option appears in the options flow with both values and defaults
  to `v3`;
- an entry created before this feature resolves to `v3` without
  migration;
- changing the value reloads the entry and the entity registry is
  byte-identical before and after (SC-008);
- `legacy` routes both reads and writes to the legacy resources;
- `legacy` logs the retirement warning once per setup;
- a 404 from the legacy resource produces the rollback-instruction
  message;
- an unparsable stored value resolves to `v3`.

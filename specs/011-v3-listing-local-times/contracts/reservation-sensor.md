<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Home Assistant Reservation Sensor Contract

This is the public state/attribute contract of the existing per-listing
reservation sensor. Feature 011 keeps entity identity, state priority for
known lifecycle statuses, and existing attribute names. It adds certainty
fields and permits exact time values to be `null` when V3 timing cannot be
verified.

## Selected reservation

- **`reservation_id`** (string or `null`): existing reservation identifier;
  never derived from time.
- **`check_in`** (ISO instant or `null`): current verified exact instant
  only.
- **`check_out`** (ISO instant or `null`): current verified exact instant
  only.
- **`check_in_local`** (known calendar date or `null`): keep the current
  valid localized date even if `check_in` is null. For an eligible Feature
  010 single-raw-segment fallback only, use the written date component of
  the corresponding valid top-level timestamp when localized dates are
  absent; do not offset-convert it.
- **`check_out_local`** (known calendar date or `null`): keep the current
  valid localized date even if `check_out` is null. For an eligible Feature
  010 single-raw-segment fallback only, use the written date component of
  the corresponding valid top-level timestamp when localized dates are
  absent; do not offset-convert it.
- **`timing_status`** (`known` or `unknown`): `known` only when both current
  exact endpoints are verified.
- **`reservation_status`**: source lifecycle status when temporal status is
  unknown. This separates Guesty's lifecycle value from an unknown
  time-dependent segment classification.
- **`planned_arrival`** (string or `null`): existing separate field; never a
  timing override.
- **`planned_departure`** (string or `null`): existing separate field; never
  a timing override.
- **Other existing attributes**: preserve existing values/nullability. No
  entity ID, reservation identity, write target, or key renames.

When a time-dependent per-listing classification cannot be calculated, the
Home Assistant state is `unknown`; `reservation_status` retains the source
lifecycle value. Existing selection priority based on known source lifecycle
status remains unchanged. Legacy records retain their current state behavior.

## Upcoming reservation summary

Each summary preserves the current `reservation_id`, `guest_name`, and
`status` values and includes `check_in`, `check_out`, `check_in_local`,
`check_out_local`, and `timing_status` with the same null/date semantics as
the selected reservation. When temporal classification is unknown,
`reservation_status` contains the source lifecycle status. Summary status
retains its existing per-listing projection meaning; `reservation_status`
keeps the original source lifecycle value when timing classification is
unknown.

## Non-contractual internals

Raw V3 suffix values, timezone provenance, raw segment count/position,
candidate hints, and identity baseline contents are not user-facing sensor
attributes. A date extracted from a valid top-level timestamp is date-only;
it never supplies a published exact instant. Do not publish internal
candidate or identity data as identity, fallback, or planned-time overrides.

# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Sensor platform for Guesty listings and reservations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from custom_components.guesty.api.models import (
    GuestyListing,
    GuestyMoney,
    GuestyReservation,
)
from custom_components.guesty.const import DOMAIN
from custom_components.guesty.entity import GuestyEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.guesty.coordinator import (
        ListingsCoordinator,
        ReservationsCoordinator,
    )

_LOGGER = logging.getLogger(__name__)

# Priority order for reservation status selection (FR-006)
_STATUS_PRIORITY: dict[str, int] = {
    "checked_in": 0,
    "confirmed": 1,
    "checked_out": 2,
    "canceled": 3,
}


@dataclass(frozen=True, kw_only=True)
class GuestyListingSensorEntityDescription(SensorEntityDescription):
    """Describe a Guesty listing sensor with a value function.

    Attributes:
        value_fn: Callable extracting the sensor value from a listing.
    """

    value_fn: Callable[[GuestyListing], StateType]


LISTING_SENSOR_DESCRIPTIONS: tuple[GuestyListingSensorEntityDescription, ...] = (
    GuestyListingSensorEntityDescription(
        key="status",
        translation_key="listing_status",
        entity_category=None,
        value_fn=lambda listing: listing.status,
    ),
    GuestyListingSensorEntityDescription(
        key="name",
        translation_key="listing_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.title,
    ),
    GuestyListingSensorEntityDescription(
        key="nickname",
        translation_key="listing_nickname",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.nickname,
    ),
    GuestyListingSensorEntityDescription(
        key="address",
        translation_key="listing_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: (
            listing.address.formatted() if listing.address else None
        ),
    ),
    GuestyListingSensorEntityDescription(
        key="property_type",
        translation_key="listing_property_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.property_type,
    ),
    GuestyListingSensorEntityDescription(
        key="room_type",
        translation_key="listing_room_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.room_type,
    ),
    GuestyListingSensorEntityDescription(
        key="bedrooms",
        translation_key="listing_bedrooms",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.bedrooms,
    ),
    GuestyListingSensorEntityDescription(
        key="bathrooms",
        translation_key="listing_bathrooms",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.bathrooms,
    ),
    GuestyListingSensorEntityDescription(
        key="accommodates",
        translation_key="listing_accommodates",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.accommodates,
    ),
    GuestyListingSensorEntityDescription(
        key="timezone",
        translation_key="listing_timezone",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.timezone,
    ),
    GuestyListingSensorEntityDescription(
        key="check_in_time",
        translation_key="listing_check_in_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.check_in_time,
    ),
    GuestyListingSensorEntityDescription(
        key="check_out_time",
        translation_key="listing_check_out_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.check_out_time,
    ),
    GuestyListingSensorEntityDescription(
        key="tags",
        translation_key="listing_tags",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: ", ".join(listing.tags),
    ),
)


@dataclass(frozen=True, kw_only=True)
class GuestyFinancialSensorEntityDescription(SensorEntityDescription):
    """Describe a financial sensor with a value extraction function.

    Attributes:
        value_fn: Callable extracting the value from GuestyMoney.
    """

    value_fn: Callable[[GuestyMoney], StateType]


RESERVATION_FINANCIAL_DESCRIPTIONS: tuple[
    GuestyFinancialSensorEntityDescription, ...
] = (
    GuestyFinancialSensorEntityDescription(
        key="reservation_total",
        translation_key="reservation_total",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda money: money.total_paid,
    ),
    GuestyFinancialSensorEntityDescription(
        key="reservation_balance",
        translation_key="reservation_balance",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda money: money.balance_due,
    ),
    GuestyFinancialSensorEntityDescription(
        key="reservation_currency",
        translation_key="reservation_currency",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda money: money.currency,
    ),
)


def create_custom_field_description(
    field_name: str,
    seen_slugs: dict[str, int] | None = None,
) -> GuestyListingSensorEntityDescription:
    """Create a sensor description for a Guesty custom field.

    Args:
        field_name: The custom field name from the listing.
        seen_slugs: Tracks slug usage to disambiguate collisions.

    Returns:
        A description whose key is ``custom_{slugified_name}``
        and whose ``value_fn`` extracts the specific field.
    """
    slug = slugify(field_name)
    if seen_slugs is not None:
        count = seen_slugs.get(slug, 0)
        seen_slugs[slug] = count + 1
        if count > 0:
            slug = f"{slug}_{count}"

    def _value_fn(listing: GuestyListing) -> StateType:
        """Extract the custom field value from the listing."""
        return listing.custom_fields.get(field_name)

    return GuestyListingSensorEntityDescription(
        key=f"custom_{slug}",
        translation_key="listing_custom_field",
        name=field_name,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_value_fn,
    )


class GuestyListingSensor(GuestyEntity, SensorEntity):
    """Sensor entity for a Guesty listing attribute.

    Uses the description's ``value_fn`` to extract the native
    value from the listing in the coordinator's data dict.

    Attributes:
        entity_description: The sensor entity description.
    """

    entity_description: GuestyListingSensorEntityDescription

    def __init__(
        self,
        coordinator: ListingsCoordinator,
        listing_id: str,
        entry: ConfigEntry,
        description: GuestyListingSensorEntityDescription,
    ) -> None:
        """Initialize the listing sensor.

        Args:
            coordinator: The listings coordinator.
            listing_id: The Guesty listing ID.
            entry: The config entry.
            description: The sensor entity description.
        """
        super().__init__(coordinator, listing_id, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{listing_id}_{description.key}"

    @property
    def native_value(self) -> StateType:
        """Return the sensor value from the listing.

        Returns:
            The sensor state value, or None if listing unavailable.
        """
        listing = self._listing
        if listing is None:
            return None
        return self.entity_description.value_fn(listing)

    @property
    def available(self) -> bool:
        """Return True only when the listing is present and not disappeared.

        Combines the parent coordinator health check with listing-level
        presence: the entity is unavailable if the coordinator itself is
        unhealthy, the listing ID is absent from coordinator data, or the
        listing ID is in the coordinator's disappeared set.

        Returns:
            True when the listing is available, False otherwise.
        """
        if not super().available:
            return False
        if self.coordinator.data is None:
            return False
        if self._listing_id in self.coordinator.disappeared_listing_ids:
            return False
        return self._listing_id in self.coordinator.data


def _select_reservation(
    reservations: list[GuestyReservation],
) -> GuestyReservation | None:
    """Select the highest-priority reservation per FR-006.

    Priority: checked_in > confirmed > checked_out > canceled.
    Unknown statuses are treated as lowest priority but still
    returned when they are the only reservation.

    Args:
        reservations: Sorted list of reservations for a listing.

    Returns:
        The highest-priority reservation, or None if empty.
    """
    if not reservations:
        return None

    best: GuestyReservation | None = None
    best_priority = len(_STATUS_PRIORITY) + 1

    for reservation in reservations:
        priority = _STATUS_PRIORITY.get(
            reservation.status,
            len(_STATUS_PRIORITY),
        )
        if priority < best_priority:
            best = reservation
            best_priority = priority

    return best


def _derive_state(reservation: GuestyReservation | None) -> str:
    """Derive sensor state string from the selected reservation.

    Maps ``confirmed`` status to ``awaiting_checkin`` per FR-006.
    Unknown statuses are passed through as-is per FR-025, with
    an informational log for observability.

    Args:
        reservation: The selected reservation, or None.

    Returns:
        The sensor state string.
    """
    if reservation is None:
        return "no_reservation"
    if reservation.status == "confirmed":
        return "awaiting_checkin"
    if reservation.status not in _STATUS_PRIORITY:
        _LOGGER.info(
            "Unrecognized reservation status '%s' for %s",
            reservation.status,
            reservation.id,
        )
    return reservation.status


def _build_attributes(
    reservation: GuestyReservation | None,
    all_reservations: list[GuestyReservation],
) -> dict[str, Any]:
    """Build extra_state_attributes for the reservation sensor.

    Args:
        reservation: The selected reservation, or None.
        all_reservations: All reservations for upcoming list.

    Returns:
        Dictionary of extra state attributes.
    """
    if reservation is None:
        return {
            "reservation_id": None,
            "check_in": None,
            "check_out": None,
            "check_in_local": None,
            "check_out_local": None,
            "planned_arrival": None,
            "planned_departure": None,
            "guest_name": None,
            "guest_phone": None,
            "guest_email": None,
            "confirmation_code": None,
            "guests_count": None,
            "nights_count": None,
            "source": None,
            "upcoming_reservations": [],
        }

    guest = reservation.guest
    upcoming = _build_upcoming(all_reservations, reservation)

    return {
        "reservation_id": reservation.id,
        "check_in": reservation.check_in.isoformat(),
        "check_out": reservation.check_out.isoformat(),
        "check_in_local": reservation.check_in_local,
        "check_out_local": reservation.check_out_local,
        "planned_arrival": reservation.planned_arrival,
        "planned_departure": reservation.planned_departure,
        "guest_name": guest.full_name if guest else None,
        "guest_phone": guest.phone if guest else None,
        "guest_email": guest.email if guest else None,
        "confirmation_code": reservation.confirmation_code,
        "guests_count": reservation.guests_count,
        "nights_count": reservation.nights_count,
        "source": reservation.source,
        "upcoming_reservations": upcoming,
    }


def _build_upcoming(
    reservations: list[GuestyReservation],
    selected: GuestyReservation,
) -> list[dict[str, Any]]:
    """Build upcoming reservations list, limited to 10 (FR-009).

    Excludes the currently selected reservation and only includes
    reservations with ``confirmed`` or ``checked_in`` status
    (i.e., actually upcoming or active, not past).

    Args:
        reservations: All reservations for this listing.
        selected: The currently selected reservation.

    Returns:
        List of upcoming reservation summary dicts.
    """
    _UPCOMING_STATUSES = {"confirmed", "checked_in"}
    upcoming: list[dict[str, Any]] = []
    for res in reservations:
        if res.id == selected.id:
            continue
        if res.status not in _UPCOMING_STATUSES:
            continue
        upcoming.append(
            {
                "reservation_id": res.id,
                "guest_name": (res.guest.full_name if res.guest else None),
                "check_in": res.check_in.isoformat(),
                "check_out": res.check_out.isoformat(),
                "status": res.status,
            }
        )
        if len(upcoming) >= 10:
            break
    return upcoming


def _build_listing_device_info(
    listings_coordinator: ListingsCoordinator,
    listing_id: str,
) -> DeviceInfo | None:
    """Build DeviceInfo linking to the listing device.

    Shared by reservation and financial sensors to keep
    device attachment logic in a single place.

    Args:
        listings_coordinator: The listings coordinator.
        listing_id: The Guesty listing ID.

    Returns:
        DeviceInfo with listing identifiers, or None.
    """
    if listings_coordinator.data is None:
        return None
    listing = listings_coordinator.data.get(listing_id)
    if listing is None:
        return None
    return DeviceInfo(
        identifiers={(DOMAIN, listing.id)},
        name=listing.title,
        manufacturer="Guesty",
        model=listing.property_type or "Listing",
    )


class GuestyReservationSensor(
    CoordinatorEntity["ReservationsCoordinator"],
    SensorEntity,
):
    """Sensor entity for reservation status on a listing.

    Uses priority selection to determine the current occupancy
    state and exposes rich attributes for the selected reservation.

    Attributes:
        _listing_id: The Guesty listing ID.
        _listings_coordinator: Reference for device_info.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ReservationsCoordinator,
        listings_coordinator: ListingsCoordinator,
        listing_id: str,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the reservation status sensor.

        Args:
            coordinator: The reservations coordinator.
            listings_coordinator: The listings coordinator.
            listing_id: The Guesty listing ID.
            entry: The config entry.
        """
        super().__init__(coordinator)
        self._listing_id = listing_id
        self._listings_coordinator = listings_coordinator
        self._attr_unique_id = f"{entry.unique_id}_{listing_id}_reservation_status"
        self._attr_translation_key = "reservation_status"

    @property
    def native_value(self) -> StateType:
        """Return the reservation status state string.

        Returns:
            The derived reservation state.
        """
        reservations = self._reservations
        selected = _select_reservation(reservations)
        return _derive_state(selected)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rich attributes for the selected reservation.

        Returns:
            Dictionary of reservation attributes.
        """
        reservations = self._reservations
        selected = _select_reservation(reservations)
        return _build_attributes(selected, reservations)

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info linking to the listing device.

        Returns:
            DeviceInfo with listing identifiers.
        """
        return _build_listing_device_info(
            self._listings_coordinator,
            self._listing_id,
        )

    @property
    def available(self) -> bool:
        """Return True when coordinator is healthy and listing exists.

        Checks both reservation coordinator health and whether the
        listing is still tracked by the listings coordinator, to
        avoid false state-change events on disappeared listings.

        Returns:
            True when the sensor should be available.
        """
        if not super().available:
            return False
        if self.coordinator.data is None:
            return False
        # Also check listing is still tracked
        listings_data = self._listings_coordinator.data
        if listings_data is None:
            return False
        return self._listing_id in listings_data

    @property
    def _reservations(self) -> list[GuestyReservation]:
        """Return the reservations for this listing.

        Returns:
            List of reservations, or empty list.
        """
        if self.coordinator.data is None:
            return []
        return self.coordinator.data.get(self._listing_id, [])


class GuestyFinancialSensor(
    CoordinatorEntity["ReservationsCoordinator"],
    SensorEntity,
):
    """Diagnostic sensor exposing reservation financial data.

    Uses priority selection to find the current reservation and
    extracts a single financial field via the description's
    ``value_fn``. Shows unavailable when no reservation or no
    financial data exists (FR-019).

    Attributes:
        _listing_id: The Guesty listing ID.
        _listings_coordinator: Reference for device_info.
        entity_description: The financial sensor description.
    """

    _attr_has_entity_name = True

    entity_description: GuestyFinancialSensorEntityDescription

    def __init__(
        self,
        coordinator: ReservationsCoordinator,
        listings_coordinator: ListingsCoordinator,
        listing_id: str,
        entry: ConfigEntry,
        description: GuestyFinancialSensorEntityDescription,
    ) -> None:
        """Initialize the financial sensor.

        Args:
            coordinator: The reservations coordinator.
            listings_coordinator: The listings coordinator.
            listing_id: The Guesty listing ID.
            entry: The config entry.
            description: The financial sensor description.
        """
        super().__init__(coordinator)
        self._listing_id = listing_id
        self._listings_coordinator = listings_coordinator
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{listing_id}_{description.key}"
        self._attr_translation_key = description.translation_key

    @property
    def native_value(self) -> StateType:
        """Return the financial value from the selected reservation.

        Returns:
            The financial sensor value, or None.
        """
        money = self._money
        if money is None:
            return None
        return self.entity_description.value_fn(money)

    @property
    def available(self) -> bool:
        """Return True only when the specific financial field exists.

        Checks coordinator health, data presence, listing
        existence, reservation presence, money data, and the
        specific field value so partial payloads show
        unavailable rather than unknown (FR-019).

        Returns:
            True when the financial field value is available.
        """
        if not super().available:
            return False
        if self.coordinator.data is None:
            return False
        listings_data = self._listings_coordinator.data
        if listings_data is None:
            return False
        if self._listing_id not in listings_data:
            return False
        money = self._money
        if money is None:
            return False
        return self.entity_description.value_fn(money) is not None

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info linking to the listing device.

        Returns:
            DeviceInfo with listing identifiers.
        """
        return _build_listing_device_info(
            self._listings_coordinator,
            self._listing_id,
        )

    @property
    def _money(self) -> GuestyMoney | None:
        """Return the money data from the selected reservation.

        Returns:
            GuestyMoney from the selected reservation, or None.
        """
        if self.coordinator.data is None:
            return None
        reservations = self.coordinator.data.get(
            self._listing_id,
            [],
        )
        selected = _select_reservation(reservations)
        if selected is None:
            return None
        return selected.money


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Guesty sensors from a config entry.

    Creates listing sensors and reservation status sensors for each
    listing. Registers coordinator listeners for runtime discovery.

    Args:
        hass: Home Assistant instance.
        entry: The config entry.
        async_add_entities: Callback to add entities.
    """
    coordinator: ListingsCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    res_coordinator: ReservationsCoordinator = hass.data[DOMAIN][entry.entry_id][
        "reservations_coordinator"
    ]

    known_ids: set[str] = set()

    def _create_sensors(
        listing_ids: set[str],
    ) -> list[GuestyListingSensor | GuestyReservationSensor | GuestyFinancialSensor]:
        """Create sensor entities for the given listing IDs.

        Creates static description sensors, dynamic custom field
        sensors, reservation status sensors, and financial
        diagnostic sensors for each listing.

        Args:
            listing_ids: Set of listing IDs to create sensors for.

        Returns:
            List of new sensor entities.
        """
        entities: list[
            GuestyListingSensor | GuestyReservationSensor | GuestyFinancialSensor
        ] = []
        for listing_id in listing_ids:
            for desc in LISTING_SENSOR_DESCRIPTIONS:
                entities.append(
                    GuestyListingSensor(
                        coordinator=coordinator,
                        listing_id=listing_id,
                        entry=entry,
                        description=desc,
                    )
                )
            # Dynamic custom field sensors
            listing = coordinator.data.get(listing_id) if coordinator.data else None
            if listing:
                seen_slugs: dict[str, int] = {}
                for field_name in sorted(
                    listing.custom_fields,
                    key=lambda n: (slugify(n), n),
                ):
                    cf_desc = create_custom_field_description(field_name, seen_slugs)
                    entities.append(
                        GuestyListingSensor(
                            coordinator=coordinator,
                            listing_id=listing_id,
                            entry=entry,
                            description=cf_desc,
                        )
                    )
            # Reservation status sensor
            entities.append(
                GuestyReservationSensor(
                    coordinator=res_coordinator,
                    listings_coordinator=coordinator,
                    listing_id=listing_id,
                    entry=entry,
                )
            )
            # Financial diagnostic sensors
            for fin_desc in RESERVATION_FINANCIAL_DESCRIPTIONS:
                entities.append(
                    GuestyFinancialSensor(
                        coordinator=res_coordinator,
                        listings_coordinator=coordinator,
                        listing_id=listing_id,
                        entry=entry,
                        description=fin_desc,
                    )
                )
        return entities

    def _on_coordinator_update() -> None:
        """Handle coordinator data updates for new listings."""
        if coordinator.data is None:
            return
        current_ids = set(coordinator.data.keys())
        new_ids = current_ids - known_ids
        if new_ids:
            _LOGGER.debug(
                "Discovered %d new listing(s): %s",
                len(new_ids),
                new_ids,
            )
            known_ids.update(new_ids)
            async_add_entities(_create_sensors(new_ids))

    # Initial entity creation
    if coordinator.data:
        initial_ids = set(coordinator.data.keys())
        known_ids.update(initial_ids)
        async_add_entities(_create_sensors(initial_ids))

    # Register listener for runtime discovery and ensure it is removed
    # when the config entry is unloaded.
    entry.async_on_unload(
        coordinator.async_add_listener(_on_coordinator_update),
    )

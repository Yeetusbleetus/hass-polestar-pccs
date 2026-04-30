"""Tier 1 binary sensors derived from the Polestar (PCCS) telemetry stream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import PolestarPccsCoordinator


# Proto enum integer values, captured at module-load time by name lookup so
# they aren't load-bearing magic numbers throughout the file.

def _resolve_enums() -> dict[str, Any]:
    from entities.vehiclestates.availability import availability_pb2
    from entities.vehiclestates.battery import battery_pb2
    from entities.vehiclestates.exterior import exterior_pb2

    return {
        "battery": battery_pb2,
        "exterior": exterior_pb2,
        "availability": availability_pb2,
    }


_E = _resolve_enums()
_BATTERY_E = _E["battery"].Battery
_EXTERIOR_E = _E["exterior"].Exterior
_AVAILABILITY_E = _E["availability"].Availability

# Whether the proto value represents "the car is actively pulling power".
_CHARGING_VALUES = frozenset(
    {
        _BATTERY_E.CHARGING_STATUS_V2_CHARGING,
        _BATTERY_E.CHARGING_STATUS_V2_CHARGING_IS_EN_ROUTE,
        _BATTERY_E.CHARGING_STATUS_V2_CHARGING_TOWARDS_MIN_SOC,
        _BATTERY_E.CHARGING_STATUS_V2_SMART_CHARGING,
    }
)

# Active driving / accessory-on states. CONVENIENCE is excluded — it covers
# things like the user unlocking the car remotely without driving.
_IN_USE_VALUES = frozenset(
    {
        _AVAILABILITY_E.USAGE_MODE_ACTIVE,
        _AVAILABILITY_E.USAGE_MODE_DRIVING,
        _AVAILABILITY_E.USAGE_MODE_ENGINE_ON,
    }
)

# OPEN or AJAR both count as "not closed" for the rollups.
_DOOR_OPEN_VALUES = frozenset(
    {_EXTERIOR_E.OPEN_STATUS_OPEN, _EXTERIOR_E.OPEN_STATUS_AJAR}
)


# --- Value extractors ------------------------------------------------------

def _battery(data: dict[str, Any]) -> Any | None:
    response = data.get("battery")
    if response is None or not response.HasField("battery"):
        return None
    return response.battery


def _exterior(data: dict[str, Any]) -> Any | None:
    response = data.get("exterior")
    if response is None or not response.HasField("exterior"):
        return None
    return response.exterior


def _availability(data: dict[str, Any]) -> Any | None:
    response = data.get("availability")
    if response is None or not response.HasField("availability"):
        return None
    return response.availability


def _plugged_in(data: dict[str, Any]) -> bool | None:
    bat = _battery(data)
    if bat is None:
        return None
    return bat.charger_connection_status == _BATTERY_E.CHARGER_CONNECTION_STATUS_CONNECTED


def _charging(data: dict[str, Any]) -> bool | None:
    bat = _battery(data)
    if bat is None:
        return None
    return bat.charging_status_v2 in _CHARGING_VALUES


def _locked(data: dict[str, Any]) -> bool | None:
    ext = _exterior(data)
    if ext is None:
        return None
    return ext.central_lock == _EXTERIOR_E.LOCK_STATUS_LOCKED


def _any_door_open(data: dict[str, Any]) -> bool | None:
    ext = _exterior(data)
    if ext is None:
        return None
    doors = (
        ext.front_left_door,
        ext.front_right_door,
        ext.rear_left_door,
        ext.rear_right_door,
    )
    return any(d in _DOOR_OPEN_VALUES for d in doors)


def _any_window_open(data: dict[str, Any]) -> bool | None:
    ext = _exterior(data)
    if ext is None:
        return None
    windows = (
        ext.front_left_window,
        ext.front_right_window,
        ext.rear_left_window,
        ext.rear_right_window,
    )
    return any(w in _DOOR_OPEN_VALUES for w in windows)


def _tailgate_open(data: dict[str, Any]) -> bool | None:
    ext = _exterior(data)
    if ext is None:
        return None
    return ext.tailgate in _DOOR_OPEN_VALUES


def _hood_open(data: dict[str, Any]) -> bool | None:
    ext = _exterior(data)
    if ext is None:
        return None
    return ext.hood in _DOOR_OPEN_VALUES


def _online(data: dict[str, Any]) -> bool | None:
    av = _availability(data)
    if av is None:
        return None
    return av.availability_status == _AVAILABILITY_E.AVAILABILITY_STATUS_AVAILABLE


def _in_use(data: dict[str, Any]) -> bool | None:
    av = _availability(data)
    if av is None:
        return None
    return av.usage_mode in _IN_USE_VALUES


# --- Entity descriptions ---------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class PolestarBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor entity description bundled with its value extractor."""

    value_fn: Callable[[dict[str, Any]], bool | None]


BINARY_SENSORS: tuple[PolestarBinarySensorDescription, ...] = (
    PolestarBinarySensorDescription(
        key="plugged_in",
        translation_key="plugged_in",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=_plugged_in,
    ),
    PolestarBinarySensorDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=_charging,
    ),
    PolestarBinarySensorDescription(
        # No LOCK device_class: that flips semantics (on=unlocked) which is
        # confusing in the UI. A plain binary sensor where "on" = locked is
        # easier to reason about; the dedicated `lock` platform comes later.
        key="locked",
        translation_key="locked",
        value_fn=_locked,
    ),
    PolestarBinarySensorDescription(
        key="any_door_open",
        translation_key="any_door_open",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=_any_door_open,
    ),
    PolestarBinarySensorDescription(
        key="any_window_open",
        translation_key="any_window_open",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=_any_window_open,
    ),
    PolestarBinarySensorDescription(
        key="tailgate_open",
        translation_key="tailgate_open",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=_tailgate_open,
    ),
    PolestarBinarySensorDescription(
        key="hood_open",
        translation_key="hood_open",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=_hood_open,
    ),
    PolestarBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=_online,
    ),
    PolestarBinarySensorDescription(
        key="in_use",
        translation_key="in_use",
        value_fn=_in_use,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tier 1 binary sensors for one config entry."""
    coordinator: PolestarPccsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        PolestarBinarySensor(coordinator, desc) for desc in BINARY_SENSORS
    )


class PolestarBinarySensor(
    CoordinatorEntity["PolestarPccsCoordinator"], BinarySensorEntity
):
    """Binary sensor backed by a value-extractor on the coordinator's data."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    entity_description: PolestarBinarySensorDescription

    def __init__(
        self,
        coordinator: PolestarPccsCoordinator,
        description: PolestarBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.vin}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.vin)},
            manufacturer="Polestar",
            name=f"Polestar {coordinator.vin[-6:]}",
            serial_number=coordinator.vin,
        )

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data or {})

"""Tier 1 sensors derived from the Polestar (PCCS) telemetry stream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfLength,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import PolestarPccsCoordinator


# --- Value extractors ------------------------------------------------------
#
# Each extractor takes the coordinator's `data` dict and returns the raw
# value (or None if the upstream call hasn't succeeded yet). They live here
# rather than as lambdas in the descriptions so the .proto-shape glue is
# greppable in one place.

def _battery(data: dict[str, Any]) -> Any | None:
    """Return the inner Battery proto, or None if unavailable."""
    response = data.get("battery")
    if response is None or not response.HasField("battery"):
        return None
    return response.battery


def _parking_climatization(data: dict[str, Any]) -> Any | None:
    response = data.get("parking_climatization")
    if response is None or not response.HasField("parking_climatization"):
        return None
    return response.parking_climatization


def _battery_level(data: dict[str, Any]) -> float | None:
    bat = _battery(data)
    return bat.battery_charge_level_percentage if bat is not None else None


def _range_km(data: dict[str, Any]) -> int | None:
    bat = _battery(data)
    return bat.estimated_distance_to_empty_km if bat is not None else None


def _time_to_full(data: dict[str, Any]) -> int | None:
    bat = _battery(data)
    if bat is None:
        return None
    # Field is 0 when not charging — surface that as "unknown" rather than 0
    # so the sensor doesn't read as "0 minutes left" while parked.
    minutes = bat.estimated_charging_time_to_full_minutes
    return minutes if minutes > 0 else None


def _charging_power_kw(data: dict[str, Any]) -> float | None:
    bat = _battery(data)
    return bat.charging_power_watts / 1000.0 if bat is not None else None


# Map proto enum int → HA option string. Built once at import — the mapping
# never changes because the .proto is checked into the repo.
def _charging_status_options() -> tuple[dict[int, str], list[str]]:
    from entities.vehiclestates.battery import battery_pb2 as battery_entity_pb2

    enum_descriptor = battery_entity_pb2.Battery.ChargingStatusV2.DESCRIPTOR
    int_to_option: dict[int, str] = {}
    options: list[str] = []
    for value in enum_descriptor.values:
        # CHARGING_STATUS_V2_CHARGING -> charging  (drop common prefix, lower).
        name = value.name.removeprefix("CHARGING_STATUS_V2_").lower()
        int_to_option[value.number] = name
        if name != "unspecified":
            options.append(name)
    return int_to_option, options


_CHARGING_STATUS_INT_TO_NAME, _CHARGING_STATUS_OPTIONS = _charging_status_options()


def _charging_status(data: dict[str, Any]) -> str | None:
    bat = _battery(data)
    if bat is None:
        return None
    name = _CHARGING_STATUS_INT_TO_NAME.get(bat.charging_status_v2, "unspecified")
    return name if name != "unspecified" else None


def _cabin_temperature(data: dict[str, Any]) -> float | None:
    pc = _parking_climatization(data)
    if pc is None:
        return None
    # When the climatization unit hasn't reported, this is exactly 0.0 — but
    # 0°C is a legitimate cabin reading. Distinguishing "no data" from "cold
    # cabin" needs a smarter check; for now trust the proto.
    return pc.current_compartment_temperature_celsius


# --- Entity descriptions ---------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class PolestarSensorDescription(SensorEntityDescription):
    """Sensor entity description bundled with its value extractor."""

    value_fn: Callable[[dict[str, Any]], Any | None]


SENSORS: tuple[PolestarSensorDescription, ...] = (
    PolestarSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=_battery_level,
    ),
    PolestarSensorDescription(
        key="range",
        translation_key="range",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_fn=_range_km,
    ),
    PolestarSensorDescription(
        key="time_to_full",
        translation_key="time_to_full",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=_time_to_full,
    ),
    PolestarSensorDescription(
        key="charging_power",
        translation_key="charging_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=1,
        value_fn=_charging_power_kw,
    ),
    PolestarSensorDescription(
        key="charging_status",
        translation_key="charging_status",
        device_class=SensorDeviceClass.ENUM,
        options=_CHARGING_STATUS_OPTIONS,
        value_fn=_charging_status,
    ),
    PolestarSensorDescription(
        key="cabin_temperature",
        translation_key="cabin_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=_cabin_temperature,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tier 1 sensors for one config entry."""
    coordinator: PolestarPccsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(PolestarSensor(coordinator, desc) for desc in SENSORS)


class PolestarSensor(
    CoordinatorEntity["PolestarPccsCoordinator"], SensorEntity
):
    """Sensor backed by a value-extractor on the coordinator's latest data."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    entity_description: PolestarSensorDescription

    def __init__(
        self,
        coordinator: PolestarPccsCoordinator,
        description: PolestarSensorDescription,
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
    def native_value(self) -> Any | None:
        return self.entity_description.value_fn(self.coordinator.data or {})

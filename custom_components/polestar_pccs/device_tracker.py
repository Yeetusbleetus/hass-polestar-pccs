"""Device tracker entity exposing the car's location."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import PolestarPccsCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the device tracker for a single Polestar config entry."""
    coordinator: PolestarPccsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PolestarLocationTracker(coordinator)])


class PolestarLocationTracker(
    CoordinatorEntity["PolestarPccsCoordinator"], TrackerEntity
):
    """Latitude/longitude reported by the C3 LBS service."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_translation_key = "location"

    def __init__(self, coordinator: PolestarPccsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.vin}_location"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.vin)},
            manufacturer="Polestar",
            name=f"Polestar {coordinator.vin[-6:]}",
            serial_number=coordinator.vin,
        )

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    def _location(self) -> Any | None:
        """Return the inner Location proto (or None) from the latest poll.

        Prefers ``last_known`` (live) and falls back to ``last_parked`` so the
        tracker still has *some* fix when the car is offline.
        """
        data = self.coordinator.data or {}
        last_known = data.get("last_known")
        if last_known is not None and last_known.HasField("location"):
            return last_known.location
        last_parked = data.get("last_parked")
        if last_parked is not None and last_parked.HasField("location"):
            return last_parked.location
        return None

    @property
    def latitude(self) -> float | None:
        loc = self._location()
        return loc.latitude if loc is not None else None

    @property
    def longitude(self) -> float | None:
        loc = self._location()
        return loc.longitude if loc is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {}
        last_parked = data.get("last_parked")
        if last_parked is not None:
            attrs["parked_stale"] = last_parked.stale
        return attrs

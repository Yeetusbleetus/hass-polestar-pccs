"""
Custom integration to integrate Polestar (PCCS) with Home Assistant.

For more details about this integration, please refer to
https://github.com/Yeetusbleetus/hass-polestar-pccs
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

# No platforms wired up yet — entities will be added once the gRPC client lands.
PLATFORMS: list[Platform] = []


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: ConfigEntry,
) -> bool:
    """Set up Polestar (PCCS) from a config entry."""
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload a Polestar (PCCS) config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a Polestar (PCCS) config entry."""
    await hass.config_entries.async_reload(entry.entry_id)

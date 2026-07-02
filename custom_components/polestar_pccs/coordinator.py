"""DataUpdateCoordinator for the Polestar (PCCS) integration.

Owns the OAuth token cache (refreshing transparently when expired) and polls
the relevant C3 endpoints on a fixed interval. Each entity is fetched
independently — a failed call leaves that key as ``None`` so the rest of the
sensors keep updating.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PolestarPccsAuthError,
    discover_endpoints,
    refresh_tokens,
)
from .client import PolestarPccsClient
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_TOKENS,
    CONF_VIN,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    LOGGER,
    MIN_SCAN_INTERVAL_SECONDS,
)

if TYPE_CHECKING:
    import aiohttp
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def _scan_interval(entry: ConfigEntry) -> timedelta:
    """Resolve the configured polling interval, clamped to the floor."""
    seconds = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS))
    return timedelta(seconds=max(seconds, MIN_SCAN_INTERVAL_SECONDS))


DATA_KEYS = (
    "battery",
    "exterior",
    "availability",
    "parking_climatization",
    "last_known",
    "last_parked",
)


class PolestarPccsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the C3 backend for a single Polestar.

    Data shape — each value is the proto response message for that entity, or
    ``None`` if the most recent call failed:

        {
            "battery":               GetBatteryResponse | None,
            "exterior":              GetExteriorResponse | None,
            "availability":          GetAvailabilityResponse | None,
            "parking_climatization": GetParkingClimatizationResponse | None,
            "last_known":            LastKnownLocation | None,
            "last_parked":           LastParkedLocation | None,
        }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: aiohttp.ClientSession,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN} {entry.data[CONF_VIN]}",
            update_interval=_scan_interval(entry),
        )
        self.entry = entry
        self.vin: str = entry.data[CONF_VIN]
        self._session = session
        self._tokens: dict[str, Any] = dict(entry.data[CONF_TOKENS])
        self._token_endpoint: str | None = None
        self._token_lock = asyncio.Lock()

        self.client = PolestarPccsClient(
            session=session, token_provider=self._async_get_access_token
        )

    async def async_close(self) -> None:
        """Close gRPC channels. Called from async_unload_entry."""
        await self.client.async_close()

    async def _async_get_access_token(self) -> str:
        """Return a non-expired access token, refreshing if needed.

        Serialised behind a lock so concurrent gRPC calls don't trigger N
        parallel refreshes.
        """
        async with self._token_lock:
            if int(time.time()) < int(self._tokens["expires_at"]):
                return self._tokens["access_token"]

            if self._token_endpoint is None:
                discovery = await discover_endpoints(self._session)
                self._token_endpoint = discovery["token_endpoint"]

            refresh_token = self._tokens.get("refresh_token")
            if not refresh_token:
                raise PolestarPccsAuthError("no refresh_token stored")

            new_tokens = await refresh_tokens(
                self._session, self._token_endpoint, refresh_token
            )
            self._tokens = new_tokens
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_TOKENS: new_tokens},
            )
            return new_tokens["access_token"]

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch every supported entity in parallel.

        Per-entity failures are logged and the previous value (or ``None``) is
        kept so the rest of the sensors keep updating. An auth error means the
        refresh token is dead — raising ``ConfigEntryAuthFailed`` hands off to
        HA's reauth flow, which prompts the user to sign in again without
        removing the entry.
        """
        results = await asyncio.gather(
            self.client.get_latest_battery(self.vin),
            self.client.get_latest_exterior(self.vin),
            self.client.get_latest_availability(self.vin),
            self.client.get_latest_parking_climatization(self.vin),
            self.client.get_last_known_location(self.vin),
            self.client.get_last_parked_location(self.vin),
            return_exceptions=True,
        )

        # PolestarPccsAuthError only originates from the token-refresh path
        # (invalid_grant / revoked refresh token), never from a flaky gRPC
        # call, so it is a reliable "re-login needed" signal.
        for result in results:
            if isinstance(result, PolestarPccsAuthError):
                raise ConfigEntryAuthFailed(
                    f"Polestar ID session expired: {result}"
                ) from result

        previous = self.data or {}
        out: dict[str, Any] = {}
        failed = 0
        for key, result in zip(DATA_KEYS, results, strict=True):
            if isinstance(result, BaseException):
                failed += 1
                LOGGER.debug("%s fetch failed: %s", key, result)
                # Keep the previous value if we have one — otherwise fail open
                # to None. Stale-but-present is more useful than gaps in the UI.
                out[key] = previous.get(key)
            else:
                out[key] = result

        if failed == len(DATA_KEYS):
            # Nothing came back at all. With a prior snapshot, serve cached
            # state and retry silently next poll; without one, fail the update
            # so entities show as unavailable instead of empty.
            if previous:
                LOGGER.warning(
                    "poll failed entirely, serving cached state: %s", results[0]
                )
                return out
            raise UpdateFailed(f"all fetches failed: {results[0]}")

        return out

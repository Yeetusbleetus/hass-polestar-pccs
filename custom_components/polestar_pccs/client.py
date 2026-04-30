"""Polestar Connected Car Service client.

Three hosts are involved (mirrors src/client.ts in the polestar-mvp reference):

    PCCS REST       https://api.pccs-prod.plstr.io   account / car list
    CNEPMOB REST    https://cnepmob.volvocars.com    returns the C3 gRPC host
    C3 gRPC         (host returned by CNEPMOB)       car telemetry

Every gRPC call carries metadata ``Authorization: Bearer <token>`` and ``vin: <VIN>``
(``com/polestar/remotecarservice/network/GrpcCredentials.smali``).
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

import aiohttp
import async_timeout
import grpc
from grpc import aio as grpc_aio

from . import proto_gen  # noqa: F401  — append proto_gen/ to sys.path for absolute imports.
from dtlinternet import dtl_internet_service_pb2 as dtl_pb2
from dtlinternet import dtl_internet_service_pb2_grpc as dtl_pb2_grpc
from services.vehiclestates.availability import (
    availability_service_pb2 as availability_pb2,
)
from services.vehiclestates.availability import (
    availability_service_pb2_grpc as availability_pb2_grpc,
)
from services.vehiclestates.battery import battery_service_pb2 as battery_pb2
from services.vehiclestates.battery import (
    battery_service_pb2_grpc as battery_pb2_grpc,
)
from services.vehiclestates.exterior import exterior_service_pb2 as exterior_pb2
from services.vehiclestates.exterior import (
    exterior_service_pb2_grpc as exterior_pb2_grpc,
)
from services.vehiclestates.parkingclimatization import (
    parkingclimatization_service_pb2 as parkclim_pb2,
)
from services.vehiclestates.parkingclimatization import (
    parkingclimatization_service_pb2_grpc as parkclim_pb2_grpc,
)

PCCS_REST = "https://api.pccs-prod.plstr.io"
CNEPMOB_REST = "https://cnepmob.volvocars.com"

C3_HOST_TTL_SECONDS = 60 * 60  # match the TS client's 1h cache.


class PolestarPccsApiError(Exception):
    """Generic API error talking to PCCS / CNEPMOB / C3."""


# A coroutine the client calls whenever it needs a non-expired access token.
# Returning a fresh token here is the caller's responsibility — the client never
# reads or writes tokens directly. This keeps gRPC plumbing free of OAuth state.
TokenProvider = Callable[[], Awaitable[str]]


async def list_cars(
    session: aiohttp.ClientSession, token: str
) -> list[dict[str, Any]]:
    """GET /car-information/car. Returns the list of cars on the account."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        async with async_timeout.timeout(15):
            response = await session.get(
                f"{PCCS_REST}/car-information/car", headers=headers
            )
            if not response.ok:
                text = await response.text()
                raise PolestarPccsApiError(
                    f"car-information/car: {response.status} {text}"
                )
            return await response.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise PolestarPccsApiError(f"car-information/car: {exc}") from exc


class PolestarPccsClient:
    """Async gRPC + REST client for a single Polestar account.

    Responsibilities:

    * Fetches and caches the C3 gRPC host pair (regular + LBS) from CNEPMOB.
    * Keeps long-lived gRPC channels per host.
    * Attaches Authorization + vin metadata on every call.

    A single instance is intended to live for the lifetime of a config entry —
    `async_close()` should be called from `async_unload_entry`.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_provider: TokenProvider,
    ) -> None:
        self._session = session
        self._token_provider = token_provider
        self._c3_cache: dict[str, str] | None = None
        self._c3_expires_at: float = 0.0
        self._channels: dict[str, grpc_aio.Channel] = {}

    async def async_close(self) -> None:
        """Close all gRPC channels."""
        channels = list(self._channels.values())
        self._channels.clear()
        for channel in channels:
            await channel.close()

    async def _fetch_c3_hosts(self) -> dict[str, str]:
        """Return ``{"grpc": "host:port", "lbsGrpc": "host:port"}``.

        Cached for ``C3_HOST_TTL_SECONDS``. The official app re-fetches at
        runtime so we don't hardcode the host either, even though it has been
        stable in EU at ``cepmobtoken.eu.prod.c3.volvocars.com:443``.
        """
        now = time.monotonic()
        if self._c3_cache is not None and now < self._c3_expires_at:
            return self._c3_cache

        token = await self._token_provider()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/volvo.cloud.cnepmob.v1+json",
        }
        try:
            async with async_timeout.timeout(15):
                response = await self._session.get(
                    f"{CNEPMOB_REST}/", headers=headers
                )
                if not response.ok:
                    text = await response.text()
                    raise PolestarPccsApiError(
                        f"cnepmob connect-options: {response.status} {text}"
                    )
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise PolestarPccsApiError(f"cnepmob connect-options: {exc}") from exc

        try:
            c3 = payload["c3"]
            c3_lbs = payload["c3Lbs"]
            hosts = {
                "grpc": f"{c3['grpcHost']}:{c3['grpcPort']}",
                "lbsGrpc": f"{c3_lbs['grpcHost']}:{c3_lbs['grpcPort']}",
            }
        except (KeyError, TypeError) as exc:
            raise PolestarPccsApiError(
                f"cnepmob connect-options: unexpected payload shape: {payload!r}"
            ) from exc

        self._c3_cache = hosts
        self._c3_expires_at = now + C3_HOST_TTL_SECONDS
        return hosts

    def _get_channel(self, host: str) -> grpc_aio.Channel:
        """Return a (cached) TLS channel to ``host``."""
        channel = self._channels.get(host)
        if channel is None:
            channel = grpc_aio.secure_channel(host, grpc.ssl_channel_credentials())
            self._channels[host] = channel
        return channel

    async def _metadata(self, vin: str) -> list[tuple[str, str]]:
        """Build per-call metadata. gRPC keys are lower-cased on the wire."""
        token = await self._token_provider()
        return [
            ("authorization", f"Bearer {token}"),
            ("vin", vin),
        ]

    async def _call_get_latest(
        self,
        stub_factory: Callable[[grpc_aio.Channel], Any],
        method_name: str,
        request_cls: Any,
        vin: str,
    ) -> Any:
        """Common helper for ``services.vehiclestates.<area>.<X>Service/GetLatest<X>``.

        All four C3 vehiclestates services use the same shape: the request is
        ``{id, vin}``, the response embeds the entity. They live on the same
        ``grpc`` host (not the LBS host).
        """
        hosts = await self._fetch_c3_hosts()
        stub = stub_factory(self._get_channel(hosts["grpc"]))
        request = request_cls(id="", vin=vin)
        try:
            return await getattr(stub, method_name)(
                request,
                metadata=await self._metadata(vin),
                timeout=15,
            )
        except grpc.RpcError as exc:
            raise PolestarPccsApiError(f"{method_name} failed: {exc}") from exc

    async def get_latest_battery(
        self, vin: str
    ) -> battery_pb2.GetBatteryResponse:
        """Battery state: SOC, range, charging, energy consumption."""
        return await self._call_get_latest(
            battery_pb2_grpc.BatteryServiceStub,
            "GetLatestBattery",
            battery_pb2.GetBatteryRequest,
            vin,
        )

    async def get_latest_exterior(
        self, vin: str
    ) -> exterior_pb2.GetExteriorResponse:
        """Exterior state: locks, doors, windows, hood/tailgate, alarm."""
        return await self._call_get_latest(
            exterior_pb2_grpc.ExteriorServiceStub,
            "GetLatestExterior",
            exterior_pb2.GetExteriorRequest,
            vin,
        )

    async def get_latest_availability(
        self, vin: str
    ) -> availability_pb2.GetAvailabilityResponse:
        """Connectivity / usage mode (online, driving, in-use, ...)."""
        return await self._call_get_latest(
            availability_pb2_grpc.AvailabilityServiceStub,
            "GetLatestAvailability",
            availability_pb2.GetAvailabilityRequest,
            vin,
        )

    async def get_latest_parking_climatization(
        self, vin: str
    ) -> parkclim_pb2.GetParkingClimatizationResponse:
        """Parking-climate state: cabin temp, running status, runtime left."""
        return await self._call_get_latest(
            parkclim_pb2_grpc.ParkingClimatizationServiceStub,
            "GetLatestParkingClimatization",
            parkclim_pb2.GetParkingClimatizationRequest,
            vin,
        )

    async def get_last_known_location(self, vin: str) -> dtl_pb2.LastKnownLocation:
        """Fetch the most recent known location (may be moving / stale)."""
        hosts = await self._fetch_c3_hosts()
        stub = dtl_pb2_grpc.DtlInternetServiceStub(self._get_channel(hosts["lbsGrpc"]))
        try:
            return await stub.GetLastKnownLocation(
                dtl_pb2.LastKnownLocationRequest(vin=vin),
                metadata=await self._metadata(vin),
                timeout=15,
            )
        except grpc.RpcError as exc:
            raise PolestarPccsApiError(f"GetLastKnownLocation failed: {exc}") from exc

    async def get_last_parked_location(self, vin: str) -> dtl_pb2.LastParkedLocation:
        """Fetch the last parked location (set when the car was last parked)."""
        hosts = await self._fetch_c3_hosts()
        stub = dtl_pb2_grpc.DtlInternetServiceStub(self._get_channel(hosts["lbsGrpc"]))
        try:
            return await stub.GetLastParkedLocation(
                dtl_pb2.LastParkedLocationRequest(vin=vin),
                metadata=await self._metadata(vin),
                timeout=15,
            )
        except grpc.RpcError as exc:
            raise PolestarPccsApiError(f"GetLastParkedLocation failed: {exc}") from exc

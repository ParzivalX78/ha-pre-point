"""Client for the public PRE charger map (SMATRICS POI service).

No account is involved: this is the data the public map on
chargepre.smatrics.com shows to every visitor, read with the same public key.
"""

from __future__ import annotations

import math
import re
from typing import Any

import aiohttp

from .const import POI_BASE, POI_KEY_PAGE, REQUEST_TIMEOUT
from .models import Location

_KEY_RE = re.compile(r"""poiServiceApiKey\s*=\s*['"]([^'"]+)['"]""")


class PoiError(Exception):
    """The public charger map could not be read."""


class PoiClient:
    """Minimal async client for the public POI service."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._key: str | None = None

    async def _api_key(self, *, refresh: bool = False) -> str:
        """Read the public key from the map page (cached until rejected)."""
        if self._key and not refresh:
            return self._key
        try:
            async with self._session.get(
                POI_KEY_PAGE, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as resp:
                page = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise PoiError(f"Map page unreachable: {err or type(err).__name__}") from err
        match = _KEY_RE.search(page)
        if match is None:
            raise PoiError("Public map key not found on the map page")
        self._key = match.group(1)
        return self._key

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        for attempt in range(2):
            key = await self._api_key(refresh=attempt > 0)
            try:
                async with self._session.post(
                    f"{POI_BASE}{path}",
                    json=payload,
                    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    if resp.status in (401, 403) and attempt == 0:
                        continue  # the website may have rotated its key
                    if resp.status >= 400:
                        raise PoiError(f"POST {path} failed (HTTP {resp.status})")
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise PoiError(f"POST {path} failed: {err or type(err).__name__}") from err
        raise PoiError(f"POST {path} rejected the public key")

    async def evse_status(self, evse_ids: list[str]) -> dict[str, str]:
        """Return the raw status of each EVSE (one request for all of them)."""
        if not evse_ids:
            return {}
        data = await self._post("/b2c/evses/status", {"evse_ids": evse_ids})
        return {
            str(item["evse_id"]): item.get("status")
            for item in data or []
            if isinstance(item, dict) and item.get("evse_id")
        }

    async def locations(self, location_ids: list[str]) -> list[Location]:
        """Return full details of the given locations."""
        if not location_ids:
            return []
        data = await self._post("/b2c/locations/location-ids", {"location_ids": location_ids})
        return [Location.from_api(i) for i in data or [] if isinstance(i, dict)]

    async def locations_for_evses(self, evse_ids: list[str]) -> list[Location]:
        """Return the locations the given EVSEs belong to."""
        if not evse_ids:
            return []
        data = await self._post("/b2c/locations/evse-ids", {"evse_ids": evse_ids})
        return [Location.from_api(i) for i in data or [] if isinstance(i, dict)]

    async def locations_near(
        self, latitude: float, longitude: float, radius_km: float
    ) -> list[Location]:
        """Return locations in a box around a point (the service caps results at 1000)."""
        dlat = radius_km / 111.0
        dlon = radius_km / (111.0 * max(math.cos(math.radians(latitude)), 0.01))
        data = await self._post(
            "/b2c/locations/reduced/bounding-box",
            {
                "longitude_west": longitude - dlon,
                "latitude_south": latitude - dlat,
                "longitude_east": longitude + dlon,
                "latitude_north": latitude + dlat,
            },
        )
        return [Location.from_api(i) for i in data or [] if isinstance(i, dict)]

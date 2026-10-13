"""Data update coordinators for PRE Point.

Two independent coordinators: the public charger map (no account involved)
and the account's charging history. A sign-in problem therefore never takes
the charger status down, and a map outage never touches the account.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PREPointApi, PREPointError, PreAuthError, PreRateLimitError
from .const import (
    CONF_LOCATIONS,
    CONF_MONTHLY_FEE,
    CONF_PRICE_AC,
    CONF_PRICE_DC,
    CONF_PRICE_UFC,
    CONF_SCAN_INTERVAL,
    DEFAULT_MONTHLY_FEE,
    DEFAULT_PRICE_AC,
    DEFAULT_PRICE_DC,
    DEFAULT_PRICE_UFC,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HISTORY_INTERVAL,
    LOCATION_DETAILS_INTERVAL,
    MAX_BACKOFF,
)
from .models import ChargingSession, Location, Tariff, normalize_status, summarize_month
from .poi import PoiClient, PoiError
from .statistics import (
    SessionArchive,
    async_clear_statistics,
    async_import_statistics,
    monthly_fees,
)

_LOGGER = logging.getLogger(__name__)

# auth.py retries a failed sign-in once, 15 minutes later; poll again then.
AUTH_RETRY_INTERVAL = timedelta(minutes=15)


@dataclass(slots=True)
class PrePointRuntimeData:
    """Everything the platforms and other modules need."""

    api: PREPointApi
    poi: PoiClient
    stations: PrePointStationsCoordinator
    history: PrePointHistoryCoordinator


type PrePointConfigEntry = ConfigEntry[PrePointRuntimeData]


def tracked_location_ids(entry: ConfigEntry) -> list[str]:
    """Location ids selected by the user."""
    return [str(i) for i in entry.options.get(CONF_LOCATIONS, [])]


def entry_tariff(entry: ConfigEntry) -> Tariff:
    """Tariff from the entry options."""
    return tariff_from_options(entry.options)


def tariff_from_options(options: Mapping[str, Any]) -> Tariff:
    """Tariff from (possibly not yet saved) options."""
    return Tariff(
        ac=float(options.get(CONF_PRICE_AC, DEFAULT_PRICE_AC)),
        dc=float(options.get(CONF_PRICE_DC, DEFAULT_PRICE_DC)),
        ufc=float(options.get(CONF_PRICE_UFC, DEFAULT_PRICE_UFC)),
        monthly_fee=float(options.get(CONF_MONTHLY_FEE, DEFAULT_MONTHLY_FEE)),
    )


class _BackoffCoordinator[T](DataUpdateCoordinator[T]):
    """Doubles the interval after failures (honouring Retry-After), up to an hour."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, name: str, interval: timedelta
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=name, update_interval=interval)
        self._base_interval = interval

    def _back_off(self, retry_after: int | None = None) -> None:
        current = (self.update_interval or self._base_interval).total_seconds()
        seconds = max(current * 2, retry_after or 0)
        self.update_interval = timedelta(seconds=min(seconds, MAX_BACKOFF))

    def _recovered(self) -> None:
        self.update_interval = self._base_interval


class PrePointStationsCoordinator(_BackoffCoordinator[dict[str, Location]]):
    """Status of the tracked chargers from the public map (one request per cycle)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, poi: PoiClient) -> None:
        super().__init__(
            hass,
            entry,
            f"{DOMAIN} stations",
            timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        self.poi = poi
        self._locations: dict[str, Location] = {}
        self._details_at: datetime | None = None

    async def _async_update_data(self) -> dict[str, Location]:
        tracked = tracked_location_ids(self.config_entry)
        if not tracked:
            return {}
        now = dt_util.utcnow()
        try:
            if (
                self._details_at is None
                or now - self._details_at >= LOCATION_DETAILS_INTERVAL
                or set(self._locations) != set(tracked)
            ):
                found = await self.poi.locations(tracked)
                self._locations = {loc.id: loc for loc in found if loc.id in tracked}
                self._details_at = now
            else:
                evse_ids = [e.id for loc in self._locations.values() for e in loc.evses]
                statuses = await self.poi.evse_status(evse_ids)
                for loc in self._locations.values():
                    for evse in loc.evses:
                        if evse.id in statuses:
                            evse.raw_status = statuses[evse.id]
                            evse.status = normalize_status(statuses[evse.id])
        except PoiError as err:
            self._back_off()
            raise UpdateFailed(str(err)) from err
        self._recovered()
        return dict(self._locations)


@dataclass(slots=True)
class HistoryData:
    """Snapshot of the account's charging history."""

    sessions: list[ChargingSession] = field(default_factory=list)
    month_energy_kwh: float = 0.0
    month_cost: float = 0.0
    month_sessions: int = 0
    monthly_fee: float = 0.0

    @property
    def last_session(self) -> ChargingSession | None:
        return self.sessions[0] if self.sessions else None


class PrePointHistoryCoordinator(_BackoffCoordinator[HistoryData]):
    """Charging history of the account, priced and written to statistics."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: PREPointApi) -> None:
        super().__init__(hass, entry, f"{DOMAIN} history", HISTORY_INTERVAL)
        self.api = api
        self.archive = SessionArchive(hass, entry.entry_id)
        self._statistics_current = False

    async def async_prepare(self) -> None:
        """Load the archive so known sessions show even before the first refresh."""
        await self.archive.async_load()
        self.data = self._snapshot()

    def _snapshot(self, tariff: Tariff | None = None) -> HistoryData:
        tariff = tariff or entry_tariff(self.config_entry)
        sessions = self.archive.sessions()
        energy, cost, count = summarize_month(sessions, dt_util.now())
        return HistoryData(
            sessions=sessions,
            month_energy_kwh=energy,
            month_cost=round(cost + tariff.monthly_fee, 2),
            month_sessions=count,
            monthly_fee=tariff.monthly_fee,
        )

    async def _async_update_data(self) -> HistoryData:
        tariff = entry_tariff(self.config_entry)
        try:
            raw = await self.api.async_get_sessions()
        except PreAuthError as err:
            if err.reauth_required:
                raise ConfigEntryAuthFailed(str(err)) from err
            # auth.py allows one more attempt after 15 minutes; come back then.
            self.update_interval = AUTH_RETRY_INTERVAL
            raise UpdateFailed(str(err)) from err
        except PreRateLimitError as err:
            self._back_off(err.retry_after)
            raise UpdateFailed(str(err)) from err
        except PREPointError as err:
            self._back_off()
            raise UpdateFailed(str(err)) from err
        self._recovered()

        items = raw if isinstance(raw, list) else []
        sessions = [ChargingSession.from_api(i) for i in items if isinstance(i, dict)]
        if self.archive.merge(sessions, tariff):
            self._statistics_current = False
            await self.archive.async_save()
        await self._async_update_statistics(tariff)
        return self._snapshot()

    async def async_reprice(self, tariff: Tariff) -> None:
        """Price all archived sessions again and refresh sensors and statistics."""
        await self.archive.async_reprice(tariff)
        self._statistics_current = False
        await self._async_update_statistics(tariff)
        self.async_set_updated_data(self._snapshot(tariff))

    async def _async_update_statistics(self, tariff: Tariff) -> None:
        fee_changed = self.archive.statistics_fee != tariff.monthly_fee
        if self._statistics_current and not fee_changed:
            return
        if fee_changed and self.archive.statistics_fee is not None:
            # Fee rows of the old value would stay behind otherwise.
            async_clear_statistics(self.hass)
        sessions = self.archive.sessions()
        async_import_statistics(
            self.hass, sessions, monthly_fees(sessions, tariff.monthly_fee, dt_util.now())
        )
        if fee_changed:
            self.archive.statistics_fee = tariff.monthly_fee
            await self.archive.async_save()
        self._statistics_current = True

"""Session archive with prices and long-term statistics of energy and cost.

PRE does not show prices, so each session is priced by the tariff from the
options when it first appears, and the price is kept. A tariff change therefore
only affects new sessions, unless a recalculation is asked for in the options.
The archive lives in `.storage`, so the statistics keep sessions older than
the twelve months the API returns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import CURRENCY, DOMAIN
from .models import ChargingSession, Tariff, day_ends, hourly_totals, month_starts

STAT_ENERGY = f"{DOMAIN}:charged_energy"
STAT_COST = f"{DOMAIN}:charging_cost"

STORAGE_VERSION = 1

_NAMES = {
    "cs": ("PRE – nabitá energie", "PRE – cena nabíjení"),
    "en": ("PRE charged energy", "PRE charging cost"),
}


class SessionArchive:
    """All finished sessions of the account with their price, keyed by id."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.sessions", private=True
        )
        self._items: dict[str, dict[str, Any]] = {}
        # Monthly fee the statistics were last written with; when it changes, the
        # statistics are rebuilt so no stale fee rows stay behind.
        self.statistics_fee: float | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._items = data.get("items") or {}
        self.statistics_fee = data.get("statistics_fee")

    async def async_save(self) -> None:
        """Save right away: changes are rare and a reload must see them."""
        await self._store.async_save(
            {"items": self._items, "statistics_fee": self.statistics_fee}
        )

    def merge(self, sessions: list[ChargingSession], tariff: Tariff) -> bool:
        """Add new sessions priced by `tariff`; known ones keep their price.

        Returns whether anything changed; the caller saves.
        """
        merged = dict(self._items)
        for session in sessions:
            if session.started_at is None:
                continue
            known = merged.get(session.id)
            if known is None or known.get("price_per_kwh") is None:
                merged[session.id] = session.priced(tariff).to_store()
                continue
            price = known["price_per_kwh"]
            merged[session.id] = {
                **session.to_store(),
                "band": known.get("band"),
                "price_per_kwh": price,
                "cost": round((session.energy_kwh or 0.0) * price, 2),
            }
        changed = merged != self._items
        self._items = merged
        return changed

    async def async_reprice(self, tariff: Tariff) -> None:
        """Price every archived session again with `tariff` and save right away
        (the entry reloads next and must read the new prices)."""
        self._items = {
            key: ChargingSession.from_store(item).priced(tariff).to_store()
            for key, item in self._items.items()
        }
        await self.async_save()

    def sessions(self) -> list[ChargingSession]:
        """Archived sessions, newest first."""
        sessions = [ChargingSession.from_store(item) for item in self._items.values()]
        return sorted(
            (s for s in sessions if s.started_at is not None),
            key=lambda s: s.started_at,
            reverse=True,
        )

    async def async_remove(self) -> None:
        await self._store.async_remove()


def monthly_fees(
    sessions: list[ChargingSession], fee: float, local_now: datetime
) -> list[tuple[datetime, float]]:
    """The monthly fee at the start of every month since the first session."""
    if fee <= 0 or not sessions:
        return []
    first = min(s.started_at for s in sessions if s.started_at is not None)
    first_local = first.astimezone(local_now.tzinfo)
    return [(start, fee) for start in month_starts(first_local, local_now)]


def recorder_available(hass: HomeAssistant) -> bool:
    return "recorder" in hass.config.components


@callback
def async_import_statistics(
    hass: HomeAssistant,
    sessions: list[ChargingSession],
    fees: list[tuple[datetime, float]],
    local_now: datetime,
) -> None:
    """Write cumulative hourly energy and cost of all sessions and fees (plus a row per day)."""
    if not recorder_available(hass):
        return
    started = [s.started_at for s in sessions if s.started_at is not None]
    extra = day_ends(min(started), local_now) if started else []
    energy_rows: list[StatisticData] = []
    cost_rows: list[StatisticData] = []
    energy_sum = cost_sum = 0.0
    for hour, energy, cost in hourly_totals(sessions, fees, extra):
        energy_sum += energy
        cost_sum += cost
        energy_rows.append(StatisticData(start=hour, state=energy, sum=round(energy_sum, 3)))
        cost_rows.append(StatisticData(start=hour, state=cost, sum=round(cost_sum, 2)))
    if not energy_rows:
        return

    language = (hass.config.language or "en").split("-")[0]
    energy_name, cost_name = _NAMES.get(language, _NAMES["en"])
    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=energy_name,
            source=DOMAIN,
            statistic_id=STAT_ENERGY,
            unit_class="energy",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ),
        energy_rows,
    )
    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=cost_name,
            source=DOMAIN,
            statistic_id=STAT_COST,
            unit_class=None,
            unit_of_measurement=CURRENCY,
        ),
        cost_rows,
    )


@callback
def async_clear_statistics(hass: HomeAssistant) -> None:
    """Remove the imported statistics (integration removed)."""
    if recorder_available(hass):
        get_instance(hass).async_clear_statistics([STAT_ENERGY, STAT_COST])

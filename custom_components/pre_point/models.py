"""Pure parsing and pricing helpers (no Home Assistant imports)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from .const import DC_MAX_KW

_STATUS_MAP = {
    "AVAILABLE": "available",
    "CHARGING": "charging",
    "OCCUPIED": "charging",
    "RESERVED": "reserved",
    "BLOCKED": "blocked",
    "OUTOFORDER": "out_of_order",
    "INOPERATIVE": "out_of_order",
    "PLANNED": "unavailable",
    "REMOVED": "unavailable",
}


def normalize_status(raw: Any) -> str:
    """Map a POI service EVSE status to one of the integration's states."""
    if not isinstance(raw, str):
        return "unknown"
    return _STATUS_MAP.get(raw.strip().upper().replace("_", ""), "unknown")


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_dt(value: Any) -> datetime | None:
    """Parse an API timestamp such as 2026-09-25T19:10:27+0200."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(names: Any) -> str | None:
    """Pick the Czech, then English, then any text from a localized list."""
    if isinstance(names, str):
        return names.strip() or None
    if not isinstance(names, list):
        return None
    texts = {
        str(n.get("language", ""))[:2]: n.get("text")
        for n in names
        if isinstance(n, dict) and n.get("text")
    }
    for language in ("cs", "en"):
        if texts.get(language):
            return str(texts[language]).strip()
    return str(next(iter(texts.values()))).strip() if texts else None


# --- Tariff -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tariff:
    """Price per kWh by charger type, plus a monthly fee (CZK)."""

    ac: float
    dc: float
    ufc: float
    monthly_fee: float = 0.0

    @staticmethod
    def band(current_type: str | None, power_kw: float | None) -> str:
        """Tariff band of a charger: ac, dc (up to 149 kW) or ufc."""
        if (current_type or "").upper().startswith("DC"):
            return "ufc" if power_kw is not None and power_kw > DC_MAX_KW else "dc"
        return "ac"

    def price(self, band: str) -> float:
        return {"ac": self.ac, "dc": self.dc, "ufc": self.ufc}[band]


# --- Charging history ---------------------------------------------------------


@dataclass(slots=True)
class ChargingSession:
    """One finished charging session from the account history."""

    id: str
    started_at: datetime | None
    energy_kwh: float | None
    duration_s: int | None
    evse_id: str | None
    address: str | None
    city: str | None
    current_type: str | None
    max_power_kw: float | None
    operator: str | None
    band: str | None = None
    price_per_kwh: float | None = None
    cost: float | None = None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> ChargingSession:
        event = item.get("event") if isinstance(item.get("event"), dict) else {}
        attrs = {
            a.get("name"): a.get("value")
            for a in event.get("attributes") or []
            if isinstance(a, dict)
        }
        current_type = attrs.get("cpType")
        power_kw = _float(attrs.get("cpMaxSockPwr"))
        # Fall back to the rating code, e.g. Usage.CZPRE.EnergyConsumption.AC.22000.
        for rate in item.get("rateResults") or []:
            code = str(rate.get("chargeCode", "")) if isinstance(rate, dict) else ""
            parts = code.split(".")
            if "EnergyConsumption" in parts and len(parts) >= 2:
                current_type = current_type or parts[-2]
                if power_kw is None and (watts := _float(parts[-1])) is not None:
                    power_kw = watts / 1000
                break
        volume = _float(event.get("volume"))
        duration = _float(event.get("duration"))
        started_at = parse_dt(event.get("startTime"))
        evse_id = attrs.get("cpId")
        session_id = attrs.get("cdrId") or f"{event.get('startTime')}|{evse_id}"
        return cls(
            id=str(session_id),
            started_at=started_at,
            energy_kwh=round(volume / 1000, 3) if volume is not None else None,
            duration_s=int(duration) if duration is not None else None,
            evse_id=evse_id,
            address=attrs.get("address"),
            city=attrs.get("city"),
            current_type=current_type,
            max_power_kw=power_kw,
            operator=attrs.get("cpo"),
        )

    def priced(self, tariff: Tariff) -> ChargingSession:
        """Copy of the session with the price computed from the tariff."""
        band = Tariff.band(self.current_type, self.max_power_kw)
        price = tariff.price(band)
        cost = round((self.energy_kwh or 0.0) * price, 2)
        return replace(self, band=band, price_per_kwh=price, cost=cost)

    def to_store(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "energy_kwh": self.energy_kwh,
            "duration_s": self.duration_s,
            "evse_id": self.evse_id,
            "address": self.address,
            "city": self.city,
            "current_type": self.current_type,
            "max_power_kw": self.max_power_kw,
            "operator": self.operator,
            "band": self.band,
            "price_per_kwh": self.price_per_kwh,
            "cost": self.cost,
        }

    @classmethod
    def from_store(cls, data: dict[str, Any]) -> ChargingSession:
        return cls(**{**data, "started_at": parse_dt(data.get("started_at"))})


def hourly_totals(
    sessions: Iterable[ChargingSession], fees: Iterable[tuple[datetime, float]] = ()
) -> list[tuple[datetime, float, float]]:
    """Return (UTC hour, kWh, cost) per hour with a session start or a fee, oldest first."""
    buckets: dict[datetime, list[float]] = {}

    def bucket(moment: datetime) -> list[float]:
        hour = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        return buckets.setdefault(hour, [0.0, 0.0])

    for session in sessions:
        if session.started_at is None:
            continue
        values = bucket(session.started_at)
        values[0] += session.energy_kwh or 0.0
        values[1] += session.cost or 0.0
    for moment, fee in fees:
        bucket(moment)[1] += fee
    return [
        (hour, round(energy, 3), round(cost, 2))
        for hour, (energy, cost) in sorted(buckets.items())
    ]


def month_starts(first: datetime, now: datetime) -> list[datetime]:
    """Local midnight of the 1st of every month from `first` to `now` (both local)."""
    starts = []
    current = first.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while current <= now:
        starts.append(current)
        year, month = divmod(current.month, 12)
        current = current.replace(year=current.year + year, month=month + 1)
    return starts


def summarize_month(
    sessions: Iterable[ChargingSession], local_now: datetime
) -> tuple[float, float, int]:
    """Return (kWh, cost, count) of sessions started in the current local month."""
    energy = cost = 0.0
    count = 0
    for session in sessions:
        if session.started_at is None:
            continue
        started = session.started_at.astimezone(local_now.tzinfo)
        if (started.year, started.month) != (local_now.year, local_now.month):
            continue
        count += 1
        energy += session.energy_kwh or 0.0
        cost += session.cost or 0.0
    return round(energy, 3), round(cost, 2), count


# --- Public charger map --------------------------------------------------------


@dataclass(slots=True)
class Evse:
    """One charging point (cable/socket) at a location."""

    id: str
    status: str
    raw_status: str | None
    current_type: str | None
    max_power_kw: float | None
    connectors: list[str]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Evse:
        connectors = [c for c in data.get("connectors") or [] if isinstance(c, dict)]
        powers = [p for c in connectors if (p := _float(c.get("power_kw"))) is not None]
        power_types = [str(c.get("power_type") or "") for c in connectors]
        current_type = None
        if any(p.upper().startswith("DC") for p in power_types):
            current_type = "DC"
        elif any(p.upper().startswith("AC") for p in power_types):
            current_type = "AC"
        return cls(
            id=str(data.get("evse_id")),
            status=normalize_status(data.get("status")),
            raw_status=data.get("status"),
            current_type=current_type,
            max_power_kw=max(powers) if powers else None,
            connectors=[str(c.get("standard")) for c in connectors if c.get("standard")],
        )

    @property
    def label(self) -> str:
        """Human readable name, e.g. 'AC 22 kW'."""
        parts = [self.current_type or "EVSE"]
        if self.max_power_kw is not None:
            parts.append(f"{self.max_power_kw:g} kW")
        return " ".join(parts)

    @property
    def number(self) -> str:
        """Last part of the EVSE id, e.g. '2' for CZ*PRE*E1S0008*2."""
        return self.id.rsplit("*", 1)[-1]


@dataclass(slots=True)
class Location:
    """A charging location with its EVSEs."""

    id: str
    name: str
    address: str | None
    city: str | None
    latitude: float | None
    longitude: float | None
    operator: str | None
    evses: list[Evse] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Location:
        coords = data.get("coordinates") if isinstance(data.get("coordinates"), dict) else {}
        operator = data.get("operator") if isinstance(data.get("operator"), dict) else {}
        return cls(
            id=str(data.get("id")),
            name=_text(data.get("name")) or str(data.get("id")),
            address=(data.get("address") or "").strip() or None,
            city=(data.get("city") or "").strip() or None,
            latitude=_float(coords.get("latitude")),
            longitude=_float(coords.get("longitude")),
            operator=operator.get("id"),
            evses=[Evse.from_api(e) for e in data.get("evses") or [] if isinstance(e, dict)],
        )

    @property
    def available_count(self) -> int:
        return sum(1 for e in self.evses if e.status == "available")

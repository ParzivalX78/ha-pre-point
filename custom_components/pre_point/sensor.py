"""Sensors for PRE Point."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CURRENCY, EVSE_STATES
from .coordinator import (
    HistoryData,
    PrePointConfigEntry,
    PrePointStationsCoordinator,
    entry_tariff,
)
from .entity import PrePointAccountEntity, PrePointLocationEntity
from .models import ChargingSession, Evse, Location, Tariff


@dataclass(frozen=True, kw_only=True)
class AccountSensorDescription(SensorEntityDescription):
    """Describes an account sensor."""

    value_fn: Callable[[HistoryData], Any]
    attrs_fn: Callable[[HistoryData], dict[str, Any]] | None = None


def _session_attrs(session: ChargingSession | None) -> dict[str, Any]:
    if session is None:
        return {}
    return {
        "started_at": session.started_at,
        "duration_minutes": round(session.duration_s / 60) if session.duration_s else None,
        "evse": session.evse_id,
        "address": session.address,
        "city": session.city,
        "current_type": session.current_type,
        "max_power_kw": session.max_power_kw,
        "tariff": session.band,
        "price_per_kwh": session.price_per_kwh,
    }


def _last(fn: Callable[[ChargingSession], Any]) -> Callable[[HistoryData], Any]:
    return lambda data: fn(data.last_session) if data.last_session else None


ACCOUNT_SENSORS: tuple[AccountSensorDescription, ...] = (
    AccountSensorDescription(
        # Unique id of the first version ("Poslední nabíjení"), kept on purpose.
        key="latest",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_last(lambda s: s.energy_kwh),
        attrs_fn=lambda data: _session_attrs(data.last_session),
    ),
    AccountSensorDescription(
        key="last_session_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=_last(lambda s: s.cost),
    ),
    AccountSensorDescription(
        key="last_session_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last(lambda s: s.started_at),
    ),
    AccountSensorDescription(
        key="month_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_fn=lambda data: data.month_energy_kwh,
        attrs_fn=lambda data: {"sessions": data.month_sessions},
    ),
    AccountSensorDescription(
        key="month_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: data.month_cost,
        attrs_fn=lambda data: {"monthly_fee": data.monthly_fee},
    ),
    AccountSensorDescription(
        # Unique id of the first version ("Nabíjecí relace"), kept on purpose.
        key="sessions",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: len(data.sessions),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrePointConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        PrePointAccountSensor(runtime.history, description) for description in ACCOUNT_SENSORS
    ]
    for location in (runtime.stations.data or {}).values():
        entities.append(PrePointAvailableCountSensor(runtime.stations, location))
        entities.extend(
            PrePointEvseSensor(runtime.stations, location, evse) for evse in location.evses
        )
    async_add_entities(entities)


class PrePointAccountSensor(PrePointAccountEntity, SensorEntity):
    """Charging history of the account."""

    entity_description: AccountSensorDescription

    def __init__(self, coordinator, description: AccountSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def last_reset(self) -> datetime | None:
        if self.entity_description.key in ("month_energy", "month_cost"):
            return dt_util.start_of_local_day().replace(day=1)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class PrePointAvailableCountSensor(PrePointLocationEntity, SensorEntity):
    """Number of free charging points at a location."""

    _attr_translation_key = "available_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PrePointStationsCoordinator, location: Location) -> None:
        super().__init__(coordinator, location)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_location_{location.id}_available"
        )

    @property
    def native_value(self) -> int | None:
        location = self.location
        return location.available_count if location else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        location = self.location
        if location is None:
            return None
        return {
            "total": len(location.evses),
            "address": location.address,
            "city": location.city,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "location_id": location.id,
        }


class PrePointEvseSensor(PrePointLocationEntity, SensorEntity):
    """Status of one charging point, with the price from the tariff."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = EVSE_STATES
    _attr_translation_key = "evse_status"

    def __init__(
        self, coordinator: PrePointStationsCoordinator, location: Location, evse: Evse
    ) -> None:
        super().__init__(coordinator, location)
        self._evse_id = evse.id
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_evse_{evse.id}_status"
        self._attr_translation_placeholders = {"evse": f"{evse.label} ({evse.number})"}

    @property
    def available(self) -> bool:
        return super().available and self._evse(self._evse_id) is not None

    @property
    def native_value(self) -> str | None:
        evse = self._evse(self._evse_id)
        return evse.status if evse else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        evse = self._evse(self._evse_id)
        if evse is None:
            return None
        tariff = entry_tariff(self.coordinator.config_entry)
        band = Tariff.band(evse.current_type, evse.max_power_kw)
        return {
            "evse_id": evse.id,
            "raw_status": evse.raw_status,
            "current_type": evse.current_type,
            "max_power_kw": evse.max_power_kw,
            "connectors": evse.connectors,
            "tariff": band,
            "price_per_kwh": tariff.price(band),
            "currency": CURRENCY,
        }

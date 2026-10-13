from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN

def _sessions(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("sessions", "items", "content", "data", "results"):
            val = data.get(key)
            if isinstance(val, list):
                return val
        return []
    return []

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([SessionsSensor(coordinator, entry.entry_id), LatestSensor(coordinator, entry.entry_id)])

class Base(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)}, name="PRE Point", manufacturer="PRE"
        )

class SessionsSensor(Base):
    _attr_name = "Nabíjecí relace"
    _attr_icon = "mdi:car-electric"
    @property
    def unique_id(self):
        return f"{self.coordinator.config_entry.entry_id}_sessions"
    @property
    def native_value(self):
        s = _sessions(self.coordinator.data)
        return len(s) if s else 0
    @property
    def extra_state_attributes(self):
        return {"api_response": self.coordinator.data}

class LatestSensor(Base):
    _attr_name = "Poslední nabíjení"
    _attr_icon = "mdi:ev-station"
    @property
    def unique_id(self):
        return f"{self.coordinator.config_entry.entry_id}_latest"
    @property
    def native_value(self):
        s = _sessions(self.coordinator.data)
        return len(s) if s else None
    @property
    def extra_state_attributes(self):
        s = _sessions(self.coordinator.data)
        return {"last_session": s[-1] if s else None, "api_response": self.coordinator.data}

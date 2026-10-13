"""PRE Point integration setup."""
from __future__ import annotations

import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PREPointApi
from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .coordinator import (
    PrePointConfigEntry,
    PrePointHistoryCoordinator,
    PrePointRuntimeData,
    PrePointStationsCoordinator,
    tracked_location_ids,
)
from .entity import account_identifier, location_identifier
from .poi import PoiClient
from .statistics import SessionArchive, async_clear_statistics

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: PrePointConfigEntry) -> bool:
    """Set up a configured PRE Point account."""
    poi = PoiClient(async_get_clientsession(hass))
    stations = PrePointStationsCoordinator(hass, entry, poi)
    # The public map first: if it is down, setup is retried before any sign-in.
    await stations.async_config_entry_first_refresh()

    api = PREPointApi(hass, entry.entry_id, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    async def _close(_: Event | None = None) -> None:
        await api.async_close()

    history = PrePointHistoryCoordinator(hass, entry, api)
    await history.async_prepare()
    # Not async_config_entry_first_refresh(): a temporary sign-in problem must
    # keep the entry loaded, so the single retry in auth.py (15 minutes later)
    # is not cut short by Home Assistant retrying the whole setup.
    await history.async_refresh()
    if isinstance(history.last_exception, ConfigEntryAuthFailed):
        await _close()
        raise history.last_exception
    if not history.last_update_success:
        _LOGGER.warning(
            "PRE Point charging history is not available yet, retrying in %s",
            history.update_interval,
        )

    entry.runtime_data = PrePointRuntimeData(
        api=api, poi=poi, stations=stations, history=history
    )
    _remove_stale_devices(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    entry.async_on_unload(_close)
    # Entries are not unloaded when Home Assistant stops; close the session then too.
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _close))
    return True


def _remove_stale_devices(hass: HomeAssistant, entry: PrePointConfigEntry) -> None:
    """Drop devices of locations the user no longer tracks."""
    keep = {account_identifier(entry.entry_id)} | {
        location_identifier(entry.entry_id, loc_id) for loc_id in tracked_location_ids(entry)
    }
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & keep:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def _async_reload(hass: HomeAssistant, entry: PrePointConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PrePointConfigEntry) -> bool:
    """Unload the configured entry (the session is closed by async_on_unload)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: PrePointConfigEntry) -> None:
    """Forget the stored refresh token, session archive and statistics."""
    api = PREPointApi(hass, entry.entry_id, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    try:
        await api.async_forget_stored_tokens()
    finally:
        await api.async_close()
    await SessionArchive(hass, entry.entry_id).async_remove()
    async_clear_statistics(hass)

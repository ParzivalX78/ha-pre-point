"""PRE Point integration setup."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PREPointApi, PREPointError, PreAuthError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]
UPDATE_INTERVAL = timedelta(minutes=15)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a configured PRE Point account."""
    api = PREPointApi(entry.data["username"], entry.data["password"])

    async def update():
        try:
            return await api.async_get_sessions()
        except PreAuthError as err:
            if err.reauth_required:
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed(f"PRE Point: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="PRE Point",
        update_method=update,
        update_interval=UPDATE_INTERVAL,
    )

    # Keep the entry loaded if the first login fails once. The API client
    # retains its retry timer and the next login attempt is no earlier than
    # 15 minutes later. A second failure raises ConfigEntryAuthFailed.
    try:
        initial_data = await api.async_get_sessions()
    except PreAuthError as err:
        if err.reauth_required:
            await api.async_close()
            raise ConfigEntryAuthFailed(str(err)) from err
        _LOGGER.warning(
            "PRE Point is temporarily unauthenticated; one retry is scheduled "
            "after 15 minutes: %s",
            err,
        )
        coordinator.async_set_updated_data(None)
    except Exception as err:
        await api.async_close()
        raise ConfigEntryNotReady(f"PRE Point initial request failed: {err}") from err
    else:
        coordinator.async_set_updated_data(initial_data)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the configured entry."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if data:
            await data["api"].async_close()
        return True
    return False

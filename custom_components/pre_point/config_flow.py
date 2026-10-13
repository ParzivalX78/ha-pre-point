"""PRE Point configuration, reauthentication and options flows."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PREPointApi, PREPointError, PreAuthError, PreInvalidCredentialsError
from .const import (
    CONF_LOCATIONS,
    CONF_MONTHLY_FEE,
    CONF_PASSWORD,
    CONF_PRICE_AC,
    CONF_PRICE_DC,
    CONF_PRICE_UFC,
    CONF_QUERY,
    CONF_RADIUS,
    CONF_RECALCULATE,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_MONTHLY_FEE,
    DEFAULT_PRICE_AC,
    DEFAULT_PRICE_DC,
    DEFAULT_PRICE_UFC,
    DEFAULT_RADIUS_KM,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    POI_OPERATOR,
)
from .coordinator import tariff_from_options, tracked_location_ids
from .models import ChargingSession, Location
from .poi import PoiClient, PoiError

_LOGGER = logging.getLogger(__name__)

_EMAIL = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL))
_PASSWORD = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
_PRICE = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0, max=100, step=0.01, unit_of_measurement="Kč/kWh",
        mode=selector.NumberSelectorMode.BOX,
    )
)
_FEE = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0, max=10000, step=1, unit_of_measurement="Kč",
        mode=selector.NumberSelectorMode.BOX,
    )
)
_RADIUS = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=1, max=50, step=1, unit_of_measurement="km",
        mode=selector.NumberSelectorMode.BOX,
    )
)
_INTERVAL = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL, step=30, unit_of_measurement="s",
        mode=selector.NumberSelectorMode.BOX,
    )
)

TARIFF_KEYS = (CONF_PRICE_AC, CONF_PRICE_DC, CONF_PRICE_UFC, CONF_MONTHLY_FEE)
TARIFF_DEFAULTS = {
    CONF_PRICE_AC: DEFAULT_PRICE_AC,
    CONF_PRICE_DC: DEFAULT_PRICE_DC,
    CONF_PRICE_UFC: DEFAULT_PRICE_UFC,
    CONF_MONTHLY_FEE: DEFAULT_MONTHLY_FEE,
}


def _tariff_fields(values: Mapping[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(key, default=float(values.get(key, TARIFF_DEFAULTS[key]))): (
            _FEE if key == CONF_MONTHLY_FEE else _PRICE
        )
        for key in TARIFF_KEYS
    }


def _multi_select(options: list[selector.SelectOptionDict]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options, multiple=True, mode=selector.SelectSelectorMode.LIST
        )
    )


async def _test_credentials(
    hass: HomeAssistant, username: str, password: str
) -> tuple[str | None, list[Any]]:
    """Return (error key, raw history). Sign-in itself is done by auth.py.

    `entry_id=None`: nothing is persisted before the entry exists.
    """
    api = PREPointApi(hass, None, username, password)
    try:
        raw = await api.async_get_sessions()
    except PreInvalidCredentialsError:
        return "invalid_auth", []
    except PreAuthError as err:
        _LOGGER.warning("PRE Point sign-in failed (%s)", type(err).__name__)
        return "pre_login_failed", []
    except PREPointError as err:
        _LOGGER.warning("PRE Point API test failed (%s)", type(err).__name__)
        return "cannot_connect", []
    except Exception:
        _LOGGER.exception("Unexpected PRE Point setup error")
        return "unknown", []
    finally:
        await api.async_close()
    return None, raw if isinstance(raw, list) else []


class LocationPicker:
    """Builds the list of chargers to choose from: where you charged + PRE nearby."""

    def __init__(self, hass: HomeAssistant, charged_evse_ids: list[str]) -> None:
        self._hass = hass
        self._poi = PoiClient(async_get_clientsession(hass))
        self._charged_evse_ids = sorted(set(charged_evse_ids))
        self._charged: dict[str, Location] | None = None
        self.search: tuple[float, str] | None = None
        self.options: list[selector.SelectOptionDict] = []
        self.charged_ids: list[str] = []

    async def async_search(
        self,
        radius_km: float,
        query: str,
        keep: list[str],
        names: Mapping[str, str] | None = None,
    ) -> None:
        """Refresh the options; raises PoiError when the public map is down."""
        if self._charged is None:
            found = await self._poi.locations_for_evses(self._charged_evse_ids)
            self._charged = {loc.id: loc for loc in found}
            self.charged_ids = list(self._charged)
        near = await self._poi.locations_near(
            self._hass.config.latitude, self._hass.config.longitude, radius_km
        )
        needle = query.casefold()
        here = "nabíjeno zde" if self._hass.config.language == "cs" else "charged here"
        locations: dict[str, tuple[Location, bool]] = {
            loc_id: (loc, True) for loc_id, loc in self._charged.items()
        }
        for loc in near:
            if loc.operator == POI_OPERATOR and loc.id not in locations:
                if not needle or needle in f"{loc.name} {loc.city or ''}".casefold():
                    locations[loc.id] = (loc, False)
        options = [
            selector.SelectOptionDict(value=loc_id, label=label)
            for loc_id, label in sorted(
                (
                    (
                        loc.id,
                        loc.name
                        + (f", {loc.city}" if loc.city else "")
                        + (f" – {', '.join(sorted({e.label for e in loc.evses}))}" if loc.evses else "")
                        + (f" ({here})" if charged else ""),
                    )
                    for loc, charged in locations.values()
                ),
                key=lambda option: (option[0] not in self._charged, option[1].casefold()),
            )
        ]
        # Keep already selected locations even when they fall outside the new search.
        shown = {o["value"] for o in options}
        options.extend(
            selector.SelectOptionDict(value=loc_id, label=(names or {}).get(loc_id, f"#{loc_id}"))
            for loc_id in keep
            if loc_id not in shown
        )
        self.options = options
        self.search = (radius_km, query)


def _charged_evse_ids(raw: list[Any]) -> list[str]:
    sessions = [ChargingSession.from_api(i) for i in raw if isinstance(i, dict)]
    return [s.evse_id for s in sessions if s.evse_id]


class PREPointConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure PRE Point."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {}
        self._picker: LocationPicker | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PREPointOptionsFlow:
        return PREPointOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()
            error, raw = await _test_credentials(self.hass, username, user_input[CONF_PASSWORD])
            if error is None:
                self._data = {CONF_USERNAME: username, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                self._picker = LocationPicker(self.hass, _charged_evse_ids(raw))
                return await self.async_step_tariff()
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {vol.Required(CONF_USERNAME): _EMAIL, vol.Required(CONF_PASSWORD): _PASSWORD}
                ),
                {CONF_USERNAME: (user_input or {}).get(CONF_USERNAME, "")},
            ),
            errors=errors,
        )

    async def async_step_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._options = {key: float(user_input[key]) for key in TARIFF_KEYS}
            return await self.async_step_locations()
        return self.async_show_form(
            step_id="tariff", data_schema=vol.Schema(_tariff_fields(TARIFF_DEFAULTS))
        )

    async def async_step_locations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._picker is not None
        errors: dict[str, str] = {}
        selected: list[str] = []
        radius, query = float(DEFAULT_RADIUS_KM), ""
        if user_input is not None:
            selected = [str(i) for i in user_input.get(CONF_LOCATIONS, [])]
            radius = float(user_input.get(CONF_RADIUS, DEFAULT_RADIUS_KM))
            query = (user_input.get(CONF_QUERY) or "").strip()
            # Same search as shown (or the map is down): this submit means "save".
            if self._picker.search in ((radius, query), None):
                return self.async_create_entry(
                    title=self._data[CONF_USERNAME],
                    data=self._data,
                    options={
                        **self._options,
                        CONF_LOCATIONS: selected,
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    },
                )
        try:
            await self._picker.async_search(radius, query, selected)
        except PoiError as err:
            _LOGGER.warning("PRE public charger map unavailable: %s", err)
            errors["base"] = "map_unavailable"
        if user_input is None:
            selected = list(self._picker.charged_ids)
        return self.async_show_form(
            step_id="locations",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_LOCATIONS, default=selected): _multi_select(
                        self._picker.options
                    ),
                    vol.Required(CONF_RADIUS, default=radius): _RADIUS,
                    vol.Optional(CONF_QUERY, default=query): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start manual reauthentication requested by the integration."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again (the account itself cannot change here)."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error, _ = await _test_credentials(
                self.hass, entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )


class PREPointOptionsFlow(OptionsFlow):
    """Change the tariff, tracked chargers and the polling interval."""

    def __init__(self) -> None:
        self._picker: LocationPicker | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        runtime = getattr(entry, "runtime_data", None)
        if self._picker is None:
            charged = (
                [s.evse_id for s in runtime.history.archive.sessions() if s.evse_id]
                if runtime
                else []
            )
            self._picker = LocationPicker(self.hass, charged)

        errors: dict[str, str] = {}
        values: dict[str, Any] = {
            **{key: entry.options.get(key, TARIFF_DEFAULTS[key]) for key in TARIFF_KEYS},
            CONF_LOCATIONS: tracked_location_ids(entry),
            CONF_SCAN_INTERVAL: entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            CONF_RADIUS: float(DEFAULT_RADIUS_KM),
            CONF_QUERY: "",
            CONF_RECALCULATE: False,
        }
        if user_input is not None:
            values.update(user_input)
            values[CONF_LOCATIONS] = [str(i) for i in user_input.get(CONF_LOCATIONS, [])]
            values[CONF_QUERY] = (values.get(CONF_QUERY) or "").strip()
            search = (float(values[CONF_RADIUS]), values[CONF_QUERY])
            if self._picker.search == search:
                return await self._async_save(values)

        try:
            await self._picker.async_search(
                float(values[CONF_RADIUS]),
                values[CONF_QUERY],
                [str(i) for i in values[CONF_LOCATIONS]],
                {loc_id: loc.name for loc_id, loc in (runtime.stations.data or {}).items()}
                if runtime
                else None,
            )
        except PoiError as err:
            _LOGGER.warning("PRE public charger map unavailable: %s", err)
            errors["base"] = "map_unavailable"

        fields: dict[Any, Any] = _tariff_fields(values)
        fields[vol.Optional(CONF_RECALCULATE, default=False)] = bool
        fields[vol.Optional(CONF_LOCATIONS, default=list(values[CONF_LOCATIONS]))] = (
            _multi_select(self._picker.options)
        )
        fields[vol.Required(CONF_RADIUS, default=float(values[CONF_RADIUS]))] = _RADIUS
        fields[vol.Optional(CONF_QUERY, default=values[CONF_QUERY])] = str
        fields[vol.Required(CONF_SCAN_INTERVAL, default=values[CONF_SCAN_INTERVAL])] = _INTERVAL
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields), errors=errors)

    async def _async_save(self, values: dict[str, Any]) -> ConfigFlowResult:
        options = {
            **{key: float(values[key]) for key in TARIFF_KEYS},
            CONF_LOCATIONS: [str(i) for i in values.get(CONF_LOCATIONS, [])],
            CONF_SCAN_INTERVAL: int(values[CONF_SCAN_INTERVAL]),
        }
        runtime = getattr(self.config_entry, "runtime_data", None)
        if values.get(CONF_RECALCULATE) and runtime is not None:
            await runtime.history.async_reprice(tariff_from_options(options))
        return self.async_create_entry(data=options)

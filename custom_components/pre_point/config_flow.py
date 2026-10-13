"""PRE Point configuration and reauthentication flows."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .api import PREPointApi, PREPointError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class PREPointConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure PRE Point."""

    VERSION = 1

    async def _test_credentials(self, username: str, password: str) -> str | None:
        api = PREPointApi(username, password)
        try:
            await api.async_get_sessions()
        except PREPointError as err:
            _LOGGER.warning("PRE Point login/API test failed: %s", err)
            return "pre_login_failed"
        except Exception:
            _LOGGER.exception("Unexpected PRE Point setup error")
            return "cannot_connect"
        finally:
            await api.async_close()
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            error = await self._test_credentials(
                username, user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=username,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Start manual reauthentication requested by the integration."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_entry_not_found")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask the user to verify/update the account credentials."""
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_entry_not_found")

        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            error = await self._test_credentials(
                username, user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    title=username,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
                await self.hass.config_entries.async_reload(
                    self._reauth_entry.entry_id
                )
                return self.async_abort(reason="reauth_successful")

        current_username = self._reauth_entry.data.get(CONF_USERNAME, "")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_USERNAME, default=current_username
                ): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )

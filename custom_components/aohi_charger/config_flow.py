"""Config flow for the AOHI Smart Charger integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult

from .api import AohiApiClient, AohiApiError, AohiAuthError
from .const import CONF_COUNTRY, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
        vol.Required(CONF_COUNTRY, default="France"): str,
    }
)


class AohiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AOHI Smart Charger."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            client = AohiApiClient(
                self.hass,
                user_input["email"],
                user_input["password"],
                user_input[CONF_COUNTRY],
            )
            try:
                await client.async_login()
            except AohiAuthError:
                errors["base"] = "invalid_auth"
            except AohiApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(str(client.user_id))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input["email"], data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

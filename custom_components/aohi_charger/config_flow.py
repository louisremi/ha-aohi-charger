"""Config flow for the AOHI Smart Charger integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import network
from homeassistant.config_entries import ConfigFlowResult

from .api import AohiApiClient, AohiApiError, AohiAuthError
from .const import (
    CONF_COUNTRY,
    CONF_HOST,
    CONF_HTTP_PORT,
    CONF_MODE,
    CONF_MQTT_PORT,
    DEFAULT_HTTP_PORT,
    DEFAULT_MQTT_PORT,
    DOMAIN,
    MODE_CLOUD,
    MODE_LOCAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_CLOUD_SCHEMA = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
        vol.Required(CONF_COUNTRY, default="France"): str,
    }
)

def local_schema(default_host: str = "") -> vol.Schema:
    """Schema for the local step, pre-filled with the detected host."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=default_host): str,
            vol.Required(CONF_HTTP_PORT, default=DEFAULT_HTTP_PORT): int,
            vol.Required(CONF_MQTT_PORT, default=DEFAULT_MQTT_PORT): int,
        }
    )


class AohiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AOHI Smart Charger."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between an AOHI account and a local server.

        These are separate entries rather than one combined entry, so a charger
        reprovisioned to a local server can coexist with others still on the
        cloud.
        """
        return self.async_show_menu(step_id="user", menu_options=["cloud", "local"])

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up chargers through an AOHI cloud account."""
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
                    title=user_input["email"],
                    data={**user_input, CONF_MODE: MODE_CLOUD},
                )

        return self.async_show_form(
            step_id="cloud", data_schema=STEP_CLOUD_SCHEMA, errors=errors
        )

    async def _async_default_host(self) -> str:
        """Where the local server most likely is.

        The usual setup is the AOHI Local Server add-on installed alongside
        Home Assistant. It uses host networking, so it listens on this machine's
        own LAN address -- which is what this returns. Anyone running it
        elsewhere can simply type over the suggestion.
        """
        try:
            return await network.async_get_source_ip(self.hass) or ""
        except Exception:  # a failed guess must not block setup
            _LOGGER.debug("Could not determine this machine's address", exc_info=True)
            return ""

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up chargers through a local server, with no cloud involved."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = AohiApiClient(
                self.hass,
                mode=MODE_LOCAL,
                host=user_input[CONF_HOST],
                http_port=user_input[CONF_HTTP_PORT],
                mqtt_port=user_input[CONF_MQTT_PORT],
            )
            try:
                devices = await client.async_get_devices()
            except AohiApiError:
                errors["base"] = "cannot_connect"
            else:
                if not devices:
                    # Reachable but empty: almost always a charger that has not
                    # been provisioned to this server yet, which is worth saying
                    # plainly rather than creating an entry with no entities.
                    errors["base"] = "no_devices"
                else:
                    host = user_input[CONF_HOST]
                    await self.async_set_unique_id(
                        f"local_{host}_{user_input[CONF_MQTT_PORT]}"
                    )
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"AOHI local ({host})",
                        data={**user_input, CONF_MODE: MODE_LOCAL},
                    )

        # Keep whatever was typed on a retry, rather than resetting to the guess.
        default_host = (user_input or {}).get(CONF_HOST) or await self._async_default_host()
        return self.async_show_form(
            step_id="local", data_schema=local_schema(default_host), errors=errors
        )

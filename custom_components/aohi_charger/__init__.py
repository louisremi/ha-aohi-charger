"""The AOHI Smart Charger integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import AohiApiClient, AohiApiError
from .const import (
    CONF_COUNTRY,
    CONF_HOST,
    CONF_HTTP_PORT,
    CONF_MODE,
    CONF_MQTT_PORT,
    DOMAIN,
    MODE_CLOUD,
    MODE_LOCAL,
)
from .coordinator import AohiCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch", "sensor", "select"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AOHI Smart Charger from a config entry."""
    # Entries created before local mode existed have no mode key; they are cloud.
    mode = entry.data.get(CONF_MODE, MODE_CLOUD)
    if mode == MODE_LOCAL:
        client = AohiApiClient(
            hass,
            mode=MODE_LOCAL,
            host=entry.data[CONF_HOST],
            http_port=entry.data[CONF_HTTP_PORT],
            mqtt_port=entry.data[CONF_MQTT_PORT],
        )
    else:
        client = AohiApiClient(
            hass, entry.data["email"], entry.data["password"], entry.data[CONF_COUNTRY]
        )

    try:
        await client.async_login()
        devices = await client.async_get_devices()
        devices_by_sn = {device["sn"]: device for device in devices}
        await client.async_connect_mqtt(list(devices_by_sn))
        if client.is_local:
            # The local server knows only serial numbers, so model and firmware
            # come from the devices themselves once the broker link is up.
            await client.async_enrich_local_devices(devices_by_sn)
    except AohiApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = AohiCoordinator(hass, client, devices_by_sn, entry.entry_id)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # The MQTT client is already connected and running its network thread by
        # this point. Without this, a failed first refresh would leak it and HA's
        # setup retry would stack up another live connection every attempt.
        await client.async_disconnect()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: AohiCoordinator = hass.data[DOMAIN][entry.entry_id]

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await coordinator.client.async_disconnect()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

"""The AOHI Smart Charger integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import AohiApiClient, AohiApiError
from .const import CONF_COUNTRY, DOMAIN
from .coordinator import AohiCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AOHI Smart Charger from a config entry."""
    client = AohiApiClient(
        hass, entry.data["email"], entry.data["password"], entry.data[CONF_COUNTRY]
    )

    try:
        await client.async_login()
        devices = await client.async_get_devices()
        devices_by_sn = {device["sn"]: device for device in devices}
        await client.async_connect_mqtt(list(devices_by_sn))
    except AohiApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = AohiCoordinator(hass, client, devices_by_sn, entry.entry_id)
    await coordinator.async_config_entry_first_refresh()

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

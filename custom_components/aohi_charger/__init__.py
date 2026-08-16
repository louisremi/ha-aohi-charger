"""The AOHI Smart Charger integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

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

    if not client.is_local:
        _async_prune_stale_devices(hass, entry, set(devices_by_sn))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _aohi_serials(device: dr.DeviceEntry) -> set[str]:
    return {ident for domain, ident in device.identifiers if domain == DOMAIN}


@callback
def _async_prune_stale_devices(
    hass: HomeAssistant, entry: ConfigEntry, known_sns: set[str]
) -> None:
    """Drop registry devices this entry no longer serves.

    Cloud entries only. ``/iot1/device/list`` is authoritative, so a serial
    missing from it has genuinely been removed from the account. The local
    server instead reports whichever chargers have connected to it, so a
    charger that is merely unplugged would vanish from that list -- and pruning
    on it would silently delete the user's names, areas and automations.
    """
    registry = dr.async_get(hass)
    # Materialised up front: removing a device mutates the registry's indexes.
    for device in list(dr.async_entries_for_config_entry(registry, entry.entry_id)):
        if _aohi_serials(device) & known_sns:
            continue
        _LOGGER.info(
            "Removing %s from this AOHI account; it is no longer listed there",
            device.name_by_user or device.name or device.id,
        )
        if device.config_entries == {entry.entry_id}:
            registry.async_remove_device(device.id)
        else:
            # Shared with another AOHI entry -- typically the same charger part
            # way through a move between cloud and local. Detach only our half.
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a charger from its device page once we no longer serve it.

    Without this, Home Assistant offers no delete button at all, so a charger
    removed in the AOHI app (or permanently retired from the local server)
    lingers in the UI with no way to clear it.
    """
    coordinator: AohiCoordinator | None = hass.data.get(DOMAIN, {}).get(
        config_entry.entry_id
    )
    if coordinator is None:
        return True
    return not (_aohi_serials(device_entry) & coordinator.devices.keys())


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: AohiCoordinator = hass.data[DOMAIN][entry.entry_id]

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await coordinator.client.async_disconnect()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

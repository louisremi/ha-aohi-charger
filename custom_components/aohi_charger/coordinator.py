"""Data update coordinator for the AOHI Smart Charger integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AohiApiClient, AohiApiError
from .const import DOMAIN, UPDATE_INTERVAL_SECONDS, signal_new_device

_LOGGER = logging.getLogger(__name__)


class AohiCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls status for every device on the account and detects newly added ones."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: AohiApiClient,
        devices: dict[str, dict[str, Any]],
        entry_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.client = client
        self.devices = devices
        self._signal_new_device = signal_new_device(entry_id)

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            found = await self.client.async_get_devices()
        except AohiApiError as err:
            raise UpdateFailed(str(err)) from err

        new_devices = {
            device["sn"]: device for device in found if device["sn"] not in self.devices
        }
        if new_devices:
            _LOGGER.info(
                "Discovered %d new AOHI device(s): %s",
                len(new_devices),
                ", ".join(new_devices),
            )
            self.devices.update(new_devices)
            await self.client.async_subscribe_devices(list(new_devices))
            async_dispatcher_send(self.hass, self._signal_new_device, new_devices)

        sns = list(self.devices)
        try:
            results = await asyncio.gather(*(self._async_device_data(sn) for sn in sns))
        except AohiApiError as err:
            raise UpdateFailed(str(err)) from err
        return dict(zip(sns, results, strict=True))

    async def _async_device_data(self, sn: str) -> dict[str, Any]:
        """Fetch one device's status, enriched with its device/WiFi info.

        cmd:3 (status) and cmd:5 (device/WiFi info) use separate pending keys
        and locks, so they're safe to issue concurrently. They share no keys,
        so the replies merge cleanly into one dict.
        """
        device_status, device_info = await asyncio.gather(
            self.client.async_get_status(sn),
            self._async_device_info_or_empty(sn),
        )
        return {**device_status, **device_info}

    async def _async_device_info_or_empty(self, sn: str) -> dict[str, Any]:
        """Return cmd:5 device info, or {} if this device doesn't answer it.

        cmd:5 only feeds the diagnostic signal-strength sensor, so a device
        that doesn't support it (other AOHI/I4SEASON models, older firmware)
        must not take the whole integration down.
        """
        try:
            return await self.client.async_get_device_info(sn)
        except AohiApiError as err:
            _LOGGER.debug("Device %s did not answer cmd:5 device info: %s", sn, err)
            return {}

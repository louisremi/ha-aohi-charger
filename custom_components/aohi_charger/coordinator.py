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
        self._unlisted: set[str] = set()

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            found = await self.client.async_get_devices()
        except AohiApiError as err:
            raise UpdateFailed(str(err)) from err

        found_by_sn = {device["sn"]: device for device in found}

        new_devices = {
            sn: device for sn, device in found_by_sn.items() if sn not in self.devices
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

        # A device that has left the account (or, locally, gone offline) answers
        # nothing, so polling it times out and fails the whole update -- taking
        # every other charger's entities down with it. Skip it instead. Its
        # entities stay put and simply report no state; removing them is the
        # user's call, from the device page.
        unlisted = self.devices.keys() - found_by_sn.keys()
        for sn in unlisted - self._unlisted:
            _LOGGER.info("AOHI device %s is no longer listed; it will not be polled", sn)
        self._unlisted = unlisted

        sns = [sn for sn in self.devices if sn not in unlisted]
        results = await asyncio.gather(
            *(self._async_device_data(sn) for sn in sns), return_exceptions=True
        )

        data: dict[str, dict[str, Any]] = {}
        failures: list[str] = []
        for sn, result in zip(sns, results, strict=True):
            if isinstance(result, AohiApiError):
                failures.append(f"{sn}: {result}")
            elif isinstance(result, BaseException):
                raise result
            else:
                data[sn] = result

        # One silent charger must not take the whole entry down with it: an
        # unplugged device answers nothing, and failing here would strand every
        # other charger on the account. Nothing answering at all is different --
        # that is the broker link itself, which is worth retrying as a unit.
        if failures and not data:
            raise UpdateFailed("; ".join(failures))
        for failure in failures:
            _LOGGER.debug("AOHI device did not answer this poll (%s)", failure)
        return data

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

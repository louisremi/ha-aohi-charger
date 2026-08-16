"""Switch platform for the AOHI Smart Charger integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ALL_PORTS, DOMAIN, signal_new_device
from .coordinator import AohiCoordinator
from .entity import AohiEntity


def _build_entities(
    coordinator: AohiCoordinator, sn: str, device: dict[str, Any]
) -> list[SwitchEntity]:
    entities: list[SwitchEntity] = [AohiWholeDeviceSwitch(coordinator, sn, device)]
    for port in ALL_PORTS:
        entities.append(AohiPortSwitch(coordinator, sn, device, port))
    return entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up switches for every AOHI device on this account, now and in the future."""
    coordinator: AohiCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = []
    for sn, device in coordinator.devices.items():
        entities.extend(_build_entities(coordinator, sn, device))
    async_add_entities(entities)

    @callback
    def _async_new_devices(new_devices: dict[str, dict[str, Any]]) -> None:
        new_entities: list[SwitchEntity] = []
        for sn, device in new_devices.items():
            new_entities.extend(_build_entities(coordinator, sn, device))
        async_add_entities(new_entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, signal_new_device(entry.entry_id), _async_new_devices
        )
    )


class AohiWholeDeviceSwitch(AohiEntity, SwitchEntity):
    """Master on/off switch for the whole charger."""

    _attr_name = "Power"

    def __init__(self, coordinator: AohiCoordinator, sn: str, device: dict[str, Any]) -> None:
        super().__init__(coordinator, sn, device)
        self._attr_unique_id = f"{sn}_power"

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data.get(self._sn)
        return status.get("poweron") if status else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.async_set_whole_power(self._sn, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.async_set_whole_power(self._sn, False)
        await self.coordinator.async_request_refresh()


class AohiPortSwitch(AohiEntity, SwitchEntity):
    """On/off switch for a single USB port."""

    def __init__(
        self, coordinator: AohiCoordinator, sn: str, device: dict[str, Any], port: str
    ) -> None:
        super().__init__(coordinator, sn, device)
        self._port = port
        self._attr_name = f"Port {port}"
        self._attr_unique_id = f"{sn}_port_{port}"

    def _port_data(self) -> dict[str, Any] | None:
        status = self.coordinator.data.get(self._sn)
        if not status:
            return None
        key = "cPorts" if self._port.startswith("C") else "aPorts"
        for port in status.get(key, []):
            if port.get("name") == self._port:
                return port
        return None

    @property
    def is_on(self) -> bool | None:
        port = self._port_data()
        return port.get("poweron") if port else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.async_set_port_power(self._sn, self._port, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.async_set_port_power(self._sn, self._port, False)
        await self.coordinator.async_request_refresh()

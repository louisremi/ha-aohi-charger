"""Select platform for the AOHI Smart Charger integration."""
from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODES, signal_new_device
from .coordinator import AohiCoordinator
from .switch import device_info

MODE_BY_NAME = {name: value for value, name in MODES.items()}


def _build_entities(
    coordinator: AohiCoordinator, sn: str, device: dict[str, Any]
) -> list[SelectEntity]:
    return [AohiModeSelect(coordinator, sn, device)]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the charging-mode selector for every AOHI device on this account."""
    coordinator: AohiCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SelectEntity] = []
    for sn, device in coordinator.devices.items():
        entities.extend(_build_entities(coordinator, sn, device))
    async_add_entities(entities)

    @callback
    def _async_new_devices(new_devices: dict[str, dict[str, Any]]) -> None:
        new_entities: list[SelectEntity] = []
        for sn, device in new_devices.items():
            new_entities.extend(_build_entities(coordinator, sn, device))
        async_add_entities(new_entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, signal_new_device(entry.entry_id), _async_new_devices
        )
    )


class AohiModeSelect(CoordinatorEntity[AohiCoordinator], SelectEntity):
    """Charging mode selector (Turbo / Smart / Custom)."""

    _attr_has_entity_name = True
    _attr_name = "Charging Mode"
    _attr_options: ClassVar[list[str]] = list(MODES.values())

    def __init__(self, coordinator: AohiCoordinator, sn: str, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"{sn}_mode"
        self._attr_device_info = device_info(sn, device)

    @property
    def current_option(self) -> str | None:
        status = self.coordinator.data.get(self._sn)
        if not status:
            return None
        return MODES.get(status.get("mode"))

    async def async_select_option(self, option: str) -> None:
        mode = MODE_BY_NAME[option]
        await self.coordinator.client.async_set_mode(self._sn, mode)
        await self.coordinator.async_request_refresh()

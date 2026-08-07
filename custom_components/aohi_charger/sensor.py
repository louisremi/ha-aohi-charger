"""Sensor platform for the AOHI Smart Charger integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ALL_PORTS, DOMAIN, signal_new_device
from .coordinator import AohiCoordinator
from .switch import device_info


def _build_entities(
    coordinator: AohiCoordinator, sn: str, device: dict[str, Any]
) -> list[SensorEntity]:
    entities: list[SensorEntity] = [
        AohiPortPowerSensor(coordinator, sn, device, port) for port in ALL_PORTS
    ]
    entities.append(AohiTemperatureSensor(coordinator, sn, device))
    entities.append(AohiTotalPowerSensor(coordinator, sn, device))
    return entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up power sensors for every AOHI device on this account, now and in the future."""
    coordinator: AohiCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for sn, device in coordinator.devices.items():
        entities.extend(_build_entities(coordinator, sn, device))
    async_add_entities(entities)

    @callback
    def _async_new_devices(new_devices: dict[str, dict[str, Any]]) -> None:
        new_entities: list[SensorEntity] = []
        for sn, device in new_devices.items():
            new_entities.extend(_build_entities(coordinator, sn, device))
        async_add_entities(new_entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, signal_new_device(entry.entry_id), _async_new_devices
        )
    )


class AohiPortPowerSensor(CoordinatorEntity[AohiCoordinator], SensorEntity):
    """Live power draw of a single USB port."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self, coordinator: AohiCoordinator, sn: str, device: dict[str, Any], port: str
    ) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._port = port
        self._attr_name = f"Port {port} Power"
        self._attr_unique_id = f"{sn}_port_{port}_power"
        self._attr_device_info = device_info(sn, device)

    @property
    def native_value(self) -> float | None:
        status = self.coordinator.data.get(self._sn)
        if not status:
            return None
        key = "cPorts" if self._port.startswith("C") else "aPorts"
        for port in status.get(key, []):
            if port.get("name") == self._port:
                power = port.get("power")
                # The device reports power in centiwatts (e.g. 9947 == 99.47W).
                return power / 100 if power is not None else None
        return None


class AohiTemperatureSensor(CoordinatorEntity[AohiCoordinator], SensorEntity):
    """Internal temperature of the charger."""

    _attr_has_entity_name = True
    _attr_name = "Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: AohiCoordinator, sn: str, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"{sn}_temperature"
        self._attr_device_info = device_info(sn, device)

    @property
    def native_value(self) -> float | None:
        status = self.coordinator.data.get(self._sn)
        return status.get("batTemp") if status else None


class AohiTotalPowerSensor(CoordinatorEntity[AohiCoordinator], SensorEntity):
    """Total output power across all ports."""

    _attr_has_entity_name = True
    _attr_name = "Total Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: AohiCoordinator, sn: str, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"{sn}_total_power"
        self._attr_device_info = device_info(sn, device)

    @property
    def native_value(self) -> float | None:
        status = self.coordinator.data.get(self._sn)
        # Unlike per-port power, allPower is already reported in whole watts.
        return status.get("allPower") if status else None

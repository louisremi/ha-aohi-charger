"""Sensor platform for the AOHI Smart Charger integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ALL_PORTS, DOMAIN, signal_new_device
from .coordinator import AohiCoordinator
from .entity import AohiEntity


def _build_entities(
    coordinator: AohiCoordinator, sn: str, device: dict[str, Any]
) -> list[SensorEntity]:
    entities: list[SensorEntity] = [
        AohiPortPowerSensor(coordinator, sn, device, port) for port in ALL_PORTS
    ]
    entities.append(AohiTemperatureSensor(coordinator, sn, device))
    entities.append(AohiTotalPowerSensor(coordinator, sn, device))
    entities.append(AohiSignalStrengthSensor(coordinator, sn, device))
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


class AohiPortPowerSensor(AohiEntity, SensorEntity):
    """Live power draw of a single USB port."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self, coordinator: AohiCoordinator, sn: str, device: dict[str, Any], port: str
    ) -> None:
        super().__init__(coordinator, sn, device)
        self._port = port
        self._attr_name = f"Port {port} Power"
        self._attr_unique_id = f"{sn}_port_{port}_power"

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


class AohiTemperatureSensor(AohiEntity, SensorEntity):
    """Internal temperature of the charger."""

    _attr_name = "Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: AohiCoordinator, sn: str, device: dict[str, Any]) -> None:
        super().__init__(coordinator, sn, device)
        self._attr_unique_id = f"{sn}_temperature"

    @property
    def native_value(self) -> float | None:
        status = self.coordinator.data.get(self._sn)
        # Despite the "bat" name (this charger has no battery), batTemp is the
        # device's own temperature in whole degrees Celsius -- live captures
        # showed 27 idle and 32 under load, consistent with room temperature.
        return status.get("batTemp") if status else None


class AohiTotalPowerSensor(AohiEntity, SensorEntity):
    """Total output power across all ports."""

    _attr_name = "Total Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: AohiCoordinator, sn: str, device: dict[str, Any]) -> None:
        super().__init__(coordinator, sn, device)
        self._attr_unique_id = f"{sn}_total_power"

    @property
    def native_value(self) -> float | None:
        status = self.coordinator.data.get(self._sn)
        # Unlike per-port power, allPower is already reported in whole watts.
        # Verified against a live capture: allPower was 99 while the only
        # active port (C2) reported power 9947, i.e. 99.47 W.
        return status.get("allPower") if status else None


class AohiSignalStrengthSensor(AohiEntity, SensorEntity):
    """WiFi signal strength (RSSI) of the charger."""

    _attr_name = "Signal Strength"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AohiCoordinator, sn: str, device: dict[str, Any]) -> None:
        super().__init__(coordinator, sn, device)
        self._attr_unique_id = f"{sn}_rssi"

    @property
    def native_value(self) -> float | None:
        status = self.coordinator.data.get(self._sn)
        return status.get("rssi") if status else None

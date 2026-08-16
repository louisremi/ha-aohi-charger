"""Shared entity base for the AOHI Smart Charger integration."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AohiCoordinator


def device_info(sn: str, device: dict[str, Any]) -> DeviceInfo:
    """Build the shared HA device entry for one charger."""
    connections = set()
    if mac := device.get("mac"):
        connections.add((dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac)))

    return DeviceInfo(
        identifiers={(DOMAIN, sn)},
        connections=connections,
        name=device.get("name") or sn,
        manufacturer="AOHI",
        model=device.get("model"),
        sw_version=device.get("version"),
        serial_number=sn,
    )


class AohiEntity(CoordinatorEntity[AohiCoordinator]):
    """Base for every AOHI entity: one charger, keyed on its serial."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AohiCoordinator, sn: str, device: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_device_info = device_info(sn, device)

    @property
    def available(self) -> bool:
        """Whether this particular charger answered the last poll.

        The coordinator deliberately survives one charger going quiet, so
        ``last_update_success`` alone would leave an unplugged charger looking
        healthy while every value reads as unknown. A charger missing from the
        last result is unreachable, which is a different thing from a charger
        reporting zero.
        """
        return super().available and self._sn in (self.coordinator.data or {})

# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Shared base entity."""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ChandlerCoordinator
from .models import firmware_version, serial_number, valve_type_name


class ChandlerEntity(CoordinatorEntity[ChandlerCoordinator]):
    """An entity that reads one value from a valve."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ChandlerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        address = coordinator.config_entry.data[CONF_ADDRESS]
        state = coordinator.data or {}
        self._attr_unique_id = f"{address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            name=coordinator.config_entry.title,
            manufacturer="Chandler Systems",
            model=valve_type_name(state),
            sw_version=firmware_version(state),
            serial_number=serial_number(state),
        )

    @property
    def available(self) -> bool:
        """Entities are unavailable whenever the connection to the valve is down."""
        return self.coordinator.connected

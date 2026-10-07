# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Binary sensors for Chandler valves."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ChandlerConfigEntry
from .coordinator import ChandlerCoordinator
from .entity import ChandlerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChandlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the regeneration motor sensor."""
    async_add_entities([ChandlerRegenMotor(entry.runtime_data)])


class ChandlerRegenMotor(ChandlerEntity, BinarySensorEntity):
    """On while the valve motor is moving to a regeneration position (``dlr``)."""

    _attr_name = "Regeneration motor moving"

    def __init__(self, coordinator: ChandlerCoordinator) -> None:
        super().__init__(coordinator, "regen_motor_moving")

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get("dlr")
        return None if value is None else bool(value)

# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Buttons for Chandler valves."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
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
    """Add the clock sync button."""
    async_add_entities([ChandlerSyncClockButton(entry.runtime_data)])


class ChandlerSyncClockButton(ChandlerEntity, ButtonEntity):
    """Set the valve's clock to Home Assistant's local time.

    This is the only thing the integration ever writes to a valve (hour, minute, seconds).
    The write is held until the next minute begins so the valve's seconds start at 0 in step
    with real time, so a press can take up to a minute to finish. It refuses while the valve
    is regenerating.
    """

    _attr_name = "Sync clock"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: ChandlerCoordinator) -> None:
        super().__init__(coordinator, "sync_clock")

    async def async_press(self) -> None:
        await self.coordinator.async_sync_clock()

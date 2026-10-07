# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Chandler Signature: Chandler Systems water valves over their Bluetooth Signature API."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import PLATFORMS
from .coordinator import CannotConnect, ChandlerCoordinator
from .session import InvalidAuth

type ChandlerConfigEntry = ConfigEntry[ChandlerCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ChandlerConfigEntry) -> bool:
    """Connect to the valve and set up its entities."""
    coordinator = ChandlerCoordinator(hass, entry)
    try:
        await coordinator.async_start()
    except InvalidAuth as err:
        raise ConfigEntryAuthFailed("The valve rejected the API token") from err
    except CannotConnect as err:
        raise ConfigEntryNotReady(f"Could not reach the valve: {err}") from err

    entry.runtime_data = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_stop()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ChandlerConfigEntry) -> bool:
    """Unload the entry and hang up on the valve."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded

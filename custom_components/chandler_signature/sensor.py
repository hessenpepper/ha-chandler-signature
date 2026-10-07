# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Sensors for Chandler valves."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfMass,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import ChandlerConfigEntry
from .coordinator import ChandlerCoordinator
from .entity import ChandlerEntity
from .models import (
    accumulate_daily,
    battery_volts,
    is_metered_softener,
    is_softener,
    salt_percent,
    scaled,
)


@dataclass(frozen=True, kw_only=True)
class ChandlerSensorDescription(SensorEntityDescription):
    """A sensor and how to read it from the valve's data."""

    value_fn: Callable[[dict[str, Any]], float | None]
    applies: Callable[[dict[str, Any]], bool] = lambda _state: True


def _has_salt_tank(state: dict[str, Any]) -> bool:
    return is_softener(state) and (state.get("dbts") or 0) > 0


SENSORS: tuple[ChandlerSensorDescription, ...] = (
    ChandlerSensorDescription(
        key="water_used_today",
        name="Water used today",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        value_fn=lambda s: scaled(s, "dwu", 100),
    ),
    ChandlerSensorDescription(
        key="average_water_used",
        name="Average daily water use",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        value_fn=lambda s: scaled(s, "dwau", 100),
    ),
    ChandlerSensorDescription(
        key="peak_flow_today",
        name="Peak flow today",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolumeFlowRate.GALLONS_PER_MINUTE,
        value_fn=lambda s: scaled(s, "dpfd", 100),
    ),
    ChandlerSensorDescription(
        key="battery_voltage",
        name="Battery",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=battery_volts,
        applies=lambda s: battery_volts(s) is not None,
    ),
    ChandlerSensorDescription(
        key="gallons_remaining",
        name="Treated water remaining",
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        value_fn=lambda s: scaled(s, "dtgr", 100),
        applies=is_metered_softener,
    ),
    ChandlerSensorDescription(
        key="salt_remaining",
        name="Salt remaining",
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfMass.POUNDS,
        value_fn=lambda s: scaled(s, "dbtr", 10),
        applies=_has_salt_tank,
    ),
    ChandlerSensorDescription(
        key="salt_level",
        name="Salt level",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=salt_percent,
        applies=_has_salt_tank,
    ),
    ChandlerSensorDescription(
        key="water_hardness",
        name="Water hardness setting",
        native_unit_of_measurement="gpg",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: scaled(s, "dwh", 1),
        applies=is_softener,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChandlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the sensors that make sense for this valve type."""
    coordinator = entry.runtime_data
    state = coordinator.data or {}
    entities: list[SensorEntity] = [
        ChandlerSensor(coordinator, description)
        for description in SENSORS
        if description.applies(state)
    ]
    entities.append(ChandlerWaterTotalSensor(coordinator))
    async_add_entities(entities)


class ChandlerWaterTotalSensor(ChandlerEntity, RestoreEntity, SensorEntity):
    """Lifetime water total built from the valve's daily counter.

    The valve only reports "water used today", which resets at its midnight. This sensor
    adds up the changes so it keeps growing, which is what the Energy dashboard needs.
    It starts at zero when first created, and usage while Home Assistant is disconnected
    across a daily reset is only counted from the reset onward.
    """

    _attr_name = "Water used total"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.GALLONS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: ChandlerCoordinator) -> None:
        super().__init__(coordinator, "water_used_total")
        self._total = 0.0
        self._last: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (previous := await self.async_get_last_state()) is not None:
            with suppress(ValueError, TypeError):
                self._total = float(previous.state)
            with suppress(ValueError, TypeError):
                self._last = float(previous.attributes["last_daily_reading"])
        self._fold_in(self.coordinator.data)

    def _fold_in(self, data: dict[str, Any] | None) -> None:
        reading = scaled(data or {}, "dwu", 100)
        if reading is None:
            return
        self._total = accumulate_daily(self._total, self._last, reading)
        self._last = reading

    @callback
    def _handle_coordinator_update(self) -> None:
        self._fold_in(self.coordinator.data)
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        return round(self._total, 2)

    @property
    def extra_state_attributes(self) -> dict[str, float | None]:
        return {"last_daily_reading": self._last}


class ChandlerSensor(ChandlerEntity, SensorEntity):
    """A numeric value reported by the valve."""

    entity_description: ChandlerSensorDescription

    def __init__(self, coordinator: ChandlerCoordinator, description: ChandlerSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data or {})

# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Sensors for Chandler valves."""

from __future__ import annotations

from collections.abc import Callable
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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ChandlerConfigEntry
from .coordinator import ChandlerCoordinator
from .entity import ChandlerEntity
from .models import (
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
    async_add_entities(
        ChandlerSensor(coordinator, description)
        for description in SENSORS
        if description.applies(state)
    )


class ChandlerSensor(ChandlerEntity, SensorEntity):
    """A numeric value reported by the valve."""

    entity_description: ChandlerSensorDescription

    def __init__(self, coordinator: ChandlerCoordinator, description: ChandlerSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data or {})

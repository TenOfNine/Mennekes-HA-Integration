"""Number platform for Mennekes AMTRON Charge Control (ECU) - HEMS current/power limit."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    MIN_CURRENT_A,
    MIN_POWER_1PHASE_W,
    MIN_POWER_3PHASE_W,
    NOMINAL_PHASE_VOLTAGE_V,
    PHASE_MODE_DISABLED,
    PHASE_MODE_SINGLE_PHASE,
)
from .coordinator import AmtronCoordinator, AmtronData
from .entity import AmtronEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the AMTRON number entities from a config entry."""
    coordinator: AmtronCoordinator = entry.runtime_data
    entities: list[NumberEntity] = [AmtronCurrentLimitNumber(coordinator, entry)]
    if coordinator.phase_mode != PHASE_MODE_DISABLED:
        entities.append(AmtronPowerLimitNumber(coordinator, entry))
    async_add_entities(entities)


class AmtronCurrentLimitNumber(AmtronEntity, NumberEntity):
    """Maximum charging current, in Amps (requirement 2).

    0 to the configured maximum: 0 means "paused" (the register's own
    documented meaning for that value), 6 A and up is the normal operating
    range (values 1-5 are technically writable but not meaningful per IEC
    61851). The upper bound comes from the "Max. Ladestrom" option
    (Settings -> Devices & Services -> Mennekes AMTRON -> Configure) - the
    ECU does not expose a register this integration could read that ceiling
    back from automatically, so it must be set to match your supply
    circuit's actual fusing.
    """

    _attr_translation_key = "current_limit"
    _unique_id_suffix = "current_limit"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_step = 1
    _attr_native_min_value = 0
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:current-ac"

    def __init__(self, coordinator: AmtronCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_native_max_value = coordinator.max_current_a

    @property
    def native_value(self) -> int:
        # Reported as-is, including 0 ("paused") - never faked, so the
        # slider always reflects what the wallbox is actually doing.
        data: AmtronData = self.coordinator.data
        return data.current_limit_a

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        return {"recommended_min_a": MIN_CURRENT_A}

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_current_limit(int(value))


class AmtronPowerLimitNumber(AmtronEntity, NumberEntity):
    """Desired charging power, in Watts - automatic 1-phase/3-phase switching.

    Only created when the "Phasenumschaltung" option (Settings -> Devices &
    Services -> Mennekes AMTRON -> Configure) is not "Disabled". Writes
    HEMS_POWER_LIMIT (register 1002) instead of HEMS_CURRENT_LIMIT (1000) -
    per the wallbox's own documented value ranges, it auto-selects 1-phase
    or 3-phase charging depending on which range the requested power falls
    into (see coordinator._clamp_power_limit_w). Only meaningful if your
    unit actually has phase-switching hardware installed and configured;
    see the README for what "phase_mode" changes and its caveats.
    """

    _attr_translation_key = "power_limit"
    _unique_id_suffix = "power_limit"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_step = 10
    _attr_native_min_value = 0
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator: AmtronCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        phases = 1 if coordinator.phase_mode == PHASE_MODE_SINGLE_PHASE else 3
        self._attr_native_max_value = coordinator.max_current_a * phases * NOMINAL_PHASE_VOLTAGE_V

    @property
    def native_value(self) -> int | None:
        data: AmtronData = self.coordinator.data
        return data.power_limit_w

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        return {
            "recommended_min_1phase_w": MIN_POWER_1PHASE_W,
            "recommended_min_3phase_w": MIN_POWER_3PHASE_W,
        }

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_power_limit(int(value))

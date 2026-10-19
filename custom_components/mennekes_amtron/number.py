"""Number platform for Mennekes AMTRON Charge Control (ECU) - HEMS current/power limit."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfElectricCurrent, UnitOfPower, UnitOfTime
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
from .charge_controller import ChargeController
from .coordinator import AmtronCoordinator, AmtronData
from .entity import AmtronEntity


@dataclass(frozen=True)
class _PvSetting:
    key: str  # attribute of charge_controller.PvSettings
    translation_key: str
    unit: str
    min_value: int
    max_value: int
    icon: str


_PV_SETTINGS = (
    _PvSetting("soc_start_pct", "pv_soc_start", PERCENTAGE, 0, 100, "mdi:battery-arrow-up"),
    _PvSetting("soc_stop_pct", "pv_soc_stop", PERCENTAGE, 0, 100, "mdi:battery-arrow-down"),
    _PvSetting("min_switch_interval_s", "pv_min_switch_interval", UnitOfTime.SECONDS, 30, 600, "mdi:timer-sand"),
    _PvSetting("buffer_time_min", "pv_buffer_time", UnitOfTime.MINUTES, 0, 60, "mdi:battery-clock"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the AMTRON number entities from a config entry."""
    runtime = entry.runtime_data
    coordinator: AmtronCoordinator = runtime.coordinator
    controller: ChargeController = runtime.controller
    entities: list[NumberEntity] = [AmtronCurrentLimitNumber(coordinator, entry, controller)]
    if coordinator.phase_mode != PHASE_MODE_DISABLED:
        entities.append(AmtronPowerLimitNumber(coordinator, entry, controller))
    if controller.has_pv_sources:
        entities.extend(AmtronPvSettingNumber(coordinator, entry, controller, setting) for setting in _PV_SETTINGS)
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

    def __init__(self, coordinator: AmtronCoordinator, entry: ConfigEntry, controller: ChargeController) -> None:
        super().__init__(coordinator, entry)
        self._controller = controller
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
        # A manual override: otherwise the PV controller would overwrite
        # the value again within one control cycle.
        await self._controller.async_manual_action(lambda: self.coordinator.async_set_current_limit(int(value)))


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

    def __init__(self, coordinator: AmtronCoordinator, entry: ConfigEntry, controller: ChargeController) -> None:
        super().__init__(coordinator, entry)
        self._controller = controller
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
        await self._controller.async_manual_action(lambda: self.coordinator.async_set_power_limit(int(value)))


class AmtronPvSettingNumber(AmtronEntity, RestoreNumber):
    """One PV surplus controller setting (SoC thresholds, intervals).

    An entity rather than an options-flow field: changing options reloads
    the integration, which would empty the 2-minute surplus average.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(
        self, coordinator: AmtronCoordinator, entry: ConfigEntry, controller: ChargeController, setting: _PvSetting
    ) -> None:
        self._unique_id_suffix = setting.translation_key
        super().__init__(coordinator, entry)
        self._controller = controller
        self._setting = setting
        self._attr_translation_key = setting.translation_key
        self._attr_native_unit_of_measurement = setting.unit
        self._attr_native_min_value = setting.min_value
        self._attr_native_max_value = setting.max_value
        self._attr_icon = setting.icon

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._controller.restore_setting(self._setting.key, int(last.native_value))

    @property
    def native_value(self) -> int:
        return getattr(self._controller.settings, self._setting.key)

    async def async_set_native_value(self, value: float) -> None:
        self._controller.update_setting(self._setting.key, int(value))
        self.async_write_ha_state()

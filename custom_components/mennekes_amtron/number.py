"""Number platform for Mennekes AMTRON Charge Control (ECU) - HEMS current limit."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MIN_CURRENT_A
from .coordinator import AmtronCoordinator, AmtronData
from .entity import AmtronEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the AMTRON current-limit number entity from a config entry."""
    coordinator: AmtronCoordinator = entry.runtime_data
    async_add_entities([AmtronCurrentLimitNumber(coordinator, entry)])


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
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_step = 1
    _attr_native_min_value = 0
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:current-ac"

    def __init__(self, coordinator: AmtronCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_current_limit"
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

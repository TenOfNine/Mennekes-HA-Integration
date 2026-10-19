"""Select platform for Mennekes AMTRON Charge Control (ECU) - charge mode."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .charge_controller import ChargeController
from .const import CHARGE_MODES
from .coordinator import AmtronCoordinator
from .entity import AmtronEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the charge-mode select entity from a config entry."""
    runtime = entry.runtime_data
    async_add_entities([AmtronChargeModeSelect(runtime.coordinator, entry, runtime.controller)])


class AmtronChargeModeSelect(AmtronEntity, SelectEntity, RestoreEntity):
    """PV surplus / immediate / off / manual - see charge_controller.py.

    Restored after a restart, so e.g. "off" stays off.
    """

    _attr_translation_key = "charge_mode"
    _unique_id_suffix = "charge_mode"
    _attr_options = CHARGE_MODES
    _attr_icon = "mdi:car-electric"

    def __init__(self, coordinator: AmtronCoordinator, entry: ConfigEntry, controller: ChargeController) -> None:
        super().__init__(coordinator, entry)
        self._controller = controller

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._controller.restore_mode(last_state.state)
        self.async_on_remove(self._controller.async_add_listener(self.async_write_ha_state))

    @property
    def current_option(self) -> str:
        return self._controller.mode

    async def async_select_option(self, option: str) -> None:
        await self._controller.async_set_mode(option)

"""Switch platform for Mennekes AMTRON Charge Control (ECU) - charge mode settings."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .charge_controller import ChargeController
from .coordinator import AmtronCoordinator
from .entity import AmtronEntity

_SETTING_KEY = "immediate_auto_return"  # attribute of charge_controller.PvSettings


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the AMTRON switch entities from a config entry."""
    runtime = entry.runtime_data
    if runtime.controller.has_pv_sources:
        async_add_entities([AmtronImmediateAutoReturnSwitch(runtime.coordinator, entry, runtime.controller)])


class AmtronImmediateAutoReturnSwitch(AmtronEntity, SwitchEntity, RestoreEntity):
    """Switch "Immediate" back to "PV surplus" once the car is full.

    The wallbox doesn't know the car's state of charge, so "full" means the
    car has paused charging itself (status "Suspended (vehicle)") for 10
    minutes while current was offered. Only created with PV sources
    configured, since PV surplus does nothing without them.
    """

    _attr_translation_key = _SETTING_KEY
    _unique_id_suffix = _SETTING_KEY
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:solar-power"

    def __init__(self, coordinator: AmtronCoordinator, entry: ConfigEntry, controller: ChargeController) -> None:
        super().__init__(coordinator, entry)
        self._controller = controller

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in (STATE_ON, STATE_OFF):
            self._controller.restore_setting(_SETTING_KEY, last_state.state == STATE_ON)

    @property
    def is_on(self) -> bool:
        return self._controller.settings.immediate_auto_return

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.update_setting(_SETTING_KEY, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.update_setting(_SETTING_KEY, False)
        self.async_write_ha_state()

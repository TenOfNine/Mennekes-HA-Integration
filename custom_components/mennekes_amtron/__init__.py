"""The Mennekes AMTRON Charge Control (ECU) integration."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_MAX_CURRENT_A,
    CONF_PAUSE_CURRENT_A,
    CONF_PHASE_MODE,
    CONF_PV_BATTERY_DISCHARGE_ENTITY,
    CONF_PV_FEED_IN_ENTITIES,
    CONF_PV_GRID_IMPORT_ENTITY,
    CONF_START_CURRENT_A,
    CONF_UNIT_ID,
    DEFAULT_MAX_CURRENT_A,
    DEFAULT_PAUSE_CURRENT_A,
    DEFAULT_PHASE_MODE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_START_CURRENT_A,
)
from .coordinator import AmtronCoordinator
from .modbus_client import AmtronModbusClient
from .pv_surplus import PvSurplusTracker

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER, Platform.BUTTON]


@dataclass
class AmtronRuntimeData:
    """What a config entry's platforms share (entry.runtime_data)."""

    coordinator: AmtronCoordinator
    pv_surplus: PvSurplusTracker | None  # None until PV feed-in entities are configured


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Mennekes AMTRON from a config entry."""
    client = AmtronModbusClient(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_UNIT_ID],
    )
    coordinator = AmtronCoordinator(
        hass,
        entry,
        client,
        DEFAULT_SCAN_INTERVAL,
        max_current_a=entry.options.get(CONF_MAX_CURRENT_A, DEFAULT_MAX_CURRENT_A),
        pause_current_a=entry.options.get(CONF_PAUSE_CURRENT_A, DEFAULT_PAUSE_CURRENT_A),
        start_current_a=entry.options.get(CONF_START_CURRENT_A, DEFAULT_START_CURRENT_A),
        phase_mode=entry.options.get(CONF_PHASE_MODE, DEFAULT_PHASE_MODE),
    )

    # Raises ConfigEntryNotReady on failure, which makes HA retry setup
    # automatically (e.g. wallbox briefly offline, network hiccup, ...).
    await coordinator.async_config_entry_first_refresh()

    pv_surplus = None
    if feed_in_entities := entry.options.get(CONF_PV_FEED_IN_ENTITIES):
        pv_surplus = PvSurplusTracker(
            hass,
            coordinator,
            feed_in_entities,
            grid_import_entity=entry.options.get(CONF_PV_GRID_IMPORT_ENTITY),
            battery_discharge_entity=entry.options.get(CONF_PV_BATTERY_DISCHARGE_ENTITY),
        )
        entry.async_on_unload(pv_surplus.async_start())

    entry.runtime_data = AmtronRuntimeData(coordinator=coordinator, pv_surplus=pv_surplus)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry (re-running this function with the new values) when
    # the user saves changes in the "Configure" options dialog.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry after its options were changed."""
    await hass.config_entries.async_reload(entry.entry_id)

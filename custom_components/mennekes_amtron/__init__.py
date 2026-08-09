"""The Mennekes AMTRON Charge Control (ECU) integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_MAX_CURRENT_A,
    CONF_PAUSE_CURRENT_A,
    CONF_UNIT_ID,
    DEFAULT_MAX_CURRENT_A,
    DEFAULT_PAUSE_CURRENT_A,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import AmtronCoordinator
from .modbus_client import AmtronModbusClient

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER, Platform.BUTTON]


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
    )

    # Raises ConfigEntryNotReady on failure, which makes HA retry setup
    # automatically (e.g. wallbox briefly offline, network hiccup, ...).
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
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

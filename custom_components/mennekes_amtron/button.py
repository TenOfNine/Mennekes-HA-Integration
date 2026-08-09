"""Button platform for Mennekes AMTRON Charge Control (ECU) - start/pause charging."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AmtronCoordinator
from .entity import AmtronEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up AMTRON control buttons from a config entry."""
    coordinator: AmtronCoordinator = entry.runtime_data
    async_add_entities(
        [
            AmtronStartButton(coordinator, entry),
            AmtronPauseButton(coordinator, entry),
        ]
    )


class AmtronStartButton(AmtronEntity, ButtonEntity):
    """Starts charging (requirement 3) by writing a synthetic OCPP IdTag.

    This has the same effect as presenting an RFID card at the reader. It
    only works if "Modbus Slave Allow Start/Stop Transaction" AND
    "kostenloses Laden" (free charging) are both enabled on the wallbox -
    see README.md. Since your car is permanently plugged in, no further
    action should be needed for a session to actually begin once authorized.
    """

    _attr_translation_key = "start_charging"
    _unique_id_suffix = "start_charging"
    _attr_icon = "mdi:play-circle-outline"

    async def async_press(self) -> None:
        await self.coordinator.async_start_charging()


class AmtronPauseButton(AmtronEntity, ButtonEntity):
    """Pauses charging by writing the configured "pause current" (0 A by default).

    The written value comes from the "Ladestrom bei Pause" option (Settings
    -> Devices & Services -> Mennekes AMTRON -> Configure); writing "0" is
    the ECU's own documented way of pausing a session. Note this is a
    *pause*, not a full session termination/de-authorization - the ECU spec
    does not document a separate "end session" register the way the HCC3
    controller does. Resume by setting the current-limit number entity back
    to a normal value, or by pressing "Start charging" again.
    """

    _attr_translation_key = "pause_charging"
    _unique_id_suffix = "pause_charging"
    _attr_icon = "mdi:pause-circle-outline"

    async def async_press(self) -> None:
        await self.coordinator.async_pause_charging()

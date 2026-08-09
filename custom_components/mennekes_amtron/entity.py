"""Shared base entity for Mennekes AMTRON (HCC3) platforms."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import AmtronCoordinator


class AmtronEntity(CoordinatorEntity[AmtronCoordinator]):
    """Base entity that wires up shared device info for every platform."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AmtronCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

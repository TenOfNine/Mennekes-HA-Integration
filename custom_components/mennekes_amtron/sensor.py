"""Sensor platform for Mennekes AMTRON Charge Control (ECU)."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CP_STATUS
from .coordinator import AmtronCoordinator, AmtronData
from .entity import AmtronEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up AMTRON sensors from a config entry."""
    coordinator: AmtronCoordinator = entry.runtime_data
    async_add_entities(
        [
            AmtronPowerSensor(coordinator, entry),
            AmtronStatusSensor(coordinator, entry),
            AmtronSessionEnergySensor(coordinator, entry),
            AmtronTotalEnergySensor(coordinator, entry),
            AmtronErrorSensor(coordinator, entry),
            AmtronFirmwareVersionSensor(coordinator, entry),
        ]
    )


class AmtronPowerSensor(AmtronEntity, SensorEntity):
    """Current charging power, in kW (requirement 1). Sum of L1+L2+L3."""

    _attr_translation_key = "charging_power"
    _unique_id_suffix = "charging_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float:
        data: AmtronData = self.coordinator.data
        return round(data.power_w / 1000, 3)


class AmtronStatusSensor(AmtronEntity, SensorEntity):
    """OCPP charge-point status (bonus, helpful for automations)."""

    _attr_translation_key = "status"
    _unique_id_suffix = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [*CP_STATUS.values(), "unknown"]
    _attr_icon = "mdi:ev-station"

    @property
    def native_value(self) -> str:
        data: AmtronData = self.coordinator.data
        return data.status


class AmtronSessionEnergySensor(AmtronEntity, SensorEntity):
    """Energy charged during the current session (bonus).

    Backed by a 16-bit register (max ~65.5 kWh per session) - the 32-bit
    variant of this register requires firmware >= 5.22, so the 16-bit one
    is used here for broader compatibility. See README.md.
    """

    _attr_translation_key = "session_energy"
    _unique_id_suffix = "session_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> int:
        data: AmtronData = self.coordinator.data
        return data.session_energy_wh


class AmtronTotalEnergySensor(AmtronEntity, SensorEntity):
    """Total (lifetime) energy delivered, from the wallbox's built-in meter.

    Backed by register 200-201 (METER_ENERG_L1), which - per the
    manufacturer's own meter-compatibility table - carries the cumulative
    "Total Energy" for every documented meter model, not just phase 1.
    """

    _attr_translation_key = "total_energy"
    _unique_id_suffix = "total_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> int | None:
        data: AmtronData = self.coordinator.data
        return data.total_energy_wh


class AmtronErrorSensor(AmtronEntity, SensorEntity):
    """Decoded ECU error flags (system errors), useful for troubleshooting.

    Unlike the HCC3 controller, the ECU can report *several* error
    conditions at once as a bitmask, so the state is a summary and every
    active (named) flag is listed in the entity's attributes. The three
    "reserved" error-code register pairs (ERROR_CODES_1-3) are read too and
    exposed as raw values, even though the manufacturer's document does not
    currently define any named bits for them - in case your firmware
    populates something there that a future spec revision documents.
    """

    _attr_translation_key = "error"
    _unique_id_suffix = "error"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:alert-circle-outline"

    @property
    def native_value(self) -> str:
        data: AmtronData = self.coordinator.data
        if not data.active_errors:
            return "no_error"
        return ", ".join(data.active_errors)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data: AmtronData = self.coordinator.data
        reserved_1, reserved_2, reserved_3 = data.reserved_error_words
        return {
            "error_bitmask": data.error_bits,
            "active_errors": data.active_errors,
            "reserved_error_codes_1": reserved_1,
            "reserved_error_codes_2": reserved_2,
            "reserved_error_codes_3": reserved_3,
        }


class AmtronFirmwareVersionSensor(AmtronEntity, SensorEntity):
    """ECU application firmware version (bonus, useful for support requests)."""

    _attr_translation_key = "firmware_version"
    _unique_id_suffix = "firmware_version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chip"

    @property
    def native_value(self) -> str | None:
        data: AmtronData = self.coordinator.data
        return data.firmware_version

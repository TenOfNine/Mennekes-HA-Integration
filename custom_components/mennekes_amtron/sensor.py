"""Sensor platform for Mennekes AMTRON Charge Control (ECU)."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .charge_controller import CONTROL_STATES, ChargeController
from .const import CP_STATUS, ERROR_STATES, PV_AVERAGE_WINDOW_S
from .coordinator import AmtronCoordinator, AmtronData, summarize_errors
from .entity import AmtronEntity
from .pv_surplus import PvSurplusTracker


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up AMTRON sensors from a config entry."""
    coordinator: AmtronCoordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        AmtronPowerSensor(coordinator, entry),
        AmtronStatusSensor(coordinator, entry),
        AmtronSessionEnergySensor(coordinator, entry),
        AmtronTotalEnergySensor(coordinator, entry),
        AmtronErrorSensor(coordinator, entry),
        AmtronFirmwareVersionSensor(coordinator, entry),
    ]
    if (pv_surplus := entry.runtime_data.pv_surplus) is not None:
        entities.append(AmtronPvSurplusSensor(coordinator, entry, pv_surplus))
    entities.append(AmtronChargeControlSensor(coordinator, entry, entry.runtime_data.controller))
    async_add_entities(entities)


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
    conditions at once as a bitmask. The state is "no_error", the one
    active flag, or "multiple_errors" - an enum, so the frontend shows it
    translated - and every active (named) flag is listed in the entity's
    attributes. The three
    "reserved" error-code register pairs (ERROR_CODES_1-3) are read too and
    exposed as raw values, even though the manufacturer's document does not
    currently define any named bits for them - in case your firmware
    populates something there that a future spec revision documents.
    """

    _attr_translation_key = "error"
    _unique_id_suffix = "error"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ERROR_STATES
    _attr_icon = "mdi:alert-circle-outline"

    @property
    def native_value(self) -> str:
        data: AmtronData = self.coordinator.data
        return summarize_errors(data.active_errors)

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


class AmtronPvSurplusSensor(AmtronEntity, SensorEntity):
    """PV surplus averaged over PV_AVERAGE_WINDOW_S - see pv_surplus.py.

    Read-only: it only reports what a surplus-charging controller would
    regulate on. The latest sample's individual sources are attributes, so
    the calculation can be checked against the inverter's own app.
    """

    _attr_translation_key = "pv_surplus"
    _unique_id_suffix = "pv_surplus"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:solar-power"

    def __init__(self, coordinator: AmtronCoordinator, entry: ConfigEntry, tracker: PvSurplusTracker) -> None:
        super().__init__(coordinator, entry)
        self._tracker = tracker

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._tracker.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> float | None:
        average = self._tracker.average_w
        return None if average is None else round(average)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        attributes: dict[str, object] = {
            "samples_in_window": self._tracker.sample_count,
            "window_s": PV_AVERAGE_WINDOW_S,
        }
        if (sample := self._tracker.last_sample) is not None:
            attributes.update(
                last_feed_in_w=round(sample.feed_in_w),
                last_grid_import_w=round(sample.grid_import_w),
                last_battery_discharge_w=round(sample.battery_discharge_w),
                last_wallbox_power_w=round(sample.wallbox_power_w),
                last_surplus_w=round(sample.surplus_w),
            )
        return attributes


class AmtronChargeControlSensor(AmtronEntity, SensorEntity):
    """What the charge controller is doing and why (see charge_controller.py)."""

    _attr_translation_key = "charge_control"
    _unique_id_suffix = "charge_control"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CONTROL_STATES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:state-machine"

    def __init__(self, coordinator: AmtronCoordinator, entry: ConfigEntry, controller: ChargeController) -> None:
        super().__init__(coordinator, entry)
        self._controller = controller

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._controller.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        return self._controller.state

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "target_current_a": self._controller.target_current_a,
            "released_by_controller": self._controller.released,
        }

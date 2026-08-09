"""Config flow for the Mennekes AMTRON Charge Control (ECU) integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    ABS_MAX_CURRENT_A,
    CONF_MAX_CURRENT_A,
    CONF_PAUSE_CURRENT_A,
    CONF_UNIT_ID,
    DEFAULT_MAX_CURRENT_A,
    DEFAULT_PAUSE_CURRENT_A,
    DEFAULT_PORT,
    DEFAULT_UNIT_ID,
    DOMAIN,
    STATUS_BLOCK_COUNT,
    STATUS_BLOCK_START,
)
from .modbus_client import AmtronModbusClient, ModbusConnectionError, ModbusError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_UNIT_ID, default=DEFAULT_UNIT_ID): int,
    }
)


async def _validate_input(data: dict[str, Any]) -> None:
    """Try a real Modbus read against the device to validate host/port/unit id."""
    client = AmtronModbusClient(data[CONF_HOST], data[CONF_PORT], data[CONF_UNIT_ID])
    try:
        # Function code 0x03 (Holding Registers) - the Charge Control ECU
        # rejects 0x04 (Input Registers) for these registers even though the
        # spec labels them "READ". See coordinator.py for details.
        await client.read_holding_registers(STATUS_BLOCK_START, STATUS_BLOCK_COUNT)
    except ModbusConnectionError as err:
        raise CannotConnect(str(err)) from err
    except ModbusError as err:
        raise InvalidResponse(str(err)) from err


class AmtronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mennekes AMTRON Charge Control (ECU)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial (and only) setup step."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()

            try:
                await _validate_input(user_input)
            except CannotConnect as err:
                errors["base"] = "cannot_connect"
                description_placeholders["error_detail"] = str(err)
            except InvalidResponse as err:
                errors["base"] = "invalid_response"
                description_placeholders["error_detail"] = str(err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception during AMTRON setup")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"AMTRON ({user_input[CONF_HOST]})", data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders=description_placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AmtronOptionsFlow:
        """Get the options flow for this handler."""
        return AmtronOptionsFlow()


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidResponse(HomeAssistantError):
    """Error to indicate the device responded with a Modbus exception."""


class AmtronOptionsFlow(OptionsFlow):
    """Options for Mennekes AMTRON Charge Control (ECU).

    Note: this class deliberately does NOT define __init__ / store
    config_entry itself - HA's flow manager sets self.config_entry
    automatically. Doing it manually is deprecated (removed in HA 2025.12)
    and raises a runtime warning/error.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options: max. charging current, and the pause current."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_options = self.config_entry.options
        options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_MAX_CURRENT_A,
                    default=current_options.get(CONF_MAX_CURRENT_A, DEFAULT_MAX_CURRENT_A),
                ): vol.All(vol.Coerce(int), vol.Range(min=6, max=ABS_MAX_CURRENT_A)),
                vol.Required(
                    CONF_PAUSE_CURRENT_A,
                    default=current_options.get(CONF_PAUSE_CURRENT_A, DEFAULT_PAUSE_CURRENT_A),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=ABS_MAX_CURRENT_A)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=options_schema)

"""Data update coordinator for the Mennekes AMTRON Charge Control (ECU) integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
import struct

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CHARGE_BLOCK_COUNT,
    CHARGE_BLOCK_START,
    CP_STATUS,
    DEFAULT_ID_TAG,
    DEFAULT_MAX_CURRENT_A,
    DEFAULT_PAUSE_CURRENT_A,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_START_CURRENT_A,
    DOMAIN,
    ERROR_FLAGS,
    FIRMWARE_BLOCK_COUNT,
    FIRMWARE_BLOCK_START,
    METER_BLOCK_COUNT,
    METER_BLOCK_START,
    METER_NOT_PRESENT,
    REG_CHARGED_ENERGY,
    REG_ERROR_CODES_1,
    REG_ERROR_CODES_2,
    REG_ERROR_CODES_3,
    REG_ERROR_CODES_4,
    REG_HEMS_CURRENT_LIMIT,
    REG_METER_ENERGY_L1,
    REG_METER_POWER_L1,
    REG_METER_POWER_L2,
    REG_METER_POWER_L3,
    REG_OCPP_STATUS,
    REG_SIGNALED_CURRENT,
    REG_WRITE_IDTAG_START,
    STATUS_BLOCK_COUNT,
    STATUS_BLOCK_START,
)
from .modbus_client import AmtronModbusClient, ModbusConnectionError, ModbusError

_LOGGER = logging.getLogger(__name__)


def _to_uint32(reg_at_lower_address: int, reg_at_higher_address: int) -> int:
    """Combine two 16-bit registers into a 32-bit value.

    Note: for the ECU controller the HIGH word comes at the LOWER register
    address (e.g. register 206 holds the high word of Power L1, 207 the low
    word) - the opposite convention from Mennekes' HCC3-based controllers.
    """
    return (reg_at_lower_address << 16) | reg_at_higher_address


def _decode_ascii32(reg_at_lower_address: int, reg_at_higher_address: int) -> str:
    """Decode a 32-bit register pair as 4 ASCII characters.

    Unlike numeric 32-bit registers, FIRMWARE_VERSION packs 4 raw ASCII
    bytes into the pair (high word/high byte first, the normal convention -
    NOT the special ERROR_CODES_ swap). Example from the spec: version
    "0.91" is stored as bytes {0x30, 0x2E, 0x39, 0x31}.
    """
    raw = struct.pack(">HH", reg_at_lower_address, reg_at_higher_address)
    return raw.decode("ascii", errors="replace").rstrip("\x00").strip()


def _byte_swap16(value: int) -> int:
    """Swap the two bytes of a 16-bit value."""
    return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)


def _decode_error_pair(reg_at_lower_address: int, reg_at_higher_address: int) -> int:
    """Decode one ECU ERROR_CODES_ register pair into its 32-bit value.

    Per the spec: for error-code registers only (unlike every other 32-bit
    register), the two words must be swapped AND the bytes within each word
    must be swapped, before being combined. This function implements exactly
    the worked example given in the manufacturer's document, and applies
    equally to all four ERROR_CODES_ pairs (only pair 4 has documented/named
    bits at the time of writing - see ERROR_FLAGS in const.py).
    """
    return (_byte_swap16(reg_at_higher_address) << 16) | _byte_swap16(reg_at_lower_address)


def _encode_id_tag(tag: str) -> list[int]:
    """Encode a tag as 10 big-endian 16-bit registers (5 x 32-bit fields).

    Per spec: max 20 ASCII bytes, padded with blank spaces on the left (i.e.
    the text is right-aligned within the 20-byte field).
    """
    raw = tag.encode("ascii")
    if len(raw) > 20:
        raise ValueError("ID tag must be at most 20 ASCII characters")
    padded = raw.rjust(20, b" ")
    return list(struct.unpack(">10H", padded))


def _decode_power_w(regs: list[int], block_start: int) -> int:
    """Sum Power L1+L2+L3, treating an absent phase (0xFFFFFFFF) as 0 W.

    This single formula correctly handles every documented meter model:
    3-phase meters report each phase separately and the sum is correct;
    meters that only expose a "Total Power" report it on the L1 pair with
    L2/L3 reading 0xFFFFFFFF (excluded), so the sum reduces to that total.
    """
    total = 0
    for reg_addr in (REG_METER_POWER_L1, REG_METER_POWER_L2, REG_METER_POWER_L3):
        offset = reg_addr - block_start
        value = _to_uint32(regs[offset], regs[offset + 1])
        if value != METER_NOT_PRESENT:
            total += value
    return total


@dataclass
class AmtronData:
    """A single decoded snapshot of the wallbox's state."""

    status: str
    active_errors: list[str] = field(default_factory=list)
    error_bits: int = 0
    reserved_error_words: tuple[int, int, int] = (0, 0, 0)
    power_w: int = 0
    session_energy_wh: int = 0
    total_energy_wh: int | None = 0
    signaled_current_a: int = 0
    current_limit_a: int = 0
    firmware_version: str | None = None


class AmtronCoordinator(DataUpdateCoordinator[AmtronData]):
    """Polls the AMTRON ECU controller over Modbus TCP and serves writes."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: AmtronModbusClient,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        max_current_a: int = DEFAULT_MAX_CURRENT_A,
        pause_current_a: int = DEFAULT_PAUSE_CURRENT_A,
        start_current_a: int = DEFAULT_START_CURRENT_A,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.entry = entry
        self.client = client
        self.max_current_a = max_current_a
        self.pause_current_a = pause_current_a
        self.start_current_a = start_current_a
        self._firmware_version: str | None = None  # cached; static for the device's lifetime

    async def _async_update_data(self) -> AmtronData:
        try:
            status_regs = await self.client.read_holding_registers(STATUS_BLOCK_START, STATUS_BLOCK_COUNT)
            meter_regs = await self.client.read_holding_registers(METER_BLOCK_START, METER_BLOCK_COUNT)
            charge_regs = await self.client.read_holding_registers(CHARGE_BLOCK_START, CHARGE_BLOCK_COUNT)
            limit_regs = await self.client.read_holding_registers(REG_HEMS_CURRENT_LIMIT, 1)
            if self._firmware_version is None:
                # Read once and cache: this never changes without a reboot,
                # which would interrupt polling anyway on the next cycle.
                fw_regs = await self.client.read_holding_registers(FIRMWARE_BLOCK_START, FIRMWARE_BLOCK_COUNT)
                self._firmware_version = _decode_ascii32(fw_regs[0], fw_regs[1])
        except (ModbusConnectionError, ModbusError) as err:
            raise UpdateFailed(str(err)) from err

        def status_reg(address: int) -> int:
            return status_regs[address - STATUS_BLOCK_START]

        def error_pair(pair: tuple[int, int]) -> int:
            return _decode_error_pair(status_reg(pair[0]), status_reg(pair[1]))

        error_bits = error_pair(REG_ERROR_CODES_4)
        reserved_words = tuple(
            error_pair(pair) for pair in (REG_ERROR_CODES_1, REG_ERROR_CODES_2, REG_ERROR_CODES_3)
        )
        active_errors = [name for bit, name in ERROR_FLAGS.items() if error_bits & (1 << bit)]

        energy_offset = REG_METER_ENERGY_L1 - METER_BLOCK_START
        total_energy = _to_uint32(meter_regs[energy_offset], meter_regs[energy_offset + 1])

        return AmtronData(
            status=CP_STATUS.get(status_reg(REG_OCPP_STATUS), "unknown"),
            active_errors=active_errors,
            error_bits=error_bits,
            reserved_error_words=reserved_words,
            power_w=_decode_power_w(meter_regs, METER_BLOCK_START),
            session_energy_wh=charge_regs[REG_CHARGED_ENERGY - CHARGE_BLOCK_START],
            total_energy_wh=None if total_energy == METER_NOT_PRESENT else total_energy,
            signaled_current_a=charge_regs[REG_SIGNALED_CURRENT - CHARGE_BLOCK_START],
            current_limit_a=limit_regs[0],
            firmware_version=self._firmware_version,
        )

    async def async_set_current_limit(self, amps: int) -> None:
        """Write a new HEMS current limit (Amps)."""
        try:
            await self.client.write_register(REG_HEMS_CURRENT_LIMIT, amps)
        except (ModbusConnectionError, ModbusError) as err:
            raise HomeAssistantError(f"Could not set charging current: {err}") from err
        await self.async_request_refresh()

    async def async_pause_charging(self) -> None:
        """Pause charging by writing the configured pause current (0 A by default)."""
        await self.async_set_current_limit(self.pause_current_a)

    async def async_start_charging(self, id_tag: str = DEFAULT_ID_TAG) -> None:
        """Set the configured start current, then write a synthetic OCPP IdTag.

        The current limit must be written first: it's what the ECU offers to
        the EV, while the IdTag is only the authorization (equivalent to
        presenting an RFID card). Writing the IdTag first would authorize a
        session at whatever current limit happened to be set previously.

        Requires "Modbus Slave Allow Start/Stop Transaction" and "kostenloses
        Laden" (free charging) to be enabled on the wallbox, otherwise the tag
        is rejected as unauthorized. See README.md.
        """
        values = _encode_id_tag(id_tag)
        try:
            await self.client.write_register(REG_HEMS_CURRENT_LIMIT, self.start_current_a)
            for offset, value in enumerate(values):
                await self.client.write_register(REG_WRITE_IDTAG_START + offset, value)
        except (ModbusConnectionError, ModbusError) as err:
            raise HomeAssistantError(f"Could not start charging: {err}") from err
        await self.async_request_refresh()

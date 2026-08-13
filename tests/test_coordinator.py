"""Tests for coordinator.py: the ECU's register decoding quirks (high-word-
first 32-bit values, the ERROR_CODES_ word/byte swap, ASCII-packed firmware
version), and the AmtronCoordinator end-to-end against a fake server.
"""
from __future__ import annotations

import asyncio
import struct

import mennekes_amtron.const as c
from fake_modbus_server import fake_modbus_server
from mennekes_amtron.coordinator import (
    AmtronCoordinator,
    _decode_ascii32,
    _decode_error_pair,
    _decode_power_w,
    _encode_id_tag,
    _to_uint32,
)
from mennekes_amtron.modbus_client import AmtronModbusClient

UNIT_ID = 255


def run(coro):
    return asyncio.run(coro)


class _StubHass:
    pass


class _StubEntry:
    entry_id = "test_entry"


def _default_registers(**overrides: int) -> dict[int, int]:
    """A full, self-consistent register table for a Charge Control that is
    currently charging, with no errors, on a meter that only reports a
    single "Total Power" (the NZR S85 case from the compatibility table)."""
    power_w = 7360
    total_energy_wh = 1_234_567
    regs = {
        # Firmware "4.44" -> ASCII bytes {0x34, 0x2E, 0x34, 0x34}
        100: 0x342E,
        101: 0x3434,
        104: 6,  # OCPP_CP_STATUS = charging
        105: 0, 106: 0,  # ERROR_CODES_1 (reserved, normally 0)
        107: 0, 108: 0,  # ERROR_CODES_2 (reserved, normally 0)
        109: 0, 110: 0,  # ERROR_CODES_3 (reserved, normally 0)
        111: 0, 112: 0,  # ERROR_CODES_4 (no error)
        200: total_energy_wh >> 16, 201: total_energy_wh & 0xFFFF,  # Energy L1 = total
        202: 0xFFFF, 203: 0xFFFF, 204: 0xFFFF, 205: 0xFFFF,  # Energy L2/L3: no meter
        206: power_w >> 16, 207: power_w & 0xFFFF,  # Power L1 = total (single-meter case)
        208: 0xFFFF, 209: 0xFFFF, 210: 0xFFFF, 211: 0xFFFF,  # Power L2/L3: no meter
        212: 0, 213: 0, 214: 0, 215: 0, 216: 0, 217: 0,  # currents, unused
        705: 4321,  # session energy Wh
        706: 16,  # signaled current A
        1000: 16,  # HEMS_CURRENT_LIMIT
    }
    regs.update(overrides)
    return regs


# --- Pure decode helpers, verified against the manufacturer's own worked examples ---


def test_to_uint32_matches_spec_worked_example():
    # "if registers 200-201 are read and contain 0x0001 and 0x1F40 [...]
    # these values are to be read as 0x00011F40" (high word first)
    assert _to_uint32(0x0001, 0x1F40) == 0x00011F40


def test_decode_ascii32_matches_spec_worked_example():
    # "0.91 = {0x30, 0x2E, 0x39, 0x31}"
    assert _decode_ascii32(0x302E, 0x3931) == "0.91"


def test_decode_error_pair_matches_spec_worked_example():
    # Registers 111-112 = 0x4100, 0x0000 -> after word+byte swap -> 0x00000041
    # -> bit 0 (ERR_RCMB_TRIGGERED) and bit 6 (ERR_CONTACTOR_WELD) set.
    result = _decode_error_pair(0x4100, 0x0000)
    assert result == 0x00000041
    assert result & 0x01  # ERR_RCMB_TRIGGERED
    assert result & 0x40  # ERR_CONTACTOR_WELD


def test_decode_power_w_ignores_absent_phases():
    # Meter reports Total Power on L1 only (L2/L3 = 0xFFFFFFFF, "no meter"),
    # per the NZR S85 row of the manufacturer's compatibility table.
    regs = _default_registers()
    meter_regs = [regs[a] for a in range(c.METER_BLOCK_START, c.METER_BLOCK_START + c.METER_BLOCK_COUNT)]
    assert _decode_power_w(meter_regs, c.METER_BLOCK_START) == 7360


def test_encode_id_tag_is_right_aligned_and_space_padded():
    values = _encode_id_tag("HOMEASSISTANT")
    assert len(values) == 10
    raw = struct.pack(">10H", *values)
    assert raw == b"HOMEASSISTANT".rjust(20)


# --- AmtronCoordinator end-to-end, against a fake server ---


def test_coordinator_full_poll_cycle():
    async def body():
        registers = _default_registers()
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            coordinator = AmtronCoordinator(_StubHass(), _StubEntry(), client, max_current_a=16, pause_current_a=6)
            data = await coordinator._async_update_data()

            assert data.status == "charging"
            assert data.power_w == 7360
            assert data.session_energy_wh == 4321
            assert data.total_energy_wh == 1_234_567
            assert data.signaled_current_a == 16
            assert data.current_limit_a == 16
            assert data.firmware_version == "4.44"
            assert data.active_errors == []
            assert coordinator.max_current_a == 16
            assert coordinator.pause_current_a == 6

    run(body())


def test_coordinator_decodes_active_errors():
    async def body():
        registers = _default_registers()
        registers[111], registers[112] = 0x4100, 0x0000  # spec's own worked example
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            coordinator = AmtronCoordinator(_StubHass(), _StubEntry(), client)
            data = await coordinator._async_update_data()
            assert data.active_errors == ["err_rcmb_triggered", "err_contactor_weld"]

    run(body())


def test_coordinator_exposes_reserved_error_words_as_raw_diagnostics():
    async def body():
        registers = _default_registers()
        registers[105], registers[106] = 0x0800, 0x0000  # arbitrary bit in a "reserved" pair
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            coordinator = AmtronCoordinator(_StubHass(), _StubEntry(), client)
            data = await coordinator._async_update_data()
            assert data.reserved_error_words[0] & 0x08
            assert data.reserved_error_words[1] == 0
            assert data.reserved_error_words[2] == 0

    run(body())


def test_firmware_version_is_read_once_and_cached():
    async def body():
        registers = _default_registers()
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            coordinator = AmtronCoordinator(_StubHass(), _StubEntry(), client)
            first = await coordinator._async_update_data()
            assert first.firmware_version == "4.44"

            # Corrupt the source registers - if the coordinator re-read them,
            # the decoded version would change. It must not.
            registers[100] = 0x0000
            registers[101] = 0x0000
            second = await coordinator._async_update_data()
            assert second.firmware_version == "4.44"

    run(body())


def test_pause_writes_the_configured_value_not_a_hardcoded_zero():
    async def body():
        registers = _default_registers()
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            coordinator = AmtronCoordinator(_StubHass(), _StubEntry(), client, pause_current_a=6)
            await coordinator.async_pause_charging()
            assert registers[c.REG_HEMS_CURRENT_LIMIT] == 6

    run(body())


def test_start_charging_writes_all_ten_idtag_registers():
    async def body():
        registers = _default_registers()
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            coordinator = AmtronCoordinator(_StubHass(), _StubEntry(), client)
            await coordinator.async_start_charging("HOMEASSISTANT")

            written = [registers[c.REG_WRITE_IDTAG_START + i] for i in range(10)]
            raw = struct.pack(">10H", *written)
            assert raw == b"HOMEASSISTANT".rjust(20)

    run(body())


def test_start_charging_sets_the_configured_start_current_before_authorizing():
    async def body():
        registers = _default_registers()
        registers[c.REG_HEMS_CURRENT_LIMIT] = 0  # e.g. left paused
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            coordinator = AmtronCoordinator(_StubHass(), _StubEntry(), client, start_current_a=10)
            await coordinator.async_start_charging("HOMEASSISTANT")
            assert registers[c.REG_HEMS_CURRENT_LIMIT] == 10

    run(body())


def test_start_charging_defaults_the_start_current_to_default_start_current_a():
    async def body():
        registers = _default_registers()
        registers[c.REG_HEMS_CURRENT_LIMIT] = 0
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            coordinator = AmtronCoordinator(_StubHass(), _StubEntry(), client)
            await coordinator.async_start_charging("HOMEASSISTANT")
            assert registers[c.REG_HEMS_CURRENT_LIMIT] == c.DEFAULT_START_CURRENT_A

    run(body())

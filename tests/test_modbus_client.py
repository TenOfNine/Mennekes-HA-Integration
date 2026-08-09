"""Tests for modbus_client.AmtronModbusClient: MBAP framing, register
decoding, and Modbus exception handling - independent of any AMTRON-specific
register semantics (those are tested in test_coordinator.py).
"""
from __future__ import annotations

import asyncio

import pytest

from fake_modbus_server import fake_modbus_server
from mennekes_amtron.modbus_client import AmtronModbusClient, ModbusError

UNIT_ID = 255


def run(coro):
    """Run an async test body. No pytest-asyncio dependency needed."""
    return asyncio.run(coro)


def test_read_holding_registers_round_trip():
    async def body():
        registers = {100: 42, 101: 4660}  # 0x1234
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            values = await client.read_holding_registers(100, 2)
            assert values == [42, 4660]

    run(body())


def test_write_register_then_read_back():
    async def body():
        registers = {1000: 16}
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            await client.write_register(1000, 6)
            values = await client.read_holding_registers(1000, 1)
            assert values == [6]

    run(body())


def test_function_code_4_is_rejected_like_real_hardware():
    """Regression test: the Charge Control rejects Read Input Registers
    (0x04) for every address, which is exactly what broke the integration
    before switching every read to function code 0x03. See README.md."""

    async def body():
        registers = {100: 1}
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            with pytest.raises(ModbusError, match="Illegal function"):
                await client.read_input_registers(100, 1)

    run(body())


def test_illegal_data_address_raises_modbus_error():
    async def body():
        registers = {100: 1}
        async with fake_modbus_server(registers) as (host, port):
            client = AmtronModbusClient(host, port, UNIT_ID, timeout=2.0)
            with pytest.raises(ModbusError, match="Illegal data address"):
                await client.read_holding_registers(9999, 1)

    run(body())

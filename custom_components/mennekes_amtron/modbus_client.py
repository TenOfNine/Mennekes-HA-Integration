"""Minimal, dependency-free async Modbus TCP client.

This implements just enough of the Modbus TCP wire protocol (MBAP header +
function codes 0x03 / 0x04 / 0x06) to talk to a Mennekes AMTRON charge
controller (ECU or HCC3 family - this client is protocol-generic; the
register maps and word-order quirks live in coordinator.py/const.py). It
intentionally does NOT depend on pymodbus:

* Mennekes' Modbus TCP servers document a single simultaneous connection
  and no keepalive support, so opening a short-lived connection per request
  is actually the more robust approach here, not a workaround.
* Home Assistant's built-in `modbus` integration pins its own pymodbus
  version. A custom_component that pins a *different* pymodbus version is a
  well-known source of "no solution found when resolving dependencies"
  failures for users who also use core Modbus elsewhere. Not declaring the
  dependency at all avoids that failure mode entirely.

Only the operations this integration needs are implemented: reading input
registers, reading holding registers, and writing a single holding register.
"""
from __future__ import annotations

import asyncio
import contextlib
import struct

READ_HOLDING_REGISTERS = 0x03
READ_INPUT_REGISTERS = 0x04
WRITE_SINGLE_REGISTER = 0x06

EXCEPTION_CODES = {
    1: "Illegal function",
    2: "Illegal data address",
    3: "Illegal data value",
    4: "Slave device failure",
    5: "Acknowledge",
    6: "Slave device busy",
    8: "Memory parity error",
    10: "Gateway path unavailable",
    11: "Gateway target device failed to respond",
}


class ModbusError(Exception):
    """Raised when the AMTRON returns a valid Modbus exception response."""


class ModbusConnectionError(Exception):
    """Raised when the TCP connection to the AMTRON fails or times out."""


class AmtronModbusClient:
    """Tiny Modbus TCP client scoped to a single unit ID.

    A fresh TCP connection is opened for every request and closed again
    immediately afterwards, matching the AMTRON's documented limit of one
    simultaneous connection. An asyncio.Lock serializes all requests so a
    poll (coordinator) and a write (number/button entity) can never race
    each other and open two connections at once.
    """

    def __init__(self, host: str, port: int, unit_id: int, timeout: float = 5.0) -> None:
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._transaction_id = 0

    def _next_transaction_id(self) -> int:
        self._transaction_id = (self._transaction_id + 1) % 0xFFFF
        return self._transaction_id

    async def _request(self, function_code: int, payload: bytes) -> bytes:
        """Send one PDU and return the response's data (function code stripped)."""
        async with self._lock:
            try:
                return await asyncio.wait_for(
                    self._do_request(function_code, payload), timeout=self._timeout
                )
            except asyncio.TimeoutError as err:
                raise ModbusConnectionError(
                    f"Timed out talking to {self._host}:{self._port}"
                ) from err

    async def _do_request(self, function_code: int, payload: bytes) -> bytes:
        try:
            reader, writer = await asyncio.open_connection(self._host, self._port)
        except OSError as err:
            raise ModbusConnectionError(
                f"Could not connect to {self._host}:{self._port}: {err}"
            ) from err

        try:
            transaction_id = self._next_transaction_id()
            pdu = bytes([function_code]) + payload
            # MBAP header: transaction id, protocol id (always 0), length
            # (unit id + PDU bytes that follow), unit id.
            mbap = struct.pack(">HHHB", transaction_id, 0x0000, len(pdu) + 1, self._unit_id)
            writer.write(mbap + pdu)
            await writer.drain()

            header = await reader.readexactly(7)
            _, _, length, _ = struct.unpack(">HHHB", header)
            remaining = await reader.readexactly(length - 1)
        except (OSError, asyncio.IncompleteReadError) as err:
            raise ModbusConnectionError(
                f"Communication with {self._host}:{self._port} failed: {err}"
            ) from err
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

        resp_function_code = remaining[0]
        if resp_function_code & 0x80:
            exception_code = remaining[1] if len(remaining) > 1 else 0
            raise ModbusError(
                EXCEPTION_CODES.get(exception_code, f"Unknown exception 0x{exception_code:02X}")
            )
        if resp_function_code != function_code:
            raise ModbusError(f"Unexpected function code 0x{resp_function_code:02X} in response")
        return remaining[1:]

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        """Read `count` input registers (function code 0x04) starting at `address`."""
        payload = struct.pack(">HH", address, count)
        data = await self._request(READ_INPUT_REGISTERS, payload)
        byte_count = data[0]
        return list(struct.unpack(f">{byte_count // 2}H", data[1 : 1 + byte_count]))

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        """Read `count` holding registers (function code 0x03) starting at `address`."""
        payload = struct.pack(">HH", address, count)
        data = await self._request(READ_HOLDING_REGISTERS, payload)
        byte_count = data[0]
        return list(struct.unpack(f">{byte_count // 2}H", data[1 : 1 + byte_count]))

    async def write_register(self, address: int, value: int) -> None:
        """Write a single holding register (function code 0x06)."""
        payload = struct.pack(">HH", address, value)
        await self._request(WRITE_SINGLE_REGISTER, payload)

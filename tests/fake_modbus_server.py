"""A minimal fake Modbus TCP server, shared by the test modules.

Mirrors the REAL Mennekes ECU Charge Control's behaviour as confirmed
against actual hardware: every register lives in one address space that
only answers to function code 0x03 (Read Holding Registers) - function code
0x04 (Read Input Registers) is rejected with "Illegal Function", exactly
like the real device. A single request/response pair is handled per TCP
connection, matching how `modbus_client.AmtronModbusClient` actually talks
to the wallbox (fresh connection per request, per the device's documented
single-connection limit).
"""
from __future__ import annotations

import asyncio
import struct
from contextlib import asynccontextmanager


@asynccontextmanager
async def fake_modbus_server(registers: dict[int, int]):
    """Start a fake server backed by the given (mutable) register table.

    Yields (host, port). Writes (function code 0x06) mutate `registers` in
    place, so a test can write-then-read to verify a round trip.
    """

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            header = await reader.readexactly(7)
            transaction_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
            pdu = await reader.readexactly(length - 1)
            function_code = pdu[0]

            if function_code == 0x03:
                address, count = struct.unpack(">HH", pdu[1:5])
                try:
                    values = [registers[address + i] for i in range(count)]
                except KeyError:
                    resp_pdu = bytes([function_code | 0x80, 0x02])  # Illegal Data Address
                else:
                    data = bytes([len(values) * 2]) + struct.pack(f">{len(values)}H", *values)
                    resp_pdu = bytes([function_code]) + data
            elif function_code == 0x06:
                address, value = struct.unpack(">HH", pdu[1:5])
                registers[address] = value
                resp_pdu = pdu  # echo back, per spec
            else:
                # Real Charge Control behaviour: function code 4 (and
                # anything else this fake doesn't implement) is rejected.
                resp_pdu = bytes([function_code | 0x80, 0x01])  # Illegal Function

            mbap = struct.pack(">HHHB", transaction_id, protocol_id, len(resp_pdu) + 1, unit_id)
            writer.write(mbap + resp_pdu)
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with server:
            yield "127.0.0.1", port
    finally:
        pass

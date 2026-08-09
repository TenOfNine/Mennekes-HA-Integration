"""Tests for config_flow.py: the setup flow's live-device validation (and
that it surfaces the real Modbus error text), and the options flow that
makes max/pause current user-configurable.
"""
from __future__ import annotations

import asyncio

import mennekes_amtron.const as c
from fake_modbus_server import fake_modbus_server
from homeassistant.const import CONF_HOST, CONF_PORT
from mennekes_amtron.config_flow import AmtronConfigFlow, AmtronOptionsFlow

UNIT_ID = 255


def run(coro):
    return asyncio.run(coro)


def _status_block_registers() -> dict[int, int]:
    return {addr: 0 for addr in range(c.STATUS_BLOCK_START, c.STATUS_BLOCK_START + c.STATUS_BLOCK_COUNT)}


def test_setup_succeeds_against_a_working_device():
    async def body():
        registers = _status_block_registers()
        async with fake_modbus_server(registers) as (host, port):
            flow = AmtronConfigFlow()
            result = await flow.async_step_user({CONF_HOST: host, CONF_PORT: port, c.CONF_UNIT_ID: UNIT_ID})
            assert result["type"] == "create_entry"
            assert result["title"] == f"AMTRON ({host})"

    run(body())


def test_setup_surfaces_the_real_modbus_error_text():
    """Regression test: the dialog used to show a generic message; it must
    now show the actual exception text (e.g. "Illegal function"), which is
    what let the function-code-3-vs-4 bug get diagnosed in the first place.
    """

    async def body():
        # No registers configured at all -> every read is "Illegal Data Address".
        async with fake_modbus_server({}) as (host, port):
            flow = AmtronConfigFlow()
            result = await flow.async_step_user({CONF_HOST: host, CONF_PORT: port, c.CONF_UNIT_ID: UNIT_ID})
            assert result["type"] == "form"
            assert result["errors"]["base"] == "invalid_response"
            assert result["description_placeholders"]["error_detail"] == "Illegal data address"

    run(body())


def test_setup_reports_cannot_connect_when_nothing_is_listening():
    async def body():
        flow = AmtronConfigFlow()
        # Port 1 is a well-known privileged port nothing will be listening on.
        result = await flow.async_step_user({CONF_HOST: "127.0.0.1", CONF_PORT: 1, c.CONF_UNIT_ID: UNIT_ID})
        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    run(body())


class _StubEntryWithOptions:
    def __init__(self, options: dict | None = None) -> None:
        self.options = options or {}


def test_options_form_defaults_match_the_documented_defaults():
    async def body():
        flow = AmtronOptionsFlow()
        flow.config_entry = _StubEntryWithOptions()  # HA sets this automatically at runtime
        form = await flow.async_step_init()
        defaults = {str(key): key.default() for key in form["data_schema"].schema if hasattr(key, "default")}
        assert defaults[c.CONF_MAX_CURRENT_A] == c.DEFAULT_MAX_CURRENT_A  # 16 A
        assert defaults[c.CONF_PAUSE_CURRENT_A] == c.DEFAULT_PAUSE_CURRENT_A  # 0 A

    run(body())


def test_options_form_prefills_from_existing_options():
    async def body():
        flow = AmtronOptionsFlow()
        flow.config_entry = _StubEntryWithOptions({c.CONF_MAX_CURRENT_A: 20, c.CONF_PAUSE_CURRENT_A: 6})
        form = await flow.async_step_init()
        defaults = {str(key): key.default() for key in form["data_schema"].schema if hasattr(key, "default")}
        assert defaults[c.CONF_MAX_CURRENT_A] == 20
        assert defaults[c.CONF_PAUSE_CURRENT_A] == 6

    run(body())


def test_saving_options_creates_an_entry_with_the_submitted_data():
    async def body():
        flow = AmtronOptionsFlow()
        flow.config_entry = _StubEntryWithOptions()
        result = await flow.async_step_init({c.CONF_MAX_CURRENT_A: 16, c.CONF_PAUSE_CURRENT_A: 6})
        assert result["type"] == "create_entry"
        assert result["data"] == {c.CONF_MAX_CURRENT_A: 16, c.CONF_PAUSE_CURRENT_A: 6}

    run(body())

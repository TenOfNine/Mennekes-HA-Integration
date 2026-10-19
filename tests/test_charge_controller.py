"""Tests for charge_controller.py, against a fake wallbox that records every write."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from mennekes_amtron import const as c
from mennekes_amtron.charge_controller import (
    STATE_BUFFERING,
    STATE_CHARGING,
    STATE_ERROR,
    STATE_IMMEDIATE,
    STATE_MANUAL,
    STATE_MIN_INTERVAL,
    STATE_NO_DATA,
    STATE_NO_SOURCES,
    STATE_NOT_CONNECTED,
    STATE_OFF,
    STATE_PHASE_SWITCHING,
    STATE_WAITING_FOR_SOC,
    STATE_WAITING_FOR_SURPLUS,
    ChargeController,
)


def run(coro):
    return asyncio.run(coro)


class FakeWallbox:
    """Stands in for AmtronCoordinator: records writes, mirrors them into data."""

    def __init__(self, status: str = "suspended_evse", current_limit_a: int = 0, phase_mode: str = "disabled"):
        self.last_update_success = True
        self.data = SimpleNamespace(status=status, current_limit_a=current_limit_a)
        self.max_current_a = 16
        self.phase_mode = phase_mode
        self.writes: list[tuple[str, int]] = []
        self.fail_with: Exception | None = None

    async def async_set_current_limit(self, amps: int) -> None:
        if self.fail_with:
            raise self.fail_with
        self.writes.append(("current", amps))
        self.data.current_limit_a = amps

    async def async_start_charging(self, id_tag: str = "HOMEASSISTANT", current_a: int | None = None) -> None:
        if self.fail_with:
            raise self.fail_with
        amps = 16 if current_a is None else current_a
        self.writes.append(("start", amps))
        self.data.current_limit_a = amps

    async def async_pause_charging(self) -> None:
        await self.async_set_current_limit(0)


class Setup:
    def __init__(self, average_w: float = 0, soc: float | str | None = 80, with_pv: bool = True, **wallbox):
        self.wallbox = FakeWallbox(**wallbox)
        self.tracker = SimpleNamespace(average_w=average_w, sample_count=c.PV_MIN_SAMPLES)
        self._states = {} if soc is None else {"sensor.soc": SimpleNamespace(state=str(soc), attributes={})}
        hass = SimpleNamespace(states=SimpleNamespace(get=self._states.get))
        self.controller = ChargeController(
            hass,
            self.wallbox,
            self.tracker if with_pv else None,
            soc_entity=None if soc is None else "sensor.soc",
        )
        self.controller.restore_mode(c.MODE_PV_SURPLUS)

    def set(self, average_w: float | None = None, soc: float | str | None = None, status: str | None = None):
        if average_w is not None:
            self.tracker.average_w = average_w
        if soc is not None:
            self._states["sensor.soc"] = SimpleNamespace(state=str(soc), attributes={})
        if status is not None:
            self.wallbox.data.status = status

    async def cycle(self, now: float) -> str:
        await self.controller.async_run_cycle(now)
        return self.controller.state


# --- PV surplus: starting -------------------------------------------------


def test_pv_start_needs_both_the_start_soc_and_6_a_of_surplus():
    async def body():
        s = Setup(average_w=3000, soc=55)
        assert await s.cycle(0) == STATE_WAITING_FOR_SOC
        s.set(average_w=1000, soc=60)
        assert await s.cycle(30) == STATE_WAITING_FOR_SURPLUS
        assert s.wallbox.writes == []

        s.set(average_w=3000)
        assert await s.cycle(60) == STATE_CHARGING
        assert s.wallbox.writes == [("start", 13)]  # floor(3000 / 230)
        assert s.controller.released

    run(body())


def test_without_a_soc_entity_only_the_surplus_counts():
    async def body():
        s = Setup(average_w=3000, soc=None)
        assert await s.cycle(0) == STATE_CHARGING
        assert s.wallbox.writes == [("start", 13)]

    run(body())


def test_target_current_follows_the_surplus_and_is_only_written_on_change():
    async def body():
        s = Setup(average_w=3000)
        await s.cycle(0)
        s.set(average_w=3100)  # still 13 A
        await s.cycle(30)
        s.set(average_w=3700)  # 16 A
        await s.cycle(60)
        s.set(average_w=9000)  # capped at the configured max of 16 A, already set
        await s.cycle(90)
        assert s.wallbox.writes == [("start", 13), ("current", 16)]

    run(body())


# --- PV surplus: stopping ---------------------------------------------------


def test_below_6_a_charges_on_at_6_a_for_the_buffer_time_then_stops():
    async def body():
        s = Setup(average_w=3000)
        await s.cycle(0)
        s.set(average_w=800)
        assert await s.cycle(30) == STATE_BUFFERING
        assert s.wallbox.writes[-1] == ("current", 6)
        assert await s.cycle(30 + 9 * 60) == STATE_BUFFERING
        assert await s.cycle(30 + 10 * 60) == STATE_WAITING_FOR_SURPLUS
        assert s.wallbox.writes[-1] == ("current", 0)
        assert not s.controller.released

    run(body())


def test_surplus_recovering_within_the_buffer_time_restarts_the_buffer():
    async def body():
        s = Setup(average_w=3000)
        await s.cycle(0)
        s.set(average_w=800)
        await s.cycle(30)
        s.set(average_w=2000)
        assert await s.cycle(300) == STATE_CHARGING
        s.set(average_w=800)
        await s.cycle(330)
        assert await s.cycle(330 + 9 * 60) == STATE_BUFFERING  # 10 min counted from 330, not 30
        assert ("current", 0) not in s.wallbox.writes

    run(body())


def test_soc_hysteresis_stops_below_the_stop_soc_and_restarts_only_at_the_start_soc():
    async def body():
        s = Setup(average_w=3000, soc=65)
        await s.cycle(0)
        s.set(soc=52)
        assert await s.cycle(30) == STATE_CHARGING  # between stop and start: keeps charging
        s.set(soc=49)
        assert await s.cycle(120) == STATE_WAITING_FOR_SOC
        assert s.wallbox.writes[-1] == ("current", 0)
        s.set(soc=55)
        assert await s.cycle(250) == STATE_WAITING_FOR_SOC  # between stop and start: stays off
        s.set(soc=60)
        assert await s.cycle(400) == STATE_CHARGING
        assert s.wallbox.writes[-1] == ("start", 13)

    run(body())


def test_starts_and_stops_keep_the_minimum_interval():
    async def body():
        s = Setup(average_w=3000)
        await s.cycle(0)
        s.set(soc=40)
        assert await s.cycle(30) == STATE_MIN_INTERVAL
        assert s.wallbox.writes == [("start", 13)]
        assert await s.cycle(90) == STATE_WAITING_FOR_SOC
        assert s.wallbox.writes[-1] == ("current", 0)

    run(body())


def test_a_session_ending_on_its_own_is_released_and_reevaluated():
    async def body():
        s = Setup(average_w=3000)
        await s.cycle(0)
        s.set(status="finishing")
        assert await s.cycle(200) == STATE_CHARGING
        assert s.wallbox.writes == [("start", 13), ("start", 13)]  # re-authorized

    run(body())


# --- Wallbox starting on its own at plug-in ---------------------------------


def test_a_session_the_wallbox_started_itself_is_stopped_if_the_start_condition_fails():
    async def body():
        s = Setup(average_w=3000, soc=40, status="charging", current_limit_a=16)
        assert await s.cycle(0) == STATE_WAITING_FOR_SOC
        assert s.wallbox.writes == [("current", 0)]

    run(body())


def test_a_session_the_wallbox_started_itself_is_taken_over_if_the_start_condition_holds():
    async def body():
        s = Setup(average_w=2000, soc=80, status="charging", current_limit_a=16)
        assert await s.cycle(0) == STATE_CHARGING
        assert s.wallbox.writes == [("start", 8)]
        assert s.controller.released

    run(body())


# --- Holding still -------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        lambda s: setattr(s.tracker, "sample_count", c.PV_MIN_SAMPLES - 1),
        lambda s: s.set(soc="unavailable"),
        lambda s: s.set(status="faulted"),
        lambda s: setattr(s.wallbox, "last_update_success", False),
    ],
    ids=["too_few_samples", "soc_unavailable", "wallbox_faulted", "wallbox_unreachable"],
)
def test_unavailable_data_changes_nothing(change):
    async def body():
        s = Setup(average_w=3000)
        await s.cycle(0)
        change(s)
        s.set(average_w=800)
        assert await s.cycle(30 + 20 * 60) == STATE_NO_DATA
        assert s.wallbox.writes == [("start", 13)]  # the last current stays

    run(body())


def test_pv_mode_without_sources_does_nothing():
    async def body():
        s = Setup(average_w=3000, with_pv=False)
        assert await s.cycle(0) == STATE_NO_SOURCES
        assert s.wallbox.writes == []

    run(body())


def test_no_vehicle_resets_the_release():
    async def body():
        s = Setup(average_w=3000)
        await s.cycle(0)
        s.set(status="available")
        assert await s.cycle(30) == STATE_NOT_CONNECTED
        assert not s.controller.released

    run(body())


def test_phase_switching_blocks_the_controller():
    async def body():
        s = Setup(average_w=3000, phase_mode="auto")
        assert await s.cycle(0) == STATE_PHASE_SWITCHING
        assert s.wallbox.writes == []

    run(body())


def test_a_failed_write_is_reported_and_rate_limited():
    async def body():
        s = Setup(average_w=3000, status="finishing")
        s.wallbox.fail_with = HomeAssistantError("Illegal data value")
        assert await s.cycle(0) == STATE_ERROR
        s.wallbox.fail_with = None
        assert await s.cycle(30) == STATE_MIN_INTERVAL
        assert await s.cycle(90) == STATE_CHARGING

    run(body())


# --- Other modes and manual override ----------------------------------------


def test_manual_override_switches_to_manual_and_the_controller_stops_writing():
    async def body():
        s = Setup(average_w=3000)
        await s.cycle(0)
        await s.controller.async_manual_action(s.wallbox.async_pause_charging)
        assert s.controller.mode == c.MODE_MANUAL
        assert await s.cycle(30) == STATE_MANUAL
        assert s.wallbox.writes == [("start", 13), ("current", 0)]

    run(body())


def test_immediate_charges_at_the_maximum_current():
    async def body():
        s = Setup(status="suspended_evse", current_limit_a=0)
        await s.controller.async_set_mode(c.MODE_IMMEDIATE, now=0)
        assert s.controller.state == STATE_IMMEDIATE
        assert s.wallbox.writes == [("current", 16)]  # session open: the current alone resumes it

        s2 = Setup(status="preparing")
        await s2.controller.async_set_mode(c.MODE_IMMEDIATE, now=0)
        assert s2.wallbox.writes == [("start", 16)]  # no session yet: authorize

    run(body())


def test_off_holds_the_current_at_0_a():
    async def body():
        s = Setup(status="charging", current_limit_a=16)
        await s.controller.async_set_mode(c.MODE_OFF, now=0)
        assert s.controller.state == STATE_OFF
        await s.cycle(30)
        assert s.wallbox.writes == [("current", 0)]

    run(body())


def test_picking_a_mode_acts_at_once_despite_the_minimum_interval():
    async def body():
        s = Setup(average_w=3000, soc=40, status="charging", current_limit_a=16)
        await s.cycle(0)  # the controller stops it: a switch at t=0
        s.set(status="preparing")  # needs a start command, which the interval would block
        await s.controller.async_set_mode(c.MODE_IMMEDIATE, now=10)
        assert s.wallbox.writes == [("current", 0), ("start", 16)]

    run(body())


# --- Unplugging -----------------------------------------------------------------


def test_unplugging_for_10_s_resets_manual_to_pv_surplus():
    async def body():
        s = Setup(status="available")
        s.controller.restore_mode(c.MODE_MANUAL)
        s.controller.handle_wallbox_update(0)
        s.controller.handle_wallbox_update(9)
        assert s.controller.mode == c.MODE_MANUAL
        s.controller.handle_wallbox_update(10)
        assert s.controller.mode == c.MODE_PV_SURPLUS

    run(body())


def test_replugging_within_10_s_keeps_manual():
    async def body():
        s = Setup(status="available")
        s.controller.restore_mode(c.MODE_MANUAL)
        s.controller.handle_wallbox_update(0)
        s.set(status="preparing")
        s.controller.handle_wallbox_update(5)
        s.set(status="available")
        s.controller.handle_wallbox_update(12)
        assert s.controller.mode == c.MODE_MANUAL

    run(body())


@pytest.mark.parametrize("mode", [c.MODE_IMMEDIATE, c.MODE_OFF])
def test_unplugging_keeps_immediate_and_off(mode):
    async def body():
        s = Setup(status="available")
        s.controller.restore_mode(mode)
        s.controller.handle_wallbox_update(0)
        s.controller.handle_wallbox_update(60)
        assert s.controller.mode == mode

    run(body())


def test_unplugging_keeps_manual_without_pv_sources():
    async def body():
        s = Setup(status="available", with_pv=False)
        s.controller.restore_mode(c.MODE_MANUAL)
        s.controller.handle_wallbox_update(0)
        s.controller.handle_wallbox_update(60)
        assert s.controller.mode == c.MODE_MANUAL

    run(body())


# --- Settings --------------------------------------------------------------------


def test_the_stop_soc_must_stay_below_the_start_soc():
    s = Setup()
    with pytest.raises(HomeAssistantError):
        s.controller.update_setting("soc_stop_pct", 60)
    s.controller.update_setting("soc_stop_pct", 40)
    assert s.controller.settings.soc_stop_pct == 40
    # Restoring after a restart isn't validated: the pair restores one value at a time.
    s.controller.restore_setting("soc_stop_pct", 70)
    assert s.controller.settings.soc_stop_pct == 70

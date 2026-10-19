"""Charge modes and the PV surplus controller.

One mode at a time, picked with the "charge mode" select entity:

- pv_surplus: start, stop and set the current from the averaged PV surplus
  (pv_surplus.py), with a battery-SoC hysteresis - see _async_regulate.
- immediate: charge at the configured maximum current, and optionally go
  back to pv_surplus once the car is full - see _async_immediate.
- off: hold the current limit at 0 A.
- manual: never touch the wallbox. The default, and what the Start/Pause
  buttons and a manual current change switch to.

The controller writes through the coordinator directly, never through the
button/number entities, so its own starts and stops don't count as a
manual override.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
import math
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CHARGE_MODES,
    CHARGING_RELEASED_STATUSES,
    CONTROLLABLE_STATUSES,
    DEFAULT_CHARGE_MODE,
    DEFAULT_IMMEDIATE_AUTO_RETURN,
    DEFAULT_PV_BUFFER_TIME_MIN,
    DEFAULT_PV_MIN_SWITCH_INTERVAL_S,
    DEFAULT_PV_SOC_START_PCT,
    DEFAULT_PV_SOC_STOP_PCT,
    IMMEDIATE_DONE_AFTER_S,
    MIN_CURRENT_A,
    MODE_IMMEDIATE,
    MODE_MANUAL,
    MODE_OFF,
    MODE_PV_SURPLUS,
    PHASE_MODE_DISABLED,
    PV_CONTROL_INTERVAL_S,
    PV_MIN_SAMPLES,
    PV_UNPLUG_RESET_S,
    PV_WATTS_PER_AMP,
    SESSION_ACTIVE_STATUSES,
)

if TYPE_CHECKING:
    from .coordinator import AmtronCoordinator, AmtronData
    from .pv_surplus import PvSurplusTracker

_LOGGER = logging.getLogger(__name__)

STATE_MANUAL = "manual"
STATE_OFF = "off"
STATE_IMMEDIATE = "immediate"
STATE_NOT_CONNECTED = "not_connected"
STATE_NO_DATA = "no_data"
STATE_NO_SOURCES = "no_sources"
STATE_PHASE_SWITCHING = "phase_switching_enabled"
STATE_WAITING_FOR_SOC = "waiting_for_soc"
STATE_WAITING_FOR_SURPLUS = "waiting_for_surplus"
STATE_CHARGING = "charging_pv"
STATE_BUFFERING = "buffering"
STATE_MIN_INTERVAL = "min_interval"
STATE_ERROR = "error"
CONTROL_STATES = [
    STATE_MANUAL,
    STATE_OFF,
    STATE_IMMEDIATE,
    STATE_NOT_CONNECTED,
    STATE_NO_DATA,
    STATE_NO_SOURCES,
    STATE_PHASE_SWITCHING,
    STATE_WAITING_FOR_SOC,
    STATE_WAITING_FOR_SURPLUS,
    STATE_CHARGING,
    STATE_BUFFERING,
    STATE_MIN_INTERVAL,
    STATE_ERROR,
]

MIN_SURPLUS_W = MIN_CURRENT_A * PV_WATTS_PER_AMP  # 1380 W


@dataclass(frozen=True)
class PvSettings:
    soc_start_pct: int = DEFAULT_PV_SOC_START_PCT
    soc_stop_pct: int = DEFAULT_PV_SOC_STOP_PCT
    min_switch_interval_s: int = DEFAULT_PV_MIN_SWITCH_INTERVAL_S
    buffer_time_min: int = DEFAULT_PV_BUFFER_TIME_MIN
    immediate_auto_return: bool = DEFAULT_IMMEDIATE_AUTO_RETURN


def _read_number(state: Any) -> float | None:
    if state is None:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


class ChargeController:
    """Applies the selected charge mode to the wallbox every PV_CONTROL_INTERVAL_S."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: AmtronCoordinator,
        tracker: PvSurplusTracker | None = None,
        soc_entity: str | None = None,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._tracker = tracker
        self._soc_entity = soc_entity
        self.mode = DEFAULT_CHARGE_MODE
        self.settings = PvSettings()
        self.state = STATE_MANUAL
        # "Charging was released by the controller": a session it started
        # or took over. Without it, a wallbox that starts on its own at
        # plug-in is stopped unless the start condition holds.
        self.released = False
        self.target_current_a: int | None = None
        self._last_switch: float | None = None
        self._below_min_since: float | None = None
        self._unplugged_since: float | None = None
        self._vehicle_paused_since: float | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    @property
    def has_pv_sources(self) -> bool:
        return self._tracker is not None

    # --- listeners -------------------------------------------------------

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update_callback)

        def remove() -> None:
            self._listeners.remove(update_callback)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # --- mode and settings -------------------------------------------------

    def _enter_mode(self, mode: str) -> None:
        self.mode = mode
        self.released = False
        self._below_min_since = None
        self._vehicle_paused_since = None

    @callback
    def restore_mode(self, mode: str) -> None:
        """Set the mode after a restart without acting on it right away."""
        if mode in CHARGE_MODES:
            self._enter_mode(mode)

    async def async_set_mode(self, mode: str, now: float | None = None) -> None:
        """User picked a mode: apply it now, not after the minimum switch interval."""
        async with self._lock:
            self._enter_mode(mode)
            self._last_switch = None
            await self._async_cycle(time.monotonic() if now is None else now)
        self._notify()

    async def async_manual_action(self, action: Callable[[], Awaitable[None]]) -> None:
        """Switch to manual, then run a user-triggered wallbox write."""
        async with self._lock:
            self._enter_mode(MODE_MANUAL)
            self.state = STATE_MANUAL
            await action()
        self._notify()

    def update_setting(self, key: str, value: int | bool) -> None:
        candidate = replace(self.settings, **{key: value})
        if candidate.soc_stop_pct >= candidate.soc_start_pct:
            raise HomeAssistantError(
                "The stop SoC must be below the start SoC, otherwise there is no hysteresis"
            )
        self.settings = candidate
        self._notify()

    @callback
    def restore_setting(self, key: str, value: int | bool) -> None:
        # Not validated: the settings restore one by one, and a restored
        # start/stop pair is only consistent once both are back.
        self.settings = replace(self.settings, **{key: value})

    # --- timers ------------------------------------------------------------

    async def async_run_cycle(self, now: float) -> None:
        async with self._lock:
            await self._async_cycle(now)
        self._notify()

    @callback
    def handle_wallbox_update(self, now: float) -> None:
        """Track unplugging; after PV_UNPLUG_RESET_S, manual goes back to PV surplus."""
        data = self._coordinator.data
        if not self._coordinator.last_update_success or data is None:
            return
        if data.status != "available":
            self._unplugged_since = None
            return
        if self._unplugged_since is None:
            self._unplugged_since = now
        self.released = False
        self._below_min_since = None
        if (
            self.mode == MODE_MANUAL
            and self.has_pv_sources
            and now - self._unplugged_since >= PV_UNPLUG_RESET_S
        ):
            self._enter_mode(MODE_PV_SURPLUS)
            self._notify()

    @callback
    def async_start(self) -> Callable[[], None]:
        """Start the control loop; returns the function that stops it again."""

        async def _on_interval(_: datetime) -> None:
            await self.async_run_cycle(time.monotonic())

        stop_timer = async_track_time_interval(
            self._hass, _on_interval, timedelta(seconds=PV_CONTROL_INTERVAL_S)
        )
        stop_listening = self._coordinator.async_add_listener(
            lambda: self.handle_wallbox_update(time.monotonic())
        )

        def stop() -> None:
            stop_timer()
            stop_listening()

        return stop

    # --- control logic ---------------------------------------------------

    def _switch_allowed(self, now: float) -> bool:
        return self._last_switch is None or now - self._last_switch >= self.settings.min_switch_interval_s

    async def _async_cycle(self, now: float) -> None:
        try:
            self.state = await self._async_apply_mode(now)
        except HomeAssistantError as err:
            _LOGGER.warning("Charge controller could not write to the wallbox: %s", err)
            self.state = STATE_ERROR

    async def _async_apply_mode(self, now: float) -> str:
        if self.mode == MODE_MANUAL:
            return STATE_MANUAL
        coordinator = self._coordinator
        if coordinator.phase_mode != PHASE_MODE_DISABLED:
            return STATE_PHASE_SWITCHING
        data = coordinator.data
        if not coordinator.last_update_success or data is None or data.status not in CONTROLLABLE_STATUSES:
            return STATE_NO_DATA
        if self.mode == MODE_OFF:
            if data.current_limit_a != 0:
                await coordinator.async_set_current_limit(0)
            return STATE_OFF
        if data.status == "available":
            self.released = False
            self._below_min_since = None
            self._vehicle_paused_since = None
            return STATE_NOT_CONNECTED
        if self.mode == MODE_IMMEDIATE:
            return await self._async_immediate(now, data)
        return await self._async_regulate(now, data)

    async def _async_immediate(self, now: float, data: AmtronData) -> str:
        if self._vehicle_full(now, data):
            _LOGGER.info(
                "The car has not drawn current for %d min in Immediate mode; switching to PV surplus",
                IMMEDIATE_DONE_AFTER_S // 60,
            )
            self._enter_mode(MODE_PV_SURPLUS)
            return await self._async_regulate(now, data)
        target = self._coordinator.max_current_a
        self.target_current_a = target
        if data.status in SESSION_ACTIVE_STATUSES:
            if data.current_limit_a != target:
                await self._coordinator.async_set_current_limit(target)
            return STATE_IMMEDIATE
        if not self._switch_allowed(now):  # preparing/finishing: needs authorizing
            return STATE_MIN_INTERVAL
        self._last_switch = now
        await self._coordinator.async_start_charging(current_a=target)
        return STATE_IMMEDIATE

    def _vehicle_full(self, now: float, data: AmtronData) -> bool:
        """The car paused charging itself (current offered, none drawn) for IMMEDIATE_DONE_AFTER_S."""
        if data.status != "suspended_ev":
            self._vehicle_paused_since = None
            return False
        if self._vehicle_paused_since is None:
            self._vehicle_paused_since = now
        return (
            self.settings.immediate_auto_return
            and self.has_pv_sources
            and now - self._vehicle_paused_since >= IMMEDIATE_DONE_AFTER_S
        )

    async def _async_regulate(self, now: float, data: AmtronData) -> str:
        if self._tracker is None:
            return STATE_NO_SOURCES
        average = self._tracker.average_w if self._tracker.sample_count >= PV_MIN_SAMPLES else None
        soc = None if self._soc_entity is None else _read_number(self._hass.states.get(self._soc_entity))
        if average is None or (self._soc_entity is not None and soc is None):
            return STATE_NO_DATA

        target = min(max(math.floor(average / PV_WATTS_PER_AMP), MIN_CURRENT_A), self._coordinator.max_current_a)
        self.target_current_a = target
        enough_surplus = average >= MIN_SURPLUS_W

        if self.released and data.status == "finishing":  # the session ended without us
            self.released = False
            self._below_min_since = None
        if not self.released:
            return await self._async_regulate_stopped(now, data, target, soc, enough_surplus)
        return await self._async_regulate_released(now, data, target, soc, enough_surplus)

    async def _async_regulate_stopped(
        self, now: float, data: AmtronData, target: int, soc: float | None, enough_surplus: bool
    ) -> str:
        soc_ok = soc is None or soc >= self.settings.soc_start_pct
        if soc_ok and enough_surplus:
            if not self._switch_allowed(now):
                return STATE_MIN_INTERVAL
            self._last_switch = now
            # Also takes over a session the wallbox started on its own:
            # with a session open, this only writes the current.
            await self._coordinator.async_start_charging(current_a=target)
            self.released = True
            return STATE_CHARGING
        if data.status in CHARGING_RELEASED_STATUSES and data.current_limit_a != 0:
            # Started on its own (e.g. at plug-in) while the start condition
            # doesn't hold: the start thresholds apply to every session.
            if not self._switch_allowed(now):
                return STATE_MIN_INTERVAL
            self._last_switch = now
            await self._coordinator.async_set_current_limit(0)
        return STATE_WAITING_FOR_SURPLUS if soc_ok else STATE_WAITING_FOR_SOC

    async def _async_regulate_released(
        self, now: float, data: AmtronData, target: int, soc: float | None, enough_surplus: bool
    ) -> str:
        if enough_surplus:
            self._below_min_since = None
        elif self._below_min_since is None:
            self._below_min_since = now
        soc_too_low = soc is not None and soc < self.settings.soc_stop_pct
        buffer_over = (
            self._below_min_since is not None
            and now - self._below_min_since >= self.settings.buffer_time_min * 60
        )
        if soc_too_low or buffer_over:
            if not self._switch_allowed(now):
                return STATE_MIN_INTERVAL
            self._last_switch = now
            await self._coordinator.async_set_current_limit(0)
            self.released = False
            self._below_min_since = None
            return STATE_WAITING_FOR_SOC if soc_too_low else STATE_WAITING_FOR_SURPLUS
        # Below 6 A of surplus the target is clamped to 6 A: the battery
        # bridges the gap, but only for the buffer time.
        if data.current_limit_a != target:
            await self._coordinator.async_set_current_limit(target)
        return STATE_CHARGING if self._below_min_since is None else STATE_BUFFERING

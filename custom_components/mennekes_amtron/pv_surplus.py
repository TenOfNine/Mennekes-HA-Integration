"""PV surplus tracking from other integrations' entities (read-only).

Surplus = sum(grid feed-in) - grid import - battery discharge + wallbox
charging power, sampled every PV_SAMPLE_INTERVAL_S and averaged over the
last PV_AVERAGE_WINDOW_S. The wallbox's own charging power is added to
each sample *before* averaging: added afterwards, the average would still
carry up to a full window of pre-change samples after every current
change, and anything regulating on it would overshoot.

This module only measures; nothing here writes to the wallbox.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import PV_AVERAGE_WINDOW_S, PV_SAMPLE_INTERVAL_S

if TYPE_CHECKING:
    from .coordinator import AmtronCoordinator

_POWER_UNIT_FACTORS = {"W": 1.0, "kW": 1000.0, "MW": 1_000_000.0}


def read_power_w(state: Any) -> float | None:
    """Return a sensor state as Watts, or None if it isn't a usable power value.

    A state without a unit is taken as Watts (e.g. a template sensor that
    omits it); one with any other unit than W/kW/MW is rejected rather than
    guessed at.
    """
    if state is None:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):  # "unavailable", "unknown", non-numeric
        return None
    unit = state.attributes.get("unit_of_measurement")
    if not unit:
        return value
    factor = _POWER_UNIT_FACTORS.get(unit)
    return None if factor is None else value * factor


class MovingAverage:
    """Arithmetic mean of the samples taken within the last `window_s` seconds."""

    def __init__(self, window_s: float) -> None:
        self._window_s = window_s
        self._samples: deque[tuple[float, float]] = deque()

    def prune(self, now: float) -> None:
        while self._samples and self._samples[0][0] <= now - self._window_s:
            self._samples.popleft()

    def add(self, now: float, value: float) -> None:
        self.prune(now)
        self._samples.append((now, value))

    @property
    def count(self) -> int:
        return len(self._samples)

    @property
    def mean(self) -> float | None:
        if not self._samples:
            return None
        return sum(value for _, value in self._samples) / len(self._samples)


@dataclass(frozen=True)
class PvSample:
    """One reading of every source, in Watts."""

    feed_in_w: float
    grid_import_w: float
    battery_discharge_w: float
    wallbox_power_w: float

    @property
    def surplus_w(self) -> float:
        return self.feed_in_w - self.grid_import_w - self.battery_discharge_w + self.wallbox_power_w


class PvSurplusTracker:
    """Samples the configured sources on a timer and keeps the moving average.

    A sample is skipped entirely - not taken with a gap filled in as 0 - if
    any configured source or the wallbox reading is unavailable, so a
    flapping sensor can't drag the average around. Grid import and battery
    discharge are optional (no battery, or no separate import sensor) and
    count as 0 W when not configured.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: AmtronCoordinator,
        feed_in_entities: list[str],
        grid_import_entity: str | None = None,
        battery_discharge_entity: str | None = None,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._feed_in_entities = list(feed_in_entities)
        self._grid_import_entity = grid_import_entity
        self._battery_discharge_entity = battery_discharge_entity
        self._average = MovingAverage(PV_AVERAGE_WINDOW_S)
        self._listeners: list[Callable[[], None]] = []
        self.last_sample: PvSample | None = None

    @property
    def average_w(self) -> float | None:
        return self._average.mean

    @property
    def sample_count(self) -> int:
        return self._average.count

    def _read_optional(self, entity_id: str | None) -> float | None:
        if entity_id is None:
            return 0.0
        return read_power_w(self._hass.states.get(entity_id))

    def _read_sample(self) -> PvSample | None:
        if not self._coordinator.last_update_success or self._coordinator.data is None:
            return None
        feed_ins = [read_power_w(self._hass.states.get(e)) for e in self._feed_in_entities]
        grid_import = self._read_optional(self._grid_import_entity)
        discharge = self._read_optional(self._battery_discharge_entity)
        if None in feed_ins or grid_import is None or discharge is None:
            return None
        return PvSample(
            feed_in_w=sum(feed_ins),
            grid_import_w=grid_import,
            battery_discharge_w=discharge,
            wallbox_power_w=float(self._coordinator.data.power_w),
        )

    def take_sample(self, now: float) -> None:
        """Take one sample at `now` (seconds) and notify listeners."""
        self._average.prune(now)
        sample = self._read_sample()
        if sample is not None:
            self._average.add(now, sample.surplus_w)
            self.last_sample = sample
        for listener in list(self._listeners):
            listener()

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update_callback)

        def remove() -> None:
            self._listeners.remove(update_callback)

        return remove

    @callback
    def async_start(self) -> Callable[[], None]:
        """Start sampling; returns the function that stops it again."""

        @callback
        def _on_interval(now: datetime) -> None:
            self.take_sample(now.timestamp())

        return async_track_time_interval(
            self._hass, _on_interval, timedelta(seconds=PV_SAMPLE_INTERVAL_S)
        )

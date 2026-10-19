"""Tests for pv_surplus.py: unit handling, the moving average, and how the
tracker combines the configured sources and the wallbox's own power."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from mennekes_amtron.const import PV_AVERAGE_WINDOW_S
from mennekes_amtron.pv_surplus import MovingAverage, PvSurplusTracker, read_power_w


def _state(value, unit: str | None = "W"):
    attributes = {} if unit is None else {"unit_of_measurement": unit}
    return SimpleNamespace(state=str(value), attributes=attributes)


class _StubStates:
    def __init__(self, states: dict) -> None:
        self._states = states

    def get(self, entity_id):
        return self._states.get(entity_id)


class _StubCoordinator:
    def __init__(self, power_w: int = 0, ok: bool = True) -> None:
        self.last_update_success = ok
        self.data = SimpleNamespace(power_w=power_w)


def _tracker(states: dict, wallbox_power_w: int = 0, **kwargs) -> PvSurplusTracker:
    hass = SimpleNamespace(states=_StubStates(states))
    kwargs.setdefault("feed_in_entities", ["sensor.feed_in"])
    return PvSurplusTracker(hass, _StubCoordinator(wallbox_power_w), **kwargs)


# --- read_power_w ---


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("1500", "W", 1500.0),
        ("1.5", "kW", 1500.0),
        ("0.0015", "MW", 1500.0),
        ("1500", None, 1500.0),  # no unit -> taken as Watts
    ],
)
def test_read_power_w_converts_to_watts(value, unit, expected):
    assert read_power_w(_state(value, unit)) == pytest.approx(expected)


@pytest.mark.parametrize(
    "state",
    [None, _state("unavailable"), _state("unknown"), _state("80", "%")],
)
def test_read_power_w_rejects_unusable_states(state):
    assert read_power_w(state) is None


# --- MovingAverage ---


def test_moving_average_is_none_without_samples():
    assert MovingAverage(120).mean is None


def test_moving_average_drops_samples_older_than_the_window():
    average = MovingAverage(120)
    average.add(0, 1000)
    average.add(60, 2000)
    assert average.mean == 1500
    average.add(130, 3000)  # the sample at t=0 is now outside the window
    assert average.count == 2
    assert average.mean == 2500


# --- PvSurplusTracker ---


def test_surplus_sums_feed_ins_subtracts_import_and_discharge_and_adds_wallbox_power():
    tracker = _tracker(
        {
            "sensor.feed_in": _state(2000),
            "sensor.balcony": _state(0.3, "kW"),
            "sensor.import": _state(100),
            "sensor.discharge": _state(200),
        },
        wallbox_power_w=1380,
        feed_in_entities=["sensor.feed_in", "sensor.balcony"],
        grid_import_entity="sensor.import",
        battery_discharge_entity="sensor.discharge",
    )
    tracker.take_sample(0)
    # 2000 + 300 - 100 - 200 + 1380: the wallbox's own draw counts as surplus
    # it is already using, so raising the current doesn't eat into the figure.
    assert tracker.average_w == 3380
    assert tracker.last_sample.surplus_w == 3380


def test_unconfigured_import_and_discharge_count_as_zero():
    tracker = _tracker({"sensor.feed_in": _state(1500)})
    tracker.take_sample(0)
    assert tracker.average_w == 1500


def test_a_sample_is_skipped_when_any_configured_source_is_unavailable():
    states = {"sensor.feed_in": _state(2000), "sensor.import": _state("unavailable")}
    tracker = _tracker(states, grid_import_entity="sensor.import")
    tracker.take_sample(0)
    assert tracker.average_w is None
    assert tracker.last_sample is None


def test_a_sample_is_skipped_while_the_wallbox_is_unreachable():
    hass = SimpleNamespace(states=_StubStates({"sensor.feed_in": _state(2000)}))
    tracker = PvSurplusTracker(hass, _StubCoordinator(ok=False), ["sensor.feed_in"])
    tracker.take_sample(0)
    assert tracker.average_w is None


def test_old_samples_expire_even_while_new_ones_are_skipped():
    states = {"sensor.feed_in": _state(2000)}
    tracker = _tracker(states)
    tracker.take_sample(0)
    states["sensor.feed_in"] = _state("unavailable")
    tracker.take_sample(PV_AVERAGE_WINDOW_S + 10)
    assert tracker.average_w is None


def test_listeners_are_notified_after_every_sample_and_can_be_removed():
    tracker = _tracker({"sensor.feed_in": _state(2000)})
    calls = []
    remove = tracker.async_add_listener(lambda: calls.append(1))
    tracker.take_sample(0)
    remove()
    tracker.take_sample(10)
    assert calls == [1]

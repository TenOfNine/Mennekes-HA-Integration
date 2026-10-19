"""Stub for homeassistant.helpers.event - only what pv_surplus.py imports.

Tests drive PvSurplusTracker.take_sample() directly instead of a timer.
"""


def async_track_time_interval(hass, action, interval):
    return lambda: None

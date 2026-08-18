"""Stub for homeassistant.helpers.selector - only what config_flow.py needs.

Mirrors the real SelectSelector's behavior as a voluptuous validator closely
enough for tests: it's a callable that raises vol.Invalid for a value not
among its configured options, and otherwise passes the value through.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import voluptuous as vol


class SelectSelectorMode(str, Enum):
    LIST = "list"
    DROPDOWN = "dropdown"


@dataclass
class SelectSelectorConfig:
    options: list
    translation_key: str | None = None
    mode: SelectSelectorMode | None = None
    multiple: bool = False
    custom_value: bool = False
    sort: bool = False


class SelectSelector:
    def __init__(self, config: SelectSelectorConfig) -> None:
        self.config = config

    def _allowed_values(self) -> set:
        return {opt if isinstance(opt, str) else opt["value"] for opt in self.config.options}

    def __call__(self, data):
        if data not in self._allowed_values():
            raise vol.Invalid(f"value {data!r} is not a valid option")
        return data

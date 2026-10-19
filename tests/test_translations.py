"""Tests for the translation files: complete, and the same keys in every language.

hassfest checks the schema in CI, but not that every enum state the code
can produce has a translation - a missing one shows up as a raw key like
"no_error" in the frontend.
"""
from __future__ import annotations

import json
from pathlib import Path

import mennekes_amtron.const as c
import pytest
from mennekes_amtron.charge_controller import CONTROL_STATES

COMPONENT = Path(__file__).parent.parent / "custom_components" / "mennekes_amtron"
STRINGS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
LANGUAGES = {
    path.stem: json.loads(path.read_text(encoding="utf-8")) for path in (COMPONENT / "translations").glob("*.json")
}


def _keys(tree: dict, prefix: str = "") -> set[str]:
    keys = set()
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        keys |= _keys(value, path) if isinstance(value, dict) else {path}
    return keys


def test_english_translation_is_a_copy_of_strings_json():
    assert LANGUAGES["en"] == STRINGS


@pytest.mark.parametrize("language", sorted(LANGUAGES))
def test_every_language_has_exactly_the_keys_of_strings_json(language):
    assert _keys(LANGUAGES[language]) == _keys(STRINGS)


@pytest.mark.parametrize(
    "section, key, states",
    [
        ("sensor", "status", [*c.CP_STATUS.values(), "unknown"]),
        ("sensor", "error", c.ERROR_STATES),
        ("sensor", "charge_control", CONTROL_STATES),
        ("select", "charge_mode", c.CHARGE_MODES),
    ],
)
def test_every_enum_state_has_a_translation(section, key, states):
    assert set(STRINGS["entity"][section][key]["state"]) == set(states)

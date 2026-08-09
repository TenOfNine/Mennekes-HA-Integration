# Why a stub `homeassistant` package?

CI here does **not** `pip install homeassistant`. That package pulls in a large
dependency tree (aiohttp, voluptuous-serialize, dozens more) and pins versions
that drift from what this repo actually needs — a slow, fragile way to test
what is fundamentally protocol and register-decoding logic.

Instead, `tests/stubs/homeassistant/` implements just the handful of classes
`coordinator.py`, `config_flow.py`, and `__init__.py` import at module load
time (`ConfigEntry`, `ConfigFlow`, `OptionsFlow`, `DataUpdateCoordinator`,
`HomeAssistantError`, ...) — enough to import and exercise the real,
unmodified integration code against a simulated Modbus TCP server, without
ever touching Home Assistant core.

This deliberately mirrors the "host tier" test split used elsewhere in
SensorsIot repos (e.g. Embedded-AI-Harness): fast, dependency-light tests
that gate every push, with the tradeoff made explicit rather than hidden —
this suite proves the Modbus framing, register math, and config/options
flow logic are correct; it does **not** prove the integration loads cleanly
inside a real Home Assistant instance. `hassfest.yaml` (manifest/translation
schema) and a manual smoke test against real hardware cover the rest.

If a future change needs a `homeassistant` symbol not in `stubs/`, add the
smallest possible stand-in here rather than reaching for the real package.

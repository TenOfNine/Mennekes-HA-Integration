"""Constants for the Mennekes AMTRON Charge Control (ECU) integration.

Register addresses below are taken from the manufacturer document
"ECU-BRx and ECU-BBx Modbus TCP Server Specification" (Doc. Revision 1.07,
MENNEKES Elektrotechnik GmbH & Co. KG), which applies to the ECU controller
used in AMTRON Professional, AMTRON Charge Control, and AMEDIO Professional.

This is a *different* register map, word order, and control mechanism than
the one used by AMTRON Xtra / AMTRON Premium (HCC3 controller) - the two are
not interchangeable.

Known caveat: at least one online report (electronics forum) describes the
documented "Mennekes" register set returning only error responses on their
Charge Control, requiring a switch to an alternative "TQDM100" register set
in the device's own web UI (Konfiguration -> Lastmanagement -> Modbus TCP
Server Registersatz). This integration targets the documented "Mennekes"
register set; if reads fail outright, that setting is the first thing to
check on the device itself.

All registers below are read via Modbus function code 0x03 (Read Holding
Registers) - despite the manufacturer's document labelling them "READ", real
Charge Control units reject function code 0x04 (Read Input Registers) for
these addresses. Confirmed against community reports (see README.md).
"""
from __future__ import annotations

DOMAIN = "mennekes_amtron"

CONF_UNIT_ID = "unit_id"
CONF_MAX_CURRENT_A = "max_current_a"
CONF_PAUSE_CURRENT_A = "pause_current_a"
CONF_START_CURRENT_A = "start_current_a"
CONF_PHASE_MODE = "phase_mode"

DEFAULT_PORT = 502
DEFAULT_UNIT_ID = 255  # empirically confirmed by installers; spec allows 1-255
DEFAULT_SCAN_INTERVAL = 15  # seconds; change here if you want faster/slower polling
REQUEST_TIMEOUT = 5  # seconds per Modbus request

MANUFACTURER = "MENNEKES"
MODEL = "AMTRON Charge Control (ECU)"

# Configurable via the integration's "Configure" option in Home Assistant
# (Settings -> Devices & Services -> Mennekes AMTRON -> Configure), not just
# here - these are just the fallback/initial values.
DEFAULT_MAX_CURRENT_A = 16  # adjust to your supply circuit's actual fusing
DEFAULT_PAUSE_CURRENT_A = 0  # value written by the "Pause" button; 0 = spec default
MIN_CURRENT_A = 6
# Value written by the "Start charging" button before it authorizes the
# session (see AmtronCoordinator.async_start_charging). Defaults to the
# configured max, i.e. "start at full available power" unless overridden.
# The options flow keeps this between MIN_CURRENT_A and the configured
# CONF_MAX_CURRENT_A, never the ABS_MAX_CURRENT_A hard ceiling below.
DEFAULT_START_CURRENT_A = DEFAULT_MAX_CURRENT_A
# Hard ceiling for both options above - the device itself does not accept
# more than this (32 A / 3-phase Type 2, per the general HEMS_CURRENT_LIMIT
# documentation), regardless of what the user configures.
ABS_MAX_CURRENT_A = 32

# --- Phase switching (HEMS_POWER_LIMIT, register 1002) -------------------
# Not (yet) directly confirmed against a physical device by this project the
# way REG_HEMS_CURRENT_LIMIT (1000) was - this is cross-referenced from the
# same "ECU-BRx and ECU-BBx Modbus TCP Server Specification" (Doc. Revision
# 1.07) that register 1000 comes from, via secondary sources (evcc-io
# community discussions/issues, wallbox reseller documentation) that quote
# it by name and describe it as controlling AMTRON Charge Control / other
# ECU-BRx-based devices with load-management-capable phase-switching
# hardware installed. Only relevant if your specific unit has that hardware
# (a physical relay disconnecting L2/L3) - see PHASE_MODE_DISABLED below,
# which is the default and leaves this feature untouched.
PHASE_MODE_DISABLED = "disabled"  # only REG_HEMS_CURRENT_LIMIT is used, as before
PHASE_MODE_SINGLE_PHASE = "single_phase_only"  # HEMS_POWER_LIMIT, clamped to the 1-phase range
PHASE_MODE_AUTO = "auto"  # HEMS_POWER_LIMIT, switches 1-phase/3-phase by requested power
DEFAULT_PHASE_MODE = PHASE_MODE_DISABLED

REG_HEMS_POWER_LIMIT = 1002  # R/W, Watts. 0 = pause; device auto-selects 1p/3p by value range.

# Nominal single-phase voltage used to convert the requested charging power
# (W) to a per-phase current (A) for the minimum-current check below. This
# is a fixed assumption (EU nominal mains), not read from the device - there
# is no documented register for it.
NOMINAL_PHASE_VOLTAGE_V = 230
# A phase is only usable at/above MIN_CURRENT_A (6 A, IEC 61851) per phase;
# below the corresponding wattage, HEMS_POWER_LIMIT falls back to 0 (paused)
# rather than to a current below the spec minimum.
MIN_POWER_1PHASE_W = MIN_CURRENT_A * NOMINAL_PHASE_VOLTAGE_V  # 1380 W
MIN_POWER_3PHASE_W = MIN_CURRENT_A * 3 * NOMINAL_PHASE_VOLTAGE_V  # 4140 W

# --- General system info -------------------------------------------------
REG_FIRMWARE_VERSION = 100  # 100-101, 32-bit - NOT a number, see coordinator.py
FIRMWARE_BLOCK_START = REG_FIRMWARE_VERSION
FIRMWARE_BLOCK_COUNT = 2  # 100, 101

REG_OCPP_STATUS = 104  # 16-bit
# The four ERROR_CODES_ pairs. Only bits 0-21 of pair 4 (111-112) currently
# have documented meaning (see ERROR_FLAGS below); pairs 1-3 (105-110) are
# marked "reserved" by the manufacturer as of this document's revision, but
# are still read and surfaced as raw diagnostic values in case a future (or
# undocumented) firmware populates them - see AmtronErrorSensor.
REG_ERROR_CODES_1 = (105, 106)
REG_ERROR_CODES_2 = (107, 108)
REG_ERROR_CODES_3 = (109, 110)
REG_ERROR_CODES_4 = (111, 112)  # the only pair with documented/named bits
REG_ERROR_CODE_LOW, REG_ERROR_CODE_HIGH = REG_ERROR_CODES_4

STATUS_BLOCK_START = REG_OCPP_STATUS  # 104
STATUS_BLOCK_END = REG_ERROR_CODE_HIGH  # 112
STATUS_BLOCK_COUNT = STATUS_BLOCK_END - STATUS_BLOCK_START + 1  # 9 registers

# Register 104 - "OCPP_CP_STATUS"
CP_STATUS = {
    0: "available",
    1: "occupied",
    2: "reserved",
    3: "unavailable",
    4: "faulted",
    5: "preparing",
    6: "charging",
    7: "suspended_evse",
    8: "suspended_ev",
    9: "finishing",
}

# Registers 111-112 ("ERROR_CODES_4"), one bit per condition (bits 0-21 only;
# all higher bits, and all of ERROR_CODES_1-3, are reserved). Multiple bits
# can be set simultaneously.
ERROR_FLAGS = {
    0: "err_rcmb_triggered",
    1: "err_vehicle_state_e",
    2: "err_mode3_diode_check",
    3: "err_mcb_type2_triggered",
    4: "err_mcb_schuko_triggered",
    5: "err_rcd_triggered",
    6: "err_contactor_weld",
    7: "err_backend_disconnected",
    8: "err_actuator_locking_failed",
    9: "err_actuator_locking_without_plug_failed",
    10: "err_actuator_stuck",
    11: "err_actuator_detection_failed",
    12: "err_fw_update_running",
    13: "err_tilt",
    14: "err_wrong_cp_pr_wiring",
    15: "err_type2_overload_thr_2",
    16: "err_actuator_unlocked_while_charging",
    17: "err_tilt_prevent_charging_until_reboot",
    18: "err_pic24",
    19: "err_usb_stick_handling",
    20: "err_incorrect_phase_installation",
    21: "err_no_power",
}

# --- Meter values from the OCPP primary meter -----------------------------
# Energy L2/L3 are never populated on any documented meter model (they
# always read back "no meter"), and Energy L1 already carries the *total*
# (lifetime) energy for every documented meter type - so only L1 is read.
REG_METER_ENERGY_L1 = 200  # 32-bit, Wh - total energy delivered (lifetime)
REG_METER_POWER_L1 = 206  # 32-bit, W
REG_METER_POWER_L2 = 208  # 32-bit, W
REG_METER_POWER_L3 = 210  # 32-bit, W

METER_BLOCK_START = REG_METER_ENERGY_L1  # 200
METER_BLOCK_END = 217  # end of the Current L3 register pair
METER_BLOCK_COUNT = METER_BLOCK_END - METER_BLOCK_START + 1  # 18 registers

# A 32-bit meter field reads back as this sentinel when no meter is present
# for that line (single-phase installs, or a meter model that only reports
# a "Total Power"/"Total Energy" on the L1 registers).
METER_NOT_PRESENT = 0xFFFFFFFF

# --- Charge process information -------------------------------------------
REG_CHARGED_ENERGY = 705  # 16-bit, Wh, resets to 0 at the start of each session
REG_SIGNALED_CURRENT = 706  # 16-bit, A - what's actually being offered to the EV right now

CHARGE_BLOCK_START = REG_CHARGED_ENERGY  # 705
CHARGE_BLOCK_COUNT = 2  # 705, 706

# --- Holding registers (function codes 0x03 read / 0x06 write) ----------
REG_HEMS_CURRENT_LIMIT = 1000  # R/W, Amps. Writing "pause_current_a" pauses charging (0 by spec default).

# --- Authorization with IDTag (write-only holding registers) ------------
# Writing a (fake) OCPP IdTag here has the same effect as presenting an RFID
# card at the reader. This ONLY works if, on the wallbox itself: (a) "Modbus
# Slave Allow Start/Stop Transaction" is enabled, and (b) "kostenloses Laden"
# (free charging) is enabled under Authorization - otherwise the tag is
# rejected because it isn't on the whitelist. See README.md.
REG_WRITE_IDTAG_START = 1110  # 1110-1119, 10 registers = 20 ASCII bytes
WRITE_IDTAG_REGISTER_COUNT = 10
DEFAULT_ID_TAG = "HOMEASSISTANT"  # arbitrary; only matters if free charging is off

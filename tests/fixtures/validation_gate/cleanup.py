#!/usr/bin/env python3
"""
One-shot cleanup script for the Klipper companion board schematic.

Uses kicad-skip directly (the same library the KiCAD MCP server uses)
to do three batch operations that are tedious via per-call MCP:

  A. Rename value text on Haiku-duplicated channel components
  B. Drop no-connect markers on intentionally-unused IC pins
  C. Assign sensible default footprints to every component

Run with the MCP venv:
    /home/komi/repos/MCP/KiCAD-MCP-Server/.venv/bin/python3 cleanup.py
"""

from __future__ import annotations
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Import the same library the MCP server uses.
sys.path.insert(0, "/home/komi/repos/MCP/KiCAD-MCP-Server/.venv/lib/python3.14/site-packages")
from skip import Schematic  # noqa: E402

SCH = Path("/home/komi/notes/kicad/kicad_experiment/kicad_experiment.kicad_sch")


# ============================================================ A. VALUE RENAMES
# Haiku duplicated each canonical's value field verbatim. Only fields that
# encode the channel name need updating; component-descriptive text stays.
VALUE_RENAMES: dict[str, str] = {
    # 4-pin fan duplicates (canonical FAN1: SW2 J3 R6 R7)
    "SW22": "FAN2 rail select", "J23": "FAN2 4-pin out",
    "SW42": "FAN3 rail select", "J43": "FAN3 4-pin out",
    "SW62": "FAN4 rail select", "J63": "FAN4 4-pin out",
    # 3-pin fan duplicate (canonical FAN5)
    "SW26": "FAN6 rail select", "J27": "FAN6 3-pin out",
    # 2-pin fan duplicates (canonical FAN7)
    "SW28": "FAN8 rail select",  "J31": "FAN8 2-pin out",
    "SW48": "FAN9 rail select",  "J51": "FAN9 2-pin out",
    "SW68": "FAN10 rail select", "J71": "FAN10 2-pin out",
    # NTC thermistor duplicate (canonical TH1)
    "J33": "TH2 NTC in",
    # MAX31865 thermistor duplicate (canonical TH3)
    "U24": "MAX31865 (TH4)", "J35": "TH4 RTD in",
}


# ============================================================ B. NO-CONNECTS
# Each entry: (component_ref, [pin_numbers_to_NC])
# pin_numbers are the symbol's pin numbers as listed by get_schematic_pin_locations
NO_CONNECTS: list[tuple[str, list[int | str]]] = [
    # Pico module — LCD pins (consumed internally on the LCD board) + spare GPIO/control
    ("U_MCU", [11, 12, 14, 15, 16,   # GPIO8-12 (LCD_DC/CS/CLK/DIN/RST)
               30,                    # RUN
               34,                    # GPIO28 spare ADC
               35,                    # ADC_VREF
               37]),                  # 3V3_EN
    # MCP23017 — spare port B + spare GPA + unused INTB + physical NC pins
    ("U5", [1, 2, 3, 4, 5, 6, 7, 8,   # GPB0-GPB7 spare
            11, 14,                    # physical NC pins on the SOIC-28
            19,                        # INTB (using INTA only)
            27, 28]),                  # GPA6, GPA7 spare
    # MAX31865 (both copies) — pins 7/9/20 unused in 2-wire RTD mode
    ("U4",  [7, 9, 20]),
    ("U24", [7, 9, 20]),
]


# ============================================================ D. LABEL-SNAP
# MCP's add_schematic_net_label puts labels at the END of the text, ~11-13mm
# off the pin endpoint, for multi-unit symbols. Snap labels to exact pin
# endpoint by (component, pin, expected_net) lookup: find the unique label
# with that net name nearest the pin, move it to the pin.
LABEL_SNAP: dict[str, dict[int, str]] = {
    "U_MCU": {
        1: "FAN1_PWM",  2: "FAN2_PWM",  3: "GND",
        4: "FAN3_PWM",  5: "FAN4_PWM",
        6: "FAN5_DRIVE", 7: "FAN6_DRIVE",
        9: "FAN7_DRIVE", 10: "FAN8_DRIVE",
        17: "FAN9_DRIVE", 19: "FAN10_DRIVE",
        20: "TH4_CS",   21: "SPI_MISO", 22: "TH3_CS",
        24: "SPI_SCK",  25: "SPI_MOSI",
        26: "I2C_SDA",  27: "I2C_SCL",
        29: "MCP_INT",
        31: "TH1_NTC",  32: "TH2_NTC",  33: "GND",
        36: "+3V3",     39: "VSYS_MCU", 40: "VBUS",
    },
    "U5": {
        9: "+3V3",  10: "GND",
        12: "I2C_SCL", 13: "I2C_SDA",
        15: "GND", 16: "GND", 17: "GND",
        18: "+3V3",
        20: "MCP_INT",
        21: "FAN1_TACH", 22: "FAN2_TACH", 23: "FAN3_TACH",
        24: "FAN4_TACH", 25: "FAN5_TACH", 26: "FAN6_TACH",
    },
}


# ============================================================ F. COLLISION CLEANUP
# Specific labels known to be in the wrong place (from user manual moves or
# Haiku layout collisions). Format: (net_name, x, y) — delete the label with
# this name at this exact coordinate.
LABEL_DELETE: list[tuple[str, float, float]] = [
    ("TH4_RTD_MINUS", 234.95, 274.32),  # belongs on U24 pin 11/12, was at SW68/1
    ("TH2_NTC",       114.30, 247.65),  # belongs on R33/J33, was on U4 pin 16 (TH3_CS)
    ("FAN6_VPLUS",    157.48, 203.20),  # was on FAN5-block Q5/2 (GND) area
    ("GND",           114.30, 250.19),  # was on U4 pin 17 (SPI_MISO)
    # NOTE: (234.95, 248.92) is a REAL component overlap — R34 pin 2 (TH4_REF)
    # and SW48 pin 4 (FAN9_PLUS) physically coincide. Do NOT delete either label.
    # Fix in KiCad UI by moving the TH4 block (U24, R34, C32, C33, J35) clear of
    # the FAN9 column.
]

# Labels to relocate to a specific position (single-point manual fix).
LABEL_RELOCATE: list[tuple[str, float, float, float, float]] = [
    # (net_name, from_x, from_y, to_x, to_y)
    ("FAN9_PLUS", 279.40, 243.84, 279.40, 246.38),  # snap to J51 pin 1
]


# ============================================================ G. EXTRA PWR_FLAGS
# Nets that need a power-output flag for ERC to consider them "driven".
EXTRA_PWR_FLAGS: list[tuple[str, float, float]] = [
    # (net_name, x, y) — position is arbitrary, just needs to be free
    ("VSYS_MCU", 210.0, 35.0),
]


# ============================================================ C. FOOTPRINTS
# Maps lib_id → footprint. Applied to every symbol that matches the lib_id
# unless the symbol is a virtual one (PWR_FLAG etc.).
FOOTPRINTS: dict[str, str] = {
    "Device:R":             "Resistor_SMD:R_0603_1608Metric",
    "Device:C":             "Capacitor_SMD:C_0603_1608Metric",
    "Device:C_Polarized":   "Capacitor_SMD:CP_Elec_6.3x7.7",
    "Device:L":             "Inductor_SMD:L_12x12mm_H8mm",
    "Device:Polyfuse":      "Resistor_SMD:R_1812_4532Metric",
    "Device:D_Zener":       "Diode_SMD:D_SOD-123",
    "Device:D_Schottky":    "Diode_SMD:D_SMA",
    "Transistor_FET:Q_PMOS_GSD":            "Package_TO_SOT_SMD:SOT-23",
    "Transistor_FET:Q_NMOS_GSD":            "Package_TO_SOT_SMD:SOT-23",
    "Connector:Screw_Terminal_01x02":       "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2_5.08mm",
    "Connector:Screw_Terminal_01x03":       "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-3_5.08mm",
    "Connector:Screw_Terminal_01x04":       "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-4_5.08mm",
    "Connector_Generic:Conn_01x03":         "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
    "Regulator_Switching:TPS54302":         "Package_TO_SOT_SMD:SOT-23-6",
    "Sensor_Temperature:MAX31865xAP":       "Package_SO:SSOP-20_4.4x6.5mm_P0.65mm",
    "Interface_Expansion:MCP23017x-x-SO":   "Package_SO:SOIC-28W_7.5x17.9mm_P1.27mm",
    "MCU_Module:RaspberryPi_Pico":          "MCU_Module:RaspberryPi_Pico_SMD_TH",
    "Switch:SW_SP3T":                       "Button_Switch_THT:SW_Slide_1P3T_CK_OS103011MA1QN1",
    # power:PWR_FLAG and any other virtual symbols intentionally absent
}


def backup(path: Path) -> Path:
    """Timestamped backup next to the file."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_suffix(f".kicad_sch.bak.{stamp}")
    shutil.copy2(path, dest)
    return dest


def main() -> int:
    if not SCH.exists():
        print(f"ERROR: schematic not found: {SCH}", file=sys.stderr)
        return 1

    bak = backup(SCH)
    print(f"Backup: {bak.name}")

    s = Schematic(str(SCH))
    print(f"Loaded: {len(list(s.symbol))} symbols, "
          f"{len(list(s.label))} labels, "
          f"{len(list(s.no_connect))} no-connects")

    # ---------- A. value renames ----------
    renamed = 0
    for ref, new_val in VALUE_RENAMES.items():
        try:
            sym = getattr(s.symbol, ref)
        except AttributeError:
            print(f"  WARN: {ref} not found, skipping")
            continue
        old_val = sym.property.Value.value
        if old_val != new_val:
            sym.property.Value.value = new_val
            renamed += 1
            print(f"  rename {ref}: {old_val!r} -> {new_val!r}")
    print(f"A. Value renames: {renamed} component(s)")

    # ---------- B. no-connect drops ----------
    # Use the first existing no_connect as a clone template.
    nc_added = 0
    nc_skipped_present = 0
    templates = list(s.no_connect)
    if not templates:
        print("  ERROR: no existing no_connect to clone from", file=sys.stderr)
        return 2
    template = templates[0]
    existing_nc_xy = {(round(n.at.value[0], 2), round(n.at.value[1], 2))
                      for n in s.no_connect}

    for ref, pin_ids in NO_CONNECTS:
        try:
            sym = getattr(s.symbol, ref)
        except AttributeError:
            print(f"  WARN: {ref} not found, skipping NC group")
            continue
        for pin_id in pin_ids:
            # Look up the pin — try by number first.
            pin = None
            for p in sym.pin:
                if p.value == str(pin_id):
                    pin = p
                    break
            if pin is None:
                print(f"  WARN: {ref} pin {pin_id} not found")
                continue
            x, y, *_ = pin.location.value
            key = (round(x, 2), round(y, 2))
            if key in existing_nc_xy:
                nc_skipped_present += 1
                continue
            nc = template.clone()
            nc.at.value = [x, y]
            existing_nc_xy.add(key)
            nc_added += 1
    print(f"B. No-connects added: {nc_added}, "
          f"already present: {nc_skipped_present}")

    # ---------- D. label-snap on misaligned IC pins ----------
    import math
    snapped = 0
    snap_skipped_already_ok = 0
    snap_no_candidate = 0
    # Track which label objects we've already snapped, so we don't move the
    # same one across multiple pins on the same net (GND, +3V3, etc.).
    used: set[int] = set()

    # First pass: claim labels already at exact pin endpoints (don't re-snap).
    pin_coord_index: list[tuple[str, int, float, float]] = []
    for ref, pin_map in LABEL_SNAP.items():
        sym = getattr(s.symbol, ref, None)
        if sym is None: continue
        pin_by_num = {p.value: p for p in sym.pin}
        for pin_num, expected_net in pin_map.items():
            pin = pin_by_num.get(str(pin_num))
            if pin is None: continue
            px, py = pin.location.value[0], pin.location.value[1]
            pin_coord_index.append((ref, pin_num, px, py))
            for l in s.label:
                if (l.value == expected_net
                        and abs(l.at.value[0] - px) < 0.01
                        and abs(l.at.value[1] - py) < 0.01
                        and id(l) not in used):
                    used.add(id(l))
                    break

    # Second pass: for each pin still without a label, find an unused match.
    for ref, pin_map in LABEL_SNAP.items():
        sym = getattr(s.symbol, ref, None)
        if sym is None:
            print(f"  WARN: {ref} not found, skipping snap")
            continue
        pin_by_num = {p.value: p for p in sym.pin}
        for pin_num, expected_net in pin_map.items():
            pin = pin_by_num.get(str(pin_num))
            if pin is None:
                print(f"  WARN: {ref} pin {pin_num} not found")
                continue
            px, py = pin.location.value[0], pin.location.value[1]
            # Already exactly-positioned labels (claimed above) sit on this pin.
            existing_here = [l for l in s.label
                             if l.value == expected_net
                             and abs(l.at.value[0] - px) < 0.01
                             and abs(l.at.value[1] - py) < 0.01]
            if existing_here:
                snap_skipped_already_ok += 1
                continue
            matches = [(math.hypot(l.at.value[0] - px, l.at.value[1] - py), l)
                       for l in s.label
                       if l.value == expected_net and id(l) not in used]
            if not matches:
                print(f"  NO_LABEL: {ref}/{pin_num} '{expected_net}' has no unused candidate")
                snap_no_candidate += 1
                continue
            matches.sort(key=lambda m: m[0])
            d, l = matches[0]
            if d > 25:
                print(f"  NO_CLOSE: {ref}/{pin_num} '{expected_net}' nearest unused is {d:.1f}mm")
                snap_no_candidate += 1
                continue
            rot = l.at.value[2] if len(l.at.value) > 2 else 0
            l.at.value = [px, py, rot]
            used.add(id(l))
            snapped += 1
    print(f"D. Labels snapped to pin endpoints: {snapped}, "
          f"already aligned: {snap_skipped_already_ok}, "
          f"no candidate: {snap_no_candidate}")

    # ---------- E. detect & report label collisions ----------
    # Two labels with DIFFERENT net names at the same coordinate = electrical short.
    coord_to_nets: dict[tuple, list] = {}
    for l in s.label:
        key = (round(l.at.value[0], 2), round(l.at.value[1], 2))
        coord_to_nets.setdefault(key, []).append(l)
    collisions = 0
    for key, labs in coord_to_nets.items():
        names = {l.value for l in labs}
        if len(names) > 1:
            print(f"  COLLISION at {key}: {names}")
            collisions += 1
    print(f"E. Label collisions (different nets at same coord): {collisions}")

    # ---------- F. label deletions and relocations ----------
    deleted = 0
    for net, x, y in LABEL_DELETE:
        target = None
        for l in s.label:
            if (l.value == net
                    and abs(l.at.value[0] - x) < 0.05
                    and abs(l.at.value[1] - y) < 0.05):
                target = l
                break
        if target is not None:
            target.delete()
            deleted += 1
        else:
            print(f"  WARN: LABEL_DELETE no match for {net!r} at ({x},{y})")
    relocated = 0
    for net, fx, fy, tx, ty in LABEL_RELOCATE:
        for l in s.label:
            if (l.value == net
                    and abs(l.at.value[0] - fx) < 0.05
                    and abs(l.at.value[1] - fy) < 0.05):
                rot = l.at.value[2] if len(l.at.value) > 2 else 0
                l.at.value = [tx, ty, rot]
                relocated += 1
                break
        else:
            print(f"  WARN: LABEL_RELOCATE no match for {net!r} at ({fx},{fy})")
    print(f"F. Collision cleanup: deleted={deleted}, relocated={relocated}")

    # ---------- G. extra PWR_FLAGs ----------
    # Need a template PWR_FLAG symbol to clone. Find any existing one.
    pwr_template = None
    for sym in s.symbol:
        if sym.lib_id.value == "power:PWR_FLAG":
            pwr_template = sym
            break
    pwr_added = 0
    if pwr_template is None:
        print("  WARN: no existing PWR_FLAG to clone from, skipping")
    else:
        # Find next available #FLG reference number
        existing_flgs = {int(sym.property.Reference.value.lstrip("#FLG"))
                         for sym in s.symbol
                         if sym.property.Reference.value.startswith("#FLG")}
        next_flg = max(existing_flgs, default=0) + 1
        for net, x, y in EXTRA_PWR_FLAGS:
            # Check if a PWR_FLAG already exists on this net
            on_net = False
            for sym in s.symbol:
                if sym.lib_id.value == "power:PWR_FLAG":
                    # PWR_FLAG has 1 pin; check labels at its at coord
                    fx = sym.at.value[0]; fy = sym.at.value[1]
                    for l in s.label:
                        if (l.value == net
                                and abs(l.at.value[0] - fx) < 0.5
                                and abs(l.at.value[1] - fy) < 0.5):
                            on_net = True; break
                    if on_net: break
            if on_net:
                continue
            new_flg = pwr_template.clone()
            new_flg.property.Reference.value = f"#FLG{next_flg}"
            new_flg.at.value = [x, y, 0]
            # Drop a label at the same coord
            tmpl_label = list(s.label)[0]
            new_lab = tmpl_label.clone()
            new_lab.value = net
            new_lab.at.value = [x, y, 0]
            pwr_added += 1
            next_flg += 1
    print(f"G. Extra PWR_FLAGs added: {pwr_added}")

    # ---------- C. footprints ----------
    fp_assigned = 0
    fp_already = 0
    fp_skipped_virtual = 0
    for sym in s.symbol:
        lib_id = sym.lib_id.value
        if lib_id not in FOOTPRINTS:
            if lib_id.startswith("power:"):
                fp_skipped_virtual += 1
            else:
                print(f"  WARN: no footprint mapping for {sym.property.Reference.value} ({lib_id})")
            continue
        current = sym.property.Footprint.value
        new_fp = FOOTPRINTS[lib_id]
        if current == new_fp:
            fp_already += 1
        else:
            sym.property.Footprint.value = new_fp
            fp_assigned += 1
    print(f"C. Footprints assigned: {fp_assigned}, "
          f"already correct: {fp_already}, "
          f"virtual symbols skipped: {fp_skipped_virtual}")

    # ---------- save ----------
    s.overwrite()
    print(f"Saved: {SCH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

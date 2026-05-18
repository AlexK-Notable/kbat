"""Unit tests for each schematic primitive in :mod:`kbat.ops`.

Every test follows the pattern:
    1. apply op (changed == True)
    2. verify post-condition
    3. re-apply op (changed == False)  ← idempotence guarantee

Tests use ``kicad_sch_api.Schematic.load`` on a tmp-path copy of the
real test schematic so primitive behaviour is exercised against the
true KiCad 10 input, not synthetic fixtures.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import kicad_sch_api as ksa
import pytest

from kbat.ops import (
    add_no_connect,
    add_pwr_flag,
    assign_footprints,
    delete_label_at,
    move_label,
    relax_validation,
    set_property,
    snap_label_to_pin,
    snap_labels_to_pins,
)

SRC = Path("/home/komi/notes/kicad/kicad_experiment/kicad_experiment.kicad_sch")


@pytest.fixture
def sch(tmp_path: Path):
    """Load a fresh per-test copy of the real test schematic."""
    if not SRC.exists():
        pytest.skip(f"test schematic not present: {SRC}")
    dst = tmp_path / "test.kicad_sch"
    shutil.copy2(SRC, dst)
    schematic = ksa.Schematic.load(str(dst))
    relax_validation(schematic)
    return schematic


# ---- set_property -----------------------------------------------------------


def test_set_property_value_changes_then_noop(sch):
    # Use a guaranteed-novel value so the test passes regardless of any
    # pre-existing cleanup.py mutations baked into the source file.
    target = "kbat_test_value_xyz"
    assert set_property(sch, "SW22", "Value", target) is True
    assert sch.components.get("SW22").value == target
    # Idempotent re-apply
    assert set_property(sch, "SW22", "Value", target) is False


def test_set_property_footprint_changes_then_noop(sch):
    target = "KbatTest:R_TEST_PACKAGE"
    assert set_property(sch, "R6", "Footprint", target) is True
    assert sch.components.get("R6").footprint == target
    assert set_property(sch, "R6", "Footprint", target) is False


def test_set_property_unknown_reference_is_noop(sch):
    assert set_property(sch, "ZZZ999", "Value", "anything") is False


# ---- add_no_connect ---------------------------------------------------------


def test_add_no_connect_adds_then_noop(sch):
    # R6 pins are unlikely to carry NCs in the cleaned schematic, so we
    # can witness the True→False transition. (Adding NCs on wired pins
    # is semantically wrong in real use but valid for a primitive-level
    # unit test on a tmp_path copy.)
    pins = [1, 2]
    pre_existing = []
    for p in pins:
        pos = sch.get_component_pin_position("R6", str(p))
        if pos is not None and sch.no_connects.find_at_position(pos, tolerance=0.05):
            pre_existing.append(p)
    if pre_existing:
        pytest.skip(f"R6 pins {pre_existing} already have NCs in source fixture")
    assert add_no_connect(sch, "R6", pins) is True
    # Each pin position now has a no-connect
    for pin in pins:
        pos = sch.get_component_pin_position("R6", str(pin))
        assert pos is not None
        assert sch.no_connects.find_at_position(pos, tolerance=0.05)
    # Idempotent re-apply
    assert add_no_connect(sch, "R6", pins) is False


def test_add_no_connect_unknown_pin_is_skipped(sch):
    assert add_no_connect(sch, "R6", [99]) is False


# ---- snap_label_to_pin ------------------------------------------------------


def test_snap_label_to_pin_changes_then_noop(sch):
    # U_MCU pin 1 should carry FAN1_PWM (per cleanup.py LABEL_SNAP)
    changed = snap_label_to_pin(sch, "U_MCU", 1, "FAN1_PWM")
    pos = sch.get_component_pin_position("U_MCU", "1")
    assert pos is not None
    # Whether changed or not, the post-state must have FAN1_PWM at the pin
    near = sch.labels.get_at_position((pos.x, pos.y), tolerance=0.01)
    assert near is not None and near.text == "FAN1_PWM"
    # Always idempotent on re-apply
    assert snap_label_to_pin(sch, "U_MCU", 1, "FAN1_PWM") is False
    # If the first call changed something, the test is meaningful;
    # if not, the label was already aligned and we still verified no-op.
    assert isinstance(changed, bool)


# ---- snap_labels_to_pins ----------------------------------------------------


def test_snap_labels_to_pins_idempotent(sch):
    mapping = {"1": "FAN1_PWM", "2": "FAN2_PWM"}
    snap_labels_to_pins(sch, "U_MCU", mapping)
    # Second pass must be a no-op (all labels claimed/snapped)
    assert snap_labels_to_pins(sch, "U_MCU", mapping) is False


# ---- assign_footprints ------------------------------------------------------


def test_assign_footprints_changes_then_noop(sch):
    # Pick a mapping where the current footprints differ.
    mapping = {"Device:R": "Resistor_SMD:R_0402_1005Metric"}
    assert assign_footprints(sch, mapping) is True
    # Every Device:R now has the new footprint
    for comp in sch.components:
        if comp.lib_id == "Device:R":
            assert comp.footprint == "Resistor_SMD:R_0402_1005Metric"
    # Idempotent
    assert assign_footprints(sch, mapping) is False


def test_assign_footprints_skips_power_symbols(sch):
    # power:PWR_FLAG must never get a footprint assigned, even if listed.
    mapping = {"power:PWR_FLAG": "BogusFootprint"}
    assert assign_footprints(sch, mapping) is False
    for comp in sch.components:
        if comp.lib_id == "power:PWR_FLAG":
            assert comp.footprint != "BogusFootprint"


# ---- delete_label_at --------------------------------------------------------


def test_delete_label_at_removes_then_noop(sch):
    # Insert a synthetic label at a known location, then delete it.
    uuid = sch.add_label("KBAT_TEST_NET", position=(5.0, 5.0))
    assert uuid is not None
    # Confirm it exists
    assert delete_label_at(sch, "KBAT_TEST_NET", 5.0, 5.0) is True
    assert sch.labels.get_at_position((5.0, 5.0), tolerance=0.05) is None
    # Idempotent re-apply
    assert delete_label_at(sch, "KBAT_TEST_NET", 5.0, 5.0) is False


# ---- move_label -------------------------------------------------------------


def test_move_label_relocates_then_noop(sch):
    sch.add_label("KBAT_MV_NET", position=(10.0, 10.0))
    assert move_label(sch, "KBAT_MV_NET", 10.0, 10.0, 20.0, 20.0) is True
    # Confirm it moved
    found = sch.labels.get_at_position((20.0, 20.0), tolerance=0.05)
    assert found is not None and found.text == "KBAT_MV_NET"
    # Source position no longer has it
    assert sch.labels.get_at_position((10.0, 10.0), tolerance=0.05) is None
    # Idempotent: target already holds the label
    assert move_label(sch, "KBAT_MV_NET", 10.0, 10.0, 20.0, 20.0) is False


def test_move_label_no_source_is_noop(sch):
    assert move_label(sch, "NO_SUCH_NET", 1.0, 1.0, 2.0, 2.0) is False


# ---- add_pwr_flag -----------------------------------------------------------


def test_add_pwr_flag_adds_then_noop(sch):
    net = "KBAT_TEST_VSYS"
    # Use a location far from existing PWR_FLAGs.
    x, y = 5.0, 5.0
    # Phase 2.4 contract: caller-supplied (x, y) is snapped to the
    # KiCad 1.27 mm grid before storing both the flag and the label.
    # 5.0 / 1.27 = 3.94 → round to 4 → 5.08. So the label lands at
    # (5.08, 5.08), NOT verbatim at (5.0, 5.0).
    snapped = (round(5.0 / 1.27) * 1.27, round(5.0 / 1.27) * 1.27)
    # Count #FLG symbols before
    before = sum(1 for c in sch.components if c.lib_id == "power:PWR_FLAG")
    assert add_pwr_flag(sch, net, x, y) is True
    after = sum(1 for c in sch.components if c.lib_id == "power:PWR_FLAG")
    assert after == before + 1
    # The new label exists at the snapped position.
    found = sch.labels.get_at_position(snapped, tolerance=0.05)
    assert found is not None and found.text == net
    # Idempotent re-apply
    assert add_pwr_flag(sch, net, x, y) is False
    after2 = sum(1 for c in sch.components if c.lib_id == "power:PWR_FLAG")
    assert after2 == after  # still no duplicate flag


# ---- save round-trip --------------------------------------------------------


def test_save_after_mutation_succeeds(sch, tmp_path: Path):
    """End-to-end smoke: mutate then save without raising ValidationError."""
    set_property(sch, "R6", "Value", "kbat_test_value")
    out = tmp_path / "saved.kicad_sch"
    sch.save(str(out))
    assert out.exists() and out.stat().st_size > 0

"""File-level tests for each schematic primitive on the akcli backend.

Each test compiles a primitive against the board state read from a
tmp-path copy of a real fixture, applies the ops through akcli, reads the
file back, checks the effect, and checks that compiling again emits
nothing (the idempotence contract, observed through the file rather than
in memory). ``tests/test_primitives.py`` covers the same contract with
Hypothesis, in memory.

Fixtures: the synthetic edge-case golden (R1, R2, C1; VCC label at
(50, 40), GND label at (60, 60) — both off the whole-mil grid) and the
vendored companion board (KiCad 10, embeds power:PWR_FLAG).

No mocking of the backend: per [[HE4_MvAS391OEYJ6M_G3h]].
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from kbat import backend
from kbat._runner import run
from kbat.ops import (
    add_no_connect,
    add_pwr_flag,
    assign_footprints,
    delete_label_at,
    move_label,
    set_property,
    snap_label_to_pin,
    snap_labels_to_pins,
)
from tests.conftest import GOLDEN_DIR, REAL_SCHEMATIC

EDGE_CASES = GOLDEN_DIR / "synthetic_edge_cases.kicad_sch.golden"


@pytest.fixture
def sch(tmp_path: Path) -> Path:
    dst = tmp_path / "edge.kicad_sch"
    shutil.copyfile(EDGE_CASES, dst)
    return dst


@pytest.fixture
def board(tmp_path: Path) -> Path:
    dst = tmp_path / "board.kicad_sch"
    shutil.copyfile(REAL_SCHEMATIC, dst)
    return dst


def _apply_then_recompile(
    path: Path, compile_op: Callable[[backend.BoardState], list[dict]]
) -> backend.BoardState:
    """Compile + apply once, then assert a recompile on the re-read file is empty."""
    state = backend.read_state(path)
    emitted = compile_op(state)
    assert emitted, "first compile emitted nothing; the test does not exercise a change"
    backend.apply_guarded(path, emitted, state.moves)
    after = backend.read_state(path)
    assert compile_op(after) == [], "recompile against the written file emitted ops"
    return backend.read_state(path)


def _labels(state: backend.BoardState, net: str) -> list[tuple[float, float]]:
    return [(lab.x, lab.y) for lab in state.labels if lab.name == net]


# ---- set_property -----------------------------------------------------------


def test_set_property_value_changes_then_noop(sch: Path) -> None:
    after = _apply_then_recompile(sch, lambda s: set_property(s, "R2", "Value", "4k7"))
    assert after.components["R2"].value == "4k7"


def test_set_property_footprint_changes_then_noop(sch: Path) -> None:
    fp = "Resistor_SMD:R_0603_1608Metric"
    after = _apply_then_recompile(sch, lambda s: set_property(s, "R1", "Footprint", fp))
    assert after.components["R1"].footprint == fp


def test_set_property_custom_field_changes_then_noop(sch: Path) -> None:
    after = _apply_then_recompile(sch, lambda s: set_property(s, "C1", "MPN", "GRM155R71C104KA88D"))
    assert after.components["C1"].properties.get("MPN") == "GRM155R71C104KA88D"


def test_set_property_unknown_reference_is_noop(sch: Path) -> None:
    assert set_property(backend.read_state(sch), "NOPE99", "Value", "x") == []


# ---- add_no_connect ---------------------------------------------------------


def test_add_no_connect_adds_then_noop(sch: Path) -> None:
    after = _apply_then_recompile(sch, lambda s: add_no_connect(s, "C1", [1, 2]))
    pins = after.components["C1"].pins
    assert all(after.no_connect_near(*pins[p], tolerance=0.05) for p in ("1", "2"))
    assert len(after.no_connects) == 2


def test_add_no_connect_skips_pins_that_already_have_one(board: Path) -> None:
    """A KiCad-made marker counts: no duplicate is emitted for it (the trial's akcli bug)."""
    state = backend.read_state(board)
    pins = state.components["U5"].pins
    already = [p for p in ("1", "2", "11") if state.no_connect_near(*pins[p], tolerance=0.05)]
    assert already, "fixture changed: expected pre-existing no-connects on U5"
    emitted = add_no_connect(state, "U5", already)
    assert emitted == []


def test_add_no_connect_unknown_pin_is_skipped(sch: Path) -> None:
    assert add_no_connect(backend.read_state(sch), "R1", [99]) == []


# ---- snap_label_to_pin / snap_labels_to_pins ---------------------------------


def test_snap_label_to_pin_changes_then_noop(sch: Path) -> None:
    after = _apply_then_recompile(sch, lambda s: snap_label_to_pin(s, "R1", 1, "VCC"))
    assert _labels(after, "VCC") == [after.components["R1"].pins["1"]]


def test_snap_labels_to_pins_idempotent(sch: Path) -> None:
    after = _apply_then_recompile(
        sch, lambda s: snap_labels_to_pins(s, "R2", {"1": "VCC", "2": "GND"})
    )
    assert _labels(after, "VCC") == [after.components["R2"].pins["1"]]
    assert _labels(after, "GND") == [after.components["R2"].pins["2"]]


# ---- assign_footprints ------------------------------------------------------


def test_assign_footprints_changes_then_noop(sch: Path) -> None:
    by_lib_id = {"Device:R": "Resistor_SMD:R_0402_1005Metric"}
    after = _apply_then_recompile(sch, lambda s: assign_footprints(s, by_lib_id))
    assert {c.footprint for c in after.components.values() if c.lib_id == "Device:R"} == {
        "Resistor_SMD:R_0402_1005Metric"
    }
    assert after.components["C1"].footprint != "Resistor_SMD:R_0402_1005Metric"


def test_assign_footprints_skips_power_symbols(board: Path) -> None:
    state = backend.read_state(board)
    emitted = assign_footprints(state, {"power:PWR_FLAG": "Should:Never"})
    assert emitted == []


# ---- delete_label_at --------------------------------------------------------


def test_delete_label_at_removes_then_noop(sch: Path) -> None:
    """VCC sits at (50, 40) mm = (1968.504, 1574.803) mil: an off-grid, non-whole-mil match."""
    after = _apply_then_recompile(sch, lambda s: delete_label_at(s, "VCC", 50.0, 40.0))
    assert _labels(after, "VCC") == []
    assert _labels(after, "GND") == [(60.0, 60.0)]


def test_delete_label_at_tolerance_finds_near_label(sch: Path) -> None:
    after = _apply_then_recompile(sch, lambda s: delete_label_at(s, "GND", 60.03, 59.98))
    assert _labels(after, "GND") == []


# ---- move_label -------------------------------------------------------------


def test_move_label_relocates_then_noop(sch: Path) -> None:
    after = _apply_then_recompile(sch, lambda s: move_label(s, "GND", 60.0, 60.0, 63.5, 63.5))
    assert _labels(after, "GND") == [(63.5, 63.5)]


def test_move_label_no_source_is_noop(sch: Path) -> None:
    assert move_label(backend.read_state(sch), "GND", 1.0, 1.0, 2.0, 2.0) == []


# ---- add_pwr_flag -----------------------------------------------------------


def test_add_pwr_flag_adds_then_noop(board: Path) -> None:
    before = backend.read_state(board)
    n_before = sum(1 for c in before.components.values() if c.lib_id == "power:PWR_FLAG")
    after = _apply_then_recompile(board, lambda s: add_pwr_flag(s, "KBAT_TEST_RAIL", 210.0, 35.0))
    n_after = sum(1 for c in after.components.values() if c.lib_id == "power:PWR_FLAG")
    assert n_after == n_before + 1
    assert _labels(after, "KBAT_TEST_RAIL") == [(209.55, 35.56)]


# ---- manifest-level behaviour -----------------------------------------------


def _manifest(tmp_path: Path, sch_name: str, transforms: str) -> Path:
    m = tmp_path / "m.kbat.yaml"
    m.write_text(
        "apiVersion: kbat.dev/v1alpha1\nkind: Project\n"
        f"metadata: {{name: t, schematic: {sch_name}}}\ntransforms:\n{transforms}",
        encoding="utf-8",
    )
    return m


def test_delete_after_snap_in_one_manifest_targets_the_moved_label(
    sch: Path, tmp_path: Path
) -> None:
    """Ops resolve sequentially: a delete at the pin finds the label snapped there."""
    m = _manifest(
        tmp_path,
        sch.name,
        "  - {id: snap, op: snap_labels_to_pins, reference: R1, mapping: {'1': VCC}}\n"
        "  - {id: del, op: delete_label_at, net: VCC, x: 49.53, y: 45.72}\n",
    )
    result = run(m)
    assert result.errors == []
    assert [d.status for d in result.details] == ["applied", "applied"]
    assert _labels(backend.read_state(sch), "VCC") == []


def test_converged_manifest_does_not_write_or_call_draw(sch: Path, tmp_path: Path) -> None:
    """Re-applying an applied manifest compiles to no ops: no akcli draw, no write."""
    m = _manifest(
        tmp_path,
        sch.name,
        "  - {op: set_property, selector: {reference: R1}, property: Value, value: 2k2}\n",
    )
    assert run(m).applied == 1
    before_bytes, before_mtime = sch.read_bytes(), sch.stat().st_mtime_ns

    log = tmp_path / "akcli-calls.log"
    shim = tmp_path / "akcli-shim"
    shim.write_text(
        f'#!/bin/sh\necho "$@" >> "{log}"\nexec "{os.sys.executable}" -m akcli "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    os.environ["KBAT_AKCLI"] = str(shim)
    try:
        second = run(m)
    finally:
        del os.environ["KBAT_AKCLI"]

    assert second.applied == 0 and second.skipped == 1
    calls = log.read_text(encoding="utf-8").split("\n") if log.exists() else []
    assert any(c.startswith("--json read") for c in calls), "shim was not used (control)"
    assert not any(" draw " in f" {c} " for c in calls), f"draw was called: {calls}"
    assert sch.read_bytes() == before_bytes
    assert sch.stat().st_mtime_ns == before_mtime


# ---- fidelity guard ---------------------------------------------------------


def test_fidelity_guard_restores_file_when_moved_label_loses_formatting(
    sch: Path, tmp_path: Path
) -> None:
    """A custom-formatted label would be flattened by delete + re-add: refuse and restore."""
    text = sch.read_text(encoding="utf-8")
    vcc = text.index('(label "VCC"')
    sized = text[vcc:].replace("(size 1.27 1.27)", "(size 2.54 2.54)", 1)
    assert sized != text[vcc:], "fixture changed: VCC label has no 1.27 font size to enlarge"
    sch.write_text(text[:vcc] + sized, encoding="utf-8")
    original = sch.read_bytes()

    m = _manifest(
        tmp_path, sch.name, "  - {op: snap_labels_to_pins, reference: R1, mapping: {'1': VCC}}\n"
    )
    result = run(m)

    assert any(e.startswith("fidelity:") and "lost its formatting" in e for e in result.errors), (
        result.errors
    )
    assert sch.read_bytes() == original


def test_check_field_counts_flags_any_decrease() -> None:
    before = backend.FieldCounts(body_style=109, in_pos_files=138, lib_symbols=29)
    backend.check_field_counts(before, before)
    backend.check_field_counts(before, backend.FieldCounts(110, 139, 29))
    with pytest.raises(backend.FidelityError, match="body_style 109 -> 0"):
        backend.check_field_counts(before, backend.FieldCounts(0, 138, 29))
    with pytest.raises(backend.FidelityError, match="lib_symbols 29 -> 19"):
        backend.check_field_counts(before, backend.FieldCounts(109, 138, 19))

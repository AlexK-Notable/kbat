"""End-to-end fidelity on the real KiCad 10 board (replaces the kicad-sch-api
round-trip and golden tests).

The old backend's tests asserted that its own load/save cycle was stable.
The contract that matters to a user is different: applying a manifest to
a real board changes what the manifest targets and nothing else, and
re-applying it changes nothing. Run here on the vendored pre-cleanup
companion board with the full cleanup manifest (the gate runs the same
flow with more criteria under ``-m gate``).
"""

from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path

from kbat import backend
from kbat._runner import run
from tests.conftest import REAL_SCHEMATIC

MANIFEST = REAL_SCHEMATIC.parent / "cleanup.kbat.yaml"


def _no_connect_points(path: Path) -> Counter:
    text = path.read_text(encoding="utf-8")
    return Counter(re.findall(r"\(no_connect\s*\(at ([-\d.]+) ([-\d.]+)\)", text))


def test_cleanup_manifest_keeps_kicad10_fields_and_converges(tmp_path: Path) -> None:
    board = tmp_path / "board.kicad_sch"
    shutil.copyfile(REAL_SCHEMATIC, board)
    before = backend.FieldCounts.of(board)
    assert before.body_style > 0 and before.in_pos_files > 0, "fixture is not a KiCad 10 file"

    first = run(MANIFEST, schematic_override=board)
    assert first.errors == []
    assert first.applied > 0
    assert any("SPLIT FAN6_VPLUS" in line for line in first.net_changes)

    after = backend.FieldCounts.of(board)
    backend.check_field_counts(before, after)

    ncs = _no_connect_points(board)
    assert sum(ncs.values()) == len(ncs) == 28, "duplicate or missing no-connect markers"

    applied_bytes = board.read_bytes()
    second = run(MANIFEST, schematic_override=board)
    assert second.errors == []
    assert second.applied == 0
    assert board.read_bytes() == applied_bytes


def test_apply_rewrites_only_what_the_manifest_targets(tmp_path: Path) -> None:
    """A one-op manifest changes one line of the 19k-line board."""
    board = tmp_path / "board.kicad_sch"
    shutil.copyfile(REAL_SCHEMATIC, board)
    m = tmp_path / "one.kbat.yaml"
    m.write_text(
        "apiVersion: kbat.dev/v1alpha1\nkind: Project\n"
        "metadata: {name: t, schematic: board.kicad_sch}\n"
        "transforms:\n"
        "  - {op: set_property, selector: {reference: J33}, property: Value, value: TH2 NTC in}\n",
        encoding="utf-8",
    )
    original = REAL_SCHEMATIC.read_text(encoding="utf-8").splitlines()
    assert run(m).applied == 1
    changed = [
        (a, b)
        for a, b in zip(original, board.read_text(encoding="utf-8").splitlines(), strict=True)
        if a != b
    ]
    assert changed == [('\t\t(property "Value" "TH1 NTC in"', '\t\t(property "Value" "TH2 NTC in"')]

"""Phase 1 validation harness — the hard-gate criteria.

The criteria from spec §9 / gate [[9qP6PTTxu4B90lFQxjrHX]], plus 1.G.e:

  1.G.a  LOC                       — cleanup.kbat.yaml ≤ 80% of cleanup.py
  1.G.b  Functional equivalence    — kbat apply ≡ cleanup.py on load-bearing fields
  1.G.c  Idempotence               — second apply is a byte-exact no-op
  1.G.d  Readable diff             — kbat plan emits ≥3 op-keywords
  1.G.e  KiCad 10 fidelity         — apply drops no body_style / in_pos_files /
                                     lib_symbols entries

Criterion 1.G.b compares LOAD-BEARING facts that matter for schematic
editing (components + properties, labels + positions, no-connects,
footprints, power flags) rather than bytes, because cleanup.py
(kicad-skip) and kbat (akcli) serialise differently. Its reference is
``expected_cleanup_output.kicad_sch``: cleanup.py's output on the vendored
pre-cleanup board, generated once (2026-10-17) and vendored so the gate
never executes cleanup.py. (Until then the gate ran cleanup.py live; its
path patch never matched the vendored path, so cleanup.py edited the
author's real board and the gate compared against an untouched copy of an
already-cleaned fixture.)

One declared difference is normalised in 1.G.b: kbat grid-snaps
``add_pwr_flag`` positions to 1.27 mm (ROADMAP Appendix A Flag #1) and
cleanup.py does not, so power-flag positions, and labels co-located with
a power flag, are compared after snapping to the grid.

KiCad 10 fields are no longer ignored: kbat moved from kicad-sch-api
(which dropped them on save) to akcli (which edits in place), and 1.G.e
checks they survive.

Run via ``python -m kbat._validation --criteria all`` (exits non-zero on
any failure) or via the wrapper ``scripts/validate_phase1.sh``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sexpdata

# ---- canonical paths ---------------------------------------------------------
#
# Paths are env-derived so the gate is portable across machines and CI.
# Defaults point at vendored fixtures under ``tests/fixtures/validation_gate/``
# so a freshly-cloned checkout works without any environment setup.

REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_DIR_DEFAULT = REPO_ROOT / "tests" / "fixtures" / "validation_gate"
EXPERIMENT_DIR = Path(
    os.environ.get("KBAT_VALIDATION_FIXTURE_DIR", _FIXTURE_DIR_DEFAULT)
)
SRC_SCH = EXPERIMENT_DIR / "kicad_experiment.kicad_sch"
EXPECTED_SCH = EXPERIMENT_DIR / "expected_cleanup_output.kicad_sch"
PY_SCRIPT = EXPERIMENT_DIR / "cleanup.py"
KBAT_MANIFEST = EXPERIMENT_DIR / "cleanup.kbat.yaml"

#: KiCad's 0.05 in schematic grid; kbat snaps add_pwr_flag positions to it.
GRID_MM = 1.27

# Positional tolerance: round to 0.01 mm. The schematic stores coordinates
# at 0.0001 mm precision but cleanup.py routinely uses ``round(x, 2)`` and
# 0.05 mm comparison tolerances. 0.01 mm rounding gives a stable hashable
# key for set membership while preserving observable position semantics.
COORD_PRECISION = 2

# Per-symbol KiCad-10 schema extensions. The old kicad-sch-api backend
# dropped them on save, so the functional check (1.G.b) ignores them;
# criterion 1.G.e now checks separately that none are lost.
KICAD10_LOSSY_ATTRS = frozenset({"body_style", "in_pos_files"})

# Property fields we consider load-bearing for component equivalence. Every
# (name, value) pair under a symbol's ``property`` forms is captured (custom
# fields included); only the visibility/position metadata of properties is
# ignored.
# (Implementation note: we capture ALL properties by name, no allowlist —
# the constant below documents the well-known ones for readers.)
KNOWN_PROPERTIES = ("Reference", "Value", "Footprint", "Datasheet", "Description")

POWER_FLAG_LIB_ID = "power:PWR_FLAG"


# ---- functional facts extraction (criterion 1.G.b) --------------------------


@dataclass(frozen=True)
class FunctionalFacts:
    """Load-bearing facts extracted from a .kicad_sch file.

    All fields use frozensets or sorted tuples so equality is
    order-independent and the dataclass itself is hashable.
    """

    # ``components``: maps reference (e.g. "R6", "U_MCU") to a frozenset of
    # (property_name, property_value) tuples plus the ``lib_id``. PWR_FLAGs
    # are EXCLUDED because their reference numbers are auto-allocated and
    # may legitimately differ between cleanup.py and kbat.
    components: frozenset[tuple[str, frozenset[tuple[str, str]]]] = field(
        default_factory=frozenset
    )
    # ``labels``: net-name + rounded (x, y) position. Rotation excluded —
    # it does not affect net connectivity.
    labels: frozenset[tuple[str, float, float]] = field(default_factory=frozenset)
    # ``no_connects``: rounded (x, y) positions of all NC markers.
    no_connects: frozenset[tuple[float, float]] = field(default_factory=frozenset)
    # ``power_flags``: rounded (x, y) positions of all PWR_FLAG symbols.
    # Keyed by position because the auto-allocated #FLGn reference may
    # differ between backends.
    power_flags: frozenset[tuple[float, float]] = field(default_factory=frozenset)
    # ``footprint_assignments``: ref -> footprint string. Redundant with
    # ``components`` (Footprint is a property) but kept as a separate
    # field for clean per-field diff reporting.
    footprint_assignments: frozenset[tuple[str, str]] = field(
        default_factory=frozenset
    )


def _round_coord(value: Any) -> float:
    """Round a coordinate to ``COORD_PRECISION`` decimal places."""
    return round(float(value), COORD_PRECISION)


def _find_child(form: list[Any], head_name: str) -> list[Any] | None:
    """Return the first child of ``form`` whose head Symbol equals
    ``head_name``, or None."""
    for child in form[1:]:
        if isinstance(child, list) and child and str(child[0]) == head_name:
            return child
    return None


def _find_children(form: list[Any], head_name: str) -> list[list[Any]]:
    """Return all children of ``form`` whose head Symbol equals
    ``head_name``."""
    return [
        child
        for child in form[1:]
        if isinstance(child, list) and child and str(child[0]) == head_name
    ]


def _scalar(value: Any) -> str:
    """Coerce a sexpdata Symbol or scalar to its string form."""
    if isinstance(value, sexpdata.Symbol):
        return str(value)
    return str(value)


def extract_functional_facts(sch_path: Path) -> FunctionalFacts:
    """Extract load-bearing facts from a .kicad_sch file.

    Ignores: orphan ``lib_symbols`` entries, per-symbol ``body_style`` /
    ``in_pos_files`` attributes, whitespace, comment ordering, trailing
    newlines.
    """
    tree = sexpdata.loads(sch_path.read_text(encoding="utf-8"))

    components_set: set[tuple[str, frozenset[tuple[str, str]]]] = set()
    labels_set: set[tuple[str, float, float]] = set()
    no_connects_set: set[tuple[float, float]] = set()
    power_flags_set: set[tuple[float, float]] = set()
    footprint_set: set[tuple[str, str]] = set()

    for child in tree[1:]:
        if not isinstance(child, list) or not child:
            continue
        head = str(child[0])

        if head == "symbol":
            # Extract lib_id + properties; classify as component or PWR_FLAG.
            lib_id_form = _find_child(child, "lib_id")
            lib_id = lib_id_form[1] if lib_id_form else ""

            # Defensive: KICAD10_LOSSY_ATTRS (e.g. ``body_style``,
            # ``in_pos_files``) are top-level symbol children that
            # kicad-sch-api 0.5.5 silently strips on save. cleanup.py
            # output preserves them; kbat output drops them. We must
            # never capture them in FunctionalFacts or criterion 1.G.b
            # would flag a false-positive divergence on every run.
            #
            # The extraction below uses explicit-head lookups
            # (``_find_child(child, "lib_id")``, ``_find_children(child,
            # "property")``, ``_find_child(child, "at")``) so unknown
            # heads — including KICAD10_LOSSY_ATTRS — are already
            # ignored by construction. This filter is a load-bearing
            # reference to the constant: if a future maintainer broadens
            # the loop to iterate all symbol children, the explicit
            # exclusion here is what documents the contract.
            _lossy_heads_present = {
                str(sub[0])
                for sub in child[1:]
                if isinstance(sub, list) and sub and str(sub[0]) in KICAD10_LOSSY_ATTRS
            }
            # _lossy_heads_present is computed for documentation only; the
            # extraction below does not consume it. Asserting against the
            # constant ensures the symbol is referenced (lint, audits).
            assert _lossy_heads_present.issubset(KICAD10_LOSSY_ATTRS)

            # Collect all properties
            properties: dict[str, str] = {}
            for prop in _find_children(child, "property"):
                # (property "Name" "Value" ...)
                if len(prop) >= 3:
                    pname = _scalar(prop[1])
                    pvalue = _scalar(prop[2])
                    properties[pname] = pvalue

            reference = properties.get("Reference", "")

            # Position (for PWR_FLAGs and power flag positional matching)
            at_form = _find_child(child, "at")
            if at_form and len(at_form) >= 3:
                pos = (_round_coord(at_form[1]), _round_coord(at_form[2]))
            else:
                pos = (0.0, 0.0)

            if lib_id == POWER_FLAG_LIB_ID:
                # Auto-allocated #FLGn reference may differ between
                # backends; key by position only.
                power_flags_set.add(pos)
                continue

            # Regular component. Include lib_id as a synthetic property so
            # it counts as load-bearing.
            prop_pairs: set[tuple[str, str]] = {("lib_id", lib_id)}
            for pname, pvalue in properties.items():
                prop_pairs.add((pname, pvalue))

            components_set.add((reference, frozenset(prop_pairs)))

            footprint = properties.get("Footprint", "")
            footprint_set.add((reference, footprint))

        elif head == "label":
            # (label "NET_NAME" (at x y rot) ...)
            if len(child) >= 3:
                net = _scalar(child[1])
                at_form = _find_child(child, "at")
                if at_form and len(at_form) >= 3:
                    x = _round_coord(at_form[1])
                    y = _round_coord(at_form[2])
                    labels_set.add((net, x, y))

        elif head == "no_connect":
            at_form = _find_child(child, "at")
            if at_form and len(at_form) >= 3:
                x = _round_coord(at_form[1])
                y = _round_coord(at_form[2])
                no_connects_set.add((x, y))

    return FunctionalFacts(
        components=frozenset(components_set),
        labels=frozenset(labels_set),
        no_connects=frozenset(no_connects_set),
        power_flags=frozenset(power_flags_set),
        footprint_assignments=frozenset(footprint_set),
    )


def _diff_sets(label: str, left: frozenset[Any], right: frozenset[Any]) -> str:
    """Render a symmetric-difference report for two frozensets."""
    only_left = left - right
    only_right = right - left
    parts = [
        f"{label} differ: |left|={len(left)} |right|={len(right)} "
        f"|only_left|={len(only_left)} |only_right|={len(only_right)}"
    ]
    # Sample up to 5 differences each side for diagnostics
    if only_left:
        sample = sorted(only_left, key=repr)[:5]
        parts.append(f"  only in cleanup.py output: {sample}")
    if only_right:
        sample = sorted(only_right, key=repr)[:5]
        parts.append(f"  only in kbat output: {sample}")
    return "\n".join(parts)


def functional_equivalence_check(
    left_path: Path,
    right_path: Path,
) -> tuple[bool, str]:
    """Compare two .kicad_sch files for functional equivalence.

    Returns (passed, message). Differences in non-load-bearing fields
    (orphan lib_symbols, body_style, in_pos_files, whitespace) are
    normalized away. Differences in load-bearing fields (components,
    nets/labels, NCs, properties, footprints, power flags) are reported
    with per-field symmetric-difference summaries.

    ``left_path`` is conventionally the cleanup.py output (oracle);
    ``right_path`` is conventionally the kbat output.
    """
    left = extract_functional_facts(left_path)
    right = extract_functional_facts(right_path)

    if left == right:
        return True, (
            "functional equivalence: all load-bearing fields match "
            f"(components={len(left.components)}, labels={len(left.labels)}, "
            f"no_connects={len(left.no_connects)}, "
            f"power_flags={len(left.power_flags)}, "
            f"footprints={len(left.footprint_assignments)})"
        )

    diffs: list[str] = []
    if left.components != right.components:
        diffs.append(_diff_sets("components", left.components, right.components))
    if left.labels != right.labels:
        diffs.append(_diff_sets("labels", left.labels, right.labels))
    if left.no_connects != right.no_connects:
        diffs.append(_diff_sets("no_connects", left.no_connects, right.no_connects))
    if left.power_flags != right.power_flags:
        diffs.append(_diff_sets("power_flags", left.power_flags, right.power_flags))
    if left.footprint_assignments != right.footprint_assignments:
        diffs.append(
            _diff_sets(
                "footprint_assignments",
                left.footprint_assignments,
                right.footprint_assignments,
            )
        )

    return False, "\n".join(diffs) if diffs else "unknown functional divergence"


# ---- criterion 1.G.a: LOC ---------------------------------------------------


def _criterion_loc_with_msg() -> tuple[bool, str]:
    """Manifest ≤ 80% the line count of cleanup.py. Returns (ok, msg)."""
    py_lines = sum(1 for _ in PY_SCRIPT.open(encoding="utf-8"))
    manifest_lines = sum(1 for _ in KBAT_MANIFEST.open(encoding="utf-8"))
    limit = py_lines * 80 // 100
    ok = manifest_lines <= limit
    msg = f"manifest={manifest_lines} py={py_lines} limit={limit}"
    return ok, msg


def criterion_loc() -> bool:
    """Criterion 1.G.a — manifest LOC ≤ 80% of cleanup.py LOC.

    Boolean wrapper for ``tests/gate/test_validation_gate.py``. Use
    ``_criterion_loc_with_msg`` when a diagnostic string is needed.
    """
    ok, _ = _criterion_loc_with_msg()
    return ok


# ---- criterion 1.G.b: functional equivalence --------------------------------


def _snap(v: float) -> float:
    return round(round(v / GRID_MM) * GRID_MM, COORD_PRECISION)


def _grid_snap_power_flags(facts: FunctionalFacts) -> FunctionalFacts:
    """Snap power flags, and labels sitting on one, to the 1.27 mm grid."""
    flag_points = set(facts.power_flags)
    labels = frozenset(
        (net, _snap(x), _snap(y)) if (x, y) in flag_points else (net, x, y)
        for net, x, y in facts.labels
    )
    return FunctionalFacts(
        components=facts.components,
        labels=labels,
        no_connects=facts.no_connects,
        power_flags=frozenset((_snap(x), _snap(y)) for x, y in facts.power_flags),
        footprint_assignments=facts.footprint_assignments,
    )


def _run_kbat_apply_against(work_dir: Path) -> Path:
    """Copy schematic to ``work_dir``, run ``kbat apply`` against it."""
    from kbat._runner import run

    sch_copy = work_dir / "kicad_experiment.kicad_sch"
    shutil.copy2(SRC_SCH, sch_copy)
    run(KBAT_MANIFEST, schematic_override=sch_copy)
    return sch_copy


def _criterion_functional_equivalence_with_msg() -> tuple[bool, str]:
    """Functional equivalence on load-bearing fields (components, labels,
    NCs, power flags, footprints) between kbat's output and the vendored
    cleanup.py output, after the declared power-flag grid snap.

    Returns (ok, msg).
    """
    with tempfile.TemporaryDirectory(prefix="kbat_val_b_") as td:
        try:
            kbat_out = _run_kbat_apply_against(Path(td))
        except Exception as e:  # noqa: BLE001
            return False, f"kbat apply crashed: {e!r}"
        expected = _grid_snap_power_flags(extract_functional_facts(EXPECTED_SCH))
        actual = _grid_snap_power_flags(extract_functional_facts(kbat_out))
        diffs = [
            _diff_sets(name, getattr(expected, name), getattr(actual, name))
            for name in (
                "components",
                "labels",
                "no_connects",
                "power_flags",
                "footprint_assignments",
            )
            if getattr(expected, name) != getattr(actual, name)
        ]
        if diffs:
            return False, "; ".join(diffs)
        return True, (
            f"equivalent: {len(actual.components)} components, {len(actual.labels)} labels, "
            f"{len(actual.no_connects)} no-connects, {len(actual.power_flags)} power flags"
        )


def criterion_functional_equivalence() -> bool:
    """Criterion 1.G.b — kbat output ≡ cleanup.py output on load-bearing fields.

    Boolean wrapper for ``tests/gate/test_validation_gate.py``.
    """
    ok, _ = _criterion_functional_equivalence_with_msg()
    return ok


# ---- criterion 1.G.c: idempotence -------------------------------------------


def _criterion_idempotence_with_msg() -> tuple[bool, str]:
    """Second `kbat apply` against an already-applied schematic must be a
    byte-exact no-op. Returns (ok, msg)."""
    from kbat._runner import run

    with tempfile.TemporaryDirectory(prefix="kbat_val_c_") as td:
        td_path = Path(td)
        sch = td_path / "kicad_experiment.kicad_sch"
        shutil.copy2(SRC_SCH, sch)

        # First apply
        run(KBAT_MANIFEST, schematic_override=sch)
        after_first = td_path / "after_first.kicad_sch"
        shutil.copy2(sch, after_first)

        # Second apply (must be no-op)
        run(KBAT_MANIFEST, schematic_override=sch)

        a = after_first.read_bytes()
        b = sch.read_bytes()
        if a == b:
            return True, f"byte-identical ({len(a)} bytes)"
        return False, f"second apply mutated schematic: {len(a)} -> {len(b)} bytes"


def criterion_idempotence() -> bool:
    """Criterion 1.G.c — second apply is a byte-exact no-op.

    Boolean wrapper for ``tests/gate/test_validation_gate.py``.
    """
    ok, _ = _criterion_idempotence_with_msg()
    return ok


# ---- criterion 1.G.d: readable diff -----------------------------------------

OP_KEYWORDS = ("set_property", "no_connect", "footprint", "snap", "label", "pwr_flag")


def _criterion_readable_diff_with_msg() -> tuple[bool, str]:
    """``kbat plan`` must emit structured op-naming output. Returns (ok, msg)."""
    # --schematic: the manifest names the author's real board; plan the
    # vendored copy so the gate is portable and never reads it.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kbat.cli.main",
            "plan",
            str(KBAT_MANIFEST),
            "--schematic",
            str(SRC_SCH),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    out = result.stdout
    lines = out.splitlines()
    matched = 0
    for line in lines:
        lower = line.lower()
        if any(kw in lower for kw in OP_KEYWORDS):
            matched += 1
    if matched >= 3:
        return True, f"OK: {matched} op-keyword line(s) matched"
    return False, (
        f"FAIL: only {matched} op-keyword lines matched in plan output "
        f"(stdout len={len(out)})"
    )


def criterion_readable_diff() -> bool:
    """Criterion 1.G.d — ``kbat plan`` emits ≥3 op-keyword lines.

    Boolean wrapper for ``tests/gate/test_validation_gate.py``.
    """
    ok, _ = _criterion_readable_diff_with_msg()
    return ok


# ---- criterion 1.G.e: KiCad 10 fidelity -------------------------------------


def _criterion_kicad10_fidelity_with_msg() -> tuple[bool, str]:
    """Applying the manifest must not drop KiCad 10 per-symbol attributes
    or embedded lib_symbols. Returns (ok, msg)."""
    from kbat.backend import FieldCounts

    before = FieldCounts.of(SRC_SCH)
    with tempfile.TemporaryDirectory(prefix="kbat_val_e_") as td:
        after = FieldCounts.of(_run_kbat_apply_against(Path(td)))
    lost = [
        f"{name} {getattr(before, name)}->{getattr(after, name)}"
        for name in ("body_style", "in_pos_files", "lib_symbols")
        if getattr(after, name) < getattr(before, name)
    ]
    if lost:
        return False, "dropped: " + ", ".join(lost)
    return True, (
        f"kept body_style={after.body_style} in_pos_files={after.in_pos_files} "
        f"lib_symbols={after.lib_symbols}"
    )


def criterion_kicad10_fidelity() -> bool:
    """Criterion 1.G.e — apply drops no KiCad 10 fields.

    Boolean wrapper for ``tests/gate/test_validation_gate.py``.
    """
    ok, _ = _criterion_kicad10_fidelity_with_msg()
    return ok


# ---- driver -----------------------------------------------------------------

CRITERIA = {
    "a": ("LOC", _criterion_loc_with_msg),
    "b": ("Functional equivalence", _criterion_functional_equivalence_with_msg),
    "c": ("Idempotence", _criterion_idempotence_with_msg),
    "d": ("Readable diff", _criterion_readable_diff_with_msg),
    "e": ("KiCad 10 fidelity", _criterion_kicad10_fidelity_with_msg),
}


def run_criteria(which: list[str]) -> int:
    """Run requested criteria; return 0 if all pass, 1 otherwise."""
    overall_ok = True
    for key in which:
        if key not in CRITERIA:
            print(f"unknown criterion: {key!r}", file=sys.stderr)
            return 2
        name, fn = CRITERIA[key]
        try:
            ok, msg = fn()
        except Exception as e:  # noqa: BLE001
            ok = False
            msg = f"harness error: {e!r}"
        status = "PASS" if ok else "FAIL"
        print(f"  1.G.{key}  {status}  {name}: {msg}")
        if not ok:
            overall_ok = False
    print()
    print("OVERALL:", "PASS" if overall_ok else "FAIL")
    return 0 if overall_ok else 1


def main() -> int:
    p = argparse.ArgumentParser(
        description="Phase 1 validation harness — the hard-gate criteria."
    )
    p.add_argument(
        "--criteria",
        default="all",
        help="comma-separated subset of a,b,c,d,e (default: all)",
    )
    args = p.parse_args()
    if args.criteria == "all":
        which = ["a", "b", "c", "d", "e"]
    else:
        which = [s.strip() for s in args.criteria.split(",") if s.strip()]
    return run_criteria(which)


if __name__ == "__main__":
    raise SystemExit(main())

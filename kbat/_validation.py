"""Phase 1 validation harness — the four hard-gate criteria.

The 4 criteria from spec §9 / gate [[9qP6PTTxu4B90lFQxjrHX]]:

  1.G.a  LOC                       — cleanup.kbat.yaml ≤ 80% of cleanup.py
  1.G.b  Functional equivalence    — kbat apply ≡ cleanup.py on load-bearing fields
  1.G.c  Idempotence               — second apply is a byte-exact no-op
  1.G.d  Readable diff             — kbat plan emits ≥3 op-keywords

Criterion 1.G.b is the **functional-equivalence** refinement (2026-05-17)
of the earlier sexpdata tree-equivalence approach (which itself was a
refinement of the original byte-identity goal). Spike
[[mdL7Q1hutGWX7vvrxHvD6]] established that byte-identity is impossible
because kicad-sch-api and kicad-skip use incompatible canonical text
formats. Phase 1 E then established that even sexpdata tree-equivalence
fails because kicad-sch-api 0.5.5 silently prunes KiCad-10 attributes
(`body_style`, `in_pos_files`) and orphan `lib_symbols` definitions
that kicad-skip preserves verbatim.

The refined criterion compares LOAD-BEARING facts that matter for
schematic editing (components + properties, labels + positions,
no-connects, footprints, power flags) and explicitly ignores
NON-LOAD-BEARING differences (orphan lib_symbols, per-symbol
``body_style`` / ``in_pos_files`` attributes, whitespace).

Refinement provenance: user decision via /znote:implement orchestrator
on 2026-05-17, after Phase 1 E ([[zCVaA0x_qdBZBvvS25phm]]) empirical
evidence. v0.5 should revisit strict tree-equivalence once kicad-sch-api
gains KiCad-10 support upstream.

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
PY_SCRIPT = EXPERIMENT_DIR / "cleanup.py"
KBAT_MANIFEST = EXPERIMENT_DIR / "cleanup.kbat.yaml"
PY_VENV_PYTHON = Path(
    os.environ.get("KBAT_VALIDATION_PY_INTERPRETER", sys.executable)
)

# Positional tolerance: round to 0.01 mm. The schematic stores coordinates
# at 0.0001 mm precision but cleanup.py routinely uses ``round(x, 2)`` and
# 0.05 mm comparison tolerances. 0.01 mm rounding gives a stable hashable
# key for set membership while preserving observable position semantics.
COORD_PRECISION = 2

# Per-symbol attributes that kicad-sch-api 0.5.5 does not preserve on save
# (KiCad-10 schema extensions). Both kbat and the cleanup.py output may
# differ on these; we explicitly ignore them in the functional check.
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


def _run_cleanup_py_against(work_dir: Path) -> Path:
    """Copy schematic + cleanup.py to ``work_dir``, patch path, run."""
    sch_copy = work_dir / "kicad_experiment.kicad_sch"
    py_copy = work_dir / "cleanup.py"
    shutil.copy2(SRC_SCH, sch_copy)
    shutil.copy2(PY_SCRIPT, py_copy)
    # Patch the SCH constant in the script copy
    txt = py_copy.read_text(encoding="utf-8")
    txt = txt.replace(str(SRC_SCH), str(sch_copy))
    py_copy.write_text(txt, encoding="utf-8")
    subprocess.run(
        [str(PY_VENV_PYTHON), str(py_copy)],
        check=True,
        capture_output=True,
        text=True,
        cwd=work_dir,
    )
    return sch_copy


def _run_kbat_apply_against(work_dir: Path) -> Path:
    """Copy schematic to ``work_dir``, run ``kbat apply`` against it."""
    from kbat._runner import run

    sch_copy = work_dir / "kicad_experiment.kicad_sch"
    shutil.copy2(SRC_SCH, sch_copy)
    run(KBAT_MANIFEST, schematic_override=sch_copy)
    return sch_copy


def _criterion_functional_equivalence_with_msg() -> tuple[bool, str]:
    """Refined per Phase 1 E user decision (2026-05-17): functional
    equivalence on load-bearing fields (components, labels, NCs, power
    flags, footprints) rather than full sexpdata tree-equivalence.

    Non-load-bearing differences (orphan lib_symbols, KiCad-10
    body_style/in_pos_files attributes, whitespace) are explicitly
    normalized out because kicad-sch-api 0.5.5 does not preserve them.

    Returns (ok, msg).
    """
    with tempfile.TemporaryDirectory(prefix="kbat_val_b_") as td:
        td_path = Path(td)
        py_dir = td_path / "py"
        kbat_dir = td_path / "kbat"
        py_dir.mkdir()
        kbat_dir.mkdir()
        try:
            py_out = _run_cleanup_py_against(py_dir)
        except subprocess.CalledProcessError as e:
            return False, f"cleanup.py crashed: {e.stderr or e.stdout}"
        try:
            kbat_out = _run_kbat_apply_against(kbat_dir)
        except Exception as e:  # noqa: BLE001
            return False, f"kbat apply crashed: {e!r}"
        ok, msg = functional_equivalence_check(py_out, kbat_out)
        size_msg = (
            f"py_bytes={py_out.stat().st_size} "
            f"kbat_bytes={kbat_out.stat().st_size}"
        )
        return ok, f"{msg} ({size_msg})"


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
    result = subprocess.run(
        ["uv", "run", "kbat", "plan", str(KBAT_MANIFEST)],
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


# ---- driver -----------------------------------------------------------------

CRITERIA = {
    "a": ("LOC", _criterion_loc_with_msg),
    "b": ("Functional equivalence (refined)", _criterion_functional_equivalence_with_msg),
    "c": ("Idempotence", _criterion_idempotence_with_msg),
    "d": ("Readable diff", _criterion_readable_diff_with_msg),
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
        description="Phase 1 validation harness — the 4 hard-gate criteria."
    )
    p.add_argument(
        "--criteria",
        default="all",
        help="comma-separated subset of a,b,c,d (default: all)",
    )
    args = p.parse_args()
    if args.criteria == "all":
        which = ["a", "b", "c", "d"]
    else:
        which = [s.strip() for s in args.criteria.split(",") if s.strip()]
    return run_criteria(which)


if __name__ == "__main__":
    raise SystemExit(main())

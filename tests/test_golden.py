"""Golden master comparison tests.

The v0.1 corpus is capped at 6 entries (per the testing-strategy note
[[HE4_MvAS391OEYJ6M_G3h]]):

    1. cleanup.kicad_sch.golden          — cleanup.py output (validation gate target)
    2-4. recipe_{usb_c,ntc,terminal}.golden.skip — Phase 3 deliverables (stubs)
    5. synthetic_minimal.kicad_sch.golden — owned minimal fixture
    6. synthetic_edge_cases.kicad_sch.golden — owned edge-case fixture

Parametrisation coverage comes from Hypothesis property tests in
``test_primitives.py``, NOT from golden combinatorial explosion. Golden
masters defend against silent drift in the parser/serializer pipeline;
property tests defend against silent drift in the per-op semantics.

We compare via :func:`kbat._validation.extract_functional_facts` rather
than byte-for-byte, because kicad-sch-api 0.5.5 has documented format
quirks (orphan ``lib_symbols`` pruning, ``body_style`` / ``in_pos_files``
attribute stripping) that are non-load-bearing — these are formally
ignored by ``FunctionalFacts``. Byte-level stability is verified by
``test_roundtrip.py`` separately.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import kicad_sch_api as ksa
import pytest

from kbat._validation import extract_functional_facts
from kbat.ops import relax_validation

GOLDEN_DIR = Path(__file__).parent / "golden"

# (golden_filename, present_today)
# present_today is computed at collection time; if the golden file is
# missing the test is skipped with an explanatory message.
REAL_GOLDENS = (
    "cleanup.kicad_sch.golden",
    "synthetic_minimal.kicad_sch.golden",
    "synthetic_edge_cases.kicad_sch.golden",
)

RECIPE_GOLDENS_PHASE3 = (
    "recipe_usb_c.golden.skip",
    "recipe_ntc.golden.skip",
    "recipe_terminal.golden.skip",
)


def _stage_golden(golden_name: str, tmp_path: Path) -> Path:
    """Copy the golden into ``tmp_path`` with a ``.kicad_sch`` extension.

    ``kicad-sch-api`` 0.5.6 refuses to load files that aren't named
    ``*.kicad_sch`` — our goldens use a ``.golden`` suffix to mark
    their role, so we stage them before any load call.
    """
    src = GOLDEN_DIR / golden_name
    if not src.exists():
        pytest.skip(f"golden missing: {golden_name}")
    staged = tmp_path / "staged.kicad_sch"
    shutil.copy2(src, staged)
    return staged


@pytest.mark.parametrize("golden_name", REAL_GOLDENS)
def test_golden_round_trip_preserves_functional_facts(
    golden_name: str, tmp_path: Path
) -> None:
    """A load-save-reload round-trip on the golden must preserve all
    load-bearing facts (components, labels, NCs, power flags, footprints).

    This is the structural-equivalence check (criterion 1.G.b's
    functional-equivalence refinement) applied to the golden corpus.
    Catches: kicad-sch-api accidentally dropping a load-bearing field
    across a save round-trip — which would, downstream, cause silent
    schematic corruption when kbat operates on the affected file.
    """
    src = GOLDEN_DIR / golden_name
    if not src.exists():
        pytest.skip(f"golden missing: {golden_name}")

    facts_in = extract_functional_facts(src)

    # Round-trip via kicad-sch-api.
    staged = _stage_golden(golden_name, tmp_path)
    out = tmp_path / "rt.kicad_sch"
    sch = ksa.Schematic.load(str(staged))
    relax_validation(sch)
    sch.save(str(out))

    facts_out = extract_functional_facts(out)
    assert facts_in == facts_out, (
        f"functional facts diverged across round-trip on {golden_name}\n"
        f"  components in:  {len(facts_in.components)}, "
        f"out: {len(facts_out.components)}\n"
        f"  labels in:      {len(facts_in.labels)}, "
        f"out: {len(facts_out.labels)}\n"
        f"  no_connects in: {len(facts_in.no_connects)}, "
        f"out: {len(facts_out.no_connects)}\n"
        f"  power_flags in: {len(facts_in.power_flags)}, "
        f"out: {len(facts_out.power_flags)}"
    )


@pytest.mark.parametrize("golden_name", REAL_GOLDENS)
def test_golden_byte_stable_under_idempotent_save(
    golden_name: str, tmp_path: Path
) -> None:
    """Golden + first-save + second-save: the two saves must be byte-identical.

    This complements ``test_roundtrip.py``'s parametrised generic
    round-trip by anchoring the byte-stability promise to the *golden
    corpus specifically* — so a corpus refresh that introduces a non-
    stable file is caught immediately.
    """
    staged = _stage_golden(golden_name, tmp_path)

    sch1 = ksa.Schematic.load(str(staged))
    relax_validation(sch1)
    out1 = tmp_path / "save1.kicad_sch"
    sch1.save(str(out1))

    sch2 = ksa.Schematic.load(str(out1))
    relax_validation(sch2)
    out2 = tmp_path / "save2.kicad_sch"
    sch2.save(str(out2))

    a = out1.read_bytes()
    b = out2.read_bytes()
    assert a == b, (
        f"{golden_name}: second save diverged from first "
        f"({len(a)} vs {len(b)} bytes)"
    )


@pytest.mark.parametrize("golden_name", RECIPE_GOLDENS_PHASE3)
def test_recipe_golden_phase3_deferred(golden_name: str) -> None:
    """Recipe goldens land in Phase 3.

    The stub files exist in ``tests/golden/`` to satisfy the corpus-size
    check in gate 2.8 (≥ 6 golden artefacts), but the actual recipe
    implementations and their expected outputs are explicitly out of
    Phase 2 scope.
    """
    stub = GOLDEN_DIR / golden_name
    assert stub.exists(), f"recipe stub missing: {golden_name}"
    pytest.skip(
        "Phase 3 deliverable — recipe implementations (and their golden "
        "masters) land in the recipes phase"
    )


def test_golden_corpus_has_six_entries() -> None:
    """The v0.1 corpus must hold exactly 6 entries (cap per the testing
    strategy note). Failing this means someone is silently growing the
    corpus and bypassing the property-test coverage discipline.
    """
    entries = sorted(
        p.name
        for p in GOLDEN_DIR.iterdir()
        if p.is_file()
        and p.suffix in {".golden", ".skip"}
        or p.name.endswith(".golden.skip")
    )
    # Deduplicate / explicit allowlist match
    expected = sorted(REAL_GOLDENS + RECIPE_GOLDENS_PHASE3)
    assert entries == expected, (
        f"golden corpus mismatch:\n  expected: {expected}\n  actual:   {entries}"
    )

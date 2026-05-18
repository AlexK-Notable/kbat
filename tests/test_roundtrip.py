"""Round-trip harness: parse → save → reparse → save → byte-diff.

The contract under test is the second-save stability property: once
kicad-sch-api has normalised the formatting on the first save, any
subsequent identity-save round-trip MUST produce byte-identical output.
This is the backend-agnostic byte assertion called for by the testing
strategy [[HE4_MvAS391OEYJ6M_G3h]] — kicad-sch-api, kicad-python, or any
future backend, the same test applies: bytes in, bytes out, must match.

NOTE on first-save vs original-bytes: kicad-sch-api 0.5.6 emits a
slightly different canonical form than upstream KiCad's eeschema (e.g.,
``generator_version "9.0"`` vs ``"10.0"``), so a parse-of-the-original
followed by save will NOT match the original. The contract is on the
*idempotent* fixed point of save, not on round-trip-to-original.

Mutation round-trip tests live in ``tests/test_primitives.py`` (which
adds idempotence via Hypothesis). This file covers identity round-trip
only — the parser/serializer must be stable on its own output.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import kicad_sch_api as ksa
import pytest

from kbat.ops import relax_validation
from tests.conftest import REAL_SCHEMATIC, SYNTHETIC_GOLDENS


def _stage_to_kicad_sch_extension(src: Path, tmp_path: Path) -> Path:
    """Copy ``src`` into ``tmp_path`` with a ``.kicad_sch`` extension.

    kicad-sch-api 0.5.6 refuses to load files whose extension isn't
    ``.kicad_sch`` (raises ``ValidationError: Not a KiCAD schematic
    file``). Our golden fixtures use a ``.golden`` suffix to make their
    role obvious — so every test must stage them first.
    """
    dst = tmp_path / "staged.kicad_sch"
    shutil.copy2(src, dst)
    return dst


def _identity_save(src: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Load src, save (out1), load out1, save (out2). Return (out1, out2)."""
    staged = _stage_to_kicad_sch_extension(src, tmp_path)
    sch1 = ksa.Schematic.load(str(staged))
    relax_validation(sch1)
    out1 = tmp_path / "first_save.kicad_sch"
    sch1.save(str(out1))

    sch2 = ksa.Schematic.load(str(out1))
    relax_validation(sch2)
    out2 = tmp_path / "second_save.kicad_sch"
    sch2.save(str(out2))

    return out1, out2


def _fixture_id(p: Path) -> str:
    """Compact human-readable id for parametrize."""
    return p.name


@pytest.mark.parametrize(
    "schematic_source",
    [REAL_SCHEMATIC, *SYNTHETIC_GOLDENS],
    ids=_fixture_id,
)
def test_second_save_is_byte_identical_to_first(
    schematic_source: Path, tmp_path: Path
) -> None:
    """After kicad-sch-api normalises formatting on the first save, the
    second identity round-trip MUST be byte-identical to the first.

    Catches: parser/serializer non-determinism (UUID regeneration on
    load, dict-order-sensitive serialization, float precision drift,
    whitespace churn between successive saves).
    """
    if not schematic_source.exists():
        pytest.skip(f"fixture missing: {schematic_source}")

    out1, out2 = _identity_save(schematic_source, tmp_path)

    a = out1.read_bytes()
    b = out2.read_bytes()
    assert a == b, (
        f"second save diverged from first: {len(a)} vs {len(b)} bytes\n"
        f"first 200 bytes diff at offset "
        f"{next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), 'eof')}"
    )


@pytest.mark.parametrize(
    "schematic_source",
    [REAL_SCHEMATIC, *SYNTHETIC_GOLDENS],
    ids=_fixture_id,
)
def test_load_save_load_preserves_component_count(
    schematic_source: Path, tmp_path: Path
) -> None:
    """Identity round-trip must not lose or duplicate components.

    Stronger-than-byte assertion: even if kicad-sch-api silently
    canonicalises the output, the component count is observable state
    that the user relies on. A drop in count is a parser bug.
    """
    if not schematic_source.exists():
        pytest.skip(f"fixture missing: {schematic_source}")

    staged = _stage_to_kicad_sch_extension(schematic_source, tmp_path)
    sch1 = ksa.Schematic.load(str(staged))
    n_before = sum(1 for _ in sch1.components)

    relax_validation(sch1)
    out = tmp_path / "rt.kicad_sch"
    sch1.save(str(out))

    sch2 = ksa.Schematic.load(str(out))
    n_after = sum(1 for _ in sch2.components)

    assert n_after == n_before, (
        f"component count changed across round-trip: {n_before} -> {n_after}"
    )


@pytest.mark.parametrize(
    "schematic_source",
    [REAL_SCHEMATIC, *SYNTHETIC_GOLDENS],
    ids=_fixture_id,
)
def test_load_save_load_preserves_label_set(
    schematic_source: Path, tmp_path: Path
) -> None:
    """Identity round-trip must preserve the exact set of label texts.

    Catches: silent label-class promotion/demotion (e.g.,
    local-label → hierarchical-label) during save, or whitespace-
    normalisation eating a label's text.
    """
    if not schematic_source.exists():
        pytest.skip(f"fixture missing: {schematic_source}")

    staged = _stage_to_kicad_sch_extension(schematic_source, tmp_path)
    sch1 = ksa.Schematic.load(str(staged))
    labels_before = sorted(lab.text for lab in sch1.labels)

    relax_validation(sch1)
    out = tmp_path / "rt.kicad_sch"
    sch1.save(str(out))

    sch2 = ksa.Schematic.load(str(out))
    labels_after = sorted(lab.text for lab in sch2.labels)

    assert labels_after == labels_before, (
        f"label set changed across round-trip:\n"
        f"  before: {labels_before}\n  after:  {labels_after}"
    )

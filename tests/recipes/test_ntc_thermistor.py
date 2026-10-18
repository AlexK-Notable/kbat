"""Tests for the ``ntc_thermistor`` recipe (Phase 2.6 — v0.5 spike).

These tests verify the published recipe contract — ``NtcThermistorParams``
(Pydantic) + ``apply_ntc_thermistor(path, params) -> ApplyResult`` —
without depending on the recipe's internal primitive choreography. They
are written in parallel with the feature-implementer against the
documented interface; the recipe module may not yet exist when these
tests are first collected, in which case the import error is the
expected (transient) signal — tests go green once the recipe lands.

Mock policy: no mocking. The recipe acts on a real ``.kicad_sch`` file
via :mod:`kbat.backend` (akcli); the tests give it a tmp-path copy of a
real synthetic fixture (`tests/fixtures/recipes/ntc_minimal.kicad_sch`).

Idempotence approach: a converged recipe compiles to no ops, so the
second apply never writes and the file is byte-identical. The test also
compares the load-bearing FunctionalFacts from
:func:`kbat._validation.extract_functional_facts`. (Under kicad-sch-api,
Phase 2.1 finding [[VS1uMQqWcgddDeIaCe2hT]], fresh UUIDs on every save
made byte-identity unreachable, so only the facts were compared.)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from kbat._validation import FunctionalFacts, extract_functional_facts
from kbat.recipes.sensors.ntc_thermistor import (
    CHIP_TABLE,
    NtcThermistorParams,
    apply_ntc_thermistor,
)
from kbat.schema.v1alpha1 import ApplyResult

# ---- canonical paths --------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "recipes"
NTC_MINIMAL = FIXTURE_DIR / "ntc_minimal.kicad_sch"

# A chip that the contract guarantees ships in ``CHIP_TABLE``. Used by
# the smoke/idempotence tests where any valid chip will do.
SAMPLE_CHIP = "NCP18XH103J03RB"


# ---- shared helpers ---------------------------------------------------------


def _copy_ntc_minimal(tmp_path: Path) -> Path:
    """Return a per-test writable copy of the NTC minimal fixture.

    The fixture has ``TH1`` (thermistor placeholder) and ``R_TH1`` (pullup
    resistor placeholder) pre-placed, which is the recipe's documented
    pre-condition (it configures existing components rather than minting
    new ones — the recipe spike intentionally avoids an ``add_component``
    primitive that doesn't exist in the v0.1.1 toolbox).
    """
    if not NTC_MINIMAL.exists():
        pytest.skip(f"NTC fixture missing: {NTC_MINIMAL}")
    dst = tmp_path / "ntc_minimal.kicad_sch"
    shutil.copy2(NTC_MINIMAL, dst)
    return dst


# ---- schema validation tests ------------------------------------------------


def test_params_accepts_valid_chip() -> None:
    """``NtcThermistorParams`` accepts a chip from ``CHIP_TABLE`` and
    populates documented defaults.

    Catches: a regression that drops the ``TH1`` / ``4.7`` defaults from
    the public Params contract, breaking every recipe call-site that
    relied on omitting these fields.
    """
    params = NtcThermistorParams(chip=SAMPLE_CHIP)
    assert params.reference == "TH1"
    assert params.pullup_k == 4.7


def test_params_rejects_extras() -> None:
    """Pydantic ``extra="forbid"`` rejects unknown fields.

    Catches: a typo in a caller's ``apply_ntc_thermistor`` invocation
    (``pulldup_k=4.7``) silently no-op'ing instead of raising — the
    same OSTH protection applied to manifest schemas, extended to the
    recipe input boundary per [[uhjfJqjIT9oRjeBp5UpG6]] §3.4.
    """
    with pytest.raises(ValidationError):
        NtcThermistorParams(chip=SAMPLE_CHIP, bogus_field="x")  # type: ignore[call-arg]


def test_apply_rejects_unknown_chip(tmp_path: Path) -> None:
    """``apply_ntc_thermistor`` raises ``ValueError`` on an unknown chip.

    Pydantic accepts arbitrary strings for the ``chip`` field — the
    enum-style lookup happens at apply time against ``CHIP_TABLE``.

    Catches: a recipe that silently substitutes a "default" chip when
    the requested MPN is unknown (data-quality footgun).
    """
    sch_path = _copy_ntc_minimal(tmp_path)
    params = NtcThermistorParams(chip="UNOBTANIUM-9000")
    with pytest.raises(ValueError, match="(?i)unknown chip"):
        apply_ntc_thermistor(sch_path, params)


# ---- smoke test -------------------------------------------------------------


def test_apply_succeeds_on_minimal_fixture(tmp_path: Path) -> None:
    """The recipe applies cleanly to a schematic that pre-places ``TH1``
    and ``R_TH1``.

    The recipe's documented contract is that the two components exist;
    it configures them (value, footprint, etc.) rather than creating
    them. This is the headline velocity datapoint for the spike.

    Catches: a recipe that errors out on its own canonical happy-path
    input — the most basic regression.
    """
    sch_path = _copy_ntc_minimal(tmp_path)
    params = NtcThermistorParams(chip=SAMPLE_CHIP)

    result = apply_ntc_thermistor(sch_path, params)

    assert isinstance(result, ApplyResult), (
        f"recipe must return ApplyResult, got {type(result).__name__}"
    )
    assert result.applied >= 1, (
        f"smoke fixture should produce at least one applied op, "
        f"got applied={result.applied} skipped={result.skipped} "
        f"errors={result.errors}"
    )
    assert not result.errors, f"smoke fixture errored: {result.errors}"


# ---- idempotence test (functional, not byte-strict) ------------------------


def test_apply_is_functionally_idempotent(tmp_path: Path) -> None:
    """Re-applying the same recipe leaves the file byte-identical.

    The second apply compiles to no ops against the converged file, so
    it does not write at all. Components, labels, no-connect positions,
    power-flag positions and footprint assignments are also compared, so
    a failure says which fact diverged.

    Catches: a recipe that re-runs op-bodies on already-converged state
    (duplicating NC markers, re-incrementing #FLGn references, etc.) —
    the highest-value bug class for recipe authors who lose track of
    primitive-level idempotence guarantees.
    """
    sch_path = _copy_ntc_minimal(tmp_path)
    params = NtcThermistorParams(chip=SAMPLE_CHIP)

    first_result = apply_ntc_thermistor(sch_path, params)
    assert not first_result.errors, (
        f"first apply errored, idempotence test cannot proceed: "
        f"{first_result.errors}"
    )
    facts_after_first = extract_functional_facts(sch_path)
    bytes_after_first = sch_path.read_bytes()

    second_result = apply_ntc_thermistor(sch_path, params)
    assert not second_result.errors, (
        f"second apply errored: {second_result.errors}"
    )
    facts_after_second = extract_functional_facts(sch_path)

    assert isinstance(facts_after_first, FunctionalFacts)
    assert facts_after_first == facts_after_second, (
        "functional facts diverged across two applies — recipe is "
        "not idempotent on the load-bearing fields"
    )
    assert sch_path.read_bytes() == bytes_after_first, (
        "second apply rewrote the converged schematic"
    )


# ---- chip-table coverage (parametric) --------------------------------------


@pytest.mark.parametrize("chip_mpn", sorted(CHIP_TABLE.keys()))
def test_each_chip_in_table_applies_cleanly(
    tmp_path: Path, chip_mpn: str
) -> None:
    """Every chip in ``CHIP_TABLE`` produces a clean apply on the smoke
    fixture.

    Catches: a ``CHIP_TABLE`` entry that's syntactically valid (passes
    Pydantic) but breaks the recipe at runtime (e.g., missing a required
    sub-field the apply path indexes into). Without this test, a new
    chip added to the table could ship broken to v0.5 recipe consumers.

    Empty ``CHIP_TABLE`` would yield zero parametrised cases, which
    pytest treats as a non-failure — but the smoke test already asserts
    the contract recipe accepts ``NCP18XH103J03RB``, so an empty table
    would be caught upstream by ``test_apply_succeeds_on_minimal_fixture``.
    """
    sch_path = _copy_ntc_minimal(tmp_path)
    params = NtcThermistorParams(chip=chip_mpn)

    result = apply_ntc_thermistor(sch_path, params)

    assert not result.errors, (
        f"chip {chip_mpn} produced errors on apply: {result.errors}"
    )

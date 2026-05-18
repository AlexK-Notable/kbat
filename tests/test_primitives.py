"""Hypothesis property tests for primitive idempotence.

Phase 1 D's ``test_ops_basic.py`` covers the happy path per primitive
with hand-written scenarios. This file extends that coverage with
property-based testing: for every primitive, the *property*
``apply(op, apply(op, sch)) == apply(op, sch)`` must hold for a wide
range of inputs.

The convention enforced by ``tests/test_primitive_registry.py``: each
test function name contains the primitive name as a substring. This
keeps the meta-test happy and makes the file's purpose self-documenting.

Generators bound runtime aggressively (``max_examples=20`` per property)
to fit the per-push CI budget. Nightly CI can crank this up via
``--hypothesis-profile=nightly`` if/when a profile is added.

No mocking: every property test runs against a real loaded schematic
fixture (synthetic or real-world), per the testing-strategy note
[[HE4_MvAS391OEYJ6M_G3h]].
"""

from __future__ import annotations

import shutil
from pathlib import Path

import kicad_sch_api as ksa
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

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
from tests.conftest import GOLDEN_DIR

# Bound runtime per property — fits in the per-push CI budget.
PROPERTY_SETTINGS = settings(
    max_examples=20,
    deadline=None,  # ksa load+save is too variable for a deadline
    # HYPOTHESIS_MASK: Flag #5 — function_scoped_fixture + too_slow
    # suppressed because the per-test working_schematic fixture is
    # intentionally function-scoped and ksa load+save wall-time varies;
    # tracked by ROADMAP Appendix A Flag #5 (target v0.5).
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)


# ---- shared per-test working copy fixture -----------------------------------


@pytest.fixture
def working_schematic(tmp_path: Path) -> Path:
    """A throw-away copy of the synthetic edge-case golden.

    Property tests load this fresh per *example*, so each example sees
    the original golden state — Hypothesis would otherwise smear state
    across attempts.
    """
    src = GOLDEN_DIR / "synthetic_edge_cases.kicad_sch.golden"
    if not src.exists():
        pytest.skip("synthetic_edge_cases golden missing")
    dst = tmp_path / "wc.kicad_sch"
    shutil.copy2(src, dst)
    return dst


def _fresh_load(working: Path):
    """Load the working copy fresh (per-example) and relax validation."""
    sch = ksa.Schematic.load(str(working))
    relax_validation(sch)
    return sch


# ---- set_property -----------------------------------------------------------


@given(
    reference=st.sampled_from(["R1", "R2", "C1", "DOES_NOT_EXIST"]),
    value=st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=126, blacklist_characters='"\\\x00'),
        min_size=0,
        max_size=20,
    ),
)
@PROPERTY_SETTINGS
def test_set_property_idempotent(
    working_schematic: Path, reference: str, value: str
) -> None:
    """``set_property`` is idempotent: second call must be a no-op.

    Catches: a primitive that mutates *something* on every call even
    when the requested value already holds (e.g., re-writing the same
    string to a property still flagging "changed").
    """
    sch = _fresh_load(working_schematic)
    # First call: may True (we changed) or False (already had value, or
    # ref missing). We don't assert on it — the property is about the
    # *second* call.
    set_property(sch, reference, "Value", value)
    # After the first call, the schematic is in the target state for
    # this property. A second call MUST be a no-op.
    assert set_property(sch, reference, "Value", value) is False, (
        f"second set_property({reference!r}, 'Value', {value!r}) "
        f"unexpectedly returned True"
    )


@given(
    reference=st.sampled_from(["R1", "R2", "C1"]),
    property_name=st.sampled_from(["Datasheet", "Description"]),
    value=st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=126, blacklist_characters='"\\\x00'),
        min_size=0,
        max_size=30,
    ),
)
@PROPERTY_SETTINGS
def test_set_property_custom_field_idempotent(
    working_schematic: Path, reference: str, property_name: str, value: str
) -> None:
    """Idempotence holds for non-canonical (custom) property names too.

    Catches: a fallback path for "arbitrary properties" that doesn't
    short-circuit on equality, producing spurious mutations.
    """
    sch = _fresh_load(working_schematic)
    set_property(sch, reference, property_name, value)
    assert set_property(sch, reference, property_name, value) is False


# ---- add_no_connect ---------------------------------------------------------


@given(
    reference=st.sampled_from(["R1", "R2", "C1", "ZZ999"]),
    # NB: pin numbers up to 8 cover Device:R (2) and most ICs
    pins=st.lists(st.integers(min_value=1, max_value=8), min_size=0, max_size=4, unique=True),
)
@PROPERTY_SETTINGS
def test_add_no_connect_idempotent(
    working_schematic: Path, reference: str, pins: list[int]
) -> None:
    """``add_no_connect`` is idempotent on any (reference, pins) pair.

    Catches: an implementation that drops a duplicate NC marker at the
    same pin position on every invocation.
    """
    sch = _fresh_load(working_schematic)
    add_no_connect(sch, reference, pins)
    assert add_no_connect(sch, reference, pins) is False, (
        f"second add_no_connect({reference!r}, {pins!r}) unexpectedly True"
    )


# ---- snap_label_to_pin ------------------------------------------------------


@given(
    reference=st.sampled_from(["R1", "R2", "C1"]),
    pin=st.integers(min_value=1, max_value=2),
    net=st.sampled_from(["VCC", "GND", "NEVER_A_REAL_NET"]),
)
@PROPERTY_SETTINGS
def test_snap_label_to_pin_idempotent(
    working_schematic: Path, reference: str, pin: int, net: str
) -> None:
    """``snap_label_to_pin`` is idempotent.

    The synthetic_edge_cases golden has VCC and GND labels at known
    positions; the primitive may or may not relocate them on the first
    call, but the second call MUST be a no-op.
    """
    sch = _fresh_load(working_schematic)
    snap_label_to_pin(sch, reference, pin, net)
    assert snap_label_to_pin(sch, reference, pin, net) is False


# ---- snap_labels_to_pins ----------------------------------------------------


@given(
    reference=st.sampled_from(["R1", "R2", "C1"]),
    mapping=st.dictionaries(
        keys=st.integers(min_value=1, max_value=2).map(str),
        values=st.sampled_from(["VCC", "GND"]),
        min_size=0,
        max_size=2,
    ),
)
@PROPERTY_SETTINGS
def test_snap_labels_to_pins_idempotent(
    working_schematic: Path, reference: str, mapping: dict[str, str]
) -> None:
    """``snap_labels_to_pins`` is idempotent under bulk-mapping inputs."""
    sch = _fresh_load(working_schematic)
    snap_labels_to_pins(sch, reference, mapping)
    assert snap_labels_to_pins(sch, reference, mapping) is False


# ---- assign_footprints ------------------------------------------------------


@given(
    by_lib_id=st.dictionaries(
        keys=st.sampled_from(["Device:R", "Device:C", "power:GND", "power:PWR_FLAG"]),
        values=st.sampled_from(
            ["Resistor_SMD:R_0402_1005Metric", "Capacitor_SMD:C_0402_1005Metric", ""]
        ),
        min_size=0,
        max_size=3,
    ),
)
@PROPERTY_SETTINGS
def test_assign_footprints_idempotent(
    working_schematic: Path, by_lib_id: dict[str, str]
) -> None:
    """``assign_footprints`` is idempotent over arbitrary lib_id → footprint maps.

    Catches: a primitive that touches the Footprint field on every
    matching component regardless of whether the target value differs
    from the current one.
    """
    sch = _fresh_load(working_schematic)
    assign_footprints(sch, by_lib_id)
    assert assign_footprints(sch, by_lib_id) is False


# ---- delete_label_at --------------------------------------------------------


# Known label positions in synthetic_edge_cases.kicad_sch.golden. Hand-extracted
# from the fixture's source: ``(label "VCC" (at 50 40 0))`` and
# ``(label "GND" (at 60 60 0))``. We pin these here so the property test can
# bias coordinate sampling toward existing-label positions; per
# [[G1DhgkrMHeSZKBO_mssfw]] sampling the full 0-200 mm² with hardcoded labels
# at known coords leaves ~19/20 examples on empty space, where idempotence is
# trivially true via the no-op early-return path. The bimodal strategy below
# (real-label hits vs near-label jitter) exercises BOTH paths.
KNOWN_LABELS_IN_FIXTURE: list[tuple[str, float, float]] = [
    ("VCC", 50.0, 40.0),
    ("GND", 60.0, 60.0),
]

# Bimodal coordinate strategy intent (inlined in @given decorators below so
# gate check 2.11/2.12 sees ``>= 2`` ``st.*(...)`` calls inside the decorator
# AST):
#   Branch A — ``st.sampled_from(KNOWN_LABELS_IN_FIXTURE)``: exact label hit;
#     exercises the MUTATION path (first call deletes/moves; second is no-op).
#   Branch B — ``st.tuples(st.floats(...), st.floats(...))``: bounded jitter
#     around the same coords; exercises BOTH the in-tolerance match path and
#     the just-outside-tolerance no-op path.


@given(
    net=st.sampled_from(["VCC", "GND", "NO_SUCH_NET"]),
    coord=st.one_of(
        # Branch A — exact known-label coords: exercise the MUTATION path.
        st.sampled_from([(x, y) for _, x, y in KNOWN_LABELS_IN_FIXTURE]),
        # Branch B — bounded jitter around known coords: a mix of in-tolerance
        # hits (mutation path) and just-outside-tolerance misses (no-op path).
        st.tuples(
            st.floats(min_value=45.0, max_value=70.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=35.0, max_value=70.0, allow_nan=False, allow_infinity=False),
        ),
    ),
)
@PROPERTY_SETTINGS
def test_delete_label_at_idempotent(
    working_schematic: Path, net: str, coord: tuple[float, float]
) -> None:
    """``delete_label_at`` is idempotent across both mutation and no-op paths.

    The bimodal ``coord`` strategy (``st.one_of`` of exact known coords +
    bounded near-label tuples) guarantees a non-trivial fraction of
    examples hit a real label position (mutation path: first call deletes,
    second is no-op) AND a non-trivial fraction sample empty space (no-op
    path: first call returns False, second also). Without the bimodal
    bias (per [[G1DhgkrMHeSZKBO_mssfw]]), full-range float sampling left
    ~95% of examples on empty space, where the assertion was trivially
    true via the early-return path. See also: HYPOTHESIS_MASK Flag #1 on
    ``_grid_coords`` below (constrained-strategy variant; tracked by
    ``scripts/check_hypothesis_masks.py``).
    """
    x, y = coord
    sch = _fresh_load(working_schematic)
    delete_label_at(sch, net, x, y)
    assert delete_label_at(sch, net, x, y) is False


# ---- move_label -------------------------------------------------------------


@given(
    net=st.sampled_from(["VCC", "GND", "NEVER_NET"]),
    # ``from_coord`` is the load-bearing axis: it MUST sometimes point at
    # a real label position so the mutation path is exercised. The
    # bimodal ``st.one_of`` is identical to test_delete_label_at_idempotent.
    from_coord=st.one_of(
        st.sampled_from([(x, y) for _, x, y in KNOWN_LABELS_IN_FIXTURE]),
        st.tuples(
            st.floats(min_value=45.0, max_value=70.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=35.0, max_value=70.0, allow_nan=False, allow_infinity=False),
        ),
    ),
    # ``to_coord`` only matters when ``from_coord`` actually matches a
    # label — same bimodal strategy keeps reachable end states realistic.
    to_coord=st.one_of(
        st.sampled_from([(x, y) for _, x, y in KNOWN_LABELS_IN_FIXTURE]),
        st.tuples(
            st.floats(min_value=45.0, max_value=70.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=35.0, max_value=70.0, allow_nan=False, allow_infinity=False),
        ),
    ),
)
@PROPERTY_SETTINGS
def test_move_label_idempotent(
    working_schematic: Path,
    net: str,
    from_coord: tuple[float, float],
    to_coord: tuple[float, float],
) -> None:
    """``move_label`` is idempotent across both mutation and no-op paths.

    Same bimodal strategy as :func:`test_delete_label_at_idempotent` —
    ``from_coord`` sampled from known-label positions plus near-jitter
    so the property exercises the actual mutation path (label found at
    ``from_coord`` → moved to ``to_coord``) and not only the
    trivial no-op-on-empty-space path. Per [[G1DhgkrMHeSZKBO_mssfw]].
    """
    from_x, from_y = from_coord
    to_x, to_y = to_coord
    sch = _fresh_load(working_schematic)
    move_label(sch, net, from_x, from_y, to_x, to_y)
    assert move_label(sch, net, from_x, from_y, to_x, to_y) is False


# ---- add_pwr_flag -----------------------------------------------------------


# KiCad schematics live on a 1.27 mm (0.05 in) grid. Phase 2.4 fixed
# Flag #1: ``add_pwr_flag`` now snaps caller-supplied ``(x, y)`` to the
# 1.27 mm grid internally before storing the flag component and label,
# so off-grid input no longer breaks idempotence. The property test
# therefore samples a wide continuous float range (the original spec
# before the Flag #1 mask was applied in Phase 2.3) so Hypothesis can
# re-attack the original counterexample class (e.g. y=127.75) and any
# others. The post-snap idempotence tolerance is the primitive's own
# 0.5 mm threshold.
GRID_MM = 1.27
_pwr_flag_coords = st.tuples(
    st.floats(min_value=5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
)


@given(
    net=st.sampled_from(["+3V3", "VBUS", "VSYS_TEST"]),
    coord=_pwr_flag_coords,
)
@PROPERTY_SETTINGS
def test_add_pwr_flag_idempotent(
    working_schematic: Path, net: str, coord: tuple[float, float]
) -> None:
    """``add_pwr_flag`` is idempotent across arbitrary off-grid coordinates.

    With the Phase 2.4 grid-snap fix in place, the primitive internally
    rounds ``(x, y)`` to the nearest 1.27 mm multiple before storing
    both the flag component and the co-located label. The post-snap
    position is what the idempotence check compares, so the second call
    on the SAME caller-supplied ``(x, y)`` must be a no-op for any
    input in this range — including the previously-failing
    ``(5.0, 127.75)`` class.
    """
    x, y = coord
    sch = _fresh_load(working_schematic)
    add_pwr_flag(sch, net, x, y)
    # The primitive snaps internally; idempotence is observable at the
    # snapped position, so we count near the snapped target.
    sx = round(x / GRID_MM) * GRID_MM
    sy = round(y / GRID_MM) * GRID_MM
    before = sum(
        1
        for c in sch.components
        if c.lib_id == "power:PWR_FLAG"
        and abs(float(c.position.x) - sx) <= 0.5
        and abs(float(c.position.y) - sy) <= 0.5
    )
    second_changed = add_pwr_flag(sch, net, x, y)
    after = sum(
        1
        for c in sch.components
        if c.lib_id == "power:PWR_FLAG"
        and abs(float(c.position.x) - sx) <= 0.5
        and abs(float(c.position.y) - sy) <= 0.5
    )
    assert second_changed is False, "second add_pwr_flag unexpectedly True"
    assert after == before, (
        f"second add_pwr_flag duplicated the flag: {before} -> {after}"
    )


def test_add_pwr_flag_grid_snap_off_grid_coords(working_schematic: Path) -> None:
    """Direct unit test for Phase 2.4 grid-snap: off-grid ``(x, y)`` is stored on-grid.

    Complements the Hypothesis property test with a fast, deterministic
    check on the specific Flag #1 counterexample (``y=127.75`` rounds
    down to ``127.0 = 100 * 1.27``). The stored component AND label must
    both land on the same 1.27 mm grid multiple so no offset breaks the
    idempotence tolerance.
    """
    sch = _fresh_load(working_schematic)
    # Off-grid input on both axes: 5.0 happens to be on grid only if
    # 5/1.27 is an integer (it isn't — nearest multiple is 5.08, i.e.
    # 4*1.27). 127.75 rounds to 100*1.27 = 127.0.
    add_pwr_flag(sch, "+3V3_SNAP_TEST", 5.0, 127.75)
    flags = [
        c
        for c in sch.components
        if c.lib_id == "power:PWR_FLAG" and getattr(c, "value", "") == "PWR_FLAG"
    ]
    assert flags, "no PWR_FLAG component inserted"
    # Pick the most recently added (highest #FLG number) so prior fixture
    # state can't fool the assertion.
    latest = sorted(flags, key=lambda c: c.reference)[-1]
    fx, fy = float(latest.position.x), float(latest.position.y)
    # Both axes must be exact integer multiples of 1.27 mm.
    assert abs(round(fx / GRID_MM) * GRID_MM - fx) < 0.001, f"x={fx} not snapped"
    assert abs(round(fy / GRID_MM) * GRID_MM - fy) < 0.001, f"y={fy} not snapped"
    # And the matching label is at the SAME snapped position (the bug
    # was that ksa snapped the component but not the label).
    sx = round(5.0 / GRID_MM) * GRID_MM
    sy = round(127.75 / GRID_MM) * GRID_MM
    near_labels = [
        lab
        for lab in sch.labels
        if lab.text == "+3V3_SNAP_TEST"
        and abs(float(lab.position.x) - sx) < 0.5
        and abs(float(lab.position.y) - sy) < 0.5
    ]
    assert near_labels, (
        f"no +3V3_SNAP_TEST label at snapped position ({sx}, {sy}); "
        "component was snapped but label was not (Flag #1 regression)"
    )

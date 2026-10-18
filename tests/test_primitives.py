"""Hypothesis property tests for primitive idempotence.

Phase 1 D's ``test_ops_basic.py`` covers the happy path per primitive
with hand-written, file-level scenarios. This file extends that coverage
with property-based testing: for every primitive, compiling the same op
twice against the same state must emit nothing the second time —
``compile(op, compile(op, state)) == []`` — for a wide range of inputs.

Primitives compile against an in-memory :class:`~kbat.backend.BoardState`
read once (through akcli) from the synthetic edge-case golden, so each
example is a pure computation: no file, no subprocess.

The convention enforced by ``tests/test_primitive_registry.py``: each
test function name contains the primitive name as a substring. This
keeps the meta-test happy and makes the file's purpose self-documenting.

Generators bound runtime aggressively (``max_examples=20`` per property)
to fit the per-push CI budget. Nightly CI can crank this up via
``--hypothesis-profile=nightly`` if/when a profile is added.

No mocking: every property test runs against state read from a real
schematic fixture, per the testing-strategy note [[HE4_MvAS391OEYJ6M_G3h]].
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

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
from tests.conftest import GOLDEN_DIR, fresh_state

# Bound runtime per property — fits in the per-push CI budget. In-memory
# compilation is fast and deterministic, so no deadline or health-check
# suppression is needed.
PROPERTY_SETTINGS = settings(max_examples=20, deadline=None)

EDGE_CASES = GOLDEN_DIR / "synthetic_edge_cases.kicad_sch.golden"


def _state():
    return fresh_state(EDGE_CASES)


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
def test_set_property_idempotent(reference: str, value: str) -> None:
    """``set_property`` is idempotent: second compile must emit nothing.

    Catches: a primitive that emits an op on every call even when the
    requested value already holds.
    """
    state = _state()
    set_property(state, reference, "Value", value)
    assert set_property(state, reference, "Value", value) == [], (
        f"second set_property({reference!r}, 'Value', {value!r}) emitted ops"
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
    reference: str, property_name: str, value: str
) -> None:
    """Idempotence holds for non-canonical (custom) property names too.

    Catches: a fallback path for "arbitrary properties" that doesn't
    short-circuit on equality, producing spurious ops.
    """
    state = _state()
    set_property(state, reference, property_name, value)
    assert set_property(state, reference, property_name, value) == []


# ---- add_no_connect ---------------------------------------------------------


@given(
    reference=st.sampled_from(["R1", "R2", "C1", "ZZ999"]),
    # NB: pin numbers up to 8 cover Device:R (2) and most ICs
    pins=st.lists(st.integers(min_value=1, max_value=8), min_size=0, max_size=4, unique=True),
)
@PROPERTY_SETTINGS
def test_add_no_connect_idempotent(reference: str, pins: list[int]) -> None:
    """``add_no_connect`` is idempotent on any (reference, pins) pair.

    Catches: an implementation that emits a duplicate NC marker at the
    same pin position on every invocation.
    """
    state = _state()
    add_no_connect(state, reference, pins)
    assert add_no_connect(state, reference, pins) == [], (
        f"second add_no_connect({reference!r}, {pins!r}) emitted ops"
    )


# ---- snap_label_to_pin ------------------------------------------------------


@given(
    reference=st.sampled_from(["R1", "R2", "C1"]),
    pin=st.integers(min_value=1, max_value=2),
    net=st.sampled_from(["VCC", "GND", "NEVER_A_REAL_NET"]),
)
@PROPERTY_SETTINGS
def test_snap_label_to_pin_idempotent(reference: str, pin: int, net: str) -> None:
    """``snap_label_to_pin`` is idempotent.

    The synthetic_edge_cases golden has VCC and GND labels at known
    positions; the primitive may or may not relocate them on the first
    call, but the second call MUST emit nothing.
    """
    state = _state()
    snap_label_to_pin(state, reference, pin, net)
    assert snap_label_to_pin(state, reference, pin, net) == []


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
def test_snap_labels_to_pins_idempotent(reference: str, mapping: dict[str, str]) -> None:
    """``snap_labels_to_pins`` is idempotent under bulk-mapping inputs."""
    state = _state()
    snap_labels_to_pins(state, reference, mapping)
    assert snap_labels_to_pins(state, reference, mapping) == []


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
def test_assign_footprints_idempotent(by_lib_id: dict[str, str]) -> None:
    """``assign_footprints`` is idempotent over arbitrary lib_id → footprint maps.

    Catches: a primitive that touches the Footprint field on every
    matching component regardless of whether the target value differs
    from the current one.
    """
    state = _state()
    assign_footprints(state, by_lib_id)
    assert assign_footprints(state, by_lib_id) == []


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
def test_delete_label_at_idempotent(net: str, coord: tuple[float, float]) -> None:
    """``delete_label_at`` is idempotent across both mutation and no-op paths.

    The bimodal ``coord`` strategy (``st.one_of`` of exact known coords +
    bounded near-label tuples) guarantees a non-trivial fraction of
    examples hit a real label position (mutation path: first call deletes,
    second emits nothing) AND a non-trivial fraction sample empty space
    (no-op path). Per [[G1DhgkrMHeSZKBO_mssfw]].
    """
    x, y = coord
    state = _state()
    delete_label_at(state, net, x, y)
    assert delete_label_at(state, net, x, y) == []


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
    net: str,
    from_coord: tuple[float, float],
    to_coord: tuple[float, float],
) -> None:
    """``move_label`` is idempotent across both mutation and no-op paths.

    Same bimodal strategy as :func:`test_delete_label_at_idempotent`.
    Per [[G1DhgkrMHeSZKBO_mssfw]].
    """
    from_x, from_y = from_coord
    to_x, to_y = to_coord
    state = _state()
    move_label(state, net, from_x, from_y, to_x, to_y)
    assert move_label(state, net, from_x, from_y, to_x, to_y) == []


# ---- add_pwr_flag -----------------------------------------------------------


# KiCad schematics live on a 1.27 mm (0.05 in) grid. Phase 2.4 fixed
# Flag #1: ``add_pwr_flag`` snaps caller-supplied ``(x, y)`` to the
# 1.27 mm grid before placing the flag and its label, so off-grid input
# does not break idempotence. The property test samples a wide continuous
# float range so Hypothesis can re-attack the original counterexample
# class (e.g. y=127.75) and any others.
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
def test_add_pwr_flag_idempotent(net: str, coord: tuple[float, float]) -> None:
    """``add_pwr_flag`` is idempotent across arbitrary off-grid coordinates.

    The second call on the SAME caller-supplied ``(x, y)`` must emit
    nothing and must not add a second flag to the state — including the
    previously-failing ``(5.0, 127.75)`` class.
    """
    x, y = coord
    state = _state()
    first = add_pwr_flag(state, net, x, y)
    flags_before = sum(1 for c in state.components.values() if c.lib_id == "power:PWR_FLAG")
    assert add_pwr_flag(state, net, x, y) == [], "second add_pwr_flag emitted ops"
    flags_after = sum(1 for c in state.components.values() if c.lib_id == "power:PWR_FLAG")
    assert flags_after == flags_before, f"flag duplicated: {flags_before} -> {flags_after}"
    assert [o["op"] for o in first] == ["place_pwr_flag", "add_net_label"]


def test_add_pwr_flag_grid_snap_off_grid_coords() -> None:
    """Direct unit test for Phase 2.4 grid-snap: off-grid ``(x, y)`` is placed on-grid.

    The Flag #1 counterexample ``y=127.75`` (127.75 / 1.27 = 100.59)
    rounds to ``128.27 = 101 * 1.27``; ``x=5.0`` rounds to
    ``5.08 = 4 * 1.27``. The flag AND its label must be emitted at the same
    snapped point (the original bug: the component was snapped but the
    label was not).
    """
    state = _state()
    ops = add_pwr_flag(state, "+3V3_SNAP_TEST", 5.0, 127.75)
    flag, label = ops
    # 5.08 mm = 200 mil, 128.27 mm = 5050 mil: whole mils on akcli's 50-mil grid.
    assert flag["at"] == [200, 5050]
    assert label == {
        "op": "add_net_label",
        "name": "+3V3_SNAP_TEST",
        "at": [200, 5050],
        "scope": "local",
        "rotation": 0,
    }
    assert state.labels_near("+3V3_SNAP_TEST", 5.08, 128.27, tolerance=0.001)

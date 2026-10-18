"""Schematic primitives, compiled against a :class:`~kbat.backend.state.BoardState`.

Each primitive reads the state, returns the list of akcli op dicts needed
to reach its target (empty list = already in the target state, i.e. an
idempotent no-op), and updates the state to reflect those ops so later
transforms in the same manifest see them. Idempotence is decided here,
against what the file actually contains, not by the backend: a manifest
that is already applied compiles to no ops at all.

Reference enumeration (cleanup.py sections):

    A. set_property                     — value/footprint/etc on one component
    B. add_no_connect                   — NC markers at pin endpoints
    C. assign_footprints                — bulk lib_id → footprint
    D. snap_labels_to_pins              — multi-label snap to pin endpoints
    F. delete_label_at                  — collision cleanup (delete)
    F. move_label                       — collision cleanup (relocate)
    G. add_pwr_flag                     — PWR_FLAG + co-located label

Section E (collision detection) is read-only and not a primitive.

Backend note: akcli has no label-move op, so a move compiles to
``delete_object`` (matched by name + exact position) followed by
``add_net_label``. :mod:`kbat.backend.fidelity` checks after the write
that each moved label kept its formatting.
"""

from __future__ import annotations

import math
from typing import Any

from kbat.backend.akcli import to_mil
from kbat.backend.state import BoardState, Component, Label

Op = dict[str, Any]

# KiCad standard editing grid (0.05 in = 1.27 mm). PWR_FLAG positions
# are snapped to it so the flag and its co-located label land on the same
# on-grid point (akcli also snaps placements to this 50-mil grid). See
# ROADMAP Appendix A Flag #1.
KICAD_GRID_MM = 1.27


def _snap_to_grid(value: float, grid: float = KICAD_GRID_MM) -> float:
    """Snap a single coordinate to the nearest integer multiple of ``grid``."""
    return round(round(value / grid) * grid, 4)


# ---- internal helpers -------------------------------------------------------


def _pin_xy(state: BoardState, reference: str, pin_number: str) -> tuple[float, float] | None:
    comp = state.components.get(reference)
    if comp is None:
        return None
    return comp.pins.get(str(pin_number))


def _delete_label_op(lab: Label) -> Op:
    """Delete ``lab`` by its file uuid; by name + position only if it has none.

    akcli's ``match.at`` is exact in mils, so a label off the whole-mil grid
    (e.g. 50 mm = 1968.503937 mil) is never matched and the delete silently
    no-ops. Labels read from the file always carry a uuid. Only labels
    created earlier in the same op-list lack one, and those sit exactly
    where kbat placed them (a pin or a whole-mil point).
    """
    if lab.uuid:
        return {"op": "delete_object", "uuid": lab.uuid}
    return {
        "op": "delete_object",
        "match": {"kind": "label", "name": lab.name, "at": [to_mil(lab.x), to_mil(lab.y)]},
    }


def _move_label(
    state: BoardState, lab: Label, to_at: str | list[int | float], to_xy: tuple[float, float]
) -> list[Op]:
    """Ops moving ``lab`` to ``to_at``; updates ``lab`` and logs the move."""
    ops: list[Op] = [
        _delete_label_op(lab),
        {
            "op": "add_net_label",
            "name": lab.name,
            "at": to_at,
            "scope": "local",
            "rotation": int(lab.rotation),
        },
    ]
    lab.x, lab.y = to_xy
    lab.uuid = None  # the re-added label is a new node, created by akcli
    if not any(m is lab for m in state.moved):
        state.moved.append(lab)
    return ops


def _next_flag_number(state: BoardState) -> int:
    used: set[int] = set()
    for ref in state.components:
        if ref.startswith("#FLG"):
            try:
                used.add(int(ref[4:]))
            except ValueError:
                continue
    return (max(used) + 1) if used else 1


# ---- primitives -------------------------------------------------------------


def set_property(state: BoardState, reference: str, property: str, value: str) -> list[Op]:
    """Set ``property`` on component ``reference`` to ``value``.

    No ops if the property already holds ``value`` or the component does
    not exist.
    """
    comp = state.components.get(reference)
    if comp is None or comp.properties.get(property) == value:
        return []
    op: Op = {"op": "set_component_parameters", "designator": reference}
    if property == "Value":
        op["value"] = value
    elif property == "Footprint":
        op["footprint"] = value
    elif property == "Reference":
        op["new_designator"] = value
    else:
        op["parameters"] = {property: value}
    comp.properties[property] = value
    if property == "Reference":
        del state.components[reference]
        comp.reference = value
        state.components[value] = comp
    return [op]


def add_no_connect(state: BoardState, reference: str, pins: list[int | str]) -> list[Op]:
    """Drop no-connect markers on the listed pins of ``reference``.

    Pins that already carry a marker (within 0.05 mm), whoever placed it,
    are skipped; unknown pins are skipped.
    """
    ops: list[Op] = []
    for pin_id in pins:
        xy = _pin_xy(state, reference, str(pin_id))
        if xy is None or state.no_connect_near(*xy, tolerance=0.05):
            continue
        ops.append({"op": "add_no_connect", "pin": f"{reference}.{pin_id}"})
        state.no_connects.append(xy)
    return ops


def snap_label_to_pin(
    state: BoardState,
    reference: str,
    pin: str | int,
    net: str,
    max_distance_mm: float = 25.0,
) -> list[Op]:
    """Snap the nearest ``net`` label onto ``reference``/``pin``.

    No ops if a ``net`` label is already at the pin (within 0.01 mm) or no
    candidate lies within ``max_distance_mm``.
    """
    xy = _pin_xy(state, reference, str(pin))
    if xy is None:
        return []
    px, py = xy
    if state.labels_near(net, px, py, tolerance=0.01):
        return []
    cands = [(math.hypot(lab.x - px, lab.y - py), lab) for lab in state.labels if lab.name == net]
    if not cands:
        return []
    d, lab = min(cands, key=lambda c: c[0])
    if d > max_distance_mm:
        return []
    return _move_label(state, lab, f"{reference}.{pin}", (px, py))


def snap_labels_to_pins(
    state: BoardState,
    reference: str,
    mapping: dict[str | int, str],
    tolerance_mm: float = 25.0,
) -> list[Op]:
    """Snap many ``{pin: net}`` labels to pin endpoints in one pass.

    Two-pass to match cleanup.py section D: (1) claim labels already
    exactly at their pin, (2) for each remaining pin, move the nearest
    unclaimed candidate within ``tolerance_mm``. This prevents the same
    GND/+3V3 label from being moved across multiple pins on the same net.
    """
    used: set[int] = set()
    pin_targets: list[tuple[str, str, float, float]] = []
    for pin_num, net in mapping.items():
        xy = _pin_xy(state, reference, str(pin_num))
        if xy is not None:
            pin_targets.append((str(pin_num), net, xy[0], xy[1]))

    for _, net, px, py in pin_targets:
        for lab in state.labels:
            if id(lab) in used or lab.name != net:
                continue
            if abs(lab.x - px) < 0.01 and abs(lab.y - py) < 0.01:
                used.add(id(lab))
                break

    ops: list[Op] = []
    for pin_num, net, px, py in pin_targets:
        if state.labels_near(net, px, py, tolerance=0.01):
            continue
        cands = [
            (math.hypot(lab.x - px, lab.y - py), lab)
            for lab in state.labels
            if id(lab) not in used and lab.name == net
        ]
        if not cands:
            continue
        d, lab = min(cands, key=lambda c: c[0])
        if d > tolerance_mm:
            continue
        ops.extend(_move_label(state, lab, f"{reference}.{pin_num}", (px, py)))
        used.add(id(lab))
    return ops


def assign_footprints(state: BoardState, by_lib_id: dict[str, str]) -> list[Op]:
    """Assign footprints to every component matching each ``lib_id``.

    Skips virtual ``power:*`` symbols and components already carrying the
    target footprint.
    """
    ops: list[Op] = []
    for comp in state.components.values():
        if comp.lib_id.startswith("power:"):
            continue
        new_fp = by_lib_id.get(comp.lib_id)
        if new_fp is None or comp.footprint == new_fp:
            continue
        ops.append(
            {"op": "set_component_parameters", "designator": comp.reference, "footprint": new_fp}
        )
        comp.properties["Footprint"] = new_fp
    return ops


def delete_label_at(
    state: BoardState, net: str, x: float, y: float, tolerance_mm: float = 0.05
) -> list[Op]:
    """Delete the ``net`` label at ``(x, y)`` ±``tolerance_mm``.

    The lookup uses the tolerance; the emitted delete names the label
    itself (see :func:`_delete_label_op`). No ops if no label matches.
    """
    matches = state.labels_near(net, x, y, tolerance_mm)
    if not matches:
        return []
    lab = matches[0]
    state.labels.remove(lab)
    return [_delete_label_op(lab)]


def move_label(
    state: BoardState,
    net: str,
    from_x: float,
    from_y: float,
    to_x: float,
    to_y: float,
    tolerance_mm: float = 0.05,
) -> list[Op]:
    """Move the ``net`` label at ``from_*`` to ``to_*``.

    No ops if a ``net`` label is already at ``to_*`` or none is at ``from_*``.
    """
    if state.labels_near(net, to_x, to_y, tolerance_mm):
        return []
    matches = state.labels_near(net, from_x, from_y, tolerance_mm)
    if not matches:
        return []
    return _move_label(state, matches[0], [to_mil(to_x), to_mil(to_y)], (to_x, to_y))


def add_pwr_flag(
    state: BoardState, net: str, x: float, y: float, tolerance_mm: float = 0.5
) -> list[Op]:
    """Add a ``power:PWR_FLAG`` plus a ``net`` label at the grid-snapped ``(x, y)``.

    No ops if a PWR_FLAG with a co-located ``net`` label already exists
    near the request. The search window is widened by ``KICAD_GRID_MM`` so
    a flag placed at the snapped point (or hand-placed near the unsnapped
    one) is recognised. See ROADMAP Appendix A Flag #1.
    """
    sx, sy = _snap_to_grid(x), _snap_to_grid(y)
    search_tol = tolerance_mm + KICAD_GRID_MM
    for comp in state.components.values():
        if comp.lib_id != "power:PWR_FLAG":
            continue
        if abs(comp.x - sx) > search_tol or abs(comp.y - sy) > search_tol:
            continue
        if state.labels_near(net, comp.x, comp.y, search_tol):
            return []
    at = [to_mil(sx), to_mil(sy)]
    ref = f"#FLG{_next_flag_number(state)}"
    state.components[ref] = Component(
        reference=ref, lib_id="power:PWR_FLAG", properties={"Value": "PWR_FLAG"}, x=sx, y=sy
    )
    state.labels.append(Label(name=net, x=sx, y=sy))
    return [
        {"op": "place_pwr_flag", "at": at, "rotation": 0},
        {"op": "add_net_label", "name": net, "at": at, "scope": "local", "rotation": 0},
    ]


__all__ = [
    "KICAD_GRID_MM",
    "Op",
    "add_no_connect",
    "add_pwr_flag",
    "assign_footprints",
    "delete_label_at",
    "move_label",
    "set_property",
    "snap_label_to_pin",
    "snap_labels_to_pins",
]

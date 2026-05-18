"""Schematic primitive operations on a loaded :class:`kicad_sch_api.Schematic`.

Owned by Phase 1 Agent D (primitives + Pydantic schema). Each primitive
returns ``True`` if it mutated the schematic, ``False`` if the schematic
was already in the target state (idempotent no-op). Idempotence is the
load-bearing contract for the Phase 1 hard gate criterion 1.G.c.

Reference enumeration (cleanup.py sections):

    A. set_property                     — value/footprint/etc on one component
    B. add_no_connect                   — NC markers at pin endpoints
    C. assign_footprints                — bulk lib_id → footprint
    D. snap_labels_to_pins              — multi-label snap to pin endpoints
    F. delete_label_at                  — collision cleanup (delete)
    F. move_label                       — collision cleanup (relocate)
    G. add_pwr_flag                     — PWR_FLAG + co-located label

Section E (collision detection) is read-only and not a primitive.

Backend caveat (KiCad 10): the upstream ``kicad-sch-api`` 0.5.5 legacy
validator rejects reference designators containing underscores (e.g.
``U_MCU``) as ``ERROR``, blocking :meth:`Schematic.save`. The exported
helper :func:`relax_validation` widens the regex on a per-instance basis;
the runner should call it once after :func:`Schematic.load` and before
the first apply. This is documented as a Phase 1 backend gap; upstream
fix tracked in the phase note.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from kicad_sch_api import Schematic


# ---- backend compat ---------------------------------------------------------


# Permissive reference pattern: accept upstream's grammar plus underscores
# (e.g. ``U_MCU``) and arbitrary digit/letter tails. Mirrors the shape of
# upstream's regex but loosens the body class.
_PERMISSIVE_REF_RE = re.compile(r"^(#[A-Z]+[0-9]+|[A-Z][A-Z0-9_]*[A-Z0-9]?|[A-Z]+\?)$")


# KiCad standard editing grid (0.05 in = 1.27 mm). All component and
# wire endpoints in a KiCad schematic are expected to lie on integer
# multiples of this. ``kicad-sch-api`` 0.5.6 snaps PWR_FLAG *component*
# positions to this grid on insert but does NOT snap label positions,
# which produces an off-grid offset of up to ~0.6 mm between the flag
# and its co-located label and breaks the :func:`add_pwr_flag`
# idempotence check (tolerance 0.5 mm). Pre-snapping the caller's
# ``(x, y)`` before storing fixes this end-to-end. See ROADMAP
# Appendix A Flag #1.
KICAD_GRID_MM = 1.27


def _snap_to_grid(value: float, grid: float = KICAD_GRID_MM) -> float:
    """Snap a single coordinate to the nearest integer multiple of ``grid``.

    Used by :func:`add_pwr_flag` to align caller-supplied ``(x, y)`` to
    the KiCad 1.27 mm grid before storing the flag component and label,
    so the idempotence check (and visual layout) is grid-stable on
    arbitrary float input. Round-half-to-even via :func:`round`.
    """
    return round(value / grid) * grid


def relax_validation(sch: Schematic) -> None:
    """Loosen the per-instance reference validator on ``sch``.

    Required because ``kicad-sch-api`` 0.5.5 hard-fails ``save()`` on
    valid KiCad references that contain underscores. Safe to call
    multiple times.
    """
    legacy = getattr(sch, "_legacy_validator", None)
    if legacy is not None:
        legacy._valid_reference_pattern = _PERMISSIVE_REF_RE


# ---- internal helpers -------------------------------------------------------


def _pin_xy(sch: Schematic, reference: str, pin_number: str) -> tuple[float, float] | None:
    """Return ``(x, y)`` of pin ``pin_number`` on ``reference``, or None."""
    pos = sch.get_component_pin_position(reference, str(pin_number))
    if pos is None:
        return None
    return float(pos.x), float(pos.y)


def _label_xy(label: Any) -> tuple[float, float]:
    """Return ``(x, y)`` of a label's :class:`Point` position."""
    p = label.position
    return float(p.x), float(p.y)


def _component_xy(comp: Any) -> tuple[float, float]:
    p = comp.position
    return float(p.x), float(p.y)


def _find_labels_near(
    sch: Schematic, net: str, x: float, y: float, tolerance: float
) -> list[Any]:
    """Return labels whose text equals ``net`` and centred within ``tolerance``."""
    out: list[Any] = []
    for lab in sch.labels:
        if lab.text != net:
            continue
        lx, ly = _label_xy(lab)
        if abs(lx - x) <= tolerance and abs(ly - y) <= tolerance:
            out.append(lab)
    return out


def _next_flag_number(sch: Schematic) -> int:
    """Return the next free ``#FLGN`` integer (1 if none exist)."""
    used: set[int] = set()
    for comp in sch.components:
        ref = comp.reference
        if not ref.startswith("#FLG"):
            continue
        try:
            used.add(int(ref[4:]))
        except ValueError:
            continue
    return (max(used) + 1) if used else 1


# ---- primitives -------------------------------------------------------------


def set_property(sch: Schematic, reference: str, property: str, value: str) -> bool:
    """Set ``property`` on component ``reference`` to ``value``.

    Returns ``True`` if the property was changed, ``False`` if it already
    held ``value`` (idempotent no-op) or the component does not exist.

    Implementation note: ``kicad-sch-api`` 0.5.5's
    :meth:`Component.set_property` updates the property dict in memory
    but does NOT propagate to the synced ``.value`` / ``.footprint``
    attributes nor to the serialised output of :meth:`Schematic.save`.
    The reliable mutation path is direct attribute assignment for the
    three canonical properties; ``set_property`` is reserved for custom
    properties not surfaced as attributes.
    """
    comp = sch.components.get(reference)
    if comp is None:
        return False
    if property == "Value":
        if comp.value == value:
            return False
        comp.value = value
        return True
    if property == "Footprint":
        if comp.footprint == value:
            return False
        comp.footprint = value
        return True
    if property == "Reference":
        if comp.reference == value:
            return False
        comp.reference = value
        return True
    # Fallback for arbitrary properties (Datasheet, Description, custom).
    prop = comp.get_property(property)
    current = prop.get("value") if isinstance(prop, dict) else prop
    if current == value:
        return False
    comp.set_property(property, value)
    return True


def add_no_connect(sch: Schematic, reference: str, pins: list[int | str]) -> bool:
    """Drop no-connect markers on the listed pins of ``reference``.

    Idempotent: existing NC markers at any pin endpoint (within 0.05 mm)
    are detected and skipped.
    """
    changed = False
    for pin_id in pins:
        xy = _pin_xy(sch, reference, str(pin_id))
        if xy is None:
            continue
        if sch.no_connects.find_at_position(xy, tolerance=0.05):
            continue
        sch.no_connects.add(xy)
        changed = True
    return changed


def snap_label_to_pin(
    sch: Schematic,
    reference: str,
    pin: str | int,
    net: str,
    max_distance_mm: float = 25.0,
) -> bool:
    """Snap one existing ``net`` label onto the endpoint of ``reference``/``pin``.

    No-op if a label with text ``net`` is already at the pin position
    (within 0.01 mm) or if no unclaimed candidate exists within
    ``max_distance_mm``. Returns ``True`` iff a label was moved.
    """
    xy = _pin_xy(sch, reference, str(pin))
    if xy is None:
        return False
    px, py = xy
    # Already aligned?
    if _find_labels_near(sch, net, px, py, tolerance=0.01):
        return False
    # Find the nearest matching label
    best: tuple[float, Any] | None = None
    for lab in sch.labels:
        if lab.text != net:
            continue
        lx, ly = _label_xy(lab)
        d = math.hypot(lx - px, ly - py)
        if best is None or d < best[0]:
            best = (d, lab)
    if best is None or best[0] > max_distance_mm:
        return False
    best[1].move(px, py)
    return True


def snap_labels_to_pins(
    sch: Schematic,
    reference: str,
    mapping: dict[str | int, str],
    tolerance_mm: float = 25.0,
) -> bool:
    """Snap many ``{pin: net}`` labels to pin endpoints in one pass.

    Two-pass to match cleanup.py section D: (1) claim labels already
    exactly at their pin, (2) for each remaining pin, snap the nearest
    unclaimed candidate within ``tolerance_mm``. This prevents the same
    GND/+3V3 label from being moved across multiple pins on the same net.

    Returns ``True`` if any label was moved.
    """
    used: set[str] = set()

    # Pre-resolve all pin positions for the mapping.
    pin_targets: list[tuple[str, str, float, float]] = []  # (pin, net, x, y)
    for pin_num, net in mapping.items():
        xy = _pin_xy(sch, reference, str(pin_num))
        if xy is None:
            continue
        pin_targets.append((str(pin_num), net, xy[0], xy[1]))

    # Pass 1: claim already-aligned labels so they cannot be reassigned.
    for _, net, px, py in pin_targets:
        for lab in sch.labels:
            if lab.uuid in used or lab.text != net:
                continue
            lx, ly = _label_xy(lab)
            if abs(lx - px) < 0.01 and abs(ly - py) < 0.01:
                used.add(lab.uuid)
                break

    # Pass 2: snap nearest unclaimed candidate within tolerance.
    changed = False
    for _, net, px, py in pin_targets:
        if _find_labels_near(sch, net, px, py, tolerance=0.01):
            continue  # already aligned (claimed above)
        best: tuple[float, Any] | None = None
        for lab in sch.labels:
            if lab.uuid in used or lab.text != net:
                continue
            lx, ly = _label_xy(lab)
            d = math.hypot(lx - px, ly - py)
            if best is None or d < best[0]:
                best = (d, lab)
        if best is None or best[0] > tolerance_mm:
            continue
        best[1].move(px, py)
        used.add(best[1].uuid)
        changed = True
    return changed


def assign_footprints(sch: Schematic, by_lib_id: dict[str, str]) -> bool:
    """Bulk-assign footprints to every component matching each ``lib_id``.

    Skips virtual ``power:*`` symbols (which have no footprint). Idempotent
    per-component: returns ``True`` only if at least one component's
    footprint actually changed.
    """
    changed = False
    for comp in sch.components:
        lib_id = comp.lib_id
        if lib_id.startswith("power:"):
            continue
        new_fp = by_lib_id.get(lib_id)
        if new_fp is None:
            continue
        if comp.footprint == new_fp:
            continue
        comp.footprint = new_fp
        changed = True
    return changed


def delete_label_at(
    sch: Schematic, net: str, x: float, y: float, tolerance_mm: float = 0.05
) -> bool:
    """Delete the label with text ``net`` at ``(x, y)`` ±``tolerance_mm``.

    Returns ``True`` iff a matching label was found and removed.
    Idempotent: if no match exists (already deleted), returns ``False``.
    """
    matches = _find_labels_near(sch, net, x, y, tolerance_mm)
    if not matches:
        return False
    sch.labels.remove(matches[0].uuid)
    return True


def move_label(
    sch: Schematic,
    net: str,
    from_x: float,
    from_y: float,
    to_x: float,
    to_y: float,
    tolerance_mm: float = 0.05,
) -> bool:
    """Move the ``net`` label at ``from_*`` to ``to_*``.

    Idempotent: if the label is already at ``to_*`` (within
    ``tolerance_mm``), returns ``False``. If neither location holds a
    matching label, also returns ``False``.
    """
    # Already at the target? → idempotent no-op.
    if _find_labels_near(sch, net, to_x, to_y, tolerance_mm):
        return False
    matches = _find_labels_near(sch, net, from_x, from_y, tolerance_mm)
    if not matches:
        return False
    matches[0].move(to_x, to_y)
    return True


def add_pwr_flag(
    sch: Schematic, net: str, x: float, y: float, tolerance_mm: float = 0.5
) -> bool:
    """Add a ``power:PWR_FLAG`` component plus the ``net`` label at ``(x, y)``.

    Idempotent: if a PWR_FLAG component and a matching net label already
    co-exist near the requested position (within
    ``tolerance_mm + KICAD_GRID_MM`` to absorb the grid snap), returns
    ``False``. Otherwise allocates the next free ``#FLGN`` reference and
    adds both the flag component and the label at the grid-snapped
    position.

    Off-grid ``(x, y)`` is snapped to the nearest ``KICAD_GRID_MM``
    (1.27 mm) multiple BEFORE the component insert and the label
    insert. This is required because ``kicad-sch-api`` 0.5.6 silently
    snaps PWR_FLAG component positions to a KiCad-internal grid on
    insert but does NOT snap label positions; a caller-supplied
    off-grid coordinate would therefore store the flag on-grid and the
    label off-grid, producing an offset that breaks the idempotence
    tolerance and the visual layout. Snapping the input ourselves
    keeps the flag and label co-located on the grid. See ROADMAP
    Appendix A Flag #1.

    The idempotence-search window is widened by ``KICAD_GRID_MM`` to
    absorb the snap delta — an existing flag+label placed via an
    earlier call (or hand-placed near the request) is still recognised
    even when the snap shifts our target by up to half a grid unit
    relative to the unsnapped caller coordinate.
    """
    # Snap caller-supplied coordinates to the KiCad grid so the flag
    # component and its co-located label end up at the same point —
    # without this the kicad-sch-api auto-snap on the component would
    # drift it off the label by up to ~0.6 mm.
    sx = _snap_to_grid(x)
    sy = _snap_to_grid(y)
    # Widened idempotence window: covers BOTH the snapped target and
    # any existing flag/label placed at (or near) the unsnapped caller
    # coordinate. Without this, snapping would always insert a fresh
    # flag adjacent to a pre-existing one rather than treating it as
    # the same logical placement.
    search_tol = tolerance_mm + KICAD_GRID_MM
    # Idempotence check: PWR_FLAG + matching label near the request?
    for comp in sch.components:
        if comp.lib_id != "power:PWR_FLAG":
            continue
        cx, cy = _component_xy(comp)
        if abs(cx - sx) > search_tol or abs(cy - sy) > search_tol:
            continue
        if _find_labels_near(sch, net, cx, cy, search_tol):
            return False
    next_n = _next_flag_number(sch)
    sch.components.add(
        lib_id="power:PWR_FLAG",
        reference=f"#FLG{next_n}",
        value="PWR_FLAG",
        position=(sx, sy),
    )
    sch.add_label(net, position=(sx, sy))
    return True


__all__ = [
    "add_no_connect",
    "add_pwr_flag",
    "assign_footprints",
    "delete_label_at",
    "move_label",
    "relax_validation",
    "set_property",
    "snap_label_to_pin",
    "snap_labels_to_pins",
]

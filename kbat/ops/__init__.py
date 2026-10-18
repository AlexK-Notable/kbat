"""Schematic primitive operations — public re-exports.

The runner in :mod:`kbat._runner` and the planner import primitives from
here rather than reaching into the implementation module. Each primitive
compiles against a :class:`~kbat.backend.state.BoardState` and returns
backend ops (empty = no-op).
"""

from kbat.ops.schematic import (
    add_no_connect,
    add_pwr_flag,
    assign_footprints,
    delete_label_at,
    move_label,
    set_property,
    snap_label_to_pin,
    snap_labels_to_pins,
)

__all__ = [
    "add_no_connect",
    "add_pwr_flag",
    "assign_footprints",
    "delete_label_at",
    "move_label",
    "set_property",
    "snap_label_to_pin",
    "snap_labels_to_pins",
]

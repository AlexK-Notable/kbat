"""Schematic primitive operations — public re-exports.

The runner in :mod:`kbat.cli.main` and the validation harness import
primitives from here rather than reaching into the implementation module.
"""

from kbat.ops.schematic import (
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

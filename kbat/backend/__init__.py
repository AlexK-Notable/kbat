"""File backend for kbat: read board state, apply compiled op-lists.

The only backend is akcli (:mod:`kbat.backend.akcli`). Primitives in
:mod:`kbat.ops` compile against :class:`~kbat.backend.state.BoardState`
and emit akcli op dicts; the runner hands one op-list per apply to
:func:`~kbat.backend.akcli.draw`. Swapping the backend means providing
another ``read_state`` / ``draw`` pair that accepts the same op dicts.
"""

from kbat.backend.akcli import BackendError, DrawResult, draw, read_state, to_mil, to_mm
from kbat.backend.fidelity import (
    FidelityError,
    FieldCounts,
    apply_guarded,
    check_field_counts,
    check_moved_labels,
)
from kbat.backend.state import BoardState, Component, Label

__all__ = [
    "BackendError",
    "BoardState",
    "Component",
    "DrawResult",
    "FidelityError",
    "FieldCounts",
    "Label",
    "apply_guarded",
    "check_field_counts",
    "check_moved_labels",
    "draw",
    "read_state",
    "to_mil",
    "to_mm",
]

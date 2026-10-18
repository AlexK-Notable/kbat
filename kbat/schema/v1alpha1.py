"""Pydantic v2 manifest schema for ``kbat.dev/v1alpha1``.

Owned by Phase 1 Agent D (primitives + Pydantic schema). The shape mirrors
spec §3: a top-level :class:`Manifest` carries an ordered list of
:data:`Transform` discriminated-union entries, each keyed on the ``op``
field that dispatches to a primitive in :mod:`kbat.ops`.

Strict by default (``extra="forbid"``): unknown YAML keys raise
:class:`pydantic.ValidationError` rather than silently no-op'ing. New
primitives must be added (1) here as a transform subclass and (2) to the
union below and (3) to the dispatch table in :mod:`kbat.ops`.

The schema namespace is locked at ``kbat.dev/v1alpha1`` per the plan-hub
standing decision; bumps are reserved for v0.5+ migration tooling.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictBase(BaseModel):
    """Strict model: extra fields forbidden, validation strict."""

    model_config = ConfigDict(strict=True, extra="forbid")


# ---- Metadata ----------------------------------------------------------------


class ProjectMetadata(_StrictBase):
    name: str
    schematic: str  # path to .kicad_sch; relative paths resolve against the manifest's directory


class FileSignature(_StrictBase):
    """Optional pinned signature recorded at manifest authorship.

    Phase 1 does not enforce; Phase 2 will check before apply.
    """

    kicad_sch_sha256: str


# ---- Selector helpers --------------------------------------------------------


class RefSelector(_StrictBase):
    """Select a single component by its reference designator."""

    reference: str


# ---- Transform variants ------------------------------------------------------
#
# Each subclass is one row in the discriminated union and maps 1:1 to a
# primitive function in kbat.ops.schematic. The ``op`` literal is the
# discriminator; the rest are the primitive's keyword arguments.


class SetPropertyTransform(_StrictBase):
    """Set one property (e.g. ``Value``, ``Footprint``) on one component."""

    id: str | None = None
    op: Literal["set_property"]
    selector: RefSelector
    property: str
    value: str


class AddNoConnectTransform(_StrictBase):
    """Add no-connect markers on the listed pins of one component."""

    id: str | None = None
    op: Literal["add_no_connect"]
    reference: str
    pins: list[int | str]


class SnapLabelsToPinsTransform(_StrictBase):
    """Snap multiple existing net labels to pin endpoints on one component.

    ``mapping`` is ``{pin_number_as_str: expected_net_name}``. The op
    iterates the mapping in insertion order; per-pin behaviour matches
    cleanup.py's two-pass (claim-aligned-then-snap-nearest) heuristic
    capped at ``tolerance_mm``.
    """

    id: str | None = None
    op: Literal["snap_labels_to_pins"]
    reference: str
    mapping: dict[str, str]
    tolerance_mm: float = 25.0


class AssignFootprintsTransform(_StrictBase):
    """Bulk-assign footprints by ``lib_id``. ``power:*`` symbols are skipped."""

    id: str | None = None
    op: Literal["assign_footprints"]
    by_lib_id: dict[str, str]


class DeleteLabelAtTransform(_StrictBase):
    """Delete the label with the given text at the given coordinate."""

    id: str | None = None
    op: Literal["delete_label_at"]
    net: str
    x: float
    y: float
    tolerance_mm: float = 0.05


class MoveLabelTransform(_StrictBase):
    """Move a label with the given text from one coordinate to another."""

    id: str | None = None
    op: Literal["move_label"]
    net: str
    from_x: float
    from_y: float
    to_x: float
    to_y: float
    tolerance_mm: float = 0.05


class AddPwrFlagTransform(_StrictBase):
    """Add a PWR_FLAG component plus the co-located net label.

    Idempotent: if a PWR_FLAG with a co-located label of ``net`` already
    exists within ``tolerance_mm``, the op is a no-op.
    """

    id: str | None = None
    op: Literal["add_pwr_flag"]
    net: str
    x: float
    y: float
    tolerance_mm: float = 0.5


Transform = Annotated[
    (
        SetPropertyTransform
        | AddNoConnectTransform
        | SnapLabelsToPinsTransform
        | AssignFootprintsTransform
        | DeleteLabelAtTransform
        | MoveLabelTransform
        | AddPwrFlagTransform
    ),
    Field(discriminator="op"),
]


# ---- Top-level manifest ------------------------------------------------------


class Manifest(_StrictBase):
    """Top-level kbat manifest document.

    Example::

        apiVersion: kbat.dev/v1alpha1
        kind: Project
        metadata:
          name: kicad_experiment
          schematic: /abs/path/to/board.kicad_sch
        transforms:
          - op: set_property
            selector: {reference: R6}
            property: Value
            value: "1k PWM series"
    """

    apiVersion: Literal["kbat.dev/v1alpha1"]
    kind: Literal["Project"]
    metadata: ProjectMetadata
    pinned_signature: FileSignature | None = None
    transforms: list[Transform]


# ---- Apply result ------------------------------------------------------------


class DetailEntry(_StrictBase):
    """One entry in :attr:`ApplyResult.details` — one transform's outcome.

    Emitted per-transform by :mod:`kbat._runner` (lines 271/275/278) and
    consumed by the CLI for the apply-summary table. The shape is locked
    to the four fields below: ``id`` (the manifest transform id or the
    op-name fallback), ``op`` (primitive name, e.g. ``set_property``),
    ``status`` (one of ``applied``/``skipped``/``error``), and ``error``
    (populated only when ``status == "error"``).

    Strict (``extra="forbid"``) so the next consumer downstream
    (currently CLI; v0.5 MCP server) cannot silently absorb a drifted
    extra key. Per refactor-agent OSTH design ([[uhjfJqjIT9oRjeBp5UpG6]]
    §3.3 step 2) this is the second Conway artifact alongside
    :class:`kbat._state_schema.TrackedFile`.
    """

    id: str | None = None
    op: str
    status: Literal["applied", "skipped", "error"]
    error: str | None = None


class ApplyResult(_StrictBase):
    """Aggregate summary of one ``kbat apply`` invocation.

    Strict (``extra="forbid"``) — every field on this model is enumerated
    below, and downstream consumers (CLI, future MCP server) read them by
    attribute access on a typed Pydantic model rather than by dict
    subscripting on ``list[dict[str, Any]]``. See :class:`DetailEntry`
    for the per-transform entry shape inside ``details``.
    """

    applied: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
    details: list[DetailEntry] = Field(default_factory=list)
    #: akcli's net-diff summary of the write (``! SPLIT ...`` / ``! MERGE ...``).
    net_changes: list[str] = Field(default_factory=list)


__all__ = [
    "AddNoConnectTransform",
    "AddPwrFlagTransform",
    "ApplyResult",
    "AssignFootprintsTransform",
    "DeleteLabelAtTransform",
    "DetailEntry",
    "FileSignature",
    "Manifest",
    "MoveLabelTransform",
    "ProjectMetadata",
    "RefSelector",
    "SetPropertyTransform",
    "SnapLabelsToPinsTransform",
    "Transform",
]

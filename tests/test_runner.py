"""Runner smoke tests — manifest load, dispatch, error handling.

These tests stay backend-agnostic where possible: the dispatcher accepts
any duck-typed Schematic object, so we can drive it with a fake without
needing a real .kicad_sch.

The end-to-end "apply to real schematic" path is covered by the Phase 1
validation harness in ``kbat/_validation.py``; tests here cover the
glue layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from kbat._runner import (
    UnknownTransformError,
    dispatch_transform,
    load_manifest,
    resolve_schematic_path,
)
from kbat.backend import BoardState, Component
from kbat.schema.v1alpha1 import (
    Manifest,
    RefSelector,
    SetPropertyTransform,
)
from tests.conftest import REAL_SCHEMATIC

# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
def minimal_manifest_text() -> str:
    return """\
apiVersion: kbat.dev/v1alpha1
kind: Project
metadata:
  name: smoketest
  schematic: /tmp/non_existent.kicad_sch
transforms:
  - id: t1
    op: set_property
    selector: {reference: R1}
    property: Value
    value: "1k"
"""


@pytest.fixture
def minimal_manifest_path(tmp_path: Path, minimal_manifest_text: str) -> Path:
    p = tmp_path / "smoke.kbat.yaml"
    p.write_text(minimal_manifest_text, encoding="utf-8")
    return p


# ---- loader / validation ----------------------------------------------------


def test_load_manifest_roundtrip(minimal_manifest_path: Path) -> None:
    m = load_manifest(minimal_manifest_path)
    assert isinstance(m, Manifest)
    assert m.apiVersion == "kbat.dev/v1alpha1"
    assert m.kind == "Project"
    assert m.metadata.name == "smoketest"
    assert len(m.transforms) == 1
    t = m.transforms[0]
    assert isinstance(t, SetPropertyTransform)
    assert t.selector.reference == "R1"
    assert t.value == "1k"


def test_load_manifest_rejects_unknown_op(tmp_path: Path) -> None:
    """Unknown ops are rejected at schema-validation time (discriminated union)."""
    p = tmp_path / "bad.kbat.yaml"
    p.write_text(
        "apiVersion: kbat.dev/v1alpha1\n"
        "kind: Project\n"
        "metadata:\n"
        "  name: x\n"
        "  schematic: /tmp/x.kicad_sch\n"
        "transforms:\n"
        "  - op: invent_a_new_op\n"
        "    reference: R1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_manifest(p)


def test_load_manifest_rejects_extra_fields(tmp_path: Path) -> None:
    """Strict schema rejects unknown YAML keys."""
    p = tmp_path / "extra.kbat.yaml"
    p.write_text(
        "apiVersion: kbat.dev/v1alpha1\n"
        "kind: Project\n"
        "metadata:\n"
        "  name: x\n"
        "  schematic: /tmp/x.kicad_sch\n"
        "  extra_field: nope\n"  # <- forbidden
        "transforms: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_manifest(p)


def test_resolve_schematic_path_absolute(tmp_path: Path) -> None:
    m = Manifest.model_validate(
        {
            "apiVersion": "kbat.dev/v1alpha1",
            "kind": "Project",
            "metadata": {"name": "x", "schematic": "/abs/path/sch.kicad_sch"},
            "transforms": [],
        }
    )
    resolved = resolve_schematic_path(m, tmp_path / "ignored.kbat.yaml")
    assert resolved == Path("/abs/path/sch.kicad_sch")


def test_resolve_schematic_path_relative(tmp_path: Path) -> None:
    m = Manifest.model_validate(
        {
            "apiVersion": "kbat.dev/v1alpha1",
            "kind": "Project",
            "metadata": {"name": "x", "schematic": "subdir/sch.kicad_sch"},
            "transforms": [],
        }
    )
    manifest_path = tmp_path / "manifest.kbat.yaml"
    resolved = resolve_schematic_path(m, manifest_path)
    assert resolved == (tmp_path / "subdir/sch.kicad_sch").resolve()


# ---- dispatch ---------------------------------------------------------------


def _state(*comps: Component) -> BoardState:
    return BoardState(components={c.reference: c for c in comps})


def test_dispatch_set_property_changes() -> None:
    state = _state(Component("R1", "Device:R", {"Value": "old"}))
    t = SetPropertyTransform(
        op="set_property",
        selector=RefSelector(reference="R1"),
        property="Value",
        value="new",
    )
    emitted = dispatch_transform(state, t)
    assert emitted == [{"op": "set_component_parameters", "designator": "R1", "value": "new"}]
    assert state.components["R1"].value == "new"


def test_dispatch_set_property_idempotent() -> None:
    state = _state(Component("R1", "Device:R", {"Value": "same"}))
    t = SetPropertyTransform(
        op="set_property",
        selector=RefSelector(reference="R1"),
        property="Value",
        value="same",
    )
    assert dispatch_transform(state, t) == []


def test_dispatch_set_property_missing_ref_is_noop() -> None:
    t = SetPropertyTransform(
        op="set_property",
        selector=RefSelector(reference="NOPE"),
        property="Value",
        value="x",
    )
    assert dispatch_transform(_state(), t) == []


def test_unknown_transform_raises() -> None:
    """A transform whose op string isn't in the dispatch table raises."""

    # DUCK_FAKE: tests dispatcher routing, not schematic mutation
    class _Spoof:
        op = "totally_made_up"

    with pytest.raises(UnknownTransformError):
        dispatch_transform(object(), _Spoof())


# ---- manifest file in the kicad_experiment dir ------------------------------


def test_real_manifest_loads() -> None:
    """The hand-authored cleanup.kbat.yaml passes schema validation.

    Per [[G1DhgkrMHeSZKBO_mssfw]] / Phase 2.2: this test subsumes the
    deleted ``test_real_manifest_yaml_parseable`` — ``load_manifest``
    already calls ``yaml.safe_load`` upstream of
    ``Manifest.model_validate``, so a malformed YAML file would fail
    here with a YAML parse error before schema validation ever runs.
    """
    real = REAL_SCHEMATIC.parent / "cleanup.kbat.yaml"
    m = load_manifest(real)
    assert m.apiVersion == "kbat.dev/v1alpha1"
    assert m.kind == "Project"
    assert len(m.transforms) >= 1

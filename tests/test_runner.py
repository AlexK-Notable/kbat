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
from kbat.schema.v1alpha1 import (
    Manifest,
    RefSelector,
    SetPropertyTransform,
)

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


# DUCK_FAKE: tests dispatcher routing, not schematic mutation
class _FakeComponent:
    """Duck-typed stand-in for kicad-sch-api Component.

    Exposes the same surface ops.set_property reads from: settable
    ``value``/``footprint``/``reference`` attributes plus a
    ``get_property``/``set_property`` pair for everything else.
    """

    def __init__(self, reference: str, props: dict[str, str]) -> None:
        self.reference = reference
        self._props = dict(props)

    @property
    def value(self) -> str | None:
        return self._props.get("Value")

    @value.setter
    def value(self, v: str) -> None:
        self._props["Value"] = v

    @property
    def footprint(self) -> str | None:
        return self._props.get("Footprint")

    @footprint.setter
    def footprint(self, v: str) -> None:
        self._props["Footprint"] = v

    def get_property(self, name: str) -> dict | None:
        v = self._props.get(name)
        return {"value": v} if v is not None else None

    def set_property(self, name: str, value: str) -> None:
        self._props[name] = value


# DUCK_FAKE: tests dispatcher routing, not schematic mutation
class _FakeComponents:
    def __init__(self, comps: list[_FakeComponent]) -> None:
        self._by_ref = {c.reference: c for c in comps}

    def get(self, reference: str) -> _FakeComponent | None:
        return self._by_ref.get(reference)


# DUCK_FAKE: tests dispatcher routing, not schematic mutation
class _FakeSchematic:
    def __init__(self, comps: list[_FakeComponent]) -> None:
        self.components = _FakeComponents(comps)


def test_dispatch_set_property_changes(monkeypatch) -> None:
    sch = _FakeSchematic([_FakeComponent("R1", {"Value": "old"})])
    t = SetPropertyTransform(
        op="set_property",
        selector=RefSelector(reference="R1"),
        property="Value",
        value="new",
    )
    changed = dispatch_transform(sch, t)
    assert changed is True
    assert sch.components.get("R1").value == "new"


def test_dispatch_set_property_idempotent(monkeypatch) -> None:
    sch = _FakeSchematic([_FakeComponent("R1", {"Value": "same"})])
    t = SetPropertyTransform(
        op="set_property",
        selector=RefSelector(reference="R1"),
        property="Value",
        value="same",
    )
    changed = dispatch_transform(sch, t)
    assert changed is False


def test_dispatch_set_property_missing_ref_returns_false() -> None:
    sch = _FakeSchematic([])
    t = SetPropertyTransform(
        op="set_property",
        selector=RefSelector(reference="NOPE"),
        property="Value",
        value="x",
    )
    changed = dispatch_transform(sch, t)
    assert changed is False


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
    real = Path("/home/komi/notes/kicad/kicad_experiment/cleanup.kbat.yaml")
    if not real.exists():
        pytest.skip("cleanup.kbat.yaml not present (run from clean checkout)")
    m = load_manifest(real)
    assert m.apiVersion == "kbat.dev/v1alpha1"
    assert m.kind == "Project"
    assert len(m.transforms) >= 1

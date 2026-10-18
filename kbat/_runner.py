"""Manifest loader + transform compiler/applier for `kbat apply`.

Owned by Phase 1 Agent E; extended by Phase 2 Agent G with safety
primitives from :mod:`kbat._safety` (`.kbat-lock`, `.kbat-state.json`
signature tracking, pre-apply snapshots for rollback); moved onto the
akcli backend (:mod:`kbat.backend`) so writes no longer lose KiCad 10
data.

Apply flow with safety on:

1. Acquire ``KbatLock`` for the project directory (refuse if held by a
   live process; reclaim if stale)
2. If the manifest carries a ``pinned_signature``, verify the current
   schematic sha256 matches (unless ``force_resignature``)
3. If ``.kbat-state.json`` exists, refuse if any tracked file drifted
   (unless ``force``)
4. Read the board state and compile every transform against it, in order
5. If the compiled op-list is empty, the manifest is already applied:
   nothing is snapshotted, written or recorded
6. Snapshot the schematic into ``.kbat-snapshots/{sha256}.kicad_sch``
7. Apply the whole op-list in one ``akcli draw --apply`` (akcli writes
   snapshot -> temp -> verify -> replace, so the batch is atomic)
8. Fidelity guard (:mod:`kbat.backend.fidelity`): if the write dropped
   KiCad 10 fields or flattened a moved label, restore the snapshot and
   report the error
9. Record the new (post-apply) signature into ``.kbat-state.json``
   alongside the snapshot pointer so ``kbat rollback`` can restore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kbat import backend, ops
from kbat._safety import (
    DriftDetectedError,
    KbatLock,
    KbatState,
    make_snapshot,
    verify_pinned_signature,
)
from kbat.ops.schematic import Op
from kbat.schema.v1alpha1 import ApplyResult, DetailEntry, Manifest


class UnknownTransformError(Exception):
    """Raised when a transform's `op` field has no registered compiler."""


def load_manifest(manifest_path: Path | str) -> Manifest:
    """Load and validate a manifest file."""
    p = Path(manifest_path)
    text = p.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return Manifest.model_validate(data)


def resolve_schematic_path(manifest: Manifest, manifest_path: Path) -> Path:
    """Resolve the schematic path. Absolute paths win; relative paths are
    interpreted relative to the manifest's directory."""
    p = Path(manifest.metadata.schematic)
    if p.is_absolute():
        return p
    return (manifest_path.parent / p).resolve()


def dispatch_transform(state: backend.BoardState, transform: Any) -> list[Op]:
    """Compile one transform against ``state``; returns its backend ops.

    An empty list means the target state already holds (no-op). ``state``
    is updated to reflect the returned ops.
    """
    op_name = transform.op
    if op_name == "set_property":
        return ops.set_property(
            state, transform.selector.reference, transform.property, transform.value
        )
    if op_name == "add_no_connect":
        return ops.add_no_connect(state, transform.reference, transform.pins)
    if op_name == "snap_labels_to_pins":
        return ops.snap_labels_to_pins(
            state, transform.reference, transform.mapping, tolerance_mm=transform.tolerance_mm
        )
    if op_name == "assign_footprints":
        return ops.assign_footprints(state, transform.by_lib_id)
    if op_name == "delete_label_at":
        return ops.delete_label_at(
            state, transform.net, transform.x, transform.y, tolerance_mm=transform.tolerance_mm
        )
    if op_name == "move_label":
        return ops.move_label(
            state,
            transform.net,
            transform.from_x,
            transform.from_y,
            transform.to_x,
            transform.to_y,
            tolerance_mm=transform.tolerance_mm,
        )
    if op_name == "add_pwr_flag":
        return ops.add_pwr_flag(state, transform.net, transform.x, transform.y)
    raise UnknownTransformError(f"unknown op: {op_name!r}")


@dataclass
class CompiledManifest:
    """A manifest compiled against one board state."""

    ops: list[Op] = field(default_factory=list)
    details: list[DetailEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    state: backend.BoardState | None = None


def compile_manifest(manifest: Manifest, state: backend.BoardState) -> CompiledManifest:
    """Compile every transform in order against ``state`` (mutated)."""
    out = CompiledManifest(state=state)
    for transform in manifest.transforms:
        op_name = transform.op
        tid = getattr(transform, "id", None) or op_name
        try:
            emitted = dispatch_transform(state, transform)
        except Exception as e:  # noqa: BLE001
            out.errors.append(f"{tid}: {e}")
            out.details.append(DetailEntry(id=tid, op=op_name, status="error", error=str(e)))
            continue
        out.ops.extend(emitted)
        out.details.append(
            DetailEntry(id=tid, op=op_name, status="applied" if emitted else "skipped")
        )
    return out


def _resolve_project_dir(sch_path: Path) -> Path:
    """The project directory is the directory containing the schematic.

    Sidecars (``.kbat-lock``, ``.kbat-state.json``, ``.kbat-snapshots/``)
    live next to the schematic itself, not next to the manifest — manifest
    placement is flexible, but a KiCad project's sidecars belong with the
    schematic so they travel together. akcli keeps its own ``.akcli/``
    workspace (backups, journal) in the same directory.
    """
    return Path(sch_path).resolve().parent


def run(
    manifest_path: Path | str,
    *,
    dry_run: bool = False,
    schematic_override: Path | str | None = None,
    force: bool = False,
    force_resignature: bool = False,
) -> ApplyResult:
    """Load manifest, read the schematic, compile all transforms, apply.

    Args:
        manifest_path: path to a .kbat.yaml manifest
        dry_run: if True, compile only: do not write, snapshot or update state
        schematic_override: if provided, target this schematic file instead
            of the one named in the manifest (useful for tmpdir harnesses)
        force: bypass drift detection against ``.kbat-state.json``
        force_resignature: bypass ``pinned_signature`` verification
    """
    manifest_p = Path(manifest_path)
    manifest = load_manifest(manifest_p)

    if schematic_override is not None:
        sch_path = Path(schematic_override)
    else:
        sch_path = resolve_schematic_path(manifest, manifest_p)

    project_dir = _resolve_project_dir(sch_path)

    # On a dry run we skip the lock entirely — no mutation, no sidecars.
    if dry_run:
        return _run_inner(
            sch_path,
            manifest,
            project_dir,
            dry_run=True,
            force=force,
            force_resignature=force_resignature,
        )

    with KbatLock(project_dir, mode="apply"):
        return _run_inner(
            sch_path,
            manifest,
            project_dir,
            dry_run=False,
            force=force,
            force_resignature=force_resignature,
        )


def _run_inner(
    sch_path: Path,
    manifest: Manifest,
    project_dir: Path,
    *,
    dry_run: bool,
    force: bool,
    force_resignature: bool,
) -> ApplyResult:
    """The apply pipeline, executed under the lock (when not dry-run)."""
    # --- pre-apply safety gates ------------------------------------------
    if not dry_run and sch_path.exists():
        # 1. pinned_signature (manifest-author intent: "I expect this exact file")
        if manifest.pinned_signature is not None:
            verify_pinned_signature(
                sch_path,
                manifest.pinned_signature.kicad_sch_sha256,
                force_resignature=force_resignature,
            )

        # 2. drift detection against the recorded post-apply state
        existing_state = KbatState.load(project_dir)
        if existing_state is not None and not force:
            drifted = existing_state.detect_drift(project_dir)
            if drifted:
                raise DriftDetectedError(drifted)

    # --- compile -----------------------------------------------------------
    compiled = compile_manifest(manifest, backend.read_state(sch_path))
    result = ApplyResult(
        applied=sum(1 for d in compiled.details if d.status == "applied"),
        skipped=sum(1 for d in compiled.details if d.status == "skipped"),
        errors=list(compiled.errors),
        details=list(compiled.details),
    )
    if dry_run or compiled.errors or not compiled.ops:
        # Nothing to write: dry run, a transform failed to compile, or the
        # manifest is already applied (converged — the file is untouched).
        return result

    # --- snapshot + apply + fidelity guard ----------------------------------
    snapshot_rel, _pre_sha = make_snapshot(sch_path, project_dir)
    try:
        drawn = backend.apply_guarded(
            sch_path, compiled.ops, compiled.state.moves if compiled.state else []
        )
    except backend.FidelityError as e:
        # apply_guarded already restored the original bytes; the snapshot
        # is kept and named so the restore can be checked by hand.
        result.errors.append(f"fidelity: {e} (schematic restored; snapshot {snapshot_rel})")
        return result
    except backend.BackendError as e:
        # akcli writes atomically, so a refused or failed draw leaves the
        # schematic untouched; drop the snapshot nobody will reference.
        _discard_snapshot(project_dir, snapshot_rel)
        hint = f" ({e.remediation})" if e.remediation else ""
        result.errors.append(f"backend: {e}{hint}")
        return result

    result.net_changes = drawn.net_changes
    new_state = KbatState.load(project_dir) or KbatState()
    new_state.record(project_dir, sch_path, snapshot_path=snapshot_rel)
    new_state.save(project_dir)
    return result


def _discard_snapshot(project_dir: Path, snapshot_rel: str) -> None:
    """Best-effort removal of a snapshot no state-file entry references."""
    try:
        orphan = project_dir / snapshot_rel
        if orphan.exists():
            orphan.unlink()
    except OSError:
        pass

"""Manifest loader + transform dispatcher for `kbat apply`.

Owned by Phase 1 Agent E; extended by Phase 2 Agent G with safety
primitives from :mod:`kbat._safety` (atomic write, `.kbat-lock`,
`.kbat-state.json` signature tracking, pre-apply snapshots for rollback).

Apply flow with safety on:

1. Acquire ``KbatLock`` for the project directory (refuse if held by a
   live process; reclaim if stale)
2. If the manifest carries a ``pinned_signature``, verify the current
   schematic sha256 matches (unless ``force_resignature``)
3. If ``.kbat-state.json`` exists, refuse if any tracked file drifted
   (unless ``force``)
4. Snapshot the schematic into ``.kbat-snapshots/{sha256}.kicad_sch``
5. Dispatch transforms in-memory
6. Save via :func:`kbat._safety.atomic_write_file` (kicad-sch-api writes
   to a tmp path; we atomically rename to the target)
7. Record the new (post-apply) signature into ``.kbat-state.json``
   alongside the snapshot pointer so ``kbat rollback`` can restore.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from kbat import ops
from kbat._safety import (
    DriftDetectedError,
    KbatLock,
    KbatState,
    atomic_write_file,
    make_snapshot,
    verify_pinned_signature,
)
from kbat.schema.v1alpha1 import ApplyResult, DetailEntry, Manifest


class UnknownTransformError(Exception):
    """Raised when a transform's `op` field has no registered dispatcher."""


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


def _save_with_relaxed_validation(sch: Any, file_path: Path) -> None:
    """Save a kicad-sch-api Schematic after relaxing its reference validator.

    Required because ``kicad-sch-api`` 0.5.5 hard-fails ``save()`` on
    KiCad references containing underscores (e.g. ``U_MCU``) that
    cleanup.py's underlying kicad-skip parser accepts. The
    :func:`kbat.ops.schematic.relax_validation` helper widens the
    per-instance regex.
    """
    from kbat.ops.schematic import relax_validation

    relax_validation(sch)
    sch.save(str(file_path))


def _atomic_save_schematic(sch: Any, file_path: Path) -> None:
    """Save the schematic crash-safely.

    kicad-sch-api 0.5.5's ``save()`` writes directly to the path you
    give it. To get tmp+fsync+rename semantics we (a) save to a temp
    file in the same directory (same filesystem → cross-fs rename
    can't happen), (b) publish via :func:`atomic_write_file`, which
    does the fsync + ``os.replace`` dance.
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    # Same-dir tmp keeps the rename inside one filesystem.
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{file_path.name}.kbat-save-",
        suffix=".tmp",
        dir=str(file_path.parent),
    )
    os.close(fd)
    tmp = Path(tmp_str)
    try:
        _save_with_relaxed_validation(sch, tmp)
        atomic_write_file(tmp, file_path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def dispatch_transform(sch: Any, transform: Any) -> bool:
    """Apply a single transform to the schematic.

    Returns True if the schematic was mutated.
    """
    op_name = transform.op
    if op_name == "set_property":
        return ops.set_property(
            sch,
            transform.selector.reference,
            transform.property,
            transform.value,
        )
    if op_name == "add_no_connect":
        return ops.add_no_connect(sch, transform.reference, transform.pins)
    if op_name == "snap_labels_to_pins":
        return ops.snap_labels_to_pins(
            sch,
            transform.reference,
            transform.mapping,
            tolerance_mm=transform.tolerance_mm,
        )
    if op_name == "assign_footprints":
        return ops.assign_footprints(sch, transform.by_lib_id)
    if op_name == "delete_label_at":
        return ops.delete_label_at(
            sch,
            transform.net,
            transform.x,
            transform.y,
            tolerance_mm=transform.tolerance_mm,
        )
    if op_name == "move_label":
        return ops.move_label(
            sch,
            transform.net,
            transform.from_x,
            transform.from_y,
            transform.to_x,
            transform.to_y,
            tolerance_mm=transform.tolerance_mm,
        )
    if op_name == "add_pwr_flag":
        return ops.add_pwr_flag(sch, transform.net, transform.x, transform.y)
    raise UnknownTransformError(f"unknown op: {op_name!r}")


def _resolve_project_dir(sch_path: Path) -> Path:
    """The project directory is the directory containing the schematic.

    Sidecars (``.kbat-lock``, ``.kbat-state.json``, ``.kbat-snapshots/``)
    live next to the schematic itself, not next to the manifest — manifest
    placement is flexible, but a KiCad project's sidecars belong with the
    schematic so they travel together.
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
    """Load manifest, open schematic, dispatch all transforms, save.

    Args:
        manifest_path: path to a .kbat.yaml manifest
        dry_run: if True, do not save the schematic, do not snapshot,
            do not update state
        schematic_override: if provided, target this schematic file instead
            of the one named in the manifest (useful for tmpdir harnesses)
        force: bypass drift detection against ``.kbat-state.json``
        force_resignature: bypass ``pinned_signature`` verification
    """
    import kicad_sch_api as ksa

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
            ksa,
            sch_path,
            manifest,
            project_dir,
            dry_run=True,
            force=force,
            force_resignature=force_resignature,
        )

    with KbatLock(project_dir, mode="apply"):
        return _run_inner(
            ksa,
            sch_path,
            manifest,
            project_dir,
            dry_run=False,
            force=force,
            force_resignature=force_resignature,
        )


def _run_inner(
    ksa: Any,
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

    # --- pre-apply snapshot ----------------------------------------------
    snapshot_rel = ""
    if not dry_run and sch_path.exists():
        snapshot_rel, _pre_sha = make_snapshot(sch_path, project_dir)

    # --- dispatch --------------------------------------------------------
    sch = ksa.load_schematic(str(sch_path))

    applied = 0
    skipped = 0
    details: list[DetailEntry] = []
    errors: list[str] = []

    for transform in manifest.transforms:
        op_name = transform.op
        tid = getattr(transform, "id", None) or op_name
        try:
            changed = dispatch_transform(sch, transform)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{tid}: {e}")
            details.append(
                DetailEntry(id=tid, op=op_name, status="error", error=str(e))
            )
            continue
        if changed:
            applied += 1
            details.append(DetailEntry(id=tid, op=op_name, status="applied"))
        else:
            skipped += 1
            details.append(DetailEntry(id=tid, op=op_name, status="skipped"))

    # --- save + record signature -----------------------------------------
    if not dry_run and not errors:
        _atomic_save_schematic(sch, sch_path)
        new_state = KbatState.load(project_dir) or KbatState()
        new_state.record(project_dir, sch_path, snapshot_path=snapshot_rel)
        new_state.save(project_dir)
    elif not dry_run and errors and snapshot_rel:
        # Snapshot was created BEFORE dispatch; dispatch errored before
        # we could record the pointer in .kbat-state.json. Without
        # cleanup the snapshot is an orphan — present on disk, but no
        # state-file entry references it (so rollback can't find it
        # and the user just sees `.kbat-snapshots/` slowly bloat).
        # Best-effort unlink: if the cleanup itself fails, swallow the
        # secondary error so the primary apply failure is not masked.
        try:
            orphan = project_dir / snapshot_rel
            if orphan.exists():
                orphan.unlink()
        except OSError:
            pass

    return ApplyResult(applied=applied, skipped=skipped, errors=errors, details=details)

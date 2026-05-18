"""Snapshot restore for ``kbat rollback``.

``kbat apply`` (see :mod:`kbat._runner` + :mod:`kbat._safety`) snapshots
every schematic it touches into ``.kbat-snapshots/`` before mutating it,
and records ``(sha256, mtime_ns, snapshot_path)`` for each file in
``.kbat-state.json``. ``kbat rollback`` reads that state file and copies
each snapshot back to its original location.

State-file contract
-------------------

The on-disk shape of ``.kbat-state.json`` is defined by
:class:`kbat._state_schema.StateFile` — a strict Pydantic model shared
with the writer. As of v0.1.1 the only accepted shape is::

    {
      "schema_version": 1,
      "last_apply_at": "2026-05-17T...Z",
      "files": {
        "kicad_experiment.kicad_sch": {
          "mtime_ns": 1700000000000000000,
          "sha256": "<64 hex chars>",
          "snapshot_path": ".kbat-snapshots/abc123....kicad_sch"
        },
        ...
      }
    }

Path keys are POSIX-relative to the project directory; ``snapshot_path``
is also POSIX-relative under the project directory. The legacy v0.1.0
reader accepted three other shapes (``schematics`` / ``schematic_paths``
wrappers, top-level absolute path keys, per-entry ``snapshot`` /
``mtime`` field names) — none of which were ever written by any kbat
version. They were imagined by the original docstring author and have
been removed; see [[NsnG-0hGvsI3cnd48Ofym]] for the divergence audit and
[[uhjfJqjIT9oRjeBp5UpG6]] for the OSTH policy that prevents recurrence.

Idempotence
-----------

``restore_previous`` is safe to call repeatedly. If the destination
file is already byte-identical to the snapshot (via SHA-256 compare),
it is skipped and not counted. Only files actually rewritten count
toward the returned ``restored_count``.

Selective restore
-----------------

The optional ``only`` argument restricts restoration to a single
schematic path. The CLI's ``--schematic`` option threads through here
so users can roll back one file from a multi-file project.

Coupling to the writer
----------------------

The writer (``kbat._safety.KbatState.save``) and this reader both
serialise through :class:`kbat._state_schema.StateFile`. Any change to
the on-disk shape MUST land in ``_state_schema.py`` first; both halves
then update in the same commit. This is the OSTH ("One Schema, Two
Halves") policy.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import ValidationError

from kbat._safety import sha256_file
from kbat._state_schema import STATE_FILENAME, StateFile


class RollbackError(Exception):
    """Raised on unrecoverable rollback errors (corrupt state, missing snapshot)."""


def _load_state(state_path: Path) -> StateFile:
    """Read ``.kbat-state.json`` into a :class:`StateFile`.

    Raises :class:`RollbackError` if the file is missing, unreadable, or
    fails Pydantic validation (the writer's strict shape is the contract).
    """
    try:
        raw = state_path.read_text(encoding="utf-8")
    except OSError as e:
        raise RollbackError(f"failed to read state file {state_path}: {e}") from e
    try:
        return StateFile.model_validate_json(raw)
    except ValidationError as e:
        raise RollbackError(
            f"malformed state file {state_path}: {e}"
        ) from e


def restore_previous(
    project_path: Path | str = ".",
    *,
    only: Path | str | None = None,
) -> int:
    """Restore previous-state snapshots referenced by ``.kbat-state.json``.

    Args:
        project_path: directory containing the ``.kbat-state.json`` file
            (typically the manifest's project root). Defaults to cwd.
        only: if provided, restrict the restore to this single
            schematic path; other entries are ignored. The match is
            done by absolute-path string equality after both sides are
            resolved against ``project_path``.

    Returns:
        Number of files actually rewritten. Files already matching
        their snapshot's SHA-256 are skipped (idempotent re-run).

    Raises:
        RollbackError: state file missing, malformed, or a referenced
            snapshot does not exist on disk.
    """
    project = Path(project_path).resolve()
    state_path = project / STATE_FILENAME
    if not state_path.exists():
        raise RollbackError(
            f"no {STATE_FILENAME} found in {project} (nothing to roll back)"
        )

    state = _load_state(state_path)

    only_resolved: str | None = None
    if only is not None:
        only_resolved = str(Path(only).resolve())

    restored = 0
    restored_keys: list[str] = []
    for rel_key, entry in state.files.items():
        sch_path = (project / rel_key).resolve()
        if only_resolved is not None and str(sch_path) != only_resolved:
            continue

        if not entry.snapshot_path:
            # No snapshot recorded (e.g. dry-run record). Skip silently;
            # there's nothing to restore from.
            continue

        snapshot_path = (project / entry.snapshot_path).resolve()
        if not snapshot_path.exists():
            raise RollbackError(
                f"snapshot file missing for {sch_path}: {snapshot_path}"
            )

        # Idempotence + drift detection:
        #   - If on-disk bytes match the snapshot exactly: skip the copy
        #     AND leave the state entry alone (re-run of `kbat rollback`
        #     is a no-op).
        #   - If on-disk bytes match the recorded post-apply ``sha256``:
        #     the file is in its expected post-apply state; restore the
        #     snapshot bytes.
        #   - Otherwise: drift. The user hand-edited the schematic after
        #     apply; refuse rather than silently clobber their edits.
        if sch_path.exists():
            try:
                current_sha = sha256_file(sch_path)
            except OSError:
                current_sha = None
            if current_sha is not None:
                try:
                    snap_sha = sha256_file(snapshot_path)
                except OSError:
                    snap_sha = None
                if snap_sha is not None and current_sha == snap_sha:
                    continue
                if current_sha != entry.sha256:
                    raise RollbackError(
                        f"drift detected for {sch_path}: on-disk sha256 "
                        f"({current_sha[:12]}…) matches neither the recorded "
                        f"post-apply signature ({entry.sha256[:12]}…) nor the "
                        f"snapshot ({snap_sha[:12] if snap_sha else '?'}…) — "
                        "refusing to overwrite hand edits"
                    )

        sch_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot_path, sch_path)
        restored += 1
        restored_keys.append(rel_key)

    # If we successfully rolled back some entries, drop them from the
    # state file so the next `kbat apply` sees a clean slate (otherwise
    # the recorded post-apply sha256 would trip DriftDetectedError
    # against the just-restored pre-apply bytes). The state file is
    # rewritten in place rather than deleted — a re-run of `kbat rollback`
    # against an already-restored project should be a benign no-op,
    # not a "missing state file" error.
    if restored_keys:
        for k in restored_keys:
            state.files.pop(k, None)
        state.save(project)

    return restored


__all__ = [
    "RollbackError",
    "STATE_FILENAME",
    "restore_previous",
]

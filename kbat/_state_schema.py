"""Shared on-disk contract for ``.kbat-state.json`` (OSTH — One Schema, Two Halves).

Imported by both the writer (:class:`kbat._safety.KbatState`) and the reader
(:mod:`kbat._rollback`). Any change to the on-disk shape MUST land here
first; both halves are then updated against the new schema in the same
commit. Out-of-band edits to writer or reader without touching this
module are a contract violation enforceable by ``git grep``.

The legacy v0.1.0 reader (which expected ``schematics`` / ``schematic_paths``
wrappers, a per-entry ``snapshot`` key, and absolute path keys) is
intentionally *not* supported — no v0.1.0 code path ever wrote that
shape, so the deprecation cost is zero. See [[NsnG-0hGvsI3cnd48Ofym]]
for the three-tier divergence audit and [[uhjfJqjIT9oRjeBp5UpG6]] for
the OSTH policy this module operationalises.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

STATE_FILENAME = ".kbat-state.json"
STATE_SCHEMA_VERSION = 1


# ---- Helpers ---------------------------------------------------------------


def _relpath_posix(path: Path, base: Path) -> str:
    """Return POSIX-style path of ``path`` relative to ``base``.

    Resolves symlinks on both sides to avoid duplicate keys under
    ``/var/folders`` vs ``/private/var/folders`` etc. Falls back to
    the absolute POSIX form if ``path`` is not under ``base`` — the
    state file remains parseable, but rollback won't be able to
    resolve such entries portably.
    """
    try:
        rel = path.resolve().relative_to(base.resolve())
    except ValueError:
        return path.as_posix()
    return rel.as_posix()


def _sha256_file(path: Path, chunk_size: int = 64 * 1024) -> str:
    """Return hex sha256 of ``path``. Streamed; no full-file load.

    Kept in the schema module so the writer and reader compute hashes
    identically without either having to import the other. The canonical
    public helper lives at :func:`kbat._safety.sha256_file` and delegates
    here for parity.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---- Models ----------------------------------------------------------------


class TrackedFile(BaseModel):
    """Per-file signature plus pre-apply snapshot pointer.

    Field semantics:
      * ``mtime_ns`` — ``os.stat(path).st_mtime_ns`` at signing time
        (integer nanoseconds; NOT a float ``mtime``).
      * ``sha256`` — hex sha256 of the file at signing time (post-apply).
      * ``snapshot_path`` — POSIX-relative path under the project_dir
        to the pre-apply snapshot in ``.kbat-snapshots/``, or empty
        string if no snapshot was taken (e.g. dry-run). Stored
        relative (not absolute) so state files are portable across
        machines.

    ``extra="forbid"`` is the OSTH enforcement: unknown keys raise at
    parse time, preventing the next Conway-Law drift surface.
    """

    model_config = ConfigDict(extra="forbid")

    mtime_ns: int = Field(..., description="os.stat().st_mtime_ns at signing time")
    sha256: str = Field(..., min_length=64, max_length=64)
    snapshot_path: str = Field(
        "", description="POSIX-relative path under project_dir to the pre-apply snapshot"
    )

    # ---- Backwards-compat helpers (mirror the prior dataclass API) -------

    def to_dict(self) -> dict[str, object]:
        """Return the canonical wire dict. Equivalent to ``model_dump()``."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> TrackedFile:
        """Construct from a wire dict. Equivalent to ``model_validate(data)``."""
        return cls.model_validate(data)


class StateFile(BaseModel):
    """``.kbat-state.json`` on-disk root.

    On-disk shape (schema_version=1)::

        {
          "schema_version": 1,
          "last_apply_at": "2026-05-17T...Z",
          "files": {
            "kicad_experiment.kicad_sch": {
              "mtime_ns": 1234567890000000,
              "sha256": "abc123...",
              "snapshot_path": ".kbat-snapshots/abc123....kicad_sch"
            },
            ...
          }
        }

    Path keys are POSIX-style and relative to the project directory.
    ``extra="ignore"`` at the top level is forward-compat for v0.5+
    fields a v0.1 reader would otherwise reject; the per-entry strictness
    (via :class:`TrackedFile`) prevents drift on the load-bearing record
    shape.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = STATE_SCHEMA_VERSION
    last_apply_at: str = ""
    files: dict[str, TrackedFile] = Field(default_factory=dict)

    # ---- I/O -------------------------------------------------------------

    @classmethod
    def load(cls, project_dir: Path) -> StateFile | None:
        """Read ``project_dir/.kbat-state.json``.

        Returns ``None`` if the file does not exist. Raises
        ``pydantic.ValidationError`` (or ``json.JSONDecodeError`` /
        ``OSError``) on malformed content — callers may catch and
        wrap as needed (the writer's drift-detection path silently
        returns None for invalid state to avoid a wedge; the reader's
        rollback path raises :class:`RollbackError`).
        """
        path = Path(project_dir) / STATE_FILENAME
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        return cls.model_validate_json(raw)

    def save(self, project_dir: Path) -> None:
        """Persist via atomic write to ``project_dir/.kbat-state.json``.

        Delegates to :func:`kbat._safety.atomic_write` for crash-safe
        publish (lazy import to break the schema → safety dependency
        cycle).
        """
        from kbat._safety import atomic_write

        path = Path(project_dir) / STATE_FILENAME
        atomic_write(path, self.to_json())

    def to_json(self) -> str:
        """Serialise to indented JSON with stable key ordering.

        ``sort_keys`` (and the indent) keep state-file diffs minimal so
        ``git diff .kbat-state.json`` is readable.
        """
        payload = {
            "schema_version": self.schema_version,
            "last_apply_at": self.last_apply_at,
            "files": {k: self.files[k].model_dump() for k in sorted(self.files)},
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    # ---- Mutation --------------------------------------------------------

    def record(
        self,
        project_dir: Path,
        file_path: Path,
        snapshot_path: str = "",
    ) -> TrackedFile:
        """Capture ``mtime_ns`` + ``sha256`` of ``file_path`` into this state.

        ``snapshot_path`` is stored verbatim as the rollback target
        (POSIX-relative under ``project_dir``). Updates
        ``last_apply_at`` to the current UTC ISO-8601 timestamp.
        Returns the recorded :class:`TrackedFile`.
        """
        file_path = Path(file_path)
        project_dir = Path(project_dir)
        rel = _relpath_posix(file_path, project_dir)
        stat = file_path.stat()
        tf = TrackedFile(
            mtime_ns=stat.st_mtime_ns,
            sha256=_sha256_file(file_path),
            snapshot_path=snapshot_path,
        )
        self.files[rel] = tf
        self.last_apply_at = datetime.now(UTC).isoformat()
        return tf


__all__ = [
    "STATE_FILENAME",
    "STATE_SCHEMA_VERSION",
    "StateFile",
    "TrackedFile",
]

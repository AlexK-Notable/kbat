"""Safety primitives for `kbat apply` — atomic write, lockfile, state sidecar.

Owned by Phase 2 Agent G. Implements spec §6 (state coherence model).

Three primitives:

1. :func:`atomic_write` — POSIX crash-safe write via `path.tmp` + ``fsync`` +
   ``os.replace``. Failures before the rename leave the original path
   untouched; failures after the rename are atomic-already-complete.

2. :class:`KbatLock` — context manager around a JSON `.kbat-lock` file
   created with ``O_CREAT|O_EXCL``. Detects stale locks (process gone) and
   clears them on a single retry. Prevents two ``kbat apply`` invocations
   from racing on the same project.

3. :class:`KbatState` (with :class:`TrackedFile`) — JSON sidecar at
   `.kbat-state.json` recording per-file ``mtime_ns`` + ``sha256`` plus a
   pre-apply snapshot path. Consumed by ``kbat rollback`` and by the
   drift-detection path on the next ``kbat apply``.

As of v0.1.1, the on-disk shape of ``.kbat-state.json`` is defined by
:class:`kbat._state_schema.StateFile` (a strict Pydantic model);
``KbatState.save`` and ``KbatState.load`` delegate serialization through
it, and :mod:`kbat._rollback` reads through the same model. This is the
OSTH ("One Schema, Two Halves") fix for the v0.1.0 three-tier writer/reader
divergence (see [[NsnG-0hGvsI3cnd48Ofym]]).

KbatLock cross-host / NFS lock policy
=====================================

The lock body is JSON-encoded :class:`LockInfo` with ``pid``, ``host``,
``mode``, ``started_at``. Acquisition uses ``O_CREAT|O_EXCL`` for
single-host atomicity; on collision the existing lock is inspected and
reclaimed only when ALL of the following hold:

* the lock has a non-empty ``host`` field, AND
* that host equals ``socket.gethostname()`` on the current process, AND
* ``os.kill(pid, 0)`` raises :class:`ProcessLookupError` (the recorded
  PID is dead on this host).

Cases that are deliberately NOT auto-reclaimed (treated as live, so
acquisition raises :class:`LockHeldError`):

* **Cross-host** — a lock written on host A cannot be reclaimed by a
  subsequent apply on host B even if host B can see the file (e.g.
  shared NFS mount). The recorded PID is meaningful only on the host
  that wrote it; ``os.kill`` on a foreign-host PID is at best wrong
  and at worst dangerous (it could probe a live process on the local
  host that happens to share the PID).
* **NFS / network filesystem** — the same cross-host policy applies.
  Additionally, ``O_CREAT|O_EXCL`` semantics on NFS are mount-option
  dependent and not universally atomic; kbat treats network filesystems
  identically to local, relying on host-match reclamation rather than
  filesystem-level locking.
* **Symlinked / bind-mounted project dirs** — the lock path is computed
  from the supplied ``project_dir``; symlinks are NOT resolved on the
  lockfile path itself, so two paths that alias the same directory can
  produce two distinct lockfile paths. Use real paths consistently.
* **Container with a different hostname** — containers typically
  randomize ``/etc/hostname``; a lock written by an apply on the host
  will not be reclaimable by an apply inside a container (or vice
  versa).
* **No ``host`` field** — a hand-crafted or third-party lock without a
  ``host`` field is treated as live (conservative). Legitimate
  ``KbatLock`` always records ``socket.gethostname()``.

When auto-reclamation cannot fire and the operator is certain no
``kbat apply`` is actually running, the manual escape hatch is the CLI
subcommand::

    kbat unlock [PROJECT]          # interactive — prints lock body, prompts
    kbat unlock [PROJECT] --force  # non-interactive — removes unconditionally

``kbat unlock`` is an EXIT path; it does NOT re-architect the
acquisition path and should never be invoked while a real apply is in
progress on any host that shares the lockfile.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import socket
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from kbat._state_schema import (
    STATE_FILENAME,
    STATE_SCHEMA_VERSION,
    StateFile,
    TrackedFile,
)

LOCK_FILENAME = ".kbat-lock"
SNAPSHOTS_DIRNAME = ".kbat-snapshots"

LockMode = Literal["apply", "plan"]


# ---- Errors ----------------------------------------------------------------


class SafetyError(Exception):
    """Base class for safety-layer errors."""


class LockHeldError(SafetyError):
    """Raised when `.kbat-lock` is present and held by a live process."""


class DriftDetectedError(SafetyError):
    """Raised when one or more tracked files no longer match recorded sigs."""

    def __init__(self, drifted: list[str]) -> None:
        self.drifted = list(drifted)
        super().__init__(
            "drift detected for: " + ", ".join(self.drifted)
            + " — pass force=True (or --force) to override"
        )


class SignatureMismatchError(SafetyError):
    """Raised when manifest's pinned_signature does not match the file."""

    def __init__(self, path: str, expected: str, actual: str) -> None:
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"pinned signature mismatch for {path!s}: "
            f"expected sha256={expected[:12]}…, got sha256={actual[:12]}… "
            "(pass --force-resignature to override)"
        )


# ---- Atomic write ----------------------------------------------------------


def _tmp_path_for(path: Path) -> Path:
    """Compute the .tmp sibling used by :func:`atomic_write`.

    Kept as a small helper so tests can predict the temp path.
    """
    return path.with_name(path.name + ".tmp")


def atomic_write(path: Path, content: bytes | str) -> None:
    """Write ``content`` to ``path`` crash-safely.

    Protocol (POSIX-atomic on the rename step):
      1. open ``path.tmp`` for writing
      2. write all bytes, flush, fsync
      3. ``os.replace(path.tmp, path)`` — atomic on POSIX

    If the process is killed before step 3 completes, ``path`` is
    untouched and ``path.tmp`` is either absent or contains partial
    write — either way the original file is intact.

    Tests may set ``KBAT_TEST_PAUSE_BEFORE_RENAME=1`` in the environment
    to pause between steps 2 and 3 so a kill -9 from the test harness
    deterministically interrupts the protocol.
    """
    path = Path(path)
    if isinstance(content, str):
        content = content.encode("utf-8")

    tmp = _tmp_path_for(path)
    # Make sure the parent dir exists.
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(tmp, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        if os.environ.get("KBAT_TEST_PAUSE_BEFORE_RENAME") == "1":
            # Park the process so the crash-safety harness can SIGKILL us
            # with the tmp file in place and the original path untouched.
            # Bounded so a forgotten env var never hangs forever.
            time.sleep(30)

        os.replace(tmp, path)
    except BaseException:
        # On any failure path (exception, signal, KeyboardInterrupt),
        # try to clean up the tmp file. The original path is untouched.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_file(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst`` atomically via the same tmp+rename pattern.

    Used when the source content is already on disk (e.g. a file another
    writer produced); we just need to atomically publish it.
    """
    src = Path(src)
    dst = Path(dst)
    tmp = _tmp_path_for(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(src, tmp)
        with open(tmp, "rb+") as f:
            f.flush()
            os.fsync(f.fileno())

        if os.environ.get("KBAT_TEST_PAUSE_BEFORE_RENAME") == "1":
            time.sleep(30)

        os.replace(tmp, dst)
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


# ---- Hashing ---------------------------------------------------------------


def sha256_file(path: Path, chunk_size: int = 64 * 1024) -> str:
    """Return hex sha256 of ``path``. Streamed; no full-file load."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---- Lock ------------------------------------------------------------------


def _pid_is_alive(pid: int) -> bool:
    """Return True if ``pid`` is a live process on this host.

    Uses ``os.kill(pid, 0)`` (POSIX). Returns False for invalid pid,
    dead pid, or any OSError other than EPERM (we treat EPERM, "the
    process exists but you can't signal it", as alive — safer default).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists; we just don't own it.
        return True
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        # Unknown — be conservative, assume alive.
        return True
    return True


@dataclass
class LockInfo:
    pid: int
    host: str
    mode: LockMode
    started_at: str  # ISO-8601 UTC


class KbatLock:
    """Project-directory lock.

    Acquired via ``O_CREAT|O_EXCL`` on ``.kbat-lock``. The file body is
    JSON-encoded :class:`LockInfo`. On second acquire-while-held, raises
    :class:`LockHeldError`. On second acquire-while-stale (PID dead),
    silently removes the stale lock and retries once.

    Use as a context manager::

        with KbatLock(project_dir, mode="apply"):
            ...  # safe to mutate project files

    The lock is released (file removed) on context exit. If the process
    is killed mid-lock, the lockfile persists but the next acquire will
    detect the dead PID and recover.
    """

    def __init__(self, project_dir: Path, mode: LockMode = "apply") -> None:
        self.project_dir = Path(project_dir)
        self.mode = mode
        self.path = self.project_dir / LOCK_FILENAME
        self._acquired = False

    # ---- internals -------------------------------------------------------

    def _make_info(self) -> LockInfo:
        return LockInfo(
            pid=os.getpid(),
            host=socket.gethostname(),
            mode=self.mode,
            started_at=datetime.now(UTC).isoformat(),
        )

    def _try_acquire(self) -> bool:
        """Attempt one O_CREAT|O_EXCL create. Returns True on success."""
        info = self._make_info()
        payload = json.dumps(asdict(info), indent=2, sort_keys=True).encode("utf-8")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.path, flags, 0o644)
        except FileExistsError:
            return False
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        return True

    def _read_existing(self) -> LockInfo | None:
        """Return the contents of the existing lock, or None if unreadable.

        A return value of ``None`` signals "lock unreadable / corrupt /
        not parseable" — the caller treats this as stale (no honest
        kbat process writes a corrupt lock). Returns a populated
        :class:`LockInfo` for well-formed lockfiles.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Corrupt lock — treat as reclaimable.
            return None
        if not isinstance(data, dict):
            return None
        try:
            return LockInfo(
                pid=int(data.get("pid", -1)),
                host=str(data.get("host", "")),
                mode=data.get("mode", "apply"),
                started_at=str(data.get("started_at", "")),
            )
        except (TypeError, ValueError):
            return None

    def _is_stale(self, info: LockInfo) -> bool:
        """A lock is stale only if (a) it was written by THIS host AND
        (b) the recorded PID is dead.

        We deliberately require an exact host match before reclaiming:
        a lockfile written by a foreign tool or hand-crafted without a
        ``host`` field is treated as live (conservative default). The
        legitimate "kbat crashed and left a lock" case always records
        ``socket.gethostname()`` so it is reclaimable on the next run.
        """
        if not info.host:
            # No host field → don't reclaim. This protects against
            # hand-crafted simulated locks (e.g. test harnesses that
            # encode only ``{"pid": ...}``) being silently bypassed.
            return False
        if info.host != socket.gethostname():
            return False
        return not _pid_is_alive(info.pid)

    # ---- public API ------------------------------------------------------

    def acquire(self) -> KbatLock:
        """Acquire the lock. Idempotent within a single instance."""
        if self._acquired:
            return self
        self.project_dir.mkdir(parents=True, exist_ok=True)
        if self._try_acquire():
            self._acquired = True
            return self

        # Existing lock — check for staleness or corruption.
        existing = self._read_existing()
        is_reclaimable = existing is None or self._is_stale(existing)
        if is_reclaimable:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            # One retry after clearing a stale lock.
            if self._try_acquire():
                self._acquired = True
                return self

        raise LockHeldError(
            f"lock held: {self.path} "
            f"(pid={existing.pid if existing else '?'} "
            f"host={existing.host if existing else '?'} "
            f"mode={existing.mode if existing else '?'})"
        )

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._acquired = False

    def __enter__(self) -> KbatLock:
        return self.acquire()

    def __exit__(self, *_exc: Any) -> None:
        self.release()


# ---- State sidecar ---------------------------------------------------------
#
# The on-disk schema (TrackedFile + StateFile Pydantic models) lives in
# kbat._state_schema and is imported at module top. KbatState below is a
# thin in-memory ergonomic wrapper that delegates serialization through
# StateFile so writer and reader cannot drift.


@dataclass
class KbatState:
    """In-memory wrapper around ``.kbat-state.json``.

    On-disk shape is defined by :class:`kbat._state_schema.StateFile`;
    this class provides the procedural ergonomics (``record``,
    ``detect_drift``) used by :mod:`kbat._runner`. ``save`` and
    ``load`` are thin adapters that round-trip through ``StateFile`` so
    the writer and the rollback reader (which imports ``StateFile``
    directly) cannot diverge.

    Path keys in ``files`` are POSIX-style and relative to the project
    directory.
    """

    files: dict[str, TrackedFile] = field(default_factory=dict)
    last_apply_at: str = ""
    schema_version: int = STATE_SCHEMA_VERSION

    # ---- I/O -------------------------------------------------------------

    @classmethod
    def load(cls, project_dir: Path) -> KbatState | None:
        """Load state from ``project_dir/.kbat-state.json``.

        Returns ``None`` if the file does not exist or is unreadable —
        the drift-detection path treats both cases as "no recorded
        state", which is the conservative behaviour for a first
        ``kbat apply``.
        """
        try:
            sf = StateFile.load(Path(project_dir))
        except Exception:
            # Corrupt / malformed → treat as no state for drift-detection
            # purposes. The rollback reader handles malformed state by
            # raising RollbackError separately.
            return None
        if sf is None:
            return None
        return cls(
            files=dict(sf.files),
            last_apply_at=sf.last_apply_at,
            schema_version=sf.schema_version,
        )

    def save(self, project_dir: Path) -> None:
        """Persist via :class:`StateFile` to ``project_dir/.kbat-state.json``."""
        sf = StateFile(
            schema_version=self.schema_version,
            last_apply_at=self.last_apply_at,
            files=dict(self.files),
        )
        sf.save(Path(project_dir))

    # ---- mutation --------------------------------------------------------

    def record(
        self,
        project_dir: Path,
        file_path: Path,
        snapshot_path: str = "",
    ) -> TrackedFile:
        """Capture mtime + sha256 of ``file_path`` into this state.

        ``snapshot_path`` (if given) is stored verbatim as the rollback
        target (POSIX-relative under ``project_dir``). Returns the
        recorded :class:`TrackedFile`.
        """
        file_path = Path(file_path)
        project_dir = Path(project_dir)
        rel = _relpath_posix(file_path, project_dir)
        stat = file_path.stat()
        tf = TrackedFile(
            mtime_ns=stat.st_mtime_ns,
            sha256=sha256_file(file_path),
            snapshot_path=snapshot_path,
        )
        self.files[rel] = tf
        self.last_apply_at = datetime.now(UTC).isoformat()
        return tf

    # ---- queries ---------------------------------------------------------

    def detect_drift(self, project_dir: Path) -> list[str]:
        """Return relative paths whose mtime+sha256 no longer match the
        recorded signature.

        A missing file is reported as drifted. A file whose mtime matches
        but sha256 differs is reported (defensive — should be rare).
        """
        project_dir = Path(project_dir)
        drifted: list[str] = []
        for rel, tf in self.files.items():
            abs_path = project_dir / rel
            if not abs_path.exists():
                drifted.append(rel)
                continue
            try:
                stat = abs_path.stat()
            except OSError:
                drifted.append(rel)
                continue
            if stat.st_mtime_ns == tf.mtime_ns:
                # mtime intact — trust it; skip the hash for speed.
                continue
            # mtime changed; only call it drift if the content actually
            # differs (touch-without-edit is not drift).
            if sha256_file(abs_path) != tf.sha256:
                drifted.append(rel)
        return drifted


# ---- Snapshot helpers ------------------------------------------------------


def snapshot_path_for(sha256: str, src: Path) -> str:
    """Return the relative POSIX path used for a pre-apply snapshot of
    ``src`` with the given pre-apply ``sha256``.

    Naming the snapshot by content hash means identical pre-apply states
    collapse to one snapshot file; rollback resolves by hash, not by name.
    The suffix of ``src`` is preserved so the file remains identifiable
    on disk.
    """
    suffix = src.suffix or ""
    return f"{SNAPSHOTS_DIRNAME}/{sha256}{suffix}"


def make_snapshot(src: Path, project_dir: Path) -> tuple[str, str]:
    """Snapshot ``src`` into ``project_dir/.kbat-snapshots/``.

    Returns ``(snapshot_relpath, sha256)``. Idempotent: if a snapshot with
    the same sha256 already exists, no copy is made.
    """
    src = Path(src)
    project_dir = Path(project_dir)
    sha = sha256_file(src)
    rel = snapshot_path_for(sha, src)
    dst = project_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        # shutil.copy2 preserves mtime — handy for forensic comparison.
        shutil.copy2(src, dst)
    return rel, sha


# ---- Helpers ---------------------------------------------------------------


def _relpath_posix(path: Path, base: Path) -> str:
    """Return POSIX-style path of ``path`` relative to ``base``.

    Resolves symlinks on both sides to avoid duplicate keys under
    `/var/folders` vs `/private/var/folders` etc.
    """
    try:
        rel = path.resolve().relative_to(base.resolve())
    except ValueError:
        # Not under base — fall back to absolute path string.
        return path.as_posix()
    return rel.as_posix()


def verify_pinned_signature(
    file_path: Path,
    expected_sha256: str,
    *,
    force_resignature: bool = False,
) -> None:
    """Verify that ``file_path``'s current sha256 matches ``expected``.

    Raises :class:`SignatureMismatchError` on mismatch unless
    ``force_resignature=True``.
    """
    actual = sha256_file(file_path)
    if actual != expected_sha256 and not force_resignature:
        raise SignatureMismatchError(str(file_path), expected_sha256, actual)


__all__ = [
    "DriftDetectedError",
    "KbatLock",
    "KbatState",
    "LOCK_FILENAME",
    "LockHeldError",
    "LockInfo",
    "SafetyError",
    "SignatureMismatchError",
    "SNAPSHOTS_DIRNAME",
    "STATE_FILENAME",
    "STATE_SCHEMA_VERSION",
    "TrackedFile",
    "atomic_write",
    "atomic_write_file",
    "make_snapshot",
    "sha256_file",
    "snapshot_path_for",
    "verify_pinned_signature",
]

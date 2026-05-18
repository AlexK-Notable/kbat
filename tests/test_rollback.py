"""Unit tests for :mod:`kbat._rollback` (snapshot restore).

These tests drive the *real* state-file writer (``KbatState`` via the
shared :mod:`kbat._state_schema`) so that the reader+writer contract is
exercised by construction. Per the OSTH ("One Schema, Two Halves")
policy ([[uhjfJqjIT9oRjeBp5UpG6]] §2 row 3), fabricating a hand-rolled
reader-shaped JSON literal here would replay the v0.1.0 rollback bug —
the writer's actual on-disk shape is the only valid test input.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kbat._rollback import (
    STATE_FILENAME,
    RollbackError,
    restore_previous,
)
from kbat._safety import KbatState


def _seed_apply_state(
    project: Path,
    *,
    sch_rel: str = "board.kicad_sch",
    pre_apply_content: str = "original state\n",
    post_apply_content: str = "current state\n",
) -> tuple[Path, Path]:
    """Simulate one ``kbat apply``: snapshot ``pre_apply_content``, write
    ``post_apply_content`` to the schematic, then persist a real
    ``.kbat-state.json`` via :class:`KbatState`.

    Returns ``(sch_path, snapshot_abs_path)``.
    """
    sch = project / sch_rel
    sch.parent.mkdir(parents=True, exist_ok=True)
    # Pre-apply state goes into .kbat-snapshots/ (POSIX-relative under project).
    snap_dir = project / ".kbat-snapshots"
    snap_dir.mkdir(exist_ok=True)
    snap = snap_dir / "pre.kicad_sch"
    snap.write_text(pre_apply_content, encoding="utf-8")
    snap_rel = snap.relative_to(project).as_posix()
    # Post-apply state lives at the schematic path.
    sch.write_text(post_apply_content, encoding="utf-8")
    # Real writer captures (sha256, mtime_ns, snapshot_path) — exactly
    # the same code path that runs in production via _runner._run_inner.
    state = KbatState()
    state.record(project, sch, snapshot_path=snap_rel)
    state.save(project)
    return sch, snap


def test_restore_previous_copies_snapshot_back(tmp_path: Path) -> None:
    """A single snapshot is copied back to the original path, count==1."""
    project = tmp_path / "proj"
    project.mkdir()
    sch, snap = _seed_apply_state(project)
    assert sch.read_text(encoding="utf-8") == "current state\n"

    restored = restore_previous(project)
    assert restored == 1
    assert sch.read_text(encoding="utf-8") == "original state\n"


def test_restore_previous_is_idempotent(tmp_path: Path) -> None:
    """Restoring twice does nothing the second time (count==0)."""
    project = tmp_path / "proj"
    project.mkdir()
    sch, _snap = _seed_apply_state(project)

    first = restore_previous(project)
    assert first == 1
    assert sch.read_text(encoding="utf-8") == "original state\n"

    # Second restore: file already matches snapshot → no copy, count==0.
    second = restore_previous(project)
    assert second == 0
    assert sch.read_text(encoding="utf-8") == "original state\n"


def test_restore_previous_only_filter(tmp_path: Path) -> None:
    """The ``only`` arg restricts restoration to one schematic path."""
    project = tmp_path / "proj"
    project.mkdir()
    # Two schematics, each recorded via the real writer in turn.
    sch_a, _snap_a = _seed_apply_state(
        project,
        sch_rel="a.kicad_sch",
        pre_apply_content="a-original\n",
        post_apply_content="a-current\n",
    )
    # _seed_apply_state's snapshot subpath "pre.kicad_sch" collides if we
    # call it twice — manually stage the second entry through the writer.
    sch_b = project / "b.kicad_sch"
    sch_b.write_text("b-current\n", encoding="utf-8")
    snap_b = project / ".kbat-snapshots" / "pre_b.kicad_sch"
    snap_b.write_text("b-original\n", encoding="utf-8")
    state = KbatState.load(project) or KbatState()
    state.record(
        project,
        sch_b,
        snapshot_path=snap_b.relative_to(project).as_posix(),
    )
    state.save(project)

    restored = restore_previous(project, only=sch_a)
    assert restored == 1
    assert sch_a.read_text(encoding="utf-8") == "a-original\n"
    # b was filtered out, so it stays at its "current" content.
    assert sch_b.read_text(encoding="utf-8") == "b-current\n"


def test_restore_previous_missing_state_file_raises(tmp_path: Path) -> None:
    """If ``.kbat-state.json`` is absent, raise :class:`RollbackError`."""
    project = tmp_path / "empty_proj"
    project.mkdir()
    with pytest.raises(RollbackError, match="no .kbat-state.json"):
        restore_previous(project)


def test_restore_previous_missing_snapshot_raises(tmp_path: Path) -> None:
    """Referenced snapshot must exist on disk; otherwise raise."""
    project = tmp_path / "proj"
    project.mkdir()
    sch, snap = _seed_apply_state(project)
    # Delete the snapshot after recording it — simulates a user nuking
    # ``.kbat-snapshots/`` between apply and rollback.
    snap.unlink()
    with pytest.raises(RollbackError, match="snapshot file missing"):
        restore_previous(project)


def test_restore_previous_forward_compat_ignores_unknown_top_level_keys(
    tmp_path: Path,
) -> None:
    """``StateFile`` is ``extra="ignore"`` at the top level — a v0.5
    state file with a new field must still be readable by a v0.1.1 rollback.

    Per-entry strictness is exercised in
    ``tests/test_safety.py::test_tracked_file_rejects_extra_fields``.
    """
    import json

    project = tmp_path / "proj"
    project.mkdir()
    sch, _snap = _seed_apply_state(project)

    # Inject a future-only top-level key into the on-disk state file
    # and confirm the reader still loads it.
    state_path = project / STATE_FILENAME
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw["future_only_field"] = {"introduced_in": "v0.5"}
    state_path.write_text(json.dumps(raw), encoding="utf-8")

    restored = restore_previous(project)
    assert restored == 1
    assert sch.read_text(encoding="utf-8") == "original state\n"

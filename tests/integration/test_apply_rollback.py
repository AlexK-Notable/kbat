"""End-to-end apply→rollback integration tests for the OSTH cure.

These tests exercise the writer/reader contract for ``.kbat-state.json``
through a real :func:`kbat._runner.run` invocation against a real
schematic (written by the akcli backend), followed by a real
:func:`kbat._rollback.restore_previous` call.

The pre-OSTH bug
----------------

In v0.1.0 the writer (:class:`kbat._safety.KbatState`) and the reader
(:mod:`kbat._rollback`) shipped two divergent docstrings:

- writer keys under ``files.{posix-relpath}`` with fields
  ``{mtime_ns, sha256, snapshot_path}``
- reader expected either flat ``{abs_path: entry}`` or nested
  ``{"schematics": {abs_path: entry}}`` with fields
  ``{snapshot, sha256, mtime}``

Result: every ``kbat apply`` produced a state file that
``kbat rollback`` could not read. The two halves were each unit-tested
in isolation against their own imagined shape, so nothing caught the
divergence at the boundary.

These tests pin the unified contract: after :func:`run`, the state
file MUST be readable by :func:`restore_previous` and the schematic
MUST be byte-restorable. The single shared shape is governed by
``kbat/_state_schema.py`` (the OSTH artifact) and shared by both
halves via import.

Mock policy
-----------

No mocking of the akcli backend, no mocking of the filesystem. The
only monkey-patch is in
:func:`test_apply_partial_failure_cleans_orphan_snapshot`, which
injects a fault into one op to force the runner's partial-failure
branch — annotated MOCK_EXEMPT below.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from kbat import _rollback, _runner
from kbat._safety import SNAPSHOTS_DIRNAME, STATE_FILENAME

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "integration"
MINIMAL_SCH = FIXTURE_DIR / "minimal.kicad_sch"
MINIMAL_MANIFEST = FIXTURE_DIR / "minimal.kbat.yaml"

# A deterministic single-transform manifest used by the idempotence test.
# The ``add_no_connect`` op mints a fresh UUID per NC marker on each
# invocation, so a multi-op manifest is NOT byte-deterministic across
# apply→rollback→apply. ``set_property`` IS deterministic (direct attribute
# write, no UUID minting), so the idempotence test uses a one-transform
# manifest. See "Flags for Investigation" in the phase note for the
# non-determinism finding.
_DETERMINISTIC_MANIFEST_TEXT = """\
apiVersion: kbat.dev/v1alpha1
kind: Project
metadata:
  name: minimal_idempotence
  schematic: minimal.kicad_sch
transforms:
  - id: rename-r1
    op: set_property
    selector:
      reference: R1
    property: Value
    value: "2k2"
"""


# ---- shared helpers ---------------------------------------------------------


def _materialize_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Copy the integration fixture pair into ``tmp_path/project``.

    Returns ``(project_dir, schematic_path, manifest_path)``. The
    manifest's ``schematic`` field is a relative path; the runner is
    invoked with ``schematic_override=schematic_path`` so the test
    harness controls path resolution independent of cwd.
    """
    if not MINIMAL_SCH.exists() or not MINIMAL_MANIFEST.exists():
        pytest.skip(f"integration fixtures missing under {FIXTURE_DIR}")
    project = tmp_path / "project"
    project.mkdir()
    sch = project / "minimal.kicad_sch"
    manifest = project / "minimal.kbat.yaml"
    shutil.copy2(MINIMAL_SCH, sch)
    shutil.copy2(MINIMAL_MANIFEST, manifest)
    return project, sch, manifest


def _materialize_deterministic_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Like :func:`_materialize_project` but with a single-transform manifest
    so the cycle apply→rollback→apply is byte-deterministic.
    """
    if not MINIMAL_SCH.exists():
        pytest.skip(f"integration fixture missing: {MINIMAL_SCH}")
    project = tmp_path / "project"
    project.mkdir()
    sch = project / "minimal.kicad_sch"
    manifest = project / "minimal.kbat.yaml"
    shutil.copy2(MINIMAL_SCH, sch)
    manifest.write_text(_DETERMINISTIC_MANIFEST_TEXT, encoding="utf-8")
    return project, sch, manifest


# ---- Happy path -------------------------------------------------------------


def test_apply_then_rollback_restores_original_bytes(tmp_path: Path) -> None:
    """``apply`` mutates the schematic; ``rollback`` restores byte-exact original.

    This is THE headline regression for the OSTH cure. Pre-fix:
    ``restore_previous`` raised ``RollbackError("missing 'snapshot' key")``
    because it parsed the writer's ``files.<rel>.{snapshot_path,...}``
    shape using the reader's imagined ``{abs: {snapshot, ...}}`` schema.

    Post-fix: both halves import ``kbat._state_schema``; the apply→rollback
    cycle yields the original bytes.
    """
    project, sch, manifest = _materialize_project(tmp_path)
    original_bytes = sch.read_bytes()

    # 1. Apply mutates the schematic.
    result = _runner.run(manifest, schematic_override=sch)
    assert result.errors == [], f"unexpected errors: {result.errors}"
    assert result.applied >= 1, "manifest should apply at least one transform"
    assert sch.read_bytes() != original_bytes, "apply should mutate the schematic"

    # 2. State file shape (the writer's contract — the OSTH artifact).
    state_path = project / STATE_FILENAME
    assert state_path.exists(), "apply should produce a state file"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "files" in state, (
        f"state file should expose 'files' key (the OSTH writer contract); "
        f"got top-level keys={sorted(state.keys())}"
    )
    assert "minimal.kicad_sch" in state["files"], (
        f"state.files key should be POSIX-relative path, not absolute; "
        f"got keys={sorted(state['files'].keys())}"
    )
    entry = state["files"]["minimal.kicad_sch"]
    assert "snapshot_path" in entry, (
        f"entry should carry 'snapshot_path' (not 'snapshot'); got fields={sorted(entry.keys())}"
    )
    assert "mtime_ns" in entry, (
        f"entry should carry 'mtime_ns' (int, not 'mtime' float); "
        f"got fields={sorted(entry.keys())}"
    )
    assert "sha256" in entry
    assert isinstance(entry["mtime_ns"], int), "mtime_ns must be int nanoseconds"
    assert len(entry["sha256"]) == 64, "sha256 must be hex-encoded sha256 digest"

    # 3. Rollback restores byte-exact original. This is the assertion that
    #    fails pre-OSTH because the reader cannot parse the writer's shape.
    count = _rollback.restore_previous(project)
    assert count == 1, f"exactly one file should be restored, got count={count}"
    assert sch.read_bytes() == original_bytes, (
        "rollback should restore byte-exact original schematic"
    )


# ---- Drift detection -------------------------------------------------------


def test_rollback_after_drift_raises(tmp_path: Path) -> None:
    """If the schematic drifted between apply and rollback, rollback
    raises :class:`RollbackError` rather than clobbering user edits.

    Contract: :func:`kbat._rollback.restore_previous` compares the
    on-disk sha256 against the recorded post-apply signature
    (``entry.sha256``) AND against the snapshot bytes. If the on-disk
    content matches neither, the user has hand-edited the file and a
    rollback would silently destroy those edits — so the reader
    refuses with a descriptive ``RollbackError``.

    Bug-catching power: protects against a class of silent
    data-loss bugs where a careless ``kbat rollback`` after a manual
    edit overwrites the user's work. The error message must mention
    drift so the user knows the recovery path (e.g. take their own
    backup before re-running).
    """
    project, sch, manifest = _materialize_project(tmp_path)

    # 1. Apply normally (writes state file + snapshot).
    result = _runner.run(manifest, schematic_override=sch)
    assert result.errors == []
    assert result.applied >= 1
    post_apply_bytes = sch.read_bytes()

    # 2. Simulate a user editing the post-apply schematic (drift).
    drift_bytes = post_apply_bytes + b"\n; user hand-edit after apply\n"
    sch.write_bytes(drift_bytes)

    # 3. Rollback MUST refuse: the on-disk content matches neither the
    #    snapshot nor the recorded post-apply signature.
    with pytest.raises(_rollback.RollbackError, match="(?i)drift"):
        _rollback.restore_previous(project)

    # 4. The user's edit was NOT overwritten — the file is still the
    #    drifted bytes, unmodified by the failed rollback attempt.
    assert sch.read_bytes() == drift_bytes, (
        "a refused rollback must not mutate the on-disk file"
    )


# ---- Inverse property ------------------------------------------------------


def test_apply_rollback_apply_idempotent(tmp_path: Path) -> None:
    """The cycle ``apply → rollback → apply`` is byte-exact for a
    deterministic manifest.

    Property formulation: given a deterministic manifest M and starting
    bytes S, ``apply(M, S)`` produces bytes A; after ``rollback`` the
    bytes return to S; ``apply(M, S)`` again must produce the same
    bytes A (byte-exact). This is the inverse-property formulation of
    rollback correctness — together with idempotence of the underlying
    ops, it's the contract that makes safe re-runs possible.

    Note: this test uses a *single-transform* manifest with
    ``set_property`` only, because some other ops (notably
    ``add_no_connect`` and ``add_pwr_flag``) mint a fresh UUID on each
    invocation and are not byte-deterministic. The non-determinism is
    flagged in the phase note for future investigation — separate from
    the OSTH contract this phase fixes.

    Bug-catching power: catches non-determinism in apply for
    ``set_property``, which is the workhorse op covered by every
    cleanup-recipe manifest in the wild. If a future change introduces
    a timestamp or environment-derived field into ``set_property``'s
    output, this test fails immediately. Also indirectly verifies that
    ``rollback`` clears the restored entry from ``.kbat-state.json`` —
    without that cleanup, the second apply would trip drift detection
    against the stale post-apply signature.
    """
    project, sch, manifest = _materialize_deterministic_project(tmp_path)
    original_bytes = sch.read_bytes()

    # First apply.
    result_a = _runner.run(manifest, schematic_override=sch)
    assert result_a.errors == [], f"unexpected errors on first apply: {result_a.errors}"
    assert result_a.applied >= 1
    bytes_a = sch.read_bytes()
    assert bytes_a != original_bytes, "first apply should mutate"

    # Rollback restores original AND clears the restored entry from the
    # state file (so the next apply doesn't trip drift detection).
    count = _rollback.restore_previous(project)
    assert count == 1, f"rollback should restore one file, got count={count}"
    assert sch.read_bytes() == original_bytes, "rollback should restore original bytes"

    # Second apply — no force= needed because rollback cleared the
    # restored entry from .kbat-state.json (this is part of the OSTH
    # contract: rollback is the inverse of apply, leaving the project
    # in a state where the next apply can run cleanly).
    result_b = _runner.run(manifest, schematic_override=sch)
    assert result_b.errors == [], f"unexpected errors on second apply: {result_b.errors}"
    bytes_b = sch.read_bytes()

    assert bytes_a == bytes_b, (
        "apply→rollback→apply should be byte-exact; "
        f"first apply produced {len(bytes_a)}B, second produced {len(bytes_b)}B"
    )


# ---- Orphan-snapshot prevention --------------------------------------------


def test_apply_partial_failure_cleans_orphan_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-apply failure must not leave an orphan snapshot on disk.

    Contract: per the gate note Check 1.10 and the orphan-snapshot leak
    diagnosis, ``_runner._run_inner`` currently snapshots BEFORE
    dispatch. If any transform raises, the snapshot lands on disk but
    the state file is NOT updated to point at it — leaving an orphan
    in ``.kbat-snapshots/`` that no rollback path can reference.

    The fix is either (a) snapshot AFTER successful dispatch, or
    (b) wrap dispatch in try/finally that unlinks orphans on failure.
    Either way the post-condition is: ``errors`` is non-empty AND
    ``.kbat-snapshots/`` is empty AND ``.kbat-state.json`` was NOT
    updated.

    Bug-catching power: prevents silent disk leakage when partial
    failures accumulate over many CI runs; preserves the atomic
    transaction integrity that the apply→rollback contract depends on.

    MOCK_EXEMPT: fault-injection to deterministically force a
    partial-failure branch in ``_runner._run_inner``. The injected
    failure target is :mod:`kbat.ops`, not the akcli backend — we
    short-circuit the dispatcher's call into the op layer to simulate
    "the second op raised mid-apply" without depending on op-specific
    failure modes (most ops gracefully no-op on bad input).
    """
    project, sch, manifest = _materialize_project(tmp_path)
    original_bytes = sch.read_bytes()
    assert not (project / SNAPSHOTS_DIRNAME).exists(), "fresh fixture has no snapshots"
    assert not (project / STATE_FILENAME).exists(), "fresh fixture has no state file"

    # Inject: make the SECOND op (add_no_connect) raise. The first
    # (set_property on R1) succeeds, then the dispatcher hits an
    # exception — the runner's partial-failure branch must clean up the
    # snapshot it took before dispatch.
    from kbat import ops as _ops

    def _boom_add_no_connect(*_args, **_kwargs):
        raise RuntimeError("simulated mid-apply failure")

    monkeypatch.setattr(_ops, "add_no_connect", _boom_add_no_connect)

    result = _runner.run(manifest, schematic_override=sch)

    # 1. The error surfaced and was recorded.
    assert result.errors, "runner should record the partial-failure error"
    assert any(
        "simulated mid-apply failure" in e for e in result.errors
    ), f"expected our injected error to surface; got errors={result.errors}"

    # 2. State file was NOT updated (atomic transaction integrity).
    assert not (project / STATE_FILENAME).exists(), (
        "state file must NOT be updated when apply has errors — "
        "the writer only persists state on a fully-successful apply"
    )

    # 3. No orphan snapshot left on disk.
    snap_dir = project / SNAPSHOTS_DIRNAME
    if snap_dir.exists():
        leftover = list(snap_dir.iterdir())
        assert leftover == [], (
            f"failed apply must not leave orphan snapshots in {snap_dir!s}; "
            f"found: {[p.name for p in leftover]}"
        )

    # 4. The schematic on disk is untouched (a failed apply rolls in-memory
    #    mutations away by simply not saving — the file remains original).
    assert sch.read_bytes() == original_bytes, (
        "a failed apply must not mutate the on-disk schematic"
    )

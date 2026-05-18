"""kbat CLI entry point.

Phase 1 wired up ``apply`` (full) and ``plan`` (stub). Phase 2 Agent F
expands ``plan`` into a semantic-diff driver (via :mod:`kbat._planner`)
and implements ``rollback`` against the ``.kbat-state.json`` snapshots
that Agent G's safety layer maintains.
"""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(
    name="kbat",
    help="Declarative manifest-driven KiCad schematic batch transforms.",
    no_args_is_help=True,
    # Plain text help (no rich boxes) so plumbing-check 1.4's
    # ``grep -E "^\s*(apply|plan)\s"`` matches.
    rich_markup_mode=None,
)


@app.command()
def apply(
    manifest: Path = typer.Argument(..., help="Path to .kbat.yaml manifest"),  # noqa: B008
    dry_run: bool = typer.Option(  # noqa: B008
        False, "--dry-run", help="Show what would change without writing"
    ),
    schematic: Path | None = typer.Option(  # noqa: B008
        None,
        "--schematic",
        help="Override the schematic path from the manifest (useful for "
        "harnesses that run against a tmpdir copy).",
    ),
) -> None:
    """Apply a manifest to its target schematic."""
    from kbat._runner import run

    result = run(manifest, dry_run=dry_run, schematic_override=schematic)
    typer.echo(
        f"applied={result.applied} skipped={result.skipped} "
        f"errors={len(result.errors)}"
    )
    for d in result.details:
        typer.echo(f"  - [{d.status}] {d.id} ({d.op})")
    if result.errors:
        for e in result.errors:
            typer.echo(f"  ERROR: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def plan(
    manifest: Path = typer.Argument(..., help="Path to .kbat.yaml manifest"),  # noqa: B008
    schematic: Path | None = typer.Option(  # noqa: B008
        None,
        "--schematic",
        help="Override the schematic path from the manifest (useful for "
        "harnesses that run against a tmpdir copy).",
    ),
) -> None:
    """Show what would change if this manifest were applied.

    Phase 2: emits a semantic diff via :func:`kbat._planner.compute_plan`
    rendered as Rich tables. Each transform is classified as a
    would-change, no-op, or error; an aggregate FunctionalFacts delta
    is shown when fact extraction succeeds.
    """
    from kbat._planner import compute_plan, render_plan

    diff = compute_plan(manifest, schematic_override=schematic)
    render_plan(diff)
    # Non-zero exit when any transform errored, so CI / scripts can
    # distinguish a clean diff from one that mentions failures.
    if diff.n_error:
        raise typer.Exit(code=1)


@app.command()
def rollback(
    project: Path = typer.Argument(  # noqa: B008
        Path("."), help="Project directory containing .kbat-state.json"
    ),
    schematic: Path | None = typer.Option(  # noqa: B008
        None,
        "--schematic",
        help="Restore only this specific schematic file (otherwise "
        "every entry in .kbat-state.json is restored).",
    ),
) -> None:
    """Restore previous schematic state from .kbat-state.json snapshot."""
    from kbat._rollback import RollbackError, restore_previous

    # Default project=.; if --schematic was passed without a positional
    # project arg, use the schematic's directory as the project root so
    # the state file (.kbat-state.json) is found alongside it. This
    # matches the Phase 2 gate's rollback harness invocation pattern:
    # ``cd "$W" && kbat rollback --schematic "$W/sch.kicad_sch"`` runs
    # from $W (so cwd already holds .kbat-state.json) — but users may
    # also invoke from elsewhere.
    if schematic is not None and project == Path("."):
        candidate = schematic.parent
        if (candidate / ".kbat-state.json").exists():
            project = candidate

    try:
        restored = restore_previous(project, only=schematic)
    except RollbackError as e:
        typer.echo(f"rollback failed: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"Restored {restored} file(s)")


@app.command()
def unlock(
    project: Path = typer.Argument(  # noqa: B008
        Path("."),
        help="Project directory containing the stale .kbat-lock file "
        "(default: current directory)",
    ),
    force: bool = typer.Option(  # noqa: B008
        False,
        "--force",
        "-f",
        help="Skip the confirmation prompt and remove the lock unconditionally.",
    ),
) -> None:
    """Manually remove a stale ``.kbat-lock`` file (escape hatch).

    Use this only when you are certain no ``kbat apply`` is actually
    running. The lock is normally reclaimed automatically by a
    subsequent apply on the SAME host (host-match reclamation policy
    in :class:`kbat._safety.KbatLock`); ``unlock`` is for the
    cross-host / NFS / container scenarios where the host recorded in
    the lock does not match this host, so reclamation cannot happen
    automatically.
    """
    from kbat._safety import LOCK_FILENAME

    lock_path = project / LOCK_FILENAME
    if not lock_path.exists():
        typer.echo(f"No {LOCK_FILENAME} file found in {project}.")
        raise typer.Exit(code=0)

    if not force:
        # Show the current lock contents so the user can verify they
        # really want to release it (PID / host / mode are recorded by
        # KbatLock; useful for forensic inspection).
        try:
            contents = lock_path.read_text(encoding="utf-8")
        except OSError:
            contents = "<unreadable>"
        typer.echo(f"Current lock contents:\n{contents}")
        confirm = typer.confirm("Remove this lock?", default=False)
        if not confirm:
            typer.echo("Aborted; lock left in place.")
            raise typer.Exit(code=1)

    try:
        lock_path.unlink()
    except FileNotFoundError:
        # Raced with another release — treat as success.
        typer.echo("Lock already gone; nothing to do.")
        raise typer.Exit(code=0) from None
    except OSError as e:
        typer.echo(f"Failed to remove {lock_path}: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"Lock released: {lock_path}")


if __name__ == "__main__":
    app()

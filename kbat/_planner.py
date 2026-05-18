"""Semantic-diff implementation for ``kbat plan`` (Phase 2 Agent F).

Where ``kbat apply`` mutates a schematic, ``kbat plan`` simulates the
manifest against an in-memory copy of the same schematic and reports,
per transform, what would change. The output is a Rich-rendered set of
tables that satisfies Phase 2 gate criterion 2.11 (≥ 5 op-keyword lines
across the diff).

The diff is grounded in :mod:`kbat._validation`'s
:class:`FunctionalFacts`: we capture facts before and after the
simulated apply and surface the per-field delta (components, labels,
no_connects, power_flags, footprint_assignments). This matches the
Phase 1 F refinement of criterion 1.G.b — "load-bearing functional
equivalence", not textual diff. The result is that ``kbat plan``
explains the manifest in terms the gate already trusts.

Public surface::

    compute_plan(manifest_path, *, schematic_override=None) -> PlanDiff
    render_plan(diff, *, console=None) -> None

The CLI calls these two functions; tests poke at :class:`PlanDiff`
directly.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from kbat._runner import dispatch_transform, load_manifest, resolve_schematic_path
from kbat._validation import FunctionalFacts, extract_functional_facts
from kbat.ops.schematic import relax_validation
from kbat.schema.v1alpha1 import Manifest

# ---- data classes -----------------------------------------------------------


@dataclass
class PlanEntry:
    """One transform's simulated outcome.

    ``status`` is one of ``"change"`` (would mutate), ``"noop"`` (target
    already matches), ``"error"`` (dispatch raised). ``summary`` is a
    short human-readable description of the transform target, mirroring
    the format used in the Phase 1 ``kbat plan`` stub so the gate's
    op-keyword grep continues to match.
    """

    index: int
    id: str
    op: str
    summary: str
    status: str  # "change" | "noop" | "error"
    error: str | None = None


@dataclass
class PlanDiff:
    """Result of simulating a manifest against a schematic.

    Carries the per-transform :class:`PlanEntry` list plus the
    aggregate FunctionalFacts delta (initial → final). The CLI renders
    both; tests assert on the structure.
    """

    manifest_path: Path
    schematic_path: Path
    entries: list[PlanEntry] = field(default_factory=list)
    initial_facts: FunctionalFacts | None = None
    final_facts: FunctionalFacts | None = None

    @property
    def n_change(self) -> int:
        return sum(1 for e in self.entries if e.status == "change")

    @property
    def n_noop(self) -> int:
        return sum(1 for e in self.entries if e.status == "noop")

    @property
    def n_error(self) -> int:
        return sum(1 for e in self.entries if e.status == "error")


# ---- helpers ---------------------------------------------------------------


def _transform_summary(t: Any) -> str:
    """Render a short human-readable target description for a transform.

    Each branch deliberately surfaces one of the gate's op-keywords
    (``set_property``, ``no_connect``, ``footprint``, ``snap``, ``label``,
    ``pwr_flag``) verbatim so check 2.11's grep matches per-line.
    """
    op = t.op
    if op == "set_property":
        return f"set_property {t.selector.reference}.{t.property} = {t.value!r}"
    if op == "add_no_connect":
        return f"no_connect {t.reference} pins={t.pins}"
    if op == "snap_labels_to_pins":
        return f"snap {t.reference} ({len(t.mapping)} label→pin snaps)"
    if op == "assign_footprints":
        return f"footprint assignments: {len(t.by_lib_id)} lib_id mapping(s)"
    if op == "delete_label_at":
        return f"label delete {t.net!r} at ({t.x}, {t.y})"
    if op == "move_label":
        return f"label move {t.net!r} ({t.from_x},{t.from_y})→({t.to_x},{t.to_y})"
    if op == "add_pwr_flag":
        return f"pwr_flag net {t.net!r} at ({t.x}, {t.y})"
    return f"{op} ?"


def _facts_delta(
    before: FunctionalFacts, after: FunctionalFacts
) -> dict[str, tuple[int, int]]:
    """Return per-field (additions, removals) counts between two FunctionalFacts."""
    return {
        "components": (
            len(after.components - before.components),
            len(before.components - after.components),
        ),
        "labels": (
            len(after.labels - before.labels),
            len(before.labels - after.labels),
        ),
        "no_connects": (
            len(after.no_connects - before.no_connects),
            len(before.no_connects - after.no_connects),
        ),
        "power_flags": (
            len(after.power_flags - before.power_flags),
            len(before.power_flags - after.power_flags),
        ),
        "footprint_assignments": (
            len(after.footprint_assignments - before.footprint_assignments),
            len(before.footprint_assignments - after.footprint_assignments),
        ),
    }


# ---- core -----------------------------------------------------------------


def compute_plan(
    manifest_path: Path | str,
    *,
    schematic_override: Path | str | None = None,
) -> PlanDiff:
    """Simulate a manifest against its target schematic.

    Loads the manifest, opens the schematic into a tmp-copy (never
    touches the original), dispatches each transform, and records
    whether it would-change or no-op. Also captures FunctionalFacts
    before any transform and after all transforms so callers can
    surface a high-level functional delta.

    Args:
        manifest_path: path to a .kbat.yaml manifest.
        schematic_override: optionally override the schematic named in
            the manifest (useful for tmpdir harnesses).

    Returns:
        :class:`PlanDiff` carrying entries + initial/final facts.
    """
    import kicad_sch_api as ksa

    manifest_p = Path(manifest_path)
    manifest: Manifest = load_manifest(manifest_p)

    if schematic_override is not None:
        sch_path = Path(schematic_override)
    else:
        sch_path = resolve_schematic_path(manifest, manifest_p)

    diff = PlanDiff(manifest_path=manifest_p, schematic_path=sch_path)

    # Capture pre-state facts from the original file.
    if sch_path.exists():
        try:
            diff.initial_facts = extract_functional_facts(sch_path)
        except Exception:  # noqa: BLE001
            # Fact extraction is best-effort; a parse failure here
            # should not abort the plan. The per-transform table is
            # still useful.
            diff.initial_facts = None

    # Simulate against an isolated tmp-copy so the on-disk file is
    # never touched. We open the schematic via kicad-sch-api just like
    # the runner does (so dispatch_transform's expectations hold).
    with tempfile.TemporaryDirectory(prefix="kbat_plan_") as td:
        if sch_path.exists():
            tmp_sch = Path(td) / sch_path.name
            shutil.copy2(sch_path, tmp_sch)
            sch = ksa.load_schematic(str(tmp_sch))
            relax_validation(sch)
        else:
            # Plan can still proceed: each transform error becomes a row.
            tmp_sch = None
            sch = None

        for idx, transform in enumerate(manifest.transforms, start=1):
            tid = getattr(transform, "id", None) or transform.op
            summary = _transform_summary(transform)
            if sch is None:
                diff.entries.append(
                    PlanEntry(
                        index=idx,
                        id=tid,
                        op=transform.op,
                        summary=summary,
                        status="error",
                        error=f"schematic not found: {sch_path}",
                    )
                )
                continue
            try:
                changed = dispatch_transform(sch, transform)
            except Exception as e:  # noqa: BLE001
                diff.entries.append(
                    PlanEntry(
                        index=idx,
                        id=tid,
                        op=transform.op,
                        summary=summary,
                        status="error",
                        error=str(e),
                    )
                )
                continue
            diff.entries.append(
                PlanEntry(
                    index=idx,
                    id=tid,
                    op=transform.op,
                    summary=summary,
                    status="change" if changed else "noop",
                )
            )

        # After all transforms run on the tmp copy, save it (so the
        # final facts reflect the post-state shape that kicad-sch-api
        # would actually serialise) and re-extract facts.
        if sch is not None and tmp_sch is not None:
            try:
                sch.save(str(tmp_sch))
                diff.final_facts = extract_functional_facts(tmp_sch)
            except Exception:  # noqa: BLE001
                diff.final_facts = None

    return diff


# ---- rendering -------------------------------------------------------------


def render_plan(diff: PlanDiff, *, console: Console | None = None) -> None:
    """Render a :class:`PlanDiff` as Rich tables to ``console``.

    Output structure (Phase 2 gate-compliant):

    1. Header line naming the manifest + schematic.
    2. Per-transform table: index, status, op, id, target summary.
       Each row's summary includes the op-keyword token so the gate's
       grep counts ≥ 5 lines for any non-trivial manifest.
    3. Aggregate summary line.
    4. (Optional) FunctionalFacts delta table if facts were captured.
    """
    console = console or Console()

    console.print(
        f"[bold]kbat plan[/bold] manifest=[cyan]{diff.manifest_path}[/cyan] "
        f"schematic=[cyan]{diff.schematic_path}[/cyan]"
    )

    table = Table(
        title=f"Transforms ({len(diff.entries)})",
        show_lines=False,
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("status")
    table.add_column("op")
    table.add_column("id")
    table.add_column("change")
    for e in diff.entries:
        if e.status == "change":
            status = "[yellow]would change[/yellow]"
        elif e.status == "noop":
            status = "[green]no-op[/green]"
        else:
            status = "[red]error[/red]"
        change_col = e.summary if e.error is None else f"{e.summary}  ERROR: {e.error}"
        table.add_row(str(e.index), status, e.op, e.id, change_col)
    console.print(table)

    console.print(
        f"Summary: [yellow]{diff.n_change} would change[/yellow], "
        f"[green]{diff.n_noop} no-op[/green], "
        f"[red]{diff.n_error} error[/red]"
    )

    if diff.initial_facts is not None and diff.final_facts is not None:
        delta = _facts_delta(diff.initial_facts, diff.final_facts)
        any_delta = any(adds or rems for adds, rems in delta.values())
        if any_delta:
            ft = Table(
                title="Functional delta (initial → final)",
                show_lines=False,
            )
            ft.add_column("field")
            ft.add_column("added", justify="right")
            ft.add_column("removed", justify="right")
            for field_name, (adds, rems) in delta.items():
                if adds == 0 and rems == 0:
                    continue
                ft.add_row(field_name, f"+{adds}", f"-{rems}")
            console.print(ft)
        else:
            console.print(
                "[green]No functional change predicted "
                "(manifest is a full no-op against this schematic).[/green]"
            )


__all__ = [
    "PlanDiff",
    "PlanEntry",
    "compute_plan",
    "render_plan",
]

"""akcli backend: read board state and apply op-lists via the akcli CLI.

kbat talks to akcli only through its command line and versioned JSON
(``protocol_version`` 2), never through its Python internals, so the
boundary stays stable across akcli releases and replaceable by another
backend. akcli is invoked as ``python -m akcli`` from kbat's own
interpreter, so it resolves to the ``akcli-kicad`` dependency.

Reading: components and pin endpoints come from ``akcli --json read``
(pin world coordinates need the symbol transform, which akcli already
does). Labels and no-connects are read straight from the file with
sexpdata, because ``read`` reports nets rather than individual labels.

Writing: one ``akcli --json draw --apply`` call per kbat apply. akcli
writes snapshot -> temp -> verify -> replace, so the batch is atomic.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sexpdata

from kbat.backend.state import BoardState, Component, Label

#: akcli op-list protocol this backend emits.
PROTOCOL_VERSION = 2

MM_PER_MIL = 0.0254


class BackendError(Exception):
    """akcli refused or failed; carries akcli's own code and remediation."""

    def __init__(self, message: str, *, code: str | None = None, remediation: str | None = None):
        super().__init__(message)
        self.code = code
        self.remediation = remediation


def to_mil(mm: float) -> int | float:
    """Millimetres to akcli mils, as an exact int when on a whole mil."""
    v = round(mm / MM_PER_MIL, 3)
    return int(round(v)) if abs(v - round(v)) < 1e-6 else v


def to_mm(mil: float) -> float:
    return round(mil * MM_PER_MIL, 4)


def _akcli_cmd() -> list[str]:
    override = os.environ.get("KBAT_AKCLI")
    return [override] if override else [sys.executable, "-m", "akcli"]


def _run_akcli(args: list[str], *, check_json: bool = True) -> dict[str, Any]:
    proc = subprocess.run(
        [*_akcli_cmd(), "--json", *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if proc.returncode != 0:
        err = payload.get("error", {}) if isinstance(payload, dict) else {}
        raise BackendError(
            err.get("message") or proc.stderr.strip() or f"akcli exited {proc.returncode}",
            code=err.get("code"),
            remediation=err.get("remediation"),
        )
    if check_json and not payload:
        raise BackendError(f"akcli {args[0]} returned no JSON")
    return payload


# ---- reading ------------------------------------------------------------------


def _sym(v: Any) -> str:
    return v.value() if isinstance(v, sexpdata.Symbol) else str(v)


def _effects_str(node: list[Any]) -> str:
    for child in node[1:]:
        if isinstance(child, list) and child and _sym(child[0]) == "effects":
            return sexpdata.dumps(child)
    return ""


def _read_labels_and_ncs(path: Path) -> tuple[list[Label], list[tuple[float, float]]]:
    tree = sexpdata.loads(path.read_text(encoding="utf-8"))
    labels: list[Label] = []
    ncs: list[tuple[float, float]] = []
    for node in tree[1:]:
        if not isinstance(node, list) or not node:
            continue
        head = _sym(node[0])
        at = next(
            (c for c in node[1:] if isinstance(c, list) and c and _sym(c[0]) == "at"),
            None,
        )
        if at is None:
            continue
        x, y = float(at[1]), float(at[2])
        if head == "label":
            rot = float(at[3]) if len(at) > 3 else 0.0
            uuid = next(
                (
                    str(c[1])
                    for c in node[1:]
                    if isinstance(c, list) and len(c) > 1 and _sym(c[0]) == "uuid"
                ),
                None,
            )
            labels.append(
                Label(
                    name=str(node[1]),
                    x=x,
                    y=y,
                    rotation=rot,
                    effects=_effects_str(node),
                    uuid=uuid,
                )
            )
        elif head == "no_connect":
            ncs.append((x, y))
    return labels, ncs


def read_state(path: Path | str) -> BoardState:
    """Read the board state kbat's primitives compile against."""
    path = Path(path)
    data = _run_akcli(["read", str(path)])
    components: dict[str, Component] = {}
    for c in data.get("components", []):
        ref = c.get("designator")
        if not ref:
            continue
        params = {k: ("" if v is None else str(v)) for k, v in (c.get("parameters") or {}).items()}
        pins = {str(p["number"]): (to_mm(p["x_mil"]), to_mm(p["y_mil"])) for p in c.get("pins", [])}
        components[ref] = Component(
            reference=ref,
            lib_id=c.get("library_ref", ""),
            properties=params,
            pins=pins,
            x=to_mm(c.get("x_mil", 0.0)),
            y=to_mm(c.get("y_mil", 0.0)),
        )
    labels, ncs = _read_labels_and_ncs(path)
    return BoardState(components=components, labels=labels, no_connects=ncs)


# ---- writing ------------------------------------------------------------------


@dataclass
class DrawResult:
    """Outcome of one ``akcli draw`` call."""

    applied: bool
    net_changes: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _net_change_lines(payload: dict[str, Any]) -> list[str]:
    nd = payload.get("net_diff") or payload.get("net_changes") or {}
    if isinstance(nd, dict):
        lines = nd.get("lines") or nd.get("summary") or []
        if isinstance(lines, list):
            return [str(x) for x in lines]
    if isinstance(nd, list):
        return [str(x) if not isinstance(x, dict) else json.dumps(x, sort_keys=True) for x in nd]
    return []


def draw(path: Path | str, ops: list[dict[str, Any]], *, apply: bool) -> DrawResult:
    """Run one op-list against ``path``; dry-run unless ``apply``."""
    path = Path(path)
    doc = {
        "protocol_version": PROTOCOL_VERSION,
        "target_format": "kicad",
        "target_file": path.name,
        "ops": ops,
    }
    fd, ops_file = tempfile.mkstemp(prefix=".kbat-ops-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        args = ["draw", str(path), "--ops", ops_file, "--no-erc"]
        if apply:
            args.append("--apply")
        payload = _run_akcli(args)
    finally:
        try:
            os.unlink(ops_file)
        except OSError:
            pass
    return DrawResult(applied=apply, net_changes=_net_change_lines(payload), raw=payload)


__all__ = [
    "BackendError",
    "DrawResult",
    "MM_PER_MIL",
    "PROTOCOL_VERSION",
    "draw",
    "read_state",
    "to_mil",
    "to_mm",
]

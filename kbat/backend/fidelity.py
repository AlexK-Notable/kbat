"""Post-write fidelity guard: no kbat op may lose data it did not target.

No kbat primitive touches per-symbol ``body_style`` / ``in_pos_files``
attributes or the embedded ``lib_symbols`` cache, so any decrease in their
counts across a write is backend data loss. Moved labels are also checked:
akcli moves a label by delete + re-add, which keeps only name, position,
rotation and default formatting, so a label with custom formatting would
be flattened. Either failure is raised so the runner can restore the
pre-apply snapshot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from kbat.backend.akcli import _read_labels_and_ncs

_LIB_SYMBOL_RE = re.compile(r'^\t\t\(symbol "', re.M)


class FidelityError(Exception):
    """A write lost data that no kbat op asked to change."""


@dataclass(frozen=True)
class FieldCounts:
    body_style: int
    in_pos_files: int
    lib_symbols: int

    @classmethod
    def of(cls, path: Path) -> FieldCounts:
        text = Path(path).read_text(encoding="utf-8")
        lib = 0
        start = text.find("\t(lib_symbols")
        if start >= 0:
            end = text.find("\n\t)\n", start)
            lib = len(_LIB_SYMBOL_RE.findall(text, start, end if end > 0 else len(text)))
        return cls(
            body_style=text.count("(body_style"),
            in_pos_files=text.count("(in_pos_files"),
            lib_symbols=lib,
        )


def check_field_counts(before: FieldCounts, after: FieldCounts) -> None:
    lost = [
        f"{name} {getattr(before, name)} -> {getattr(after, name)}"
        for name in ("body_style", "in_pos_files", "lib_symbols")
        if getattr(after, name) < getattr(before, name)
    ]
    if lost:
        raise FidelityError("backend dropped data on write: " + ", ".join(lost))


def check_moved_labels(path: Path, moves: list[tuple[str, float, float, str]]) -> None:
    """Each ``(name, x, y, effects)`` must now exist at ``(x, y)`` with the same effects."""
    if not moves:
        return
    labels, _ = _read_labels_and_ncs(Path(path))
    for name, x, y, effects in moves:
        here = [
            lab
            for lab in labels
            if lab.name == name and abs(lab.x - x) < 0.01 and abs(lab.y - y) < 0.01
        ]
        if not here:
            raise FidelityError(f"moved label {name!r} not found at ({x}, {y}) after write")
        if not any(_norm(lab.effects) == _norm(effects) for lab in here):
            raise FidelityError(
                f"moved label {name!r} at ({x}, {y}) lost its formatting: "
                f"{effects} -> {here[0].effects}"
            )


def _norm(effects: str) -> str:
    return re.sub(r"\s+", " ", effects).strip()


__all__ = ["FidelityError", "FieldCounts", "check_field_counts", "check_moved_labels"]

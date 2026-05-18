#!/usr/bin/env python3
"""Hypothesis-mask lint — flag narrowed strategies that hide known bugs.

Policy (ROADMAP §7.3.5): every Hypothesis strategy that is narrowed
(``min_value == max_value``), filtered with ``.map(lambda ... * CONST)``,
or shielded with ``@settings(suppress_health_check=...)`` MUST carry a
``# HYPOTHESIS_MASK: Flag #N`` annotation pointing at the open flag the
mask works around. When the underlying flag is resolved (per ROADMAP
Appendix A), the mask AND its annotation must be removed in the same
fix PR.

Two checks:

1. **Orphan-mask detection**: a mask pattern with no annotation in the
   surrounding 5-line window → exit 1.
2. **Stale-mask detection**: an annotation pointing at a flag marked
   ``resolved`` in ROADMAP Appendix A → exit 1.

Usage:
    python scripts/check_hypothesis_masks.py [path ...]

Default path is ``tests/``. Designed to run under CI's ``gate`` job.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Mask patterns (any of these triggers an annotation requirement).
MASK_PATTERNS = [
    # @settings(suppress_health_check=...) — always suspicious
    re.compile(r"suppress_health_check"),
    # st.integers/floats with min_value == max_value (single-value narrowing)
    re.compile(
        r"st\.(?:integers|floats)\s*\(\s*min_value\s*=\s*([0-9.+-]+)\s*,"
        r"\s*max_value\s*=\s*\1\b"
    ),
    # ``.map(lambda <var>: <var> * CONST)`` — multiplicative grid-snap mask
    re.compile(r"\.map\s*\(\s*lambda\s+\w+\s*:\s*\w+\s*\*\s*[A-Z_0-9]"),
]

ANNOTATION_PATTERN = re.compile(r"#\s*HYPOTHESIS_MASK:\s*Flag\s*#(\d+)")
MASK_WINDOW = 5  # lines above/below


def _scan_roadmap_resolved_flags(roadmap_path: Path) -> set[int]:
    """Parse ROADMAP Appendix A. Return the set of flag numbers that
    are marked resolved (or otherwise closed).

    A flag is considered resolved when its row contains the word
    ``resolved`` (case-insensitive). The matrix format is::

        | # | Severity | Flag | Target | Source |

    so we anchor on the leading pipe + number.
    """
    if not roadmap_path.exists():
        return set()
    resolved: set[int] = set()
    in_appendix_a = False
    for line in roadmap_path.read_text(encoding="utf-8").splitlines():
        if "Appendix A" in line:
            in_appendix_a = True
            continue
        if in_appendix_a and line.startswith("## Appendix"):
            break
        if not in_appendix_a:
            continue
        m = re.match(r"\|\s*(\d+)\s*\|", line)
        if m and re.search(r"\bresolved\b", line, re.IGNORECASE):
            resolved.add(int(m.group(1)))
    return resolved


def find_orphan_masks(path: Path) -> list[tuple[Path, int, str]]:
    """Return (file, line_no, line_text) for unannotated mask sites."""
    if path.is_dir():
        files = sorted(path.rglob("*.py"))
    else:
        files = [path]

    violations: list[tuple[Path, int, str]] = []
    for fp in files:
        if fp.resolve() == Path(__file__).resolve():
            continue
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        for idx, line in enumerate(lines):
            if not any(p.search(line) for p in MASK_PATTERNS):
                continue
            lo = max(0, idx - MASK_WINDOW)
            hi = min(len(lines), idx + MASK_WINDOW + 1)
            window = "\n".join(lines[lo:hi])
            if ANNOTATION_PATTERN.search(window):
                continue
            violations.append((fp, idx + 1, line.rstrip()))
    return violations


def find_stale_annotations(
    path: Path, resolved_flags: set[int]
) -> list[tuple[Path, int, int]]:
    """Return (file, line_no, flag_num) for annotations pointing at a
    flag marked resolved in ROADMAP."""
    if path.is_dir():
        files = sorted(path.rglob("*.py"))
    else:
        files = [path]

    stale: list[tuple[Path, int, int]] = []
    for fp in files:
        if fp.resolve() == Path(__file__).resolve():
            continue
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for idx, line in enumerate(lines):
            m = ANNOTATION_PATTERN.search(line)
            if m and int(m.group(1)) in resolved_flags:
                stale.append((fp, idx + 1, int(m.group(1))))
    return stale


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [Path("tests")]
    repo_root = Path(__file__).resolve().parent.parent
    roadmap = repo_root / "docs" / "ROADMAP.md"
    resolved = _scan_roadmap_resolved_flags(roadmap)

    orphans: list[tuple[Path, int, str]] = []
    stale: list[tuple[Path, int, int]] = []
    for t in targets:
        if not t.exists():
            print(f"WARN: path does not exist: {t}", file=sys.stderr)
            continue
        orphans.extend(find_orphan_masks(t))
        stale.extend(find_stale_annotations(t, resolved))

    failed = False
    if orphans:
        failed = True
        print("Hypothesis-mask violations (orphan — need '# HYPOTHESIS_MASK: Flag #N'):")
        for fp, ln, text in orphans:
            print(f"  {fp}:{ln}: {text}")
    if stale:
        failed = True
        print(
            "Hypothesis-mask violations (stale — annotation references a "
            "RESOLVED flag; remove both the mask and the annotation):"
        )
        for fp, ln, flag in stale:
            print(f"  {fp}:{ln}: Flag #{flag}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Mock-policy lint — flag unannotated kbat-mocking sites in tests/.

Policy (ROADMAP §7.3.5): kbat internals should NOT be mocked. Tests must
exercise real code paths. Two narrow exceptions exist:

- Stdlib patching for fault injection (e.g. ``monkeypatch.setattr(safety.os,
  "replace", boom)`` to simulate a crash mid-rename).
- ``unittest.mock`` usage where there is no other way to provoke a
  specific failure mode (rare; should be justified in the annotation).

Every legitimate site MUST carry a ``# MOCK_EXEMPT: <reason>`` annotation
on the same line or within 3 lines above/below. Unannotated matches fail
the lint with exit code 1.

Usage:
    python scripts/check_mock_policy.py [path ...]

Default path is ``tests/``. Designed to run under CI's ``gate`` job.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Patterns considered policy-relevant (any of these flags a site).
PATTERNS = [
    re.compile(r"\bfrom\s+unittest\.mock\b"),
    re.compile(r"\bimport\s+unittest\.mock\b"),
    re.compile(r"\bmock\.patch\b"),
    re.compile(r"\bpatch\.object\b"),
    re.compile(r"\bpatch\("),
    re.compile(r"\bMagicMock\b"),
    re.compile(r"monkeypatch\.setattr\s*\([^)]*safety\.os"),
    re.compile(r"monkeypatch\.setattr\s*\([^)]*os\.(replace|fsync|rename)"),
]

ANNOTATION = re.compile(r"#\s*MOCK_EXEMPT:")
EXEMPT_WINDOW = 3  # lines above/below


def find_violations(path: Path) -> list[tuple[Path, int, str]]:
    """Return (file, line_no, line_text) for unannotated mock sites."""
    if path.is_dir():
        files = sorted(path.rglob("*.py"))
    else:
        files = [path]

    violations: list[tuple[Path, int, str]] = []
    for fp in files:
        # Skip the lint script itself (it contains the patterns as strings).
        if fp.resolve() == Path(__file__).resolve():
            continue
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        for idx, line in enumerate(lines):
            if not any(p.search(line) for p in PATTERNS):
                continue
            # Look for the annotation in the surrounding window.
            lo = max(0, idx - EXEMPT_WINDOW)
            hi = min(len(lines), idx + EXEMPT_WINDOW + 1)
            window = "\n".join(lines[lo:hi])
            if ANNOTATION.search(window):
                continue
            violations.append((fp, idx + 1, line.rstrip()))
    return violations


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [Path("tests")]
    all_violations: list[tuple[Path, int, str]] = []
    for t in targets:
        if not t.exists():
            print(f"WARN: path does not exist: {t}", file=sys.stderr)
            continue
        all_violations.extend(find_violations(t))

    if all_violations:
        print("Mock-policy violations (need '# MOCK_EXEMPT: <reason>'):")
        for fp, ln, text in all_violations:
            print(f"  {fp}:{ln}: {text}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

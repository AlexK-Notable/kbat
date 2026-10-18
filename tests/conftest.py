"""Shared fixtures for the kbat test suite.

Phase 2 Agent H additions: per-test working copies of the real test
schematic and the synthetic goldens, plus a helper that introspects
``kbat.ops`` to enumerate the public primitives. The intent is to keep
the primitive-discovery logic in *one* place so the meta-test and the
Hypothesis property tests cannot drift apart.

No mocking of the akcli backend, the filesystem, or schematic fixtures
per the testing-strategy note [[HE4_MvAS391OEYJ6M_G3h]] (mock theater
forbidden — the byte-exact contract is the contract).
"""

from __future__ import annotations

import copy
import functools
import inspect
import shutil
from pathlib import Path

import pytest

from kbat import backend

# ---- canonical paths --------------------------------------------------------

#: The vendored Klipper companion board, before cleanup.py ran (the gate's
#: input). A real KiCad 10 file: 109 symbols, body_style / in_pos_files on
#: every symbol, an embedded power:PWR_FLAG.
REAL_SCHEMATIC = (
    Path(__file__).parent / "fixtures" / "validation_gate" / "kicad_experiment.kicad_sch"
)

#: Directory holding the v0.1 golden master corpus (6 entries).
GOLDEN_DIR = Path(__file__).parent / "golden"

#: Synthetic goldens — small fixtures we own. Distinct from the cleanup
#: golden (which is auto-generated from cleanup.py output) and the recipe
#: stubs (Phase 3 placeholders).
SYNTHETIC_GOLDENS = (
    GOLDEN_DIR / "synthetic_minimal.kicad_sch.golden",
    GOLDEN_DIR / "synthetic_edge_cases.kicad_sch.golden",
)


# ---- primitive enumeration --------------------------------------------------

#: Names exported from ``kbat.ops`` that are NOT primitives but compat
#: helpers / type-hints / module imports. Excluded from PRIMITIVE_REGISTRY.
_NON_PRIMITIVE_EXPORTS: frozenset[str] = frozenset()


def discover_primitives() -> frozenset[str]:
    """Return the set of public primitive names exported by ``kbat.ops``.

    A "primitive" is any public, callable, non-class attribute of
    :mod:`kbat.ops` whose name is not in :data:`_NON_PRIMITIVE_EXPORTS`.

    This is the *single source of truth* used by both the meta-test
    (``tests/test_primitive_registry.py``) and the Hypothesis property
    tests (``tests/test_primitives.py``). Adding a primitive in
    :mod:`kbat.ops` automatically extends both.
    """
    import kbat.ops as ops_module

    primitives: set[str] = set()
    for name in dir(ops_module):
        if name.startswith("_"):
            continue
        if name in _NON_PRIMITIVE_EXPORTS:
            continue
        obj = getattr(ops_module, name)
        if not callable(obj):
            continue
        if inspect.isclass(obj):
            continue
        primitives.add(name)
    return frozenset(primitives)


# ---- shared fixtures --------------------------------------------------------


@pytest.fixture
def real_schematic_copy(tmp_path: Path) -> Path:
    """Return a tmp-path copy of the real test schematic.

    Tests get a fresh writable copy per invocation. Skips the test if
    the source isn't checked out (e.g., on a sparse clone).
    """
    if not REAL_SCHEMATIC.exists():
        pytest.skip(f"real schematic not present: {REAL_SCHEMATIC}")
    dst = tmp_path / REAL_SCHEMATIC.name
    shutil.copy2(REAL_SCHEMATIC, dst)
    return dst


@pytest.fixture
def synthetic_minimal_copy(tmp_path: Path) -> Path:
    """Return a tmp-path copy of the minimal synthetic golden."""
    src = GOLDEN_DIR / "synthetic_minimal.kicad_sch.golden"
    if not src.exists():
        pytest.skip(f"synthetic golden missing: {src}")
    dst = tmp_path / "synthetic_minimal.kicad_sch"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture
def synthetic_edge_cases_copy(tmp_path: Path) -> Path:
    """Return a tmp-path copy of the edge-case synthetic golden."""
    src = GOLDEN_DIR / "synthetic_edge_cases.kicad_sch.golden"
    if not src.exists():
        pytest.skip(f"synthetic golden missing: {src}")
    dst = tmp_path / "synthetic_edge_cases.kicad_sch"
    shutil.copy2(src, dst)
    return dst


@functools.cache
def _cached_state(src: str) -> backend.BoardState:
    return backend.read_state(Path(src))


def fresh_state(src: Path) -> backend.BoardState:
    """A private copy of ``src``'s board state, read through akcli once per run.

    The Hypothesis property tests compile against this in memory, so each
    example starts from the fixture's state without a per-example akcli call.
    """
    return copy.deepcopy(_cached_state(str(src)))


@pytest.fixture
def primitive_names() -> frozenset[str]:
    """The discovered set of primitive names (sorted, frozen)."""
    return discover_primitives()

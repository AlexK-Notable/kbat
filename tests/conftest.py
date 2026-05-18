"""Shared fixtures for the kbat test suite.

Phase 2 Agent H additions: per-test working copies of the real test
schematic and the synthetic goldens, plus a helper that introspects
``kbat.ops`` to enumerate the public primitives. The intent is to keep
the primitive-discovery logic in *one* place so the meta-test and the
Hypothesis property tests cannot drift apart.

No mocking of ``kicad_sch_api``, the filesystem, or schematic fixtures
per the testing-strategy note [[HE4_MvAS391OEYJ6M_G3h]] (mock theater
forbidden — the byte-exact contract is the contract).
"""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path

import pytest

# ---- canonical paths --------------------------------------------------------

#: The real schematic that the cleanup.py validation gate targets.
REAL_SCHEMATIC = Path(
    "/home/komi/notes/kicad/kicad_experiment/kicad_experiment.kicad_sch"
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
_NON_PRIMITIVE_EXPORTS = frozenset({"relax_validation"})


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


@pytest.fixture
def loaded_real_schematic(real_schematic_copy: Path):
    """Load the real schematic into a kicad-sch-api object with relaxed validation.

    The Hypothesis property tests use this fixture; it avoids per-example
    reload cost while still giving each test a fresh in-memory mutable
    schematic (because ``tmp_path`` is per-test, the underlying bytes are
    per-test).
    """
    import kicad_sch_api as ksa

    from kbat.ops import relax_validation

    sch = ksa.Schematic.load(str(real_schematic_copy))
    relax_validation(sch)
    return sch


@pytest.fixture
def primitive_names() -> frozenset[str]:
    """The discovered set of primitive names (sorted, frozen)."""
    return discover_primitives()

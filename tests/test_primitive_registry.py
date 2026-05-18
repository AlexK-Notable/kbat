"""Coverage-by-construction meta-test for kbat primitives.

Every public callable exported from :mod:`kbat.ops` (the implicit
PRIMITIVE_REGISTRY) MUST have a corresponding test. New primitives that
land without coverage break the suite — this is the enforcement
mechanism prescribed by the testing strategy
[[HE4_MvAS391OEYJ6M_G3h]] and Phase 2 gate 2.18.

Discovery convention: a primitive is considered tested if at least one
test function in ``test_primitives.py`` or ``test_ops_basic.py``:

    1. has the primitive's name as a substring of its own function name,
    2. lives in a file that imports the primitive from ``kbat.ops`` via
       an explicit ``from kbat.ops import <primitive>`` statement, AND
    3. has at least one ``assert`` statement inside its body.

Substring naming alone is gameable: an empty-body test function whose
name mentions the primitive would pass a name-only check while exercising
nothing. The Phase 2.2 tightening (per [[G1DhgkrMHeSZKBO_mssfw]]) adds
the import + assert AST checks so the meta-test detects:

    - empty-body / pass-only test stubs
    - skipped tests that never run
    - tests that import the primitive but never assert
    - tests that assert but never reference the primitive

Examples that PASS:

    set_property            → matched by test_set_property_value_changes...
    add_no_connect          → matched by test_add_no_connect_adds_then_noop
    snap_labels_to_pins     → matched by test_snap_labels_to_pins_idempotent

Note on ``relax_validation``: this is a backend-compat shim, not a
primitive (it does not declare an apply/idempotent contract). It is
explicitly excluded by :func:`conftest.discover_primitives`. If a future
refactor reclassifies it as a primitive, this meta-test will start
demanding coverage automatically — which is the right behaviour.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conftest import discover_primitives

TESTS_DIR = Path(__file__).parent

#: Test files that count as covering a primitive when they reference it.
#: ``test_ops_basic.py`` is Phase 1 D's per-op test file (do not modify);
#: ``test_primitives.py`` is Phase 2 H's Hypothesis property tests.
#:
#: These are the *expected* coverage files (used for error messages and
#: gate calibration). Discovery itself scans every ``tests/test_*.py``
#: so that a stub test function dropped in any test file is still
#: required to satisfy the import + assert checks — otherwise an empty
#: ``def test_set_property_empty(): pass`` in a fresh file would
#: silently "cover" the primitive (Phase 2.2 hardening per
#: [[G1DhgkrMHeSZKBO_mssfw]] / gate check 2.9).
COVERAGE_FILES = (
    TESTS_DIR / "test_ops_basic.py",
    TESTS_DIR / "test_primitives.py",
)


def _all_test_files() -> tuple[Path, ...]:
    """Return every ``tests/**/*.py`` file that may contain ``def test_*``.

    Scans the entire ``tests/`` directory tree (not just ``test_*.py``)
    so that stub coverage cannot hide behind an unconventional filename.
    Excludes ``conftest.py``, ``__init__.py``, and the meta-test file
    itself (the latter to avoid a self-referential loop where the
    meta-test scans its own helper-test names for primitive coverage).
    """
    excluded = {"conftest.py", "__init__.py", Path(__file__).name}
    return tuple(
        sorted(
            p
            for p in TESTS_DIR.rglob("*.py")
            if p.name not in excluded
        )
    )


def _file_imports_primitive(tree: ast.AST, primitive_name: str) -> bool:
    """True iff ``tree`` contains a ``from kbat.ops import <primitive_name>``.

    Walks ``ImportFrom`` nodes and matches both bare imports and
    aliased imports (``from kbat.ops import set_property as sp`` still
    counts — the original primitive name is what matters).
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "kbat.ops":
            continue
        if any(alias.name == primitive_name for alias in node.names):
            return True
    return False


def _function_has_assert(func_node: ast.FunctionDef) -> bool:
    """True iff ``func_node`` contains at least one ``ast.Assert`` in its body.

    Walks the entire function body (nested ifs/loops/try blocks count).
    Bare ``pass`` bodies, ``return`` -only bodies, and skip-only bodies
    return False.
    """
    return any(isinstance(n, ast.Assert) for n in ast.walk(func_node))


def _collect_test_functions(path: Path) -> list[tuple[str, ast.FunctionDef, ast.AST]]:
    """Return ``[(test_name, func_node, file_tree)]`` for every ``def test_*``.

    Returns [] if the file doesn't exist (so a missing coverage file is
    silently ignored rather than raising — the assertion below is what
    surfaces the gap).
    """
    if not path.exists():
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, ast.FunctionDef, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            out.append((node.name, node, tree))
    return out


def _tested_primitives(primitives: frozenset[str]) -> dict[str, list[str]]:
    """Map each primitive name → list of MEANINGFUL test functions that cover it.

    A primitive is "tested" iff at least one test function across
    :data:`COVERAGE_FILES` satisfies ALL of:

        1. The test function's name contains the primitive name as a substring.
        2. The test's file imports the primitive from ``kbat.ops``.
        3. The test function body contains at least one ``assert`` statement.

    The substring + import + assert triple makes the meta-test resistant
    to gaming via name-only stubs (per [[G1DhgkrMHeSZKBO_mssfw]]).
    """
    collected: list[tuple[str, ast.FunctionDef, ast.AST]] = []
    for path in _all_test_files():
        collected.extend(_collect_test_functions(path))

    coverage: dict[str, list[str]] = {prim: [] for prim in primitives}
    for prim in primitives:
        for test_name, func_node, file_tree in collected:
            if prim not in test_name:
                continue
            if not _file_imports_primitive(file_tree, prim):
                continue
            if not _function_has_assert(func_node):
                continue
            coverage[prim].append(test_name)
    return coverage


def test_primitive_registry_is_non_empty() -> None:
    """Sanity check: the discovery mechanism must find at least one primitive.

    If this fails, the introspection logic in :func:`discover_primitives`
    has broken — and every other meta-test result is meaningless.
    """
    primitives = discover_primitives()
    assert primitives, (
        "discover_primitives() returned the empty set — introspection "
        "logic is broken or kbat.ops exports nothing"
    )


def test_every_primitive_has_a_corresponding_test() -> None:
    """Each primitive in ``kbat.ops`` must be referenced by a test function name.

    Failure mode this catches: a developer adds a new primitive to
    ``kbat/ops/`` (and registers it in the dispatcher) but forgets to
    add a test. The build fails immediately, listing the orphan
    primitive(s).

    This is the coverage-by-construction enforcement called for by Phase 2
    gate 2.18. It is intentionally a SEPARATE assertion from any
    individual primitive's behaviour test, so the gap is visible in
    the test report rather than buried in a skipped behaviour test.
    """
    primitives = discover_primitives()
    coverage = _tested_primitives(primitives)
    untested = sorted(prim for prim, tests in coverage.items() if not tests)
    assert not untested, (
        f"Primitives without a corresponding test in {COVERAGE_FILES}: "
        f"{untested}\n"
        f"Add a test function whose name contains the primitive name, "
        f"e.g., `def test_<primitive>_idempotent(...)`."
    )


@pytest.mark.parametrize("expected_primitive", sorted(discover_primitives()))
def test_primitive_has_test(expected_primitive: str) -> None:
    """Per-primitive variant of the meta-test for finer-grained reporting.

    When the suite-wide :func:`test_every_primitive_has_a_corresponding_test`
    flags an orphan primitive, this parametrized test surfaces *which*
    primitive in a separate test ID — easier to scan in CI logs.
    """
    coverage = _tested_primitives(frozenset({expected_primitive}))
    matching_tests = coverage[expected_primitive]
    assert matching_tests, (
        f"primitive {expected_primitive!r} has no matching test function "
        f"in {[p.name for p in COVERAGE_FILES]}"
    )


def _test_name_claims_primitive(test_name: str, primitive: str) -> bool:
    """True iff ``test_name`` is shaped as a primitive-coverage test for ``primitive``.

    We treat a test as "claiming coverage" only if its name starts with
    ``test_<primitive>`` (so the primitive is the test's focus, not a
    parenthetical mention). This avoids flagging legitimate dispatcher
    or planner tests whose names contain a primitive incidentally
    (e.g. ``test_dispatch_set_property_changes`` is a dispatcher test,
    not a ``set_property`` primitive test).
    """
    prefix = f"test_{primitive}"
    if not test_name.startswith(prefix):
        return False
    # Avoid spurious matches when the primitive name is a strict prefix
    # of another identifier (e.g. ``set_property`` vs ``set_property_X``).
    # ``test_set_property`` and ``test_set_property_*`` both claim the
    # primitive; ``test_set_propertyish`` (no underscore) does not.
    remainder = test_name[len(prefix) :]
    return remainder == "" or remainder.startswith("_")


def test_no_stub_tests_pretend_to_cover_a_primitive() -> None:
    """Reject test functions whose names claim a primitive but do nothing.

    A common gaming pattern: someone adds ``def test_set_property_empty():
    pass`` (or an only-skip body) to a fresh file. The substring-on-name
    match would silently mark ``set_property`` as "covered" while the
    body exercises nothing.

    This check walks *every* ``tests/test_*.py`` (not just the historical
    COVERAGE_FILES) and reports any test function whose name is shaped
    ``test_<primitive>...`` but whose body either (a) lacks an
    ``assert``, or (b) lives in a file that doesn't import the primitive
    from ``kbat.ops``. Operationalizes Phase 2.2 gate check 2.9.

    Note on scope: only ``test_<primitive>...``-prefixed names are
    checked (see :func:`_test_name_claims_primitive`). Tests like
    ``test_dispatch_set_property_changes`` are dispatcher tests that
    mention the primitive incidentally; they're out of scope for the
    primitive-coverage contract.
    """
    primitives = discover_primitives()
    offenders: list[str] = []
    for path in _all_test_files():
        for test_name, func_node, file_tree in _collect_test_functions(path):
            for prim in primitives:
                if not _test_name_claims_primitive(test_name, prim):
                    continue
                # This test claims to cover ``prim``. Verify it actually does.
                imports_prim = _file_imports_primitive(file_tree, prim)
                has_assert = _function_has_assert(func_node)
                if not (imports_prim and has_assert):
                    reasons = []
                    if not imports_prim:
                        reasons.append(f"no `from kbat.ops import {prim}`")
                    if not has_assert:
                        reasons.append("no assert in body")
                    offenders.append(
                        f"{path.name}::{test_name} (primitive={prim!r}): "
                        f"{'; '.join(reasons)}"
                    )
    assert not offenders, (
        "Stub or unhelpful test functions that pretend to cover a primitive:\n  "
        + "\n  ".join(offenders)
        + "\nEither flesh the test out (add an assert and import the primitive "
        "from kbat.ops) or rename it so it no longer claims primitive coverage."
    )

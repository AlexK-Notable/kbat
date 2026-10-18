"""Phase 1 validation gate as pytest tests.

These tests run under ``@pytest.mark.gate`` and verify the 4 criteria
from ``kbat._validation``. Required for merge per ROADMAP §7.3.

The boolean criterion functions return ``True`` on pass and ``False`` on
fail. For diagnostic output on failure, run
``uv run python -m kbat._validation --criteria all`` directly.
"""

from __future__ import annotations

import pytest

from kbat._validation import (
    criterion_functional_equivalence,
    criterion_idempotence,
    criterion_kicad10_fidelity,
    criterion_loc,
    criterion_readable_diff,
)

pytestmark = pytest.mark.gate


@pytest.mark.gate
def test_gate_1ga_loc() -> None:
    """1.G.a — manifest LOC ≤ 80% of script LOC."""
    assert criterion_loc()


@pytest.mark.gate
def test_gate_1gb_functional_equivalence() -> None:
    """1.G.b — kbat output is functionally equivalent to cleanup.py output."""
    assert criterion_functional_equivalence()


@pytest.mark.gate
def test_gate_1gc_idempotence() -> None:
    """1.G.c — second apply is byte-exact no-op."""
    assert criterion_idempotence()


@pytest.mark.gate
def test_gate_1gd_readable_diff() -> None:
    """1.G.d — kbat plan emits ≥3 op-keywords."""
    assert criterion_readable_diff()


@pytest.mark.gate
def test_gate_1ge_kicad10_fidelity() -> None:
    """1.G.e — apply drops no body_style / in_pos_files / lib_symbols entries."""
    assert criterion_kicad10_fidelity()

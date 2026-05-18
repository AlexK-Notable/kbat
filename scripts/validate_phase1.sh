#!/usr/bin/env bash
# Phase 1 validation harness wrapper.
#
# Runs the 4 hard-gate criteria from spec §9 / gate
# [[9qP6PTTxu4B90lFQxjrHX]]. Exits non-zero on any failure.
#
# Fixtures default to ``tests/fixtures/validation_gate/`` (vendored under
# version control). Override with ``KBAT_VALIDATION_FIXTURE_DIR`` to point
# at a different snapshot.
#
# Usage:
#   bash scripts/validate_phase1.sh                  # all four criteria
#   bash scripts/validate_phase1.sh --criteria a,c   # subset

set -euo pipefail

# Resolve repo root from this script's location (portable; no hardcoded path).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Sanity: vendored schematic + manifest + cleanup.py present.
FIXTURE_DIR="${KBAT_VALIDATION_FIXTURE_DIR:-${REPO_ROOT}/tests/fixtures/validation_gate}"
test -s "${FIXTURE_DIR}/kicad_experiment.kicad_sch" \
    || { echo "FATAL: source schematic missing under ${FIXTURE_DIR}" >&2; exit 2; }
test -s "${FIXTURE_DIR}/cleanup.py" \
    || { echo "FATAL: cleanup.py missing under ${FIXTURE_DIR}" >&2; exit 2; }
test -s "${FIXTURE_DIR}/cleanup.kbat.yaml" \
    || { echo "FATAL: cleanup.kbat.yaml missing under ${FIXTURE_DIR}" >&2; exit 2; }

exec uv run python -m kbat._validation "$@"

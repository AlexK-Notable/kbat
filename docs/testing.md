# kbat — Testing strategy

This document is the long-form companion to `docs/ROADMAP.md` §7.3. It
records the diamond-pyramid target, the mock and Hypothesis disciplines,
the CI-gate design, and the schema-lifetime regression policy. ROADMAP
§7.3 is a one-paragraph summary that links here; this file is canonical.

## 7.3.1 Diamond-pyramid target and tracking

The pyramid is a **commitment about the next 6 months**, not a
description of the current suite. Each release notes the current
percentages in the CHANGELOG so drift is auditable.

| Tier | Target | v0.1.1 actual (129 tests) | Notes |
|---|---|---|---|
| E2E (real `kicad-cli` + Docker) | 5% | 0% (gap) | First E2E lands with `kbat doctor` in v0.5 |
| Validation gate (cleanup port + recipe smoke) | 10% | 4 tests (~3%) | Shipped in v0.1.1 as a CI artifact — see §7.3.2 |
| Golden master + round-trip | 35% | ~16% (~20 / 129) | Expanded by every recipe (10 in v0.5) and every schema-bump corpus entry |
| Hypothesis property | 25% | ~9-10 `@given` calls (~8%) | Re-widened in v0.1.1 (Flag #1); expanded under the Hypothesis discipline in §7.3.4 |
| Manifest fuzz / schema rejection | 10% | bimodal Hypothesis strategies shipped in v0.1.1 (Phase 2.2) | Discriminator coverage via `DetailEntry` + bimodal strategies; broader fuzz file is a v0.5 deliverable |
| Pure unit (fast inner loop) | 15% | ~70% | Decreases as the four tiers above grow; absolute count holds |
| Integration (cross-module contract) | implied by §7.3.3 | 4 tests in `tests/integration/test_apply_rollback.py` | New tier added in v0.1.1 as the OSTH enforcement vehicle |

## 7.3.2 The validation gate is a CI artifact, not a script

The four 1.G criteria (LOC, functional-equivalence, idempotence,
readable diff) are first-class pytest tests under
`tests/gate/test_validation_gate.py`, marked with `@pytest.mark.gate`.
The marker is registered in `pyproject.toml`. They run as a separate
GHA job (`gate`) gated by branch protection on `master` and `main`.

The fixtures the gate depends on (the `kicad_experiment.kicad_sch`
~80 KB schematic, `cleanup.py` ~12 KB, `cleanup.kbat.yaml` ~5 KB) are
**vendored** into `tests/fixtures/validation_gate/`. The harness
(`kbat/_validation.py`) reads paths from environment variables
`KBAT_VALIDATION_FIXTURE_DIR` (default: the vendored path) and
`KBAT_VALIDATION_PY_INTERPRETER` (default: `sys.executable`). No
host-specific path is hardcoded.

Trade-off: ~280 KB of vendored fixtures vs network-fetched fixtures.
Vendoring wins because (a) the gate must be reproducible at any tag for
the 10-year `kbat.dev/v1` lifetime (see §7.3.7), (b) network fetches in
CI introduce flakiness incompatible with a hard gate, (c) 280 KB is
well under any reasonable repo budget.

**Status**: shipped in v0.1.1 (Phase 2.3). `.github/workflows/ci.yml`
runs `fast` (excludes `gate`) then `gate` (only `gate` marker) jobs.
The lint scripts `scripts/check_mock_policy.py` and
`scripts/check_hypothesis_masks.py` also run in the `gate` job.

## 7.3.3 Integration coverage requirement: cross-module contracts (OSTH policy)

Any contract that spans modules MUST have at least one integration test
that exercises both halves against the same on-disk artifact. The MVP
shipped two such contracts unguarded — the `_safety` writer / `_rollback`
reader state-file shape and the `_runner` apply → `_planner` plan
reproducibility path. The state-file divergence was the v0.1.0 release
blocker that motivated this policy.

The v0.1.1 fix is the **OSTH discipline** ("One Schema, Two Halves"):
any cross-module on-disk contract is serialised through a single shared
Pydantic model. For the state file, that model is
`kbat/_state_schema.py:StateFile`; both `_safety.KbatState.save()` and
`_rollback._load_state()` serialise / validate through it. Any change to
the on-disk shape lands in `_state_schema.py` first; both halves update
in the same commit.

v0.1.1 shipped `tests/integration/test_apply_rollback.py` with 4
integration tests exercising the apply → rollback round-trip end-to-end
against the shared schema. The integration tier is now a permanent
suite-shape commitment, not a one-off.

## 7.3.4 Hypothesis discipline (the "no narrowing without a ticket" rule)

When a property test surfaces a bug:

1. A constraint that masks the bug MAY be added, but only in a commit
   paired with an open-flag entry (Appendix A) citing the masked input
   class.
2. The narrowing line carries the comment `# HYPOTHESIS_MASK: Flag #N`.
3. The fixing PR MUST also widen the strategy back to its pre-mask form
   in the same commit. Reviewers reject any "fix Flag #N" PR that does
   not re-widen.
4. A CI lint (`scripts/check_hypothesis_masks.py`) parses test files
   for `HYPOTHESIS_MASK:` tokens, cross-references against ROADMAP
   Appendix A, and fails the build if any token references a flag
   that's been marked resolved.

**Status**: shipped in v0.1.1 (Phase 2.3). Flag #1 was the canonical
test: Phase 2.4 fixed `add_pwr_flag`, re-widened the Hypothesis
strategy, and removed the `# HYPOTHESIS_MASK: Flag #1` annotation
atomically. Flag #5 (Hypothesis runtime budgets at scale) remains open
and is annotated `# HYPOTHESIS_MASK: Flag #5` on the
`PROPERTY_SETTINGS.suppress_health_check` line.

## 7.3.5 Mock policy (precise version)

- **Banned, hard-fail**: any `unittest.mock.patch` or `mock.patch`
  targeting `kicad_sch_api` or its dependencies. `scripts/check_mock_policy.py`
  greps for `mock\.patch.*kicad_sch_api` and `from kicad_sch_api`
  alongside any `MagicMock`. Currently 0 violations.
- **Banned, hard-fail**: any patching of `pcbnew`, `kicad-cli`
  subprocess invocations, or `.kicad_sch` file content. The schematic
  IS the unit of behavior; mocking it tests nothing.
- **Permitted, with explicit annotation**: monkey-patching of stdlib
  I/O primitives (`os.replace`, `os.fsync`) for the express purpose of
  fault injection in safety/recovery tests. These sites are
  `tests/test_safety.py:92,113,223` — each is annotated
  `# MOCK_EXEMPT: fault-injection for crash-recovery`. v0.5 evaluates
  replacing them with a `FaultInjector` wrapper that simulates `EIO`
  deterministically without touching the syscall namespace.
- **Permitted, with explicit annotation**: duck-typed substitutes for
  kicad-sch-api objects in dispatcher unit tests
  (`tests/test_runner.py:_FakeComponent` etc.) that test routing logic,
  not schematic mutation. These are explicit Python classes, not `Mock`
  instances, and must be annotated
  `# DUCK_FAKE: tests dispatcher routing, not schematic mutation`.

A `scripts/check_mock_policy.py` CI lint enforces the annotations. Both
lint scripts shipped in v0.1.1 (Phase 2.3) and run in the `gate` GHA
job.

## 7.3.6 CI budgets

Per-push (PR feedback, `fast` job): currently <30s — excludes `gate`.
Per-push `gate` job: currently <90s — runs the 4 gate tests plus the
mock-policy and Hypothesis-mask lints. Combined: <120s per-PR.

Nightly (aspirational, v0.5): <20min — adds E2E and extended-Hypothesis
(10,000 examples per property). Release (aspirational, v1.0): <60min —
adds E2E + KiCad-version matrix (9.x and 10.x).

E2E and matrix tiers are deferred to v0.5; the v0.1.x CI only runs
`fast` + `gate` on every push.

## 7.3.7 Schema lifetime regression suite (the 10-year guarantee)

ROADMAP §2 + §5.1 commit `kbat.dev/v1` to a 10-year minimum freeze (post-v1.0)
matched to the typical industrial-PCB-design tool lifetime. The lifetime
regression suite is the test-side mechanism that makes that promise enforceable.

**Status**: design only. The suite is a v1.0 deliverable, not v0.1.x. The
design below records the discipline so the lifetime promise has a
concrete fulfilment plan from the start.

**Directory layout**:

```
tests/lifetime/
├── README.md                    # The lifetime-suite contract
├── corpus/
│   ├── v1/                      # v1 manifests (added once v1 ships)
│   │   ├── 2027-q1-001.yaml     # Each entry datestamped, never deleted
│   │   ├── 2027-q1-001.golden.kicad_sch
│   │   └── ...
│   ├── v1beta1/                 # frozen at v1.0 cutover
│   └── v1alpha1/                # frozen at v0.5 cutover
├── test_v1_parse.py             # Every corpus entry parses against current kbat
├── test_v1_apply_byte_stable.py # Every entry's apply produces the golden bytes
├── test_v1_reject_invalid.py    # Invalid corpus rejected
└── test_v1_migrate.py           # When v2 lands, every v1 entry migrates losslessly
```

**Corpus collection**:

1. **Internal** — every recipe golden + every kbat-author-written test
   manifest. Seed for v1.0.
2. **Public corpus** — periodic snapshot of `*.kbat.yaml` files in
   public repos. Sanitised and de-duplicated. Mechanics TBD at v1.0.

**Test budget**: lifetime tests run on **release CI only** (not per-push,
not nightly). Failure on any lifetime entry blocks the release for the
entire 10-year window.

**Discipline**:

1. **Corpus entries are never deleted.** Resolved-bug corpus entries are
   tagged `[resolved]` but retained. The corpus only grows.
2. **A lifetime test failure NEVER fixes itself by adjusting the test.**
   The choice is: (a) kbat is wrong (it must accept v1 forever) — fix
   kbat; (b) the manifest was always invalid and the original kbat had
   a bug — file a CVE-style schema-bug advisory, mark the entry
   `[honored-original-bug]`, and accept it as-is going forward.

## 7.3.8 v0.5+ test methodology (aspirational)

The following are planned but not shipped:

- **`mcp__kbat__run` MCP tool tests**: protocol-contract, subprocess
  interface, schema round-trip, integration, and regression-matrix
  layers. The MCP server lives in a separate repo (integration partner
  TBD); kbat's responsibility is the subprocess interface stability.
- **PCB ops tested in three tiers**: per-push (subprocess-boundary
  mock under `# MOCK_EXEMPT: subprocess-boundary`), nightly (Docker
  with real `pcbnew`), release (KiCad-version matrix 9.x / 10.x).
- **Per-recipe test contract** (5 layers per recipe: schema
  validation, smoke, idempotence, chip-table coverage, golden
  manifest). 10 recipes × ~50 LOC/recipe = ~500 LOC of test code.
- **Schema migration corpus** at `tests/fixtures/migrations/` covering
  every Transform variant, every optional-field presence permutation,
  every Pydantic discriminator branch, including `requires_judgment:
  true` entries the `--strict` mode must reject.

These items are tracked in ROADMAP §3 (v0.1.x), §4 (v0.5), §5 (v1.0).

# kbat — Roadmap

**Document status**: living spec, written 2026-05-18, grounded in the
v0.1 Phases 0-2 implementation session. Updates land via PR; major shifts
(milestone re-scoping, schema-version promotions) require a paired
z-note decision record.

This document complements two prior artifacts and supersedes neither:

- **`specs/00_kbat_spec.md`** (master spec, v0) — *what kbat is*. The
  layering, the manifest, the ownership boundary with the MCP server,
  the v0.5 / v1.0 milestone sketches. Still authoritative on intent.
- **z-note `8Z-G4p9aw6F0tyWSKQDZQ`** (v0.1 implementation hub) — *what
  shipped in the Day 1 implementation session (2026-05-17)*. The
  validation HARD GATE firing, the 11 agent outputs, the 6 open flags,
  the 12 anamnesis decisions. The original plan budgeted ~one week of
  Week 1 of original plan; the actual session was a single calendar day.

This roadmap synthesizes the two against the empirical evidence: the
master spec's milestones were drawn before code existed; the
implementation session moved several things and surfaced new ones. The
plan below is the post-Day-1 reconciliation (the originally-planned
Week 1 of original plan landed in one calendar day; subsequent weeks
remain on the original calendar budget).

---

## 0. Status snapshot — where v0.1 stands

| Dimension | State |
|---|---|
| Repo | [github.com/AlexK-Notable/kbat](https://github.com/AlexK-Notable/kbat) (public since 2026-10-17), MIT |
| Schema namespace | `kbat.dev/v1alpha1` (locked; bumps reserved for v0.5+) |
| CLI | `kbat apply | plan | rollback | unlock` shipped (`rollback` fully integration-tested in v0.1.1, this release; `unlock` added in v0.1.1 to resolve Flag #6 cross-host lock recovery); `validate` `lint` `diff` `recipes` `manifest upgrade` `doctor` not yet |
| Schematic primitives | 7 of ~25 planned: `set_property`, `add_no_connect`, `snap_labels_to_pins`, `assign_footprints`, `delete_label_at`, `move_label`, `add_pwr_flag` (Flag #1 resolved in v0.1.1: grid-snapped + Hypothesis re-widened) |
| PCB primitives | 0 — deferred entirely to v0.5 |
| Recipes | 0 — Phase 3 deferred |
| Backend | `akcli-kicad >=0.22,<0.23` (subprocess, in-place edits) since 2026-10, replacing `kicad-sch-api ~=0.5.6`; kbat compiles transforms to one akcli op-list and guards every write for KiCad 10 field loss |
| Safety | atomic write (`os.replace` + fsync), `O_CREAT|O_EXCL` lock with host-match reclamation, sidecar state file via shared `kbat/_state_schema.py` Pydantic model (OSTH: One Schema, Two Halves — writer in `_safety.py`, reader in `_rollback.py`, both serialise through the same `StateFile`) |
| Tests | 125 fast tests pass (5 gate tests deselected) on a vendored-fixture suite. The kicad-sch-api round-trip and golden tests were replaced by file-level fidelity tests (`tests/test_fidelity.py`) and per-primitive apply-then-recompile tests; see §7.3 + `docs/testing.md` |
| Validation gate | PASS (2026-10-17, akcli backend) — 5 `@pytest.mark.gate` tests (1.G.a-e) under `tests/gate/test_validation_gate.py`. Input is the genuine pre-cleanup board; output is compared to the stored `cleanup.py` result (`expected_cleanup_output.kicad_sch`), re-apply is byte-identical at 329,896 bytes, and 1.G.e checks no `body_style` / `in_pos_files` / `lib_symbols` entries are lost. (The earlier gate ran against an already-cleaned fixture and silently ran `cleanup.py` on the real board's path; fixed in the same change) |
| MCP integration | **None.** `mcp__kbat__run` deferred to v0.5 |
| Distribution | local `uv` editable; no PyPI publish; no Docker; no docs site |
| Open flags | 1 of 6 open (#4, Cue revisit, MEDIUM) — see Appendix A |

---

## 1. The empirical signal — what Day 1 (the originally-budgeted Week 1 of original plan) actually proved

The hub-note's "validation HARD GATE fired at Phase 1, was diagnosed,
refined, re-run PASS" line is the single most load-bearing piece of
evidence in this roadmap. Three claims it establishes:

**Claim 1 — the manifest-as-moat hypothesis survives empirical contact.**
The 143-line `cleanup.kbat.yaml` reproduces the load-bearing semantics
of the 420-line `cleanup.py` (66% reduction; 57% headroom under the
≤336-line gate) with byte-identical idempotence on re-apply. All
load-bearing facts match — 100 components, 318 labels, 28 no-connects,
10 power flags, 100 footprint assignments — at exact coordinates. The
spec's bet ("declarative wins on this shape") is real.

**Claim 2 — `kicad-sch-api` is lossy on KiCad 10, and the spec must
acknowledge it.** kicad-sch-api 0.5.5 silently prunes orphan
`lib_symbols` and per-symbol KiCad-10 attributes (`body_style`,
`in_pos_files`) on save. The Phase 0C spike predicted this from static
source analysis; the Phase 1E runtime check confirmed it. This is not a
kbat bug — it is upstream pass-through behavior — but it forced the
Phase 1 validation criterion from "tree-equivalent" to
"functional-equivalent modulo declared semantic-preserving
normalizations." The master spec's "byte-exact roundtrip" claim
(§7) is currently aspirational, not achieved, for KiCad-10 inputs.
**v0.5 revisits when kicad-sch-api gains KiCad-10 support upstream.**

*Update 2026-10-17:* resolved by changing backend rather than waiting
on upstream (kicad-sch-api had no release since 2025-11-19). kbat now
writes through akcli, which edits only the objects an op touches; the
gate's 1.G.e criterion confirms all 109 `body_style`, 138
`in_pos_files` and 29 `lib_symbols` entries survive the full cleanup.

**Claim 3 — empirical-first sequencing is the right plan shape for the
rest of v0.1.** The Reframer's POC-first plan put the gate at the end
of the originally-budgeted Week 1 of original plan (day 5 of 5-7);
empirically it fired within a single calendar day's working session,
was diagnosed, was refined, and the project moved on. The compression
from a planned week to an actual day is itself a data point — but it is
also a one-host / one-maintainer / one-project data point. The Claim's
force is "the sequencing was right," not "everything will land this
fast." A waterfall plan would have surfaced the same upstream issue at
v0.5 with an order of magnitude more sunk cost. Phases 3-5 should
preserve the sequencing (ship the smallest demonstrable thing, then
expand) without anchoring forward estimates on the Day-1 velocity. The
post-v0.1.1 evidence base (129 tests, including 4 apply-rollback
integration tests + 4 `@pytest.mark.gate` tests running on every push)
extends the sequencing with CI-enforceable invariants.

---

## 2. Versioning model

Two version axes, decoupled by design (this is the Wave 1 Schema
Governance decision, captured here so it does not need re-litigation):

**Tool version (semver, applies to the `kbat` CLI and Python package).**

- `0.1.0` → first shipped release (end of Phase 4)
- `0.2.x` → optional hardening release if v0.5 slips
- `0.5.0` → mcp__kbat__run + PCB ops + recipe library (the "keystone" release)
- `0.9.x` → release candidates leading into v1.0
- `1.0.0` → schema freeze, MCP Tier-1 removals, BOM subsystem GA

**Schema version (K8s-style staged `apiVersion`).**

- `kbat.dev/v1alpha1` → pre-v0.5 (development); no compatibility promise. Currently in use.
- `kbat.dev/v1beta1` → v0.5 → v1.0-rc (stabilizing); breaking changes between betas with deprecation warnings
- `kbat.dev/v1` → v1.0+ (stable, supported indefinitely with a 10-year minimum freeze + 12-month deprecation window — stronger than K8s', matched to the industrial-PCB-design tool lifetime; see §5.1)

A given kbat release serves multiple `apiVersion`s in parallel (the
"served-versions" model). The tool reads transparently in-memory but
never writes back to a different version without explicit user action
via `kbat manifest upgrade`.

This is the part of the spec to publish first and most loudly: it is
the moat. The master spec §3 example currently shows `apiVersion:
kbat.dev/v1` — that is post-1.0 aspirational and should be footnoted or
amended.

---

## 3. v0.1.x — finish the in-flight release

**Goal**: get the CLI, schema, and validation gate out the door under a
PyPI release, with the empirical evidence (cleanup.py port + gate
output) documented as the headline feature. No new capabilities beyond
Phases 0-2; this is purely a "finish what's started" milestone.

### v0.1.0 release — remaining work (Phases 3 + 4 + 5 from original plan)

The original 6-7 person-week plan deferred Phases 3-5 to the next
implementation session. They map straight into v0.1.0:

**Phase 3 — Recipes scaffolding + eDSL polish + upgrade stub (~1.5w)**.

- Three reference recipes that exercise the machinery, not the
  taxonomy: `usb_c_basic`, `ntc_thermistor`, `screw_terminal_block`.
  These are deliberately trivial — they prove the recipe-loading path
  works for parametric Python recipes with optional YAML wrappers.
  Production-quality recipes are a v0.5 deliverable (see §4).
- `kbat recipes list / show / new` subcommands.
- Drop the spec §4 attribute-magic eDSL syntax (`proj.symbol.J33.value
  = "..."`). Recommended canonical style (per Wave 1 #4): context-manager
  + function-style ops + dict-style read-only accessors. The eDSL is
  documented as a power-user surface, not a first-class one; ~25% of
  v0.1 polish budget.
- **Stub `kbat manifest upgrade` CLI** — no actual migrations yet,
  structure in place for v0.5's first cutover (`v1alpha1 → v1beta1`).
  Subcommand sibling to `manifest validate`, `manifest downgrade`,
  `manifest diff`.

**Phase 4 — Distribution + docs + publish (~1.0w)**.

- PyPI publish via GHA → twine. README leads with `uv tool install
  kbat`; `pipx install kbat` documented as fallback.
- GHCR multi-arch Docker image (`linux/amd64`, `linux/arm64`) on
  `python:3.13-slim`. CI-focused; not the primary install path.
- Docs site on GitHub Pages.
- **Schema published at a stable URL** (`https://schemas.kbat.dev/v1alpha1.json`
  or, until the domain is provisioned, `https://{user}.github.io/kbat/schemas/v1alpha1.json`).
  Referenced from manifests via `# yaml-language-server: $schema=...`.
- Wave 1 flag dispositions documented in CHANGELOG.
- Validation gate re-confirmed on a fresh checkout.
- Tag `v0.1.0`.

**Phase 5 — Buffer (~0.5-1.0w)**. Reserve for spec edits, last-mile
polish, unanticipated friction. Do not consume on scope creep.

### v0.1.x — open-flag fixes in patch releases

Three of the six open flags should ship before v0.5 to avoid carrying
known bugs into the keystone release. Two map to v0.1.x patch
releases; one bundles with the v0.5 backend revisit. See Appendix A
for the full matrix.

- **Flag #1 — `add_pwr_flag` non-idempotent on off-grid coordinates
  (HIGH)** — fix in **v0.1.1**. Recipes will exercise this primitive,
  so it must be solid before v0.5. Recommended fix: snap `(x, y)` to
  the KiCad 1.27mm grid before storing; re-enable the unconstrained
  Hypothesis property test. (Phase 2H constrained the property test to
  grid-aligned coords as a temporary mask.)
- **Flag #6 — KbatLock cross-host policy doc-debt (LOW)** — fix in
  **v0.1.1**, doc-only. Add a paragraph to `docs/safety.md` explaining
  that an honest `kbat apply` that crashed on host A cannot be
  reclaimed by an `apply` running on a different mount of the same
  project (NFS, container with different hostname). Recommend `kbat
  --unlock` as the manual-recovery path — implement the subcommand in
  v0.2 or v0.5.
- **Flag #5 — Hypothesis runtime budgets at scale (LOW)** — handle
  ad-hoc in v0.1.x; promote to formal CI-budget policy in v0.5.

---

## 4. v0.5 — the keystone release

**Goal**: kbat becomes useful to AI agents end-to-end via the MCP
transport, ships a credible production recipe library, gains PCB
reach, and promotes the schema to `v1beta1` with real migration
tooling exercising the Phase 3 stub.

### 4.1 `mcp__kbat__run` — the keystone integration

Single new MCP tool exposed by an MCP integration target (TBD —
the ecosystem of KiCad MCP server implementations is fragmented as
of v0.1.x; the specific integration partner will be selected during
v0.5 scoping). Accepts a manifest payload (full YAML or JSON), runs
`kbat apply` on it, returns `{applied, skipped, errors, diff,
signature}`. The ~100× token win on the dominant agent workflow comes
entirely from this tool; the eDSL never enters that path because MCP
agents have no Python interpreter to run `with open_project(...)`
against.

Two concrete sub-deliverables:

- The tool implementation lives in the MCP server repo (whichever
  integration target v0.5 selects). kbat's contribution is a stable
  subprocess interface (exit codes, JSON stdout) and a regression
  matrix that any MCP server implementation can pin against.
- **Tier-1 MCP deprecation warnings** (not removals): `export_gerber`,
  `export_bom`, `export_position_file`, `export_3d`, `export_svg`,
  `export_pdf`, `place_component_array`, `annotate_schematic`,
  `enrich_datasheets`, `download_jlcpcb_database`. Warning text points
  to the kbat equivalent. Removal is v1.0.

### 4.2 PCB capability subset

Per spec §8, v0.1 deferred PCB entirely. v0.5 ships the four
"intentionally modest" ops:

- Mass footprint swap on PCB
- Net class application by pattern
- Design rule profile (JLCPCB-2L / PCBWay-4L / custom)
- Manufacturing output bundle (gerbers + drill + BOM + P&P + 3D + hash)

The subprocess-per-op SWIG isolation pattern (spec §8) is mandatory.
Each PCB op spawns a Python subprocess that imports `pcbnew`, does its
one job, and exits. Half-life of SWIG is 12-18 months (deprecated K9,
removed K11) — `kicad-python` over IPC takes over when KiCad 11 lands
headless support. Subprocess isolation makes that swap internal.

**`kbat doctor` ships as first-class** for KiCad-installation
detection — necessary for both the SWIG path and the eventual IPC
path.

### 4.3 Recipe library — 10 production recipes

Per the Wave 1 #1 deep-dive decision: 10 recipes, parametric Python
with curated chip-tables, sparse YAML wrappers (5-10 max) for common
parametrizations. The full roster is in Appendix D. Summary:

- **Tier A must-ship (5)**: `buck_converter`, `rp2040_socket`,
  `esp32_s3_module`, `fan_channel`, `ntc_thermistor`
- **Tier B high-value (4)**: `lipo_charger`, `rs485_half_duplex`,
  `dc_motor_hbridge`, `usb_c_pd_sink`
- **Tier C round-out (1)**: `max31865_rtd`

`fan_channel`, `usb_c_pd_sink`, and `max31865_rtd` are the three
positioning bets — kbat-specific differentiation against atopile /
circuit-synth / faebryk / Common Parts Library consensus
(dogfooding Klipper-companion; 2025-era USB-PD ubiquity;
precision-temp upgrade path from NTC).

`kbat init --recipe <name>` ships at v0.5 for project bootstrap.

### 4.4 Schema bump: `v1alpha1 → v1beta1`

The Phase 3 stub becomes real. Concrete migrations: any field renames
or shape changes accumulated during v0.1.x stabilization. The migration
tool ships with:

- `kbat manifest upgrade --to v1beta1` (per-field structured diff,
  not textual)
- `kbat manifest downgrade --to v1alpha1` (lossy paths warned, not
  silently dropped)
- `--strict` flag for CI mode (fail on judgment-required changes)
- `--report PATH` for audit-trail output
- Atomic write + backup + idempotent + no side effects on
  `.kicad_sch`, `.kbat-lock`, or `.kbat-state.json`

A served-versions release: kbat 0.5 reads `v1alpha1` and `v1beta1`,
writes `v1beta1` by default. `v1alpha1` is deprecated but supported
for the entire v0.5 → v1.0 window.

### 4.5 `kicad-core` shared library extraction

Per spec §12, extract the parser, IR, signature, lockfile,
libcache, and vendor cache into `kicad-core`. Both kbat and the
KiCad MCP server consume it. Two design constraints:

- The extraction is internal: no manifest-visible breakage.
- Kicad-core ships independently to PyPI with its own version (1.0
  appropriate; the contract is small enough to commit to).

### 4.6 KiCad-10 lossiness revisit (Flag #3)

> **Obsolete (2026-10-17).** Trigger 3 fired in effect: kbat moved to
> the akcli backend, which preserves KiCad 10 fields. Kept for history.

Track upstream kicad-sch-api KiCad-10 support. Three triggers, any
one promotes the Phase 1 1.G.b criterion back to strict
tree-equivalence:

1. kicad-sch-api 0.6.x or 0.7.x adds `body_style` / `in_pos_files`
   preservation
2. kicad-sch-api gains an explicit KiCad-10 mode flag
3. An alternative actively-maintained KiCad-10 parser emerges

If none have landed by v0.5 freeze, the README KiCad-10 caveat
remains, but the validation gate is re-run against the latest
kicad-sch-api with the orphan-lib_symbols normalization unchanged.

### 4.7 Re-examining the `kicad-sch-api ~=0.5.6` pin

> **Obsolete (2026-10-17).** The pin and every workaround below were
> removed with the move to akcli. Kept for history.

Flag #2 (the four upstream API quirks; Appendix C). If
kicad-sch-api 0.5.7 / 0.6.x / 0.7.x fixes any of:

- Reference validator rejecting underscores
- `set_property("Value"|"Footprint", v)` silent no-op
- `Point` non-subscriptable
- `LabelElement.move(...)` scalar-vs-tuple signature

…the in-tree workarounds become removable. File upstream issues
during v0.1.x (see §7 — Upstream Workstream) so the fixes are
candidates by v0.5.

---

## 5. v1.0 — stable schema, full ecosystem

**Goal**: kbat.dev/v1 frozen, MCP Tier-1 deprecations removed, BOM
sourcing subsystem GA, docs site complete.

### 5.1 Schema freeze: `v1beta1 → v1`

The v0.9.x release-candidate window stabilizes `v1beta1`; v1.0.0
promotes to `v1` GA. From this point:

- Breaking changes require a `v2alpha1` proposal.
- The `kbat.dev/v1` URL guarantees stable semantics for at least 10
  years post-v1.0 (matches the typical industrial-PCB-design tool
  lifetime — kbat's user base lives in this time horizon).
- `kbat manifest upgrade --to v1` runs the cutover; `v1alpha1` is
  removed entirely; `v1beta1` enters the 12-month deprecation
  window.

### 5.2 MCP Tier-1 removal

Tools warned in v0.5 are removed entirely. Removal is mechanical at
this point; the warning period is the migration window.

### 5.3 BOM sourcing subsystem

Per the Wave 1 #2 deep-dive design (`lookup_mpn`, `find_alternatives`,
`enrich_bom`). Three providers — JLCPCB, LCSC, DigiKey — async-first
with sync wrappers. Caching at `<project>/.kbat-cache/parts.db`
(SQLite); three-tier TTL (stock 24h, pricing 7d, lifecycle/datasheet
90d). Raw vendor responses stored separately from merged Parts so
reconciliation rules can change without re-fetching.

**Schema for `Part` and `BomDocument` should be published at v0.5**
(moat play), ahead of the v1.0 implementation. This decouples the
contract from the implementation timeline.

Nexar deferred to v1.1+ (paywalled).

### 5.4 Schematic ↔ PCB sync helpers

The smallest useful subset of the spec §10 v1.0 line item. Specific
ops to be determined during v0.5 implementation when the PCB ops are
exercised against real projects.

### 5.5 Custom-rule plugin API

ERC-style custom validation rules ship as a plugin point —
not in-tree code. Plugin discovery via Python entry points (`kbat.rules`).

### 5.6 Docs site

GitHub Pages, MkDocs (Material theme). Sections: schema reference,
recipes catalog, migration guide from kicad-skip, MCP integration
guide, custom-rule plugin tutorial.

### 5.7 CI presets

GitHub Actions composite actions and reusable workflows for the three
typical jobs: `kbat plan` (per-PR check), `kbat apply` + `kbat validate`
(merge gate), and `kbat manifest upgrade --strict` (schema-bump CI
guard). GitLab CI as long tail (community contribution welcome; not
ship-blocking).

---

## 6. v1.0+ — the deferred-deliberately list

These are not on the path to v1.0, but they are on the watch list.

### 6.1 Cue revisit

Five falsifiable trigger conditions; **any one** opens the migration
window. Estimated migration cost: 4-8 person-weeks of focused
engineering (schema port + tooling + docs), plus a 5-10%-of-release
ongoing maintenance tax. Full list in Appendix B; restated here so the
release-train understands the gate:

1. AI training-data gap closes — foundation models achieve first-try-valid
   Cue generation at parity with YAML on a 10-op schema-prompted manifest
2. `cue-py` reaches v1.0 stable (pip-installable, no Go binary required)
3. kbat develops a manifest-composition use case (imported fragments + overrides)
4. A kbat-specific Cue codegen tool emerges
5. A Cue-vs-YAML benchmark in LLM-config-generation literature shows parity

The decision to ship YAML for v0.1 (logged as anamnesis decision
`decision_ed3822ac4e94`) was three-pronged: AI training-data
asymmetry of ~4 orders of magnitude favoring YAML; pycue
unmaintained, cue-py unreleased; Pydantic already triple-duty
(manifest validation, op signatures, recipe Params). The triggers
falsify each of those.

### 6.2 Extended PCB capability

Per spec §8: zone/pour management, 3D model assignment,
component placement by net group. Gated on KiCad 11 IPC stabilization.

### 6.3 Distribution expansion

Homebrew tap (v1.0 if community demand), conda-forge (v1.0+),
Docker Hub mirror (until GHCR rate-limiting bites). KiCad PCM is
permanently rejected (wrong category — kbat is a CLI, not a plugin).

### 6.4 Nexar BOM integration

Paywalled API; pulled in if user demand crystallizes.

---

## 7. Cross-cutting workstreams

These run alongside the milestone train.

### 7.1 Upstream contributions workstream

> **Obsolete (2026-10-17)** for kicad-sch-api, which kbat no longer
> uses. Gaps found in akcli (e.g. `delete_object` `match.at` requires
> exact mils, so off-grid labels can only be deleted by uuid) are the
> new candidates.

File four upstream issues against kicad-sch-api during v0.1.x (Appendix
C). For each: minimal reproducer, root-cause analysis, suggested fix,
link to the kbat in-tree workaround. Owner: maintainer who is closest
to the workaround.

Target: at least two of four fixed by v0.5. If kicad-sch-api gains a
maintainer-bandwidth problem, evaluate forking with a scoped patchset
(reference-validator regex + property-setter sync) rather than
maintaining workarounds in perpetuity.

### 7.2 Schema governance workstream

The Wave 1 #6 design lands in v0.5; ongoing the workstream owns:

- The `https://schemas.kbat.dev/` URL and per-build immutable URLs
  (`https://schemas.kbat.dev/v1/2026-08-15.json`)
- The 12-month deprecation window: each release publishes a
  deprecation-status report (which apiVersions are served, which are
  warning, which are removed in the next release)
- Migration test suite: golden manifests for each version pair, run on
  every release

### 7.3 Testing strategy workstream

The diamond-pyramid target (Wave 1 #3) and the mock / Hypothesis /
CI-gate disciplines that operationalise it are documented in
[`docs/testing.md`](testing.md). Headline: pyramid is a 6-month target
not a current measurement; v0.1.1 shipped the validation gate as a
CI artifact (4 `@pytest.mark.gate` tests, vendored fixtures, GHA
`fast` + `gate` jobs) and the mock-policy / Hypothesis-mask lints
(`scripts/check_mock_policy.py`, `scripts/check_hypothesis_masks.py`).
The integration coverage requirement (any cross-module contract has
at least one integration test) is enforced by the OSTH discipline —
see `docs/testing.md` §7.3.3.

### 7.4 Distribution workstream

PyPI primary via `uv tool install`, GHCR secondary via multi-arch
Docker. The PCB-cliff at v0.5 introduces a layered detection model:
kbat detects the local KiCad install and shells into its bundled
Python (`kipython`) for PCB ops; CI/container path bundles
`ghcr.io/<org>/kbat:<kicad-version>`. `kbat doctor` validates
detection.

---

## 8. Risk register

Top-five risks ordered by combined likelihood × impact.

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|------------|--------|-----------|
| 1 | akcli (the backend since 2026-10) changes its op protocol or stalls | Medium | Medium | Pinned to `<0.23`; edit lists declare `protocol_version`; the fidelity guard and gate 1.G.e catch silent field loss on upgrade. Replaces the old risk "kicad-sch-api KiCad-10 support doesn't land", which is closed |
| 2 | MCP server maintainers delay `mcp__kbat__run` | Medium | High | Spec the tool early (open the integration discussion during v0.1 publish). Fallback: ship a stub MCP server reference implementation in `examples/` to demonstrate feasibility |
| 3 | KiCad 11 IPC slips its window, leaving PCB ops on SWIG longer than 18mo | Medium | Medium | Subprocess-per-op pattern absorbs this; cost is performance not correctness. PCB v0.5 ops scoped to operations where 200-500ms overhead per op is acceptable |
| 4 | Schema accumulates a breaking change between `v1beta1` and `v1` that the migration tool can't auto-resolve | Low | High | Per-field deprecation lifecycle with two-release warning; `--strict` mode in `manifest upgrade` blocks judgment-required changes from automation; manual override always available |
| 5 | A recipe-content choice (chip selection in a parametric recipe) goes EOL between v0.5 and v1.0 | Medium | Low | Granularity policy IS the mitigation: chip table is parametric, not encoded in recipe names. EOL = swap one chip table entry, not deprecate a recipe |

Risk #6 (Cue spike runs over budget) is already resolved — the spike
landed cleanly with YAML chosen and Cue archived for v1.0+ revisit.

---

## Appendix A — Open flags resolution matrix

From hub-note `8Z-G4p9aw6F0tyWSKQDZQ` ("Open flags"), with target
milestone assignment:

| # | Severity | Flag | Target | Status | Source |
|---|----------|------|--------|--------|--------|
| 1 | HIGH | `add_pwr_flag` not idempotent on off-grid coordinates | **v0.1.1** | **RESOLVED in v0.1.1** — Phase 2.4 shipped `add_pwr_flag` snap-to-grid (1.27 mm) and re-widened the Hypothesis strategy atomically with the fix; the `# HYPOTHESIS_MASK: Flag #1` annotation has been removed | `g-YWanREKgb-5tFgf_2Mw` |
| 2 | MEDIUM | 4 kicad-sch-api 0.5.5 API quirks (Appendix C) | **v0.5** | **OBSOLETE 2026-10-17** — kicad-sch-api removed with the move to akcli; all four workarounds (incl. `relax_validation`) deleted | `AdV12nGeybO3g3JOdI-aa` |
| 3 | MEDIUM | KiCad-10 lossy fields caveat | **v0.5** (revisit when kicad-sch-api gains KiCad-10 support) | **RESOLVED 2026-10-17** — akcli backend preserves the fields; README caveat removed; gate criterion 1.G.e enforces it | `6Ql8zLmuVt5bVHf40OEUh` |
| 4 | MEDIUM | Cue v1.0+ revisit triggers (5 falsifiable conditions) | **v1.0+** (Appendix B) | **OPEN** — deferred to v1.0+, dependent on `cue-py` reaching v1.0 stable | `3SWfu5Hfwx4598KYH9kKC` |
| 5 | LOW | Hypothesis test runtime budgets at scale | **v0.5** (formal CI-budget policy) | **RESOLVED 2026-10-17** — property tests now run in memory against a `BoardState`; health-check suppression and the `# HYPOTHESIS_MASK: Flag #5` annotation removed | `g-YWanREKgb-5tFgf_2Mw` |
| 6 | LOW | KbatLock cross-host reclamation policy doc-debt | **v0.1.1** | **RESOLVED in v0.1.1** — Phase 2.4 shipped `kbat unlock` subcommand (with `--force`) AND expanded `kbat/_safety.py` module docstring with the cross-host policy text; manual-recovery path is now first-class | `EvxjWSffJ9qrJZcNsEwtX` |

---

## Appendix B — The 5 Cue revisit triggers

Restated from `3SWfu5Hfwx4598KYH9kKC`. **Any one** trigger
opens the migration window.

1. **AI training-data gap closes**: foundation-model first-try-valid
   Cue generation rate >90% on a 10-op schema-prompted manifest, parity
   with YAML.
2. **`cue-py` reaches v1.0 stable** (pip-installable, no Go binary
   required) — track `cue-lang/cue#3100`.
3. **kbat develops a manifest-composition use case** (recipe-library /
   shared-transform-library pattern with imported fragments + local
   overrides).
4. **A kbat-specific Cue codegen tool emerges** (analogous to
   `cue-gen` for Istio) that amortizes schema-as-contract across
   recipe-authoring, CLI, plan diffs.
5. **A Cue-vs-YAML benchmark in LLM-config-generation literature**
   (a CloudEval-YAML successor including Cue) shows parity.

Migration cost: **4-8 person-weeks** for the schema port + tooling +
docs. Phased: Phase A schema port (2-3w), Phase B loader integration
(1-2w), Phase C user-facing changes (1-2w), Phase D recipe library
compat (1w). Plus a 5-10%-of-release ongoing maintenance tax.
**Gating dependency**: `cue-py` ≥ v0.5 / beta-stable; if shell-out to
Go binary is the only path, the estimate balloons.

---

## Appendix C — kicad-sch-api API quirks (upstream issues to file)

> **Historical (2026-10-17).** kbat no longer depends on kicad-sch-api;
> these quirks no longer affect it.

From `AdV12nGeybO3g3JOdI-aa`. Each gets an upstream issue
during v0.1.x.

**Quirk 1 — Reference validator rejects underscores (BLOCKS save).**
The legacy `SchematicValidator._valid_reference_pattern` rejects
references like `U_MCU`, blocking `Schematic.save()` on any schematic
using underscore conventions. kbat workaround: per-instance
monkey-patch via `relax_validation(sch)`. Upstream fix: relax the
regex to `^(#[A-Z]+[0-9]+|[A-Z][A-Z0-9_]*[A-Z0-9]?|[A-Z]+\?)$`.

**Quirk 2 — `Component.set_property("Value"|"Footprint", v)` is a
silent no-op (HIGH-severity silent-data-loss footgun).** The property
dict updates in memory but does not propagate to the synced `.value` /
`.footprint` attributes nor to serialized output. kbat workaround:
special-case `Value`, `Footprint`, `Reference` to direct attribute
assignment; fall through to `set_property()` only for other properties.

**Quirk 3 — `Point` objects are not subscriptable.** `pos[0]` raises
`TypeError`. kbat workaround: `pos.x` / `pos.y` everywhere.

**Quirk 4 — `LabelElement.move(...)` takes two scalars, not a tuple.**
Signature is `move(x: float, y: float)`. kbat workaround: explicit
two-argument calls.

**Re-pin trigger**: if 0.5.7 / 0.6.x / 0.7.x fixes any of these, the
workaround can be removed in the same release.

---

## Appendix D — v0.5 recipe roster (10 recipes)

From Wave 1 #1 (`AAQXZ94gF1TdnCo6CaQ65`), anamnesis decision
`decision_4d316edf298a`. All parametric Python; sparse YAML wrappers
for common parametrizations.

**Tier A — Must-ship (5)**:

| Path | Notes |
|------|-------|
| `power/buck_converter` | Parametric on chip family (TPS54331 / MP2307 / TPS62130 / AP63203) |
| `mcu/rp2040_socket` | Pico-pinout socket; ties to Klipper-companion validation |
| `mcu/esp32_s3_module` | Common WROOM/MINI variants |
| `actuator/fan_channel` | Dogfoods Klipper-companion (`drop-pico-ncs` + `snap-labels` validated against this shape in v0.1) |
| `sensors/ntc_thermistor` | Promoted from v0.1 reference recipe |

**Tier B — High-value (4)**:

| Path | Notes |
|------|-------|
| `power/lipo_charger` | MCP73831 baseline |
| `comms/rs485_half_duplex` | MAX485 baseline |
| `actuator/dc_motor_hbridge` | DRV8833 baseline |
| `connectors/usb_c_pd_sink` | CH224K / FUSB302; 2025-era ubiquity bet |

**Tier C — Round-out (1)**:

| Path | Notes |
|------|-------|
| `sensors/max31865_rtd` | Precision-temp upgrade path from NTC (recommended over `ina228`) |

7 of 10 overlap with the de-facto consensus across atopile,
circuit-synth, ruben-iteng, faebryk, and the Common Parts Library. The
three positioning bets — `fan_channel`, `usb_c_pd_sink`, `max31865_rtd`
— are deliberate kbat differentiation.

---

## References

- `specs/00_kbat_spec.md` — master spec (v0)
- Hub note `8Z-G4p9aw6F0tyWSKQDZQ` — v0.1 implementation hub
- Plan note `0izyKtvZr5EthRrpD7Yqn` — original 6-7 week implementation plan
- Phase 1F refinement `6Ql8zLmuVt5bVHf40OEUh` — KiCad-10 lossiness diagnosis
- Cue spike `3SWfu5Hfwx4598KYH9kKC` — YAML-vs-Cue decision and revisit triggers
- Wave 1 deep-dives: `AAQXZ94gF1TdnCo6CaQ65` (recipes), `4DqvV2mHbrR0gh1hpqe81`
  (schema governance), `sfVfyB_Wp6D9i6p_0Zqwh` (BOM), `3fWXuyhKyynK9hb6fvsLd`
  (distribution), `CtVYZGZycypzKP0L81Uhn` (eDSL), `HE4_MvAS391OEYJ6M_G3h`
  (testing)
- Anamnesis decisions: `decision_64592c5748b0` (v0.1 implementation synthesis),
  `decision_ae63dffa075f` (1.G.b refinement), `decision_ed3822ac4e94` (YAML over Cue),
  `decision_4d316edf298a` (10-recipe roster), `decision_eeb4c5ec3c25` (testing pyramid),
  `decision_99f10e4f9c95` (KbatLock host-match policy)

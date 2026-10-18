# Validation gate fixtures (vendored — do not edit)

These files are a verbatim snapshot of the upstream KiCad experiment that
the Phase 1 validation gate (`kbat._validation`, criteria 1.G.a-e) runs
against. They are vendored into the repository so the gate is portable —
CI, contributors, and the `tests/gate/` pytest suite no longer depend on
`/home/komi/notes/kicad/...` existing on disk.

**Do NOT hand-edit these files.** If the upstream snapshot legitimately
needs to change, re-vendor and update the provenance hashes.

## Provenance

| File | Source | SHA-256 | Date copied |
|------|--------|---------|-------------|
| `kicad_experiment.kicad_sch` | `/home/komi/notes/kicad/kicad_experiment/kicad_experiment.kicad_sch.bak.20260517-121000` (the board before cleanup.py's first run) | `5d768ca1ff07900d501084f5f08524c8a1c9c8659c0facd3ac3ad16431a33671` | 2026-10-17 |
| `expected_cleanup_output.kicad_sch` | cleanup.py (kicad-skip 0.2.5) run once on a copy of the file above | `2ec2ecd3b7be1b464ae73ce077cce4453d1ff086685611aae312c4977cee1b6a` | 2026-10-17 |
| `cleanup.py` | `/home/komi/notes/kicad/kicad_experiment/cleanup.py` | `d092769843b61466f3fc9c86fda40bcca51750f5326b9743bc3f845e29aeb458` | 2026-05-18 |
| `cleanup.kbat.yaml` | `/home/komi/notes/kicad/kicad_experiment/cleanup.kbat.yaml` | `28538f32b470702f16ff111093375234b287a40551c3733bb257f9e942aaa2f7` | 2026-05-18 |

Until 2026-10-17 `kicad_experiment.kicad_sch` was a copy of the board
*after* cleanup, so the manifest's ops were mostly no-ops against it.

## Why these files?

- `kicad_experiment.kicad_sch` — the pre-cleanup schematic the gate edits.
- `expected_cleanup_output.kicad_sch` — cleanup.py's output on that
  schematic: the reference for criterion 1.G.b (functional equivalence).
  Vendored so the gate never executes cleanup.py.
- `cleanup.py` — the imperative script the manifest replaces. Only read
  for criterion 1.G.a (LOC); never run. Its hardcoded `SCH` path points at
  the author's real board, which is why the gate no longer executes it.
- `cleanup.kbat.yaml` — the declarative manifest under test. Its
  `metadata.schematic` is also the real board; every criterion passes
  `schematic_override` / `--schematic` pointing at a tmp copy instead.

## Related

- Gate harness: `kbat/_validation.py`
- Pytest wrappers: `tests/gate/test_validation_gate.py`
- CI integration: `.github/workflows/ci.yml` (the `gate` job)
- Env override: `KBAT_VALIDATION_FIXTURE_DIR`

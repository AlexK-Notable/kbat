# Validation gate fixtures (vendored — do not edit)

These files are a verbatim snapshot of the upstream KiCad experiment that
the Phase 1 validation gate (`kbat._validation`, criteria 1.G.a-d) runs
against. They are vendored into the repository so the gate is portable —
CI, contributors, and the `tests/gate/` pytest suite no longer depend on
`/home/komi/notes/kicad/...` existing on disk.

**Do NOT hand-edit these files.** If the upstream snapshot legitimately
needs to change, re-vendor by re-running the copy step below and
updating the provenance hashes.

## Provenance

| File | Upstream path | SHA-256 (at vendoring) | Date copied |
|------|---------------|------------------------|-------------|
| `kicad_experiment.kicad_sch` | `/home/komi/notes/kicad/kicad_experiment/kicad_experiment.kicad_sch` | `f5e4da7069dbe853954c94fb14bfe9403f8d8a5b47c908e96ae93dcf227cbd4f` | 2026-05-18 |
| `cleanup.py` | `/home/komi/notes/kicad/kicad_experiment/cleanup.py` | `d092769843b61466f3fc9c86fda40bcca51750f5326b9743bc3f845e29aeb458` | 2026-05-18 |
| `cleanup.kbat.yaml` | `/home/komi/notes/kicad/kicad_experiment/cleanup.kbat.yaml` | `28538f32b470702f16ff111093375234b287a40551c3733bb257f9e942aaa2f7` | 2026-05-18 |

Re-vendoring command:

```bash
cp /home/komi/notes/kicad/kicad_experiment/kicad_experiment.kicad_sch tests/fixtures/validation_gate/
cp /home/komi/notes/kicad/kicad_experiment/cleanup.py                  tests/fixtures/validation_gate/
cp /home/komi/notes/kicad/kicad_experiment/cleanup.kbat.yaml           tests/fixtures/validation_gate/
sha256sum tests/fixtures/validation_gate/*
```

## Why these three files?

- `kicad_experiment.kicad_sch` — the schematic the gate edits.
- `cleanup.py` — the imperative oracle script. Criterion 1.G.b compares
  its output against kbat's output for functional equivalence.
- `cleanup.kbat.yaml` — the declarative manifest under test. Criterion
  1.G.a measures its LOC against `cleanup.py`'s LOC.

## Caveats

`cleanup.py` retains its original hardcoded paths (`SCH = Path(...)`,
`sys.path.insert(...)`). The validation harness
(`kbat._validation._run_cleanup_py_against`) string-patches the `SCH`
constant in a temporary copy before execution, so the hardcoded paths
are not actually used during the gate run. The `sys.path.insert` to a
KiCAD-MCP-Server venv is similarly inert when kicad-sch-api is already
importable from the active interpreter (which the harness ensures via
`KBAT_VALIDATION_PY_INTERPRETER` defaulting to `sys.executable`).

## Related

- Gate harness: `kbat/_validation.py`
- Pytest wrappers: `tests/gate/test_validation_gate.py`
- CI integration: `.github/workflows/ci.yml` (the `gate` job)
- Env overrides: `KBAT_VALIDATION_FIXTURE_DIR`, `KBAT_VALIDATION_PY_INTERPRETER`

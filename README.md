# kbat

A CLI batch tool for KiCad schematic transforms. Declarative,
manifest-driven, idempotent.

## Status

v0.1.0.dev0 — pre-release. Schema: `kbat.dev/v1alpha1`.

## Install

```bash
uv tool install kbat
```

Fallback:

```bash
pipx install kbat
```

## What it does

kbat applies declarative YAML manifests against KiCad schematic
files. A manifest names a sequence of typed operations (set property,
add no-connect, snap labels to pins, assign footprints, etc.) and
kbat applies them transactionally and idempotently, with a readable
diff via `kbat plan` and a rollback path via `kbat rollback`
(end-to-end integration-tested in v0.1.1; see "Known limitations"
below for the single-step undo caveat). A `kbat unlock` subcommand
provides the manual recovery path for stale `.kbat-lock` files left
behind by a crashed apply.

Positioning: kbat is to schematic transforms what `make` is to build
steps and what `kubectl apply` is to cluster state. The manifest is
the durable artifact; the runner is replaceable.

## Schema

All manifests declare `apiVersion: kbat.dev/v1alpha1`. The schema is
versioned K8s-style (`v1alpha1` → `v1beta1` → `v1`); breaking changes
are gated by version bumps and migration tooling.

## Validated against

v0.1.x has been exercised on Linux against **one Klipper-companion
project** (`kicad_experiment`) via the in-tree validation gate at
`tests/fixtures/validation_gate/`. The gate is now a CI artifact: four
`@pytest.mark.gate` tests in `tests/gate/test_validation_gate.py` run
on every push against vendored fixtures, with no host-specific paths.
The 129-test suite (3 skipped, 0 failed) — including 4 apply-rollback
integration tests and the 4 gate tests — runs reproducibly on any
checkout. v0.5 will broaden the evidence base to additional projects;
treat v0.1.x as a one-project validation, not field-tested across
diverse KiCad designs.

## Design rationale

The full implementation plan and design rationale lives in the
project's z-note knowledge base. The entry point is the plan hub
note: `0izyKtvZr5EthRrpD7Yqn`.

## Supported KiCad versions

> **kbat v0.1 targets KiCad 9 only.** The pinned `kicad-sch-api ~=0.5.6`
> backend was built against the KiCad 9 schema. Running kbat against a
> KiCad-10-authored `.kicad_sch` file results in **silent data loss** on
> save for KiCad-10-only fields — see "KiCad 10 silent-data-loss caveat"
> below.

If you are on KiCad 10, downgrade your project files to KiCad 9 or wait
for the v0.5 backend revisit (tracked in `docs/ROADMAP.md` §4.6).

## KiCad 10 silent-data-loss caveat

kbat v0.1 ships with `kicad-sch-api` 0.5.x, which was written against
the KiCad 9 schema and does not preserve the following KiCad-10-only
per-symbol attributes when re-serializing a `.kicad_sch` file:

- `body_style`
- `in_pos_files`

It also prunes orphan `lib_symbols` entries (symbol library definitions
that are no longer referenced by any component instance).

These differences are explicitly excluded from kbat's Phase 1 validation
gate (criterion 1.G.b is a **functional-equivalence** check rather than
a strict tree-equivalence check — load-bearing fields like components,
nets, labels, no-connects, footprints, and power flags MUST match, while
the lossy attributes above and orphan definitions are ignored).

v0.5 will revisit strict equivalence once `kicad-sch-api` gains
KiCad-10 support upstream.

## Known limitations

- **KiCad 10 inputs lose `body_style`, `in_pos_files`, and orphan
  `lib_symbols` on save.** See the "KiCad 10 silent-data-loss caveat"
  section above. Mitigation: stay on KiCad 9 until v0.5 backend revisit.
- **Single-step undo only.** `kbat rollback` restores the
  snapshot-before-last-apply. There is no multi-step history; running
  `kbat apply` twice in succession means the first apply's snapshot is
  unreachable via the CLI (it is still on disk under
  `.kbat-snapshots/`). Multi-step rollback is a v0.5+ consideration.
  Until then, rely on your VCS (git) for deeper undo.

## License

MIT — see [LICENSE](LICENSE).

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

## How it edits files

kbat does not parse and re-save schematics itself. It reads the board
state, compiles every transform in the manifest into one
[akcli](https://github.com/tipoLi5890/akcli) op-list, and applies it with
a single `akcli draw --apply`, which edits the file in place (only the
objects an op touches change) and writes atomically. kbat adds what
akcli's op vocabulary does not have — selector-style transforms such as
`snap_labels_to_pins` and `assign_footprints` (by `lib_id`), a
hand-writable YAML manifest, and idempotence decided against the file's
actual contents: a manifest that is already applied compiles to no ops
and the file is not rewritten.

After every write kbat checks that no KiCad 10 per-symbol fields
(`body_style`, `in_pos_files`) or embedded `lib_symbols` were lost and
that every moved label kept its formatting; if not, it restores the
original file and reports the error. akcli keeps its own `.akcli/`
workspace (backups, journal) next to the schematic.

## Schema

All manifests declare `apiVersion: kbat.dev/v1alpha1`. The schema is
versioned K8s-style (`v1alpha1` → `v1beta1` → `v1`); breaking changes
are gated by version bumps and migration tooling.

## Validated against

kbat has been exercised on Linux against **one Klipper-companion
project** (`kicad_experiment`, KiCad 10) via the in-tree validation gate
at `tests/fixtures/validation_gate/`. The gate's input is the board as it
was before its cleanup script ran; five `@pytest.mark.gate` tests in
`tests/gate/test_validation_gate.py` check that the cleanup manifest
reproduces the script's result, re-applies as a byte-identical no-op, and
keeps every KiCad 10 field. Treat this as a one-project validation, not
field-tested across diverse KiCad designs.

## Design rationale

The full implementation plan and design rationale lives in the
project's z-note knowledge base. The entry point is the plan hub
note: `0izyKtvZr5EthRrpD7Yqn`.

## Supported KiCad versions

KiCad 9 and 10 schematic files, as far as `akcli-kicad` 0.22 supports
them; the gate runs on a KiCad 10 board. Until 2026-10 kbat used
`kicad-sch-api` 0.5.x, which dropped KiCad 10's `body_style` /
`in_pos_files` attributes and unused `lib_symbols` on every save; that
backend is gone.

## Known limitations

- **Moving a label replaces it.** akcli has no label-move op, so kbat
  moves a label by deleting it and adding one at the target with the same
  name, rotation and default formatting. The label gets a new uuid; a
  label with custom formatting (font size, justification) would be
  flattened, so kbat refuses that write and restores the file instead.
- **`add_pwr_flag` needs the `power:PWR_FLAG` symbol** embedded in the
  schematic or resolvable from a KiCad library table (akcli also looks in
  `/usr/share/kicad`); otherwise akcli refuses the write.
- **Single-step undo only.** `kbat rollback` restores the
  snapshot-before-last-apply. There is no multi-step history; running
  `kbat apply` twice in succession means the first apply's snapshot is
  unreachable via the CLI (it is still on disk under
  `.kbat-snapshots/`). Multi-step rollback is a v0.5+ consideration.
  Until then, rely on your VCS (git) for deeper undo. akcli's own
  `akcli undo` (rotated backups under `.akcli/backups/`) is a second
  path, independent of kbat's snapshots.

## License

MIT — see [LICENSE](LICENSE).

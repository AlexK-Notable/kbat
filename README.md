# kbat

[![ci](https://github.com/AlexK-Notable/kbat/actions/workflows/ci.yml/badge.svg)](https://github.com/AlexK-Notable/kbat/actions/workflows/ci.yml)

A CLI batch tool for KiCad schematic transforms. Declarative,
manifest-driven, idempotent.

## Status

v0.1.0.dev0 — pre-release, not published on PyPI. Schema:
`kbat.dev/v1alpha1`. Requires Python 3.11+; tested on Linux only.

## Install

```bash
uv tool install git+https://github.com/AlexK-Notable/kbat
```

or `pipx install git+https://github.com/AlexK-Notable/kbat`. The file
backend, [akcli-kicad](https://pypi.org/project/akcli-kicad/), installs
from PyPI with it. KiCad itself is not needed to run kbat, but a symbol
that a transform adds (`add_pwr_flag`) must be embedded in the schematic
or resolvable from a KiCad library.

## Quick start

Write a manifest next to your schematic, e.g. `board.kbat.yaml`:

```yaml
apiVersion: kbat.dev/v1alpha1
kind: Project
metadata:
  name: my-board
  schematic: board.kicad_sch   # relative to this manifest

transforms:
  - id: r1-value
    op: set_property
    selector: {reference: R1}
    property: Value
    value: 10k

  - id: smd-footprints
    op: assign_footprints
    by_lib_id:
      Device:R: Resistor_SMD:R_0402_1005Metric
      Device:C: Capacitor_SMD:C_0402_1005Metric

  - id: r2-labels
    op: snap_labels_to_pins
    reference: R2
    mapping: {"1": VCC, "2": GND}
```

Then, in that directory:

```bash
kbat plan board.kbat.yaml    # what would change, incl. net renames; writes nothing
kbat apply board.kbat.yaml   # apply; prints applied/skipped per transform
kbat apply board.kbat.yaml   # again: applied=0 skipped=3, file untouched
kbat rollback                # restore the snapshot taken before the last apply
```

Commit your schematic to git before applying; kbat's own undo is one
step deep (see "Known limitations").

## Transforms

| `op` | What it does |
|------|--------------|
| `set_property` | Set `Value`, `Footprint`, `Reference` or any custom field on one symbol (`selector: {reference: ...}`) |
| `assign_footprints` | Set the footprint of every symbol whose `lib_id` is in `by_lib_id` (power symbols are skipped) |
| `add_no_connect` | Put no-connect markers on the listed `pins` of `reference`; pins that already have one are left alone |
| `snap_labels_to_pins` | Move each net label in `mapping` (pin → net) onto that pin of `reference`, if within `tolerance_mm` (default 25) |
| `move_label` | Move the `net` label at `from_x, from_y` to `to_x, to_y` (mm) |
| `delete_label_at` | Delete the `net` label at `x, y` (mm) |
| `add_pwr_flag` | Place a `PWR_FLAG` on `net` at `x, y` (mm), snapped to KiCad's 1.27 mm grid |

Transforms run in order, each seeing the result of the ones before it.
Every transform takes an optional `id`. The full schema is
`kbat/schema/v1alpha1.py`; a real 143-line manifest is
`tests/fixtures/validation_gate/cleanup.kbat.yaml`.

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

`docs/ROADMAP.md` covers the plan, the evidence behind it and open
questions; `docs/testing.md` covers the test strategy. IDs such as
`[[0izyKtvZr5EthRrpD7Yqn]]` in docs and docstrings refer to the author's
private design notes and can be ignored.

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

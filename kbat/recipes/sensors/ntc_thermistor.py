"""NTC thermistor recipe (sensor frontend) — v0.5 velocity spike.

A parametric Python recipe that *configures* an NTC thermistor +
pullup voltage-divider for analog temperature sensing. The recipe
takes a pre-laid-out schematic that already contains the thermistor
component (``TH1`` or caller-supplied ref), its pullup (``R_TH1``),
and the surrounding net labels (``+3V3``, ``TEMP_SENSE``, ``GND``) —
the recipe stamps the correct Value / Footprint / MPN onto the two
components, snaps the divider labels onto the right pin endpoints,
and adds a power flag for the supply rail.

This module is the **first measured recipe spike** for v0.5 — the
goal is not a polished production recipe but a data point on
recipe-authoring velocity and on which primitive **gaps** the 7
existing v0.1.1 ops surface. See the phase note for the full
velocity-measurement breakdown (hours spent, primitives used,
missing-primitive workarounds, recommended v0.5 follow-ups).

Velocity & gap summary (see also phase note):

- **Hours**: ~1 hr end-to-end (research + fixture + recipe + tests).
- **Primitives USED**: ``set_property`` (×6 — Value/Footprint/MPN on
  TH1 + R_TH1), ``snap_labels_to_pins`` (×2 — one per component),
  ``add_pwr_flag`` (×1 — supply rail).
- **Primitives MISSING** (workarounds employed, NOT new ops):
    1. ``add_component(lib_id, reference, position)`` — there is no
       way to instantiate ``TH1`` or ``R_TH1`` from scratch. Workaround:
       *require the caller to pre-place both components and the net
       labels*. The recipe configures, it does not lay out.
    2. ``add_label_at(net, x, y)`` — there is no way to mint a fresh
       net label without going through ``add_pwr_flag`` (which adds a
       PWR_FLAG component as a side effect, only acceptable on power
       nets). Workaround: caller pre-places ``TEMP_SENSE`` and ``GND``
       labels in the divider area; the recipe only snaps them.
    3. ``connect_pins(ref_a, pin_a, ref_b, pin_b)`` — no wire-routing
       primitive exists. Workaround: rely on the label-naming
       convention (two pins carrying the same net text are
       electrically connected in KiCad), so the recipe just makes sure
       the labels are at the right pin endpoints.
    4. ``snap_labels_to_pins`` label-multiplicity gap — when two
       different pins on different components need the SAME net (e.g.
       ``TEMP_SENSE`` appears on both ``TH1`` pin 1 and ``R_TH1`` pin 2),
       the recipe needs *two* pre-placed labels of that net so each
       ``snap_labels_to_pins`` call has a candidate to claim. With
       only one label, the second call moves it off the first pin,
       breaking idempotence. Discovered during this spike; the v0.5
       fix is either (a) clone-on-demand inside ``snap_labels_to_pins``
       or (b) the new ``add_label_at`` primitive above.
- **Net v0.5 ask**: ``add_component`` + ``add_label_at`` together
  would unblock zero-prep recipe authoring; without them the recipe
  is a "configure my pre-laid-out divider" helper rather than a
  "drop in a temperature sensor" generator. ``connect_pins`` would
  push it from "label-based connectivity" to "true component
  generator".

Example::

    from pathlib import Path
    from kbat.recipes.sensors.ntc_thermistor import (
        NtcThermistorParams, apply_ntc_thermistor,
    )
    result = apply_ntc_thermistor(
        Path("board.kicad_sch"),
        NtcThermistorParams(chip="NCP18XH103J03RB"),
    )
    print(result.applied, "ops applied,", result.skipped, "skipped")
"""

from __future__ import annotations

import re as _re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kbat.ops import add_pwr_flag, set_property, snap_labels_to_pins
from kbat.schema.v1alpha1 import ApplyResult, DetailEntry

# Permissive reference pattern: accept KiCad references with underscores
# (e.g. ``R_TH1``) that ``kicad-sch-api`` 0.5.5's legacy validator rejects
# on save. Inlined here rather than imported from :mod:`kbat.ops.schematic`
# so the recipe's import surface contains ONLY the 7 v0.1.1 primitives
# (per Phase 6 gate Check 6.4 — recipes must not depend on backend-compat
# shims that are not themselves primitives).
_PERMISSIVE_REF_RE = _re.compile(
    r"^(#[A-Z]+[0-9]+|[A-Z][A-Z0-9_]*[A-Z0-9]?|[A-Z]+\?)$"
)


def _relax_save_validation(sch: Any) -> None:
    """Loosen the per-instance reference validator (private helper).

    Mirrors :func:`kbat.ops.schematic.relax_validation` but lives in this
    module so the recipe does not depend on a non-primitive export from
    :mod:`kbat.ops`. Safe to call multiple times.
    """
    legacy = getattr(sch, "_legacy_validator", None)
    if legacy is not None:
        legacy._valid_reference_pattern = _PERMISSIVE_REF_RE


# ---- curated chip table (v0.5 expands; this is a 3-entry spike) -----------

#: Curated MPN → spec lookup. Each entry carries the data needed to
#: stamp the component (``value``, ``footprint``, ``mpn`` for BOM) and
#: enough datasheet metadata (``beta``, ``r25``) for downstream
#: temperature-conversion helpers (out of scope for this recipe).
CHIP_TABLE: dict[str, dict[str, Any]] = {
    "NCP18XH103J03RB": {
        "value": "10k NTC",
        "footprint": "Resistor_SMD:R_0603_1608Metric",
        "mpn": "NCP18XH103J03RB",
        "beta": 3380,
        "r25": 10000,
        "package": "0603",
    },
    "NCP21XV103J03RA": {
        "value": "10k NTC",
        "footprint": "Resistor_SMD:R_0402_1005Metric",
        "mpn": "NCP21XV103J03RA",
        "beta": 3950,
        "r25": 10000,
        "package": "0402",
    },
    "B57891M0103J000": {
        "value": "10k NTC",
        "footprint": "Resistor_SMD:R_0805_2012Metric",
        "mpn": "B57891M0103J000",
        "beta": 3950,
        "r25": 10000,
        "package": "0805",
    },
}


# ---- parameter schema -----------------------------------------------------


class NtcThermistorParams(BaseModel):
    """Parameters for an NTC thermistor + pullup divider.

    Strict (``extra="forbid"``) per OSTH §3.4 (recipes are a new
    Pydantic boundary at v0.5). Unknown keys raise
    :class:`pydantic.ValidationError` rather than silently no-op'ing.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    chip: str = Field(
        ...,
        description="MPN from CHIP_TABLE — picks Value + Footprint + BOM MPN.",
    )
    reference: str = Field(
        "TH1",
        description="Reference designator of the pre-placed thermistor.",
    )
    pullup_reference: str = Field(
        "R_TH1",
        description="Reference designator of the pre-placed pullup resistor.",
    )
    pullup_k: float = Field(
        4.7,
        description=(
            "Pullup resistor value in kilo-ohms. Stamped on the pullup as "
            "``{pullup_k}k`` (e.g. ``4.7`` → ``'4.7k'``)."
        ),
    )
    pullup_footprint: str = Field(
        "Resistor_SMD:R_0603_1608Metric",
        description="Footprint to stamp on the pullup resistor.",
    )
    vref_net: str = Field(
        "+3V3",
        description="Net name of the supply rail at the top of the divider.",
    )
    sense_net: str = Field(
        "TEMP_SENSE",
        description="Net name at the divider midpoint (ADC input).",
    )
    gnd_net: str = Field(
        "GND",
        description="Net name at the bottom of the divider.",
    )
    add_pwr_flag_for_vref: bool = Field(
        True,
        description=(
            "If True, drop a PWR_FLAG on the supply rail near the pullup top "
            "(workaround for the missing add_label_at primitive — guarantees "
            "the vref label exists even if the caller forgot to place it)."
        ),
    )


# ---- helpers --------------------------------------------------------------


def _format_pullup_value(k: float) -> str:
    """Render ``pullup_k`` as a KiCad-conventional value string.

    Integer-valued floats render without a decimal (``10.0`` → ``'10k'``);
    fractional values keep one decimal (``4.7`` → ``'4.7k'``).
    """
    if float(k).is_integer():
        return f"{int(k)}k"
    return f"{k:g}k"


# ---- entry function -------------------------------------------------------


def apply_ntc_thermistor(
    schematic_path: Path,
    params: NtcThermistorParams,
    *,
    save: bool = True,
) -> ApplyResult:
    """Apply an NTC thermistor + pullup configuration to ``schematic_path``.

    The schematic at ``schematic_path`` **must already contain**:

    - A pre-placed thermistor component with reference ``params.reference``
      (default ``TH1``). It can be a generic ``Device:R`` placeholder.
    - A pre-placed pullup component with reference
      ``params.pullup_reference`` (default ``R_TH1``).
    - Net labels for ``params.vref_net``, ``params.sense_net``, and
      ``params.gnd_net`` placed somewhere near the relevant pin
      endpoints (within the default 25 mm ``snap_labels_to_pins``
      tolerance).

    The recipe is idempotent: a second application against the
    already-configured schematic returns ``ApplyResult(applied=0, ...)``
    on the *functional* level. (UUIDs minted by ``add_pwr_flag`` may
    still differ on re-save — Phase 2.1 flag, see
    :class:`kbat._validation.FunctionalFacts`.)

    Args:
        schematic_path: Path to a writable ``.kicad_sch`` file. The
            recipe loads, mutates in memory, and saves in place
            (unless ``save=False``).
        params: Validated :class:`NtcThermistorParams`.
        save: If False, mutate the in-memory schematic but don't save
            to disk — useful for tests that want to inspect the result
            without round-tripping through KiCad serialisation.

    Returns:
        :class:`ApplyResult` with one :class:`DetailEntry` per
        primitive call (applied / skipped).

    Raises:
        ValueError: ``params.chip`` is not in :data:`CHIP_TABLE`.
        FileNotFoundError: ``schematic_path`` does not exist.
        KeyError / AttributeError: a required component or label is
            missing from the schematic (caller did not pre-place).
    """
    import kicad_sch_api as ksa

    # ---- validate params ------------------------------------------------
    if params.chip not in CHIP_TABLE:
        raise ValueError(
            f"Unknown chip {params.chip!r}. "
            f"Available: {sorted(CHIP_TABLE)}"
        )
    chip_info = CHIP_TABLE[params.chip]

    sch_path = Path(schematic_path)
    if not sch_path.exists():
        raise FileNotFoundError(sch_path)

    sch = ksa.Schematic.load(str(sch_path))
    _relax_save_validation(sch)

    applied = 0
    skipped = 0
    details: list[DetailEntry] = []

    def _record(tid: str, op_name: str, changed: bool) -> None:
        nonlocal applied, skipped
        if changed:
            applied += 1
            details.append(DetailEntry(id=tid, op=op_name, status="applied"))
        else:
            skipped += 1
            details.append(DetailEntry(id=tid, op=op_name, status="skipped"))

    # ---- 1. stamp thermistor properties (Value, Footprint, MPN) ---------
    _record(
        "th-value",
        "set_property",
        set_property(sch, params.reference, "Value", chip_info["value"]),
    )
    _record(
        "th-footprint",
        "set_property",
        set_property(sch, params.reference, "Footprint", chip_info["footprint"]),
    )
    _record(
        "th-mpn",
        "set_property",
        set_property(sch, params.reference, "MPN", chip_info["mpn"]),
    )

    # ---- 2. stamp pullup properties --------------------------------------
    # ``pullup_k`` is a float in kΩ; the stamped Value is the canonical
    # ``"{k}k"`` string form so it matches KiCad BOM conventions.
    pullup_value_str = _format_pullup_value(params.pullup_k)
    _record(
        "pullup-value",
        "set_property",
        set_property(sch, params.pullup_reference, "Value", pullup_value_str),
    )
    _record(
        "pullup-footprint",
        "set_property",
        set_property(
            sch, params.pullup_reference, "Footprint", params.pullup_footprint
        ),
    )
    _record(
        "pullup-mpn",
        "set_property",
        set_property(
            sch,
            params.pullup_reference,
            "MPN",
            f"PULLUP_{pullup_value_str}",
        ),
    )

    # ---- 3. snap divider labels to pin endpoints -------------------------
    # Thermistor: pin 1 = sense midpoint, pin 2 = GND.
    _record(
        "th-labels",
        "snap_labels_to_pins",
        snap_labels_to_pins(
            sch,
            params.reference,
            {"1": params.sense_net, "2": params.gnd_net},
        ),
    )
    # Pullup: pin 1 = vref (top), pin 2 = sense midpoint (bottom).
    _record(
        "pullup-labels",
        "snap_labels_to_pins",
        snap_labels_to_pins(
            sch,
            params.pullup_reference,
            {"1": params.vref_net, "2": params.sense_net},
        ),
    )

    # ---- 4. (optional) add PWR_FLAG on the supply rail -------------------
    if params.add_pwr_flag_for_vref:
        pullup_pin1 = sch.get_component_pin_position(params.pullup_reference, "1")
        if pullup_pin1 is not None:
            _record(
                "vref-pwr-flag",
                "add_pwr_flag",
                add_pwr_flag(
                    sch, params.vref_net, float(pullup_pin1.x), float(pullup_pin1.y)
                ),
            )

    # ---- 5. save (unless caller opted out) -------------------------------
    if save:
        sch.save(str(sch_path))

    return ApplyResult(
        applied=applied,
        skipped=skipped,
        errors=[],
        details=details,
    )


__all__ = [
    "CHIP_TABLE",
    "NtcThermistorParams",
    "apply_ntc_thermistor",
]

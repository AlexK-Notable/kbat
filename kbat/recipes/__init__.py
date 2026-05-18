"""kbat recipes — parametric Python wrappers over the primitive ops.

A *recipe* is a Pydantic-typed entry point (``apply_<recipe_name>``) that
composes one or more of the seven schematic primitives in
:mod:`kbat.ops` into a higher-level circuit-authoring intent (e.g.
"add an NTC thermistor + pullup divider", "instantiate a USB-C
receptacle pinout"). Recipes are the v0.5 surface area, not v0.1.1 —
the modules under this package are spikes that measure recipe-authoring
velocity and surface gaps in the primitive toolbox.

Per Lateral §Experiment 8: recipes MUST NOT add new primitives;
discovered gaps are documented as workarounds on each recipe.
"""

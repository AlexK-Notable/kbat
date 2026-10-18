"""In-memory model of the parts of a schematic that kbat's ops read.

Primitives compile against a :class:`BoardState` instead of mutating a
loaded schematic object. Each primitive both emits backend ops and updates
the state to reflect them, so later transforms in the same manifest see
earlier transforms' effects. A manifest that is already applied compiles
to zero ops against the state it reads back.

Coordinates are millimetres throughout (KiCad file units); the backend
converts to whatever unit it speaks.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Component:
    """One placed symbol: reference, lib_id, properties and pin endpoints."""

    reference: str
    lib_id: str
    properties: dict[str, str] = field(default_factory=dict)
    pins: dict[str, tuple[float, float]] = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0

    @property
    def value(self) -> str:
        return self.properties.get("Value", "")

    @property
    def footprint(self) -> str:
        return self.properties.get("Footprint", "")


@dataclass
class Label:
    """One local net label. ``effects`` is its formatting, normalised to a string."""

    name: str
    x: float
    y: float
    rotation: float = 0.0
    effects: str = ""


@dataclass
class BoardState:
    components: dict[str, Component] = field(default_factory=dict)
    labels: list[Label] = field(default_factory=list)
    no_connects: list[tuple[float, float]] = field(default_factory=list)
    #: Labels moved by compiled ops, as ``(name, to_x, to_y, effects)``,
    #: so the post-write fidelity guard can check their formatting survived.
    moves: list[tuple[str, float, float, str]] = field(default_factory=list)

    def labels_near(self, net: str, x: float, y: float, tolerance: float) -> list[Label]:
        """Labels whose text is ``net`` and whose position is within ``tolerance``."""
        return [
            lab
            for lab in self.labels
            if lab.name == net and abs(lab.x - x) <= tolerance and abs(lab.y - y) <= tolerance
        ]

    def no_connect_near(self, x: float, y: float, tolerance: float) -> bool:
        return any(
            abs(nx - x) <= tolerance and abs(ny - y) <= tolerance for nx, ny in self.no_connects
        )


__all__ = ["BoardState", "Component", "Label"]

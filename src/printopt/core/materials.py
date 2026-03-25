"""Filament material properties database."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class MaterialProfile:
    name: str
    density: float              # g/cm3
    specific_heat: float        # J/(g*K)
    thermal_conductivity: float # W/(m*K)
    glass_transition: float     # C
    cte: float = 0.0            # coefficient of thermal expansion, m/(m*K)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path) -> MaterialProfile:
        return cls(**json.loads(path.read_text()))


_BUILTINS: dict[str, MaterialProfile] = {
    "pla": MaterialProfile("pla", 1.24, 1.8, 0.13, 60, 68e-6),
    "petg": MaterialProfile("petg", 1.27, 1.2, 0.20, 78, 60e-6),
    "abs": MaterialProfile("abs", 1.04, 1.4, 0.17, 105, 90e-6),
    "asa": MaterialProfile("asa", 1.07, 1.3, 0.18, 100, 95e-6),
    "tpu": MaterialProfile("tpu", 1.21, 1.5, 0.19, 55, 150e-6),
}


def get_profile(name: str) -> MaterialProfile:
    key = name.lower()
    if key in _BUILTINS:
        return _BUILTINS[key]
    raise KeyError(f"Unknown material profile: {name}")


def list_profiles() -> list[str]:
    return list(_BUILTINS.keys())

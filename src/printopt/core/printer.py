"""Printer configuration model with auto-discovery from Moonraker."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from printopt.core.moonraker import MoonrakerClient


@dataclass
class PrinterConfig:
    host: str = ""
    kinematics: str = "corexy"
    bed_x: float = 0
    bed_y: float = 0
    bed_z: float = 0
    nozzle_diameter: float = 0.4
    filament_diameter: float = 1.75
    max_velocity: float = 300
    has_accelerometer: bool = False
    shaper_x: tuple[str, float] = ("", 0.0)
    shaper_y: tuple[str, float] = ("", 0.0)

    @classmethod
    def from_moonraker_data(cls, server_info: dict, printer_config: dict) -> PrinterConfig:
        cfg = printer_config.get("config", {})
        bed_x = float(cfg.get("stepper_x", {}).get("position_max", 0))
        bed_y = float(cfg.get("stepper_y", {}).get("position_max", 0))
        bed_z = float(cfg.get("stepper_z", {}).get("position_max", 0))
        ext = cfg.get("extruder", {})
        nozzle = float(ext.get("nozzle_diameter", 0.4))
        filament = float(ext.get("filament_diameter", 1.75))
        printer = cfg.get("printer", {})
        kinematics = printer.get("kinematics", "corexy")
        max_vel = float(printer.get("max_velocity", 300))
        has_accel = "adxl345" in cfg
        shaper = cfg.get("input_shaper", {})
        shaper_x = (shaper.get("shaper_type_x", ""), float(shaper.get("shaper_freq_x", 0)))
        shaper_y = (shaper.get("shaper_type_y", ""), float(shaper.get("shaper_freq_y", 0)))
        return cls(
            kinematics=kinematics, bed_x=bed_x, bed_y=bed_y, bed_z=bed_z,
            nozzle_diameter=nozzle, filament_diameter=filament,
            max_velocity=max_vel, has_accelerometer=has_accel,
            shaper_x=shaper_x, shaper_y=shaper_y,
        )

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path) -> PrinterConfig:
        data = json.loads(path.read_text())
        data["shaper_x"] = tuple(data["shaper_x"])
        data["shaper_y"] = tuple(data["shaper_y"])
        return cls(**data)


async def discover_printer(client: MoonrakerClient) -> PrinterConfig:
    server_info = await client.query("server.info")
    printer_config = await client.query(
        "printer.objects.query", {"objects": {"configfile": ["config"]}}
    )
    cfg_data = printer_config.get("status", {}).get("configfile", printer_config)
    config = PrinterConfig.from_moonraker_data(server_info, cfg_data)
    config.host = client.host
    return config

"""2D thermal grid simulation for print bed heat tracking."""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass


@dataclass
class ThermalConfig:
    """Configuration for the thermal simulation."""
    bed_x: float = 245.0  # mm
    bed_y: float = 245.0  # mm
    resolution: float = 1.0  # mm per cell
    ambient_temp: float = 35.0  # C (enclosed chamber)
    bed_temp: float = 70.0  # C
    update_interval: float = 0.5  # seconds
    # Material properties (PETG defaults)
    thermal_conductivity: float = 0.20  # W/(m*K)
    specific_heat: float = 1.2  # J/(g*K)
    density: float = 1.27  # g/cm3
    glass_transition: float = 78.0  # C
    # Cooling coefficients
    convection_base: float = 10.0  # W/(m2*K) base convection
    fan_convection_factor: float = 30.0  # additional W/(m2*K) at 100% fan


class ThermalGrid:
    """2D thermal simulation grid.

    Models heat accumulation and dissipation across the print bed.
    Each cell tracks temperature as heat is deposited by the nozzle
    and lost through conduction, convection, and bed contact.
    """

    def __init__(self, config: ThermalConfig | None = None) -> None:
        self.config = config or ThermalConfig()
        c = self.config
        self.nx = int(c.bed_x / c.resolution)
        self.ny = int(c.bed_y / c.resolution)
        # Temperature grid initialized to ambient
        self.grid = np.full((self.ny, self.nx), c.ambient_temp, dtype=np.float64)
        self.fan_speed = 0.0  # 0-1 fraction
        self.nozzle_temp = 248.0  # C

    def reset(self) -> None:
        """Reset grid to ambient temperature."""
        self.grid[:] = self.config.ambient_temp

    def deposit_heat(self, x: float, y: float, flow_rate: float, dt: float) -> None:
        """Deposit heat at a position from nozzle extrusion.

        Args:
            x: X position in mm
            y: Y position in mm
            flow_rate: Volumetric flow rate in mm3/s
            dt: Time duration of extrusion at this position in seconds
        """
        c = self.config
        ix = int(x / c.resolution)
        iy = int(y / c.resolution)
        if 0 <= ix < self.nx and 0 <= iy < self.ny:
            # Q = flow * (T_nozzle - T_glass) * specific_heat * density * dt
            # Convert flow_rate from mm3/s to cm3/s for density
            flow_cm3 = flow_rate / 1000.0
            q = flow_cm3 * (self.nozzle_temp - c.glass_transition) * c.specific_heat * c.density * dt
            # Convert energy to temperature rise in the cell
            # Cell volume = resolution^2 * layer_height (assume 0.2mm)
            cell_volume_cm3 = (c.resolution * c.resolution * 0.2) / 1000.0
            cell_mass = cell_volume_cm3 * c.density
            if cell_mass > 0:
                self.grid[iy, ix] += q / (cell_mass * c.specific_heat)

    def step(self, dt: float) -> None:
        """Advance the simulation by dt seconds.

        Applies:
        - Conduction to neighboring cells
        - Convection to air (fan-dependent)
        - Bed conduction (cells cool toward bed temp)
        """
        c = self.config
        old = self.grid.copy()

        # Thermal diffusivity: alpha = k / (rho * cp)
        # k in W/(m*K), rho in kg/m3 (density * 1000), cp in J/(kg*K) (specific_heat * 1000)
        alpha = c.thermal_conductivity / (c.density * 1000.0 * c.specific_heat * 1000.0)
        # Convert to mm2/s: alpha_mm = alpha * 1e6
        alpha_mm = alpha * 1e6

        # Conduction: discrete Laplacian
        dx2 = c.resolution * c.resolution
        laplacian = np.zeros_like(old)
        laplacian[1:-1, :] += old[:-2, :] + old[2:, :] - 2 * old[1:-1, :]
        laplacian[:, 1:-1] += old[:, :-2] + old[:, 2:] - 2 * old[:, 1:-1]
        laplacian /= dx2

        self.grid += alpha_mm * laplacian * dt

        # Convection: Newton's cooling law
        h = c.convection_base + c.fan_convection_factor * self.fan_speed
        # h in W/(m2*K), need to convert cell area to m2
        cell_area_m2 = (c.resolution * c.resolution) * 1e-6
        cell_mass_kg = (c.resolution * c.resolution * 0.2) * 1e-9 * c.density * 1000.0
        cp_j_kg_k = c.specific_heat * 1000.0
        if cell_mass_kg > 0 and cp_j_kg_k > 0:
            cooling_rate = h * cell_area_m2 / (cell_mass_kg * cp_j_kg_k)
            self.grid -= cooling_rate * (self.grid - c.ambient_temp) * dt

        # Bed conduction: cells slowly approach bed temp
        bed_rate = 0.01  # slow equilibration
        self.grid += bed_rate * (c.bed_temp - self.grid) * dt

        # Clamp to physical range
        np.clip(self.grid, c.ambient_temp - 5, self.nozzle_temp, out=self.grid)

    def get_hotspots(self, threshold: float | None = None) -> list[tuple[int, int, float]]:
        """Find cells above the glass transition temperature.

        Returns list of (x_mm, y_mm, temperature) tuples.
        """
        if threshold is None:
            threshold = self.config.glass_transition
        ys, xs = np.where(self.grid > threshold)
        res = self.config.resolution
        return [
            (int(x * res), int(y * res), float(self.grid[y, x]))
            for x, y in zip(xs, ys)
        ]

    def get_thermal_gradient(self) -> np.ndarray:
        """Compute the magnitude of thermal gradient at each cell.

        High gradients indicate warping risk.
        """
        gy, gx = np.gradient(self.grid, self.config.resolution)
        return np.sqrt(gx**2 + gy**2)

    def get_max_gradient(self) -> float:
        """Return the maximum thermal gradient magnitude."""
        grad = self.get_thermal_gradient()
        return float(np.max(grad))

    def get_heatmap(self) -> np.ndarray:
        """Return the current temperature grid for visualization."""
        return self.grid.copy()

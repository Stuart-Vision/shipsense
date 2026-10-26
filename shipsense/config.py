"""Sensors the pipeline understands, with their physical limits.

A reading outside its limits is a sensor or logger fault, not a real value, so it
is dropped and reported as a data-quality issue instead of being averaged in.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    name: str
    unit: str
    low: float
    high: float


METRICS: dict[str, Metric] = {
    m.name: m
    for m in [
        Metric("rpm", "rpm", 0, 2500),
        Metric("exhaust_temp_c", "°C", 0, 650),
        Metric("coolant_temp_c", "°C", 0, 120),
        Metric("lube_oil_pressure_bar", "bar", 0, 10),
        Metric("fuel_rate_lph", "L/h", 0, 2000),
        Metric("vibration_mm_s", "mm/s", 0, 50),
    ]
}

# Some older engine loggers report exhaust temperature in Fahrenheit.
FAHRENHEIT_ALIASES = {"exhaust_temp_f": "exhaust_temp_c"}

# Gaps up to this many minutes are filled by linear interpolation (and flagged);
# longer gaps are left empty and reported, because inventing data would hide outages.
MAX_INTERPOLATED_GAP_MIN = 2

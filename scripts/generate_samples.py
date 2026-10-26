"""Generates realistic sample files with known faults, used by the tests and the demo.

  python scripts/generate_samples.py [output_dir]

MV-ORION, 20 Oct 2026, 08:00-14:00 ship time (UTC+8):
  ME-1 (main engine) CSV logger, every 10 s, exhaust temperature in Fahrenheit:
    - exhaust above 450 °C for 8 minutes from 10:00          -> exhaust_temp_high alert
    - logger offline 11:30-11:35 (5 minutes)                 -> gap issue, not filled
    - logger offline 12:10 (1 minute)                        -> interpolated
    - 20 rows resent after a reconnect                        -> duplicate issue
    - 3 coolant readings of 999 °C (sensor fault)             -> out_of_range issues
    - 4 empty fuel readings                                    -> missing_value issues
  GEN-1 (generator) IoT gateway JSONL, every 30 s:
    - lube oil pressure 1.6 bar for 4 minutes at 1,800 rpm    -> lube_oil_pressure_low alert
    - vibration jumps from 2.5 to 6.0 mm/s for 6 minutes      -> vibration_anomaly alert
    - 3 corrupt lines, and an unknown 'humidity_pct' sensor   -> issues
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

SHIP_TIME = timezone(timedelta(hours=8))
START = datetime(2026, 10, 20, 8, 0, tzinfo=SHIP_TIME)
HOURS = 6


def at(hh: int, mm: int) -> datetime:
    return START.replace(hour=hh, minute=mm)


def main_engine_csv(path: Path, rng: np.random.Generator) -> None:
    rows = []
    t = START
    while t < START + timedelta(hours=HOURS):
        if at(11, 30) <= t < at(11, 35) or at(12, 10) <= t < at(12, 11):
            t += timedelta(seconds=10)
            continue
        exhaust_c = 380 + rng.normal(0, 4)
        if at(10, 0) <= t < at(10, 8):
            exhaust_c = 470 + rng.normal(0, 3)
        rows.append({
            "timestamp": t.isoformat(),
            "vessel_id": "MV-ORION",
            "equipment_id": "ME-1",
            "rpm": round(720 + rng.normal(0, 5), 1),
            "exhaust_temp_f": round(exhaust_c * 9 / 5 + 32, 1),
            "coolant_temp_c": round(82 + rng.normal(0, 0.8), 2),
            "lube_oil_pressure_bar": round(4.8 + rng.normal(0, 0.05), 3),
            "fuel_rate_lph": round(310 + rng.normal(0, 6), 1),
        })
        t += timedelta(seconds=10)

    for i in (500, 900, 1300):
        rows[i]["coolant_temp_c"] = 999
    for i in (200, 201, 202, 203):
        rows[i]["fuel_rate_lph"] = ""
    rows += [dict(r) for r in rows[1500:1520]]  # resent block

    header = list(rows[0])
    lines = [",".join(header)] + [",".join(str(r[h]) for h in header) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generator_jsonl(path: Path, rng: np.random.Generator) -> None:
    lines = []
    t = START
    while t < START + timedelta(hours=HOURS):
        ms = int(t.timestamp() * 1000)
        oil = 4.4 + rng.normal(0, 0.05)
        if at(9, 15) <= t < at(9, 19):
            oil = 1.6 + rng.normal(0, 0.03)
        vibration = 2.5 + rng.normal(0, 0.15)
        if at(13, 0) <= t < at(13, 6):
            vibration = 6.0 + rng.normal(0, 0.15)
        for sensor, value in [("rpm", 1800 + rng.normal(0, 4)), ("coolant_temp_c", 78 + rng.normal(0, 0.6)),
                              ("lube_oil_pressure_bar", oil), ("fuel_rate_lph", 95 + rng.normal(0, 2)),
                              ("vibration_mm_s", vibration), ("humidity_pct", 61.0)]:
            lines.append(json.dumps({"ts": ms, "vessel": "MV-ORION", "equipment": "GEN-1",
                                     "sensor": sensor, "value": round(float(value), 3)}))
        t += timedelta(seconds=30)
    for i in (100, 2000, 3000):
        lines.insert(i, '{"ts": 17609, "vessel": "MV-OR')  # truncated by a dropped connection
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)  # fixed seed: the same files every time
    csv_path = out_dir / "mv-orion_ME-1_2026-10-20.csv"
    jsonl_path = out_dir / "mv-orion_GEN-1_2026-10-20.jsonl"
    main_engine_csv(csv_path, rng)
    generator_jsonl(jsonl_path, rng)
    return [csv_path, jsonl_path]


if __name__ == "__main__":
    for p in generate(Path(sys.argv[1] if len(sys.argv) > 1 else "data/samples")):
        print(p)

"""Read raw sensor files into one long table: vessel_id, equipment_id, ts, metric, value.

Two source formats arrive from ships:
  * CSV from engine data loggers: one row per timestamp, one column per sensor (wide).
  * JSON Lines from IoT gateways: one reading per line (long).
"""

import json
from pathlib import Path

import pandas as pd

LONG_COLUMNS = ["vessel_id", "equipment_id", "ts", "metric", "value"]
ISSUE_COLUMNS = ["vessel_id", "equipment_id", "metric", "ts", "issue", "detail"]


def empty_issues() -> pd.DataFrame:
    return pd.DataFrame(columns=ISSUE_COLUMNS)


def read_file(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (readings, issues). Timestamps are converted to UTC."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return read_logger_csv(path)
    if suffix in (".jsonl", ".ndjson"):
        return read_gateway_jsonl(path)
    raise ValueError(f"Unsupported file type: {path.name}")


def read_logger_csv(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    wide = pd.read_csv(path, dtype=str)
    missing = {"timestamp", "vessel_id", "equipment_id"} - set(wide.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
    sensor_columns = [c for c in wide.columns if c not in ("timestamp", "vessel_id", "equipment_id")]
    long = wide.melt(id_vars=["timestamp", "vessel_id", "equipment_id"], value_vars=sensor_columns,
                     var_name="metric", value_name="value")
    long["ts"] = pd.to_datetime(long.pop("timestamp"), utc=True, errors="coerce", format="ISO8601")
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    return _split_unparseable(long[LONG_COLUMNS])


def read_gateway_jsonl(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, issues = [], []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                rows.append({"vessel_id": r["vessel"], "equipment_id": r["equipment"],
                             "ts": r["ts"], "metric": r["sensor"], "value": r["value"]})
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                issues.append({"vessel_id": None, "equipment_id": None, "metric": None, "ts": None,
                               "issue": "unparseable_line", "detail": f"line {line_no}: {type(e).__name__}"})
    long = pd.DataFrame(rows, columns=LONG_COLUMNS)
    long["ts"] = pd.to_datetime(long["ts"], unit="ms", utc=True, errors="coerce")
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    readings, more = _split_unparseable(long)
    return readings, pd.concat([pd.DataFrame(issues, columns=ISSUE_COLUMNS), more], ignore_index=True)


def _split_unparseable(long: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rows without a usable timestamp or numeric value become issues."""
    bad_ts = long["ts"].isna()
    bad_value = ~bad_ts & long["value"].isna()
    issues = pd.concat([
        long[bad_ts].assign(issue="bad_timestamp", detail="timestamp could not be parsed"),
        long[bad_value].assign(issue="missing_value", detail="value is empty or not a number"),
    ])[ISSUE_COLUMNS]
    return long[~bad_ts & ~bad_value].reset_index(drop=True), issues.reset_index(drop=True)

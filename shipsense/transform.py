"""Clean raw readings and aggregate them to one row per sensor per minute.

Steps, in order:
  1. Convert units (°F to °C) and drop sensors we don't know.
  2. Remove duplicate readings (loggers resend after a reconnect).
  3. Drop physically impossible values (sensor faults).
  4. Resample each sensor to 1-minute avg / min / max / sample count.
  5. Fill short gaps by interpolation (flagged); report long gaps.
Every row that is dropped or changed is reported as a data-quality issue.
"""

import pandas as pd

from .config import FAHRENHEIT_ALIASES, MAX_INTERPOLATED_GAP_MIN, METRICS
from .extract import ISSUE_COLUMNS

SERIES = ["vessel_id", "equipment_id", "metric"]
MINUTE_COLUMNS = [*SERIES, "minute", "avg_value", "min_value", "max_value", "samples", "interpolated"]


def clean(readings: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Steps 1-3. Returns (clean readings, issues)."""
    df = readings.copy()
    issues = []

    # 1. Units
    is_f = df["metric"].isin(FAHRENHEIT_ALIASES)
    df.loc[is_f, "value"] = (df.loc[is_f, "value"] - 32) * 5 / 9
    df["metric"] = df["metric"].replace(FAHRENHEIT_ALIASES)

    unknown = ~df["metric"].isin(METRICS)
    if unknown.any():
        for metric, n in df.loc[unknown, "metric"].value_counts().items():
            issues.append(_issue(df[unknown & (df["metric"] == metric)].iloc[0], "unknown_metric", f"{n} readings for '{metric}' ignored"))
        df = df[~unknown]

    # 2. Duplicates: same sensor and timestamp. Keep the first; report one issue per sensor.
    dup = df.duplicated(subset=[*SERIES, "ts"], keep="first")
    for keys, group in df[dup].groupby(SERIES):
        issues.append(_issue(group.iloc[0], "duplicate", f"{len(group)} duplicate readings removed"))
    df = df[~dup]

    # 3. Physical limits
    low = df["metric"].map(lambda m: METRICS[m].low)
    high = df["metric"].map(lambda m: METRICS[m].high)
    out = (df["value"] < low) | (df["value"] > high)
    for _, row in df[out].iterrows():
        m = METRICS[row["metric"]]
        issues.append(_issue(row, "out_of_range", f"{row['value']:g} outside {m.low:g}-{m.high:g} {m.unit}"))
    df = df[~out]

    return df.sort_values([*SERIES, "ts"]).reset_index(drop=True), pd.DataFrame(issues, columns=ISSUE_COLUMNS)


def to_minutes(readings: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Steps 4-5. Returns (one row per sensor-minute, gap issues)."""
    frames, issues = [], []
    for (vessel, equipment, metric), group in readings.groupby(SERIES):
        per_minute = (group.set_index("ts")["value"]
                      .resample("1min")
                      .agg(["mean", "min", "max", "count"])
                      .rename(columns={"mean": "avg_value", "min": "min_value", "max": "max_value", "count": "samples"}))
        empty = per_minute["samples"] == 0

        # Label each run of consecutive empty minutes and measure its length.
        run_id = (empty != empty.shift()).cumsum()
        run_len = empty.groupby(run_id).transform("sum")
        short_gap = empty & (run_len <= MAX_INTERPOLATED_GAP_MIN)
        long_gap = empty & ~short_gap

        per_minute["avg_value"] = per_minute["avg_value"].interpolate(method="linear", limit_area="inside")
        per_minute["interpolated"] = short_gap
        for col in ("min_value", "max_value"):
            per_minute.loc[short_gap, col] = per_minute.loc[short_gap, "avg_value"]

        for _, gap in per_minute[long_gap].groupby(run_id[long_gap]):
            issues.append({"vessel_id": vessel, "equipment_id": equipment, "metric": metric,
                           "ts": gap.index[0], "issue": "gap",
                           "detail": f"{len(gap)} minutes without data, not filled"})

        kept = per_minute[~long_gap].reset_index().rename(columns={"ts": "minute"})
        kept[SERIES] = vessel, equipment, metric
        frames.append(kept)

    minutes = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=MINUTE_COLUMNS)
    minutes["samples"] = minutes["samples"].astype(int)
    return minutes[MINUTE_COLUMNS], pd.DataFrame(issues, columns=ISSUE_COLUMNS)


def _issue(row: pd.Series, issue: str, detail: str) -> dict:
    return {"vessel_id": row["vessel_id"], "equipment_id": row["equipment_id"], "metric": row["metric"],
            "ts": row["ts"], "issue": issue, "detail": detail}

"""Equipment-health alerts from the 1-minute data.

Two kinds of rule:
  * Thresholds that must hold for several consecutive minutes, so one noisy
    minute doesn't page the crew (e.g. exhaust above 450 °C for 3+ minutes).
  * A statistical rule for vibration: a rolling z-score against the previous
    hour, which catches a machine drifting from its own normal behaviour.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

ALERT_COLUMNS = ["vessel_id", "equipment_id", "metric", "rule", "started_at", "ended_at", "peak_value"]


@dataclass(frozen=True)
class ThresholdRule:
    name: str
    metric: str
    above: float | None = None
    below: float | None = None
    min_minutes: int = 3
    only_when_rpm_above: float | None = None  # e.g. low oil pressure only matters while running


RULES = [
    ThresholdRule("exhaust_temp_high", "exhaust_temp_c", above=450, min_minutes=3),
    ThresholdRule("coolant_temp_high", "coolant_temp_c", above=95, min_minutes=3),
    ThresholdRule("lube_oil_pressure_low", "lube_oil_pressure_bar", below=2.0, min_minutes=2, only_when_rpm_above=300),
]

VIBRATION_Z = 4.0
VIBRATION_BASELINE_MIN = 60


def detect(minutes: pd.DataFrame) -> pd.DataFrame:
    alerts = []
    for (vessel, equipment), group in minutes.groupby(["vessel_id", "equipment_id"]):
        wide = group.pivot(index="minute", columns="metric", values="avg_value").sort_index()
        wide = wide.asfreq("1min")  # missing minutes stay NaN and break runs
        for rule in RULES:
            if rule.metric in wide:
                alerts += _threshold(rule, wide, vessel, equipment)
        if "vibration_mm_s" in wide:
            alerts += _vibration(wide["vibration_mm_s"], vessel, equipment)
    return pd.DataFrame(alerts, columns=ALERT_COLUMNS)


def _threshold(rule: ThresholdRule, wide: pd.DataFrame, vessel: str, equipment: str) -> list[dict]:
    values = wide[rule.metric]
    breach = pd.Series(False, index=wide.index)
    if rule.above is not None:
        breach |= values > rule.above
    if rule.below is not None:
        breach |= values < rule.below
    if rule.only_when_rpm_above is not None:
        rpm = wide["rpm"] if "rpm" in wide else pd.Series(np.nan, index=wide.index)
        breach &= rpm > rule.only_when_rpm_above
    peak = max if rule.above is not None else min
    return [_alert(vessel, equipment, rule.metric, rule.name, run, peak(run.tolist()))
            for run in _runs(values, breach, rule.min_minutes)]


def _vibration(values: pd.Series, vessel: str, equipment: str) -> list[dict]:
    # Baseline excludes the current minute (shift) so a spike can't hide itself.
    baseline = values.shift(1).rolling(VIBRATION_BASELINE_MIN, min_periods=30)
    z = (values - baseline.mean()) / baseline.std()
    return [_alert(vessel, equipment, "vibration_mm_s", "vibration_anomaly", run, max(run.tolist()))
            for run in _runs(values, z > VIBRATION_Z, 3)]


def _runs(values: pd.Series, mask: pd.Series, min_len: int) -> list[pd.Series]:
    """Consecutive stretches where mask is true, at least min_len long."""
    mask = mask.fillna(False).astype(bool)
    run_id = (mask != mask.shift()).cumsum()
    return [values[idx] for _, idx in mask[mask].groupby(run_id[mask]).groups.items() if len(idx) >= min_len]


def _alert(vessel: str, equipment: str, metric: str, rule: str, run: pd.Series, peak: float) -> dict:
    return {"vessel_id": vessel, "equipment_id": equipment, "metric": metric, "rule": rule,
            "started_at": run.index[0], "ended_at": run.index[-1] + pd.Timedelta(minutes=1),
            "peak_value": round(float(peak), 2)}

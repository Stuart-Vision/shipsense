import numpy as np
import pandas as pd

from shipsense.anomalies import detect


def minutes(series: dict[str, list[float]], equipment="ME-1") -> pd.DataFrame:
    rows = []
    for metric, values in series.items():
        for i, v in enumerate(values):
            rows.append({"vessel_id": "V1", "equipment_id": equipment, "metric": metric,
                         "minute": pd.Timestamp("2026-10-20T00:00:00Z") + pd.Timedelta(minutes=i), "avg_value": v})
    return pd.DataFrame(rows)


def test_threshold_must_hold_for_three_minutes():
    two = detect(minutes({"exhaust_temp_c": [400, 460, 470, 400, 400]}))
    three = detect(minutes({"exhaust_temp_c": [400, 460, 470, 480, 400]}))
    assert two.empty
    assert list(three["rule"]) == ["exhaust_temp_high"]
    assert three["peak_value"].iloc[0] == 480
    assert three["ended_at"].iloc[0] - three["started_at"].iloc[0] == pd.Timedelta(minutes=3)


def test_low_oil_pressure_only_counts_while_the_engine_runs():
    stopped = detect(minutes({"rpm": [0, 0, 0], "lube_oil_pressure_bar": [0.5, 0.5, 0.5]}))
    running = detect(minutes({"rpm": [1800, 1800, 1800], "lube_oil_pressure_bar": [1.5, 1.4, 1.5]}))
    assert stopped.empty
    assert list(running["rule"]) == ["lube_oil_pressure_low"]
    assert running["peak_value"].iloc[0] == 1.4  # the lowest pressure seen


def test_vibration_jump_against_its_own_baseline():
    rng = np.random.default_rng(1)
    normal = list(2.5 + rng.normal(0, 0.1, 90))
    alerts = detect(minutes({"vibration_mm_s": normal + [6.0] * 5 + [2.5] * 10}))
    assert list(alerts["rule"]) == ["vibration_anomaly"]
    assert alerts["started_at"].iloc[0] == pd.Timestamp("2026-10-20T01:30:00Z")


def test_a_data_gap_breaks_a_run():
    df = minutes({"exhaust_temp_c": [460, 460, 460, 460]})
    df = df[df["minute"].dt.minute != 2]  # minute 2 missing: two runs of 2 and 1
    assert detect(df).empty

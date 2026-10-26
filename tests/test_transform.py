"""Unit tests for extraction and cleaning, using small hand-made inputs."""

import pandas as pd
import pytest

from shipsense.extract import read_gateway_jsonl, read_logger_csv
from shipsense.transform import clean, to_minutes


def readings(rows):
    df = pd.DataFrame(rows, columns=["vessel_id", "equipment_id", "ts", "metric", "value"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def test_csv_is_reshaped_to_long_and_times_become_utc(tmp_path):
    f = tmp_path / "logger.csv"
    f.write_text("timestamp,vessel_id,equipment_id,rpm,coolant_temp_c\n"
                 "2026-10-20T08:00:00+08:00,V1,ME-1,700,80\n", encoding="utf-8")
    long, issues = read_logger_csv(f)
    assert issues.empty
    assert len(long) == 2
    assert set(long["metric"]) == {"rpm", "coolant_temp_c"}
    assert long["ts"].iloc[0] == pd.Timestamp("2026-10-20T00:00:00Z")


def test_csv_empty_and_bad_values_become_issues(tmp_path):
    f = tmp_path / "logger.csv"
    f.write_text("timestamp,vessel_id,equipment_id,rpm\n"
                 "2026-10-20T08:00:00+08:00,V1,ME-1,\n"
                 "not-a-time,V1,ME-1,700\n"
                 "2026-10-20T08:00:10+08:00,V1,ME-1,abc\n", encoding="utf-8")
    long, issues = read_logger_csv(f)
    assert long.empty
    assert sorted(issues["issue"]) == ["bad_timestamp", "missing_value", "missing_value"]


def test_jsonl_skips_corrupt_lines(tmp_path):
    f = tmp_path / "gw.jsonl"
    f.write_text('{"ts": 1792454400000, "vessel": "V1", "equipment": "GEN-1", "sensor": "rpm", "value": 1800}\n'
                 '{"ts": 17924544\n'
                 '{"ts": 1792454430000, "vessel": "V1", "equipment": "GEN-1", "value": 1801}\n', encoding="utf-8")
    long, issues = read_gateway_jsonl(f)
    assert len(long) == 1
    assert list(issues["issue"]) == ["unparseable_line", "unparseable_line"]
    assert "line 2" in issues["detail"].iloc[0]


def test_fahrenheit_is_converted_to_celsius():
    cleaned, _ = clean(readings([("V1", "ME-1", "2026-10-20T00:00:00Z", "exhaust_temp_f", 752.0)]))
    assert cleaned["metric"].iloc[0] == "exhaust_temp_c"
    assert cleaned["value"].iloc[0] == pytest.approx(400.0)


def test_duplicates_unknown_sensors_and_impossible_values_are_removed_and_reported():
    cleaned, issues = clean(readings([
        ("V1", "ME-1", "2026-10-20T00:00:00Z", "rpm", 700),
        ("V1", "ME-1", "2026-10-20T00:00:00Z", "rpm", 700),        # duplicate
        ("V1", "ME-1", "2026-10-20T00:00:10Z", "rpm", 9999),       # above 2,500 rpm
        ("V1", "ME-1", "2026-10-20T00:00:10Z", "coolant_temp_c", -5),  # below 0 °C
        ("V1", "ME-1", "2026-10-20T00:00:10Z", "humidity_pct", 60),    # not a known sensor
    ]))
    assert len(cleaned) == 1
    assert sorted(issues["issue"]) == ["duplicate", "out_of_range", "out_of_range", "unknown_metric"]


def test_minutes_hold_avg_min_max_and_sample_count():
    minutes, gaps = to_minutes(readings([
        ("V1", "ME-1", "2026-10-20T00:00:00Z", "rpm", 700),
        ("V1", "ME-1", "2026-10-20T00:00:20Z", "rpm", 710),
        ("V1", "ME-1", "2026-10-20T00:00:40Z", "rpm", 720),
        ("V1", "ME-1", "2026-10-20T00:01:00Z", "rpm", 730),
    ]))
    first = minutes.iloc[0]
    assert (first["avg_value"], first["min_value"], first["max_value"], first["samples"]) == (710, 700, 720, 3)
    assert len(minutes) == 2 and gaps.empty


def test_short_gaps_are_interpolated_and_long_gaps_are_reported():
    rows = [("V1", "ME-1", f"2026-10-20T00:{m:02d}:00Z", "rpm", 700 + m) for m in (0, 1, 3, 9)]
    minutes, gaps = to_minutes(readings(rows))
    by_minute = minutes.set_index(minutes["minute"].dt.minute)

    assert by_minute.loc[2, "interpolated"]                   # 1-minute gap: filled
    assert by_minute.loc[2, "avg_value"] == pytest.approx(702)  # halfway between 701 and 703
    assert by_minute.loc[2, "samples"] == 0
    assert not set(range(4, 9)) & set(by_minute.index)        # 5-minute gap: left empty
    assert list(gaps["detail"]) == ["5 minutes without data, not filled"]

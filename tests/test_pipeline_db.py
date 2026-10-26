"""End-to-end tests against a real PostgreSQL: the sample files through the whole pipeline."""

import shutil

from fastapi.testclient import TestClient

from shipsense import api
from shipsense.pipeline import process_file


def scalar(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def test_main_engine_csv_end_to_end(conn, sample_files):
    result = process_file(sample_files["ME-1"], conn)

    assert result["status"] == "succeeded"
    assert result["raw_rows"] == 10720       # including the 4 empty readings
    assert result["clean_rows"] == 10613     # minus 4 empty, 100 duplicates and 3 impossible values
    assert result["minutes_loaded"] == 1775    # 5 sensors x (360 - 5 minute gap)
    assert scalar(conn, "SELECT count(*) FROM readings_1min WHERE interpolated") == 5
    issues = dict(conn.execute("SELECT issue, count(*) FROM quality_issues GROUP BY issue").fetchall())
    assert issues == {"duplicate": 5, "gap": 5, "missing_value": 4, "out_of_range": 3}
    rule, peak = conn.execute("SELECT rule, peak_value FROM alerts").fetchone()
    assert rule == "exhaust_temp_high" and 470 < peak < 480   # converted from Fahrenheit


def test_generator_jsonl_end_to_end(conn, sample_files):
    result = process_file(sample_files["GEN-1"], conn)

    assert result["status"] == "succeeded"
    assert result["minutes_loaded"] == 1800
    rules = [r[0] for r in conn.execute("SELECT rule FROM alerts ORDER BY started_at").fetchall()]
    assert rules == ["lube_oil_pressure_low", "vibration_anomaly"]


def test_the_same_file_is_never_loaded_twice(conn, sample_files, tmp_path):
    first = process_file(sample_files["ME-1"], conn)
    renamed = tmp_path / "resent-with-another-name.csv"
    shutil.copy(sample_files["ME-1"], renamed)
    second = process_file(renamed, conn)

    assert second == {"run_id": first["run_id"], "source": renamed.name, "status": "skipped", "reason": "file already loaded"}
    assert scalar(conn, "SELECT count(*) FROM readings_1min") == 1775
    assert scalar(conn, "SELECT count(*) FROM pipeline_runs") == 1


def test_overlapping_files_update_minutes_instead_of_duplicating(conn, tmp_path):
    header = "timestamp,vessel_id,equipment_id,rpm\n"
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text(header + "2026-10-20T00:00:00Z,V1,ME-1,700\n2026-10-20T00:01:00Z,V1,ME-1,700\n", encoding="utf-8")
    b.write_text(header + "2026-10-20T00:01:00Z,V1,ME-1,750\n2026-10-20T00:02:00Z,V1,ME-1,750\n", encoding="utf-8")

    process_file(a, conn)
    process_file(b, conn)

    rows = conn.execute("SELECT extract(minute FROM minute AT TIME ZONE 'UTC')::int, avg_value FROM readings_1min ORDER BY minute").fetchall()
    assert rows == [(0, 700), (1, 750), (2, 750)]


def test_a_bad_file_fails_cleanly_and_can_be_retried(conn, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("time,vessel\nx,y\n", encoding="utf-8")   # wrong columns

    result = process_file(bad, conn)

    assert result["status"] == "failed" and "missing columns" in result["error"]
    assert conn.execute("SELECT status FROM pipeline_runs").fetchone()[0] == "failed"
    assert scalar(conn, "SELECT count(*) FROM readings_1min") == 0   # nothing half-loaded
    assert process_file(bad, conn)["run_id"] == result["run_id"]     # a retry reuses the run


def test_api(conn, sample_files, monkeypatch):
    process_file(sample_files["ME-1"], conn)
    monkeypatch.setenv("DATABASE_URL", conn.info.dsn + " password=" + (conn.info.password or ""))
    client = TestClient(api.app)

    summary = client.get("/vessels/MV-ORION/equipment/ME-1/summary", params={"day": "2026-10-20"}).json()
    by_metric = {m["metric"]: m for m in summary["metrics"]}
    assert by_metric["rpm"]["minutes_with_data"] == 355
    assert by_metric["rpm"]["minutes_interpolated"] == 1
    assert isinstance(by_metric["rpm"]["avg"], float)
    assert summary["alerts"][0]["started_at"] == "2026-10-20T02:00:00Z"
    assert [a["rule"] for a in summary["alerts"]] == ["exhaust_temp_high"]

    series = client.get("/vessels/MV-ORION/equipment/ME-1/readings", params={
        "metric": "exhaust_temp_c", "start": "2026-10-20T02:00:00Z", "end": "2026-10-20T02:10:00Z"}).json()
    assert len(series) == 10 and series[0]["avg_value"] > 450

    assert client.get("/vessels/MV-ORION/equipment/ME-1/readings", params={
        "metric": "pressure", "start": "2026-10-20T00:00:00Z", "end": "2026-10-20T01:00:00Z"}).status_code == 400
    assert client.get("/vessels/MV-ORION/equipment/ME-1/summary", params={"day": "2020-01-01"}).status_code == 404
    assert client.get("/runs/999/issues").status_code == 404
    assert len(client.get("/alerts", params={"vessel_id": "MV-ORION"}).json()) == 1

"""Read API for the monitoring dashboard and other services (FastAPI).

    uvicorn shipsense.api:app --reload
"""

import os
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from psycopg.rows import dict_row

from .config import METRICS

app = FastAPI(title="ShipSense", description="Equipment monitoring data from ship sensors")


def db() -> Iterator[psycopg.Connection]:
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute("SET TIME ZONE 'UTC'")  # timestamps in responses are always UTC
        yield conn


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/runs")
def runs(limit: int = Query(20, ge=1, le=200), conn: psycopg.Connection = Depends(db)) -> list[dict]:
    return conn.execute("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT %s", (limit,)).fetchall()


@app.get("/runs/{run_id}/issues")
def run_issues(run_id: int, conn: psycopg.Connection = Depends(db)) -> list[dict]:
    if not conn.execute("SELECT 1 FROM pipeline_runs WHERE id = %s", (run_id,)).fetchone():
        raise HTTPException(404, f"No run {run_id}")
    return conn.execute("""SELECT issue, metric, ts, detail FROM quality_issues WHERE run_id = %s
                           ORDER BY ts NULLS FIRST, id""", (run_id,)).fetchall()


@app.get("/vessels/{vessel_id}/equipment/{equipment_id}/readings")
def readings(vessel_id: str, equipment_id: str, metric: str, start: datetime, end: datetime,
             conn: psycopg.Connection = Depends(db)) -> list[dict]:
    if metric not in METRICS:
        raise HTTPException(400, f"Unknown metric '{metric}'. Known: {sorted(METRICS)}")
    if end <= start or end - start > timedelta(days=7):
        raise HTTPException(400, "end must be after start, and the range at most 7 days")
    return conn.execute("""
        SELECT minute, avg_value, min_value, max_value, samples, interpolated
        FROM readings_1min
        WHERE vessel_id = %s AND equipment_id = %s AND metric = %s AND minute >= %s AND minute < %s
        ORDER BY minute""", (vessel_id, equipment_id, metric, start, end)).fetchall()


@app.get("/vessels/{vessel_id}/equipment/{equipment_id}/summary")
def daily_summary(vessel_id: str, equipment_id: str, day: date, conn: psycopg.Connection = Depends(db)) -> dict:
    """Per-sensor statistics and data completeness for one UTC day."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    metrics = conn.execute("""
        SELECT metric,
               round(avg(avg_value)::numeric, 2)::float8                  AS avg,
               round(min(min_value)::numeric, 2)::float8                  AS min,
               round(max(max_value)::numeric, 2)::float8                  AS max,
               count(*)                                                   AS minutes_with_data,
               count(*) FILTER (WHERE interpolated)                       AS minutes_interpolated
        FROM readings_1min
        WHERE vessel_id = %s AND equipment_id = %s AND minute >= %s AND minute < %s
        GROUP BY metric ORDER BY metric""", (vessel_id, equipment_id, start, end)).fetchall()
    if not metrics:
        raise HTTPException(404, f"No data for {vessel_id}/{equipment_id} on {day}")
    alerts = conn.execute("""
        SELECT rule, metric, started_at, ended_at, peak_value FROM alerts
        WHERE vessel_id = %s AND equipment_id = %s AND started_at >= %s AND started_at < %s
        ORDER BY started_at""", (vessel_id, equipment_id, start, end)).fetchall()
    return {"vessel_id": vessel_id, "equipment_id": equipment_id, "day": day, "metrics": metrics, "alerts": alerts}


@app.get("/alerts")
def alerts(vessel_id: str | None = None, since: datetime | None = None,
           conn: psycopg.Connection = Depends(db)) -> list[dict]:
    return conn.execute("""
        SELECT vessel_id, equipment_id, rule, metric, started_at, ended_at, peak_value FROM alerts
        WHERE (%(vessel)s::text IS NULL OR vessel_id = %(vessel)s)
          AND (%(since)s::timestamptz IS NULL OR started_at >= %(since)s)
        ORDER BY started_at DESC LIMIT 500""", {"vessel": vessel_id, "since": since}).fetchall()

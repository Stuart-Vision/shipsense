"""Runs one raw file through extract -> clean -> 1-minute aggregation -> alerts -> PostgreSQL.

Each file is recorded in pipeline_runs with its SHA-256, so delivering the same
file twice (a common thing with ship-to-shore uploads) never double-loads it.
All writes for a file happen in one transaction: a file is loaded completely or
not at all, and a failure is recorded on the run with its error.
"""

import hashlib
import os
from importlib import resources
from pathlib import Path

import pandas as pd
import psycopg

from .anomalies import detect
from .extract import read_file
from .transform import clean, to_minutes


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or database_url())


def database_url() -> str:
    """DATABASE_URL locally; on AWS, the connection string is read from Secrets Manager
    (DATABASE_SECRET_ARN) so the password never sits in an environment variable."""
    if url := os.environ.get("DATABASE_URL"):
        return url
    arn = os.environ.get("DATABASE_SECRET_ARN")
    if not arn:
        raise RuntimeError("Set DATABASE_URL or DATABASE_SECRET_ARN")
    import json
    import boto3
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
    return json.loads(secret)["url"]


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute(resources.files("shipsense").joinpath("schema.sql").read_text(encoding="utf-8"))
    conn.commit()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def process_file(path: Path, conn: psycopg.Connection, source: str | None = None) -> dict:
    source = source or path.name
    digest = sha256(path)

    existing = conn.execute("SELECT id, status FROM pipeline_runs WHERE file_sha256 = %s", (digest,)).fetchone()
    if existing and existing[1] == "succeeded":
        conn.rollback()
        return {"run_id": existing[0], "source": source, "status": "skipped", "reason": "file already loaded"}
    if existing:  # a previous attempt failed: retry under the same run
        run_id = existing[0]
        conn.execute("UPDATE pipeline_runs SET status = 'running', started_at = now(), finished_at = NULL, error = NULL, source = %s WHERE id = %s",
                     (source, run_id))
    else:
        run_id = conn.execute("INSERT INTO pipeline_runs (source, file_sha256, status) VALUES (%s, %s, 'running') RETURNING id",
                              (source, digest)).fetchone()[0]
    conn.commit()

    try:
        readings, read_issues = read_file(path)
        cleaned, clean_issues = clean(readings)
        minutes, gap_issues = to_minutes(cleaned)
        alerts = detect(minutes)
        issues = pd.concat([read_issues, clean_issues, gap_issues], ignore_index=True)

        _load_minutes(conn, minutes, run_id)
        _load_issues(conn, issues, run_id)
        new_alerts = _load_alerts(conn, alerts, run_id)
        conn.execute("""UPDATE pipeline_runs SET status = 'succeeded', finished_at = now(), raw_rows = %s,
                        clean_rows = %s, minutes_loaded = %s, issues = %s, alerts = %s WHERE id = %s""",
                     (len(readings) + len(read_issues), len(cleaned), len(minutes), len(issues), new_alerts, run_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.execute("UPDATE pipeline_runs SET status = 'failed', finished_at = now(), error = %s WHERE id = %s",
                     (f"{type(e).__name__}: {e}"[:1000], run_id))
        conn.commit()
        return {"run_id": run_id, "source": source, "status": "failed", "error": f"{type(e).__name__}: {e}"}

    return {"run_id": run_id, "source": source, "status": "succeeded",
            "raw_rows": len(readings) + len(read_issues), "clean_rows": len(cleaned),
            "minutes_loaded": len(minutes), "issues": len(issues), "alerts": new_alerts}


def _load_minutes(conn: psycopg.Connection, minutes: pd.DataFrame, run_id: int) -> None:
    """Bulk-load with COPY into a staging table, then upsert: fast and idempotent."""
    conn.execute("CREATE TEMP TABLE staging_1min (LIKE readings_1min INCLUDING DEFAULTS) ON COMMIT DROP")
    with conn.cursor().copy(
            "COPY staging_1min (vessel_id, equipment_id, metric, minute, avg_value, min_value, max_value, "
            "samples, interpolated, run_id) FROM STDIN") as copy:
        for r in minutes.itertuples(index=False):
            copy.write_row((r.vessel_id, r.equipment_id, r.metric, r.minute.to_pydatetime(), float(r.avg_value),
                            float(r.min_value), float(r.max_value), int(r.samples), bool(r.interpolated), run_id))
    # A minute delivered again (e.g. an overlapping re-export) takes the newest values.
    conn.execute("""
        INSERT INTO readings_1min (vessel_id, equipment_id, metric, minute, avg_value, min_value, max_value,
                                   samples, interpolated, run_id)
        SELECT vessel_id, equipment_id, metric, minute, avg_value, min_value, max_value, samples, interpolated, run_id
        FROM staging_1min
        ON CONFLICT (vessel_id, equipment_id, metric, minute) DO UPDATE SET
            avg_value = EXCLUDED.avg_value, min_value = EXCLUDED.min_value, max_value = EXCLUDED.max_value,
            samples = EXCLUDED.samples, interpolated = EXCLUDED.interpolated, run_id = EXCLUDED.run_id""")


def _load_issues(conn: psycopg.Connection, issues: pd.DataFrame, run_id: int) -> None:
    rows = [(run_id, r.vessel_id, r.equipment_id, r.metric, None if pd.isna(r.ts) else pd.Timestamp(r.ts).to_pydatetime(),
             r.issue, r.detail) for r in issues.itertuples(index=False)]
    if rows:
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO quality_issues (run_id, vessel_id, equipment_id, metric, ts, issue, detail)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)""", rows)


def _load_alerts(conn: psycopg.Connection, alerts: pd.DataFrame, run_id: int) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for a in alerts.itertuples(index=False):
            cur.execute("""INSERT INTO alerts (run_id, vessel_id, equipment_id, metric, rule, started_at, ended_at, peak_value)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (vessel_id, equipment_id, rule, started_at) DO NOTHING""",
                        (run_id, a.vessel_id, a.equipment_id, a.metric, a.rule, a.started_at.to_pydatetime(),
                         a.ended_at.to_pydatetime(), float(a.peak_value)))
            inserted += cur.rowcount
    return inserted

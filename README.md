# ShipSense: data pipeline for shipboard machinery sensors

[![CI](https://github.com/Stuart-Vision/shipsense/actions/workflows/ci.yml/badge.svg)](https://github.com/Stuart-Vision/shipsense/actions/workflows/ci.yml)

Ships log sensor readings from their main engines and generators: RPM, exhaust and coolant temperature, lube-oil pressure, fuel rate and vibration. The files reach shore messy:
- different formats;
- some temperatures in Fahrenheit;
- duplicate rows after reconnects;
- sensor faults and logger outages.

ShipSense cleans them and stores **one row per sensor per minute** in PostgreSQL. It raises **equipment-health alerts** and serves the results through a REST API for remote-monitoring dashboards.

**Python · pandas · NumPy · PostgreSQL · FastAPI · AWS (S3, Lambda, RDS, Secrets Manager, SQS) · pytest · GitHub Actions**

```mermaid
flowchart LR
  S[Ship data loggers<br/>CSV every 10 s] --> B[(S3 raw bucket)]
  G[IoT gateways<br/>JSON Lines every 30 s] --> B
  B -->|ObjectCreated event| L[Lambda: extract → clean → 1-min aggregate → alerts]
  L --> DB[(PostgreSQL / RDS<br/>readings_1min · alerts<br/>quality_issues · pipeline_runs)]
  L -. after 2 failed retries .-> Q[SQS dead-letter queue]
  DB --> API[FastAPI: readings, daily summary, alerts]
```

## What the pipeline does

| Step | What happens | Reported as |
| --- | --- | --- |
| **Extract** | Logger CSV (wide: one column per sensor) and gateway JSON Lines (long: one reading per line) both become one long table. Timestamps with a ship's UTC offset become **UTC**. | `bad_timestamp`, `missing_value`, `unparseable_line` |
| **Units** | Older loggers report exhaust temperature in °F; it is converted to °C. | |
| **Duplicates** | Readings resent after a reconnect are removed. | `duplicate` (one per sensor, with a count) |
| **Validity** | Physically impossible values, such as a coolant reading of 999 °C, are sensor faults. They are dropped, not averaged in. | `out_of_range` |
| **Aggregate** | Each sensor is resampled to **1-minute avg / min / max / sample count**. | |
| **Gaps** | Gaps of up to 2 minutes are filled by linear interpolation and flagged `interpolated`. Longer outages are left empty, because inventing data would hide them. | `gap` |
| **Alerts** | Threshold rules must hold for several consecutive minutes, e.g. exhaust above 450 °C for 3+ minutes. Low lube-oil pressure only counts **while the engine runs** (rpm > 300). Vibration uses a **rolling z-score** against the previous hour's baseline. | `alerts` table |

Every change to the data is recorded in `quality_issues`, so the data team can see exactly what was dropped and why.

## Loading: reliable and repeatable

- **Each file is processed once.** Its SHA-256 is stored in `pipeline_runs`, so a file delivered twice, even renamed, is skipped.
- **All or nothing.** All writes for a file are one transaction. A failed file leaves no partial data; the run is marked `failed` with the error and can be retried.
- **Fast bulk load.** Data goes in with `COPY` into a staging table, then `INSERT … ON CONFLICT DO UPDATE`. Overlapping re-exports update minutes instead of duplicating them.
- **Indexes for time-series:** the primary key `(vessel, equipment, metric, minute)` serves "one sensor over a time range", and a **BRIN** index on `minute` speeds up scans across all sensors by time at a tiny size.

## AWS deployment (`template.yaml`, AWS SAM)

- Files uploaded to `s3://…-raw-…/raw/` trigger the Lambda (Python 3.13, arm64).
- Lambda runs inside the VPC and connects to **RDS for PostgreSQL**. The connection string comes from **Secrets Manager**, never from a plain environment variable.
- Least-privilege IAM: the Lambda can only read the raw bucket, read its one secret, and send to its dead-letter queue.
- Failed events are retried twice, then sent to an **SQS dead-letter queue** for investigation and replay.
- The raw bucket is private and encrypted, and moves files to Glacier Instant Retrieval after 30 days.

The template is validated and linted in CI. It hasn't been deployed to an AWS account.

## Run it

```bash
python -m venv .venv && .venv/Scripts/activate          # or source .venv/bin/activate
pip install -r requirements-dev.txt

export DATABASE_URL=postgresql://user:pass@localhost:5432/shipsense
python scripts/generate_samples.py                      # writes data/samples/
python -m shipsense.run data/samples/*.csv data/samples/*.jsonl
uvicorn shipsense.api:app --reload                      # http://localhost:8000/docs
```

Or run `docker compose up --build` for PostgreSQL plus the API.

| Endpoint | |
| --- | --- |
| `GET /vessels/{vessel}/equipment/{equipment}/readings?metric=&start=&end=` | 1-minute series for one sensor (up to 7 days) |
| `GET /vessels/{vessel}/equipment/{equipment}/summary?day=` | Per-sensor avg/min/max, minutes with data, interpolated minutes, and the day's alerts |
| `GET /alerts?vessel_id=&since=` | Alerts, newest first |
| `GET /runs`, `GET /runs/{id}/issues` | Pipeline runs and their data-quality issues |

## Sample data and tests

`scripts/generate_samples.py` writes 6 hours of realistic data for one vessel, with a fixed random seed. Each fault is injected on purpose so the tests can check it is caught:
- main engine: an 8-minute exhaust overheat, a 5-minute and a 1-minute logger outage, 20 resent rows, 3 faulty coolant readings, 4 empty values;
- generator: 4 minutes of low oil pressure while running, a 6-minute vibration jump, 3 corrupt lines, and an unknown sensor.

`pytest` runs **20 tests**:
- **unit tests** for extraction, cleaning, resampling, gap handling and every alert rule;
- **integration tests against PostgreSQL** that load the samples end to end and check every count. They also cover:
  - re-delivery and overlapping files;
  - a bad file failing cleanly;
  - the REST API;
  - the Lambda handler with a fake S3 client;
  - Secrets Manager lookup.

Database tests run when `TEST_DATABASE_URL` points at a throwaway database. CI provides one as a Postgres service container.

## Layout

```
shipsense/extract.py      CSV / JSON Lines -> long table (UTC)
shipsense/transform.py    units, duplicates, validity, 1-minute resampling, gaps
shipsense/anomalies.py    sustained-threshold and rolling z-score alerts
shipsense/pipeline.py     orchestration, idempotency, COPY + upsert into PostgreSQL
shipsense/schema.sql      tables and indexes
shipsense/api.py          FastAPI read API
shipsense/lambda_handler.py   S3-triggered AWS Lambda entry point
template.yaml             AWS SAM: S3, Lambda (VPC), Secrets Manager, SQS DLQ
```

-- ShipSense schema (PostgreSQL). Safe to run repeatedly.

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id             BIGSERIAL   PRIMARY KEY,
    source         TEXT        NOT NULL,
    file_sha256    TEXT        NOT NULL UNIQUE,   -- the same file is never processed twice
    status         TEXT        NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    raw_rows       INT,
    clean_rows     INT,
    minutes_loaded INT,
    issues         INT,
    alerts         INT,
    error          TEXT
);

-- One row per sensor per minute.
CREATE TABLE IF NOT EXISTS readings_1min (
    vessel_id     TEXT             NOT NULL,
    equipment_id  TEXT             NOT NULL,
    metric        TEXT             NOT NULL,
    minute        TIMESTAMPTZ      NOT NULL,
    avg_value     DOUBLE PRECISION NOT NULL,
    min_value     DOUBLE PRECISION NOT NULL,
    max_value     DOUBLE PRECISION NOT NULL,
    samples       INT              NOT NULL,
    interpolated  BOOLEAN          NOT NULL DEFAULT false,
    run_id        BIGINT           NOT NULL REFERENCES pipeline_runs (id),
    -- Also the index for "one sensor over a time range", the main query.
    PRIMARY KEY (vessel_id, equipment_id, metric, minute)
);
-- BRIN: a tiny index that suits append-only time-series and speeds up scans by time across all sensors.
CREATE INDEX IF NOT EXISTS readings_minute_brin ON readings_1min USING BRIN (minute);

CREATE TABLE IF NOT EXISTS quality_issues (
    id            BIGSERIAL   PRIMARY KEY,
    run_id        BIGINT      NOT NULL REFERENCES pipeline_runs (id),
    vessel_id     TEXT,
    equipment_id  TEXT,
    metric        TEXT,
    ts            TIMESTAMPTZ,
    issue         TEXT        NOT NULL,
    detail        TEXT
);
CREATE INDEX IF NOT EXISTS quality_issues_run ON quality_issues (run_id);

CREATE TABLE IF NOT EXISTS alerts (
    id            BIGSERIAL        PRIMARY KEY,
    run_id        BIGINT           NOT NULL REFERENCES pipeline_runs (id),
    vessel_id     TEXT             NOT NULL,
    equipment_id  TEXT             NOT NULL,
    metric        TEXT             NOT NULL,
    rule          TEXT             NOT NULL,
    started_at    TIMESTAMPTZ      NOT NULL,
    ended_at      TIMESTAMPTZ      NOT NULL,
    peak_value    DOUBLE PRECISION NOT NULL,
    UNIQUE (vessel_id, equipment_id, rule, started_at)
);
CREATE INDEX IF NOT EXISTS alerts_vessel_time ON alerts (vessel_id, started_at DESC);

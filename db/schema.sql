-- 4WARD schema for Tiger Data (Postgres + TimescaleDB).
-- Run once:  python -m backend.db --init   (needs DATABASE_URL), or paste into the Tiger Data SQL editor.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- One row per TTC route per poll (~every 30 s): how many trips are reporting, how many were cancelled
-- (GTFS-RT trip_update schedule_relationship = CANCELED/DELETED), mean reported delay, active alerts.
CREATE TABLE IF NOT EXISTS ttc_route_snapshot (
    ts              timestamptz      NOT NULL,
    route_id        text             NOT NULL,
    active_trips    integer          NOT NULL,
    cancelled_trips integer          NOT NULL DEFAULT 0,
    avg_delay_s     double precision,
    alerts          integer          NOT NULL DEFAULT 0,
    source          text             NOT NULL DEFAULT 'live'   -- live | simulated | replay
);
SELECT create_hypertable('ttc_route_snapshot', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS ttc_route_snapshot_route_ts ON ttc_route_snapshot (route_id, ts DESC);

-- One row per poll for the whole Bike Share Toronto system (GBFS station_status summed).
CREATE TABLE IF NOT EXISTS bike_system_snapshot (
    ts       timestamptz NOT NULL,
    stations integer     NOT NULL,
    bikes    integer     NOT NULL,
    ebikes   integer     NOT NULL DEFAULT 0,
    docks    integer     NOT NULL,
    source   text        NOT NULL DEFAULT 'live'
);
SELECT create_hypertable('bike_system_snapshot', 'ts', if_not_exists => TRUE);

-- Every 4WARD run (what the user asked and what each bot chose).
CREATE TABLE IF NOT EXISTS bot_runs (
    ts      timestamptz NOT NULL DEFAULT now(),
    run_id  text        NOT NULL,
    prompt  text        NOT NULL,
    result  jsonb       NOT NULL
);

-- Continuous aggregates: pre-computed hourly rollups the dashboard charts read instantly.
CREATE MATERIALIZED VIEW IF NOT EXISTS ttc_route_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket(INTERVAL '1 hour', ts) AS bucket,
       route_id,
       avg(active_trips)    AS avg_active,
       avg(cancelled_trips) AS avg_cancelled,
       avg(avg_delay_s)     AS avg_delay_s,
       max(alerts)          AS max_alerts,
       count(*)             AS samples
FROM ttc_route_snapshot
GROUP BY bucket, route_id
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS bike_system_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket(INTERVAL '1 hour', ts) AS bucket,
       avg(bikes)  AS avg_bikes,
       avg(ebikes) AS avg_ebikes,
       avg(docks)  AS avg_docks,
       min(bikes)  AS min_bikes,
       count(*)    AS samples
FROM bike_system_snapshot
GROUP BY bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy('ttc_route_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 minute', schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('bike_system_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 minute', schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE);

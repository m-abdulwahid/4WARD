-- Storage policies for the free-tier size limit. Applied by `python -m backend.db --init` AFTER schema.sql.
-- Each statement is run on its own and a failure only prints a warning (policy syntax differs between
-- TimescaleDB versions), so a policy problem never blocks the core schema.

ALTER TABLE ttc_route_snapshot SET (timescaledb.compress, timescaledb.compress_segmentby = 'route_id');
SELECT add_compression_policy('ttc_route_snapshot', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('ttc_route_snapshot', INTERVAL '30 days', if_not_exists => TRUE);

ALTER TABLE bike_system_snapshot SET (timescaledb.compress);
SELECT add_compression_policy('bike_system_snapshot', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('bike_system_snapshot', INTERVAL '30 days', if_not_exists => TRUE);

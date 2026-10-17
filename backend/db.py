"""Storage: Tiger Data (Postgres + TimescaleDB) when DATABASE_URL is set, otherwise local SQLite.

Both expose the same methods. The Tiger Data path reads the continuous aggregates defined in
db/schema.sql; the SQLite path computes the same hourly rollups with GROUP BY so the app and the
demo work with no cloud account. `python -m backend.db --init` applies the Postgres schema.
"""
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQLITE_FILE = ROOT / "data" / "4ward.db"

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS ttc_route_snapshot (ts INTEGER NOT NULL, route_id TEXT NOT NULL, active_trips INTEGER NOT NULL,
  cancelled_trips INTEGER NOT NULL DEFAULT 0, avg_delay_s REAL, alerts INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'live');
CREATE INDEX IF NOT EXISTS ix_ttc ON ttc_route_snapshot(route_id, ts);
CREATE TABLE IF NOT EXISTS bike_system_snapshot (ts INTEGER NOT NULL, stations INTEGER NOT NULL, bikes INTEGER NOT NULL,
  ebikes INTEGER NOT NULL DEFAULT 0, docks INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'live');
CREATE INDEX IF NOT EXISTS ix_bike ON bike_system_snapshot(ts);
CREATE TABLE IF NOT EXISTS bot_runs (ts INTEGER NOT NULL, run_id TEXT NOT NULL, prompt TEXT NOT NULL, result TEXT NOT NULL);
"""


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Store:
    def __init__(self) -> None:
        self.url = os.environ.get("DATABASE_URL", "").strip()
        self.pg = bool(self.url)
        self.backend = "tigerdata" if self.pg else "sqlite-local"
        self._lite: sqlite3.Connection | None = None
        self._lock = threading.Lock()  # sqlite connection is shared across worker threads

    # -- connections -------------------------------------------------------------------
    def _conn(self):
        if self.pg:
            import psycopg

            return psycopg.connect(self.url, autocommit=True, connect_timeout=8)
        if self._lite is None:
            SQLITE_FILE.parent.mkdir(exist_ok=True)
            self._lite = sqlite3.connect(SQLITE_FILE, check_same_thread=False)
            self._lite.executescript(SQLITE_DDL)
        return self._lite

    def _exec(self, sql: str, params: tuple = (), many: list | None = None, fetch: bool = False):
        with self._lock if not self.pg else _NoLock():
            return self._exec_locked(sql, params, many, fetch)

    def _exec_locked(self, sql: str, params: tuple, many: list | None, fetch: bool):
        c = self._conn()
        q = sql if self.pg else sql.replace("%s", "?")
        try:
            cur = c.cursor()
            if many is not None:
                cur.executemany(q, many)
            else:
                cur.execute(q, params)
            rows = cur.fetchall() if fetch else None
            if not self.pg:
                c.commit()
            return rows
        finally:
            if self.pg:
                c.close()

    # -- writes ------------------------------------------------------------------------
    def insert_ttc(self, ts: float, rows: list[dict], source: str = "live") -> None:
        if not rows:
            return
        if self.pg:
            sql = "INSERT INTO ttc_route_snapshot (ts,route_id,active_trips,cancelled_trips,avg_delay_s,alerts,source) VALUES (to_timestamp(%s),%s,%s,%s,%s,%s,%s)"
        else:
            sql = "INSERT INTO ttc_route_snapshot (ts,route_id,active_trips,cancelled_trips,avg_delay_s,alerts,source) VALUES (%s,%s,%s,%s,%s,%s,%s)"
        self._exec(sql, many=[(int(ts), r["route_id"], r["active_trips"], r["cancelled_trips"], r.get("avg_delay_s"), r.get("alerts", 0), source) for r in rows])

    def insert_bike(self, ts: float, stations: int, bikes: int, ebikes: int, docks: int, source: str = "live") -> None:
        ph = "to_timestamp(%s)" if self.pg else "%s"
        self._exec(f"INSERT INTO bike_system_snapshot (ts,stations,bikes,ebikes,docks,source) VALUES ({ph},%s,%s,%s,%s,%s)",
                   (int(ts), stations, bikes, ebikes, docks, source))

    def insert_run(self, run_id: str, prompt: str, result: dict) -> None:
        payload = json.dumps(result, default=str)
        if self.pg:
            self._exec("INSERT INTO bot_runs (run_id,prompt,result) VALUES (%s,%s,%s::jsonb)", (run_id, prompt, payload))
        else:
            self._exec("INSERT INTO bot_runs (ts,run_id,prompt,result) VALUES (%s,%s,%s,%s)", (int(time.time()), run_id, prompt, payload))

    # -- reads (what the dashboard charts call) ------------------------------------------
    def ttc_hourly(self, hours: int = 24, top: int = 6) -> list[dict]:
        since = int(time.time()) - hours * 3600
        if self.pg:
            sql = ("SELECT extract(epoch from bucket)::bigint, route_id, avg_active, avg_cancelled, avg_delay_s, max_alerts "
                   "FROM ttc_route_hourly WHERE bucket >= to_timestamp(%s) AND route_id IN "
                   "(SELECT route_id FROM ttc_route_hourly WHERE bucket >= to_timestamp(%s) GROUP BY route_id ORDER BY sum(avg_active) DESC LIMIT %s) "
                   "ORDER BY bucket")
            rows = self._exec(sql, (since, since, top), fetch=True)
        else:
            sql = ("SELECT (ts/3600)*3600 AS b, route_id, AVG(active_trips), AVG(cancelled_trips), AVG(avg_delay_s), MAX(alerts) "
                   "FROM ttc_route_snapshot WHERE ts >= %s AND route_id IN "
                   "(SELECT route_id FROM ttc_route_snapshot WHERE ts >= %s GROUP BY route_id ORDER BY SUM(active_trips) DESC LIMIT %s) "
                   "GROUP BY b, route_id ORDER BY b")
            rows = self._exec(sql, (since, since, top), fetch=True)
        return [{"bucket": int(r[0]), "route_id": r[1], "avg_active": r[2], "avg_cancelled": r[3], "avg_delay_s": r[4], "max_alerts": r[5]} for r in rows]

    def bikes_hourly(self, hours: int = 24) -> list[dict]:
        since = int(time.time()) - hours * 3600
        if self.pg:
            rows = self._exec("SELECT extract(epoch from bucket)::bigint, avg_bikes, avg_ebikes, avg_docks, min_bikes FROM bike_system_hourly "
                              "WHERE bucket >= to_timestamp(%s) ORDER BY bucket", (since,), fetch=True)
        else:
            rows = self._exec("SELECT (ts/3600)*3600 AS b, AVG(bikes), AVG(ebikes), AVG(docks), MIN(bikes) FROM bike_system_snapshot "
                              "WHERE ts >= %s GROUP BY b ORDER BY b", (since,), fetch=True)
        return [{"bucket": int(r[0]), "avg_bikes": r[1], "avg_ebikes": r[2], "avg_docks": r[3], "min_bikes": r[4]} for r in rows]

    def sources(self, hours: int = 24) -> dict:
        since = int(time.time()) - hours * 3600
        ph = "to_timestamp(%s)" if self.pg else "%s"
        out = {}
        for t in ("ttc_route_snapshot", "bike_system_snapshot"):
            rows = self._exec(f"SELECT source, count(*) FROM {t} WHERE ts >= {ph} GROUP BY source", (since,), fetch=True)
            out[t] = {r[0]: r[1] for r in rows}
        return out

    def delete_simulated(self) -> None:
        for t in ("ttc_route_snapshot", "bike_system_snapshot"):
            self._exec(f"DELETE FROM {t} WHERE source = 'simulated'")

    def refresh_aggregates(self) -> None:
        """Tiger Data: refresh now instead of waiting for the policy (used right after seeding)."""
        if not self.pg:
            return
        import psycopg

        with psycopg.connect(self.url, autocommit=True) as c:  # CALL refresh_... cannot run in a transaction
            for v in ("ttc_route_hourly", "bike_system_hourly"):
                c.execute(f"CALL refresh_continuous_aggregate('{v}', NULL, NULL)")


store = Store()


def init_postgres() -> None:
    import psycopg

    sql = (ROOT / "db" / "schema.sql").read_text()
    with psycopg.connect(store.url, autocommit=True) as c:
        for stmt in [s.strip() for s in sql.split(";\n") if s.strip() and not all(l.strip().startswith("--") or not l.strip() for l in s.splitlines())]:
            c.execute(stmt)
    print("schema applied to Tiger Data")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    store.__init__()
    if "--init" in sys.argv:
        if not store.pg:
            print("DATABASE_URL not set; local SQLite needs no init (tables are created on first use).")
        else:
            init_postgres()
    else:
        print("backend:", store.backend)

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
CREATE TABLE IF NOT EXISTS bot_steps (ts INTEGER NOT NULL, run_id TEXT NOT NULL, bot TEXT NOT NULL, step INTEGER NOT NULL,
  brain TEXT, action TEXT NOT NULL, ok INTEGER NOT NULL DEFAULT 1, note TEXT);
"""


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Store:
    def __init__(self) -> None:
        # Tiger Data is the main database. SQLite is only the offline fallback: replay mode (REPLAY=1) or no DATABASE_URL.
        self.url = os.environ.get("DATABASE_URL", "").strip()
        self.pg = bool(self.url) and os.environ.get("REPLAY") != "1"
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

    def insert_step(self, run_id: str, bot: str, step: int, action: dict, brain: str | None = None, ok: bool = True, note: str | None = None) -> None:
        payload = json.dumps(action, default=str)
        if self.pg:
            self._exec("INSERT INTO bot_steps (run_id,bot,step,brain,action,ok,note) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)",
                       (run_id, bot, step, brain, payload, ok, note))
        else:
            self._exec("INSERT INTO bot_steps (ts,run_id,bot,step,brain,action,ok,note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                       (int(time.time()), run_id, bot, step, brain, payload, int(ok), note))

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

    def ttc_daily_at_dow_hour(self, route_id: str, dow: int, hour: int, days: int = 56) -> dict[str, float]:
        """{date_str: avg_delay_s} for this route at this day-of-week/hour, most recent `days` days.
        Used by reliability scoring to 'replay' historical days. dow: 0=Sunday (matches SQLite %w and
        Postgres extract(dow)). Times are Toronto-local on Postgres; UTC on the SQLite fallback
        (acceptable for the offline/local demo path, not used in production with Tiger Data)."""
        since = int(time.time()) - days * 86400
        if self.pg:
            rows = self._exec(
                "SELECT (date_trunc('day', ts AT TIME ZONE 'America/Toronto'))::date::text, avg(avg_delay_s) "
                "FROM ttc_route_snapshot WHERE route_id = %s "
                "AND extract(dow FROM ts AT TIME ZONE 'America/Toronto') = %s "
                "AND extract(hour FROM ts AT TIME ZONE 'America/Toronto') = %s "
                "AND ts >= to_timestamp(%s) AND avg_delay_s IS NOT NULL GROUP BY 1",
                (route_id, dow, hour, since), fetch=True)
        else:
            rows = self._exec(
                "SELECT strftime('%Y-%m-%d', ts, 'unixepoch'), AVG(avg_delay_s) FROM ttc_route_snapshot "
                "WHERE route_id = %s AND CAST(strftime('%w', ts, 'unixepoch') AS INTEGER) = %s "
                "AND CAST(strftime('%H', ts, 'unixepoch') AS INTEGER) = %s AND ts >= %s "
                "AND avg_delay_s IS NOT NULL GROUP BY 1",
                (route_id, dow, hour, since), fetch=True)
        return {r[0]: r[1] for r in (rows or []) if r[1] is not None}

    def latest_route_delay(self, route_id: str, max_age_s: int = 300) -> tuple[float, float] | None:
        """(avg delay seconds, age seconds) of the newest LIVE poll for a TTC route, or None if there is none that fresh."""
        now = time.time()
        if self.pg:
            rows = self._exec("SELECT extract(epoch from ts)::bigint, avg_delay_s FROM ttc_route_snapshot WHERE route_id = %s AND source = 'live' "
                              "AND avg_delay_s IS NOT NULL ORDER BY ts DESC LIMIT 1", (route_id,), fetch=True)
        else:
            rows = self._exec("SELECT ts, avg_delay_s FROM ttc_route_snapshot WHERE route_id = %s AND source = 'live' "
                              "AND avg_delay_s IS NOT NULL ORDER BY ts DESC LIMIT 1", (route_id,), fetch=True)
        if not rows:
            return None
        ts, delay = int(rows[0][0]), float(rows[0][1])
        return (delay, now - ts) if now - ts <= max_age_s else None

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

    def live(self, route_id: str | None = None, history_hours: int = 24) -> dict:
        """Latest raw poll + 3h sparkline + hourly CAGG history for the Live data panel."""
        t0 = time.perf_counter()
        routes_available = self._live_route_ids()
        chosen = route_id if route_id in routes_available else (routes_available[0] if routes_available else None)
        latest_routes, latest_ts, latest_source = self._live_latest_routes()
        bikes = self._live_latest_bikes()
        sparkline = self._live_sparkline(chosen, hours=3) if chosen else []
        history = self._live_history(chosen, hours=history_hours) if chosen else []
        first_ts, poll_count = self._live_footer_stats()
        query_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "backend": self.backend,
            "query_ms": query_ms,
            "route_id": chosen,
            "routes_available": routes_available,
            "latest": {
                "ts": latest_ts,
                "source": latest_source,
                "routes": latest_routes,
                "bikes": bikes,
            },
            "sparkline": sparkline,
            "history": history,
            "footer": {
                "collected_since": first_ts,
                "poll_count": poll_count,
                "last_update_ts": latest_ts,
                "query_ms": query_ms,
            },
        }

    def _ts_epoch(self, col: str = "ts") -> str:
        return f"extract(epoch from {col})::bigint" if self.pg else col

    def _live_route_ids(self) -> list[str]:
        since = int(time.time()) - 3 * 3600
        ph = "to_timestamp(%s)" if self.pg else "%s"
        rows = self._exec(
            f"SELECT route_id FROM ttc_route_snapshot WHERE ts >= {ph} "
            f"GROUP BY route_id ORDER BY count(*) DESC, route_id LIMIT 40",
            (since,),
            fetch=True,
        )
        return [r[0] for r in (rows or [])]

    def _live_latest_routes(self) -> tuple[list[dict], int | None, str | None]:
        te = self._ts_epoch()
        if self.pg:
            rows = self._exec(
                f"SELECT {te}, route_id, active_trips, cancelled_trips, avg_delay_s, alerts, source "
                "FROM ttc_route_snapshot WHERE ts = (SELECT max(ts) FROM ttc_route_snapshot) "
                "ORDER BY route_id",
                fetch=True,
            )
        else:
            rows = self._exec(
                f"SELECT {te}, route_id, active_trips, cancelled_trips, avg_delay_s, alerts, source "
                "FROM ttc_route_snapshot WHERE ts = (SELECT max(ts) FROM ttc_route_snapshot) "
                "ORDER BY route_id",
                fetch=True,
            )
        if not rows:
            return [], None, None
        out = [
            {
                "route_id": r[1],
                "active_trips": r[2],
                "cancelled_trips": r[3],
                "avg_delay_s": r[4],
                "alerts": r[5],
                "ts": int(r[0]),
                "source": r[6],
            }
            for r in rows
        ]
        return out, int(rows[0][0]), rows[0][6]

    def _live_latest_bikes(self) -> dict | None:
        te = self._ts_epoch()
        rows = self._exec(
            f"SELECT {te}, stations, bikes, ebikes, docks, source FROM bike_system_snapshot "
            "ORDER BY ts DESC LIMIT 1",
            fetch=True,
        )
        if not rows:
            return None
        r = rows[0]
        return {
            "ts": int(r[0]),
            "stations": r[1],
            "bikes": r[2],
            "ebikes": r[3],
            "docks": r[4],
            "source": r[5],
        }

    def _live_sparkline(self, route_id: str, hours: int = 3) -> list[dict]:
        since = int(time.time()) - hours * 3600
        ph = "to_timestamp(%s)" if self.pg else "%s"
        te = self._ts_epoch()
        rows = self._exec(
            f"SELECT {te}, avg_delay_s, source FROM ttc_route_snapshot "
            f"WHERE route_id = %s AND ts >= {ph} ORDER BY ts",
            (route_id, since),
            fetch=True,
        )
        return [{"ts": int(r[0]), "avg_delay_s": r[1], "source": r[2]} for r in (rows or [])]

    def _live_history(self, route_id: str, hours: int = 24) -> list[dict]:
        since = int(time.time()) - hours * 3600
        if self.pg:
            rows = self._exec(
                "SELECT extract(epoch from bucket)::bigint, avg_delay_s, samples "
                "FROM ttc_route_hourly WHERE route_id = %s AND bucket >= to_timestamp(%s) ORDER BY bucket",
                (route_id, since),
                fetch=True,
            )
        else:
            rows = self._exec(
                "SELECT (ts/3600)*3600 AS b, AVG(avg_delay_s), COUNT(*) "
                "FROM ttc_route_snapshot WHERE route_id = %s AND ts >= %s GROUP BY b ORDER BY b",
                (route_id, since),
                fetch=True,
            )
        return [{"bucket": int(r[0]), "avg_delay_s": r[1], "samples": int(r[2] or 0)} for r in (rows or [])]

    def _live_footer_stats(self) -> tuple[int | None, int]:
        te = self._ts_epoch()
        # Distinct poll timestamps on the bike table ≈ number of collector cycles (one row per poll).
        rows = self._exec(f"SELECT min({te}), count(*) FROM bike_system_snapshot", fetch=True)
        if not rows or rows[0][0] is None:
            return None, 0
        return int(rows[0][0]), int(rows[0][1])


store = Store()


def init_postgres() -> None:
    import psycopg

    def statements(name: str) -> list[str]:
        sql = (ROOT / "db" / name).read_text()
        return [s.strip() for s in sql.split(";\n") if s.strip() and not all(l.strip().startswith("--") or not l.strip() for l in s.splitlines())]

    with psycopg.connect(store.url, autocommit=True) as c:
        for stmt in statements("schema.sql"):
            c.execute(stmt)
        print("schema applied to Tiger Data")
        for stmt in statements("policies.sql"):  # best effort: a policy syntax difference must not block the schema
            try:
                c.execute(stmt)
            except Exception as e:
                print("policy skipped:", stmt.splitlines()[-1][:70], "->", type(e).__name__)


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

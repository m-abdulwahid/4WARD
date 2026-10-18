"use client";
import { useCallback, useEffect, useState } from "react";
import { API } from "@/lib/api";
import { Sparkline, HistoryBars, ago, fmtTs } from "@/components/charts";

type RouteNow = {
  route_id: string;
  active_trips: number;
  cancelled_trips: number;
  avg_delay_s: number | null;
  alerts: number;
  ts: number;
  source: string;
};
type BikesNow = {
  ts: number;
  stations: number;
  bikes: number;
  ebikes: number;
  docks: number;
  source: string;
};
type Spark = { ts: number; avg_delay_s: number | null; source: string };
type Hist = { bucket: number; avg_delay_s: number | null; samples: number };
type LiveResp = {
  backend: string;
  query_ms: number;
  route_id: string | null;
  routes_available: string[];
  latest: {
    ts: number | null;
    source: string | null;
    routes: RouteNow[];
    bikes: BikesNow | null;
  };
  sparkline: Spark[];
  history: Hist[];
  footer: {
    collected_since: number | null;
    poll_count: number;
    last_update_ts: number | null;
    query_ms: number;
  };
};

export default function LivePanel() {
  const [route, setRoute] = useState<string>("");
  const [data, setData] = useState<LiveResp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (rt: string) => {
    try {
      const q = new URLSearchParams();
      if (rt) q.set("route", rt);
      const r = await fetch(`${API}/live?${q}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j: LiveResp = await r.json();
      setData(j);
      setErr(null);
      if (!rt && j.route_id) setRoute(j.route_id);
    } catch {
      setErr("Live data unavailable (backend offline or Tiger Data not ready).");
    }
  }, []);

  useEffect(() => {
    load(route);
    const id = setInterval(() => load(route), 10_000);
    return () => clearInterval(id);
  }, [route, load]);

  const bikes = data?.latest.bikes;
  const routes = data?.latest.routes ?? [];
  const topDelayed = [...routes]
    .filter((r) => r.avg_delay_s != null)
    .sort((a, b) => (b.avg_delay_s ?? 0) - (a.avg_delay_s ?? 0))
    .slice(0, 8);
  const chosen = routes.find((r) => r.route_id === (route || data?.route_id));
  const sparkPts = (data?.sparkline ?? [])
    .filter((s) => s.avg_delay_s != null)
    .map((s) => ({ x: s.ts, y: s.avg_delay_s as number }));

  return (
    <section aria-label="Live Tiger Data feeds" className="mt-3 rounded-lg bg-white p-3 shadow-sm" data-testid="live-panel">
      <div className="mb-2 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-sm font-bold">Live data</h2>
          <p className="text-[11px] text-zinc-500">
            Raw poll tables + hourly continuous aggregate · auto-refresh 10s · backend:{" "}
            {data?.backend ?? "…"}
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs text-zinc-700">
          Route
          <select
            className="rounded border border-zinc-300 bg-white px-2 py-1 text-sm"
            data-testid="live-route"
            value={route || data?.route_id || ""}
            onChange={(e) => setRoute(e.target.value)}
            aria-label="Choose TTC route for sparkline and history"
          >
            {(data?.routes_available ?? []).map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
      </div>

      {err ? (
        <p className="text-sm text-red-700">{err}</p>
      ) : !data ? (
        <p className="text-sm text-zinc-500">Loading live feeds…</p>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded border border-zinc-100 p-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Bike Share now</h3>
              {bikes ? (
                <>
                  <p className="mt-1 text-2xl font-semibold tabular-nums text-zinc-900">
                    {bikes.bikes.toLocaleString()}
                    <span className="ml-1 text-sm font-normal text-zinc-500">bikes</span>
                  </p>
                  <p className="text-xs text-zinc-600">
                    {bikes.ebikes.toLocaleString()} e-bikes · {bikes.docks.toLocaleString()} docks ·{" "}
                    {bikes.stations.toLocaleString()} stations
                  </p>
                  <p className="mt-1 text-[11px] text-zinc-500">
                    Source: {bikes.source} · {fmtTs(bikes.ts)} ({ago(bikes.ts)})
                  </p>
                </>
              ) : (
                <p className="text-sm text-zinc-500">No bike poll yet.</p>
              )}
            </div>

            <div className="rounded border border-zinc-100 p-2 md:col-span-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                Per-route delay now (latest poll)
              </h3>
              {chosen ? (
                <p className="mt-1 text-sm text-zinc-800">
                  Selected route <span className="font-semibold">{chosen.route_id}</span>:{" "}
                  <span className="tabular-nums font-semibold">
                    {chosen.avg_delay_s == null ? "n/a" : `${Math.round(chosen.avg_delay_s)}s`}
                  </span>{" "}
                  avg delay · {chosen.active_trips} active · {chosen.cancelled_trips} cancelled
                  {chosen.alerts ? ` · ${chosen.alerts} alerts` : ""}
                </p>
              ) : null}
              <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs sm:grid-cols-4" data-testid="live-routes">
                {topDelayed.map((r) => (
                  <li key={r.route_id}>
                    <button
                      type="button"
                      className={`tabular-nums underline-offset-2 hover:underline ${r.route_id === (route || data.route_id) ? "font-bold text-zinc-900" : "text-zinc-700"}`}
                      onClick={() => setRoute(r.route_id)}
                    >
                      {r.route_id} {r.avg_delay_s == null ? "—" : `${Math.round(r.avg_delay_s)}s`}
                    </button>
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-[11px] text-zinc-500">
                Source: {data.latest.source ?? "—"} · poll {fmtTs(data.latest.ts)} ({ago(data.latest.ts)}) ·{" "}
                {routes.length} routes in latest snapshot
              </p>
            </div>
          </div>

          <div className="mt-3 grid gap-3 lg:grid-cols-2">
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                Route {data.route_id ?? "—"} · last 3 h (raw polls)
              </h3>
              <Sparkline pts={sparkPts} unit="Delay seconds over last 3 hours" />
              <p className="text-[11px] text-zinc-500">
                Source: raw ttc_route_snapshot · {sparkPts.length} poll points · as of {fmtTs(sparkPts.at(-1)?.x ?? null)}
              </p>
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                Route {data.route_id ?? "—"} · hourly avg delay (continuous aggregate)
              </h3>
              <HistoryBars rows={data.history} />
              <p className="text-[11px] text-zinc-500">
                Source: Tiger Data ttc_route_hourly (real-time aggregate on) · {data.history.length} hours · as of{" "}
                {fmtTs(data.history.at(-1)?.bucket ?? null)}
              </p>
            </div>
          </div>

          <p className="mt-3 border-t border-zinc-100 pt-2 text-[11px] text-zinc-500" data-testid="live-footer">
            Collected since {fmtTs(data.footer.collected_since)}, {data.footer.poll_count.toLocaleString()} polls, last
            update {ago(data.footer.last_update_ts)} · query {data.footer.query_ms} ms · backend {data.backend}
          </p>
        </>
      )}
    </section>
  );
}

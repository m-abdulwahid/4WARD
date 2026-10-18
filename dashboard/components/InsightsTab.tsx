"use client";
import { useEffect, useState } from "react";
import { API } from "@/lib/api";
import { ago, SeriesChart, SERIES_COLORS } from "@/components/charts";

type RouteNow = { route_id: string; avg_delay_s: number | null };
type BikesNow = { bikes: number; ebikes: number; docks: number };
type LiveResp = { backend: string; latest: { ts: number | null; routes: RouteNow[]; bikes: BikesNow | null }; footer: { last_update_ts: number | null } };
type TtcRow = { bucket: number; route_id: string; avg_delay_s: number | null };
type TtcResp = { rows: TtcRow[]; sources: Record<string, number> };

/** A plain LIVE/SIMULATED label from the /insights "sources" breakdown, matching the rest of the app's labelling. */
function sourceLabel(sources: Record<string, number> | undefined): { text: string; tone: string } {
  const live = (sources?.live ?? 0) + (sources?.replay ?? 0);
  const sim = sources?.simulated ?? 0;
  if (live > 0) return { text: "Live", tone: "bg-emerald-50 text-emerald-800" };
  if (sim > 0) return { text: "Simulated backfill", tone: "bg-amber-50 text-amber-900" };
  return { text: "No data yet", tone: "bg-zinc-100 text-zinc-600" };
}

/** Toronto conditions from Tiger Data, trimmed to what a judge can read in five seconds: the live numbers
 * right now, and one chart proving the history is real. No SQL, no internals, no jargon. */
export default function InsightsTab() {
  const [live, setLive] = useState<LiveResp | null>(null);
  const [ttc, setTtc] = useState<TtcResp | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    const load = () =>
      Promise.all([fetch(`${API}/live`).then((r) => r.json()), fetch(`${API}/insights/ttc?hours=12&top=3`).then((r) => r.json())])
        .then(([l, t]) => { setLive(l); setTtc(t); setErr(false); })
        .catch(() => setErr(true));
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, []);

  if (err) return <p className="rounded-xl bg-red-50 p-3 text-sm text-red-800">Can&rsquo;t reach the backend for live conditions right now.</p>;
  if (!live || !ttc) return <p className="rounded-xl bg-zinc-50 p-3 text-sm text-zinc-500">Loading live conditions…</p>;

  const bikes = live.latest.bikes;
  const allRoutesNow = [...live.latest.routes].filter((r) => r.avg_delay_s != null).sort((a, b) => (b.avg_delay_s ?? 0) - (a.avg_delay_s ?? 0));
  const routesNow = allRoutesNow.slice(0, 4);
  const routeIds = Array.from(new Set(ttc.rows.map((r) => r.route_id))).slice(0, 3);
  const src = sourceLabel(ttc.sources);
  const worst = allRoutesNow[0]?.avg_delay_s ?? 1;

  return (
    <div className="flex flex-col gap-3" data-testid="insights-tab">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold tracking-wide text-zinc-500 uppercase">Toronto right now</h2>
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${src.tone}`} data-testid="insights-source">
          {src.text} · {ago(live.latest.ts)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-xl bg-white p-3 ring-1 ring-zinc-200">
          <p className="text-[11px] font-medium text-zinc-500">Bike Share 🚲</p>
          {bikes ? (
            <p className="mt-0.5 text-2xl font-bold tabular-nums text-zinc-900">
              {bikes.bikes.toLocaleString()}
              <span className="ml-1 text-xs font-normal text-zinc-500">bikes free</span>
            </p>
          ) : (
            <p className="mt-1 text-sm text-zinc-400">No data</p>
          )}
          {bikes && <p className="text-[11px] text-zinc-500">{bikes.ebikes.toLocaleString()} e-bikes · {bikes.docks.toLocaleString()} docks</p>}
        </div>
        <div className="rounded-xl bg-white p-3 ring-1 ring-zinc-200">
          <p className="text-[11px] font-medium text-zinc-500">Slowest route</p>
          {routesNow[0] ? (
            <>
              <p className="mt-0.5 text-2xl font-bold tabular-nums text-zinc-900">
                {Math.round(routesNow[0].avg_delay_s ?? 0)}
                <span className="ml-1 text-xs font-normal text-zinc-500">sec delay</span>
              </p>
              <p className="text-[11px] text-zinc-500">Route {routesNow[0].route_id}</p>
            </>
          ) : (
            <p className="mt-1 text-sm text-zinc-400">No delays right now</p>
          )}
        </div>
      </div>

      {routesNow.length > 1 && (
        <ul className="flex flex-wrap gap-2 text-xs" data-testid="insights-routes-now">
          {routesNow.map((r) => (
            <li key={r.route_id} className="rounded-full bg-zinc-100 px-2.5 py-1 tabular-nums text-zinc-700">
              {r.route_id}: {Math.round(r.avg_delay_s ?? 0)}s
            </li>
          ))}
        </ul>
      )}

      <div className="rounded-xl bg-white p-3 ring-1 ring-zinc-200">
        <p className="mb-1 text-[11px] font-medium text-zinc-500">Delay today, busiest routes</p>
        <SeriesChart
          unit="Average delay in seconds"
          fmt={(v) => `${Math.round(v)}s`}
          series={routeIds.map((rt, i) => ({
            name: rt,
            color: SERIES_COLORS[i % SERIES_COLORS.length],
            pts: ttc.rows.filter((r) => r.route_id === rt && r.avg_delay_s !== null).map((r) => ({ x: r.bucket, y: r.avg_delay_s as number })),
          }))}
        />
      </div>

      <details className="group rounded-xl bg-white p-3 ring-1 ring-zinc-200" data-testid="insights-go-deeper">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-[11px] font-semibold text-zinc-600 focus-visible:outline-2 focus-visible:outline-zinc-900">
          Go deeper: every route right now
          <span aria-hidden className="text-zinc-400 transition-transform duration-150 group-open:rotate-180">▾</span>
        </summary>
        <ul className="mt-2 flex flex-col gap-1.5">
          {allRoutesNow.map((r) => (
            <li key={r.route_id} className="flex items-center gap-2 text-xs">
              <span className="w-10 shrink-0 font-semibold text-zinc-700">{r.route_id}</span>
              <span className="h-2 flex-1 overflow-hidden rounded-full bg-zinc-100">
                <span
                  className="block h-full rounded-full bg-sky-500"
                  style={{ width: `${Math.max(4, Math.round(((r.avg_delay_s ?? 0) / Math.max(worst, 1)) * 100))}%` }}
                />
              </span>
              <span className="w-12 shrink-0 text-right tabular-nums text-zinc-500">{Math.round(r.avg_delay_s ?? 0)}s</span>
            </li>
          ))}
          {!allRoutesNow.length && <li className="text-xs text-zinc-400">No routes reporting a delay right now, which is its own kind of good news.</li>}
        </ul>
        <p className="mt-2 text-[10px] leading-snug text-zinc-400">
          Every bar is a real TTC route from this minute&rsquo;s poll. The Reliable bot replays weeks of exactly this data to score each route&rsquo;s odds of getting you there on time.
        </p>
      </details>

      <p className="text-[11px] text-zinc-400">This is what the Reliable bot&rsquo;s on-time % is built from.</p>
    </div>
  );
}

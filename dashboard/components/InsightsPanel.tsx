"use client";
import { useEffect, useState } from "react";
import { API } from "@/lib/api";
import { TONE, TigerStatus, dbLabel } from "@/lib/dataSource";
import { SeriesChart, SERIES_COLORS } from "@/components/charts";

type BikeRow = { bucket: number; avg_bikes: number; avg_ebikes: number };
type TtcRow = { bucket: number; route_id: string; avg_active: number; avg_delay_s: number | null; avg_cancelled: number };
type Resp<T> = { backend: string; tiger?: TigerStatus; sources: Record<string, number>; rows: T[] };

const SERIES = SERIES_COLORS; // fixed order, one colour per route

function Note({ backend, tiger, sources }: { backend: string; tiger?: TigerStatus; sources: Record<string, number> }) {
  const sim = sources.simulated ?? 0;
  const live = (sources.live ?? 0) + (sources.replay ?? 0);
  return (
    <p className="mt-1 text-[11px] text-zinc-500">
      Source: {dbLabel({ backend, tiger }).long} ·{" "}
      {live} live/replay samples{sim ? `, ${sim} SIMULATED backfill samples (labelled, not real)` : ""}
    </p>
  );
}

export default function InsightsPanel() {
  const [bikes, setBikes] = useState<Resp<BikeRow> | null>(null);
  const [ttc, setTtc] = useState<Resp<TtcRow> | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    Promise.all([fetch(`${API}/insights/bikes?hours=24`).then((r) => r.json()), fetch(`${API}/insights/ttc?hours=24&top=4`).then((r) => r.json())])
      .then(([b, t]) => { setBikes(b); setTtc(t); })
      .catch(() => setErr(true));
  }, []);

  const routes = Array.from(new Set((ttc?.rows ?? []).map((r) => r.route_id))).slice(0, 4);
  return (
    <section aria-label="Toronto transit and Bike Share history" className="grid gap-3" data-testid="insights">
      <p data-testid="insights-source" className={`rounded-xl px-3 py-2 text-xs ${TONE[err ? "warn" : dbLabel(bikes ? { backend: bikes.backend, tiger: bikes.tiger } : null).tone]}`}>
        {err ? "Reading from: nothing (backend offline)." : bikes ? `Reading from: ${dbLabel({ backend: bikes.backend, tiger: bikes.tiger }).long}.` : "Checking which database is available…"}
      </p>
      <div className="rounded-2xl p-4 ring-1 ring-zinc-200">
        <h2 className="text-sm font-bold">Bike Share bikes available, last 24 h</h2>
        {err ? <p className="text-sm text-red-700">History unavailable (backend offline).</p> : !bikes ? <p className="text-sm text-zinc-500">Loading…</p> : (
          <>
            <SeriesChart unit="Bikes available" fmt={(v) => Math.round(v).toLocaleString()} series={[{ name: "Bikes", color: SERIES[0], pts: bikes.rows.map((r) => ({ x: r.bucket, y: r.avg_bikes })) }]} />
            <Note backend={bikes.backend} tiger={bikes.tiger} sources={bikes.sources} />
          </>
        )}
      </div>
      <div className="rounded-2xl p-4 ring-1 ring-zinc-200">
        <h2 className="text-sm font-bold">TTC average delay by route (seconds), last 24 h</h2>
        {err ? <p className="text-sm text-red-700">History unavailable (backend offline).</p> : !ttc ? <p className="text-sm text-zinc-500">Loading…</p> : (
          <>
            <SeriesChart unit="Average delay in seconds" fmt={(v) => `${Math.round(v)}s`} series={routes.map((rt, i) => ({
              name: `Route ${rt}`, color: SERIES[i % SERIES.length],
              pts: ttc.rows.filter((r) => r.route_id === rt && r.avg_delay_s !== null).map((r) => ({ x: r.bucket, y: r.avg_delay_s as number })),
            }))} />
            <Note backend={ttc.backend} tiger={ttc.tiger} sources={ttc.sources} />
          </>
        )}
      </div>
    </section>
  );
}

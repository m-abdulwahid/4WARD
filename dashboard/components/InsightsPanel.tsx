"use client";
import { useEffect, useState } from "react";
import { API } from "@/lib/api";
import { TONE, TigerStatus, dbLabel } from "@/lib/dataSource";

type BikeRow = { bucket: number; avg_bikes: number; avg_ebikes: number };
type TtcRow = { bucket: number; route_id: string; avg_active: number; avg_delay_s: number | null; avg_cancelled: number };
type Resp<T> = { backend: string; tiger?: TigerStatus; sources: Record<string, number>; rows: T[] };

const SERIES = ["#2a6fdb", "#d9730d", "#12906a", "#a8379a"]; // fixed order, one colour per route
const INK = "#27272a";
const MUTED = "#71717a";
const GRID = "#e4e4e7";
const W = 560;
const H = 220;
const M = { l: 44, r: 62, t: 12, b: 26 };

function hourLabel(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric" });
}

function Chart({ series, fmt, unit }: { series: { name: string; color: string; pts: { x: number; y: number }[] }[]; fmt: (v: number) => string; unit: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const xs = Array.from(new Set(series.flatMap((s) => s.pts.map((p) => p.x)))).sort((a, b) => a - b);
  if (!xs.length) return <p className="text-sm text-zinc-500">No data yet.</p>;
  const ys = series.flatMap((s) => s.pts.map((p) => p.y));
  const y0 = Math.min(...ys) * 0.9;
  const y1 = Math.max(...ys) * 1.05 || 1;
  const sx = (x: number) => M.l + ((x - xs[0]) / Math.max(1, xs[xs.length - 1] - xs[0])) * (W - M.l - M.r);
  const sy = (y: number) => H - M.b - ((y - y0) / (y1 - y0 || 1)) * (H - M.t - M.b);
  const ticks = [0, 1, 2, 3].map((i) => y0 + ((y1 - y0) * i) / 3);
  const hx = hover === null ? null : xs[hover];
  // direct labels at line ends, nudged apart so they never overlap
  const labelY: Record<string, number> = {};
  let prev = -1e9;
  series
    .filter((s) => s.pts.length)
    .map((s) => ({ name: s.name, y: sy(s.pts[s.pts.length - 1].y) }))
    .sort((a, b) => a.y - b.y)
    .forEach((l) => {
      prev = Math.max(l.y, prev + 12);
      labelY[l.name] = prev;
    });
  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[300px]" role="img" aria-label={`${unit} by hour`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          let best = 0;
          xs.forEach((x, i) => { if (Math.abs(sx(x) - px) < Math.abs(sx(xs[best]) - px)) best = i; });
          setHover(best);
        }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} stroke={GRID} strokeWidth={1} />
            <text x={M.l - 6} y={sy(t) + 4} textAnchor="end" fontSize={10} fill={MUTED}>{fmt(t)}</text>
          </g>
        ))}
        {xs.filter((_, i) => i % Math.ceil(xs.length / 6) === 0).map((x) => (
          <text key={x} x={sx(x)} y={H - 8} textAnchor="middle" fontSize={10} fill={MUTED}>{hourLabel(x)}</text>
        ))}
        {series.map((s) => (
          <g key={s.name}>
            <polyline fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" points={s.pts.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ")} />
            {s.pts.length > 0 && (
              <text x={sx(s.pts[s.pts.length - 1].x) + 6} y={(labelY[s.name] ?? sy(s.pts[s.pts.length - 1].y)) + 4} fontSize={10} fill={INK}>{s.name}</text>
            )}
          </g>
        ))}
        {hx !== null && (
          <g>
            <line x1={sx(hx)} x2={sx(hx)} y1={M.t} y2={H - M.b} stroke={MUTED} strokeDasharray="3 3" />
            {series.map((s) => {
              const p = s.pts.find((q) => q.x === hx);
              return p ? <circle key={s.name} cx={sx(hx)} cy={sy(p.y)} r={4} fill={s.color} stroke="#fff" strokeWidth={2} /> : null;
            })}
          </g>
        )}
      </svg>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-700" aria-live="polite">
        {series.length > 1 && series.map((s) => (
          <span key={s.name} className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-3 rounded-sm" style={{ background: s.color }} />
            {s.name}
          </span>
        ))}
        {hx !== null && (
          <span className="ml-auto tabular-nums">
            {hourLabel(hx)}: {series.map((s) => { const p = s.pts.find((q) => q.x === hx); return p ? `${s.name} ${fmt(p.y)}` : null; }).filter(Boolean).join(" · ")}
          </span>
        )}
      </div>
    </div>
  );
}

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
            <Chart unit="Bikes available" fmt={(v) => Math.round(v).toLocaleString()} series={[{ name: "Bikes", color: SERIES[0], pts: bikes.rows.map((r) => ({ x: r.bucket, y: r.avg_bikes })) }]} />
            <Note backend={bikes.backend} tiger={bikes.tiger} sources={bikes.sources} />
          </>
        )}
      </div>
      <div className="rounded-2xl p-4 ring-1 ring-zinc-200">
        <h2 className="text-sm font-bold">TTC average delay by route (seconds), last 24 h</h2>
        {err ? <p className="text-sm text-red-700">History unavailable (backend offline).</p> : !ttc ? <p className="text-sm text-zinc-500">Loading…</p> : (
          <>
            <Chart unit="Average delay in seconds" fmt={(v) => `${Math.round(v)}s`} series={routes.map((rt, i) => ({
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

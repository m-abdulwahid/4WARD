"use client";
import { useState } from "react";

// Shared small-chart primitives: the multi-series chart and timestamp helpers used by the Reliable
// bot's Evidence section and the Insights tab.

export const INK = "#27272a";
export const MUTED = "#71717a";
export const GRID = "#e4e4e7";

export function ago(ts: number | null): string {
  if (!ts) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function fmtTs(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

const SERIES_W = 560;
const SERIES_H = 220;
const SERIES_M = { l: 44, r: 62, t: 12, b: 26 };
export const SERIES_COLORS = ["#2a6fdb", "#d9730d", "#12906a", "#a8379a"];

function hourLabel(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric" });
}

/** Multi-series time chart with hover readout. Used for the bikes / TTC-delay-by-route history charts. */
export function SeriesChart({ series, fmt, unit }: { series: { name: string; color: string; pts: { x: number; y: number }[] }[]; fmt: (v: number) => string; unit: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = SERIES_W, H = SERIES_H, M = SERIES_M;
  const xs = Array.from(new Set(series.flatMap((s) => s.pts.map((p) => p.x)))).sort((a, b) => a - b);
  if (!xs.length) return <p className="text-sm text-zinc-500">No data yet.</p>;
  const ys = series.flatMap((s) => s.pts.map((p) => p.y));
  const y0 = Math.min(...ys) * 0.9;
  const y1 = Math.max(...ys) * 1.05 || 1;
  const sx = (x: number) => M.l + ((x - xs[0]) / Math.max(1, xs[xs.length - 1] - xs[0])) * (W - M.l - M.r);
  const sy = (y: number) => H - M.b - ((y - y0) / (y1 - y0 || 1)) * (H - M.t - M.b);
  const ticks = [0, 1, 2, 3].map((i) => y0 + ((y1 - y0) * i) / 3);
  const hx = hover === null ? null : xs[hover];
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

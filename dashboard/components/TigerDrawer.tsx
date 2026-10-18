"use client";
import { useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";
import { ago } from "@/components/charts";
import { TONE, dbLabel } from "@/lib/dataSource";
import LivePanel from "@/components/LivePanel";
import InsightsPanel from "@/components/InsightsPanel";

type LiveFooter = { collected_since: number | null; poll_count: number; last_update_ts: number | null; query_ms: number };
type LiveResp = { backend: string; query_ms: number; footer: LiveFooter };

/** Real, read-only SQL from backend/db.py, shown verbatim so "the SQL behind now vs normal" is the actual
 * query, not a paraphrase. Nothing here is executed by the dashboard. */
const SQL = [
  {
    title: "Now: latest live poll for a route",
    fn: "Store.latest_route_delay",
    sql: `SELECT extract(epoch from ts)::bigint, avg_delay_s
FROM ttc_route_snapshot
WHERE route_id = %s AND source = 'live' AND avg_delay_s IS NOT NULL
ORDER BY ts DESC LIMIT 1`,
  },
  {
    title: "Normal: the same day-of-week and hour, replayed over past weeks",
    fn: "Store.ttc_daily_at_dow_hour",
    sql: `SELECT (date_trunc('day', ts AT TIME ZONE 'America/Toronto'))::date::text, avg(avg_delay_s)
FROM ttc_route_snapshot
WHERE route_id = %s
  AND extract(dow  FROM ts AT TIME ZONE 'America/Toronto') = %s
  AND extract(hour FROM ts AT TIME ZONE 'America/Toronto') = %s
  AND ts >= to_timestamp(%s) AND avg_delay_s IS NOT NULL
GROUP BY 1`,
  },
  {
    title: "Hourly rollup used by the charts (continuous aggregate)",
    fn: "Store.ttc_hourly",
    sql: `SELECT extract(epoch from bucket)::bigint, route_id, avg_active, avg_cancelled, avg_delay_s, max_alerts
FROM ttc_route_hourly
WHERE bucket >= to_timestamp(%s)
  AND route_id IN (
    SELECT route_id FROM ttc_route_hourly WHERE bucket >= to_timestamp(%s)
    GROUP BY route_id ORDER BY sum(avg_active) DESC LIMIT %s
  )
ORDER BY bucket`,
  },
] as const;

function SqlBlock({ item }: { item: (typeof SQL)[number] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg ring-1 ring-zinc-200">
      <button
        type="button"
        data-testid={`tiger-sql-${item.fn}`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-zinc-800 focus-visible:outline-2 focus-visible:outline-zinc-900"
      >
        <span>
          {item.title} <span className="font-mono text-[10px] text-zinc-400">{item.fn}</span>
        </span>
        <span aria-hidden className={`shrink-0 transition-transform duration-150 ${open ? "rotate-180" : ""}`}>▾</span>
      </button>
      {open && (
        <pre className="overflow-x-auto rounded-b-lg bg-zinc-950 px-3 py-2 text-[11px] leading-relaxed text-emerald-200">
          <code>{item.sql}</code>
        </pre>
      )}
    </div>
  );
}

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-xs">
      <span className="text-zinc-500">{label}</span>
      <span className="text-right font-semibold tabular-nums text-zinc-900" title={note}>{value}</span>
    </div>
  );
}

function NotAvailable({ label }: { label: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-xs">
      <span className="text-zinc-500">{label}</span>
      <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-semibold text-zinc-500">Not available yet</span>
    </div>
  );
}

/** Header chip: live rows + last update. Opens a side drawer with per-hypertable row counts, query time,
 * and the real SQL behind the reliability score's "now vs normal" comparison. Chunk count, compression
 * ratio and aggregate refresh lag are not exposed by any endpoint yet, so they are labelled as such rather
 * than guessed. */
export default function TigerDataControl() {
  const [open, setOpen] = useState(false);
  const [live, setLive] = useState<LiveResp | null>(null);
  const [liveErr, setLiveErr] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);

  const loadChip = () =>
    fetch(`${API}/live`).then((r) => r.json()).then(setLive).catch(() => setLiveErr(true));

  useEffect(() => {
    loadChip();
    const id = setInterval(loadChip, 30_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const f = live?.footer;

  return (
    <>
      <button
        type="button"
        data-testid="tiger-chip"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        className="flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-xs font-semibold text-zinc-800 shadow ring-1 ring-black/5 hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-zinc-900"
      >
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${liveErr ? "bg-zinc-300" : "bg-emerald-500"}`} />
        Tiger Data
        <span className="font-normal text-zinc-500">
          {liveErr ? "offline" : f ? `${f.poll_count.toLocaleString()} rows · ${ago(f.last_update_ts)}` : "…"}
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Tiger Data details"
          data-testid="tiger-drawer"
          className="fixed inset-0 z-50 flex justify-end bg-zinc-950/40"
          onClick={() => setOpen(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="flex h-full w-full max-w-sm flex-col overflow-y-auto bg-white shadow-2xl"
          >
            <header className="flex shrink-0 items-center justify-between gap-2 border-b border-zinc-100 px-4 py-3">
              <h2 className="text-sm font-bold">Tiger Data</h2>
              <button
                ref={closeRef}
                type="button"
                data-testid="tiger-drawer-close"
                onClick={() => setOpen(false)}
                className="rounded-full bg-zinc-100 px-3 py-1 text-xs font-semibold text-zinc-800 hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-zinc-900"
              >
                Close
              </button>
            </header>
            <div className="flex flex-col gap-4 px-4 py-3">
              <p className={`rounded-lg px-2 py-1.5 text-[11px] ${TONE[liveErr ? "warn" : dbLabel(live ? { backend: live.backend } : null).tone]}`}>
                {liveErr ? "Backend offline." : live ? dbLabel({ backend: live.backend }).long : "Checking…"}
              </p>

              <section>
                <h3 className="mb-1 text-[11px] font-semibold tracking-wide text-zinc-500 uppercase">Query time</h3>
                <Row label="Backend-reported (live poll)" value={live ? `${live.query_ms} ms` : "…"} />
                <Row label="Backend-reported (footer)" value={f ? `${f.query_ms} ms` : "…"} />
              </section>

              <details className="group rounded-lg ring-1 ring-zinc-200">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-2.5 py-1.5 text-xs font-semibold text-zinc-700 focus-visible:outline-2 focus-visible:outline-zinc-900">
                  Live feeds (per-route delay, sparkline, hourly history)
                  <span aria-hidden className="text-zinc-400 transition-transform duration-150 group-open:rotate-180">▾</span>
                </summary>
                <div className="px-1 pb-1">
                  <LivePanel />
                </div>
              </details>

              <details className="group rounded-lg ring-1 ring-zinc-200">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-2.5 py-1.5 text-xs font-semibold text-zinc-700 focus-visible:outline-2 focus-visible:outline-zinc-900">
                  Bike Share and TTC history charts
                  <span aria-hidden className="text-zinc-400 transition-transform duration-150 group-open:rotate-180">▾</span>
                </summary>
                <div className="px-1 pb-1">
                  <InsightsPanel />
                </div>
              </details>

              <section>
                <h3 className="mb-1 text-[11px] font-semibold tracking-wide text-zinc-500 uppercase">TimescaleDB internals</h3>
                <NotAvailable label="Chunk count" />
                <NotAvailable label="Compression ratio" />
                <NotAvailable label="Continuous aggregate refresh lag" />
                <p className="mt-1 text-[10px] leading-snug text-zinc-500">
                  No endpoint reads timescaledb_information yet, so these are left blank instead of guessed.
                </p>
              </section>

              <section>
                <h3 className="mb-1 text-[11px] font-semibold tracking-wide text-zinc-500 uppercase">The SQL behind &ldquo;now vs normal&rdquo;</h3>
                <div className="flex flex-col gap-1.5">
                  {SQL.map((item) => (
                    <SqlBlock key={item.fn} item={item} />
                  ))}
                </div>
                <p className="mt-1 text-[10px] leading-snug text-zinc-500">Copied from backend/db.py, read-only reference. Not executed by the dashboard.</p>
              </section>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

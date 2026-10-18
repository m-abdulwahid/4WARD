"use client";
import type { BotKey, BotState } from "@/lib/bots";
import { STATUS_CHIP } from "@/lib/bots";
import type { Candidate } from "@/lib/api";
import { legColor, legMode, MODES } from "@/lib/modes";
import { clock } from "@/lib/routeFit";
import ModeBadge from "@/components/ModeBadge";
import LegTimeline from "@/components/LegTimeline";

type Bot = { key: BotKey; name: string; color: string; blurb: string };

function Metric({ label, value, tag }: { label: string; value: string; tag?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-zinc-500">{label}</dt>
      <dd className="flex flex-wrap items-baseline gap-x-1 text-sm font-semibold tabular-nums text-zinc-900">
        {value}
        {tag && <span className="rounded bg-zinc-100 px-1 text-[10px] font-medium text-zinc-600">{tag}</span>}
      </dd>
    </div>
  );
}

export function StatusChip({ botKey, state }: { botKey: BotKey; state: BotState }) {
  const s = STATUS_CHIP[state.status];
  return (
    <span data-testid={`bot-${botKey}-status`} className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${s.cls}`}>
      {s.label}
    </span>
  );
}

/** Short route type: "TTC subway (less walking)" -> "TTC subway". */
const routeType = (r: Candidate) => r.title.replace(/\s*\(.*?\)\s*/g, "").trim();
/** The price includes an engine estimate (rideshare fare, GO fare, parking, lot fee): the engine says so in its notes.
 * Notes about estimated timing (e.g. "Bus leg is an ESTIMATE") do not count. */
const estimatedFare = (r: Candidate) => (r.notes ?? []).some((n) => /\b(fare|parking|fee|price|cost)\b/i.test(n) && /\bestimat/i.test(n));

/** A compact block: title row, stats row, button. The leg timeline opens when the card is selected. */
export default function RouteCard({
  bot,
  state,
  running,
  arrival = false,
  focused,
  chosen,
  onFocus,
  onChoose,
}: {
  bot: Bot;
  state: BotState;
  running: boolean;
  arrival?: boolean; // show the arrival time (only when no route makes the deadline)
  focused: boolean;
  chosen: boolean;
  onFocus: () => void;
  onChoose: () => void;
}) {
  const r = state.route;
  return (
    <article
      data-testid={`bot-${bot.key}`}
      className={`shrink-0 overflow-hidden rounded-2xl bg-white ring-1 transition-shadow duration-150 ${focused ? "shadow-md ring-2" : "ring-zinc-200"}`}
      style={focused ? ({ "--tw-ring-color": bot.color } as React.CSSProperties) : undefined}
    >
      <button
        type="button"
        data-testid={`route-card-toggle-${bot.key}`}
        aria-expanded={focused}
        onClick={onFocus}
        disabled={!r}
        className="block w-full px-4 pt-3 pb-2 text-left focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-zinc-900 disabled:cursor-default"
      >
        {/* title row */}
        <div className="flex items-center gap-2">
          <span aria-hidden className="h-3 w-3 shrink-0 rounded-full" style={{ background: bot.color }} />
          <h3 className="shrink-0 text-sm font-semibold text-zinc-900">{bot.name}</h3>
          {r && <span className="min-w-0 truncate text-xs text-zinc-500">{routeType(r)}</span>}
          <span className="ml-auto flex shrink-0 items-center gap-1">
            {r?.reliability && (
              <span
                data-testid={`reliability-${bot.key}`}
                title={`${r.reliability.history_days} matching day(s) of TTC delay history (${r.reliability.basis})`}
                className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-800 ring-1 ring-emerald-200"
              >
                {r.reliability.reliability_pct}% on time
              </span>
            )}
            {r?.pareto_optimal && (
              <span
                data-testid={`pareto-${bot.key}`}
                title="No other computed route beats this one on time, cost, CO2, transfers and walking all at once."
                className="rounded-full bg-zinc-900 px-2 py-0.5 text-[11px] font-semibold text-white"
              >
                Pareto-optimal
              </span>
            )}
            <StatusChip botKey={bot.key} state={state} />
          </span>
        </div>
        {r && r.legs.length > 0 && (
          <p className="mt-1.5 flex items-center gap-0.5 pl-5" data-testid={`modes-${bot.key}`}>
            <span className="sr-only">Modes: {r.legs.map((l) => MODES[legMode(l, r.id)].label).join(", ")}</span>
            {r.legs.map((l, i) => {
              const mode = legMode(l, r.id);
              return (
                <span key={i} className="flex items-center gap-0.5">
                  {i > 0 && <span aria-hidden className="text-[10px] text-zinc-400">›</span>}
                  <ModeBadge mode={mode} color={legColor(l, mode)} size={18} />
                </span>
              );
            })}
            {arrival && r.arrive && (
              <span data-testid={`arrival-${bot.key}`} className="ml-auto text-xs font-semibold text-amber-900">
                Arrives {clock(r.arrive)}
              </span>
            )}
          </p>
        )}
        {/* stats row */}
        {r ? (
          <dl className="mt-2.5 grid grid-cols-4 gap-2">
            <Metric label="Time" value={`${Math.round(r.duration_min ?? 0)} min`} />
            <Metric label="Cost" value={`$${(r.cost ?? 0).toFixed(2)}`} tag={estimatedFare(r) ? "Estimated" : undefined} />
            <Metric label="CO₂" value={`${(r.co2_kg ?? 0).toFixed(2)} kg`} />
            <Metric label="Transfers" value={String(r.transfers ?? 0)} />
          </dl>
        ) : state.status === "FAILED" ? (
          <p className="mt-1.5 text-xs text-red-700">No route</p>
        ) : (
          <p className="mt-1.5 truncate text-xs text-zinc-500">{state.lastReason ?? (running ? "Starting…" : "Waiting for a trip")}</p>
        )}
      </button>

      {r && (
        <div className="px-4 pb-3">
          {focused && (
            <div className="mb-3 border-t border-zinc-100 pt-3">
              <LegTimeline route={r} testid={chosen ? "itinerary" : `itinerary-${bot.key}`} />
            </div>
          )}
          <button
            type="button"
            data-testid={`choose-${bot.key}`}
            onClick={onChoose}
            aria-pressed={chosen}
            className="w-full rounded-full px-3 py-2 text-sm font-semibold transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900"
            style={chosen ? { background: bot.color, color: "#fff" } : { background: "#f4f4f5", color: "#18181b" }}
          >
            {chosen ? "Chosen ✓" : "Choose this route"}
          </button>
        </div>
      )}
    </article>
  );
}

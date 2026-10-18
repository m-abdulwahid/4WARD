"use client";
import type { BotKey, BotState } from "@/lib/bots";
import { STATUS_CHIP } from "@/lib/bots";
import { MODES, legColor, legMode } from "@/lib/modes";
import ModeBadge, { ModeLine } from "@/components/ModeBadge";

type Bot = { key: BotKey; name: string; color: string; blurb: string };

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] text-zinc-500">{label}</dt>
      <dd className="text-sm font-semibold tabular-nums text-zinc-900">{value}</dd>
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

export default function RouteCard({
  bot,
  state,
  running,
  budget = null,
  focused,
  chosen,
  onFocus,
  onChoose,
}: {
  bot: Bot;
  state: BotState;
  running: boolean;
  budget?: number | null; // from the user's prompt; compared against the engine's cost
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
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-start gap-2">
            <span aria-hidden className="mt-1 h-3 w-3 shrink-0 rounded-full" style={{ background: bot.color }} />
            <div className="min-w-0">
              <h3 className="truncate text-sm font-semibold text-zinc-900">{bot.name}</h3>
              <p className="truncate text-xs text-zinc-500">{r ? r.title : bot.blurb}</p>
              {r && r.legs.length > 0 && (
                <p className="mt-1 flex items-center gap-0.5" data-testid={`modes-${bot.key}`}>
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
                </p>
              )}
            </div>
          </div>
          <StatusChip botKey={bot.key} state={state} />
        </div>
        {r ? (
          <dl className="mt-3 grid grid-cols-4 gap-2">
            <Metric label="Time" value={`${Math.round(r.duration_min ?? 0)} min`} />
            <Metric label="Cost" value={`$${(r.cost ?? 0).toFixed(2)}`} />
            <Metric label="CO₂" value={`${(r.co2_kg ?? 0).toFixed(2)} kg`} />
            <Metric label="Transfers" value={String(r.transfers ?? 0)} />
          </dl>
        ) : null}
        {r && budget !== null && (r.cost ?? 0) > budget && (
          <p data-testid={`over-budget-${bot.key}`} className="mt-2 inline-block rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-semibold text-red-800 ring-1 ring-red-200">
            Over your ${budget.toFixed(2)} budget
          </p>
        )}
        {r ? null : state.status === "FAILED" ? (
          <p className="mt-2 text-xs text-red-700">Failed: {state.error ?? "unknown error"}. The other bots are unaffected.</p>
        ) : (
          <p className="mt-2 line-clamp-2 text-xs text-zinc-500">{state.lastReason ?? (running ? "Starting…" : "Waiting for a trip")}</p>
        )}
      </button>

      {r && (
        <div className="px-4 pb-3">
          {focused && (
            <div className="mb-3 border-t border-zinc-100 pt-3">
              <p className="text-xs text-zinc-600">
                {r.depart} to {r.arrive} · walk {r.walk_m} m
                {r.meets_deadline !== undefined && (r.meets_deadline ? " · makes your deadline" : ` · misses your deadline (leave by ${r.leave_by})`)}
              </p>
              <ol className="mt-2 flex flex-col text-sm" data-testid={chosen ? "itinerary" : `itinerary-${bot.key}`}>
                {r.legs.map((l, i) => {
                  const mode = legMode(l, r.id);
                  const color = legColor(l, mode);
                  return (
                    <li key={i} className="flex gap-2">
                      <span className="w-14 shrink-0 pt-0.5 text-right text-xs whitespace-nowrap tabular-nums leading-5 text-zinc-500">{Math.round(l.duration_s / 60)} min</span>
                      {/* timeline: mode icon, then a stretch of line drawn like the map draws this mode */}
                      <span className="flex w-6 shrink-0 flex-col items-center">
                        <ModeBadge mode={mode} color={color} />
                        {i < r.legs.length - 1 && <span className="flex min-h-3 flex-1 justify-center overflow-hidden"><ModeLine mode={mode} color={color} vertical length={40} /></span>}
                      </span>
                      <span className="min-w-0 pb-1 leading-5 text-zinc-800">
                        <span className="block text-[11px] font-semibold text-zinc-500">{MODES[mode].label}</span>
                        {l.label}
                        {l.cost ? <span className="text-zinc-500"> · ${l.cost.toFixed(2)}</span> : null}
                        {l.provenance === "estimated" && <span className="ml-1 rounded bg-zinc-100 px-1 text-[10px] text-zinc-600">estimated</span>}
                      </span>
                    </li>
                  );
                })}
              </ol>
              {r.notes?.length ? (
                <ul className="mt-2 flex flex-col gap-0.5 text-xs text-zinc-500">
                  {r.notes.map((n) => (
                    <li key={n}>• {n}</li>
                  ))}
                </ul>
              ) : null}
              <p className="mt-2 text-xs text-zinc-500">
                {r.reliability
                  ? `Reliability: made it on ${r.reliability.reliability_pct}% of the last ${r.reliability.history_days} matching day(s) (${r.reliability.basis}, from TTC delay history).`
                  : "Reliability: not enough TTC delay history yet for this route and time."}
              </p>
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

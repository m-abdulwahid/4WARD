"use client";
import type { BotKey, BotState } from "@/lib/bots";
import { STATUS_CHIP } from "@/lib/bots";
import type { Candidate } from "@/lib/api";
import { legColor, legMode, MODES } from "@/lib/modes";
import { clock } from "@/lib/routeFit";
import ModeBadge from "@/components/ModeBadge";
import LegTimeline from "@/components/LegTimeline";
import { timingLine } from "@/lib/timing";

type Bot = { key: BotKey; name: string; color: string; blurb: string };

function Metric({ label, value, tag, tagTitle }: { label: string; value: string; tag?: string; tagTitle?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-zinc-500">{label}</dt>
      <dd className="flex flex-wrap items-baseline gap-x-1 text-sm font-semibold tabular-nums text-zinc-900">
        {value}
        {tag && (
          <span title={tagTitle} className="rounded bg-zinc-100 px-1 text-[10px] font-medium text-zinc-600">
            {tag}
          </span>
        )}
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

/** The price includes an engine estimate (rideshare fare, GO fare, parking, lot fee): the engine says so in its notes.
 * Notes about estimated timing (e.g. "Bus leg is an ESTIMATE") do not count. */
const estimatedFare = (r: Candidate) => (r.notes ?? []).some((n) => /\b(fare|parking|fee|price|cost)\b/i.test(n) && /\bestimat/i.test(n));

/** The travel time includes an estimate (typical-day traffic, a leg without stop-level schedules): the engine's notes,
 * shown only as the tag's tooltip. */
const timeEstimates = (r: Candidate) =>
  (r.notes ?? []).filter((n) => /traffic\s+factor|typical-day|\bleg is an estimat/i.test(n));

/** A compact block: title row, stats row, button. The leg timeline opens when the card is selected. */
export default function RouteCard({
  bot,
  state,
  tags,
  running,
  budget = null,
  deadlineNote,
  arrival = false,
  focused,
  chosen,
  onFocus,
  onChoose,
  onUseAlternative,
}: {
  bot: Bot; // the lead bot of this card (first of the bots that picked this route)
  state: BotState;
  tags?: { bot: Bot; state: BotState }[]; // every bot whose pick is this route, lead first
  running: boolean;
  budget?: number | null;
  deadlineNote?: string | null;
  arrival?: boolean; // show the arrival time (only when no route makes the deadline)
  focused: boolean;
  chosen: boolean;
  onFocus: () => void;
  onChoose: () => void;
  onUseAlternative?: (botKey: BotKey, alt: Candidate) => void;
}) {
  const r = state.route;
  const tagList = tags ?? [{ bot, state }];
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
              <h3 className="text-sm font-semibold text-zinc-900" data-testid={`tags-${bot.key}`}>
                {r ? tagList.map((t) => t.bot.name).join(" · ") : bot.name}
              </h3>
              <p className="truncate text-xs text-zinc-500">{r ? r.title : bot.blurb}</p>
              {!focused && r && state.alternatives && state.alternatives.length > 1 && (
                <p className="mt-1 text-[11px] text-zinc-400">Compared {state.alternatives.length} options for this goal · tap to see them</p>
              )}
            </div>
          </div>
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
            <Metric
              label="Time"
              value={`${Math.round(r.duration_min ?? 0)} min`}
              tag={timeEstimates(r).length ? "Estimated" : undefined}
              tagTitle={timeEstimates(r).join(". ") || undefined}
            />
            <Metric
              label="Cost"
              value={`$${(r.cost ?? 0).toFixed(2)}`}
              tag={estimatedFare(r) ? "Estimated" : undefined}
              tagTitle={(r.notes ?? []).filter((n) => /\b(fare|parking|fee|price|cost)\b/i.test(n) && /\bestimat/i.test(n)).join(". ") || undefined}
            />
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
          <p className="mt-1.5 truncate text-xs text-zinc-500">{state.lastReason ?? (running ? "Starting…" : "Waiting for a trip")}</p>
        )}
      </button>

      {r && (
        <div className="px-4 pb-3">
          {focused && (
            <div className="mb-3 border-t border-zinc-100 pt-3">
              <p className="text-xs text-zinc-600">
                {timingLine(r, deadlineNote)} · walk {r.walk_m} m · Toronto time
              </p>
              <div className="mt-2">
                <LegTimeline route={r} testid={chosen ? "itinerary" : `itinerary-${bot.key}`} />
              </div>
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
              {onUseAlternative && tagList.some((t) => t.state.nextBest) && (
                <ul className="mt-3 flex flex-col gap-1 border-t border-zinc-100 pt-2" data-testid={`next-best-${bot.key}`}>
                  {tagList.map((t) =>
                    t.state.nextBest ? (
                      <li key={t.bot.key} className="flex items-center justify-between gap-2 text-xs text-zinc-600">
                        <span className="min-w-0">
                          <span className="font-semibold text-zinc-800">Next best for {t.bot.name}:</span> {t.state.nextBest.title}, {Math.round(t.state.nextBest.duration_min ?? 0)} min, ${(t.state.nextBest.cost ?? 0).toFixed(2)}, {(t.state.nextBest.co2_kg ?? 0).toFixed(2)} kg
                        </span>
                        <button
                          type="button"
                          data-testid={`use-next-${t.bot.key}`}
                          onClick={() => onUseAlternative(t.bot.key, t.state.nextBest as Candidate)}
                          className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-semibold text-zinc-800 hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-zinc-900"
                        >
                          Use this
                        </button>
                      </li>
                    ) : null,
                  )}
                </ul>
              )}
              {onUseAlternative && state.alternatives && state.alternatives.length > 1 && (
                <div className="mt-3 border-t border-zinc-100 pt-2">
                  <p className="text-[11px] font-semibold text-zinc-500">{bot.name}'s other options, ranked the same way it picked this one</p>
                  <table className="mt-1 w-full text-xs" data-testid={`alternatives-${bot.key}`}>
                    <thead>
                      <tr className="text-left text-[10px] text-zinc-400">
                        <th className="w-16 font-normal">Time</th>
                        <th className="w-14 font-normal">Cost</th>
                        <th className="w-16 font-normal">CO₂</th>
                        <th className="font-normal">Route</th>
                        <th className="w-16 font-normal"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {state.alternatives.map((alt) => {
                        const isCurrent = alt.id === r.id;
                        return (
                          <tr key={alt.id} className={isCurrent ? "bg-zinc-50" : undefined}>
                            <td className="py-1 tabular-nums">{Math.round(alt.duration_min ?? 0)} min</td>
                            <td className="tabular-nums">${(alt.cost ?? 0).toFixed(2)}</td>
                            <td className="tabular-nums">{(alt.co2_kg ?? 0).toFixed(2)} kg</td>
                            <td className="truncate pr-2 text-zinc-600">{alt.title}</td>
                            <td>
                              {isCurrent ? (
                                <span className="text-[10px] font-semibold text-zinc-500">current</span>
                              ) : (
                                <button
                                  type="button"
                                  data-testid={`use-alt-${bot.key}-${alt.id}`}
                                  onClick={() => onUseAlternative(bot.key, alt)}
                                  className="rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-semibold text-zinc-800 hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-zinc-900"
                                >
                                  Use this
                                </button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
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

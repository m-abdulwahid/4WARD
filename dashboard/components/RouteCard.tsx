"use client";
import { useEffect, useState } from "react";
import type { BotKey, BotState } from "@/lib/bots";
import { STATUS_CHIP } from "@/lib/bots";
import type { Candidate } from "@/lib/api";
import { API } from "@/lib/api";
import { legColor, legMode, MODES } from "@/lib/modes";
import { clock } from "@/lib/routeFit";
import ModeBadge from "@/components/ModeBadge";
import LegTimeline from "@/components/LegTimeline";
import { timingLine } from "@/lib/timing";
import { HistoryBars, ago, fmtTs } from "@/components/charts";

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

/** One line explaining, in this bot's own metric, why an alternative ranks below the chosen route. */
function whyBelow(bot: BotKey, chosen: Candidate, alt: Candidate): string {
  if (alt.available === false) return alt.reason ? `Not available: ${alt.reason}` : "Not available";
  if (alt.violates?.length) return `Breaks a constraint: ${alt.violates.join(", ")}`;
  if (bot === "fastest") return `+${Math.round((alt.duration_min ?? 0) - (chosen.duration_min ?? 0))} min slower`;
  if (bot === "cheap") return `+$${((alt.cost ?? 0) - (chosen.cost ?? 0)).toFixed(2)} more`;
  if (bot === "green") return `+${((alt.co2_kg ?? 0) - (chosen.co2_kg ?? 0)).toFixed(2)} kg more CO₂`;
  if (bot === "reliable") {
    const d = (chosen.reliability?.reliability_pct ?? 0) - (alt.reliability?.reliability_pct ?? 0);
    return d > 0 ? `${d} pts less likely to be on time` : "Lower on-time estimate";
  }
  return "Ranks lower for this goal";
}

/** Top 3 candidates this bot actually compared, with plain-words reasons the rest ranked below #1. */
function WhyThisRoute({ bot, state }: { bot: BotKey; state: BotState }) {
  const r = state.route;
  const alts = state.alternatives ?? [];
  if (!r) return null;
  const top3 = [r, ...alts.filter((a) => a.id !== r.id)].slice(0, 3);
  return (
    <details className="group mt-2 rounded-lg ring-1 ring-zinc-200" data-testid={`why-this-route-${bot}`}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-2.5 py-1.5 text-xs font-semibold text-zinc-700 focus-visible:outline-2 focus-visible:outline-zinc-900">
        Why this route
        <span aria-hidden className="text-zinc-400 transition-transform duration-150 group-open:rotate-180">▾</span>
      </summary>
      <ol className="flex flex-col gap-1.5 px-2.5 pb-2.5 text-xs">
        {top3.map((c, i) => (
          <li key={c.id} className={`rounded-md px-2 py-1.5 ${i === 0 ? "bg-emerald-50" : "bg-zinc-50"}`}>
            <p className="flex items-center justify-between gap-2 font-semibold text-zinc-800">
              <span className="truncate">
                {i === 0 ? "Chosen · " : `#${i + 1} · `}
                {c.title}
              </span>
            </p>
            <p className="mt-0.5 tabular-nums text-zinc-600">
              {Math.round(c.duration_min ?? 0)} min · ${(c.cost ?? 0).toFixed(2)} · {(c.co2_kg ?? 0).toFixed(2)} kg
              {c.reliability ? ` · ${c.reliability.reliability_pct}% on time` : ""}
            </p>
            {i > 0 && <p className="mt-0.5 text-zinc-500">{whyBelow(bot, r, c)}</p>}
          </li>
        ))}
        {alts.length > 3 && <li className="text-[11px] text-zinc-400">{alts.length - 3} more option(s) considered, ranked further below.</li>}
      </ol>
    </details>
  );
}

type LiveLite = { footer: { last_update_ts: number | null }; history: { bucket: number; avg_delay_s: number | null }[]; latest: { routes: { route_id: string; avg_delay_s: number | null; alerts: number; ts: number }[] } };

/** Reliable-only evidence: the numbers behind the on-time estimate, plus system-wide context from the live
 * TTC feed. The engine does not (yet) tag which GTFS route_id a leg's label corresponds to, so this is shown
 * as system-wide context rather than claimed to be this exact leg's live delay. */
function Evidence({ route, state, computedAt }: { route: Candidate; state: BotState; computedAt?: number }) {
  const [live, setLive] = useState<LiveLite | null>(null);
  useEffect(() => {
    fetch(`${API}/live`).then((r) => r.json()).then(setLive).catch(() => {});
  }, []);
  const rel = route.reliability;
  const topDelayed = (live?.latest.routes ?? [])
    .filter((r) => r.avg_delay_s != null)
    .sort((a, b) => (b.avg_delay_s ?? 0) - (a.avg_delay_s ?? 0))
    .slice(0, 3);
  const estimatedLegs = route.legs.filter((l) => /estimat/i.test(l.provenance ?? ""));

  return (
    <details className="group mt-2 rounded-lg ring-1 ring-violet-200 bg-violet-50/40" data-testid="reliable-evidence">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-2.5 py-1.5 text-xs font-semibold text-violet-900 focus-visible:outline-2 focus-visible:outline-zinc-900">
        Evidence
        <span aria-hidden className="text-violet-400 transition-transform duration-150 group-open:rotate-180">▾</span>
      </summary>
      <div className="flex flex-col gap-3 px-2.5 pb-2.5 text-xs">
        {rel ? (
          <p className="text-zinc-700">
            Made it on time on <b className="tabular-nums">{rel.reliability_pct}%</b> of the last <b>{rel.history_days}</b> matching day(s)
            {rel.history_days < 14 && <span className="text-amber-800"> (confidence still building, caps at 14 days)</span>}. Basis: {rel.basis}, from TTC delay history.
          </p>
        ) : (
          <p className="text-zinc-500">Not enough TTC delay history yet for this route and time.</p>
        )}

        <div>
          <p className="mb-1 font-semibold text-zinc-700">System-wide TTC delay right now vs. typical for this hour</p>
          {live ? (
            <>
              <HistoryBars rows={live.history} label="typical avg delay / hour, all tracked routes" />
              <p className="mt-1 text-[10px] leading-snug text-zinc-500">
                Not specific to this route&rsquo;s exact leg (Tiger Data does not map GTFS legs to a live route id yet). Source: Tiger Data live polls, as of {ago(live.footer.last_update_ts)}.
              </p>
            </>
          ) : (
            <p className="text-zinc-500">LIVE data loading…</p>
          )}
        </div>

        <div>
          <p className="mb-1 font-semibold text-zinc-700">What could delay you</p>
          <ul className="flex flex-col gap-1">
            {topDelayed.map((r) => (
              <li key={r.route_id} className="flex items-baseline justify-between gap-2 text-zinc-600">
                <span>Route {r.route_id} is running {Math.round(r.avg_delay_s ?? 0)}s behind average right now{r.alerts ? `, ${r.alerts} alert(s)` : ""}.</span>
                <span className="shrink-0 rounded bg-sky-50 px-1 text-[10px] font-semibold text-sky-800">LIVE · Tiger Data · {ago(r.ts)}</span>
              </li>
            ))}
            {estimatedLegs.map((l, i) => (
              <li key={i} className="flex items-baseline justify-between gap-2 text-zinc-600">
                <span>{l.label} has no real-time schedule yet, so its timing is a typical-headway estimate.</span>
                <span className="shrink-0 rounded bg-amber-50 px-1 text-[10px] font-semibold text-amber-900">
                  ESTIMATED · 4WARD routing engine{computedAt ? ` · ${fmtTs(Math.round(computedAt / 1000))}` : ""}
                </span>
              </li>
            ))}
            {!topDelayed.length && !estimatedLegs.length && <li className="text-zinc-500">No known risk factors at plan time.</li>}
          </ul>
        </div>
      </div>
    </details>
  );
}

/** A compact block: title row, stats row, always-visible leg timeline, and two collapsed expanders. */
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
  const isReliable = tagList.some((t) => t.bot.key === "reliable");
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
        {/* 1. name + objective */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-start gap-2">
            <span aria-hidden className="mt-1 h-3 w-3 shrink-0 rounded-full" style={{ background: bot.color }} />
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-zinc-900" data-testid={`tags-${bot.key}`}>
                {r ? tagList.map((t) => t.bot.name).join(" · ") : bot.name}
              </h3>
              <p className="truncate text-xs text-zinc-500">{tagList.map((t) => t.bot.blurb).join(" · ")}</p>
            </div>
          </div>
          <span className="ml-auto flex shrink-0 items-center gap-1">
            {/* small on-time chip, every card */}
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
        {/* 2. time, cost, co2e in that fixed order */}
        {r ? (
          <dl className="mt-2.5 grid grid-cols-3 gap-2">
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
            <Metric label="CO₂e" value={`${(r.co2_kg ?? 0).toFixed(2)} kg`} />
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
          {/* 3. legs timeline with arrival times, always visible */}
          <div className="border-t border-zinc-100 pt-2.5">
            <p className="text-xs text-zinc-600">
              {timingLine(r, deadlineNote)} · {r.transfers ?? 0} transfer{(r.transfers ?? 0) === 1 ? "" : "s"} · walk {r.walk_m} m · Toronto time
            </p>
            <div className="mt-2">
              <LegTimeline route={r} testid={chosen ? "itinerary" : `itinerary-${bot.key}`} />
            </div>
          </div>

          {/* 4. "Why this route" expander: top 3 candidates, rejected + why */}
          <WhyThisRoute bot={bot.key} state={state} />

          {/* Reliable only: extra "Evidence" expander */}
          {isReliable && <Evidence route={r} state={state} computedAt={state.computedAt} />}

          {focused && (
            <div className="mt-2 border-t border-zinc-100 pt-2">
              {r.notes?.length ? (
                <ul className="flex flex-col gap-0.5 text-xs text-zinc-500">
                  {r.notes.map((n) => (
                    <li key={n}>• {n}</li>
                  ))}
                </ul>
              ) : null}
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
            </div>
          )}

          <button
            type="button"
            data-testid={`choose-${bot.key}`}
            onClick={onChoose}
            aria-pressed={chosen}
            className="mt-2 w-full rounded-full px-3 py-2 text-sm font-semibold transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900"
            style={chosen ? { background: bot.color, color: "#fff" } : { background: "#f4f4f5", color: "#18181b" }}
          >
            {chosen ? "Chosen ✓" : "Choose this route"}
          </button>
        </div>
      )}
    </article>
  );
}

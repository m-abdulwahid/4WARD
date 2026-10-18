"use client";
import { useEffect, useRef, useState } from "react";
import type { Candidate } from "@/lib/api";
import { legColor, legMode } from "@/lib/modes";
import ModeBadge from "@/components/ModeBadge";
import LegTimeline from "@/components/LegTimeline";
import { timingLine } from "@/lib/timing";

type Bot = { key: string; name: string; color: string };

/**
 * Clean route view: a small strip floating over the map with the chosen route's numbers, a collapsed itinerary,
 * and the way back. It can be dismissed to a chip so the whole route stays visible.
 */
export default function RouteSummary({
  bot,
  route,
  deadlineNote,
  onBack,
  onChange,
}: {
  bot: Bot;
  route: Candidate;
  deadlineNote?: string | null;
  onBack: () => void;
  onChange: () => void;
}) {
  const [legs, setLegs] = useState(false);
  const [min, setMin] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []); // announce the new view to keyboard and screen reader users

  const backBtn = (
    <button
      type="button"
      data-testid="back-to-routes"
      onClick={onBack}
      className="rounded-full bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-zinc-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900"
    >
      Back to all routes
    </button>
  );

  if (min)
    return (
      <div data-testid="route-summary" className="pointer-events-auto flex items-center gap-2 rounded-full bg-white py-1.5 pr-1.5 pl-3 text-xs shadow-lg ring-1 ring-black/5">
        <span aria-hidden className="h-2.5 w-2.5 rounded-full" style={{ background: bot.color }} />
        <span className="font-semibold">{bot.name}</span>
        <span className="tabular-nums text-zinc-600">{Math.round(route.duration_min ?? 0)} min</span>
        <button type="button" data-testid="route-summary-show" onClick={() => setMin(false)} className="rounded-full px-2 py-1 font-medium text-zinc-700 ring-1 ring-zinc-200 hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-zinc-900">
          Show summary
        </button>
        {backBtn}
      </div>
    );

  return (
    <section
      aria-labelledby="route-summary-title"
      data-testid="route-summary"
      className="pointer-events-auto w-full max-w-xl rounded-2xl bg-white/95 p-3 shadow-xl ring-1 ring-black/5 backdrop-blur md:p-4"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 id="route-summary-title" ref={heading} tabIndex={-1} className="flex items-center gap-2 text-sm font-semibold focus:outline-none">
            <span aria-hidden className="h-3 w-3 shrink-0 rounded-full" style={{ background: bot.color }} />
            Your route: {bot.name}
          </h2>
          <p className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-zinc-600">
            {route.title}
            <span className="ml-1 flex items-center gap-0.5" aria-hidden>
              {route.legs.map((l, i) => {
                const m = legMode(l, route.id);
                return <ModeBadge key={i} mode={m} color={legColor(l, m)} size={16} />;
              })}
            </span>
          </p>
        </div>
        <button
          type="button"
          data-testid="route-summary-dismiss"
          onClick={() => setMin(true)}
          aria-label="Make the summary smaller"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-zinc-500 hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-zinc-900"
        >
          <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </div>

      <dl className="mt-2 grid grid-cols-4 gap-2">
        {[
          ["Time", `${Math.round(route.duration_min ?? 0)} min`],
          ["Cost", `$${(route.cost ?? 0).toFixed(2)}`],
          ["CO₂", `${(route.co2_kg ?? 0).toFixed(2)} kg`],
          ["Transfers", String(route.transfers ?? 0)],
        ].map(([k, v]) => (
          <div key={k}>
            <dt className="text-[11px] text-zinc-500">{k}</dt>
            <dd className="text-sm font-semibold tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-1 text-[11px] text-zinc-500">
        {timingLine(route, deadlineNote)} · Toronto time
      </p>

      {legs && (
        <div id="route-summary-legs" className="mt-2 max-h-[30dvh] overflow-y-auto border-t border-zinc-100 pt-2">
          <LegTimeline route={route} testid="route-view-itinerary" />
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="route-summary-legs"
          aria-expanded={legs}
          aria-controls="route-summary-legs"
          onClick={() => setLegs((v) => !v)}
          className="rounded-full px-3 py-1.5 text-xs font-medium text-zinc-800 ring-1 ring-zinc-200 hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-zinc-900"
        >
          {legs ? "Hide itinerary" : "Show itinerary"}
        </button>
        <button
          type="button"
          data-testid="change-choice"
          onClick={onChange}
          className="rounded-full px-3 py-1.5 text-xs font-medium text-zinc-800 ring-1 ring-zinc-200 hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-zinc-900"
        >
          Change choice
        </button>
        <span className="ml-auto">{backBtn}</span>
      </div>
    </section>
  );
}

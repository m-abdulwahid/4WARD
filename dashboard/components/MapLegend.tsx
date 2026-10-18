"use client";
import { useEffect, useState } from "react";
import { BOTS, BotKey } from "@/lib/bots";
import { MODES, MODE_ORDER, ModeKey } from "@/lib/modes";
import ModeBadge, { ModeLine } from "@/components/ModeBadge";

function Pin({ letter }: { letter: string }) {
  return (
    <span aria-hidden className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-[9px] font-bold text-white ring-2 ring-white">
      {letter}
    </span>
  );
}

/**
 * What the map shows. Lines are coloured by transport mode; small labels on the map say which bot found each route.
 * Open on laptops, collapsed behind a button on phones.
 */
export type LegendBotStatus = { state: "waiting" | "searching" | "done" | "failed" | "excluded"; error?: string | null };

export default function MapLegend({
  status,
  hiddenBots,
  onToggleBot,
  onShowAll,
  highlight,
  modes,
}: {
  status: Record<BotKey, LegendBotStatus>;
  hiddenBots: Partial<Record<BotKey, boolean>>;
  onToggleBot: (k: BotKey) => void;
  onShowAll: () => void;
  highlight: BotKey | null;
  modes: Partial<Record<ModeKey, string>>;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(window.matchMedia("(min-width: 768px)").matches), []);
  // Once there are routes, list only the modes they use; before that, list every mode.
  const used = Object.keys(modes).length > 0;
  const shown = used ? MODE_ORDER.filter((m) => modes[m]) : MODE_ORDER;

  return (
    <section aria-label="Map legend" data-testid="map-legend" className="rounded-2xl bg-white/95 p-2 shadow-lg ring-1 ring-black/5 backdrop-blur">
      <button
        type="button"
        data-testid="legend-button"
        aria-expanded={open}
        aria-controls="legend-body"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 rounded-lg px-1 py-0.5 text-left focus-visible:outline-2 focus-visible:outline-zinc-900"
      >
        <span className="text-xs font-semibold text-zinc-800 md:text-[11px] md:tracking-wide md:text-zinc-500 md:uppercase">Legend</span>
        <span aria-hidden className={`text-xs text-zinc-500 transition-transform duration-150 ${open ? "rotate-180" : ""}`}>
          ▾
        </span>
      </button>
      <div id="legend-body" hidden={!open} className="mt-1 max-h-[55dvh] w-52 space-y-2 overflow-y-auto px-1 pb-1 text-xs text-zinc-700">
        <div>
          <h3 className="mb-1 text-[10px] font-semibold tracking-wide text-zinc-500 uppercase">Routes on the map</h3>
          <ul className="space-y-1">
            {BOTS.map((b) => {
              const st = status[b.key];
              const shown = !hiddenBots[b.key];
              return (
                <li key={b.key} className="flex items-center gap-2" data-testid={`legend-bot-${b.key}`}>
                  <button
                    type="button"
                    data-testid={`legend-eye-${b.key}`}
                    aria-pressed={shown}
                    aria-label={`${shown ? "Hide" : "Show"} the ${b.name} route on the map`}
                    title={`${shown ? "Hide" : "Show"} on the map`}
                    onClick={() => onToggleBot(b.key)}
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md ring-1 focus-visible:outline-2 focus-visible:outline-zinc-900 ${shown ? "bg-white text-zinc-700 ring-zinc-200 hover:bg-zinc-50" : "bg-zinc-100 text-zinc-400 ring-zinc-200"}`}
                  >
                    <svg aria-hidden viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                      {shown ? (
                        <>
                          <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" />
                          <circle cx="12" cy="12" r="3" />
                        </>
                      ) : (
                        <>
                          <path d="M10.7 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-2.2 3.2M6.6 6.6C3.9 8.4 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6" />
                          <path d="M2 2l20 20" />
                        </>
                      )}
                    </svg>
                  </button>
                  <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: b.color, opacity: st.state === "done" && shown ? 1 : 0.4 }} />
                  <span className={`min-w-0 truncate ${highlight === b.key ? "font-semibold text-zinc-900" : st.state === "done" && shown ? "" : "text-zinc-400"}`}>{b.name}</span>
                  <span className={`ml-auto shrink-0 text-[10px] ${st.state === "failed" ? "text-red-700" : "text-zinc-500"}`} title={st.state === "failed" ? st.error ?? undefined : undefined}>
                    {highlight === b.key ? "selected" : !shown && st.state === "done" ? "hidden" : { waiting: "", searching: "searching…", done: "", failed: "failed, no route", excluded: "doesn't fit trip" }[st.state]}
                  </span>
                </li>
              );
            })}
          </ul>
          {highlight && (
            <button
              type="button"
              data-testid="legend-show-all"
              onClick={onShowAll}
              className="mt-1.5 w-full rounded-lg bg-zinc-900 px-2 py-1 text-[11px] font-semibold text-white hover:bg-zinc-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900"
            >
              Show all routes
            </button>
          )}
          <p className="mt-1 text-[10px] leading-snug text-zinc-500">Labels on the map name the bot behind each route. With all routes shown, each line has an edge in its bot&rsquo;s colour, and routes on the same street sit side by side. Click a line to select it.</p>
        </div>
        <div className="border-t border-zinc-100 pt-2">
          <h3 className="mb-1 text-[10px] font-semibold tracking-wide text-zinc-500 uppercase">{used ? "Modes on these routes" : "Modes"}</h3>
          <ul className="space-y-1">
            {shown.map((m) => (
              <li key={m} className="flex items-center gap-2" data-testid={`legend-mode-${m}`}>
                <ModeBadge mode={m} color={modes[m]} size={18} />
                <ModeLine mode={m} color={modes[m]} length={24} />
                <span className="leading-tight">{MODES[m].label}</span>
              </li>
            ))}
          </ul>
          {(shown.includes("subway") || shown.includes("streetcar")) && <p className="mt-1 text-[10px] leading-snug text-zinc-500">Subway and streetcar legs use the TTC line colour, so they can differ from route to route.</p>}
        </div>
        <p className="flex items-center gap-2 border-t border-zinc-100 pt-2 text-[11px] text-zinc-600">
          <span className="flex gap-0.5">
            <Pin letter="A" />
            <Pin letter="B" />
          </span>
          Start and destination
        </p>
      </div>
    </section>
  );
}

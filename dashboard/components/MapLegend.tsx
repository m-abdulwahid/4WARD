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
export default function MapLegend({ found, highlight, modes }: { found: Record<BotKey, boolean>; highlight: BotKey | null; modes: Partial<Record<ModeKey, string>> }) {
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
        <div className="border-t border-zinc-100 pt-2">
          <h3 className="mb-1 text-[10px] font-semibold tracking-wide text-zinc-500 uppercase">Which bot found it</h3>
          <ul className="space-y-1">
            {BOTS.map((b) => (
              <li key={b.key} className={`flex items-center gap-2 ${found[b.key] ? "" : "text-zinc-400"}`}>
                <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: b.color, opacity: found[b.key] ? 1 : 0.4 }} />
                <span className={highlight === b.key ? "font-semibold text-zinc-900" : ""}>{b.name}</span>
                {!found[b.key] && <span className="ml-auto text-[10px]">no route yet</span>}
                {highlight === b.key && <span className="ml-auto text-[10px] text-zinc-500">selected</span>}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-[10px] leading-snug text-zinc-500">Labels on the map name the bot behind each route. Routes not selected are thinner and lighter.</p>
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

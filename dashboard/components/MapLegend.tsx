"use client";
import { useEffect, useState } from "react";
import { BOTS, BotKey } from "@/lib/bots";

function Line({ color, dashed = false, faded = false }: { color: string; dashed?: boolean; faded?: boolean }) {
  return (
    <svg aria-hidden viewBox="0 0 28 8" className="h-2 w-7 shrink-0">
      {!dashed && !faded && <line x1="3" y1="4" x2="25" y2="4" stroke="#fff" strokeWidth="7" strokeLinecap="round" />}
      <line
        x1="3"
        y1="4"
        x2="25"
        y2="4"
        stroke={color}
        strokeWidth={faded ? 2.5 : 4}
        strokeOpacity={faded ? 0.35 : 1}
        strokeLinecap="round"
        strokeDasharray={dashed ? "1 5" : undefined}
      />
    </svg>
  );
}

function Pin({ letter }: { letter: string }) {
  return (
    <span aria-hidden className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-[9px] font-bold text-white ring-2 ring-white">
      {letter}
    </span>
  );
}

/** What the lines and pins on the map mean. Open on laptops, collapsed behind a button on phones. */
export default function MapLegend({ found, highlight }: { found: Record<BotKey, boolean>; highlight: BotKey | null }) {
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(window.matchMedia("(min-width: 768px)").matches), []);

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
      <div id="legend-body" hidden={!open} className="mt-1 w-52 space-y-2 px-1 pb-1 text-xs text-zinc-700">
        <ul className="space-y-1">
          {BOTS.map((b) => (
            <li key={b.key} className={`flex items-center gap-2 ${found[b.key] ? "" : "text-zinc-400"}`}>
              <Line color={b.color} faded={!!highlight && highlight !== b.key} />
              <span className={highlight === b.key ? "font-semibold text-zinc-900" : ""}>{b.name}</span>
              {!found[b.key] && <span className="ml-auto text-[10px]">no route yet</span>}
              {highlight === b.key && <span className="ml-auto text-[10px] text-zinc-500">selected</span>}
            </li>
          ))}
        </ul>
        <ul className="space-y-1 border-t border-zinc-100 pt-2 text-[11px] text-zinc-600">
          <li className="flex items-center gap-2">
            <Line color="#52525b" />
            Ride, drive or cycle
          </li>
          <li className="flex items-center gap-2">
            <Line color="#52525b" dashed />
            Walk
          </li>
          <li className="flex items-center gap-2">
            <Line color="#52525b" faded />
            Other routes (not selected)
          </li>
          <li className="flex items-center gap-2">
            <span className="flex w-7 justify-center gap-0.5">
              <Pin letter="A" />
              <Pin letter="B" />
            </span>
            Start and destination
          </li>
        </ul>
      </div>
    </section>
  );
}

"use client";
import { BOTS, BotKey } from "@/lib/bots";

function Eye({ open }: { open: boolean }) {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      {open ? (
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
  );
}

/**
 * Show or hide each bot's route on the map. Same state as the legend's eye toggles.
 * Double-click a chip (or press its "Only" button) to see that route by itself.
 */
export default function RouteChips({
  found,
  hidden,
  onToggle,
  onSolo,
  onShowAll,
  onHover,
  sameAs = {},
}: {
  found: Record<BotKey, boolean>;
  hidden: Partial<Record<BotKey, boolean>>;
  onToggle: (k: BotKey) => void;
  onSolo: (k: BotKey) => void;
  onShowAll: () => void;
  onHover: (k: BotKey | null) => void; // pointer over or keyboard focus on a chip highlights its route
  sameAs?: Partial<Record<BotKey, string>>; // "same route as Economic"
}) {
  const withRoute = BOTS.filter((b) => found[b.key]);
  const shownKeys = withRoute.filter((b) => !hidden[b.key]).map((b) => b.key);
  const solo = withRoute.length > 1 && shownKeys.length === 1 ? shownKeys[0] : null;
  const anyHidden = withRoute.some((b) => hidden[b.key]);

  return (
    <div role="group" aria-label="Show or hide routes on the map" data-testid="route-chips" className="flex items-center gap-1.5 rounded-full bg-white/95 p-1.5 shadow-lg ring-1 ring-black/5 backdrop-blur">
      {BOTS.map((b) => {
        const has = found[b.key];
        const shown = has && !hidden[b.key];
        return (
          <div
            key={b.key}
            onMouseEnter={() => shown && onHover(b.key)}
            onMouseLeave={() => onHover(null)}
            onFocus={() => shown && onHover(b.key)}
            onBlur={() => onHover(null)}
            className={`flex shrink-0 items-center rounded-full ring-1 ${shown ? "bg-white ring-zinc-200" : "bg-zinc-100 ring-zinc-200"}`}
          >
            <button
              type="button"
              data-testid={`route-chip-${b.key}`}
              aria-pressed={shown}
              disabled={!has}
              onClick={() => onToggle(b.key)}
              onDoubleClick={() => onSolo(b.key)}
              title={has ? `${shown ? "Hide" : "Show"} ${b.name}. Double-click to see only this route.` : `${b.name}: no route yet`}
              aria-label={has ? `${b.name} route, ${shown ? "shown" : "hidden"}` : `${b.name}: no route yet`}
              className={`flex items-center gap-1.5 rounded-full py-1 pr-2 pl-2 text-xs font-medium focus-visible:outline-2 focus-visible:outline-zinc-900 disabled:cursor-default ${shown ? "text-zinc-900" : "text-zinc-400"}`}
            >
              <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: b.color, opacity: shown ? 1 : 0.35 }} />
              <span className="flex flex-col items-start leading-tight">
                <span className={shown ? "" : "line-through decoration-zinc-400"}>{b.name}</span>
                {sameAs[b.key] && (
                  <span data-testid={`route-same-${b.key}`} className="text-[10px] font-normal text-zinc-500">
                    same route as {sameAs[b.key]}
                  </span>
                )}
              </span>
              <Eye open={!!shown} />
            </button>
            {has && withRoute.length > 1 && (
              <button
                type="button"
                data-testid={`route-solo-${b.key}`}
                aria-pressed={solo === b.key}
                onClick={() => (solo === b.key ? onShowAll() : onSolo(b.key))}
                aria-label={solo === b.key ? `Show all routes again` : `Show only the ${b.name} route`}
                title={solo === b.key ? "Show all routes" : "Show only this route"}
                className={`mr-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold focus-visible:outline-2 focus-visible:outline-zinc-900 ${solo === b.key ? "bg-zinc-900 text-white" : "text-zinc-500 hover:bg-zinc-100"}`}
              >
                Only
              </button>
            )}
          </div>
        );
      })}
      <button
        type="button"
        data-testid="route-chips-show-all"
        onClick={onShowAll}
        disabled={!anyHidden}
        className="shrink-0 rounded-full bg-zinc-900 px-3 py-1 text-xs font-semibold text-white hover:bg-zinc-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900 disabled:bg-zinc-200 disabled:text-zinc-500"
      >
        Show all
      </button>
    </div>
  );
}

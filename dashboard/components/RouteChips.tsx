"use client";
import { BOTS, BotKey } from "@/lib/bots";

/** On/off switch drawn like the Layers panel switches (Transit lines, Bike Share stations), sized for a chip. */
function Switch({ on }: { on: boolean }) {
  return (
    <span aria-hidden className={`relative h-4 w-7 shrink-0 rounded-full transition-colors duration-150 ${on ? "bg-zinc-900" : "bg-zinc-300"}`}>
      <span className={`absolute top-0.5 left-0 h-3 w-3 rounded-full bg-white shadow transition-transform duration-150 ${on ? "translate-x-3.5" : "translate-x-0.5"}`} />
    </span>
  );
}

/**
 * Show or hide each bot's route on the map with an on/off switch. Same state as the legend's eye toggles.
 */
export default function RouteChips({
  found,
  hidden,
  onToggle,
  onShowAll,
  onHover,
}: {
  found: Record<BotKey, boolean>;
  hidden: Partial<Record<BotKey, boolean>>;
  onToggle: (k: BotKey) => void;
  onShowAll: () => void;
  onHover: (k: BotKey | null) => void; // pointer over or keyboard focus on a chip highlights its route
}) {
  const withRoute = BOTS.filter((b) => found[b.key]);
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
              role="switch"
              aria-checked={!!shown}
              disabled={!has}
              onClick={() => onToggle(b.key)}
              title={has ? `${shown ? "Hide" : "Show"} ${b.name} on the map` : `${b.name}: no route yet`}
              aria-label={has ? `${b.name} route on the map` : `${b.name}: no route yet`}
              className={`flex items-center gap-1.5 rounded-full py-1 pr-2 pl-2 text-xs font-medium focus-visible:outline-2 focus-visible:outline-zinc-900 disabled:cursor-default ${shown ? "text-zinc-900" : "text-zinc-400"}`}
            >
              <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: b.color, opacity: shown ? 1 : 0.35 }} />
              <span>{b.name}</span>
              <Switch on={!!shown} />
            </button>
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

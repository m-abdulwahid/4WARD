import type { Candidate } from "@/lib/api";
import { MODES, legColor, legMode } from "@/lib/modes";
import ModeBadge, { ModeLine } from "@/components/ModeBadge";

/** A route's legs as a timeline: duration, mode icon with a stretch of line drawn like the map draws it, label, cost. */
export default function LegTimeline({ route, testid }: { route: Candidate; testid: string }) {
  return (
    <ol className="flex flex-col text-sm" data-testid={testid}>
      {route.legs.map((l, i) => {
        const mode = legMode(l, route.id);
        const color = legColor(l, mode);
        return (
          <li key={i} className="flex gap-2">
            <span className="w-14 shrink-0 pt-0.5 text-right text-xs whitespace-nowrap tabular-nums leading-5 text-zinc-500">{Math.round(l.duration_s / 60)} min</span>
            <span className="flex w-6 shrink-0 flex-col items-center">
              <ModeBadge mode={mode} color={color} />
              {i < route.legs.length - 1 && (
                <span className="flex min-h-3 flex-1 justify-center overflow-hidden">
                  <ModeLine mode={mode} color={color} vertical length={40} />
                </span>
              )}
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
  );
}

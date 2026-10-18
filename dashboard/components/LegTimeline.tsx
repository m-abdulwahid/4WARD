import type { Candidate, Leg, StopVisit } from "@/lib/api";
import { MODES, legColor, legMode } from "@/lib/modes";
import ModeBadge, { ModeLine } from "@/components/ModeBadge";

function LegRow({ leg, route, last }: { leg: Leg; route: Candidate; last: boolean }) {
  const mode = legMode(leg, route.id);
  const color = legColor(leg, mode);
  return (
    <li className="flex gap-2">
      <span className="w-14 shrink-0 pt-0.5 text-right text-xs whitespace-nowrap tabular-nums leading-5 text-zinc-500">{Math.round(leg.duration_s / 60)} min</span>
      <span className="flex w-6 shrink-0 flex-col items-center">
        <ModeBadge mode={mode} color={color} />
        {!last && (
          <span className="flex min-h-3 flex-1 justify-center overflow-hidden">
            <ModeLine mode={mode} color={color} vertical length={40} />
          </span>
        )}
      </span>
      <span className="min-w-0 pb-1 leading-5 text-zinc-800">
        <span className="block text-[11px] font-semibold text-zinc-500">{MODES[mode].label}</span>
        {leg.label}
        {leg.cost ? <span className="text-zinc-500"> · ${leg.cost.toFixed(2)}</span> : null}
        {leg.provenance === "estimated" && <span className="ml-1 rounded bg-zinc-100 px-1 text-[10px] text-zinc-600">estimated</span>}
      </span>
    </li>
  );
}

function StopRow({ v }: { v: StopVisit }) {
  const parts = [
    v.arrive ? `arrive ${v.arrive}` : null,
    v.wait_min > 0 ? `wait ${Math.round(v.wait_min)} min` : null,
    v.dwell_min > 0 ? `stay ${Math.round(v.dwell_min)} min` : null,
    v.depart ? `leave ${v.depart}` : v.kind === "origin" ? null : "trip ends",
  ].filter(Boolean);
  return (
    <li className="flex items-center gap-2 py-1" data-testid={`stop-visit-${v.label}`}>
      <span className="w-14 shrink-0" />
      <span aria-hidden className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-[11px] font-bold text-white">
        {v.label}
      </span>
      <span className="min-w-0 text-xs leading-5 text-zinc-800">
        <span className="font-semibold">{v.name}</span>
        {v.kind !== "origin" && v.kind !== "destination" && v.kind !== "stop" ? <span className="text-zinc-500"> ({v.kind})</span> : null}
        <span className="block text-zinc-500">{v.kind === "origin" ? `start, leave ${v.depart}` : parts.join(" · ")}</span>
        {(v.traveler || v.car) && (
          <span className="block text-[11px] text-zinc-500" data-testid={`stop-state-${v.label}`}>
            {[v.traveler ? `you: ${v.traveler.replace("_", " ")}` : null, v.car && v.car !== "no car" ? `car: ${v.car}` : null].filter(Boolean).join(" · ")}
          </span>
        )}
      </span>
    </li>
  );
}

/** A route's legs as a timeline: duration, mode icon with a stretch of line drawn like the map draws it, label, cost.
 * Multi-stop trips also list every stop with its arrival, stay and departure between the legs. */
export default function LegTimeline({ route, testid }: { route: Candidate; testid: string }) {
  const schedule = route.schedule;
  if (!schedule?.length) {
    return (
      <ol className="flex flex-col text-sm" data-testid={testid}>
        {route.legs.map((l, i) => (
          <LegRow key={i} leg={l} route={route} last={i === route.legs.length - 1} />
        ))}
      </ol>
    );
  }
  return (
    <ol className="flex flex-col text-sm" data-testid={testid}>
      {schedule.map((v, s) => {
        const legs = route.legs.filter((l) => (l.segment ?? 0) === s);
        return [
          <StopRow key={`stop-${v.label}`} v={v} />,
          ...legs.map((l, i) => <LegRow key={`leg-${s}-${i}`} leg={l} route={route} last={i === legs.length - 1} />),
        ];
      })}
    </ol>
  );
}

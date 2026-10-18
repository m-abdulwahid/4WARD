"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Candidate } from "@/lib/api";
import RouteMap, { MapRoute, Vehicle } from "@/components/RouteMap";
import { MODES } from "@/lib/modes";
import { addMinutesToClock, positionAtTime, routeDurationMin } from "@/lib/timelapse";

export type TimelapseRoute = { key: string; name: string; color: string; candidate: Candidate };

const SPEEDS = [0.5, 1, 2, 4] as const;
const TARGET_REAL_SECONDS = 25; // how long the slowest route takes to finish, at 1x

export default function Timelapse({
  routes,
  departLabel,
  origin,
  destination,
  onClose,
}: {
  routes: TimelapseRoute[];
  departLabel: string;
  origin?: [number, number] | null;
  destination?: [number, number] | null;
  onClose: () => void;
}) {
  const maxDuration = useMemo(() => Math.max(1, ...routes.map((r) => routeDurationMin(r.candidate))), [routes]);
  const baseSpeed = maxDuration / TARGET_REAL_SECONDS; // simulated minutes per real second at 1x
  const [simMinutes, setSimMinutes] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speedMult, setSpeedMult] = useState<(typeof SPEEDS)[number]>(1);
  const speedRef = useRef(baseSpeed * speedMult);
  speedRef.current = baseSpeed * speedMult;
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === " ") {
        e.preventDefault();
        setPlaying((p) => !p);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      setSimMinutes((s) => {
        const next = s + speedRef.current * dt;
        if (next >= maxDuration) {
          setPlaying(false);
          return maxDuration;
        }
        return next;
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, maxDuration]);

  const points = useMemo(() => routes.map((r) => ({ r, pt: positionAtTime(r.candidate, simMinutes) })), [routes, simMinutes]);
  const vehicles: Vehicle[] = points
    .filter((p) => p.pt)
    .map(({ r, pt }) => ({ id: r.key, color: r.color, lngLat: pt!.lngLat, mode: pt!.mode, arrived: pt!.arrived }));
  const mapRoutes: MapRoute[] = routes.map((r) => ({ id: r.key, color: r.color, candidate: r.candidate, label: r.name }));
  const allArrived = points.every((p) => p.pt?.arrived);

  const replay = () => {
    setSimMinutes(0);
    setPlaying(true);
  };

  return (
    <div
      data-testid="timelapse"
      role="dialog"
      aria-modal="true"
      aria-label="Watch the trip unfold, a synced playback of all four routes"
      className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-950/60 p-2 sm:p-6"
    >
      <div className="flex max-h-full w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-black/10">
        <div className="flex items-center justify-between gap-3 border-b border-zinc-100 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-zinc-900">Watch the trip unfold</h2>
            <p className="text-xs text-zinc-500">All four routes leave together and arrive on their own real schedule.</p>
          </div>
          <div className="flex items-center gap-3">
            <span data-testid="timelapse-clock" className="rounded-full bg-zinc-900 px-3 py-1 text-sm font-semibold tabular-nums text-white">
              {addMinutesToClock(departLabel, simMinutes)}
            </span>
            <button
              ref={closeRef}
              type="button"
              data-testid="timelapse-close"
              onClick={onClose}
              aria-label="Close the trip timelapse"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-100 text-zinc-700 hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-zinc-900"
            >
              <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round">
                <path d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          </div>
        </div>

        <div className="relative h-[42vh] min-h-[260px] sm:h-[52vh]">
          <RouteMap
            routes={mapRoutes}
            origin={origin}
            destination={destination}
            colorBy="route"
            vehicles={vehicles}
            padding={{ top: 40, right: 40, bottom: 40, left: 40 }}
          />
        </div>

        <div className="flex flex-col gap-2.5 border-t border-zinc-100 px-4 py-3">
          <div className="flex items-center gap-3">
            <button
              type="button"
              data-testid="timelapse-playpause"
              onClick={() => (allArrived ? replay() : setPlaying((p) => !p))}
              aria-label={allArrived ? "Replay" : playing ? "Pause" : "Play"}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-white hover:bg-zinc-700"
            >
              {allArrived ? (
                <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2.2}>
                  <path d="M3 12a9 9 0 1 1 3 6.7M3 12v5h5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              ) : playing ? (
                <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor">
                  <rect x="6" y="5" width="4" height="14" rx="1" />
                  <rect x="14" y="5" width="4" height="14" rx="1" />
                </svg>
              ) : (
                <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor">
                  <path d="M7 5.5v13l11-6.5z" />
                </svg>
              )}
            </button>
            <input
              data-testid="timelapse-scrub"
              type="range"
              min={0}
              max={maxDuration}
              step={0.1}
              value={simMinutes}
              onChange={(e) => {
                setPlaying(false);
                setSimMinutes(Number(e.target.value));
              }}
              aria-label="Scrub through the trip"
              className="h-1.5 flex-1 cursor-pointer accent-zinc-900"
            />
            <div className="flex shrink-0 items-center gap-1 rounded-full bg-zinc-100 p-0.5 text-[11px] font-semibold">
              {SPEEDS.map((s) => (
                <button
                  key={s}
                  type="button"
                  data-testid={`timelapse-speed-${s}`}
                  onClick={() => setSpeedMult(s)}
                  aria-pressed={speedMult === s}
                  className={`rounded-full px-2 py-1 ${speedMult === s ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-200"}`}
                >
                  {s}x
                </button>
              ))}
            </div>
          </div>

          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {points.map(({ r, pt }) => {
              const dur = routeDurationMin(r.candidate);
              const arrived = pt?.arrived;
              return (
                <li key={r.key} data-testid={`timelapse-status-${r.key}`} className="flex items-center gap-2 rounded-lg bg-zinc-50 px-2.5 py-1.5">
                  <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: r.color }} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-semibold text-zinc-800">{r.name}</p>
                    <p className="truncate text-[11px] text-zinc-500">
                      {arrived ? `Arrived ${addMinutesToClock(departLabel, dur)}` : pt ? `${MODES[pt.mode].label} · en route` : "—"}
                    </p>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </div>
  );
}

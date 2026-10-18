import type { Candidate } from "@/lib/api";
import { legMode, ModeKey } from "@/lib/modes";

export type TimelapsePoint = { lngLat: [number, number]; mode: ModeKey; legLabel: string; arrived: boolean };

/** A point a fraction f of the way along a line, by cumulative length (works for 2-point legs too). */
function along(coords: [number, number][], f: number): [number, number] {
  if (coords.length === 1) return coords[0];
  const seg = coords.slice(1).map((c, i) => Math.hypot(c[0] - coords[i][0], c[1] - coords[i][1]));
  const total = seg.reduce((a, b) => a + b, 0);
  if (total === 0) return coords[0];
  let left = total * Math.min(1, Math.max(0, f));
  for (let i = 0; i < seg.length; i++) {
    if (left <= seg[i] || i === seg.length - 1) {
      const t = seg[i] ? Math.min(1, left / seg[i]) : 0;
      return [coords[i][0] + (coords[i + 1][0] - coords[i][0]) * t, coords[i][1] + (coords[i + 1][1] - coords[i][1]) * t];
    }
    left -= seg[i];
  }
  return coords[coords.length - 1];
}

/** How many simulated minutes this route takes door to door (sum of its own legs' real durations). */
export function routeDurationMin(route: Candidate): number {
  return route.legs.reduce((a, l) => a + l.duration_s / 60, 0);
}

/** Where a route's vehicle is `simMinutes` after departure: which leg it's on, how far along it, in map
 * [lng, lat] order. Legs run sequentially at a constant pace across each leg's own real duration -- an
 * honest simplification for the animation (no per-stop dwell time shown), never a route recomputation;
 * the numbers driving it are the same ones already shown in the itinerary. */
export function positionAtTime(route: Candidate, simMinutes: number): TimelapsePoint | null {
  const legs = route.legs.filter((l) => l.geometry.length > 0);
  if (!legs.length) return null;
  const total = routeDurationMin(route);
  if (simMinutes >= total) {
    const last = legs[legs.length - 1];
    const [la, lo] = last.geometry[last.geometry.length - 1];
    return { lngLat: [lo, la], mode: legMode(last, route.id), legLabel: last.label, arrived: true };
  }
  let elapsed = 0;
  for (let i = 0; i < legs.length; i++) {
    const leg = legs[i];
    const dMin = leg.duration_s / 60;
    if (simMinutes < elapsed + dMin || i === legs.length - 1) {
      const f = dMin > 0 ? (simMinutes - elapsed) / dMin : 1;
      const coords = leg.geometry.map(([la, lo]) => [lo, la] as [number, number]);
      return { lngLat: along(coords, f), mode: legMode(leg, route.id), legLabel: leg.label, arrived: false };
    }
    elapsed += dMin;
  }
  return null;
}

/** "HH:MM" + minutes -> "h:mm AM/PM", wrapping past midnight. */
export function addMinutesToClock(hhmm: string, minutes: number): string {
  const [h, m] = hhmm.split(":").map(Number);
  let total = (((h * 60 + m + Math.round(minutes)) % 1440) + 1440) % 1440;
  const hh = Math.floor(total / 60);
  const mm = total % 60;
  const period = hh < 12 ? "AM" : "PM";
  const h12 = hh % 12 === 0 ? 12 : hh % 12;
  return `${h12}:${String(mm).padStart(2, "0")} ${period}`;
}

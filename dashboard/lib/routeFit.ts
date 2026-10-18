import type { Candidate } from "@/lib/api";
import { legMode } from "@/lib/modes";

// Which routes to show for a trip. Done in the frontend for now; the rules mirror bots/critic.py
// (meets_deadline, violates, bag_friendliness) plus budget and vehicle checks, all from engine output.

export type TripConstraints = {
  arrive_by?: string | null; // "HH:MM"
  bags?: number;
  has_car?: boolean;
  has_bike?: boolean;
  has_ebike?: boolean;
};

export type Breaks = "late" | "budget" | "bags" | "limit" | "car" | "bike" | "ebike";

/** Constraints this route breaks. `checkDeadline` is false when the deadline cannot be judged (it is tomorrow). */
export function routeBreaks(r: Candidate, c: TripConstraints, budget: number | null, checkDeadline: boolean): Breaks[] {
  const out: Breaks[] = [];
  if (checkDeadline && c.arrive_by) {
    const late = r.meets_deadline !== undefined ? r.meets_deadline === false : !!r.arrive && r.arrive > c.arrive_by;
    if (late) out.push("late");
  }
  if (budget !== null && (r.cost ?? 0) > budget) out.push("budget");
  if ((c.bags ?? 0) > 0 && r.bag_friendliness !== undefined && r.bag_friendliness < 0.3) out.push("bags"); // same threshold as the critic
  if (r.violates?.length) out.push("limit"); // e.g. max_walk
  for (const l of r.legs) {
    const m = legMode(l, r.id);
    if ((m === "car" || m === "park_ride") && !c.has_car && !out.includes("car")) out.push("car");
    if (l.mode === "bike" && !c.has_bike && !out.includes("bike")) out.push("bike");
    if (l.mode === "ebike" && !c.has_ebike && !out.includes("ebike")) out.push("ebike");
  }
  return out;
}

export type Fit<K extends string> = {
  shown: K[]; // routes to put on the map, chips and Routes list
  excluded: K[]; // finished routes left out because they break a constraint
  fallback: null | { kind: "deadline"; arriveBy: string } | { kind: "budget"; budget: number }; // nothing fits: closest shown
};

/** Keep routes that fit; if none arrive in time, show the closest by arrival instead (and say so). */
export function fitRoutes<K extends string>(
  routes: { key: K; route: Candidate }[],
  c: TripConstraints,
  budget: number | null,
  checkDeadline: boolean,
): Fit<K> {
  const broken = new Map(routes.map(({ key, route }) => [key, routeBreaks(route, c, budget, checkDeadline)]));
  const fits = routes.filter(({ key }) => broken.get(key)!.length === 0).map((x) => x.key);
  if (fits.length || !routes.length) return { shown: fits, excluded: routes.map((x) => x.key).filter((k) => !fits.includes(k)), fallback: null };

  // Nothing fits everything. If the deadline is what no route can meet, show the closest arrivals.
  const onTime = routes.filter(({ key }) => !broken.get(key)!.includes("late"));
  if (checkDeadline && c.arrive_by && onTime.length === 0) {
    // prefer routes whose only problem is the deadline; otherwise all of them, earliest arrival first
    const onlyLate = routes.filter(({ key }) => broken.get(key)!.every((b) => b === "late"));
    const pool = (onlyLate.length ? onlyLate : routes).slice().sort((a, b) => (a.route.arrive ?? "99:99").localeCompare(b.route.arrive ?? "99:99"));
    const shown = pool.map((x) => x.key);
    return { shown, excluded: routes.map((x) => x.key).filter((k) => !shown.includes(k)), fallback: { kind: "deadline", arriveBy: c.arrive_by } };
  }
  // Otherwise, if the budget is what nothing fits, show the cheapest (an empty list would look broken).
  if (budget !== null && routes.every(({ key }) => broken.get(key)!.includes("budget"))) {
    const cheapest = Math.min(...routes.map((x) => x.route.cost ?? Infinity));
    const shown = routes.filter((x) => (x.route.cost ?? Infinity) === cheapest).map((x) => x.key);
    return { shown, excluded: routes.map((x) => x.key).filter((k) => !shown.includes(k)), fallback: { kind: "budget", budget } };
  }
  return { shown: [], excluded: routes.map((x) => x.key), fallback: null };
}

/** "08:30" -> "8:30 AM" */
export function clock(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const h12 = h % 12 || 12;
  return `${h12}:${String(m).padStart(2, "0")}${h < 12 ? " AM" : " PM"}`;
}

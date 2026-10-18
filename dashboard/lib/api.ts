export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Shared bot token for /run and WebSocket. Empty in local when BOT_TOKEN is unset. */
export const BOT_TOKEN = process.env.NEXT_PUBLIC_BOT_TOKEN ?? "";

export function apiHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  if (BOT_TOKEN) h["X-Bot-Token"] = BOT_TOKEN;
  return h;
}

export function runWebSocketUrl(runId: string): string {
  const base = `${API.replace(/^http/, "ws")}/ws/run/${runId}`;
  return BOT_TOKEN ? `${base}?bot_token=${encodeURIComponent(BOT_TOKEN)}` : base;
}

export type Leg = {
  mode: string;
  label: string;
  from: [number, number];
  to: [number, number];
  distance_km: number;
  duration_s: number;
  geometry: [number, number][]; // [lat, lon]
  provenance: string;
  cost?: number;
  co2_kg?: number;
  color?: string;
  cand_id?: string; // multi-stop: id of the single-leg option this leg came from (e.g. park_ride)
  segment?: number; // multi-stop: which leg of the trip (A to B is 0, B to C is 1, ...) this leg belongs to
};

/** One row of a multi-stop schedule: when you reach a stop, how long you stay, and when you leave. */
export type StopVisit = {
  label: string; // A, B, C ...
  name: string;
  kind: string; // origin | stop | via | pickup | dropoff | errand | destination
  arrive: string | null;
  wait_min: number; // waiting for a time window to open
  dwell_min: number;
  depart: string | null;
  window?: [string, string] | null;
};

export type StopType = "stop" | "via" | "pickup" | "dropoff" | "errand";
export type StopInput = { name: string; type: StopType; dwell_min: number };

export type Candidate = {
  id: string;
  title: string;
  available: boolean;
  reason?: string;
  legs: Leg[];
  duration_min: number | null;
  cost: number | null;
  co2_kg: number | null;
  transfers: number | null;
  walk_m: number | null;
  bag_friendliness?: number;
  depart?: string;
  arrive?: string;
  notes?: string[];
  meets_deadline?: boolean;
  leave_by?: string;
  violates?: string[];
  reliability?: { reliability_pct: number; history_days: number; basis: string };
  pareto_optimal?: boolean;
  schedule?: StopVisit[]; // multi-stop trips only
};

export type TripPoint = { label: string; name: string; lat: number; lon: number; type: string; dwell_min: number; window?: [string, string] | null };

export type RouteResult = {
  origin_place: { name: string; lat: number; lon: number };
  destination_place: { name: string; lat: number; lon: number };
  points?: TripPoint[]; // multi-stop trips: every point in visiting order, labelled A, B, C ...
  optimize_order?: boolean;
  candidates: Candidate[];
  data_notes: string[];
  traffic_multiplier: number;
};

export type Constraints = {
  modes: string[];
  depart_at?: string | null;
  arrive_by?: string | null;
  park_ride_lot?: string | null;
  bags: number;
  no_presto: boolean;
  has_car: boolean;
  ev: boolean;
  has_bike: boolean;
  has_ebike: boolean;
  avoid_stairs: boolean;
  max_walk_m?: number | null;
  stay_hours?: number | null;
};

export const ALL_MODES = ["walk", "bike", "ebike", "bikeshare", "drive", "rideshare", "transit", "park_ride"] as const;

export async function computeRoute(
  origin: string,
  destination: string,
  constraints: Constraints,
  stops: StopInput[] = [],
  optimizeOrder = false,
): Promise<RouteResult> {
  const r = await fetch(`${API}/route`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      origin: { text: origin },
      destination: { text: destination },
      stops: stops.filter((s) => s.name.trim()).map((s) => ({ name: s.name.trim(), type: s.type, dwell_min: s.dwell_min })),
      optimize_order: optimizeOrder,
      constraints,
    }),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail ?? `Route request failed (${r.status})`);
  }
  return r.json();
}

export async function suggest(q: string): Promise<{ name: string; lat: number; lon: number }[]> {
  try {
    const r = await fetch(`${API}/suggest?q=${encodeURIComponent(q)}`);
    return (await r.json()).results;
  } catch {
    return [];
  }
}

export async function getAssumptions(): Promise<{ park_ride_lots: string[] }> {
  try {
    const r = await fetch(`${API}/assumptions`);
    const j = await r.json();
    return { park_ride_lots: j.park_ride_lots ?? [] };
  } catch {
    return { park_ride_lots: [] };
  }
}

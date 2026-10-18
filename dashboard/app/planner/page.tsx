"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { timingLine } from "@/lib/timing";
import { ALL_MODES, Candidate, Constraints, EndType, RouteResult, StopInput, StopType, computeRoute, getAssumptions, suggest } from "@/lib/api";

const RouteMap = dynamic(() => import("@/components/RouteMap"), { ssr: false });

const MODE_LABEL: Record<string, string> = {
  walk: "Walk",
  bike: "My bike",
  ebike: "My e-bike",
  bikeshare: "Bike Share",
  drive: "Drive",
  rideshare: "Rideshare",
  transit: "TTC transit",
  park_ride: "Park & ride",
};
const COLORS: Record<string, string> = {
  walk: "#6b7280",
  bike: "#16a34a",
  ebike: "#0d9488",
  bikeshare: "#65a30d",
  drive: "#dc2626",
  rideshare: "#db2777",
  transit_rail: "#2563eb",
  transit_rail_lowwalk: "#0891b2",
  transit_bus: "#7c3aed",
  park_ride: "#ea580c",
};
const STOP_TYPES: [StopType, string][] = [
  ["stop", "Stop"],
  ["via", "Via"],
  ["pickup", "Pick up"],
  ["dropoff", "Drop off"],
  ["errand", "Errand"],
  ["park", "Park my car here"],
  ["alight", "Get off transit here"],
];
const VEHICLES = ["transit", "bus", "subway", "streetcar", "train"] as const;
const END_TYPES: Record<"origin" | "destination", [Exclude<EndType, null>, string][]> = {
  origin: [["pickup", "A ride picks me up here"]],
  destination: [
    ["dropoff", "I am dropped off here"],
    ["park", "I park my car here"],
  ],
};
const LETTERS = "ABCDEFGHIJ";
const TOGGLES: [keyof Constraints, string][] = [
  ["no_presto", "No Presto card"],
  ["has_car", "I have a car"],
  ["ev", "Car is electric"],
  ["has_bike", "I have a bike"],
  ["has_ebike", "I have an e-bike"],
  ["avoid_stairs", "Avoid stairs"],
  ["start_in_car", "I start in my car"],
  ["car_needed_after", "I need my car after"],
  ["plan_car_return", "Plan the way back to my car"],
];

declare global {
  interface Window {
    __4ward?: Record<string, unknown>;
  }
}

/** Stable, reversible id for a lot name so bots (and tests) can target a specific button. */
function lotSlug(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

function EndTypes({ side, value, onChange }: { side: "origin" | "destination"; value: EndType; onChange: (v: EndType) => void }) {
  return (
    <div className="-mt-1 flex flex-wrap gap-1.5">
      {END_TYPES[side].map(([t, label]) => (
        <button
          key={t}
          type="button"
          data-testid={`${side}-type-${t}`}
          aria-pressed={value === t}
          onClick={() => onChange(value === t ? null : t)}
          className={`rounded-full border px-2.5 py-0.5 text-xs ${value === t ? "border-zinc-900 bg-zinc-900 text-white" : "border-zinc-200 bg-white text-zinc-700"}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function Place({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (v: string) => void }) {
  const [opts, setOpts] = useState<{ name: string }[]>([]);
  useEffect(() => {
    if (value.length < 2) return void setOpts([]);
    const t = setTimeout(() => suggest(value).then(setOpts), 120);
    return () => clearTimeout(t);
  }, [value]);
  return (
    <div className="relative">
      <label htmlFor={id} className="block text-xs font-medium text-zinc-600">
        {label}
      </label>
      <input
        id={id}
        data-testid={`${id}-input`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
        placeholder="e.g. Union Station or an address"
        className="mt-1 w-full rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-900 focus:border-zinc-400 focus:bg-white focus:outline-none"
      />
      {opts.length > 0 && value.toLowerCase() !== opts[0].name.toLowerCase() && (
        <ul data-testid={`${id}-suggestions`} className="absolute z-10 mt-1 w-full overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-lg">
          {opts.map((o, i) => (
            <li key={o.name}>
              <button
                type="button"
                data-testid={`${id}-option-${i}`}
                onClick={() => {
                  onChange(o.name);
                  setOpts([]);
                }}
                className="block w-full px-2 py-1 text-left text-sm text-zinc-900 hover:bg-zinc-100"
              >
                {o.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const BOT_BADGE: Record<string, { name: string; color: string }> = {
  cheap: { name: "Economic bot", color: "#b45309" },
  fastest: { name: "Efficient bot", color: "#dc2626" },
  green: { name: "Environmental bot", color: "#047857" },
  reliable: { name: "Reliable bot", color: "#7c3aed" },
};

export default function Planner() {
  const [bot, setBot] = useState<string | null>(null);
  useEffect(() => setBot(new URLSearchParams(window.location.search).get("bot")), []);
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [c, setC] = useState<Constraints>({
    modes: ["walk", "bikeshare", "transit"],
    bags: 0,
    no_presto: false,
    has_car: false,
    ev: false,
    has_bike: false,
    has_ebike: false,
    avoid_stairs: false,
    depart_at: "",
    arrive_by: "",
    park_ride_lot: null,
  });
  const [stops, setStops] = useState<StopInput[]>([]);
  const [optimizeOrder, setOptimizeOrder] = useState(false);
  const [originType, setOriginType] = useState<EndType>(null);
  const [destinationType, setDestinationType] = useState<EndType>(null);
  const [lots, setLots] = useState<string[]>([]);
  const updateStop = (i: number, patch: Partial<StopInput>) => setStops((s) => s.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const moveStop = (i: number, by: number) =>
    setStops((s) => {
      const j = i + by;
      if (j < 0 || j >= s.length) return s;
      const next = [...s];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  useEffect(() => {
    getAssumptions().then((a) => setLots(a.park_ride_lots));
  }, []);
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [error, setError] = useState("");
  const [result, setResult] = useState<RouteResult | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState<Candidate | null>(null);

  const compute = useCallback(async () => {
    setState("loading");
    setError("");
    setSelected(null);
    setConfirmed(null);
    try {
      const r = await computeRoute(origin, destination, {
        ...c,
        depart_at: c.depart_at || null,
        arrive_by: c.arrive_by || null,
        as_of: c.as_of || null,
      }, stops, optimizeOrder, originType, destinationType);
      setResult(r);
      setState("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("error");
    }
  }, [origin, destination, c, stops, optimizeOrder, originType, destinationType]);

  useEffect(() => {
    window.__4ward = { state, error, origin, destination, originType, destinationType, stops, selected, confirmed, candidates: result?.candidates ?? [] };
  }, [state, error, origin, destination, originType, destinationType, stops, selected, confirmed, result]);

  // A trip with stops can have dozens of options; drawing every one makes the map crawl (a bot's headless browser
  // most of all), so with many options only the selected one is drawn.
  const mapRoutes = useMemo(
    () =>
      (result?.candidates ?? [])
        .filter((x) => x.available && x.legs.length)
        .filter((x, _i, all) => all.length <= 8 || x.id === selected)
        .map((x) => ({
          id: x.id,
          color: COLORS[x.id] ?? "#2563eb",
          candidate: x,
          emphasized: selected ? x.id === selected : true,
        })),
    [result, selected],
  );
  const waypoints = useMemo(() => (result?.points ?? []).slice(1, -1).map((p) => ({ lat: p.lat, lon: p.lon, name: p.name })), [result]);
  const toggleMode = (m: string) =>
    setC((p) => ({ ...p, modes: p.modes.includes(m) ? p.modes.filter((x) => x !== m) : [...p.modes, m] }));

  return (
    <div className="flex h-screen w-screen flex-col bg-zinc-100 text-zinc-900 md:flex-row" data-testid="planner">
      <aside className="flex w-full flex-col gap-3 overflow-y-auto bg-white p-5 shadow-lg md:z-10 md:w-[26rem]">
        <h1 className="text-lg font-semibold tracking-tight">4WARD route planner</h1>
        {bot && BOT_BADGE[bot] && (
          <p data-testid="bot-badge" className="rounded-full px-3 py-1 text-xs font-semibold text-white" style={{ background: BOT_BADGE[bot].color }}>
            Driven by: {BOT_BADGE[bot].name}
          </p>
        )}
        <Place id="origin" label="From (A)" value={origin} onChange={setOrigin} />
        <EndTypes side="origin" value={originType} onChange={setOriginType} />
        <section aria-label="Stops" className="flex flex-col gap-2" data-testid="stops">
          {stops.map((s, i) => (
            <div key={i} className="rounded-xl border border-zinc-200 p-2" data-testid={`stop-${i}`}>
              <Place id={`stop-${i}`} label={`Stop ${LETTERS[i + 1]}`} value={s.name} onChange={(v) => updateStop(i, { name: v })} />
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
                <label className="flex items-center gap-1">
                  Stay (min)
                  <input
                    data-testid={`stop-${i}-dwell`}
                    type="number"
                    min={0}
                    max={600}
                    value={s.dwell_min}
                    disabled={s.type === "via"}
                    onChange={(e) => updateStop(i, { dwell_min: Math.max(0, Number(e.target.value) || 0) })}
                    className="w-16 rounded-lg border border-zinc-200 px-2 py-1 disabled:opacity-50"
                  />
                </label>
                <label className="flex items-center gap-1">
                  Meet from
                  <input data-testid={`stop-${i}-window-start`} value={s.window_start ?? ""} placeholder="HH:MM" onChange={(e) => updateStop(i, { window_start: e.target.value })} className="w-16 rounded-lg border border-zinc-200 px-2 py-1" />
                </label>
                <label className="flex items-center gap-1">
                  to
                  <input data-testid={`stop-${i}-window-end`} value={s.window_end ?? ""} placeholder="HH:MM" onChange={(e) => updateStop(i, { window_end: e.target.value })} className="w-16 rounded-lg border border-zinc-200 px-2 py-1" />
                </label>
                {STOP_TYPES.map(([t, label]) => (
                  <button
                    key={t}
                    type="button"
                    data-testid={`stop-${i}-type-${t}`}
                    aria-pressed={s.type === t}
                    onClick={() => updateStop(i, { type: t, ...(t === "via" ? { dwell_min: 0 } : {}) })}
                    className={`rounded-full border px-2 py-0.5 ${s.type === t ? "border-zinc-900 bg-zinc-900 text-white" : "border-zinc-200 bg-white text-zinc-700"}`}
                  >
                    {label}
                  </button>
                ))}
                {s.type === "alight" &&
                  VEHICLES.map((v) => (
                    <button
                      key={v}
                      type="button"
                      data-testid={`stop-${i}-vehicle-${v}`}
                      aria-pressed={(s.vehicle ?? "transit") === v}
                      onClick={() => updateStop(i, { vehicle: v })}
                      className={`rounded-full border px-2 py-0.5 ${(s.vehicle ?? "transit") === v ? "border-sky-800 bg-sky-800 text-white" : "border-zinc-200 bg-white text-zinc-700"}`}
                    >
                      {v}
                    </button>
                  ))}
                <span className="ml-auto flex gap-1">
                  <button type="button" data-testid={`stop-${i}-up`} aria-label={`Move stop ${LETTERS[i + 1]} earlier`} disabled={i === 0} onClick={() => moveStop(i, -1)} className="rounded border border-zinc-200 px-1.5 disabled:opacity-40">↑</button>
                  <button type="button" data-testid={`stop-${i}-down`} aria-label={`Move stop ${LETTERS[i + 1]} later`} disabled={i === stops.length - 1} onClick={() => moveStop(i, 1)} className="rounded border border-zinc-200 px-1.5 disabled:opacity-40">↓</button>
                  <button type="button" data-testid={`stop-${i}-remove`} aria-label={`Remove stop ${LETTERS[i + 1]}`} onClick={() => setStops((all) => all.filter((_, j) => j !== i))} className="rounded border border-zinc-200 px-1.5">✕</button>
                </span>
              </div>
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="add-stop"
              disabled={stops.length >= 6}
              onClick={() => setStops((s) => [...s, { name: "", type: "stop", dwell_min: 0 }])}
              className="rounded-full border border-zinc-300 px-3 py-1 text-xs font-medium text-zinc-800 disabled:opacity-50"
            >
              + Add a stop
            </button>
            {stops.length > 1 && (
              <button
                type="button"
                data-testid="toggle-optimize_order"
                aria-pressed={optimizeOrder}
                onClick={() => setOptimizeOrder((v) => !v)}
                title="Off by default: your stops are visited in the order you typed them. On: every order is tried (up to 3 stops)."
                className={`rounded-full border px-3 py-1 text-xs ${optimizeOrder ? "border-zinc-900 bg-zinc-900 text-white" : "border-zinc-200 bg-white text-zinc-700"}`}
              >
                Let the planner reorder stops
              </button>
            )}
          </div>
        </section>
        <Place id="destination" label={`To (${LETTERS[stops.length + 1]})`} value={destination} onChange={setDestination} />
        <EndTypes side="destination" value={destinationType} onChange={setDestinationType} />

        <fieldset>
          <legend className="text-xs font-medium text-zinc-600">Travel modes to consider</legend>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {ALL_MODES.map((m) => (
              <button
                key={m}
                type="button"
                data-testid={`mode-toggle-${m}`}
                aria-pressed={c.modes.includes(m)}
                onClick={() => toggleMode(m)}
                className={`rounded-full border px-3 py-1 text-xs ${c.modes.includes(m) ? "border-zinc-900 bg-zinc-900 text-white" : "border-zinc-200 bg-white text-zinc-700"}`}
              >
                {MODE_LABEL[m]}
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend className="text-xs font-medium text-zinc-600">Constraints</legend>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {TOGGLES.map(([k, label]) => (
              <button
                key={k}
                type="button"
                data-testid={`toggle-${k}`}
                aria-pressed={Boolean(c[k])}
                onClick={() => setC((p) => ({ ...p, [k]: !p[k] }))}
                className={`rounded-full border px-3 py-1 text-xs ${c[k] ? "border-emerald-700 bg-emerald-700 text-white" : "border-zinc-200 bg-white text-zinc-700"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
            <label>
              Bags
              <input
                data-testid="bags-input"
                type="number"
                min={0}
                max={6}
                value={c.bags}
                onChange={(e) => setC((p) => ({ ...p, bags: Number(e.target.value) || 0 }))}
                className="mt-1 w-full rounded-lg border border-zinc-200 px-2 py-1.5"
              />
            </label>
            <label>
              Depart (HH:MM)
              <input
                data-testid="depart-at-input"
                value={c.depart_at ?? ""}
                placeholder="now"
                onChange={(e) => setC((p) => ({ ...p, depart_at: e.target.value }))}
                className="mt-1 w-full rounded-lg border border-zinc-200 px-2 py-1.5"
              />
            </label>
            <label>
              Now (HH:MM)
              <input
                data-testid="as-of-input"
                value={c.as_of ?? ""}
                placeholder="real time"
                title="Leave blank to use the real current time. Bots pin it so their results match the engine."
                onChange={(e) => setC((p) => ({ ...p, as_of: e.target.value }))}
                className="mt-1 w-full rounded-lg border border-zinc-200 px-2 py-1.5"
              />
            </label>
            <label>
              Arrive by
              <input
                data-testid="arrive-by-input"
                value={c.arrive_by ?? ""}
                placeholder="optional"
                onChange={(e) => setC((p) => ({ ...p, arrive_by: e.target.value }))}
                className="mt-1 w-full rounded-lg border border-zinc-200 px-2 py-1.5"
              />
            </label>
          </div>
        </fieldset>

        {c.modes.includes("park_ride") && lots.length > 0 && (
          <fieldset>
            <legend className="text-xs font-medium text-zinc-600">
              Park &amp; ride lot <span className="text-zinc-400">(which station to drive to is not something the engine searches on its own)</span>
            </legend>
            <div className="mt-1 flex flex-wrap gap-1.5">
              <button
                type="button"
                data-testid="park-ride-lot-auto"
                aria-pressed={!c.park_ride_lot}
                onClick={() => setC((p) => ({ ...p, park_ride_lot: null }))}
                className={`rounded-full border px-3 py-1 text-xs ${!c.park_ride_lot ? "border-orange-700 bg-orange-700 text-white" : "border-zinc-200 bg-white text-zinc-700"}`}
              >
                Auto (nearest)
              </button>
              {lots.map((name) => (
                <button
                  key={name}
                  type="button"
                  data-testid={`park-ride-lot-${lotSlug(name)}`}
                  aria-pressed={c.park_ride_lot === name}
                  onClick={() => setC((p) => ({ ...p, park_ride_lot: name }))}
                  className={`rounded-full border px-3 py-1 text-xs ${c.park_ride_lot === name ? "border-orange-700 bg-orange-700 text-white" : "border-zinc-200 bg-white text-zinc-700"}`}
                >
                  {name}
                </button>
              ))}
            </div>
          </fieldset>
        )}

        <button
          type="button"
          data-testid="compute-route"
          onClick={compute}
          disabled={!origin || !destination || state === "loading"}
          className="rounded-full bg-zinc-900 px-3 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
        >
          {state === "loading" ? "Computing…" : "Compute route"}
        </button>

        <div data-testid="route-status" data-state={confirmed ? "confirmed" : state} role="status" aria-live="polite" className="text-sm">
          {state === "error" && <p className="text-red-700">Error: {error}</p>}
          {confirmed && <p className="font-semibold text-emerald-700">Confirmed: {confirmed.title}</p>}
        </div>

        <section aria-label="Route options" className="flex flex-col gap-2">
          {result?.candidates.map((x) => (
            <article
              key={x.id}
              data-testid={`route-card-${x.id}`}
              data-available={x.available}
              className={`rounded-xl border p-3 text-sm ${selected === x.id ? "border-zinc-900 ring-2 ring-zinc-300" : "border-zinc-200"} ${x.available ? "" : "opacity-60"}`}
            >
              <div className="flex items-center justify-between">
                <h2 className="flex items-center gap-2 font-semibold">
                  <span className="inline-block h-3 w-3 rounded-full" style={{ background: COLORS[x.id] ?? "#999" }} />
                  {x.title}
                </h2>
                {x.available && (
                  <button
                    type="button"
                    data-testid={`select-route-${x.id}`}
                    onClick={() => setSelected(x.id)}
                    className="rounded-full border border-zinc-900 px-2.5 py-0.5 text-xs font-medium text-zinc-900"
                  >
                    {selected === x.id ? "Selected" : "Select"}
                  </button>
                )}
              </div>
              {x.available ? (
                <>
                  <dl className="mt-1 grid grid-cols-4 gap-1 text-xs">
                    <div>
                      <dt className="text-zinc-500">Time</dt>
                      <dd data-testid={`${x.id}-time`}>{Math.round(x.duration_min ?? 0)} min</dd>
                    </div>
                    <div>
                      <dt className="text-zinc-500">Cost</dt>
                      <dd data-testid={`${x.id}-cost`}>${(x.cost ?? 0).toFixed(2)}</dd>
                    </div>
                    <div>
                      <dt className="text-zinc-500">CO₂</dt>
                      <dd data-testid={`${x.id}-co2`}>{(x.co2_kg ?? 0).toFixed(2)} kg</dd>
                    </div>
                    <div>
                      <dt className="text-zinc-500">Transfers</dt>
                      <dd>{x.transfers}</dd>
                    </div>
                  </dl>
                  <p className="mt-1 text-xs text-zinc-600">
                    {timingLine(x)} · walk {x.walk_m} m
                  </p>
                  {x.schedule && (
                    <ol className="mt-1 text-xs text-zinc-700" data-testid={`schedule-${x.id}`}>
                      {x.schedule.map((v) => (
                        <li key={v.label}>
                          <b>{v.label}</b> {v.name}: {v.arrive ? `arrive ${v.arrive}` : "start"}
                          {v.wait_min > 0 ? `, wait ${Math.round(v.wait_min)} min` : ""}
                          {v.dwell_min > 0 ? `, stay ${Math.round(v.dwell_min)} min` : ""}
                          {v.depart ? `, leave ${v.depart}` : ""}
                          {v.traveler ? ` · ${v.traveler.replace("_", " ")}` : ""}
                          {v.car ? ` · car: ${v.car}` : ""}
                        </li>
                      ))}
                    </ol>
                  )}
                  {x.notes?.map((n) => (
                    <p key={n} className="text-xs text-zinc-500">
                      • {n}
                    </p>
                  ))}
                </>
              ) : (
                <p className="text-xs text-zinc-600">Unavailable: {x.reason}</p>
              )}
            </article>
          ))}
        </section>

        {selected && !confirmed && (
          <button
            type="button"
            data-testid="confirm-route"
            onClick={() => setConfirmed(result!.candidates.find((x) => x.id === selected) ?? null)}
            className="rounded-full bg-emerald-700 px-3 py-2.5 text-sm font-semibold text-white"
          >
            Confirm this route
          </button>
        )}
        {result && result.candidates.filter((x) => x.available).length > 8 && !selected && (
          <p className="text-xs text-zinc-500" data-testid="map-hint">Select a route to see it on the map ({result.candidates.length} options).</p>
        )}
        {result && <p className="text-xs text-zinc-500">{result.data_notes.join(" · ")}</p>}
      </aside>
      <main className="relative min-h-[50vh] flex-1">
        <RouteMap
          routes={mapRoutes}
          origin={result ? [result.origin_place.lat, result.origin_place.lon] : null}
          destination={result ? [result.destination_place.lat, result.destination_place.lon] : null}
          waypoints={waypoints}
        />
      </main>
    </div>
  );
}

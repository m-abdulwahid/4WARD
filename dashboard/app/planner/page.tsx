"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { ALL_MODES, Candidate, Constraints, RouteResult, computeRoute, suggest } from "@/lib/api";

const RouteMap = dynamic(() => import("@/components/RouteMap"), { ssr: false });

const MODE_LABEL: Record<string, string> = {
  walk: "Walk",
  bike: "My bike",
  ebike: "My e-bike",
  bikeshare: "Bike Share",
  drive: "Drive",
  transit: "TTC transit",
  park_ride: "Park & ride",
};
const COLORS: Record<string, string> = {
  walk: "#6b7280",
  bike: "#16a34a",
  ebike: "#0d9488",
  bikeshare: "#65a30d",
  drive: "#dc2626",
  transit_rail: "#2563eb",
  transit_bus: "#7c3aed",
  park_ride: "#ea580c",
};
const TOGGLES: [keyof Constraints, string][] = [
  ["no_presto", "No Presto card"],
  ["has_car", "I have a car"],
  ["ev", "Car is electric"],
  ["has_bike", "I have a bike"],
  ["has_ebike", "I have an e-bike"],
  ["avoid_stairs", "Avoid stairs"],
];

declare global {
  interface Window {
    __4ward?: Record<string, unknown>;
  }
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
        className="mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm text-zinc-900"
      />
      {opts.length > 0 && value.toLowerCase() !== opts[0].name.toLowerCase() && (
        <ul data-testid={`${id}-suggestions`} className="absolute z-10 mt-1 w-full rounded border bg-white shadow">
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

export default function Planner() {
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
  });
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
      });
      setResult(r);
      setState("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("error");
    }
  }, [origin, destination, c]);

  useEffect(() => {
    window.__4ward = { state, error, origin, destination, selected, confirmed, candidates: result?.candidates ?? [] };
  }, [state, error, origin, destination, selected, confirmed, result]);

  const mapRoutes = useMemo(
    () =>
      (result?.candidates ?? [])
        .filter((x) => x.available && x.legs.length)
        .map((x) => ({
          id: x.id,
          color: COLORS[x.id] ?? "#2563eb",
          candidate: x,
          emphasized: selected ? x.id === selected : true,
        })),
    [result, selected],
  );
  const toggleMode = (m: string) =>
    setC((p) => ({ ...p, modes: p.modes.includes(m) ? p.modes.filter((x) => x !== m) : [...p.modes, m] }));

  return (
    <div className="flex h-screen w-screen flex-col bg-zinc-100 text-zinc-900 md:flex-row" data-testid="planner">
      <aside className="flex w-full flex-col gap-3 overflow-y-auto bg-white p-4 md:w-[26rem]">
        <h1 className="text-lg font-bold">4WARD route planner</h1>
        <Place id="origin" label="From" value={origin} onChange={setOrigin} />
        <Place id="destination" label="To" value={destination} onChange={setDestination} />

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
                className={`rounded-full border px-3 py-1 text-xs ${c.modes.includes(m) ? "border-blue-600 bg-blue-600 text-white" : "border-zinc-300 bg-white text-zinc-700"}`}
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
                className={`rounded-full border px-3 py-1 text-xs ${c[k] ? "border-emerald-600 bg-emerald-600 text-white" : "border-zinc-300 bg-white text-zinc-700"}`}
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
                className="mt-1 w-full rounded border border-zinc-300 px-2 py-1"
              />
            </label>
            <label>
              Depart (HH:MM)
              <input
                data-testid="depart-at-input"
                value={c.depart_at ?? ""}
                placeholder="now"
                onChange={(e) => setC((p) => ({ ...p, depart_at: e.target.value }))}
                className="mt-1 w-full rounded border border-zinc-300 px-2 py-1"
              />
            </label>
            <label>
              Arrive by
              <input
                data-testid="arrive-by-input"
                value={c.arrive_by ?? ""}
                placeholder="optional"
                onChange={(e) => setC((p) => ({ ...p, arrive_by: e.target.value }))}
                className="mt-1 w-full rounded border border-zinc-300 px-2 py-1"
              />
            </label>
          </div>
        </fieldset>

        <button
          type="button"
          data-testid="compute-route"
          onClick={compute}
          disabled={!origin || !destination || state === "loading"}
          className="rounded bg-blue-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
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
              className={`rounded border p-2 text-sm ${selected === x.id ? "border-blue-600 ring-2 ring-blue-300" : "border-zinc-200"} ${x.available ? "" : "opacity-60"}`}
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
                    className="rounded border border-blue-600 px-2 py-0.5 text-xs text-blue-700"
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
                    {x.depart} → {x.arrive} · walk {x.walk_m} m
                    {x.meets_deadline !== undefined &&
                      (x.meets_deadline ? " · makes your deadline" : ` · MISSES deadline (leave by ${x.leave_by})`)}
                  </p>
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
            className="rounded bg-emerald-700 px-3 py-2 text-sm font-semibold text-white"
          >
            Confirm this route
          </button>
        )}
        {result && <p className="text-xs text-zinc-500">{result.data_notes.join(" · ")}</p>}
      </aside>
      <main className="relative min-h-[50vh] flex-1">
        <RouteMap
          routes={mapRoutes}
          origin={result ? [result.origin_place.lat, result.origin_place.lon] : null}
          destination={result ? [result.destination_place.lat, result.destination_place.lon] : null}
        />
      </main>
    </div>
  );
}

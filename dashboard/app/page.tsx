"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { API, Candidate } from "@/lib/api";
import InsightsPanel from "@/components/InsightsPanel";

const RouteMap = dynamic(() => import("@/components/RouteMap"), { ssr: false });

const BOTS = [
  { key: "cheap", name: "Cheapest", color: "#16a34a", blurb: "lowest total cost" },
  { key: "fastest", name: "Fastest", color: "#dc2626", blurb: "shortest door-to-door time" },
  { key: "green", name: "Greenest", color: "#0d9488", blurb: "lowest CO₂" },
  { key: "best", name: "Best overall", color: "#7c3aed", blurb: "balanced trade-offs" },
] as const;
type BotKey = (typeof BOTS)[number]["key"];

type BotState = {
  status: "WAITING" | "SCANNING" | "ROUTING" | "DONE" | "FAILED";
  frame?: string;
  lastReason?: string;
  steps: number;
  brain?: string;
  elapsed?: number;
  route?: Candidate | null;
  error?: string | null;
};
const blank = (): Record<BotKey, BotState> => ({
  cheap: { status: "WAITING", steps: 0 },
  fastest: { status: "WAITING", steps: 0 },
  green: { status: "WAITING", steps: 0 },
  best: { status: "WAITING", steps: 0 },
});

const EXAMPLES = [
  "Get me from York University to Union Station by 5pm. I have 2 big bags, no Presto card, but I have my car.",
  "I'm at Kipling Station and need to reach the CN Tower by 6pm with a suitcase. No Presto.",
  "From the Beaches to Pearson Airport, leaving at 7am, I have 2 suitcases and an e-bike.",
];

const STATUS_STYLE: Record<string, string> = {
  WAITING: "bg-zinc-200 text-zinc-700",
  SCANNING: "bg-amber-100 text-amber-800",
  ROUTING: "bg-sky-100 text-sky-800",
  DONE: "bg-emerald-100 text-emerald-800",
  FAILED: "bg-red-100 text-red-800",
};

function Bar({ label, value, max, color, text }: { label: string; value: number; max: number; color: string; text: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-24 shrink-0 text-zinc-600">{label}</span>
      <div className="h-4 flex-1 rounded bg-zinc-100">
        <div className="h-4 rounded" style={{ width: `${Math.max(3, (value / (max || 1)) * 100)}%`, background: color }} />
      </div>
      <span className="w-20 shrink-0 text-right tabular-nums text-zinc-800">{text}</span>
    </div>
  );
}

export default function Home() {
  const [prompt, setPrompt] = useState(EXAMPLES[0]);
  const [bots, setBots] = useState<Record<BotKey, BotState>>(blank);
  const [running, setRunning] = useState(false);
  const [parsed, setParsed] = useState<{ origin: string; destination: string; parser?: string; constraints?: Record<string, unknown> } | null>(null);
  const [error, setError] = useState("");
  const [chosen, setChosen] = useState<BotKey | null>(null);
  const [health, setHealth] = useState<{ replay: boolean; gemini_configured: boolean } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const t0 = useRef(0);

  useEffect(() => {
    fetch(`${API}/health`).then((r) => r.json()).then(setHealth).catch(() => setHealth(null));
  }, []);
  useEffect(() => {
    if (!running) return;
    const i = setInterval(() => setElapsed(Math.round((Date.now() - t0.current) / 100) / 10), 100);
    return () => clearInterval(i);
  }, [running]);

  const patch = useCallback((k: BotKey, p: Partial<BotState>) => setBots((b) => ({ ...b, [k]: { ...b[k], ...p } })), []);

  const start = useCallback(async () => {
    wsRef.current?.close();
    setError("");
    setChosen(null);
    setParsed(null);
    setBots(blank());
    setRunning(true);
    t0.current = Date.now();
    try {
      const r = await fetch(`${API}/run`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ prompt }) });
      if (!r.ok) throw new Error(`Backend refused the run (${r.status})`);
      const { id } = await r.json();
      const ws = new WebSocket(`${API.replace(/^http/, "ws")}/ws/run/${id}`);
      wsRef.current = ws;
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        const k = ev.bot as BotKey;
        if (ev.type === "parsed") setParsed(ev);
        else if (ev.type === "frame") patch(k, { frame: ev.data });
        else if (ev.type === "status") patch(k, { status: ev.status });
        else if (ev.type === "step") setBots((b) => ({ ...b, [k]: { ...b[k], steps: ev.n, lastReason: ev.reason ?? b[k].lastReason } }));
        else if (ev.type === "result") patch(k, { status: ev.status, route: ev.route, brain: ev.brain, elapsed: ev.elapsed_s, error: ev.error });
        else if (ev.type === "run_done") {
          setRunning(false);
          if (ev.result?.error) setError(ev.result.error);
          ws.close();
        }
      };
      ws.onerror = () => {
        setError("Lost connection to the backend.");
        setRunning(false);
      };
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setRunning(false);
    }
  }, [prompt, patch]);

  const done = BOTS.filter((b) => bots[b.key].route);
  const mapRoutes = useMemo(
    () =>
      done.map((b) => ({ id: b.key, color: b.color, candidate: bots[b.key].route as Candidate, emphasized: chosen ? chosen === b.key : true })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [bots, chosen],
  );
  const first = done[0] ? bots[done[0].key].route : null;
  const maxT = Math.max(...done.map((b) => bots[b.key].route!.duration_min ?? 0), 1);
  const maxC = Math.max(...done.map((b) => bots[b.key].route!.cost ?? 0), 1);
  const maxG = Math.max(...done.map((b) => bots[b.key].route!.co2_kg ?? 0), 0.01);
  const pick = chosen ? bots[chosen].route : null;

  return (
    <div className="min-h-screen bg-zinc-100 text-zinc-900">
      <header className="bg-zinc-900 px-4 py-3 text-white">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-3">
          <h1 className="text-xl font-extrabold tracking-tight">
            4WARD<span className="text-emerald-400">.</span>
          </h1>
          <p className="hidden text-sm text-zinc-300 md:block">Four AI bots plan your Toronto trip in parallel. You choose.</p>
          <div className="ml-auto flex gap-2 text-xs">
            {health && (
              <>
                <span className={`rounded px-2 py-1 ${health.gemini_configured ? "bg-emerald-600" : "bg-zinc-700"}`}>
                  {health.gemini_configured ? "Gemini-driven bots" : "Rule-based bots (no Gemini key)"}
                </span>
                <span className={`rounded px-2 py-1 ${health.replay ? "bg-amber-600" : "bg-emerald-700"}`}>{health.replay ? "REPLAY data" : "LIVE data"}</span>
              </>
            )}
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1500px] p-3 md:p-4">
        <form
          className="flex flex-col gap-2 rounded-lg bg-white p-3 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            if (!running && prompt.trim()) start();
          }}
        >
          <label htmlFor="prompt" className="text-sm font-medium">
            Where are you going, and what should we know?
          </label>
          <div className="flex flex-col gap-2 md:flex-row">
            <textarea
              id="prompt"
              data-testid="prompt-input"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={2}
              className="flex-1 rounded border border-zinc-300 p-2 text-sm"
            />
            <button
              type="submit"
              data-testid="plan-trip"
              disabled={running || !prompt.trim()}
              className="rounded bg-emerald-700 px-6 py-2 text-sm font-semibold text-white disabled:opacity-50 md:w-48"
            >
              {running ? `Bots working… ${elapsed}s` : "Plan my trip"}
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((x, i) => (
              <button key={i} type="button" onClick={() => setPrompt(x)} className="rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-700 hover:bg-zinc-50">
                Example {i + 1}
              </button>
            ))}
          </div>
          {parsed && (
            <p className="text-xs text-zinc-600" data-testid="parsed-summary">
              Understood ({parsed.parser}): <b>{parsed.origin}</b> → <b>{parsed.destination}</b>
              {parsed.constraints && ` · ${Object.entries(parsed.constraints).filter(([, v]) => v && v !== 0).map(([k, v]) => (v === true ? k.replace("_", " ") : `${k.replace("_", " ")} ${v}`)).join(", ")}`}
            </p>
          )}
          {error && (
            <p role="alert" className="rounded bg-red-50 p-2 text-sm text-red-800">
              {error}
            </p>
          )}
        </form>

        <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <section aria-label="Map of all routes" className="h-[420px] overflow-hidden rounded-lg bg-white shadow-sm lg:h-[620px]">
            <RouteMap
              routes={mapRoutes}
              origin={first ? first.legs[0]?.from : null}
              destination={first ? first.legs[first.legs.length - 1]?.to : null}
            />
          </section>

          <section aria-label="Bot live views" className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {BOTS.map((b) => {
              const s = bots[b.key];
              return (
                <article key={b.key} data-testid={`bot-${b.key}`} className={`flex flex-col overflow-hidden rounded-lg bg-white shadow-sm ring-2 ${chosen === b.key ? "" : "ring-transparent"}`} style={chosen === b.key ? { boxShadow: `0 0 0 3px ${b.color}` } : undefined}>
                  <div className="flex items-center justify-between px-3 py-2" style={{ borderTop: `4px solid ${b.color}` }}>
                    <div>
                      <h2 className="text-sm font-bold">{b.name}</h2>
                      <p className="text-[11px] text-zinc-500">{b.blurb}</p>
                    </div>
                    <span data-testid={`bot-${b.key}-status`} className={`rounded px-2 py-0.5 text-[11px] font-semibold ${STATUS_STYLE[s.status]}`}>
                      {s.status}
                    </span>
                  </div>
                  <div className="relative aspect-[1000/640] bg-zinc-200">
                    {s.frame ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={`data:image/jpeg;base64,${s.frame}`} alt={`${b.name} bot's browser view`} className="h-full w-full object-cover" />
                    ) : (
                      <div className="flex h-full items-center justify-center text-xs text-zinc-500">{running ? "Starting browser…" : "Waiting for a trip"}</div>
                    )}
                  </div>
                  <div className="px-3 py-2 text-xs">
                    {s.status === "FAILED" ? (
                      <p className="text-red-700">Failed: {s.error ?? "unknown error"}. The other bots are unaffected.</p>
                    ) : s.route ? (
                      <>
                        <p className="font-semibold">{s.route.title}</p>
                        <p className="tabular-nums text-zinc-700">
                          {Math.round(s.route.duration_min ?? 0)} min · ${(s.route.cost ?? 0).toFixed(2)} · {(s.route.co2_kg ?? 0).toFixed(2)} kg CO₂
                        </p>
                        <button type="button" data-testid={`choose-${b.key}`} onClick={() => setChosen(b.key)} className="mt-1 rounded border px-2 py-0.5 text-xs font-medium" style={{ borderColor: b.color, color: b.color }}>
                          {chosen === b.key ? "Chosen ✓" : "Choose this route"}
                        </button>
                      </>
                    ) : (
                      <p className="line-clamp-2 text-zinc-600">{s.lastReason ?? (running ? "Thinking…" : "Idle")}</p>
                    )}
                  </div>
                </article>
              );
            })}
          </section>
        </div>

        {done.length > 0 && (
          <section aria-label="Comparison" className="mt-3 grid gap-3 lg:grid-cols-2">
            <div className="rounded-lg bg-white p-3 shadow-sm">
              <h2 className="mb-2 text-sm font-bold">Side by side</h2>
              {(
                [
                  ["Time", maxT, (r: Candidate) => r.duration_min ?? 0, (v: number) => `${Math.round(v)} min`],
                  ["Cost", maxC, (r: Candidate) => r.cost ?? 0, (v: number) => `$${v.toFixed(2)}`],
                  ["CO₂", maxG, (r: Candidate) => r.co2_kg ?? 0, (v: number) => `${v.toFixed(2)} kg`],
                ] as const
              ).map(([label, max, get, fmt]) => (
                <div key={label} className="mb-3">
                  <h3 className="mb-1 text-xs font-semibold text-zinc-500">{label}</h3>
                  <div className="flex flex-col gap-1">
                    {done.map((b) => (
                      <Bar key={b.key} label={b.name} value={get(bots[b.key].route!)} max={max} color={b.color} text={fmt(get(bots[b.key].route!))} />
                    ))}
                  </div>
                </div>
              ))}
              <p className="text-[11px] text-zinc-500">Costs and traffic are estimates from assumptions shown in the planner; transit schedule data is from TTC GTFS.</p>
            </div>
            <div className="rounded-lg bg-white p-3 shadow-sm">
              <h2 className="mb-2 text-sm font-bold">{pick ? `Your pick: ${pick.title}` : "Pick a route to see the itinerary"}</h2>
              {pick ? (
                <ol className="flex flex-col gap-1 text-sm" data-testid="itinerary">
                  {pick.legs.map((l, i) => (
                    <li key={i} className="flex gap-2">
                      <span className="w-14 shrink-0 text-right tabular-nums text-zinc-500">{Math.round(l.duration_s / 60)} min</span>
                      <span>
                        {l.label}
                        {l.cost ? <span className="text-zinc-500"> · ${l.cost.toFixed(2)}</span> : null}
                      </span>
                    </li>
                  ))}
                  {pick.notes?.map((n) => (
                    <li key={n} className="mt-1 text-xs text-zinc-500">
                      • {n}
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="text-sm text-zinc-500">Each bot's route is drawn in its colour on the map. Choose one to highlight it.</p>
              )}
            </div>
          </section>
        )}

        <InsightsPanel />
      </div>
    </div>
  );
}

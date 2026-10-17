"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { API, Candidate } from "@/lib/api";
import { BOTS, BotKey, BotState, blankBots } from "@/lib/bots";
import InsightsPanel from "@/components/InsightsPanel";
import RouteCard from "@/components/RouteCard";
import BotDrawer from "@/components/BotDrawer";
import LayerToggles from "@/components/LayerToggles";
import MapLegend from "@/components/MapLegend";
import TripOptions, { Priority } from "@/components/TripOptions";
import ExampleTrips from "@/components/ExampleTrips";
import { ModeKey, legColor, legMode } from "@/lib/modes";
import { DbStatus, TONE, dbLabel } from "@/lib/dataSource";
import type { BikeStation, MapPadding } from "@/components/RouteMap";

const RouteMap = dynamic(() => import("@/components/RouteMap"), { ssr: false });

// Turn backend/network messages into what the user should do next.
function friendlyError(msg: string): string {
  if (/origin and destination/i.test(msg))
    return "We couldn't tell where you're starting and where you're going. Write it as \u201cA to B\u201d, for example \u201cYork U to CN Tower\u201d, then press Plan my trip again.";
  if (/failed to fetch|networkerror|load failed|refused the run/i.test(msg))
    return "We can't reach the 4WARD backend right now. Check that it is running (start.ps1), then press Plan my trip again.";
  if (/lost connection/i.test(msg)) return "The connection to the 4WARD backend dropped while the bots were working. Check that it is running, then press Plan my trip again.";
  if (/prompt is empty/i.test(msg)) return "Type a trip first, or pick an example.";
  return `Something went wrong (${msg}). Try rewording your trip, or start from an example.`;
}

type Place = { typed: string; found: { name: string; source: string } | null };

// Quick-add chips append a phrase the prompt parser already understands (bots/nlp.py).
// "test" decides whether the prompt already mentions it, so the chip shows as on.
const QUICK = [
  { key: "bags", label: "Bags", phrase: "I have 2 bags.", test: /\b(\d+|an?|one|two|three|four)\s+(?:big |large |heavy |carry-on )?(?:bags?|suitcases?|luggage)\b/i },
  { key: "presto", label: "No Presto card", phrase: "No Presto card.", test: /no presto|without (?:a )?presto|(?:don'?t|do not) have (?:a )?presto/i },
  { key: "deadline", label: "Deadline", phrase: "I need to arrive by 5pm.", test: /\b(?:by|before|arrive(?: at| by)?)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b/i },
  { key: "ebike", label: "E-bike", phrase: "I have an e-bike.", test: /e-?bike/i },
  { key: "budget", label: "Budget", phrase: "My budget is $10.", test: /budget/i },
] as const;

// The parser has no budget field, so the dashboard reads it here and flags routes the engine priced above it.
function readBudget(text: string): number | null {
  const m = text.match(/budget(?:\s+(?:is|of))?\s*(?:about|around|under)?\s*\$?\s*(\d+(?:\.\d+)?)/i) ?? text.match(/\bunder\s+\$(\d+(?:\.\d+)?)/i);
  return m ? Number(m[1]) : null;
}

const TABS = [
  { key: "map", label: "Map", icon: "M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2Zm0 0v14m6-12v14" },
  { key: "routes", label: "Routes", icon: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm12-10a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM6 15V9a4 4 0 0 1 4-4h4m4 4v6a4 4 0 0 1-4 4h-4" },
  { key: "insights", label: "Insights", icon: "M4 20V10m6 10V4m6 16v-7m4 7H2" },
] as const;
type Tab = (typeof TABS)[number]["key"];

const RAIL_W = 104; // left rail (72px) + gaps
const PANEL_W: Record<Tab, number> = { map: 400, routes: 400, insights: 600 };

function useWide() {
  const [wide, setWide] = useState(true);
  useEffect(() => {
    const q = window.matchMedia("(min-width: 768px)");
    const on = () => setWide(q.matches);
    on();
    q.addEventListener("change", on);
    return () => q.removeEventListener("change", on);
  }, []);
  return wide;
}

function Icon({ d }: { d: string }) {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}

export default function Home() {
  const [tab, setTab] = useState<Tab>("map");
  const [sheetFull, setSheetFull] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [prompt, setPrompt] = useState("");
  // what each typed place was matched to (the geocoder only searches inside Toronto)
  const [places, setPlaces] = useState<{ origin: Place; destination: Place } | null>(null);
  const [bots, setBots] = useState<Record<BotKey, BotState>>(blankBots);
  const [running, setRunning] = useState(false);
  const [parsed, setParsed] = useState<{ origin: string; destination: string; parser?: string; constraints?: Record<string, unknown> } | null>(null);
  const [error, setError] = useState("");
  const [focus, setFocus] = useState<BotKey | null>(null);
  const [chosen, setChosen] = useState<BotKey | null>(null);
  // undefined: still checking; null: backend unreachable
  const [health, setHealth] = useState<{ replay: boolean; gemini_configured: boolean } | null | undefined>(undefined);
  const [dbStatus, setDbStatus] = useState<DbStatus | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [budget, setBudget] = useState<number | null>(null);
  const [showTtc, setShowTtc] = useState(false);
  const [bikeStations, setBikeStations] = useState<BikeStation[] | null>(null);
  const [insightsSeen, setInsightsSeen] = useState(false);
  const [priority, setPriority] = useState<Priority>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const t0 = useRef(0);
  const wide = useWide();
  const closeDrawer = useCallback(() => setDrawer(false), []);

  useEffect(() => {
    fetch(`${API}/health`).then((r) => r.json()).then(setHealth).catch(() => setHealth(null));
    // the chart endpoint reports which database it actually read (Tiger Data or the local fallback)
    fetch(`${API}/insights/bikes?hours=1`)
      .then((r) => r.json())
      .then((d) => setDbStatus({ backend: d.backend, tiger: d.tiger }))
      .catch(() => setDbStatus(null));
  }, []);
  useEffect(() => {
    if (!running) return;
    const i = setInterval(() => setElapsed(Math.round((Date.now() - t0.current) / 100) / 10), 100);
    return () => clearInterval(i);
  }, [running]);

  const backToPrompt = () => {
    setTab("map");
    setTimeout(() => promptRef.current?.focus(), 50);
  };

  const checkPlaces = (origin: string, destination: string) => {
    const look = (q: string): Promise<Place> =>
      fetch(`${API}/geocode?q=${encodeURIComponent(q)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((g) => ({ typed: q, found: g ? { name: g.name, source: g.source } : null }));
    Promise.all([look(origin), look(destination)])
      .then(([o, d]) => setPlaces({ origin: o, destination: d }))
      .catch(() => {}); // backend errors surface through the run itself
  };

  const patch = useCallback((k: BotKey, p: Partial<BotState>) => setBots((b) => ({ ...b, [k]: { ...b[k], ...p } })), []);

  const start = useCallback(async () => {
    wsRef.current?.close();
    setError("");
    setChosen(null);
    setFocus(null);
    setParsed(null);
    setPlaces(null);
    setBots(blankBots());
    setBudget(readBudget(prompt));
    setRunning(true);
    setTab("routes");
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
        if (ev.type === "parsed") {
          setParsed(ev);
          if (ev.origin && ev.destination) checkPlaces(ev.origin, ev.destination);
        }
        else if (ev.type === "frame") patch(k, { frame: ev.data });
        else if (ev.type === "status") patch(k, { status: ev.status });
        else if (ev.type === "step") setBots((b) => ({ ...b, [k]: { ...b[k], steps: ev.n, lastReason: ev.reason ?? b[k].lastReason } }));
        else if (ev.type === "result") patch(k, { status: ev.status, route: ev.route, brain: ev.brain, elapsed: ev.elapsed_s, error: ev.error });
        else if (ev.type === "run_done") {
          setRunning(false);
          if (ev.result?.error) {
            setError(friendlyError(ev.result.error));
            if (/origin and destination/i.test(ev.result.error)) backToPrompt();
          }
          ws.close();
        }
      };
      ws.onerror = () => {
        setError(friendlyError("Lost connection to the backend."));
        setRunning(false);
      };
    } catch (e) {
      setError(friendlyError(e instanceof Error ? e.message : String(e)));
      setRunning(false);
      backToPrompt();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prompt, patch]);

  const done = BOTS.filter((b) => bots[b.key].route);
  const highlight = focus ?? chosen;

  // When the run finishes, open the route that best matches the user's priority (engine numbers only).
  useEffect(() => {
    if (running || !priority || focus || chosen) return;
    const ready = BOTS.filter((b) => bots[b.key].route);
    if (!ready.length) return;
    const by = (f: (r: Candidate) => number) => ready.reduce((a, b) => (f(bots[b.key].route!) < f(bots[a.key].route!) ? b : a)).key;
    const pick: BotKey =
      priority === "cheapest" ? by((r) => r.cost ?? Infinity)
      : priority === "fastest" ? by((r) => r.duration_min ?? Infinity)
      : priority === "greenest" ? by((r) => r.co2_kg ?? Infinity)
      : priority === "fewest_transfers" ? by((r) => r.transfers ?? Infinity)
      : by((r) => r.walk_m ?? Infinity);
    setFocus(pick);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  const mapRoutes = useMemo(
    () =>
      BOTS.filter((b) => bots[b.key].route).map((b) => ({
        id: b.key,
        color: b.color,
        label: b.name,
        candidate: bots[b.key].route as Candidate,
        emphasized: highlight ? highlight === b.key : true,
      })),
    [bots, highlight],
  );
  // Modes the finished routes use, with the colour each is drawn in (TTC line colour for transit).
  const usedModes = useMemo(() => {
    const out: Partial<Record<ModeKey, string>> = {};
    done.forEach((b) => {
      const c = bots[b.key].route as Candidate;
      c.legs.forEach((l) => {
        const m = legMode(l, c.id);
        out[m] ??= legColor(l, m);
      });
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bots]);
  const first = done[0] ? bots[done[0].key].route : null;

  // keep routes inside the part of the map that is not covered by the panel / sheet
  const mapPadding: MapPadding = wide
    ? { top: 60, right: 270, bottom: 60, left: RAIL_W + PANEL_W[tab] + 40 } // right: Layers + Legend column (224px + gaps)
    : { top: 110, right: 30, bottom: typeof window === "undefined" ? 400 : Math.round(window.innerHeight * (sheetFull ? 0.8 : 0.46)) + 110, left: 30 };

  const onTabKey = (e: React.KeyboardEvent, i: number) => {
    const dir = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (!dir) return;
    e.preventDefault();
    const next = TABS[(i + dir + TABS.length) % TABS.length].key;
    setTab(next);
    if (next === "insights") setInsightsSeen(true);
    document.getElementById(`tab-${next}`)?.focus();
  };

  const panelTitle = { map: "Plan a trip", routes: "Four routes", insights: "Insights" }[tab];

  return (
    <div className="fixed inset-0 overflow-hidden bg-zinc-100 text-zinc-900">
      <div className="absolute inset-0">
        <RouteMap
          routes={mapRoutes}
          origin={first ? first.legs[0]?.from : null}
          destination={first ? first.legs[first.legs.length - 1]?.to : null}
          padding={mapPadding}
          colorBy="mode"
          showTtc={showTtc}
          bikeStations={bikeStations}
        />
      </div>

      <div className="absolute top-3 right-3 z-10 flex flex-col items-end gap-2 md:top-4 md:right-4 md:items-stretch">
        <LayerToggles onTtc={setShowTtc} onBikes={setBikeStations} />
        <MapLegend found={{ cheap: !!bots.cheap.route, fastest: !!bots.fastest.route, green: !!bots.green.route, best: !!bots.best.route }} highlight={highlight} modes={usedModes} />
      </div>

      {/* One nav element: bottom tab bar on phones, slim left rail on wide screens */}
      <nav
        aria-label="Sections"
        className="absolute inset-x-0 bottom-0 z-20 flex h-16 items-stretch border-t border-zinc-200 bg-white md:inset-x-auto md:top-4 md:bottom-4 md:left-4 md:h-auto md:w-[72px] md:flex-col md:items-center md:gap-1 md:rounded-2xl md:border-0 md:py-3 md:shadow-lg md:ring-1 md:ring-black/5"
      >
        <p className="hidden pb-3 text-base font-extrabold tracking-tight md:block" aria-label="4WARD">
          4W<span className="text-emerald-600">.</span>
        </p>
        <div role="tablist" aria-orientation={wide ? "vertical" : "horizontal"} className="flex flex-1 md:flex-none md:flex-col md:gap-1">
          {TABS.map((t, i) => {
            const active = tab === t.key;
            return (
              <button
                key={t.key}
                id={`tab-${t.key}`}
                role="tab"
                type="button"
                data-testid={`tab-${t.key}`}
                aria-selected={active}
                aria-controls={`panel-${t.key}`}
                tabIndex={active ? 0 : -1}
                onClick={() => {
                  setTab(t.key);
                  if (t.key === "insights") setInsightsSeen(true);
                }}
                onKeyDown={(e) => onTabKey(e, i)}
                className={`relative flex flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-zinc-900 md:h-14 md:w-14 md:flex-none md:rounded-xl ${active ? "text-zinc-900 md:bg-zinc-100" : "text-zinc-500 hover:text-zinc-800"}`}
              >
                <Icon d={t.icon} />
                {t.label}
                {t.key === "routes" && running && <span aria-hidden className="absolute top-2 right-[calc(50%-18px)] h-2 w-2 rounded-full bg-sky-500 md:top-2 md:right-2" />}
              </button>
            );
          })}
        </div>
      </nav>

      {/* Bottom sheet on phones, floating panel on wide screens */}
      <section
        aria-label={panelTitle}
        className="absolute inset-x-0 bottom-16 z-10 flex h-[var(--sheet-h)] flex-col rounded-t-3xl bg-white shadow-[0_-8px_30px_rgba(0,0,0,0.08)] md:inset-x-auto md:top-4 md:bottom-auto md:left-[104px] md:h-auto md:max-h-[calc(100%-2rem)] md:w-[var(--panel-w)] md:rounded-2xl md:shadow-lg md:ring-1 md:ring-black/5"
        style={{ "--sheet-h": sheetFull ? "calc(100dvh - 7rem)" : "46dvh", "--panel-w": `${PANEL_W[tab]}px` } as React.CSSProperties}
      >
        <button
          type="button"
          data-testid="sheet-handle"
          onClick={() => setSheetFull((v) => !v)}
          aria-label={sheetFull ? "Shrink panel" : "Expand panel"}
          aria-expanded={sheetFull}
          className="flex shrink-0 justify-center pt-2 pb-1 md:hidden"
        >
          <span aria-hidden className="h-1.5 w-10 rounded-full bg-zinc-300" />
        </button>
        <header className="shrink-0 px-4 pt-1 pb-2 md:px-5 md:pt-4">
          <h1 className="text-lg font-semibold tracking-tight">{panelTitle}</h1>
          {/* Honest status: where data comes from right now. */}
          <ul aria-label="Data status" data-testid="data-status" className="mt-1.5 flex flex-wrap gap-1.5 text-[11px] font-medium">
            {health === undefined ? null : health === null ? (
              <li className={`rounded-full px-2 py-0.5 ${TONE.warn}`}>Backend offline</li>
            ) : (
              <>
                <li data-testid="status-data" className={`rounded-full px-2 py-0.5 ${health.replay ? "bg-amber-50 text-amber-900" : TONE.neutral}`}>
                  {health.replay ? "Replay data" : "Live data"}
                </li>
                <li data-testid="status-db" title={dbLabel(dbStatus).long} className={`rounded-full px-2 py-0.5 ${TONE[dbLabel(dbStatus).tone]}`}>
                  {dbLabel(dbStatus).chip}
                </li>
                <li className={`rounded-full px-2 py-0.5 ${health.gemini_configured ? TONE.good : TONE.neutral}`}>
                  {health.gemini_configured ? "Gemini bots" : "Rule-based bots"}
                </li>
              </>
            )}
          </ul>
        </header>

        {/* MAP tab */}
        <div id="panel-map" role="tabpanel" aria-labelledby="tab-map" hidden={tab !== "map"} className="flex min-h-0 flex-1 flex-col">
          <form
            className="flex min-h-0 flex-1 flex-col"
            onSubmit={(e) => {
              e.preventDefault();
              if (!running && prompt.trim()) start();
            }}
          >
            <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-3 md:px-5">
              <label htmlFor="prompt" className="text-sm font-medium text-zinc-700">
                Where are we going, and what should we know?
              </label>
              <textarea
                ref={promptRef}
                id="prompt"
                data-testid="prompt-input"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Where are you going? e.g. Union Station to Pearson by 6pm, 2 suitcases, no Presto card"
                aria-describedby="prompt-hint"
                rows={4}
                className="w-full shrink-0 resize-none rounded-xl border border-zinc-200 bg-zinc-50 p-3 text-sm leading-relaxed focus:border-zinc-400 focus:bg-white focus:outline-none"
              />
              <p id="prompt-hint" data-testid="prompt-hint" className="-mt-1 text-xs leading-relaxed text-zinc-600">
                <span className="font-semibold text-zinc-800">Try your own.</span> Short is fine (&ldquo;York U to CN Tower&rdquo;). Places must be in Toronto. 4WARD understands a deadline (&ldquo;by 6pm&rdquo;), bags, a budget (&ldquo;budget $10&rdquo;), no Presto card, a car or electric car, your bike or e-bike, and stairs or a stroller (step-free access is not checked yet).
              </p>
              <ExampleTrips
                current={prompt}
                onPick={(x) => {
                  setPrompt(x);
                  setError("");
                  promptRef.current?.focus();
                }}
              />
              <div role="group" aria-label="Quick add" className="flex flex-wrap gap-2">
                {QUICK.map((q) => {
                  const on = q.test.test(prompt);
                  return (
                    <button
                      key={q.key}
                      type="button"
                      data-testid={`quick-${q.key}`}
                      aria-pressed={on}
                      onClick={() => {
                        if (!on) setPrompt((p) => `${p.trim()} ${q.phrase}`.trim());
                        else if (prompt.includes(q.phrase)) setPrompt((p) => p.replace(q.phrase, "").replace(/\s{2,}/g, " ").trim());
                        else promptRef.current?.focus(); // mentioned in the user's own words: edit the text to change it
                      }}
                      className={`rounded-full px-3 py-1.5 text-xs font-medium ring-1 transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900 ${on ? "bg-zinc-900 text-white ring-zinc-900" : "bg-white text-zinc-700 ring-zinc-200 hover:bg-zinc-50"}`}
                    >
                      {on ? "✓ " : "+ "}
                      {q.label}
                    </button>
                  );
                })}
              </div>
              <TripOptions prompt={prompt} setPrompt={setPrompt} focusPrompt={() => promptRef.current?.focus()} priority={priority} setPriority={setPriority} />
              {error && (
                <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-800">
                  {error}
                </p>
              )}
            </div>
            <div className="shrink-0 border-t border-zinc-100 px-4 py-3 md:px-5">
              <button
                type="submit"
                data-testid="plan-trip"
                disabled={running || !prompt.trim()}
                aria-describedby="plan-hint"
                className="w-full rounded-full bg-zinc-900 px-6 py-3 text-sm font-semibold text-white transition-colors duration-150 hover:bg-zinc-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900 disabled:opacity-50"
              >
                {running ? `Bots working… ${elapsed}s` : "Plan my trip"}
              </button>
              <p id="plan-hint" data-testid="plan-hint" className="mt-1.5 text-center text-xs text-zinc-500" aria-live="polite">
                {running ? "The bots are working on your last trip." : !prompt.trim() ? "Type a trip above or pick an example to plan." : "Edit the text as much as you like before planning."}
              </p>
            </div>
          </form>
        </div>

        {/* ROUTES tab */}
        <div id="panel-routes" role="tabpanel" aria-labelledby="tab-routes" hidden={tab !== "routes"} className="flex min-h-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-3 md:px-5">
            {parsed?.origin && parsed.destination && (
              <p className="rounded-xl bg-zinc-50 p-3 text-xs text-zinc-600" data-testid="parsed-summary">
                Understood ({parsed.parser}): <b className="text-zinc-900">{parsed.origin}</b> to <b className="text-zinc-900">{parsed.destination}</b>
                {parsed.constraints &&
                  ` · ${Object.entries(parsed.constraints)
                    .filter(([, v]) => v && v !== 0)
                    .map(([k, v]) => (v === true ? k.replace("_", " ") : `${k.replace("_", " ")} ${v}`))
                    .join(", ")}`}
              </p>
            )}
            {places && (
              <div data-testid="places-check" className="rounded-xl bg-zinc-50 p-3 text-xs text-zinc-600">
                <p className="font-semibold text-zinc-800">Where we looked (Toronto only)</p>
                {(["origin", "destination"] as const).map((k) => {
                  const pl = places[k];
                  const fuzzy = !!pl.found && pl.found.source !== "gazetteer" && pl.found.name.toLowerCase() !== pl.typed.toLowerCase();
                  return (
                    <p key={k} className={!pl.found ? "text-red-800" : fuzzy ? "text-amber-900" : ""}>
                      {k === "origin" ? "From" : "To"} &ldquo;{pl.typed}&rdquo;: {!pl.found ? "not found in Toronto" : <b className="font-semibold">{pl.found.name}</b>}
                      {fuzzy && " (closest match from an OpenStreetMap search, check this is the place you meant)"}
                    </p>
                  );
                })}
              </div>
            )}
            {(() => {
              // Friendly summary when the trip cannot be planned as typed.
              const missing =
                (places && (["origin", "destination"] as const).map((k) => places[k]).find((pl) => !pl.found)?.typed) ??
                BOTS.map((b) => bots[b.key].error?.match(/Could not locate place: '([^']+)'/)?.[1]).find(Boolean);
              const allFailed = !running && !!parsed?.origin && BOTS.every((b) => bots[b.key].status === "FAILED");
              if (!missing && !allFailed) return null;
              return (
                <div role="alert" data-testid="trip-problem" className="rounded-xl bg-red-50 p-3 text-sm text-red-900">
                  <p>
                    {missing ? (
                      <>
                        We couldn&rsquo;t find &ldquo;{missing}&rdquo; in Toronto. Try a street address, an intersection like &ldquo;King and Spadina&rdquo;, or a landmark like &ldquo;CN Tower&rdquo;.
                      </>
                    ) : (
                      <>None of the four bots could finish a route for this trip. Try rewording it, or start from an example.</>
                    )}
                  </p>
                  <button
                    type="button"
                    data-testid="edit-trip"
                    onClick={backToPrompt}
                    className="mt-2 rounded-full bg-white px-3 py-1.5 text-xs font-semibold text-red-900 ring-1 ring-red-200 hover:bg-red-100 focus-visible:outline-2 focus-visible:outline-red-900"
                  >
                    Edit my trip
                  </button>
                </div>
              );
            })()}
            {running && <p className="text-xs text-zinc-500" role="status">Bots working… {elapsed}s</p>}
            {error && (
              <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-800">
                {error}
              </p>
            )}
            {!running && !parsed && !error && (
              <p className="rounded-xl bg-zinc-50 p-4 text-sm text-zinc-600">Plan a trip on the Map tab. Four bots will each find a route with a different goal.</p>
            )}
            {BOTS.map((b) => (
              <RouteCard
                key={b.key}
                bot={b}
                state={bots[b.key]}
                running={running}
                budget={budget}
                focused={focus === b.key}
                chosen={chosen === b.key}
                onFocus={() => setFocus((f) => (f === b.key ? null : b.key))}
                onChoose={() => {
                  setChosen(b.key);
                  setFocus(b.key);
                }}
              />
            ))}
            <p className="text-[11px] leading-relaxed text-zinc-500">
              Numbers come from the routing engine, not the AI. Transit fares follow TTC rules; driving, parking, rideshare costs and traffic are estimated. Transit schedule data is from TTC GTFS.
            </p>
          </div>
          <div className="shrink-0 border-t border-zinc-100 px-4 py-3 md:px-5">
            <button
              type="button"
              data-testid="watch-bots"
              aria-expanded={drawer}
              onClick={() => setDrawer((v) => !v)}
              className="w-full rounded-full bg-zinc-100 px-6 py-2.5 text-sm font-semibold text-zinc-900 transition-colors duration-150 hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900"
            >
              {drawer ? "Hide the bots" : "Watch the bots"}
            </button>
          </div>
        </div>

        {/* INSIGHTS tab */}
        <div id="panel-insights" role="tabpanel" aria-labelledby="tab-insights" hidden={tab !== "insights"} className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 md:px-5">
          {insightsSeen && <InsightsPanel />}
        </div>
      </section>

      {drawer && <BotDrawer bots={bots} running={running} onClose={closeDrawer} />}
    </div>
  );
}

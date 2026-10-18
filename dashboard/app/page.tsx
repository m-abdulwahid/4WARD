"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { API, Candidate, Clarification, ClarifyOption, TripPoint, apiHeaders, runWebSocketUrl } from "@/lib/api";
import { BOTS, BotKey, BotState, blankBots } from "@/lib/bots";
import RouteCard, { StatusChip } from "@/components/RouteCard";
import BotDrawer from "@/components/BotDrawer";
import LayerToggles from "@/components/LayerToggles";
import MapLegend, { LegendBotStatus } from "@/components/MapLegend";
import TripOptions from "@/components/TripOptions";
import InsightsTab from "@/components/InsightsTab";
import { TripConstraints, clock, fitRoutes } from "@/lib/routeFit";

type SortKey = "default" | "cost" | "time" | "co2" | "ontime";
import ExampleTrips from "@/components/ExampleTrips";
import { TimeKind, parseTime, writeTimeToPrompt } from "@/lib/timeParse";

// A time followed by "tomorrow" in the final trip text (the fields write it that way).
const TOMORROW = {
  deadline: /\b(?:by|before|arrive(?: at| by)?)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s+tomorrow\b/i,
  leave: /\b(?:leave|leaving|depart(?:ing)?|starting)\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s+tomorrow\b/i,
};
import { ModeKey, legColor, legMode } from "@/lib/modes";
import { DbStatus, TONE, dbLabel } from "@/lib/dataSource";
import type { BikeStation, MapPadding } from "@/components/RouteMap";

const RouteMap = dynamic(() => import("@/components/RouteMap"), { ssr: false });
const Timelapse = dynamic(() => import("@/components/Timelapse"), { ssr: false });

// Turn backend/network messages into what the user should do next.
function friendlyError(msg: string): string {
  if (/origin and destination/i.test(msg))
    return "We couldn't tell where you're starting and where you're going. Write it as “A to B”, for example “York U to CN Tower”, then press Plan my trip again.";
  if (/\bgta\b/i.test(msg))
    return `${msg} Try a street address, an intersection, or a landmark inside the GTA, then press Plan my trip again.`;
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

const CARD_W = 340;
// "map" closes the floating panel; the other three open it to that content.
const NAV = [
  { key: "map", label: "Map", icon: (p: { className?: string }) => (
    <svg aria-hidden viewBox="0 0 24 24" className={p.className} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2Z" />
      <path d="M9 4v14M15 6v14" />
    </svg>
  ) },
  { key: "plan", label: "Plan", icon: (p: { className?: string }) => (
    <svg aria-hidden viewBox="0 0 24 24" className={p.className} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  ) },
  { key: "routes", label: "Routes", icon: (p: { className?: string }) => (
    <svg aria-hidden viewBox="0 0 24 24" className={p.className} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="6" cy="6" r="2.3" />
      <circle cx="18" cy="18" r="2.3" />
      <path d="M8 7c3 0 2 9 5 9h5" />
    </svg>
  ) },
  { key: "insights", label: "Insights", icon: (p: { className?: string }) => (
    <svg aria-hidden viewBox="0 0 24 24" className={p.className} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 20V10M11 20V4M18 20v-7" />
    </svg>
  ) },
] as const;
type LeftTab = Exclude<(typeof NAV)[number]["key"], "map">;

export default function Home() {
  const [prompt, setPrompt] = useState("");
  // typed time fields (they win over any time in the trip text)
  const [deadlineText, setDeadlineText] = useState("");
  const [leaveText, setLeaveText] = useState("");
  const [deadlineNote, setDeadlineNote] = useState<string | null>(null);
  // what each typed place was matched to (the geocoder only searches inside Toronto)
  const [places, setPlaces] = useState<{ origin: Place; destination: Place } | null>(null);
  const [bots, setBots] = useState<Record<BotKey, BotState>>(blankBots);
  const [running, setRunning] = useState(false);
  const [parsed, setParsed] = useState<{ origin: string; destination: string; stops?: { name: string; type: string; dwell_min: number }[]; optimize_order?: boolean; parser?: string; constraints?: Record<string, unknown> } | null>(null);
  const [clarify, setClarify] = useState<Clarification[]>([]); // questions asked instead of guessing an ambiguous phrase
  const runTextRef = useRef("");
  const [points, setPoints] = useState<TripPoint[]>([]); // A, B, C ... in visiting order, from the run's first event
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
  const [engineIssues, setEngineIssues] = useState<string[]>([]); // the optimum check failed: a bug in 4WARD, shown plainly
  const [relaxed, setRelaxed] = useState<string[]>([]); // a soft rule the engine had to drop, or a constraint that left every bot with the same one option
  const [sortBy, setSortBy] = useState<SortKey>("default"); // neutral card order only; nothing is ever highlighted by it
  const [drawer, setDrawer] = useState(false);
  const [timelapseOpen, setTimelapseOpen] = useState(false);
  const [leftTab, setLeftTab] = useState<LeftTab>("plan");
  const [leftOpen, setLeftOpen] = useState(true); // the floating panel; "Map" in the nav strip hides it
  const [hiddenBots, setHiddenBots] = useState<Partial<Record<BotKey, boolean>>>({});
  const [showAll, setShowAll] = useState(false); // "Show all routes": ignore the chosen route's highlight
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const runIdRef = useRef<string | null>(null);
  const t0 = useRef(0);
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

  // Esc closes the bots drawer first, otherwise clears a focused card back to the full comparison.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || drawer) return;
      if (focus) {
        setFocus(null);
        setShowAll(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer, focus]);

  // Typing a valid time writes it into the trip text; clearing the field takes it out again.
  const typeTime = (kind: TimeKind, text: string) => {
    const prev = kind === "deadline" ? deadlineText : leaveText;
    (kind === "deadline" ? setDeadlineText : setLeaveText)(text);
    const p = text.trim() ? parseTime(text) : null;
    if (p) setPrompt((x) => writeTimeToPrompt(x, kind, p));
    else if (!text.trim() && prev.trim()) setPrompt((x) => writeTimeToPrompt(x, kind, null));
  };
  const badTime = (deadlineText.trim() && !parseTime(deadlineText)) || (leaveText.trim() && !parseTime(leaveText));
  // On Plan my trip the fields win: apply them once more in case the trip text was edited afterwards.
  const finalPrompt = () => {
    let t = prompt;
    for (const [kind, text] of [["deadline", deadlineText], ["leave", leaveText]] as const) {
      const p = text.trim() ? parseTime(text) : null;
      if (p) t = writeTimeToPrompt(t, kind, p);
    }
    return t;
  };

  const stop = useCallback(() => {
    const id = runIdRef.current;
    if (!id) return;
    fetch(`${API}/run/${id}/cancel`, { method: "POST", headers: apiHeaders() }).catch(() => {});
  }, []);

  const backToPrompt = () => setTimeout(() => promptRef.current?.focus(), 50);

  const checkPlaces = (origin: string, destination: string) => {
    const look = (q: string): Promise<Place> =>
      fetch(`${API}/geocode?q=${encodeURIComponent(q)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((g) => ({ typed: q, found: g ? { name: g.name, source: g.source } : null }));
    Promise.all([look(origin), look(destination)])
      .then(([o, d]) => setPlaces({ origin: o, destination: d }))
      .catch(() => {}); // backend errors surface through the run itself
  };

  const answerClarify = (o: ClarifyOption) => {
    const text = runTextRef.current;
    const next = o.replace ? text.replace(o.replace[0], o.replace[1]) : o.append ? `${text.trim()} ${o.append}` : text;
    setPrompt(next);
    start(next);
  };

  const patch = useCallback((k: BotKey, p: Partial<BotState>) => setBots((b) => ({ ...b, [k]: { ...b[k], ...p } })), []);

  const start = useCallback(async (text: string = prompt) => {
    wsRef.current?.close();
    setError("");
    setChosen(null);
    setFocus(null);
    setParsed(null);
    setPoints([]);
    setClarify([]);
    runTextRef.current = text;
    setPlaces(null);
    setShowAll(false);
    setBots(blankBots());
    setEngineIssues([]);
    setRelaxed([]);
    setBudget(readBudget(text));
    // The backend compares deadlines with today's clock: a deadline tomorrow (without leaving tomorrow) cannot be checked.
    setDeadlineNote(
      TOMORROW.deadline.test(text) && !TOMORROW.leave.test(text)
        ? "Your deadline is tomorrow. 4WARD checks deadlines against today's clock, so this check is skipped."
        : null,
    );
    setRunning(true);
    t0.current = Date.now();
    try {
      const r = await fetch(`${API}/run`, {
        method: "POST",
        headers: apiHeaders({ "content-type": "application/json" }),
        body: JSON.stringify({ prompt: text }), // final text: typed time fields already applied
      });
      if (!r.ok) throw new Error(`Backend refused the run (${r.status})`);
      const { id } = await r.json();
      runIdRef.current = id;
      const ws = new WebSocket(runWebSocketUrl(id));
      wsRef.current = ws;
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        const k = ev.bot as BotKey;
        if (ev.type === "parsed") {
          setParsed(ev);
          if (ev.origin && ev.destination) checkPlaces(ev.origin, ev.destination);
        }
        else if (ev.type === "points") setPoints(ev.points ?? []);
        else if (ev.type === "frame") patch(k, { frame: ev.data });
        else if (ev.type === "steel_session") patch(k, { liveViewUrl: ev.live_view_url });
        else if (ev.type === "status") patch(k, { status: ev.status });
        else if (ev.type === "step")
          setBots((b) => ({
            ...b,
            [k]: {
              ...b[k],
              steps: ev.n,
              lastReason: ev.reason ?? b[k].lastReason,
              log: [...b[k].log, { n: ev.n, action: ev.action, testid: ev.testid, text: ev.text, reason: ev.reason, outcome: ev.outcome }],
            },
          }));
        else if (ev.type === "result")
          patch(k, { status: ev.status, route: ev.route, alternatives: ev.alternatives, nextBest: ev.next_best, brain: ev.brain, elapsed: ev.elapsed_s, error: ev.error, computedAt: Date.now() });
        else if (ev.type === "invariant_violation") setEngineIssues(ev.violations ?? []);
        else if (ev.type === "run_done") {
          setEngineIssues(ev.result?.invariant_violations ?? []);
          setRelaxed(ev.result?.relaxed ?? []);
          setClarify(ev.result?.clarify ?? []);
          setRunning(false);
          runIdRef.current = null;
          if (ev.status === "cancelled") {
            // the user asked for this: no error banner, just let them see or edit what's there
          } else if (ev.result?.error) {
            setError(friendlyError(ev.result.error));
            if (/origin and destination|\bgta\b/i.test(ev.result.error)) backToPrompt();
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

  // Routes that fit the trip (deadline, budget, bags, car/bike). Everything below (map, legend, cards,
  // timelapse, priority pick) uses this, so a route that breaks a constraint is left out everywhere.
  const fit = useMemo(
    () =>
      fitRoutes(
        BOTS.filter((b) => bots[b.key].route).map((b) => ({ key: b.key, route: bots[b.key].route as Candidate })),
        (parsed?.constraints ?? {}) as TripConstraints,
        budget,
        !deadlineNote, // a deadline tomorrow cannot be judged against today's clock
      ),
    [bots, parsed, budget, deadlineNote],
  );
  const done = BOTS.filter((b) => bots[b.key].route && fit.shown.includes(b.key));
  const highlight = focus ?? (showAll ? null : chosen);

  // Card order for the neutral sort control. The default keeps the fixed bot order; a bot with no route sorts last.
  const sortedBots = useMemo(() => {
    if (sortBy === "default") return [...BOTS];
    const value = (k: BotKey): number => {
      const r = bots[k].route;
      if (!r) return Infinity;
      if (sortBy === "cost") return r.cost ?? Infinity;
      if (sortBy === "time") return r.duration_min ?? Infinity;
      if (sortBy === "co2") return r.co2_kg ?? Infinity;
      return r.reliability ? -r.reliability.reliability_pct : Infinity; // on-time %: higher first
    };
    return [...BOTS].sort((a, b) => value(a.key) - value(b.key));
  }, [bots, sortBy]);
  // Every bot draws its own line and gets its own card, even when two bots land on the identical route:
  // the map offsets same-street lines side by side, and the legend's eye toggles always list all four.
  const mapRoutes = useMemo(
    () =>
      BOTS.filter((b) => bots[b.key].route && fit.shown.includes(b.key)).map((b) => ({
        id: b.key,
        color: b.color,
        label: b.name,
        candidate: bots[b.key].route as Candidate,
        emphasized: highlight ? b.key === highlight : true,
        visible: !hiddenBots[b.key],
      })),
    [bots, highlight, hiddenBots, fit],
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
  }, [bots, done]);
  const first = done[0] ? bots[done[0].key].route : null;
  const waypoints = useMemo(() => points.slice(1, -1).map((p) => ({ lat: p.lat, lon: p.lon, name: p.name })), [points]);
  const timelapseRoutes = useMemo(
    () => BOTS.filter((b) => bots[b.key].route).map((b) => ({ key: b.key, name: b.name, color: b.color, candidate: bots[b.key].route as Candidate })),
    [bots],
  );

  // Legend and layers now live in a persistent side panel, not floating over the map, so the map only
  // needs a small margin for markers near the edge.
  const mapPadding: MapPadding = { top: 16, right: 16, bottom: 16, left: 16 };
  const toggleBot = (k: BotKey) => setHiddenBots((h) => ({ ...h, [k]: !h[k] }));
  const selectFromMap = (id: string) => {
    setShowAll(false);
    setFocus(id as BotKey);
  };
  const showAllRoutes = () => {
    setFocus(null);
    setShowAll(true);
  };
  const legendStatus = Object.fromEntries(
    BOTS.map((b) => {
      const s = bots[b.key];
      const state = s.route
        ? fit.excluded.includes(b.key)
          ? "excluded"
          : "done"
        : s.status === "FAILED"
          ? "failed"
          : running || s.status === "SCANNING" || s.status === "ROUTING"
            ? "searching"
            : "waiting";
      return [b.key, { state, error: s.error }];
    }),
  ) as Record<BotKey, LegendBotStatus>;

  const tabTitle = leftTab === "plan" ? "Plan a trip" : leftTab === "routes" ? "Routes" : "Insights";

  const tabContent = (
    <>
          {leftTab === "insights" && <InsightsTab />}

          {leftTab === "routes" && (
            <div className="flex min-h-0 flex-1 flex-col gap-2">
              <label className="flex items-center justify-between gap-2 text-xs text-zinc-600">
                Order cards by
                <select
                  data-testid="sort-routes"
                  value={sortBy}
                  onChange={(e) => setSortBy(e.target.value as SortKey)}
                  className="rounded-lg bg-white px-2 py-1 text-xs text-zinc-800 ring-1 ring-zinc-200 focus-visible:outline-2 focus-visible:outline-zinc-900"
                >
                  <option value="default">Bot order</option>
                  <option value="cost">Cost (lowest first)</option>
                  <option value="time">Time (shortest first)</option>
                  <option value="co2">CO₂e (lowest first)</option>
                  <option value="ontime">On-time % (highest first)</option>
                </select>
              </label>
              {!running && !parsed && !error && (
                <p className="rounded-xl bg-zinc-50 p-4 text-sm text-zinc-600 ring-1 ring-zinc-200">
                  Plan a trip on the Plan tab. All four bots land here, each with its own route, timeline and card.
                </p>
              )}
              {sortedBots
                .filter((b) => !fit.excluded.includes(b.key))
                .sort((a, b) => {
                  if (fit.fallback?.kind !== "deadline") return 0;
                  const ia = fit.shown.indexOf(a.key);
                  const ib = fit.shown.indexOf(b.key);
                  return ia >= 0 && ib >= 0 ? ia - ib : 0;
                })
                .map((b) => (
                  <RouteCard
                    key={b.key}
                    bot={b}
                    state={bots[b.key]}
                    running={running}
                    budget={budget}
                    deadlineNote={deadlineNote}
                    arrival={fit.fallback?.kind === "deadline"}
                    focused={focus === b.key}
                    chosen={chosen === b.key}
                    onFocus={() => {
                      const open = focus === b.key;
                      setShowAll(open); // clicking the open card again goes back to all routes
                      setFocus(open ? null : b.key);
                    }}
                    onChoose={() => {
                      setChosen(b.key);
                      setFocus(b.key);
                      setShowAll(false);
                    }}
                    onUseAlternative={(botKey, alt) => patch(botKey, { route: alt })}
                  />
                ))}
              <p
                className="px-1 text-[11px] leading-relaxed text-zinc-500"
                title="From the routing engine, not the AI. Transit fares follow TTC and GO rules; driving, parking, rideshare costs and traffic are estimated."
              >
                Numbers come from the routing engine, not the AI. Transit fares follow TTC rules; driving, parking, rideshare costs and traffic are estimated. Transit schedule data is from TTC GTFS.
              </p>
            </div>
          )}

          {leftTab === "plan" && (
          <>
          <form
            className="flex shrink-0 flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (running || !prompt.trim() || badTime) return;
              const t = finalPrompt();
              if (t !== prompt) setPrompt(t);
              start(t);
            }}
          >
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
              rows={5}
              className="w-full min-h-[6rem] shrink-0 resize-y rounded-xl border border-zinc-200 bg-zinc-50 p-3 text-sm leading-relaxed focus:border-zinc-400 focus:bg-white focus:outline-none"
            />
            <ExampleTrips
              current={prompt}
              onPick={(trip) => {
                setPrompt(trip.prompt);
                setDeadlineText(trip.deadline ?? "");
                setLeaveText("");
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
            <TripOptions prompt={prompt} setPrompt={setPrompt} focusPrompt={() => promptRef.current?.focus()}
              deadlineText={deadlineText}
              setDeadlineText={(t) => typeTime("deadline", t)}
              leaveText={leaveText}
              setLeaveText={(t) => typeTime("leave", t)}
            />
            {error && (
              <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-800">
                {error}
              </p>
            )}
            <button
              type="submit"
              data-testid="plan-trip"
              disabled={running || !prompt.trim() || !!badTime}
              aria-describedby="plan-hint"
              className="w-full rounded-full bg-zinc-900 px-6 py-3 text-sm font-semibold text-white transition-colors duration-150 hover:bg-zinc-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900 disabled:opacity-50"
            >
              {running ? `Bots working… ${elapsed}s` : "Plan my trip"}
            </button>
            <p id="plan-hint" data-testid="plan-hint" className="-mt-1 text-center text-xs text-zinc-500" aria-live="polite">
              {running
                ? "The bots are working on your last trip."
                : !prompt.trim()
                  ? "Type a trip above or pick an example to plan."
                  : badTime
                    ? "Fix the time under Deadline and leave time first (try 5pm or 17:30)."
                    : "Edit the text as much as you like before planning."}
            </p>
          </form>

          {/* Timeline: run progress, once a trip has started. Always lists all four bots. */}
          {(running || parsed || error) && (
            <div data-testid="run-timeline" className="flex flex-col gap-2 border-t border-zinc-100 pt-3">
              <h2 className="text-xs font-semibold tracking-wide text-zinc-500 uppercase">Timeline</h2>

              {clarify.length > 0 && !running && (
                <div data-testid="clarify" role="group" aria-label="A question about your trip" className="rounded-xl bg-amber-50 p-3 text-sm text-amber-950 ring-1 ring-amber-200">
                  <p className="text-[11px] font-semibold tracking-wide uppercase">One question before the bots start</p>
                  {clarify.slice(0, 1).map((q) => (
                    <div key={q.id} className="mt-1">
                      <p>{q.question}</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {q.options.map((o, i) => (
                          <button
                            key={o.label}
                            type="button"
                            data-testid={`clarify-${q.id}-${i}`}
                            onClick={() => answerClarify(o)}
                            className="rounded-full bg-white px-3 py-1.5 text-xs font-semibold text-zinc-900 ring-1 ring-amber-300 hover:bg-amber-100 focus-visible:outline-2 focus-visible:outline-zinc-900"
                          >
                            {o.label}
                          </button>
                        ))}
                        <button
                          type="button"
                          data-testid="clarify-edit"
                          onClick={backToPrompt}
                          className="rounded-full px-3 py-1.5 text-xs font-semibold text-amber-950 underline focus-visible:outline-2 focus-visible:outline-zinc-900"
                        >
                          Edit my trip instead
                        </button>
                      </div>
                    </div>
                  ))}
                  {clarify.length > 1 && <p className="mt-2 text-[11px]">{clarify.length - 1} more question(s) after this one.</p>}
                </div>
              )}

              {parsed?.origin && parsed.destination && (
                <p className="rounded-xl bg-zinc-50 p-3 text-xs text-zinc-600" data-testid="parsed-summary">
                  Understood ({parsed.parser}): <b className="text-zinc-900">{parsed.origin}</b>
                  {(parsed.stops ?? []).map((s) => (
                    <span key={s.name}>
                      {" "}
                      then {s.type === "via" ? "via " : ""}
                      <b className="text-zinc-900">{s.name}</b>
                      {s.dwell_min > 0 ? ` (${Math.round(s.dwell_min)} min)` : ""}
                    </span>
                  ))}{" "}
                  to <b className="text-zinc-900">{parsed.destination}</b>
                  {parsed.optimize_order ? " · any stop order" : (parsed.stops ?? []).length > 1 ? " · stops in the order you typed" : ""}
                  {parsed.constraints &&
                    ` · ${Object.entries(parsed.constraints)
                      .filter(([, v]) => v && v !== 0)
                      .map(([k, v]) => (v === true ? k.replace("_", " ") : `${k.replace("_", " ")} ${v}`))
                      .join(", ")}`}
                </p>
              )}
              {places && (
                <div data-testid="places-check" className="rounded-xl bg-zinc-50 p-3 text-xs text-zinc-600">
                  <p className="font-semibold text-zinc-800">Where we looked (GTA only)</p>
                  {(["origin", "destination"] as const).map((k) => {
                    const pl = places[k];
                    const fuzzy = !!pl.found && pl.found.source !== "gazetteer" && pl.found.name.toLowerCase() !== pl.typed.toLowerCase();
                    return (
                      <p key={k} className={!pl.found ? "text-red-800" : fuzzy ? "text-amber-900" : ""}>
                        {k === "origin" ? "From" : "To"} &ldquo;{pl.typed}&rdquo;: {!pl.found ? "not found in the GTA" : <b className="font-semibold">{pl.found.name}</b>}
                        {fuzzy && " (closest match from an OpenStreetMap search, check this is the place you meant)"}
                      </p>
                    );
                  })}
                </div>
              )}
              {(() => {
                const missing =
                  (places && (["origin", "destination"] as const).map((k) => places[k]).find((pl) => !pl.found)?.typed) ??
                  BOTS.map((b) => bots[b.key].error?.match(/Could not locate place: '([^']+)'/)?.[1]).find(Boolean);
                const allFailed = !running && !!parsed?.origin && BOTS.every((b) => bots[b.key].status === "FAILED");
                if (!missing && !allFailed) return null;
                return (
                  <div role="alert" data-testid="trip-problem" className="rounded-xl bg-red-50 p-3 text-sm text-red-900">
                    <p>
                      {missing ? (
                        <>We couldn&rsquo;t find &ldquo;{missing}&rdquo; in the GTA. Try a street address, an intersection like &ldquo;King and Spadina&rdquo;, or a landmark like &ldquo;CN Tower&rdquo;.</>
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

              {running && (
                <p className="flex items-center justify-between gap-2 text-xs text-zinc-500" role="status">
                  Bots working… {elapsed}s
                  <button
                    type="button"
                    data-testid="stop-run"
                    onClick={stop}
                    className="shrink-0 rounded-full bg-zinc-100 px-2.5 py-1 text-[11px] font-semibold text-zinc-800 hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-zinc-900"
                  >
                    Stop
                  </button>
                </p>
              )}

              <ul className="flex flex-col gap-1" data-testid="bot-rows" aria-label="The four bots">
                {BOTS.map((b) => {
                  const s = bots[b.key];
                  const r = s.route;
                  return (
                    <li key={b.key} data-testid={`bot-row-${b.key}`} className="flex items-center gap-2 text-xs text-zinc-700">
                      <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: b.color }} />
                      <span className="w-24 shrink-0 font-semibold text-zinc-900">{b.name}</span>
                      <StatusChip botKey={b.key} state={s} />
                      <span className="min-w-0 truncate">
                        {r ? `${r.title}: ${Math.round(r.duration_min ?? 0)} min, $${(r.cost ?? 0).toFixed(2)}, ${(r.co2_kg ?? 0).toFixed(2)} kg` : s.status === "FAILED" ? s.error ?? "failed" : running ? "working…" : "waiting"}
                      </span>
                    </li>
                  );
                })}
              </ul>

              {relaxed.length > 0 && (
                <ul data-testid="relaxed-notice" className="flex flex-col gap-1.5">
                  {relaxed.map((r) => (
                    <li key={r} role="status" className="rounded-xl bg-amber-50 px-3 py-2 text-sm font-medium text-amber-900 ring-1 ring-amber-200">
                      {r}
                    </li>
                  ))}
                </ul>
              )}
              {fit.fallback && (
                <p role="status" data-testid="fit-notice" className="rounded-xl bg-amber-50 px-3 py-2 text-sm font-medium text-amber-900 ring-1 ring-amber-200">
                  {fit.fallback.kind === "deadline"
                    ? `No route arrives by ${clock(fit.fallback.arriveBy)}. Closest options shown.`
                    : `No route fits your $${fit.fallback.budget.toFixed(2)} budget. Cheapest option shown.`}
                </p>
              )}
              {engineIssues.length > 0 && (
                <div role="alert" data-testid="engine-check-failed" className="rounded-xl bg-red-50 p-3 text-xs text-red-900">
                  <p className="font-semibold">Engine check failed. This is a bug in 4WARD, not in your trip.</p>
                  <ul className="mt-1 list-disc pl-4">
                    {engineIssues.map((v) => (
                      <li key={v}>{v}</li>
                    ))}
                  </ul>
                </div>
              )}
              {!running && done.length >= 2 && (
                <button
                  type="button"
                  data-testid="open-timelapse"
                  onClick={() => setTimelapseOpen(true)}
                  className="flex w-full items-center justify-center gap-2 rounded-xl bg-zinc-900 px-3 py-2.5 text-sm font-semibold text-white hover:bg-zinc-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900"
                >
                  <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor">
                    <path d="M7 5.5v13l11-6.5z" />
                  </svg>
                  Watch the trip unfold
                </button>
              )}
            </div>
          )}
          </>
          )}
    </>
  );

  return (
    <div className="fixed inset-0 flex overflow-hidden bg-zinc-100 text-zinc-900">
      {/* Icon sidebar: wordmark + the four nav buttons. Always visible, takes real layout space. */}
      <nav aria-label="Navigate" data-testid="icon-nav" className="z-20 flex w-[72px] shrink-0 flex-col items-center gap-1 border-r border-zinc-200 bg-white py-3">
        <p className="mb-2 text-base font-extrabold tracking-tight">
          4W<span className="text-emerald-600">.</span>
        </p>
        {NAV.map((item) => {
          const active = item.key === "map" ? !leftOpen : leftOpen && leftTab === item.key;
          return (
            <button
              key={item.key}
              type="button"
              data-testid={item.key === "map" ? "nav-map" : `left-tab-${item.key}`}
              aria-pressed={active}
              onClick={() => {
                if (item.key === "map") setLeftOpen(false);
                else {
                  setLeftTab(item.key as LeftTab);
                  setLeftOpen(true);
                }
              }}
              className="flex flex-col items-center gap-1 rounded-xl px-2 py-2 text-[10px] font-medium text-zinc-500 hover:text-zinc-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900"
            >
              <span className={`flex h-9 w-9 items-center justify-center rounded-xl transition-colors duration-150 ${active ? "bg-zinc-900 text-white" : "bg-transparent"}`}>
                <item.icon className="h-5 w-5" />
              </span>
              {item.label}
            </button>
          );
        })}
      </nav>

      {/* Map + floating chrome. The map is full-bleed; everything else overlays it. */}
      <div className="relative min-h-0 flex-1">
        <RouteMap
          routes={mapRoutes}
          origin={first ? first.legs[0]?.from : null}
          destination={first ? first.legs[first.legs.length - 1]?.to : null}
          waypoints={waypoints}
          padding={mapPadding}
          colorBy="mode"
          lineScale={1}
          onRouteClick={selectFromMap}
          fitKey="map"
          showTtc={showTtc}
          bikeStations={bikeStations}
        />

        {leftOpen && (
          <section
            aria-label="Left panel"
            data-testid="left-panel"
            className="absolute top-4 bottom-4 left-4 z-20 flex w-[340px] max-w-[calc(100vw-6.5rem)] flex-col overflow-hidden rounded-2xl bg-white shadow-xl ring-1 ring-black/5"
            style={{ "--card-w": `${CARD_W}px` } as React.CSSProperties}
          >
            <div className="flex shrink-0 items-start justify-between gap-2 border-b border-zinc-100 px-4 py-3">
              <div className="min-w-0">
                <h2 className="text-sm font-bold">{tabTitle}</h2>
                <ul aria-label="Data status" data-testid="data-status" className="mt-1 flex flex-wrap gap-1.5 text-[11px] font-medium">
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
              </div>
              <button
                type="button"
                data-testid="toggle-left-panel"
                aria-label="Hide the trip panel"
                onClick={() => setLeftOpen(false)}
                className="shrink-0 text-xs font-semibold text-zinc-500 hover:text-zinc-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900"
              >
                ← Hide panel
              </button>
            </div>
            <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">{tabContent}</div>
          </section>
        )}

        {/* Floating: Watch the bots, map layers, legend. Always reachable regardless of the left panel. */}
        <div className="absolute top-4 right-4 z-20 flex flex-col items-end gap-2">
          <button
            type="button"
            data-testid="watch-bots"
            aria-expanded={drawer}
            onClick={() => setDrawer((v) => !v)}
            className="rounded-full bg-zinc-900 px-4 py-1.5 text-xs font-semibold text-white shadow-lg hover:bg-zinc-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900"
          >
            {drawer ? "Hide the bots" : "Watch the bots"}
            {running && !drawer && <span aria-hidden className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-sky-400 align-middle" />}
          </button>
          <LayerToggles onTtc={setShowTtc} onBikes={setBikeStations} />
          <MapLegend
            status={legendStatus}
            hiddenBots={hiddenBots}
            onToggleBot={toggleBot}
            onShowAll={showAllRoutes}
            highlight={highlight}
            modes={usedModes}
          />
        </div>
      </div>

      {drawer && <BotDrawer bots={bots} running={running} onClose={closeDrawer} />}
      {timelapseOpen && timelapseRoutes.length >= 2 && (
        <Timelapse
          routes={timelapseRoutes}
          departLabel={first?.depart ?? "12:00"}
          origin={first ? first.legs[0]?.from : null}
          destination={first ? first.legs[first.legs.length - 1]?.to : null}
          onClose={() => setTimelapseOpen(false)}
        />
      )}
    </div>
  );
}

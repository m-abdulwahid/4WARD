"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { API, Candidate } from "@/lib/api";
import { BOTS, BotKey, BotState, blankBots } from "@/lib/bots";
import InsightsPanel from "@/components/InsightsPanel";
import RouteCard from "@/components/RouteCard";
import BotDrawer from "@/components/BotDrawer";
import RouteSummary from "@/components/RouteSummary";
import RouteChips from "@/components/RouteChips";
import LayerToggles from "@/components/LayerToggles";
import MapLegend, { LegendBotStatus } from "@/components/MapLegend";
import TripOptions, { Priority } from "@/components/TripOptions";
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
  { key: "plan", label: "Plan", icon: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm5-2 4 4M8 11h6m-3-3v6" },
  { key: "routes", label: "Routes", icon: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm12-10a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM6 15V9a4 4 0 0 1 4-4h4m4 4v6a4 4 0 0 1-4 4h-4" },
  { key: "insights", label: "Insights", icon: "M4 20V10m6 10V4m6 16v-7m4 7H2" },
] as const;
type Tab = (typeof TABS)[number]["key"];

const RAIL_W = 104; // left rail (72px) + gaps
const PANEL_W: Record<Tab, number> = { map: 0, plan: 400, routes: 400, insights: 600 }; // the Map tab has no panel

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
  const [tab, setTab] = useState<Tab>("plan");
  const tabRef = useRef<Tab>("plan");
  tabRef.current = tab;
  // Clean route view: only the chosen route on the map, with a small summary strip instead of the panel.
  const [routeView, setRouteView] = useState<BotKey | null>(null);
  const [sheetFull, setSheetFull] = useState(false);
  // Panel hidden = full map (wide screens) or a small peek bar (phones). Remembered for this browser session.
  const [panelHidden, setPanelHidden] = useState(false);
  const panelHiddenRef = useRef(false);
  panelHiddenRef.current = panelHidden;
  const [unseen, setUnseen] = useState(0); // routes that arrived while the panel was hidden
  const [hiddenBots, setHiddenBots] = useState<Partial<Record<BotKey, boolean>>>({});
  const [showAll, setShowAll] = useState(false); // "Show all routes": ignore the chosen route's highlight
  const showPanelBtn = useRef<HTMLButtonElement>(null);
  const routeViewRef = useRef<BotKey | null>(null);
  const backToAllRoutesRef = useRef<(unchoose: boolean) => void>(() => {});
  const dragY = useRef<number | null>(null);
  const dragged = useRef(false);
  const [drawer, setDrawer] = useState(false);
  const [prompt, setPrompt] = useState("");
  // typed time fields (they win over any time in the trip text)
  const [deadlineText, setDeadlineText] = useState("");
  const [leaveText, setLeaveText] = useState("");
  const [deadlineNote, setDeadlineNote] = useState<string | null>(null);
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

  const backToPrompt = () => {
    setTab("plan");
    setRouteView(null);
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

  useEffect(() => {
    try {
      if (sessionStorage.getItem("4ward.panelHidden") === "1") setPanelHidden(true);
    } catch {}
  }, []);
  const setHidden = useCallback((h: boolean) => {
    setPanelHidden(h);
    if (!h) setUnseen(0);
    try {
      sessionStorage.setItem("4ward.panelHidden", h ? "1" : "0");
    } catch {}
    // keep keyboard focus somewhere visible
    if (h) setTimeout(() => showPanelBtn.current?.focus(), 0);
  }, []);
  // Esc hides the panel and shows it again (the bots drawer handles its own Esc first)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || drawer) return;
      if (routeViewRef.current) return backToAllRoutesRef.current(false);
      if (tabRef.current === "map") return;
      setHidden(!panelHiddenRef.current);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer, setHidden]);

  const patch = useCallback((k: BotKey, p: Partial<BotState>) => setBots((b) => ({ ...b, [k]: { ...b[k], ...p } })), []);

  const start = useCallback(async (text: string = prompt) => {
    wsRef.current?.close();
    setError("");
    setChosen(null);
    setFocus(null);
    setParsed(null);
    setPlaces(null);
    setUnseen(0);
    setShowAll(false);
    setRouteView(null);
    setBots(blankBots());
    setBudget(readBudget(text));
    // The backend compares deadlines with today's clock: a deadline tomorrow (without leaving tomorrow) cannot be checked.
    setDeadlineNote(
      TOMORROW.deadline.test(text) && !TOMORROW.leave.test(text)
        ? "Your deadline is tomorrow. 4WARD checks deadlines against today's clock, so this check is skipped."
        : null,
    );
    setRunning(true);
    setTab("routes");
    t0.current = Date.now();
    try {
      const r = await fetch(`${API}/run`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ prompt: text }) });
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
        else if (ev.type === "result") {
          patch(k, { status: ev.status, route: ev.route, brain: ev.brain, elapsed: ev.elapsed_s, error: ev.error });
          if (panelHiddenRef.current || tabRef.current === "map") setUnseen((u) => u + 1);
        }
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
  const [hoverBot, setHoverBot] = useState<BotKey | null>(null); // chip under the pointer (or focused)
  const highlight = (hoverBot && !hiddenBots[hoverBot] ? hoverBot : null) ?? focus ?? (showAll ? null : chosen);

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
  routeViewRef.current = routeView;
  const mapRoutes = useMemo(
    () =>
      BOTS.filter((b) => bots[b.key].route && (!routeView || b.key === routeView)).map((b) => ({
        id: b.key,
        color: b.color,
        label: b.name,
        candidate: bots[b.key].route as Candidate,
        emphasized: routeView ? true : highlight ? highlight === b.key : true,
        visible: routeView ? true : !hiddenBots[b.key],
      })),
    [bots, highlight, hiddenBots, routeView],
  );
  // Modes the finished routes use, with the colour each is drawn in (TTC line colour for transit).
  const usedModes = useMemo(() => {
    const out: Partial<Record<ModeKey, string>> = {};
    // only what is on the map: the chosen route in the clean route view, otherwise every finished route
    done.filter((b) => !routeView || b.key === routeView).forEach((b) => {
      const c = bots[b.key].route as Candidate;
      c.legs.forEach((l) => {
        const m = legMode(l, c.id);
        out[m] ??= legColor(l, m);
      });
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bots, routeView]);
  const first = done[0] ? bots[done[0].key].route : null;

  // keep routes inside the part of the map that is not covered by the panel / sheet
  const noPanel = tab === "map" || !!routeView;
  const panelShown = !noPanel && !panelHidden;
  const mapPadding: MapPadding = routeView
    ? wide
      ? { top: 70, right: 270, bottom: 300, left: RAIL_W + 40 } // summary strip along the bottom
      : { top: 110, right: 30, bottom: 360, left: 30 }
    : wide
      ? { top: 60, right: 270, bottom: 100, left: RAIL_W + (panelShown ? PANEL_W[tab] : 0) + 40 } // right: Layers + Legend; bottom: route chips
      : {
          top: 110,
          right: 30,
          bottom: !panelShown ? (noPanel ? 110 : 150) : typeof window === "undefined" ? 400 : Math.round(window.innerHeight * (sheetFull ? 0.8 : 0.46)) + 110,
          left: 30,
        };
  // Route visibility (chips on the map and eyes in the legend share it). Cards in the Routes tab are never hidden.
  const toggleBot = (k: BotKey) => setHiddenBots((h) => ({ ...h, [k]: !h[k] }));
  const selectFromMap = (id: string) => {
    if (routeView) return;
    setShowAll(false);
    setFocus(id as BotKey);
  };
  const showEveryRoute = () => setHiddenBots({});
  const showAllRoutes = () => {
    setFocus(null);
    setShowAll(true);
  };
  const legendStatus = Object.fromEntries(
    BOTS.map((b) => {
      const s = bots[b.key];
      const state = s.route ? "done" : s.status === "FAILED" ? "failed" : running || s.status === "SCANNING" || s.status === "ROUTING" ? "searching" : "waiting";
      return [b.key, { state, error: s.error }];
    }),
  ) as Record<BotKey, LegendBotStatus>;

  const goTab = (t: Tab) => {
    setTab(t);
    setRouteView(null);
    if (t === "insights") setInsightsSeen(true);
    if (t === "routes") setUnseen(0);
  };
  const backToAllRoutes = (unchoose: boolean) => {
    if (unchoose) setChosen(null);
    setRouteView(null);
    setFocus(null);
    setShowAll(true); // all four drawn with equal emphasis to compare
    setTab("routes");
  };
  backToAllRoutesRef.current = backToAllRoutes;

  const onTabKey = (e: React.KeyboardEvent, i: number) => {
    const dir = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (!dir) return;
    e.preventDefault();
    const next = TABS[(i + dir + TABS.length) % TABS.length].key;
    goTab(next);
    document.getElementById(`tab-${next}`)?.focus();
  };

  const panelTitle = { map: "Map", plan: "Plan a trip", routes: "Four routes", insights: "Insights" }[tab];

  return (
    <div className="fixed inset-0 overflow-hidden bg-zinc-100 text-zinc-900">
      <div className="absolute inset-0">
        <RouteMap
          routes={mapRoutes}
          origin={first ? first.legs[0]?.from : null}
          destination={first ? first.legs[first.legs.length - 1]?.to : null}
          padding={mapPadding}
          colorBy="mode"
          lineScale={routeView ? 1.6 : 1}
          onRouteClick={selectFromMap}
          fitKey={routeView ? `route:${routeView}` : tab === "map" ? "map" : "panel"}
          showTtc={showTtc}
          bikeStations={bikeStations}
        />
      </div>

      <div className="absolute top-3 right-3 z-10 flex flex-col items-end gap-2 md:top-4 md:right-4 md:items-stretch">
        <LayerToggles onTtc={setShowTtc} onBikes={setBikeStations} />
        <MapLegend
          status={legendStatus}
          hiddenBots={hiddenBots}
          onToggleBot={toggleBot}
          onShowAll={() => (routeView ? backToAllRoutes(false) : showAllRoutes())}
          highlight={highlight}
          modes={usedModes}
        />
      </div>

      {highlight && done.length > 1 && !routeView && (
        <button
          type="button"
          data-testid="show-all-routes"
          onClick={showAllRoutes}
          className="absolute top-16 left-1/2 z-10 -translate-x-1/2 rounded-full bg-white px-4 py-2 text-xs font-semibold text-zinc-900 shadow-lg ring-1 ring-black/5 hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-zinc-900 md:top-4"
        >
          Show all {done.length} routes
        </button>
      )}

      {/* Wide screens: tab on the panel's edge that stays visible when the panel is hidden */}
      {!noPanel && (
      <button
        ref={showPanelBtn}
        type="button"
        data-testid="panel-toggle"
        aria-expanded={!panelHidden}
        aria-controls="main-panel"
        aria-keyshortcuts="Escape"
        onClick={() => setHidden(!panelHidden)}
        style={{ left: panelHidden ? RAIL_W - 8 : RAIL_W + PANEL_W[tab] + 8 }}
        className="absolute top-4 z-20 hidden items-center gap-1 rounded-full bg-white py-2 pr-3 pl-2 text-xs font-semibold text-zinc-800 shadow-lg ring-1 ring-black/5 transition-[left] duration-200 hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-zinc-900 motion-reduce:transition-none md:flex"
      >
        <svg aria-hidden viewBox="0 0 24 24" className={`h-4 w-4 transition-transform duration-200 ${panelHidden ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
          <path d="M15 6l-6 6 6 6" />
        </svg>
        {panelHidden ? "Show panel" : "Hide panel"}
        {panelHidden && unseen > 0 && (
          <span data-testid="panel-unseen" className="ml-1 rounded-full bg-sky-600 px-1.5 py-0.5 text-[10px] leading-none text-white">
            {unseen} new
          </span>
        )}
      </button>
      )}
      {done.length > 0 && !routeView && (
        <div
          className="absolute top-3 right-[104px] left-3 z-10 overflow-x-auto md:top-auto md:right-4 md:bottom-6 md:left-[var(--chips-left)] md:flex md:justify-center md:overflow-visible"
          style={{ "--chips-left": `${RAIL_W + (panelShown ? PANEL_W[tab] : 0) + 16}px` } as React.CSSProperties}
        >
          <div className="w-max">
            <RouteChips
              found={{ cheap: !!bots.cheap.route, fastest: !!bots.fastest.route, green: !!bots.green.route, best: !!bots.best.route }}
              hidden={hiddenBots}
              onToggle={toggleBot}
              onShowAll={showEveryRoute}
              onHover={setHoverBot}
            />
          </div>
        </div>
      )}
      {(panelHidden || tab === "map") && !routeView && unseen > 0 && (
        <button
          type="button"
          data-testid="routes-ready-notice"
          onClick={() => {
            setHidden(false);
            goTab("routes");
          }}
          className="absolute bottom-20 left-1/2 z-10 -translate-x-1/2 rounded-full bg-zinc-900/90 px-3 py-1.5 text-xs font-medium text-white shadow-lg hover:bg-zinc-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900 md:bottom-20"
        >
          <span role="status">{done.length} of 4 routes ready.</span> Compare them
        </button>
      )}
      {routeView && bots[routeView].route && (
        <div className="pointer-events-none absolute inset-x-3 bottom-20 z-20 flex justify-center md:right-4 md:bottom-6 md:left-[104px]">
          <RouteSummary
            bot={BOTS.find((b) => b.key === routeView)!}
            route={bots[routeView].route!}
            deadlineNote={deadlineNote}
            onBack={() => backToAllRoutes(false)}
            onChange={() => backToAllRoutes(true)}
          />
        </div>
      )}

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
                onClick={() => goTab(t.key)}
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
        id="main-panel"
        aria-label={panelTitle}
        data-hidden={panelHidden || undefined}
        inert={noPanel || (wide && panelHidden)}
        className={`absolute inset-x-0 bottom-16 z-10 flex h-[var(--sheet-h)] flex-col overflow-hidden rounded-t-3xl bg-white shadow-[0_-8px_30px_rgba(0,0,0,0.08)] transition-[height,transform,opacity] duration-200 motion-reduce:transition-none md:inset-x-auto md:top-4 md:bottom-auto md:left-[104px] md:h-auto md:max-h-[calc(100%-2rem)] md:w-[var(--panel-w)] md:rounded-2xl md:shadow-lg md:ring-1 md:ring-black/5 ${panelHidden ? "md:pointer-events-none md:-translate-x-[calc(100%+8rem)] md:opacity-0" : ""}`}
        style={
          {
            "--sheet-h": panelHidden ? "3.75rem" : sheetFull ? "calc(100dvh - 7rem)" : "46dvh",
            "--panel-w": `${PANEL_W[tab] || 400}px`,
            display: noPanel ? "none" : undefined,
          } as React.CSSProperties
        }
      >
        {/* Phones: tap the handle to step peek -> half -> full, or drag it up and down. */}
        <button
          type="button"
          data-testid="sheet-handle"
          onClick={() => {
            if (dragged.current) return void (dragged.current = false);
            if (panelHidden) setHidden(false);
            else setSheetFull((v) => !v);
          }}
          onPointerDown={(e) => {
            dragY.current = e.clientY;
            dragged.current = false;
            e.currentTarget.setPointerCapture(e.pointerId); // keep getting the release even when the finger leaves the handle
          }}
          onPointerCancel={() => (dragY.current = null)}
          onPointerUp={(e) => {
            if (dragY.current === null) return;
            const dy = e.clientY - dragY.current;
            dragY.current = null;
            if (Math.abs(dy) < 40) return;
            dragged.current = true;
            if (dy > 0) {
              if (sheetFull) setSheetFull(false);
              else setHidden(true);
            } else if (panelHidden) setHidden(false);
            else setSheetFull(true);
          }}
          aria-label={panelHidden ? "Show the panel" : sheetFull ? "Shrink the panel" : "Expand the panel"}
          aria-expanded={!panelHidden}
          className="flex shrink-0 touch-none justify-center pt-2 pb-1 md:hidden"
        >
          <span aria-hidden className="h-1.5 w-10 rounded-full bg-zinc-300" />
        </button>
        <header className="shrink-0 px-4 pt-1 pb-2 md:px-5 md:pt-4">
          <div className="flex items-center justify-between gap-2">
            <h1 className="text-lg font-semibold tracking-tight">
              {panelTitle}
              {panelHidden && unseen > 0 && (
                <span className="ml-2 rounded-full bg-sky-600 px-1.5 py-0.5 align-middle text-[10px] font-semibold text-white md:hidden">{unseen} new</span>
              )}
            </h1>
            <button
              type="button"
              data-testid="sheet-hide"
              onClick={() => setHidden(!panelHidden)}
              aria-label={panelHidden ? "Show the panel" : "Hide the panel to see the whole map"}
              className="rounded-full px-2.5 py-1 text-xs font-medium text-zinc-600 ring-1 ring-zinc-200 hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-zinc-900 md:hidden"
            >
              {panelHidden ? "Show" : "Hide"}
            </button>
          </div>
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

        {/* PLAN tab */}
        <div inert={!wide && panelHidden} className="contents">
        <div id="panel-plan" role="tabpanel" aria-labelledby="tab-plan" hidden={tab !== "plan"} className="flex min-h-0 flex-1 flex-col">
          <form
            className="flex min-h-0 flex-1 flex-col"
            onSubmit={(e) => {
              e.preventDefault();
              if (running || !prompt.trim() || badTime) return;
              const t = finalPrompt();
              if (t !== prompt) setPrompt(t);
              start(t);
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
                  setDeadlineText("");
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
              <TripOptions prompt={prompt} setPrompt={setPrompt} focusPrompt={() => promptRef.current?.focus()} priority={priority}
                setPriority={setPriority}
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
            </div>
            <div className="shrink-0 border-t border-zinc-100 px-4 py-3 md:px-5">
              <button
                type="submit"
                data-testid="plan-trip"
                disabled={running || !prompt.trim() || !!badTime}
                aria-describedby="plan-hint"
                className="w-full rounded-full bg-zinc-900 px-6 py-3 text-sm font-semibold text-white transition-colors duration-150 hover:bg-zinc-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900 disabled:opacity-50"
              >
                {running ? `Bots working… ${elapsed}s` : "Plan my trip"}
              </button>
              <p id="plan-hint" data-testid="plan-hint" className="mt-1.5 text-center text-xs text-zinc-500" aria-live="polite">
                {running
                  ? "The bots are working on your last trip."
                  : !prompt.trim()
                    ? "Type a trip above or pick an example to plan."
                    : badTime
                      ? "Fix the time under Deadline and leave time first (try 5pm or 17:30)."
                      : "Edit the text as much as you like before planning."}
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
                deadlineNote={deadlineNote}
                focused={focus === b.key}
                chosen={chosen === b.key}
                onFocus={() => {
                  setShowAll(focus === b.key); // clicking the open card again goes back to all routes
                  setFocus((f) => (f === b.key ? null : b.key));
                }}
                onChoose={() => {
                  setChosen(b.key);
                  setFocus(b.key);
                  setShowAll(false);
                  setRouteView(b.key);
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
        </div>
      </section>

      {drawer && <BotDrawer bots={bots} running={running} onClose={closeDrawer} />}
    </div>
  );
}

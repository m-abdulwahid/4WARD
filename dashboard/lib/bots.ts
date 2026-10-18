import type { Candidate } from "@/lib/api";

// One accent colour per bot. Darker shades so white text on them stays readable (>= 4.5:1).
// Keys are internal (bots, backend, data-testids use them); only name/blurb are shown.
export const BOTS = [
  { key: "cheap", name: "Economic", color: "#b45309", blurb: "Lowest total cost" },
  { key: "fastest", name: "Efficient", color: "#dc2626", blurb: "Shortest door to door time" },
  { key: "green", name: "Environmental", color: "#047857", blurb: "Lowest CO₂" },
  { key: "reliable", name: "Reliable", color: "#7c3aed", blurb: "Most likely to arrive on time" },
] as const;
export type BotKey = (typeof BOTS)[number]["key"];
export type BotInfo = (typeof BOTS)[number];

/** Bots whose finished route is the same route (same id) share one group, in the order given. Bots with no
 * route each get their own group so a failed bot is never hidden. */
export function groupByRoute(order: readonly BotInfo[], bots: Record<BotKey, BotState>): BotInfo[][] {
  const groups: BotInfo[][] = [];
  const byRoute = new Map<string, BotInfo[]>();
  for (const b of order) {
    const r = bots[b.key].route;
    if (!r) {
      groups.push([b]);
      continue;
    }
    const g = byRoute.get(r.id);
    if (g) g.push(b);
    else {
      const fresh = [b];
      byRoute.set(r.id, fresh);
      groups.push(fresh);
    }
  }
  return groups;
}

export type BotStatus = "WAITING" | "SCANNING" | "ROUTING" | "DONE" | "FAILED";

export type StepLogEntry = {
  n: number;
  action?: string;
  testid?: string;
  text?: string;
  reason?: string;
  outcome?: string;
};

export type BotState = {
  status: BotStatus;
  frame?: string;
  liveViewUrl?: string; // Steel's own embeddable live view (BROWSER_BACKEND=steel only); preferred over `frame` when set
  lastReason?: string;
  steps: number;
  log: StepLogEntry[];
  brain?: string;
  elapsed?: number;
  route?: Candidate | null;
  alternatives?: Candidate[];
  nextBest?: Candidate | null; // this bot's best DIFFERENT route for its own goal
  error?: string | null;
};

export const blankBots = (): Record<BotKey, BotState> => ({
  cheap: { status: "WAITING", steps: 0, log: [] },
  fastest: { status: "WAITING", steps: 0, log: [] },
  green: { status: "WAITING", steps: 0, log: [] },
  reliable: { status: "WAITING", steps: 0, log: [] },
});

export const STATUS_CHIP: Record<BotStatus, { label: string; cls: string }> = {
  WAITING: { label: "Waiting", cls: "bg-zinc-100 text-zinc-600" },
  SCANNING: { label: "Scanning", cls: "bg-amber-50 text-amber-800 ring-1 ring-amber-200" },
  ROUTING: { label: "Routing", cls: "bg-sky-50 text-sky-800 ring-1 ring-sky-200" },
  DONE: { label: "Done", cls: "bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200" },
  FAILED: { label: "Failed", cls: "bg-red-50 text-red-800 ring-1 ring-red-200" },
};

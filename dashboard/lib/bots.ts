import type { Candidate } from "@/lib/api";

// One accent colour per bot. Darker shades so white text on them stays readable (>= 4.5:1).
// Keys are internal (bots, backend, data-testids use them); only name/short/blurb are shown.
// "short" is used where space is tight (narrow phones).
export const BOTS = [
  { key: "cheap", name: "Economic", short: "Economic", color: "#b45309", blurb: "Lowest total cost" },
  { key: "fastest", name: "Efficient", short: "Efficient", color: "#dc2626", blurb: "Shortest door to door time" },
  { key: "green", name: "Environmentally friendly", short: "Eco", color: "#047857", blurb: "Lowest CO₂" },
  { key: "best", name: "Best overall", short: "Best overall", color: "#7c3aed", blurb: "Balanced trade-offs" },
] as const;
export type BotKey = (typeof BOTS)[number]["key"];

export type BotStatus = "WAITING" | "SCANNING" | "ROUTING" | "DONE" | "FAILED";

export type BotState = {
  status: BotStatus;
  frame?: string;
  lastReason?: string;
  steps: number;
  brain?: string;
  elapsed?: number;
  route?: Candidate | null;
  error?: string | null;
};

export const blankBots = (): Record<BotKey, BotState> => ({
  cheap: { status: "WAITING", steps: 0 },
  fastest: { status: "WAITING", steps: 0 },
  green: { status: "WAITING", steps: 0 },
  best: { status: "WAITING", steps: 0 },
});

export const STATUS_CHIP: Record<BotStatus, { label: string; cls: string }> = {
  WAITING: { label: "Waiting", cls: "bg-zinc-100 text-zinc-600" },
  SCANNING: { label: "Scanning", cls: "bg-amber-50 text-amber-800 ring-1 ring-amber-200" },
  ROUTING: { label: "Routing", cls: "bg-sky-50 text-sky-800 ring-1 ring-sky-200" },
  DONE: { label: "Done", cls: "bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200" },
  FAILED: { label: "Failed", cls: "bg-red-50 text-red-800 ring-1 ring-red-200" },
};

// How the backend is storing and reading feed history, as reported by /insights/* ("tiger" field).
export type TigerStatus = "connected" | "unreachable" | "replay" | "not configured";
export type DbStatus = { backend: string; tiger?: TigerStatus };

/** Short chip text and a full sentence for the same database status. */
export function dbLabel(s: DbStatus | null): { chip: string; long: string; tone: "good" | "neutral" | "warn" } {
  if (!s) return { chip: "Database unknown", long: "Database status unknown (backend offline)", tone: "warn" };
  if (s.backend === "tigerdata") return { chip: "Tiger Data connected", long: "Tiger Data (Postgres + TimescaleDB) continuous aggregates, hourly", tone: "good" };
  switch (s.tiger) {
    case "unreachable":
      return { chip: "Local database (Tiger Data unreachable)", long: "local SQLite hourly rollup. Tiger Data is configured but unreachable right now", tone: "warn" };
    case "replay":
      return { chip: "Local database", long: "local SQLite hourly rollup (replay mode, Tiger Data not used)", tone: "neutral" };
    default:
      return { chip: "Local database", long: "local SQLite hourly rollup (Tiger Data not configured)", tone: "neutral" };
  }
}

export const TONE: Record<"good" | "neutral" | "warn", string> = {
  good: "bg-emerald-50 text-emerald-800",
  neutral: "bg-zinc-100 text-zinc-700",
  warn: "bg-amber-50 text-amber-900",
};

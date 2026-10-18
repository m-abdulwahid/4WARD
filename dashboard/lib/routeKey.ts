import type { Candidate } from "@/lib/api";

/**
 * Same legs and same geometry = the same route. Used to draw a route found by several bots once,
 * and to say "same route as ..." on the chips.
 */
export function routeSignature(c: Candidate): string {
  return c.legs
    .map((l) => {
      const g = l.geometry;
      const end = (p?: [number, number]) => (p ? `${p[0].toFixed(5)},${p[1].toFixed(5)}` : "-");
      return `${l.mode}:${g.length}:${end(g[0])}:${end(g[Math.floor(g.length / 2)])}:${end(g[g.length - 1])}`;
    })
    .join("|");
}

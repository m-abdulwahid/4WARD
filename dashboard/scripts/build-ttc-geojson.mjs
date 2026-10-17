// Converts data/transit_network.json (TTC GTFS static, built by backend/build_transit_network.py)
// into public/ttc-lines.geojson for the map's "TTC lines" layer.
// Lines join stops in order, so they follow the route but not the exact track.
// Run: node scripts/build-ttc-geojson.mjs  (also runs before `npm run dev` and `npm run build`)
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(here, "../../data/transit_network.json");
const OUT = resolve(here, "../public/ttc-lines.geojson");

if (!existsSync(SRC)) {
  // Dashboard deployed without the repo's data/ folder: keep the committed copy.
  console.log(`ttc-lines: ${SRC} not found, keeping existing ${OUT}`);
  process.exit(0);
}

const net = JSON.parse(readFileSync(SRC, "utf8"));
const seen = new Set();
const features = [];
for (const line of net.lines) {
  // One direction per route is enough to draw it.
  if (seen.has(line.route_id) || line.stops.length < 2) continue;
  seen.add(line.route_id);
  features.push({
    type: "Feature",
    properties: { route_id: line.route_id, name: line.name, type: line.type, color: line.color },
    geometry: {
      type: "LineString",
      coordinates: line.stops.map((s) => [Math.round(s.lon * 1e5) / 1e5, Math.round(s.lat * 1e5) / 1e5]),
    },
  });
}

// Timestamp of the network file itself, so the map caption says how old the data is.
const builtAt = statSync(SRC).mtime.toISOString();
writeFileSync(OUT, JSON.stringify({ type: "FeatureCollection", source: net.source, built_at: builtAt, features }));
console.log(`ttc-lines: wrote ${features.length} lines to ${OUT}`);

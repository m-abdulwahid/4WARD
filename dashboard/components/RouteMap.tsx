"use client";
import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Candidate } from "@/lib/api";
import { MODES, iconSvg, inkOn, legColor, legMode } from "@/lib/modes";

export type MapRoute = { id: string; color: string; candidate: Candidate; emphasized?: boolean; label?: string; visible?: boolean };
export type MapPadding = { top: number; right: number; bottom: number; left: number };
export type BikeStation = { id: string; name: string; lat: number; lon: number; bikes: number; ebikes: number; docks: number };

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

/** Point a fraction f of the way along a line (by length, so straight 2-point legs work too). */
function along(coords: [number, number][], f: number): [number, number] {
  const seg = coords.slice(1).map((c, i) => Math.hypot(c[0] - coords[i][0], c[1] - coords[i][1]));
  let left = seg.reduce((a, b) => a + b, 0) * f;
  for (let i = 0; i < seg.length; i++) {
    if (left <= seg[i] || i === seg.length - 1) {
      const t = seg[i] ? Math.min(1, left / seg[i]) : 0;
      return [coords[i][0] + (coords[i + 1][0] - coords[i][0]) * t, coords[i][1] + (coords[i + 1][1] - coords[i][1]) * t];
    }
    left -= seg[i];
  }
  return coords[0];
}

// Turbopack cannot bundle the ESM worker; serve the copy in /public instead.
maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");

const TORONTO: [number, number] = [-79.3832, 43.6532];

// Basemap: OpenFreeMap "Liberty" (OpenMapTiles schema). Free, no API key, no usage limits, commercial use allowed;
// attribution "OpenFreeMap © OpenMapTiles Data from OpenStreetMap" comes from the style and is shown by MapLibre.
const BASEMAP = "https://tiles.openfreemap.org/styles/liberty";

// Liberty's parks and water are pastel; these stronger fills make them read at a glance without fighting the routes.
const BOOST: [string, "fill-color" | "line-color", string][] = [
  ["park", "fill-color", "#b9dfa0"],
  ["landcover_grass", "fill-color", "#b3d996"],
  ["landcover_wood", "fill-color", "#9fd085"],
  ["water", "fill-color", "#86b6f2"],
  ["waterway_river", "line-color", "#86b6f2"],
  ["waterway_other", "line-color", "#86b6f2"],
];

// Used only if the vector basemap cannot load: the plain OpenStreetMap raster tiles the map used before.
const OSM_FALLBACK: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#eef0ea" } }, // something to draw on if even these fail
    { id: "osm", type: "raster", source: "osm" },
  ],
};
const NO_PAD: MapPadding = { top: 60, right: 60, bottom: 60, left: 60 };

/** Same itinerary found by several bots: draw it once and name all of them. */
const sameRoute = (r: MapRoute) => `${r.candidate.id}|${r.candidate.duration_min}|${r.candidate.cost}|${r.candidate.legs.length}`;
const OFFSET_PX = 5; // routes side by side: neighbours on the same street are this far apart

/**
 * Home map: every leg in its transport mode's style. All found routes are drawn at once; each distinct route gets a
 * small sideways offset so routes sharing a street or subway line stay visible next to each other, plus a label
 * naming the bot(s) that found it. Hidden routes (route chips, legend eyes) are left out, and the map refits to what is shown.
 */
function drawByMode(m: maplibregl.Map, routes: MapRoute[], bounds: maplibregl.LngLatBounds, markers: maplibregl.Marker[], scale = 1) {
  // fit to the routes that are shown (all of them if every one is hidden)
  const shown = routes.filter((r) => r.visible !== false);
  (shown.length ? shown : routes).forEach((r) => r.candidate.legs.forEach((l) => l.geometry.forEach(([la, lo]) => bounds.extend([lo, la]))));
  const groups: MapRoute[][] = [];
  routes
    .filter((r) => r.visible !== false)
    .forEach((r) => {
      const g = groups.find((x) => sameRoute(x[0]) === sameRoute(r));
      if (g) g.push(r);
      else groups.push([r]);
    });
  const n = groups.length;
  const anyDim = groups.some((rs) => rs.every((r) => r.emphasized === false));
  // offsets follow the stable group order; drawing order puts dimmed routes underneath
  const placed = groups.map((rs, gi) => ({ rs, offset: n > 1 ? (gi - (n - 1) / 2) * OFFSET_PX : 0, dim: rs.every((r) => r.emphasized === false) }));
  [...placed].sort((a, b) => Number(!a.dim) - Number(!b.dim)).forEach(({ rs, offset, dim }, order) => {
    const key = rs.map((r) => r.id).join("-");
    rs[0].candidate.legs.forEach((leg, i) => {
      const coords = leg.geometry.map(([la, lo]) => [lo, la] as [number, number]);
      if (coords.length < 2) return;
      const id = `rt-${key}-${i}`;
      m.addSource(id, { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } } });
      const mode = legMode(leg, rs[0].candidate.id);
      const st = MODES[mode];
      const w = (dim ? Math.max(2.5, st.width * 0.55) : st.width) * scale;
      if (mode !== "walk") {
        m.addLayer({
          id: `${id}-casing`,
          type: "line",
          source: id,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": dim ? "#ffffff" : st.casing, "line-width": w + (dim ? 2.5 : 3.5) * scale, "line-opacity": dim ? 0.8 : 0.95, "line-offset": offset },
        });
      }
      m.addLayer({
        id,
        type: "line",
        source: id,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": legColor(leg, mode),
          "line-width": w,
          // dimmed legs stay clearly visible on the colourful basemap: thinner, not washed out
          "line-opacity": dim ? 0.75 : 1,
          "line-offset": offset,
          ...(st.dash ? { "line-dasharray": st.dash } : {}),
        },
      });
      // Mode icons on the selected route (or the only route), skipping legs too short to see.
      if (!dim && (anyDim || n === 1) && leg.distance_km >= 0.15) {
        const bg = legColor(leg, mode);
        const e = document.createElement("div");
        e.setAttribute("role", "img");
        e.setAttribute("aria-label", `${st.label} leg`);
        e.innerHTML = iconSvg(mode, 13, inkOn(bg));
        e.style.cssText = `width:24px;height:24px;border-radius:50%;background:${bg};display:flex;align-items:center;justify-content:center;border:2px solid #fff;box-shadow:0 1px 4px #0004`;
        markers.push(new maplibregl.Marker({ element: e }).setLngLat(along(coords, 0.5)).addTo(m));
      }
    });
    // Label: which bot(s) found this route. Staggered along the route so neighbours do not stack.
    const legs = rs[0].candidate.legs.filter((l) => l.geometry.length > 1);
    if (!legs.length) return;
    const longest = legs.reduce((a, b) => (b.distance_km > a.distance_km ? b : a));
    const f = [0.25, 0.75, 0.4, 0.6, 0.15, 0.85][groups.indexOf(rs) % 6];
    const [lo, la] = along(longest.geometry.map(([a, b]) => [b, a] as [number, number]), f);
    const e = document.createElement("div");
    e.setAttribute("data-testid", `map-label-${key}`);
    e.style.cssText = `display:flex;gap:4px;align-items:center;padding:3px 8px 3px 6px;border-radius:999px;background:#fff;box-shadow:0 2px 8px #0003;font:600 11px/1.2 var(--font-geist-sans),sans-serif;color:#18181b;white-space:nowrap;opacity:${dim ? 0.75 : 1};z-index:${order}`;
    rs.forEach((r, k) => {
      const dot = document.createElement("span");
      dot.style.cssText = `width:9px;height:9px;border-radius:50%;background:${r.color};flex:none`;
      e.append(dot, document.createTextNode(r.label ?? r.id), ...(k < rs.length - 1 ? [document.createTextNode(" ")] : []));
    });
    markers.push(new maplibregl.Marker({ element: e, anchor: "bottom", offset: [0, -8] }).setLngLat([lo, la]).addTo(m));
  });
}

export default function RouteMap({
  routes,
  origin,
  destination,
  padding = NO_PAD,
  showTtc = false,
  bikeStations = null,
  colorBy = "route",
  lineScale = 1,
}: {
  routes: MapRoute[];
  origin?: [number, number] | null; // [lat, lon]
  destination?: [number, number] | null;
  padding?: MapPadding; // space covered by floating panels, so routes are fitted into the visible part
  showTtc?: boolean; // TTC subway/streetcar lines from /ttc-lines.geojson
  bikeStations?: BikeStation[] | null; // null hides the Bike Share layer
  colorBy?: "route" | "mode"; // "route": one colour per route (planner); "mode": each leg by transport mode (home)
  lineScale?: number; // thicker lines for the clean single-route view
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const ready = useRef(false);
  const fitted = useRef("");
  const [styleGen, setStyleGen] = useState(0); // bumped on every style load, so overlays and routes are redrawn
  const pad = useRef(padding);
  pad.current = padding;

  useEffect(() => {
    if (!el.current || map.current) return;
    const m = new maplibregl.Map({
      container: el.current,
      center: TORONTO,
      zoom: 11,
      attributionControl: { compact: true },
      style: BASEMAP,
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

    // Fall back to plain OSM raster tiles if the vector basemap cannot load (offline, provider down).
    let fellBack = false;
    let loadedOnce = false;
    let tileErrors = 0;
    const fallBack = () => {
      if (fellBack) return;
      fellBack = true;
      ready.current = false;
      m.setStyle(OSM_FALLBACK, { diff: false });
    };
    const timer = setTimeout(() => !loadedOnce && fallBack(), 8000);
    m.on("error", (e: maplibregl.ErrorEvent & { sourceId?: string }) => {
      if (fellBack) return;
      if (!loadedOnce) return fallBack(); // the style itself failed
      if (e.sourceId === "openmaptiles" && ++tileErrors >= 4) fallBack(); // its tiles keep failing
    });

    // Fires for the first style and again after a fallback: (re)add overlays, then let the effects redraw.
    m.on("style.load", () => {
      loadedOnce = true;
      clearTimeout(timer);
      if (!fellBack) {
        for (const [layer, prop, color] of BOOST) {
          if (m.getLayer(layer)) m.setPaintProperty(layer, prop, color);
        }
      }
      // Overlays are added before any route, so routes always draw on top of them.
      m.addSource("ttc", { type: "geojson", data: "/ttc-lines.geojson" });
      m.addLayer({
        id: "ttc-lines",
        type: "line",
        source: "ttc",
        layout: { visibility: "none", "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["coalesce", ["get", "color"], "#52525b"],
          "line-width": ["match", ["get", "type"], "subway", 4, 2.5],
          "line-opacity": 0.55,
        },
      });
      m.addSource("bikes", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "bike-stations",
        type: "circle",
        source: "bikes",
        layout: { visibility: "none" },
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2.5, 15, 6],
          // dark teal: has e-bikes, teal: classic bikes only, grey: empty
          "circle-color": ["case", [">", ["get", "ebikes"], 0], "#0f766e", [">", ["get", "bikes"], 0], "#2dd4bf", "#a1a1aa"],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1,
        },
      });
      ready.current = true;
      setStyleGen((g) => g + 1);
    });
    map.current = m;
    return () => {
      clearTimeout(timer);
      m.remove();
      map.current = null;
      ready.current = false;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const apply = () => {
      m.setLayoutProperty("ttc-lines", "visibility", showTtc ? "visible" : "none");
      m.setLayoutProperty("bike-stations", "visibility", bikeStations ? "visible" : "none");
      (m.getSource("bikes") as maplibregl.GeoJSONSource).setData({
        type: "FeatureCollection",
        features: (bikeStations ?? []).map((s) => ({
          type: "Feature",
          properties: { bikes: s.bikes, ebikes: s.ebikes },
          geometry: { type: "Point", coordinates: [s.lon, s.lat] },
        })),
      });
    };
    if (ready.current) apply(); // otherwise style.load bumps styleGen and this runs again
  }, [showTtc, bikeStations, styleGen]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const draw = () => {
      (m.getStyle().layers ?? []).forEach((l: { id: string }) => {
        if (l.id.startsWith("rt-")) m.removeLayer(l.id);
      });
      Object.keys(m.getStyle().sources ?? {}).forEach((s) => {
        if (s.startsWith("rt-")) m.removeSource(s);
      });
      markers.current.forEach((k) => k.remove());
      markers.current = [];
      const bounds = new maplibregl.LngLatBounds();
      if (colorBy === "mode") drawByMode(m, routes, bounds, markers.current, lineScale);
      else {
        // planner: one colour per route; dimmed routes first so the highlighted one is drawn on top
        const ordered = [...routes].sort((a, b) => Number(a.emphasized !== false) - Number(b.emphasized !== false));
        ordered.forEach((r) => {
          const dim = r.emphasized === false;
          r.candidate.legs.forEach((leg, i) => {
            const coords = leg.geometry.map(([la, lo]) => [lo, la] as [number, number]);
            if (coords.length < 2) return;
            coords.forEach((c) => bounds.extend(c));
            const id = `rt-${r.id}-${i}`;
            m.addSource(id, { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } } });
            if (!dim) {
              m.addLayer({
                id: `${id}-casing`,
                type: "line",
                source: id,
                layout: { "line-cap": "round", "line-join": "round" },
                paint: { "line-color": "#ffffff", "line-width": 9, "line-opacity": 0.9 },
              });
            }
            m.addLayer({
              id,
              type: "line",
              source: id,
              layout: { "line-cap": "round", "line-join": "round" },
              paint: {
                "line-color": r.color,
                "line-width": dim ? 3 : 5.5,
                "line-opacity": dim ? 0.3 : 1,
                ...(leg.mode === "walk" ? { "line-dasharray": [0.6, 1.6] } : {}),
              },
            });
          });
        });
      }
      const mk = (ll: [number, number] | null | undefined, color: string, label: string, letter: string) => {
        if (!ll) return;
        const e = document.createElement("div");
        e.setAttribute("aria-label", label);
        e.setAttribute("role", "img");
        e.textContent = letter;
        e.style.cssText = `width:26px;height:26px;border-radius:50%;background:${color};color:#fff;font:700 13px/26px var(--font-geist-sans),sans-serif;text-align:center;border:2px solid #fff;box-shadow:0 2px 6px #0003`;
        markers.current.push(new maplibregl.Marker({ element: e }).setLngLat([ll[1], ll[0]]).addTo(m));
        bounds.extend([ll[1], ll[0]]);
      };
      mk(origin, "#18181b", "Start", "A");
      mk(destination, "#18181b", "Destination", "B");
      // refit when the set of shown routes or the endpoints change, not when one is highlighted
      const sig = JSON.stringify([routes.filter((r) => r.visible !== false).map((r) => r.id), origin, destination]);
      if (!bounds.isEmpty() && sig !== fitted.current) {
        fitted.current = sig;
        m.fitBounds(bounds, { padding: pad.current, duration: 500, maxZoom: 15 });
      }
    };
    if (ready.current) draw();
  }, [routes, origin, destination, colorBy, styleGen, lineScale]);

  return <div ref={el} className="h-full w-full" data-testid="map" role="application" aria-label="Route map of Toronto" />;
}

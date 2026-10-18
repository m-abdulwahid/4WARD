"use client";
import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Candidate } from "@/lib/api";
import { MODES, ModeKey, iconSvg, inkOn, legColor, legMode } from "@/lib/modes";

export type MapRoute = { id: string; color: string; candidate: Candidate; emphasized?: boolean; label?: string; visible?: boolean };
export type MapPadding = { top: number; right: number; bottom: number; left: number };
export type BikeStation = { id: string; name: string; lat: number; lon: number; bikes: number; ebikes: number; docks: number };
// One per route being animated in timelapse mode: its current position and mode, in map [lng, lat] order.
export type Vehicle = { id: string; color: string; lngLat: [number, number]; mode: ModeKey; arrived?: boolean };

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

/**
 * Home map: every leg in its transport mode's style. All found routes are drawn at once. Routes that share a street
 * or line sit side by side (each distinct route gets its own line-offset, spaced by the widest line), and in the
 * overview every line carries a thin edge in its bot's colour over a white casing, so each route stays identifiable.
 * The selected route (or the only route) keeps the plain mode styling. Each route has one plain label with its bot's name.
 * Hidden routes (route chips, legend eyes) are left out, and the map fits what is shown.
 * Returns the line layer ids, so clicks on them can select a route.
 */
function drawByMode(m: maplibregl.Map, routes: MapRoute[], bounds: maplibregl.LngLatBounds, markers: maplibregl.Marker[], scale = 1): string[] {
  const layers: string[] = [];
  // fit to the routes that are shown (all of them if every one is hidden)
  const shown = routes.filter((r) => r.visible !== false);
  (shown.length ? shown : routes).forEach((r) => r.candidate.legs.forEach((l) => l.geometry.forEach(([la, lo]) => bounds.extend([lo, la]))));
  // every route is drawn on its own (even when two bots found the same path), so every colour shows side by side
  const groups: MapRoute[][] = shown.map((r) => [r]);
  const n = groups.length;
  const anyDim = groups.some((rs) => rs.every((r) => r.emphasized === false));
  const EDGE = 1.5 * scale;
  // how a group's legs are drawn: "plain" = mode styling (selected or only route), "edged" = overview with a bot edge
  const styleOf = (dim: boolean) => (!dim && (anyDim || n === 1) ? "plain" : "edged");
  const widthOf = (mode: ModeKey, dim: boolean) =>
    (dim ? Math.max(2.5, MODES[mode].width * 0.55) : styleOf(dim) === "edged" ? Math.min(MODES[mode].width, 4.5) : MODES[mode].width) * scale;
  // spacing between neighbours = the widest line drawn (plus its edges), so lines on the same street never cover each other
  const spacing =
    n > 1
      ? Math.max(
          ...groups.flatMap((rs) => {
            const dim = rs.every((r) => r.emphasized === false);
            return rs[0].candidate.legs.map((l) => widthOf(legMode(l, rs[0].candidate.id), dim) + (styleOf(dim) === "edged" ? 2 * EDGE : 3 * scale));
          }),
        ) + 1
      : 0;
  // offsets follow the stable group order; drawing order puts dimmed routes underneath
  const placed = groups.map((rs, gi) => ({ rs, offset: n > 1 ? (gi - (n - 1) / 2) * spacing : 0, dim: rs.every((r) => r.emphasized === false) }));
  [...placed].sort((a, b) => Number(!a.dim) - Number(!b.dim)).forEach(({ rs, offset, dim }, order) => {
    const key = rs.map((r) => r.id).join("-");
    const style = styleOf(dim);
    rs[0].candidate.legs.forEach((leg, i) => {
      const coords = leg.geometry.map(([la, lo]) => [lo, la] as [number, number]);
      if (coords.length < 2) return;
      const id = `rt-${key}-${i}`;
      // properties say which bots this line belongs to, for click-to-select
      m.addSource(id, { type: "geojson", data: { type: "Feature", properties: { routes: rs.map((r) => r.id).join(",") }, geometry: { type: "LineString", coordinates: coords } } });
      const mode = legMode(leg, rs[0].candidate.id);
      const st = MODES[mode];
      const w = widthOf(mode, dim);
      const op = dim ? 0.75 : 1;
      const line = (lid: string, color: string, width: number, opacity: number, extra: Record<string, unknown> = {}) => {
        m.addLayer({
          id: lid,
          type: "line",
          source: id,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": color, "line-width": width, "line-opacity": opacity, "line-offset": offset, ...extra },
        });
        layers.push(lid);
      };
      if (style === "edged") {
        line(`${id}-casing`, "#ffffff", w + 2 * EDGE + 2, dim ? 0.8 : 0.95);
        line(`${id}-edge`, rs[0].color, w + 2 * EDGE, op); // the bot's own colour along the route
      } else if (mode !== "walk") {
        line(`${id}-casing`, st.casing, w + 3.5 * scale, 0.95);
      }
      line(id, legColor(leg, mode), w, op, st.dash ? { "line-dasharray": st.dash } : {});
      // Mode icons on the selected route (or the only route), skipping legs too short to see.
      if (style === "plain" && leg.distance_km >= 0.15) {
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
    e.style.cssText = `display:flex;gap:4px;align-items:center;padding:3px 8px 3px 6px;border-radius:999px;background:#fff;box-shadow:0 2px 8px #0003;font:600 11px/1.2 var(--font-geist-sans),sans-serif;color:#18181b;white-space:nowrap;z-index:${order}`;
    rs.forEach((r, k) => {
      const dot = document.createElement("span");
      dot.style.cssText = `width:9px;height:9px;border-radius:50%;background:${r.color};flex:none`;
      e.append(dot, document.createTextNode(r.label ?? r.id), ...(k < rs.length - 1 ? [document.createTextNode(" ")] : []));
    });
    // opacity goes through the Marker (it manages the element's opacity itself)
    markers.push(new maplibregl.Marker({ element: e, anchor: "bottom", offset: [0, -8], opacity: dim ? "0.6" : "1" }).setLngLat([lo, la]).addTo(m));
  });
  return layers;
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
  onRouteClick,
  fitKey = "",
  vehicles = null,
}: {
  routes: MapRoute[];
  origin?: [number, number] | null; // [lat, lon]
  destination?: [number, number] | null;
  padding?: MapPadding; // space covered by floating panels, so routes are fitted into the visible part
  showTtc?: boolean; // TTC subway/streetcar lines from /ttc-lines.geojson
  bikeStations?: BikeStation[] | null; // null hides the Bike Share layer
  colorBy?: "route" | "mode"; // "route": one colour per route (planner); "mode": each leg by transport mode (home)
  lineScale?: number; // thicker lines for the clean single-route view
  onRouteClick?: (id: string) => void; // a route line was clicked (home map); gets the route id (bot key)
  fitKey?: string; // changes when the free map area changes on purpose (e.g. Map tab vs a tab with a panel): refit then
  vehicles?: Vehicle[] | null; // timelapse mode: one moving marker per animated route, updated every frame
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const ready = useRef(false);
  const fitted = useRef("");
  const routeLayers = useRef<string[]>([]);
  const onClickRef = useRef(onRouteClick);
  onClickRef.current = onRouteClick;
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

    // Click a route line to select it (a few pixels of slack so thin lines are easy to hit).
    const routeAt = (pt: maplibregl.Point): string | null => {
      const ids = routeLayers.current.filter((id) => m.getLayer(id));
      if (!ids.length || !onClickRef.current) return null;
      const hit = m.queryRenderedFeatures([[pt.x - 6, pt.y - 6], [pt.x + 6, pt.y + 6]], { layers: ids })[0];
      return (hit?.properties?.routes as string | undefined)?.split(",")[0] ?? null;
    };
    m.on("click", (e) => {
      const id = routeAt(e.point);
      if (id) onClickRef.current?.(id);
    });
    m.on("mousemove", (e) => {
      m.getCanvas().style.cursor = routeAt(e.point) ? "pointer" : "";
    });

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
      routeLayers.current = [];
      if (colorBy === "mode") routeLayers.current = drawByMode(m, routes, bounds, markers.current, lineScale);
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
      const sig = JSON.stringify([routes.filter((r) => r.visible !== false).map((r) => r.id), origin, destination, fitKey]);
      if (!bounds.isEmpty() && sig !== fitted.current) {
        fitted.current = sig;
        m.fitBounds(bounds, { padding: pad.current, duration: 500, maxZoom: 15 });
      }
    };
    if (ready.current) draw();
  }, [routes, origin, destination, colorBy, styleGen, lineScale, fitKey]);

  // Timelapse vehicle markers: moved every frame via setLngLat (cheap), never recreated, so motion is
  // smooth. The element itself is only rebuilt when a vehicle's mode changes (it switched legs).
  const vehicleMarkers = useRef<Map<string, { marker: maplibregl.Marker; mode: ModeKey; arrived: boolean }>>(new Map());
  useEffect(() => {
    const m = map.current;
    if (!m || !ready.current) return;
    const live = new Set((vehicles ?? []).map((v) => v.id));
    for (const [id, entry] of vehicleMarkers.current) {
      if (!live.has(id)) {
        entry.marker.remove();
        vehicleMarkers.current.delete(id);
      }
    }
    for (const v of vehicles ?? []) {
      let entry = vehicleMarkers.current.get(v.id);
      if (!entry || entry.mode !== v.mode || entry.arrived !== Boolean(v.arrived)) {
        entry?.marker.remove();
        const e = document.createElement("div");
        e.setAttribute("role", "img");
        e.setAttribute("aria-label", `${MODES[v.mode].label} vehicle, ${v.arrived ? "arrived" : "en route"}`);
        const ring = v.arrived ? "0 0 0 3px #fff, 0 0 0 5px " + v.color : "0 0 0 3px #fff";
        e.style.cssText = `width:30px;height:30px;border-radius:50%;background:${v.color};display:flex;align-items:center;justify-content:center;box-shadow:${ring},0 2px 8px #0005;transition:opacity .15s;opacity:${v.arrived ? 0.55 : 1}`;
        e.innerHTML = iconSvg(v.mode, 16, inkOn(v.color));
        const marker = new maplibregl.Marker({ element: e }).setLngLat(v.lngLat).addTo(m);
        entry = { marker, mode: v.mode, arrived: Boolean(v.arrived) };
        vehicleMarkers.current.set(v.id, entry);
      } else {
        entry.marker.setLngLat(v.lngLat);
      }
    }
  }, [vehicles, styleGen]);

  return <div ref={el} className="h-full w-full" data-testid="map" role="application" aria-label="Route map of the Greater Toronto Area" />;
}

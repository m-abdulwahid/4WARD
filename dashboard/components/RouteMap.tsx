"use client";
import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Candidate } from "@/lib/api";
import { MODES, iconSvg, inkOn, legColor, legMode } from "@/lib/modes";

export type MapRoute = { id: string; color: string; candidate: Candidate; emphasized?: boolean; label?: string };
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
const NO_PAD: MapPadding = { top: 60, right: 60, bottom: 60, left: 60 };

export default function RouteMap({
  routes,
  origin,
  destination,
  padding = NO_PAD,
  showTtc = false,
  bikeStations = null,
  colorBy = "route",
}: {
  routes: MapRoute[];
  origin?: [number, number] | null; // [lat, lon]
  destination?: [number, number] | null;
  padding?: MapPadding; // space covered by floating panels, so routes are fitted into the visible part
  showTtc?: boolean; // TTC subway/streetcar lines from /ttc-lines.geojson
  bikeStations?: BikeStation[] | null; // null hides the Bike Share layer
  colorBy?: "route" | "mode"; // "route": one colour per route (planner); "mode": each leg by transport mode (home)
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const ready = useRef(false);
  const fitted = useRef("");
  const pad = useRef(padding);
  pad.current = padding;

  useEffect(() => {
    if (!el.current || map.current) return;
    const m = new maplibregl.Map({
      container: el.current,
      center: TORONTO,
      zoom: 11,
      attributionControl: { compact: true },
      // Positron style from OpenFreeMap: free, no API key (CARTO's own Positron tiles now need a key)
      style: "https://tiles.openfreemap.org/styles/positron",
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    m.on("load", () => {
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
    });
    map.current = m;
    return () => {
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
    if (ready.current) apply();
    else m.once("load", apply);
  }, [showTtc, bikeStations]);

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
      // dimmed routes first so the highlighted one is drawn on top
      const ordered = [...routes].sort((a, b) => Number(a.emphasized !== false) - Number(b.emphasized !== false));
      const byMode = colorBy === "mode";
      const anyDim = routes.some((r) => r.emphasized === false);
      const distinct = new Set(routes.map((r) => r.candidate.id)).size;
      const iconsDone = new Set<string>(); // routes sharing a candidate draw identical legs: one set of icons is enough
      ordered.forEach((r) => {
        const dim = r.emphasized === false;
        r.candidate.legs.forEach((leg, i) => {
          const coords = leg.geometry.map(([la, lo]) => [lo, la] as [number, number]);
          if (coords.length < 2) return;
          coords.forEach((c) => bounds.extend(c));
          const id = `rt-${r.id}-${i}`;
          m.addSource(id, { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } } });
          const mode = legMode(leg, r.candidate.id);
          const st = MODES[mode];
          const walk = mode === "walk";
          if (byMode) {
            // Mode colours stay visible when dimmed: thinner and lighter, but the same hue and pattern.
            const w = dim ? Math.max(2.5, st.width * 0.55) : st.width;
            if (!walk) {
              m.addLayer({
                id: `${id}-casing`,
                type: "line",
                source: id,
                layout: { "line-cap": "round", "line-join": "round" },
                paint: { "line-color": st.casing, "line-width": w + (dim ? 2 : 3.5), "line-opacity": dim ? 0.5 : 0.95 },
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
                "line-opacity": dim ? 0.5 : 1,
                ...(st.dash ? { "line-dasharray": st.dash } : {}),
              },
            });
            // Mode icon in the middle of each leg of the route in focus (skip legs too short to see).
            const iconKey = `${r.candidate.id}-${i}`;
            if (!dim && (anyDim || distinct === 1) && leg.distance_km >= 0.15 && !iconsDone.has(iconKey)) {
              iconsDone.add(iconKey);
              const bg = legColor(leg, mode);
              const e = document.createElement("div");
              e.setAttribute("role", "img");
              e.setAttribute("aria-label", `${st.label} leg`);
              e.innerHTML = iconSvg(mode, 13, inkOn(bg));
              e.style.cssText = `width:24px;height:24px;border-radius:50%;background:${bg};display:flex;align-items:center;justify-content:center;border:2px solid #fff;box-shadow:0 1px 4px #0004`;
              markers.current.push(new maplibregl.Marker({ element: e }).setLngLat(along(coords, 0.5)).addTo(m));
            }
            return;
          }
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
              ...(walk ? { "line-dasharray": [0.6, 1.6] } : {}),
            },
          });
        });
      });
      // With mode colours the lines no longer say which bot found them, so label each route with its bot(s).
      if (byMode) {
        const groups = new Map<string, MapRoute[]>();
        routes.forEach((r) => groups.set(r.candidate.id, [...(groups.get(r.candidate.id) ?? []), r]));
        groups.forEach((rs) => {
          const legs = rs[0].candidate.legs.filter((l) => l.geometry.length > 1);
          if (!legs.length) return;
          const longest = legs.reduce((a, b) => (b.distance_km > a.distance_km ? b : a));
          // 35% along, so the label does not sit on the leg's mode icon at 50%
          const [lo, la] = along(longest.geometry.map(([a, b]) => [b, a] as [number, number]), 0.35);
          const focused = rs.some((r) => r.emphasized !== false);
          const e = document.createElement("div");
          e.setAttribute("data-testid", `map-label-${rs.map((r) => r.id).join("-")}`);
          e.style.cssText = `display:flex;gap:4px;align-items:center;padding:3px 8px 3px 6px;border-radius:999px;background:#fff;box-shadow:0 2px 8px #0003;font:600 11px/1.2 var(--font-geist-sans),sans-serif;color:#18181b;white-space:nowrap;opacity:${focused ? 1 : 0.7}`;
          rs.forEach((r, k) => {
            const dot = document.createElement("span");
            dot.style.cssText = `width:9px;height:9px;border-radius:50%;background:${r.color};flex:none`;
            e.append(dot, document.createTextNode(r.label ?? r.id), ...(k < rs.length - 1 ? [document.createTextNode(" ")] : []));
          });
          markers.current.push(new maplibregl.Marker({ element: e, anchor: "bottom", offset: [0, -8] }).setLngLat([lo, la]).addTo(m));
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
      // refit only when the set of routes or endpoints changes, not when one is highlighted
      const sig = JSON.stringify([routes.map((r) => r.id), origin, destination]);
      if (!bounds.isEmpty() && sig !== fitted.current) {
        fitted.current = sig;
        m.fitBounds(bounds, { padding: pad.current, duration: 500, maxZoom: 15 });
      }
    };
    if (ready.current) draw();
    else m.once("load", draw);
  }, [routes, origin, destination, colorBy]);

  return <div ref={el} className="h-full w-full" data-testid="map" role="application" aria-label="Route map of Toronto" />;
}

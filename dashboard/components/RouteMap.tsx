"use client";
import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Candidate } from "@/lib/api";

export type MapRoute = { id: string; color: string; candidate: Candidate; emphasized?: boolean };

// Turbopack cannot bundle the ESM worker; serve the copy in /public instead.
maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");

const TORONTO: [number, number] = [-79.3832, 43.6532];

export default function RouteMap({
  routes,
  origin,
  destination,
}: {
  routes: MapRoute[];
  origin?: [number, number] | null; // [lat, lon]
  destination?: [number, number] | null;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const ready = useRef(false);

  useEffect(() => {
    if (!el.current || map.current) return;
    const m = new maplibregl.Map({
      container: el.current,
      center: TORONTO,
      zoom: 11,
      style: {
        version: 8,
        sources: {
          base: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [{ id: "base", type: "raster", source: "base" }],
      },
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.on("load", () => {
      ready.current = true;
      m.fire("redraw" as never);
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
    const draw = () => {
      // clear old layers
      (m.getStyle().layers ?? []).forEach((l: { id: string }) => {
        if (l.id.startsWith("rt-")) m.removeLayer(l.id);
      });
      Object.keys(m.getStyle().sources ?? {}).forEach((s) => {
        if (s.startsWith("rt-")) m.removeSource(s);
      });
      markers.current.forEach((k) => k.remove());
      markers.current = [];
      const bounds = new maplibregl.LngLatBounds();
      routes.forEach((r) => {
        r.candidate.legs.forEach((leg, i) => {
          const coords = leg.geometry.map(([la, lo]) => [lo, la] as [number, number]);
          if (coords.length < 2) return;
          coords.forEach((c) => bounds.extend(c));
          const id = `rt-${r.id}-${i}`;
          m.addSource(id, { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } } });
          const walk = leg.mode === "walk";
          m.addLayer({
            id,
            type: "line",
            source: id,
            layout: { "line-cap": "round", "line-join": "round" },
            paint: {
              "line-color": r.color,
              "line-width": r.emphasized === false ? 3 : 5,
              "line-opacity": r.emphasized === false ? 0.45 : 0.95,
              ...(walk ? { "line-dasharray": [1, 1.6] } : {}),
            },
          });
        });
      });
      const mk = (ll: [number, number] | null | undefined, color: string, label: string) => {
        if (!ll) return;
        const e = document.createElement("div");
        e.setAttribute("aria-label", label);
        e.style.cssText = `width:16px;height:16px;border-radius:50%;background:${color};border:3px solid #fff;box-shadow:0 0 0 1px #0006`;
        markers.current.push(new maplibregl.Marker({ element: e }).setLngLat([ll[1], ll[0]]).addTo(m));
        bounds.extend([ll[1], ll[0]]);
      };
      mk(origin, "#16a34a", "Origin");
      mk(destination, "#dc2626", "Destination");
      if (!bounds.isEmpty()) m.fitBounds(bounds, { padding: 60, duration: 600, maxZoom: 15 });
    };
    if (ready.current) draw();
    else m.once("load", draw);
  }, [routes, origin, destination]);

  return <div ref={el} className="h-full w-full" data-testid="map" role="application" aria-label="Route map of Toronto" />;
}

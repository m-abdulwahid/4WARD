"use client";
import { useEffect, useState } from "react";
import { API } from "@/lib/api";
import type { BikeStation } from "@/components/RouteMap";

type BikeFeed = { provenance: string; fetched_at: number | null; stations: BikeStation[] };
type TtcMeta = { source: string; built_at: string };

const time = (d: Date) => d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

function Toggle({ id, label, on, onChange, children }: { id: string; label: string; on: boolean; onChange: () => void; children?: React.ReactNode }) {
  return (
    <div>
      <button
        type="button"
        role="switch"
        data-testid={id}
        aria-checked={on}
        onClick={onChange}
        className="flex w-full items-center justify-between gap-3 rounded-lg px-1 py-1 text-left text-xs font-medium text-zinc-800 focus-visible:outline-2 focus-visible:outline-zinc-900"
      >
        {label}
        <span aria-hidden className={`relative h-5 w-9 shrink-0 rounded-full transition-colors duration-150 ${on ? "bg-zinc-900" : "bg-zinc-300"}`}>
          <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform duration-150 ${on ? "translate-x-4" : "translate-x-0.5"}`} />
        </span>
      </button>
      {on && children && <div className="px-1 pb-1 text-[10px] leading-snug text-zinc-500">{children}</div>}
    </div>
  );
}

/** Floating card of map overlays. Reports what to draw through onTtc / onBikes. */
export default function LayerToggles({ onTtc, onBikes }: { onTtc: (on: boolean) => void; onBikes: (s: BikeStation[] | null) => void }) {
  const [open, setOpen] = useState(false);
  const [ttc, setTtc] = useState(false);
  const [bikes, setBikes] = useState(false);
  const [feed, setFeed] = useState<BikeFeed | null>(null);
  const [ttcMeta, setTtcMeta] = useState<TtcMeta | null>(null);
  const [bikeErr, setBikeErr] = useState(false);

  useEffect(() => onTtc(ttc), [ttc, onTtc]);
  useEffect(() => onBikes(bikes && feed ? feed.stations : null), [bikes, feed, onBikes]);

  // Fetch lazily, the first time a layer is switched on.
  useEffect(() => {
    if (!ttc || ttcMeta) return;
    fetch("/ttc-lines.geojson")
      .then((r) => r.json())
      .then((d) => setTtcMeta({ source: d.source, built_at: d.built_at }))
      .catch(() => {});
  }, [ttc, ttcMeta]);
  useEffect(() => {
    if (!bikes || feed) return;
    setBikeErr(false);
    fetch(`${API}/bikeshare`)
      .then((r) => r.json())
      .then(setFeed)
      .catch(() => setBikeErr(true));
  }, [bikes, feed]);

  const withEbikes = feed?.stations.filter((s) => s.ebikes > 0).length ?? 0;

  return (
    <section aria-label="Map layers" data-testid="map-layers" className="rounded-2xl bg-white/95 p-2 shadow-lg ring-1 ring-black/5 backdrop-blur md:w-56">
      {/* phones: collapsed behind a button so the map stays visible; wide screens: always open */}
      <button
        type="button"
        data-testid="layers-button"
        aria-expanded={open}
        aria-controls="layer-list"
        onClick={() => setOpen((v) => !v)}
        className="rounded-lg px-2 py-1 text-xs font-semibold text-zinc-800 focus-visible:outline-2 focus-visible:outline-zinc-900 md:hidden"
      >
        Layers{ttc || bikes ? ` (${Number(ttc) + Number(bikes)})` : ""}
      </button>
      <h2 className="hidden px-1 pb-1 text-[11px] font-semibold tracking-wide text-zinc-500 uppercase md:block">Layers</h2>
      <div id="layer-list" className={`${open ? "mt-1 w-56" : "hidden"} md:mt-0 md:block`}>
      <Toggle id="layer-ttc" label="TTC lines" on={ttc} onChange={() => setTtc((v) => !v)}>
        {ttcMeta ? `${ttcMeta.source}, built ${time(new Date(ttcMeta.built_at))}.` : "Loading…"} Stops joined in order, not exact track.
      </Toggle>
      <Toggle id="layer-bikes" label="Bike Share stations" on={bikes} onChange={() => setBikes((v) => !v)}>
        {bikeErr ? (
          <span className="text-red-700">Station feed unavailable (backend offline).</span>
        ) : feed ? (
          <>
            <span className="flex flex-wrap items-center gap-x-2">
              <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-teal-700" />e-bikes ({withEbikes})</span>
              <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-teal-400" />bikes</span>
              <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-zinc-400" />empty</span>
            </span>
            Bike Share Toronto GBFS, {feed.provenance === "live" ? "live" : feed.provenance === "snapshot" ? "saved snapshot" : feed.provenance}
            {feed.fetched_at ? `, ${time(new Date(feed.fetched_at * 1000))}` : ""}. {feed.stations.length} stations.
          </>
        ) : (
          "Loading…"
        )}
      </Toggle>
      </div>
    </section>
  );
}

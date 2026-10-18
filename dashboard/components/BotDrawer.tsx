"use client";
import { useEffect, useRef, useState } from "react";
import { BOTS, BotKey, BotState } from "@/lib/bots";
import { StatusChip } from "@/components/RouteCard";

// The bots' browser viewport (bots/runner.py), so the zoom keeps the screenshot's own shape and size.
const SHOT_W = 1000;
const SHOT_H = 640;

type Zoom = { key: BotKey; pinned: boolean } | null;

/**
 * Enlarged, live view of one bot's screen.
 * Hover mode (mouse): the preview ignores the pointer, so the mouse stays "over" the tile underneath and nothing
 * flickers; leaving the tile closes it. Pinned mode (tap, click or keyboard): a close button, tap outside or Esc.
 */
function ZoomView({ zoom, bots, onClose }: { zoom: NonNullable<Zoom>; bots: Record<BotKey, BotState>; onClose: () => void }) {
  const [shown, setShown] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const bot = BOTS.find((b) => b.key === zoom.key)!;
  const s = bots[zoom.key];
  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(true)); // start the fade/scale-in on the next frame
    return () => cancelAnimationFrame(id);
  }, []);
  useEffect(() => {
    if (zoom.pinned) closeRef.current?.focus();
  }, [zoom.pinned]);

  return (
    <div
      data-testid="bot-zoom"
      role={zoom.pinned ? "dialog" : undefined}
      aria-modal={zoom.pinned ? true : undefined}
      aria-label={`${bot.name} bot's screen, enlarged`}
      onClick={zoom.pinned ? onClose : undefined}
      className={`fixed inset-0 z-50 flex items-center justify-center p-2 transition-opacity duration-150 motion-reduce:transition-none sm:p-6 ${
        zoom.pinned ? "bg-zinc-950/50" : "pointer-events-none bg-zinc-950/20"
      } ${shown ? "opacity-100" : "opacity-0"}`}
    >
      <figure
        onClick={(e) => e.stopPropagation()}
        // phones: nearly full width; larger screens: about 75% of the width, capped near the screenshot's own size
        // and by the screen height so the whole frame stays visible
        className={`flex max-h-full w-[96vw] flex-col overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-black/10 transition-transform duration-150 motion-reduce:transition-none sm:w-[min(76vw,1150px,calc((100dvh-8rem)*1.5625))] ${shown ? "scale-100" : "scale-95"}`}
      >
        <figcaption className="flex items-center justify-between gap-3 border-b border-zinc-100 px-4 py-2.5">
          <span className="flex min-w-0 items-center gap-2 text-sm font-semibold">
            <span aria-hidden className="h-3 w-3 shrink-0 rounded-full" style={{ background: bot.color }} />
            <span className="truncate">{bot.name}</span>
            <StatusChip botKey={zoom.key} state={s} />
          </span>
          {zoom.pinned ? (
            <button
              ref={closeRef}
              type="button"
              data-testid="bot-zoom-close"
              onClick={onClose}
              aria-label="Close the enlarged screen"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-100 text-zinc-700 hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-zinc-900"
            >
              <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round">
                <path d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          ) : (
            <span className="shrink-0 text-[11px] text-zinc-500">Live</span>
          )}
        </figcaption>
        {/* phones: taller than the tile and swipeable sideways, so it is a real zoom */}
        <div data-testid="bot-zoom-screen" className="relative overflow-x-auto bg-zinc-100 max-sm:h-[min(62dvh,560px)] sm:aspect-[1000/640]">
          {s.liveViewUrl ? (
            // Steel's own embeddable live view: a real, interactive cloud browser, not a screenshot -- the
            // viewer can watch it think in real time and even take over, per Steel's intended embed.
            <iframe src={s.liveViewUrl} title={`${bot.name} bot's live browser (Steel)`} className="h-full w-full border-0" />
          ) : s.frame ? (
            // local backend only: no Steel live view, fall back to the bot's own periodic screenshot
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={`data:image/jpeg;base64,${s.frame}`}
              alt={`${bot.name} bot's browser view, enlarged`}
              width={SHOT_W}
              height={SHOT_H}
              className="h-full max-w-none object-contain max-sm:w-auto sm:w-full"
            />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-zinc-500">No screen yet</div>
          )}
        </div>
        <p className="px-4 pt-1.5 text-[11px] text-zinc-500 sm:hidden">Swipe sideways to look around the screen.</p>
        {s.lastReason && (
          <p className="line-clamp-2 px-4 py-2 text-xs text-zinc-600">
            {s.steps ? `Step ${s.steps}: ` : ""}
            {s.lastReason}
          </p>
        )}
      </figure>
    </div>
  );
}

const OUTCOME_CLS = (outcome?: string) =>
  !outcome ? "text-zinc-400" : outcome === "ok" ? "text-emerald-700" : outcome.startsWith("error") ? "text-red-700" : "text-zinc-500";

export default function BotDrawer({ bots, running, onClose }: { bots: Record<BotKey, BotState>; running: boolean; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  // one bot's full step log (from master), and the enlarged live screen (hover or tap)
  const [expanded, setExpanded] = useState<BotKey | null>(null);
  const expandedRef = useRef<BotKey | null>(null);
  expandedRef.current = expanded;
  const logEndRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState<Zoom>(null);
  const zoomRef = useRef<Zoom>(null);
  zoomRef.current = zoom;
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tileRefs = useRef<Partial<Record<BotKey, HTMLButtonElement | null>>>({});

  useEffect(() => {
    closeRef.current?.focus();
    // Esc closes the enlarged screen first, then the step log, then the drawer
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (zoomRef.current) {
        const k = zoomRef.current.key;
        setZoom(null);
        tileRefs.current[k]?.focus();
      } else if (expandedRef.current) setExpanded(null);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  useEffect(
    () => () => {
      if (hoverTimer.current) clearTimeout(hoverTimer.current);
    },
    [],
  );
  useEffect(() => {
    if (expanded) logEndRef.current?.scrollIntoView({ block: "end" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, bots[expanded ?? "cheap"]?.log.length]);

  const clearTimer = () => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
  };

  /**
   * A bot's live screen: hover to preview it enlarged (grid only), tap/click/Enter to pin the enlarged view.
   * The step-log view shows the screen large already and right where the pointer lands, so there it opens on click only.
   */
  const screen = (k: BotKey, name: string, s: BotState, hover = true) => (
    <button
      ref={(el) => {
        tileRefs.current[k] = el;
      }}
      type="button"
      data-testid={`bot-${k}-screen`}
      disabled={!s.frame && !s.liveViewUrl}
      aria-label={`Enlarge the ${name} bot's screen`}
      aria-haspopup="dialog"
      onPointerEnter={(e) => {
        if (!hover || e.pointerType !== "mouse" || (!s.frame && !s.liveViewUrl) || zoomRef.current?.pinned) return;
        clearTimer();
        // short delay: passing over a screen on the way somewhere else does not open it
        hoverTimer.current = setTimeout(() => setZoom({ key: k, pinned: false }), zoomRef.current ? 0 : 140);
      }}
      onPointerLeave={(e) => {
        if (e.pointerType !== "mouse") return;
        clearTimer();
        setZoom((z) => (z && !z.pinned && z.key === k ? null : z));
      }}
      onClick={() => {
        clearTimer();
        setZoom({ key: k, pinned: true }); // tap, click or keyboard: stays open until X, tap outside or Esc
      }}
      className="relative block aspect-[1000/640] w-full shrink-0 cursor-zoom-in bg-zinc-100 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-zinc-900 disabled:cursor-default"
    >
      {s.liveViewUrl ? (
        // visual only here (pointer-events disabled) so hover-to-zoom still works; the enlarged view is interactive
        <iframe src={s.liveViewUrl} title={`${name} bot's live browser (Steel)`} className="pointer-events-none h-full w-full border-0" tabIndex={-1} />
      ) : s.frame ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={`data:image/jpeg;base64,${s.frame}`} alt={`${name} bot's browser view`} className="h-full w-full object-cover" />
      ) : (
        <span className="flex h-full items-center justify-center text-xs text-zinc-500">{running ? "Starting browser…" : "Waiting for a trip"}</span>
      )}
    </button>
  );

  const zoomView = zoom && (
    <ZoomView
      key={`${zoom.key}-${zoom.pinned}`}
      zoom={zoom}
      bots={bots}
      onClose={() => {
        const k = zoom.key;
        setZoom(null);
        tileRefs.current[k]?.focus();
      }}
    />
  );

  if (expanded) {
    const b = BOTS.find((x) => x.key === expanded)!;
    const s = bots[expanded];
    return (
      <div
        role="dialog"
        aria-modal="false"
        aria-label={`${b.name} bot, full step log`}
        data-testid="bots-drawer"
        className="absolute inset-x-0 top-12 bottom-0 z-30 flex flex-col rounded-t-3xl bg-white shadow-2xl ring-1 ring-black/5 md:inset-x-auto md:top-4 md:right-4 md:bottom-4 md:w-[min(640px,calc(100vw-560px))] md:min-w-[420px] md:rounded-2xl"
      >
        <div className="flex items-center justify-between gap-2 border-b border-zinc-100 px-4 py-3">
          <button
            type="button"
            data-testid="unfocus-bot"
            onClick={() => setExpanded(null)}
            className="flex items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium text-zinc-600 hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-zinc-900"
          >
            <span aria-hidden>←</span> All bots
          </button>
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <span aria-hidden className="h-2.5 w-2.5 rounded-full" style={{ background: b.color }} />
            {b.name}
          </h2>
          <StatusChip botKey={b.key} state={s} />
        </div>
        {screen(b.key, b.name, s, false)}
        <ol data-testid="bot-step-log" className="flex-1 overflow-y-auto px-4 py-3 text-xs">
          {s.log.length === 0 && <li className="text-zinc-400">No steps yet.</li>}
          {s.log.map((e) => (
            <li key={e.n} className="border-b border-zinc-100 py-2 last:border-0">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-semibold text-zinc-800">
                  Step {e.n}: {e.reason || e.action}
                </span>
                <span className={`shrink-0 text-[10px] ${OUTCOME_CLS(e.outcome)}`}>{e.outcome}</span>
              </div>
              {(e.action || e.testid || e.text) && (
                <p className="mt-0.5 font-mono text-[10px] text-zinc-500">
                  {e.action}
                  {e.testid ? ` → ${e.testid}` : ""}
                  {e.text ? ` "${e.text}"` : ""}
                </p>
              )}
            </li>
          ))}
          <div ref={logEndRef} />
        </ol>
        {zoomView}
      </div>
    );
  }

  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-label="Live view of the four bots"
      data-testid="bots-drawer"
      className="absolute inset-x-0 top-12 bottom-0 z-30 flex flex-col rounded-t-3xl bg-white shadow-2xl ring-1 ring-black/5 md:inset-x-auto md:top-4 md:right-4 md:bottom-4 md:w-[min(640px,calc(100vw-560px))] md:min-w-[420px] md:rounded-2xl"
    >
      <div className="flex items-center justify-between border-b border-zinc-100 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold">Watch the bots</h2>
          <p className="text-xs text-zinc-500">Hover or tap a screen to enlarge it. Open a bot&rsquo;s steps to follow them one by one.</p>
        </div>
        <button
          ref={closeRef}
          type="button"
          data-testid="close-bots"
          onClick={onClose}
          className="rounded-full bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-800 hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-zinc-900"
        >
          Close
        </button>
      </div>
      <div className="grid flex-1 auto-rows-max grid-cols-1 content-start gap-3 overflow-y-auto p-3 sm:grid-cols-2">
        {BOTS.map((b) => {
          const s = bots[b.key];
          return (
            <section key={b.key} data-testid={`bot-${b.key}-live`} aria-label={`${b.name} bot`} className="overflow-hidden rounded-xl ring-1 ring-zinc-200">
              <div className="flex items-center justify-between gap-2 px-3 py-2">
                <h3 className="flex items-center gap-2 text-xs font-semibold">
                  <span aria-hidden className="h-2.5 w-2.5 rounded-full" style={{ background: b.color }} />
                  {b.name}
                </h3>
                <StatusChip botKey={b.key} state={s} />
              </div>
              {screen(b.key, b.name, s)}
              <button
                type="button"
                data-testid={`bot-${b.key}-steps`}
                onClick={() => setExpanded(b.key)}
                aria-label={`${b.name} bot: view the full step log`}
                className="block w-full px-3 py-2 text-left hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-zinc-900"
              >
                <span className="line-clamp-2 text-[11px] text-zinc-600">
                  {s.steps ? `Step ${s.steps}: ` : ""}
                  {s.lastReason ?? (s.status === "DONE" ? "Finished" : "Idle")}
                </span>
                <span className="mt-0.5 block text-[11px] font-semibold text-zinc-800">{s.log.length ? `View all ${s.log.length} steps →` : "View steps →"}</span>
              </button>
            </section>
          );
        })}
      </div>
      {zoomView}
    </div>
  );
}

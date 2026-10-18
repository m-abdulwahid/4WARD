"use client";
import { useEffect, useRef, useState } from "react";
import { BOTS, BotKey, BotState } from "@/lib/bots";
import { StatusChip } from "@/components/RouteCard";

// The bots' browser viewport (bots/runner.py), so the zoom keeps the screenshot's own shape and size.
const SHOT_W = 1000;
const SHOT_H = 640;

const KEYS = BOTS.map((b) => b.key);

function ExpandIcon({ className }: { className?: string }) {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3" />
    </svg>
  );
}

function isLive(s: BotState): boolean {
  return s.status === "SCANNING" || s.status === "ROUTING";
}

function hasScreen(s: BotState): boolean {
  return Boolean((s.liveViewUrl && isLive(s)) || s.frame);
}

/** How often a still-connecting Steel iframe is force-remounted. Steel's own debug player sometimes never
 * finishes its first WebRTC handshake; cross-origin content gives us no reliable "it's stuck" signal, so
 * instead of leaving it dead we just retry on a timer. Cheap and self-healing, no user action needed. */
const LIVE_RETRY_MS = 12_000;

/** Once a bot is no longer live (done or failed), Steel's own debug player shows its own "Browser
 * Disconnected" / "Session ended" chrome -- technically accurate (we release the session the moment the
 * bot finishes, since Steel bills per minute) but it reads as broken. Show a clean summary instead. */
function FinishedScreen({ s }: { s: BotState }) {
  if (s.status === "FAILED") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1 px-4 text-center text-zinc-500">
        <span className="text-xs font-semibold text-red-700">Bot stopped</span>
        {s.error && <span className="line-clamp-2 text-[11px]">{s.error}</span>}
      </div>
    );
  }
  if (s.route) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1 px-4 text-center">
        <span aria-hidden className="text-lg">✓</span>
        <span className="text-xs font-semibold text-zinc-800">{s.route.title}</span>
        <span className="text-[11px] text-zinc-500">
          {s.route.duration_min != null ? `${s.route.duration_min} min` : ""}
          {s.route.duration_min != null && s.route.cost != null ? " · " : ""}
          {s.route.cost != null ? `$${s.route.cost.toFixed(2)}` : ""}
        </span>
        <span className="text-[10px] text-zinc-400">Live view ended (browser session released)</span>
      </div>
    );
  }
  return <div className="flex h-full items-center justify-center text-xs text-zinc-500">Live view ended</div>;
}

/**
 * Enlarged, live view of one bot's screen. Click-only: stays open until outside click, X, or Escape.
 * Arrow keys / on-screen arrows switch bots without closing. Image (or Steel embed) updates as new frames arrive.
 */
function ZoomView({
  zoomKey,
  bots,
  retryEpoch,
  onClose,
  onSelect,
}: {
  zoomKey: BotKey;
  bots: Record<BotKey, BotState>;
  retryEpoch: number;
  onClose: () => void;
  onSelect: (k: BotKey) => void;
}) {
  const [shown, setShown] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const bot = BOTS.find((b) => b.key === zoomKey)!;
  const s = bots[zoomKey];
  const idx = KEYS.indexOf(zoomKey);
  const prevKey = KEYS[(idx + KEYS.length - 1) % KEYS.length];
  const nextKey = KEYS[(idx + 1) % KEYS.length];

  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, []);

  useEffect(() => {
    closeRef.current?.focus();
  }, [zoomKey]);

  // Prevent scrollbar jump / layout shift under the overlay.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        onSelect(prevKey);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        onSelect(nextKey);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [prevKey, nextKey, onSelect]);

  return (
    <div
      data-testid="bot-zoom"
      role="dialog"
      aria-modal="true"
      aria-label={`${bot.name} bot's screen, enlarged`}
      onClick={onClose}
      className={`fixed inset-0 z-50 flex items-center justify-center p-2 transition-opacity duration-200 motion-reduce:transition-none sm:p-6 ${
        shown ? "bg-zinc-950/50 opacity-100" : "bg-zinc-950/50 opacity-0"
      }`}
    >
      <figure
        onClick={(e) => e.stopPropagation()}
        className={`relative flex max-h-full w-[96vw] flex-col overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-black/10 transition-transform duration-200 motion-reduce:transition-none sm:w-[min(76vw,1150px,calc((100dvh-8rem)*1.5625))] ${
          shown ? "scale-100" : "scale-95"
        }`}
      >
        <figcaption className="flex items-center justify-between gap-3 border-b border-zinc-100 px-4 py-2.5">
          <span className="flex min-w-0 items-center gap-2 text-sm font-semibold">
            <span aria-hidden className="h-3 w-3 shrink-0 rounded-full" style={{ background: bot.color }} />
            <span className="truncate">{bot.name}</span>
            <StatusChip botKey={zoomKey} state={s} />
            <span className="hidden text-[11px] font-normal text-zinc-500 sm:inline">Live</span>
          </span>
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
        </figcaption>

        <div className="relative">
          <button
            type="button"
            aria-label={`Show ${BOTS.find((b) => b.key === prevKey)!.name} bot`}
            onClick={() => onSelect(prevKey)}
            className="absolute top-1/2 left-1 z-10 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-white/95 text-zinc-800 shadow ring-1 ring-black/10 hover:bg-white focus-visible:outline-2 focus-visible:outline-zinc-900 sm:left-2 sm:h-10 sm:w-10"
          >
            <svg aria-hidden viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 6 9 12l6 6" />
            </svg>
          </button>
          <button
            type="button"
            aria-label={`Show ${BOTS.find((b) => b.key === nextKey)!.name} bot`}
            onClick={() => onSelect(nextKey)}
            className="absolute top-1/2 right-1 z-10 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-white/95 text-zinc-800 shadow ring-1 ring-black/10 hover:bg-white focus-visible:outline-2 focus-visible:outline-zinc-900 sm:right-2 sm:h-10 sm:w-10"
          >
            <svg aria-hidden viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
              <path d="m9 6 6 6-6 6" />
            </svg>
          </button>

          {/* phones: taller than the tile and swipeable sideways, so it is a real zoom */}
          <div data-testid="bot-zoom-screen" className="relative overflow-x-auto bg-zinc-100 max-sm:h-[min(62dvh,560px)] sm:aspect-[1000/640]">
            {s.liveViewUrl && isLive(s) ? (
              // Steel's own embeddable live view: a real cloud browser, not a screenshot. Remounted on a
              // timer (key changes) so a session stuck mid-handshake self-heals instead of staying dead.
              <iframe
                key={`${zoomKey}-${retryEpoch}`}
                src={s.liveViewUrl}
                title={`${bot.name} bot's live browser (Steel)`}
                className="h-full w-full border-0"
              />
            ) : s.liveViewUrl ? (
              <FinishedScreen s={s} />
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
        </div>

        <p className="px-4 pt-1.5 text-[11px] text-zinc-500 sm:hidden">Swipe sideways to look around the screen. Use the arrows to switch bots.</p>
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
  const [expanded, setExpanded] = useState<BotKey | null>(null);
  const expandedRef = useRef<BotKey | null>(null);
  expandedRef.current = expanded;
  const logEndRef = useRef<HTMLDivElement>(null);
  const [zoomKey, setZoomKey] = useState<BotKey | null>(null);
  const zoomRef = useRef<BotKey | null>(null);
  zoomRef.current = zoomKey;
  const tileRefs = useRef<Partial<Record<BotKey, HTMLButtonElement | null>>>({});
  const [retryEpoch, setRetryEpoch] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setRetryEpoch((e) => e + 1), LIVE_RETRY_MS);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    closeRef.current?.focus();
    // Esc closes the enlarged screen first, then the step log, then the drawer
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (zoomRef.current) {
        const k = zoomRef.current;
        setZoomKey(null);
        tileRefs.current[k]?.focus();
      } else if (expandedRef.current) setExpanded(null);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (expanded) logEndRef.current?.scrollIntoView({ block: "end" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, bots[expanded ?? "cheap"]?.log.length]);

  /** Click/tap a bot screen to open the stable enlarged view (no hover). */
  const screen = (k: BotKey, name: string, s: BotState) => (
    <button
      ref={(el) => {
        tileRefs.current[k] = el;
      }}
      type="button"
      data-testid={`bot-${k}-screen`}
      disabled={!hasScreen(s)}
      aria-label={`Enlarge the ${name} bot's screen`}
      aria-haspopup="dialog"
      onClick={() => {
        if (hasScreen(s)) setZoomKey(k);
      }}
      className="group relative block aspect-[1000/640] w-full shrink-0 cursor-pointer bg-zinc-100 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-zinc-900 disabled:cursor-default disabled:opacity-80"
    >
      {s.liveViewUrl && isLive(s) ? (
        // visual only in the tile (pointer-events disabled); the enlarged view is interactive
        <iframe
          key={`${k}-${retryEpoch}`}
          src={s.liveViewUrl}
          title={`${name} bot's live browser (Steel)`}
          className="pointer-events-none h-full w-full border-0"
          tabIndex={-1}
        />
      ) : s.liveViewUrl ? (
        <FinishedScreen s={s} />
      ) : s.frame ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={`data:image/jpeg;base64,${s.frame}`} alt={`${name} bot's browser view`} className="h-full w-full object-cover" />
      ) : (
        <span className="flex h-full items-center justify-center text-xs text-zinc-500">{running ? "Starting browser…" : "Waiting for a trip"}</span>
      )}
      {hasScreen(s) && (
        <span
          aria-hidden
          className="pointer-events-none absolute right-1.5 bottom-1.5 flex h-7 w-7 items-center justify-center rounded-md bg-zinc-900/75 text-white shadow-sm ring-1 ring-white/20 transition-opacity group-hover:bg-zinc-900/90 group-focus-visible:bg-zinc-900/90"
        >
          <ExpandIcon className="h-3.5 w-3.5" />
        </span>
      )}
    </button>
  );

  const zoomView = zoomKey && (
    <ZoomView
      zoomKey={zoomKey}
      bots={bots}
      retryEpoch={retryEpoch}
      onSelect={setZoomKey}
      onClose={() => {
        const k = zoomKey;
        setZoomKey(null);
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
        {screen(b.key, b.name, s)}
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
          <p className="text-xs text-zinc-500">Click or tap a screen to enlarge it. Open a bot&rsquo;s steps to follow them one by one.</p>
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

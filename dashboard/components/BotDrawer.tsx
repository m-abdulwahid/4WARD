"use client";
import { useEffect, useRef, useState } from "react";
import { BOTS, BotKey, BotState } from "@/lib/bots";
import { StatusChip } from "@/components/RouteCard";

const OUTCOME_CLS = (outcome?: string) =>
  !outcome ? "text-zinc-400" : outcome === "ok" ? "text-emerald-700" : outcome.startsWith("error") ? "text-red-700" : "text-zinc-500";

export default function BotDrawer({ bots, running, onClose }: { bots: Record<BotKey, BotState>; running: boolean; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState<BotKey | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && (expanded ? setExpanded(null) : onClose());
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, expanded]);

  useEffect(() => {
    if (expanded) logEndRef.current?.scrollIntoView({ block: "end" });
  }, [expanded, bots[expanded ?? "cheap"]?.log.length]);

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
        <div className="relative aspect-[1000/640] shrink-0 bg-zinc-100">
          {s.frame ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={`data:image/jpeg;base64,${s.frame}`} alt={`${b.name} bot's browser view`} className="h-full w-full object-cover" />
          ) : (
            <div className="flex h-full items-center justify-center text-xs text-zinc-500">{running ? "Starting browser…" : "Waiting for a trip"}</div>
          )}
        </div>
        <ol data-testid="bot-step-log" className="flex-1 overflow-y-auto px-4 py-3 text-xs">
          {s.log.length === 0 && <li className="text-zinc-400">No steps yet.</li>}
          {s.log.map((e) => (
            <li key={e.n} className="border-b border-zinc-100 py-2 last:border-0">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-semibold text-zinc-800">Step {e.n}: {e.reason || e.action}</span>
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
          <p className="text-xs text-zinc-500">Click a bot to follow its steps one by one.</p>
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
            <button
              key={b.key}
              type="button"
              data-testid={`bot-${b.key}-live`}
              aria-label={`${b.name} bot — view full step log`}
              onClick={() => setExpanded(b.key)}
              className="overflow-hidden rounded-xl text-left ring-1 ring-zinc-200 transition-shadow duration-150 hover:shadow-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900"
            >
              <div className="flex items-center justify-between gap-2 px-3 py-2">
                <h3 className="flex items-center gap-2 text-xs font-semibold">
                  <span aria-hidden className="h-2.5 w-2.5 rounded-full" style={{ background: b.color }} />
                  {b.name}
                </h3>
                <StatusChip botKey={b.key} state={s} />
              </div>
              <div className="relative aspect-[1000/640] bg-zinc-100">
                {s.frame ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={`data:image/jpeg;base64,${s.frame}`} alt={`${b.name} bot's browser view`} className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full items-center justify-center text-xs text-zinc-500">{running ? "Starting browser…" : "Waiting for a trip"}</div>
                )}
              </div>
              <p className="line-clamp-2 px-3 py-2 text-[11px] text-zinc-600">
                {s.steps ? `Step ${s.steps}: ` : ""}
                {s.lastReason ?? (s.status === "DONE" ? "Finished" : "Idle")}
              </p>
            </button>
          );
        })}
      </div>
    </div>
  );
}

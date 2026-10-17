"use client";
import { useEffect, useRef } from "react";
import { BOTS, BotKey, BotState } from "@/lib/bots";
import { StatusChip } from "@/components/RouteCard";

export default function BotDrawer({ bots, running, onClose }: { bots: Record<BotKey, BotState>; running: boolean; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

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
          <p className="text-xs text-zinc-500">Each bot drives its own browser through the planner.</p>
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
            </section>
          );
        })}
      </div>
    </div>
  );
}

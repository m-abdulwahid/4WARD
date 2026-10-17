"use client";
import { useState } from "react";
import { ParsedTime, parseTime } from "@/lib/timeParse";

/**
 * A time you can type ("5pm", "17:30", "noon", "8am tomorrow", "in 2 hours"), with a clock button that opens a
 * picker for people who prefer clicking. Typing never needs the picker.
 */
export default function TimeField({
  id,
  label,
  text,
  setText,
  testid,
  pickerTestid,
  clearTestid,
  extraNote,
}: {
  id: string;
  label: string;
  text: string;
  setText: (t: string) => void;
  testid: string;
  pickerTestid: string;
  clearTestid: string;
  extraNote?: React.ReactNode; // shown under the summary (e.g. what the trip text says)
}) {
  const [touched, setTouched] = useState(false);
  const [open, setOpen] = useState(false);
  const [pickTime, setPickTime] = useState("");
  const [pickDay, setPickDay] = useState<"today" | "tomorrow">("today");
  const parsed: ParsedTime | null = text.trim() ? parseTime(text) : null;
  const bad = !!text.trim() && !parsed;
  const showBad = bad && touched;

  const applyPick = (time: string, day: "today" | "tomorrow") => {
    if (!time) return;
    const [h, m] = time.split(":").map(Number);
    const h12 = h % 12 || 12;
    setText(`${h12}${m ? `:${String(m).padStart(2, "0")}` : ""}${h < 12 ? "am" : "pm"}${day === "tomorrow" ? " tomorrow" : ""}`);
    setTouched(true);
  };

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-zinc-700">
        {label}
      </label>
      <div className="flex items-stretch gap-1.5">
        <input
          id={id}
          type="text"
          inputMode="text"
          autoComplete="off"
          spellCheck={false}
          data-testid={testid}
          value={text}
          placeholder="e.g. 5pm, 17:30, noon, in 2 hours"
          aria-invalid={showBad || undefined}
          aria-describedby={`${id}-status`}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => setTouched(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault(); // Enter confirms the time; it should not submit the whole trip
              setTouched(true);
            }
          }}
          className={`min-w-0 flex-1 rounded-lg border bg-zinc-50 px-2.5 py-1.5 text-sm text-zinc-900 focus:bg-white focus:outline-none ${showBad ? "border-red-400 focus:border-red-500" : "border-zinc-200 focus:border-zinc-400"}`}
        />
        <button
          type="button"
          data-testid={`${testid}-pick`}
          aria-label={`Pick ${label.toLowerCase()} from a clock`}
          aria-expanded={open}
          aria-controls={`${id}-picker`}
          onClick={() => {
            setPickTime(parsed?.hhmm ?? "");
            setPickDay(parsed?.tomorrow ? "tomorrow" : "today");
            setOpen((v) => !v);
          }}
          className={`flex w-9 shrink-0 items-center justify-center rounded-lg ring-1 focus-visible:outline-2 focus-visible:outline-zinc-900 ${open ? "bg-zinc-900 text-white ring-zinc-900" : "bg-white text-zinc-700 ring-zinc-200 hover:bg-zinc-50"}`}
        >
          <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7v5l3 2" />
          </svg>
        </button>
        {text && (
          <button
            type="button"
            data-testid={clearTestid}
            onClick={() => {
              setText("");
              setTouched(false);
              setOpen(false);
            }}
            className="rounded-lg px-2.5 text-xs font-medium text-zinc-700 ring-1 ring-zinc-200 hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-zinc-900"
          >
            Clear
          </button>
        )}
      </div>

      {open && (
        <div id={`${id}-picker`} role="group" aria-label={`Pick ${label.toLowerCase()}`} className="flex flex-wrap items-end gap-2 rounded-lg bg-zinc-50 p-2 ring-1 ring-zinc-200">
          <label className="flex flex-col gap-0.5 text-[11px] font-medium text-zinc-600">
            Time
            <input
              type="time"
              data-testid={pickerTestid}
              value={pickTime}
              onChange={(e) => {
                setPickTime(e.target.value);
                applyPick(e.target.value, pickDay);
              }}
              className="rounded-md border border-zinc-200 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-400 focus:outline-none"
            />
          </label>
          <div role="radiogroup" aria-label="Day" className="flex overflow-hidden rounded-md ring-1 ring-zinc-200">
            {(["today", "tomorrow"] as const).map((d) => (
              <button
                key={d}
                type="button"
                role="radio"
                aria-checked={pickDay === d}
                data-testid={`${testid}-pick-${d}`}
                onClick={() => {
                  setPickDay(d);
                  applyPick(pickTime, d);
                }}
                className={`px-2.5 py-1 text-xs font-medium capitalize focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-zinc-900 ${pickDay === d ? "bg-zinc-900 text-white" : "bg-white text-zinc-700"}`}
              >
                {d}
              </button>
            ))}
          </div>
          <button type="button" onClick={() => setOpen(false)} className="ml-auto rounded-md px-2 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-zinc-900">
            Done
          </button>
        </div>
      )}

      <div id={`${id}-status`} data-testid={`${testid}-status`} aria-live="polite" className="text-[11px] leading-snug">
        {showBad ? (
          <p className="text-red-700">Couldn&rsquo;t read that time, try 5pm or 17:30.</p>
        ) : parsed ? (
          <p className="text-zinc-700">
            {label} <b className="font-semibold">{parsed.label}</b> (Toronto time)
            {parsed.rolled && <span className="text-amber-900">. That time has already passed today, so we assumed tomorrow.</span>}
          </p>
        ) : null}
        {extraNote}
      </div>
    </div>
  );
}

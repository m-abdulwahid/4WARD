"use client";
import { useState } from "react";

// Option cards under the trip box. Every card writes a phrase the prompt parser (bots/nlp.py) already understands,
// so the prompt text stays the single source of truth. Options with no backend support are shown as such.

export type Priority = "cheapest" | "fastest" | "greenest" | "fewest_transfers" | "least_walking" | null;

const BAGS = /\b(\d+|an?|one|two|three|four)\s+(?:big |large |heavy |carry-on )?(?:bags?|suitcases?|luggage)\b/i;
const DEADLINE = /\b(by|before|arrive(?: at| by)?)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b/i;
const WORDS: Record<string, number> = { a: 1, an: 1, one: 1, two: 2, three: 3, four: 4 };

type Toggle = { key: string; label: string; phrase: string; test: RegExp; note?: string };

const TRAVEL: Toggle[] = [
  { key: "bike", label: "My bike", phrase: "I have my bike.", test: /\b(my bike|have a bike|my bicycle)\b/i },
  { key: "ebike", label: "My e-bike", phrase: "I have an e-bike.", test: /e-?bike/i },
  { key: "car", label: "My car", phrase: "I have my car.", test: /\b(my car|have a car|i drive|i have a vehicle|driving)\b/i },
  { key: "ev", label: "Electric car", phrase: "My car is electric.", test: /\b(electric|tesla|ev)\b/i },
];
const ALWAYS = ["Walk", "TTC transit", "Bike Share"];

const SITUATION: Toggle[] = [
  { key: "presto", label: "No Presto card", phrase: "No Presto card.", test: /no presto|without (?:a )?presto|(?:don'?t|do not) have (?:a )?presto|no (?:transit|bus) pass/i, note: "Cash fares" },
  {
    key: "stairs",
    label: "Stairs are hard",
    phrase: "Please avoid stairs.",
    test: /\bstairs?\b/i,
    note: "Counts against subway routes in the bag score (estimate). Elevators are not checked yet.",
  },
  {
    key: "stroller",
    label: "Stroller or wheelchair",
    phrase: "I have a stroller or wheelchair.",
    test: /\b(stroller|wheelchair)\b/i,
    note: "Same as stairs for now. Step-free access is not checked, so no route is marked accessible.",
  },
];

const PRIORITIES: { key: Exclude<Priority, null>; label: string }[] = [
  { key: "cheapest", label: "Cheapest" },
  { key: "fastest", label: "Fastest" },
  { key: "greenest", label: "Greenest" },
  { key: "fewest_transfers", label: "Fewest transfers" },
  { key: "least_walking", label: "Least walking" },
];

const tidy = (s: string) => s.replace(/\s+([.,])/g, "$1").replace(/\s{2,}/g, " ").trim();

function to12h(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h >= 12 ? "pm" : "am";
  const h12 = h % 12 || 12;
  return m ? `${h12}:${String(m).padStart(2, "0")}${suffix}` : `${h12}${suffix}`;
}

/** The deadline already in the prompt as HH:MM (24 h), read the same way as bots/nlp.py _clock (no am/pm = 24 h). */
function readDeadline(prompt: string): string {
  const m = prompt.match(DEADLINE);
  if (!m) return "";
  let h = Number(m[2]);
  const min = Number(m[3] ?? 0);
  const ap = m[4]?.toLowerCase();
  if (ap === "pm" && h < 12) h += 12;
  if (ap === "am" && h === 12) h = 0;
  return h < 24 ? `${String(h).padStart(2, "0")}:${String(min).padStart(2, "0")}` : "";
}

function Group({ id, title, summary, open, onToggle, children }: { id: string; title: string; summary: string; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  return (
    <div className="rounded-xl ring-1 ring-zinc-200">
      <button
        type="button"
        data-testid={`options-${id}`}
        aria-expanded={open}
        aria-controls={`options-${id}-body`}
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2 text-left focus-visible:outline-2 focus-visible:outline-zinc-900"
      >
        <span className="text-sm font-medium text-zinc-800">{title}</span>
        <span className="flex items-center gap-2 text-xs text-zinc-500">
          {summary}
          <span aria-hidden className={`transition-transform duration-150 ${open ? "rotate-180" : ""}`}>
            ▾
          </span>
        </span>
      </button>
      <div id={`options-${id}-body`} hidden={!open} className="px-3 pb-3">
        {children}
      </div>
    </div>
  );
}

function Card({ id, label, on, disabled, note, onClick, children }: { id: string; label: string; on: boolean; disabled?: boolean; note?: string; onClick?: () => void; children?: React.ReactNode }) {
  return (
    <div className={`rounded-lg ring-1 ${on ? "bg-zinc-900 text-white ring-zinc-900" : disabled ? "bg-zinc-50 text-zinc-500 ring-zinc-200" : "bg-white text-zinc-800 ring-zinc-200"}`}>
      <button
        type="button"
        data-testid={`option-${id}`}
        aria-pressed={disabled ? undefined : on}
        aria-disabled={disabled || undefined}
        disabled={disabled}
        onClick={onClick}
        className="flex w-full flex-col items-start gap-0.5 rounded-lg px-2.5 py-2 text-left text-xs font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900 disabled:cursor-default"
      >
        <span>
          {on ? "✓ " : ""}
          {label}
        </span>
        {note && <span className={`text-[10px] font-normal leading-snug ${on ? "text-zinc-300" : "text-zinc-500"}`}>{note}</span>}
      </button>
      {children}
    </div>
  );
}

export default function TripOptions({
  prompt,
  setPrompt,
  focusPrompt,
  priority,
  setPriority,
}: {
  prompt: string;
  setPrompt: (f: (p: string) => string) => void;
  focusPrompt: () => void;
  priority: Priority;
  setPriority: (p: Priority) => void;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const flip = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }));

  const toggle = (t: Toggle) => {
    if (!t.test.test(prompt)) setPrompt((p) => tidy(`${p} ${t.phrase}`));
    else if (prompt.includes(t.phrase)) setPrompt((p) => tidy(p.replace(t.phrase, "")));
    else focusPrompt(); // said in the user's own words: edit the text to change it
  };

  const bagMatch = prompt.match(BAGS);
  const bags = bagMatch ? (/^\d+$/.test(bagMatch[1]) ? Number(bagMatch[1]) : WORDS[bagMatch[1].toLowerCase()] ?? 1) : 0;
  const setBags = (n: number) => {
    if (n === 0) {
      if (bagMatch && prompt.includes(`I have ${bags} bag${bags === 1 ? "" : "s"}.`)) setPrompt((p) => tidy(p.replace(`I have ${bags} bag${bags === 1 ? "" : "s"}.`, "")));
      else if (bagMatch) focusPrompt();
      return;
    }
    const words = `${n} bag${n === 1 ? "" : "s"}`;
    setPrompt((p) => (BAGS.test(p) ? p.replace(BAGS, words) : tidy(`${p} I have ${words}.`)));
  };

  const deadline = readDeadline(prompt);
  const setDeadline = (hhmm: string) => {
    if (!hhmm) {
      setPrompt((p) => tidy(p.replace(/I need to arrive by [^.]*\./, "").replace(DEADLINE, "")));
      return;
    }
    const t = to12h(hhmm);
    setPrompt((p) => (DEADLINE.test(p) ? p.replace(DEADLINE, (_m, w: string) => `${w} ${t}`) : tidy(`${p} I need to arrive by ${t}.`)));
  };

  const travelOn = TRAVEL.filter((t) => t.test.test(prompt)).length;
  const situationOn = SITUATION.filter((t) => t.test.test(prompt)).length + (bags ? 1 : 0);

  return (
    <div className="flex flex-col gap-2" data-testid="trip-options">
      <Group id="travel" title="How are you travelling" summary={travelOn ? `${travelOn} selected` : "Optional"} open={!!open.travel} onToggle={() => flip("travel")}>
        <div className="grid grid-cols-2 gap-1.5">
          {TRAVEL.map((t) => (
            <Card key={t.key} id={t.key} label={t.label} on={t.test.test(prompt)} onClick={() => toggle(t)} />
          ))}
          <Card id="rideshare" label="Rideshare" on={false} disabled note="Included whenever you have no car" />
          {ALWAYS.map((a) => (
            <Card key={a} id={a.toLowerCase().replace(/\s+/g, "-")} label={a} on={false} disabled note="Always included" />
          ))}
        </div>
      </Group>

      <Group id="situation" title="Your situation" summary={situationOn ? `${situationOn} selected` : "Optional"} open={!!open.situation} onToggle={() => flip("situation")}>
        <div className="grid grid-cols-2 gap-1.5">
          <Card id="bags" label={bags ? `${bags} bag${bags === 1 ? "" : "s"}` : "Bags"} on={bags > 0} onClick={() => setBags(bags ? 0 : 1)}>
            {bags > 0 && (
              <div className="flex items-center justify-between gap-1 px-2.5 pb-2" role="group" aria-label="Number of bags">
                <button type="button" data-testid="option-bags-minus" aria-label="One bag fewer" onClick={() => setBags(bags - 1)} className="h-6 w-6 rounded-full bg-white/15 text-sm leading-none focus-visible:outline-2 focus-visible:outline-white">
                  −
                </button>
                <span className="text-xs tabular-nums" aria-live="polite">
                  {bags}
                </span>
                <button type="button" data-testid="option-bags-plus" aria-label="One more bag" disabled={bags >= 4} onClick={() => setBags(bags + 1)} className="h-6 w-6 rounded-full bg-white/15 text-sm leading-none focus-visible:outline-2 focus-visible:outline-white disabled:opacity-40">
                  +
                </button>
              </div>
            )}
          </Card>
          {SITUATION.map((t) => (
            <Card key={t.key} id={t.key} label={t.label} note={t.note} on={t.test.test(prompt)} onClick={() => toggle(t)} />
          ))}
          <Card id="kids" label="Travelling with kids" on={false} disabled note="Coming soon" />
        </div>
      </Group>

      <Group id="priorities" title="Priorities" summary={priority ? PRIORITIES.find((p) => p.key === priority)!.label : "Optional"} open={!!open.priorities} onToggle={() => flip("priorities")}>
        <p className="mb-1.5 text-[11px] leading-snug text-zinc-500">All four bots always run. Your priority picks which of their routes opens first, using the engine&apos;s numbers.</p>
        <div className="grid grid-cols-2 gap-1.5" role="group" aria-label="Show this route first">
          {PRIORITIES.map((p) => (
            <Card key={p.key} id={`priority-${p.key}`} label={p.label} on={priority === p.key} onClick={() => setPriority(priority === p.key ? null : p.key)} />
          ))}
        </div>
      </Group>

      <Group id="deadline" title="Deadline" summary={deadline ? `Arrive by ${to12h(deadline)}` : "Optional"} open={!!open.deadline} onToggle={() => flip("deadline")}>
        <div className="flex items-end gap-2">
          <label className="flex flex-1 flex-col gap-1 text-xs font-medium text-zinc-700">
            Arrive by
            <input
              type="time"
              data-testid="option-arrive-by"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
              className="rounded-lg border border-zinc-200 bg-zinc-50 px-2 py-1.5 text-sm text-zinc-900 focus:border-zinc-400 focus:bg-white focus:outline-none"
            />
          </label>
          {deadline && (
            <button type="button" data-testid="option-arrive-by-clear" onClick={() => setDeadline("")} className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-700 ring-1 ring-zinc-200 hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-zinc-900">
              Clear
            </button>
          )}
        </div>
      </Group>
    </div>
  );
}

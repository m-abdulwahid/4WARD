"use client";
import { useState } from "react";
import TimeField from "@/components/TimeField";
import { ParsedTime, TimeKind, parseTime, timeInPrompt } from "@/lib/timeParse";

// Option cards under the trip box. Every card writes a phrase the prompt parser (bots/nlp.py) already understands,
// so the prompt text stays the single source of truth. Options with no backend support are shown as such.

const BAGS = /\b(\d+|an?|one|two|three|four)\s+(?:big |large |heavy |carry-on )?(?:bags?|suitcases?|luggage)\b/i;
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

const tidy = (s: string) => s.replace(/\s+([.,])/g, "$1").replace(/\s{2,}/g, " ").trim();

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
  deadlineText,
  setDeadlineText,
  leaveText,
  setLeaveText,
}: {
  prompt: string;
  setPrompt: (f: (p: string) => string) => void;
  focusPrompt: () => void;
  deadlineText: string;
  setDeadlineText: (t: string) => void;
  leaveText: string;
  setLeaveText: (t: string) => void;
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

  // Times: the typed field wins over the trip text; say so whenever they differ.
  const fieldTime = (text: string) => (text.trim() ? parseTime(text) : null);
  const deadlineP = fieldTime(deadlineText);
  const leaveP = fieldTime(leaveText);
  const note = (kind: TimeKind, text: string, p: ParsedTime | null) => {
    const said = timeInPrompt(prompt, kind);
    if (!text.trim()) return said ? <p className="text-zinc-600">Your trip text says {said.label}. Type here to change it.</p> : null;
    if (!p || (said && said.hhmm === p.hhmm)) return null;
    return (
      <p className="text-amber-900">
        {said ? <>Your trip text says {said.label}.</> : <>Your trip text no longer mentions this time.</>} This field wins: Plan my trip uses {p.label}.
      </p>
    );
  };
  const textDeadline = timeInPrompt(prompt, "deadline");
  const timeSummary =
    [deadlineP && `Arrive by ${deadlineP.label}`, leaveP && `Leave at ${leaveP.label}`].filter(Boolean).join(", ") ||
    (textDeadline ? `Arrive by ${textDeadline.label} (from text)` : "Optional");

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

      <Group id="deadline" title="Deadline and leave time" summary={timeSummary} open={!!open.deadline} onToggle={() => flip("deadline")}>
        <div className="flex flex-col gap-3">
          <TimeField
            id="deadline-field"
            label="Arrive by"
            text={deadlineText}
            setText={setDeadlineText}
            testid="deadline-input"
            pickerTestid="option-arrive-by"
            clearTestid="option-arrive-by-clear"
            extraNote={
              <>
                {note("deadline", deadlineText, deadlineP)}
                {deadlineP?.tomorrow && !leaveP?.tomorrow && (
                  <p className="text-amber-900">
                    4WARD checks deadlines against today&rsquo;s clock, so the route cards will skip &ldquo;makes your deadline&rdquo; for a deadline tomorrow. Set a leave time tomorrow too to get that check.
                  </p>
                )}
              </>
            }
          />
          <TimeField
            id="leave-field"
            label="Leave at"
            text={leaveText}
            setText={setLeaveText}
            testid="leave-at-input"
            pickerTestid="leave-at-picker"
            clearTestid="leave-at-clear"
            extraNote={note("leave", leaveText, leaveP)}
          />
          <p className="text-[11px] leading-snug text-zinc-500">Leave blank to leave now. Times are Toronto local time.</p>
        </div>
      </Group>
    </div>
  );
}

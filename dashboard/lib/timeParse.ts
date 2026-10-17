// Turns what people type ("5pm", "17:30", "noon", "8am tomorrow", "in 2 hours") into a Toronto clock time.
// The backend only takes a time of day (HH:MM), so the result is that plus whether it falls tomorrow.

export type ParsedTime = {
  hhmm: string; // 24 h, what the prompt parser produces
  tomorrow: boolean;
  rolled: boolean; // no day given and the time had already passed today, so we assumed tomorrow
  label: string; // "5:00 PM today"
  phrase: string; // "5pm" / "5:30pm": what goes into the trip text
};

const TZ = "America/Toronto";

/** Current Toronto time as minutes after midnight. */
export function torontoMinutes(d = new Date()): number {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(d);
  const h = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const m = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  return h * 60 + m;
}

function make(mins: number, tomorrow: boolean, rolled: boolean): ParsedTime {
  const h = Math.floor(mins / 60) % 24;
  const m = mins % 60;
  const h12 = h % 12 || 12;
  const ap = h < 12 ? "AM" : "PM";
  return {
    hhmm: `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`,
    tomorrow,
    rolled,
    label: `${h12}:${String(m).padStart(2, "0")} ${ap} ${tomorrow ? "tomorrow" : "today"}`,
    phrase: `${h12}${m ? `:${String(m).padStart(2, "0")}` : ""}${ap.toLowerCase()}`,
  };
}

const WORD_NUM: Record<string, number> = { a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, half: 0.5 };

/** null = could not read it. Empty text is the caller's job (it means "no time"). */
export function parseTime(raw: string, now = torontoMinutes()): ParsedTime | null {
  let t = raw.toLowerCase().replace(/,/g, " ").replace(/\s+/g, " ").trim();
  t = t.replace(/^(?:at|by|around|about|before|leave at|leaving at|arrive by)\s+/, "");
  if (!t) return null;

  // relative: "in 2 hours", "in 45 min", "in an hour", "in half an hour", "in 1.5 hours", "in 1 hour 30 minutes"
  const rel = t.match(/^in\s+(?:(\d+(?:\.\d+)?|an?|one|two|three|four|five|six|half)\s*(?:an?\s+)?(h|hr|hrs|hour|hours))?\s*(?:and\s+)?(?:(\d+)\s*(m|min|mins|minute|minutes))?$/);
  if (rel && (rel[1] || rel[3])) {
    const hours = rel[1] ? (isNaN(Number(rel[1])) ? WORD_NUM[rel[1]] : Number(rel[1])) : 0;
    const total = now + Math.round(hours * 60) + (rel[3] ? Number(rel[3]) : 0);
    if (total >= 48 * 60) return null;
    return make(total % (24 * 60), total >= 24 * 60, false);
  }
  if (t === "now" || t === "right now") return make(now, false, false);

  // day and part-of-day words
  let day: "today" | "tomorrow" | null = null;
  let part: "am" | "pm" | null = null;
  t = t
    .replace(/\b(tomorrow|tmrw|tmr)\b/, () => ((day = "tomorrow"), ""))
    .replace(/\btoday\b/, () => ((day = day ?? "today"), ""))
    .replace(/\btonight\b/, () => ((day = day ?? "today"), (part = "pm"), ""))
    .replace(/\b(?:this|in the)\s+(morning)\b|\bmorning\b/, () => ((part = "am"), ""))
    .replace(/\b(?:this|in the)\s+(afternoon|evening)\b|\b(afternoon|evening)\b/, () => ((part = "pm"), ""))
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^(?:at|by)\s+/, ""); // "tomorrow at 8"

  let mins: number | null = null;
  let explicit = false; // am/pm or 24 h given, so no guessing needed
  if (t === "noon" || t === "midday") (mins = 12 * 60), (explicit = true);
  else if (t === "midnight") {
    return make(0, true, false); // the coming midnight
  } else {
    const m = t.match(/^(\d{1,2})(?:[:h.](\d{2}))?\s*(a\.?m?\.?|p\.?m?\.?)?$/) ?? t.match(/^(\d{2})(\d{2})$/);
    if (!m) return null;
    let h = Number(m[1]);
    const min = Number(m[2] ?? 0);
    const ap = m[3]?.[0] as "a" | "p" | undefined;
    if (min > 59 || h > 23) return null;
    if (ap) {
      if (h < 1 || h > 12) return null;
      h = (h % 12) + (ap === "p" ? 12 : 0);
      explicit = true;
    } else if (h === 0 || h >= 13 || /^\d{4}$/.test(t) || (m[2] && m[1].length === 2 && h < 10)) {
      explicit = true; // 24 h clock: "17:30", "0930", "08:15"
    } else if (part) {
      h = (h % 12) + (part === "pm" ? 12 : 0);
      explicit = true;
    }
    mins = h * 60 + min;
    if (!explicit) {
      // "5" or "5:30": take the next time it comes round (5 am or 5 pm), the summary shows which
      const am = (h % 12) * 60 + min;
      const pm = am + 12 * 60;
      if (day === "tomorrow") return make(am, true, false);
      const next = [am, pm].find((x) => x >= now);
      return next !== undefined ? make(next, false, false) : make(am, true, true);
    }
  }
  if (day === "tomorrow") return make(mins, true, false);
  if (mins < now) return make(mins, true, true); // already passed today: assume tomorrow
  return make(mins, false, false);
}

// ---- keeping the trip text and the time fields in agreement ------------------------------------------
// Same patterns as bots/nlp.py (plus an optional day word), so we read the text exactly the way the backend will.
const PATTERNS = {
  deadline: /\b(by|before|arrive(?: at| by)?)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b(?:\s+(today|tomorrow|tonight))?/gi,
  leave: /\b(leave|leaving|depart(?:ing)?|starting)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b(?:\s+(today|tomorrow|tonight))?/gi,
};
const OUR_SENTENCE = { deadline: /I need to arrive by [^.]*\./i, leave: /I'm leaving at [^.]*\./i };
export type TimeKind = keyof typeof PATTERNS;

/** The time the backend will read from the trip text (first match; no am/pm means a 24 h hour, as in nlp.py _clock). */
export function timeInPrompt(prompt: string, kind: TimeKind): { hhmm: string; label: string } | null {
  const re = new RegExp(PATTERNS[kind].source, "i");
  const m = prompt.match(re);
  if (!m) return null;
  let h = Number(m[2]);
  const min = Number(m[3] ?? 0);
  const ap = m[4]?.toLowerCase();
  if (ap === "pm" && h < 12) h += 12;
  if (ap === "am" && h === 12) h = 0;
  if (h > 23 || min > 59) return null;
  const h12 = h % 12 || 12;
  return { hhmm: `${String(h).padStart(2, "0")}:${String(min).padStart(2, "0")}`, label: `${h12}:${String(min).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}` };
}

const tidy = (s: string) =>
  s.replace(/\s+([.,])/g, "$1").replace(/,+\./g, ".").replace(/,{2,}/g, ",").replace(/\.{2,}/g, ".").replace(/\s{2,}/g, " ").trim();

/** Put this time into the trip text (replacing any earlier one), or remove it when p is null. */
export function writeTimeToPrompt(prompt: string, kind: TimeKind, p: ParsedTime | null): string {
  const g = PATTERNS[kind];
  if (!p) return tidy(prompt.replace(OUR_SENTENCE[kind], "").replace(g, ""));
  const words = `${p.phrase}${p.tomorrow ? " tomorrow" : ""}`;
  let first = true;
  const out = prompt.replace(g, (_m, verb: string) => {
    if (!first) return "";
    first = false;
    if (kind === "deadline") return `${verb} ${words}`;
    return `${verb[0] === verb[0].toUpperCase() ? "Leaving" : "leaving"} at ${words}`;
  });
  if (!first) return tidy(out);
  return tidy(`${prompt} ${kind === "deadline" ? `I need to arrive by ${words}.` : `I'm leaving at ${words}.`}`);
}

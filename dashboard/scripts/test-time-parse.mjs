// Tests for lib/timeParse.ts. Run: npm run test:time  (Node 22.18+/24 runs the TypeScript directly)
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseTime } from "../lib/timeParse.ts";

const at = (h, m = 0) => h * 60 + m; // "now" in Toronto, minutes after midnight
const read = (text, now) => {
  const p = parseTime(text, now);
  return p && `${p.hhmm}${p.tomorrow ? "+1" : ""}${p.rolled ? " rolled" : ""}`;
};

test("clock times", () => {
  assert.equal(read("5pm", at(9)), "17:00");
  assert.equal(read("5 pm", at(9)), "17:00");
  assert.equal(read("5:30 pm", at(9)), "17:30");
  assert.equal(read("5:30PM", at(9)), "17:30");
  assert.equal(read("5.30pm", at(9)), "17:30");
  assert.equal(read("5p", at(9)), "17:00");
  assert.equal(read("17:30", at(9)), "17:30");
  assert.equal(read("1730", at(9)), "17:30");
  assert.equal(read("08:15", at(7)), "08:15");
  assert.equal(read("12am", at(9)), "00:00+1 rolled");
  assert.equal(read("12pm", at(9)), "12:00");
  assert.equal(read("noon", at(9)), "12:00");
  assert.equal(read("midnight", at(9)), "00:00+1");
  assert.equal(read("by 6pm", at(9)), "18:00");
  assert.equal(read("at 7am", at(5)), "07:00");
});

test("past times roll to tomorrow", () => {
  assert.equal(read("5pm", at(18)), "17:00+1 rolled");
  assert.equal(read("17:00", at(17, 1)), "17:00+1 rolled");
  assert.equal(read("5pm", at(17)), "17:00"); // exactly now still counts as today
});

test("day words", () => {
  assert.equal(read("8am tomorrow", at(9)), "08:00+1");
  assert.equal(read("tomorrow 8am", at(9)), "08:00+1");
  assert.equal(read("tomorrow at 8", at(9)), "08:00+1");
  assert.equal(read("tonight 9", at(9)), "21:00");
  assert.equal(read("9 tonight", at(9)), "21:00");
  assert.equal(read("8 this morning", at(6)), "08:00");
  assert.equal(read("3 this afternoon", at(9)), "15:00");
});

test("bare hours pick the next one", () => {
  assert.equal(read("5", at(9)), "17:00"); // 5 am passed, 5 pm is next
  assert.equal(read("5", at(3)), "05:00");
  assert.equal(read("5:30", at(20)), "05:30+1 rolled");
});

test("relative times", () => {
  assert.equal(read("in 2 hours", at(9)), "11:00");
  assert.equal(read("in 2 hrs", at(9)), "11:00");
  assert.equal(read("in 45 min", at(9)), "09:45");
  assert.equal(read("in an hour", at(9, 30)), "10:30");
  assert.equal(read("in half an hour", at(9)), "09:30");
  assert.equal(read("in 1.5 hours", at(9)), "10:30");
  assert.equal(read("in 1 hour 30 minutes", at(9)), "10:30");
  assert.equal(read("in 3 hours", at(23)), "02:00+1");
  assert.equal(read("now", at(9, 5)), "09:05");
});

test("nonsense is rejected", () => {
  for (const bad of ["", "soon", "25:00", "13pm", "5:75", "pizza", "in hours", "0am"]) assert.equal(read(bad, at(9)), null, bad);
});

test("labels and prompt phrases", () => {
  const p = parseTime("17:30", at(9));
  assert.equal(p.label, "5:30 PM today");
  assert.equal(p.phrase, "5:30pm");
  assert.equal(parseTime("5pm", at(18)).label, "5:00 PM tomorrow");
  assert.equal(parseTime("noon", at(9)).phrase, "12pm");
});

import { timeInPrompt, writeTimeToPrompt } from "../lib/timeParse.ts";

test("trip text: read the way the backend does", () => {
  assert.equal(timeInPrompt("York U to CN Tower by 6pm", "deadline").hhmm, "18:00");
  assert.equal(timeInPrompt("York U to CN Tower by 5", "deadline").hhmm, "05:00"); // nlp.py: no am/pm = 24 h
  assert.equal(timeInPrompt("leaving at 8am, by 9:30am", "leave").hhmm, "08:00");
  assert.equal(timeInPrompt("leaving at 8am, by 9:30am", "deadline").hhmm, "09:30");
  assert.equal(timeInPrompt("York U to CN Tower", "deadline"), null);
});

test("trip text: the field replaces what the text said", () => {
  const p = parseTime("5:30pm", at(9));
  assert.equal(writeTimeToPrompt("York U to CN Tower by 6pm. 2 bags.", "deadline", p), "York U to CN Tower by 5:30pm. 2 bags.");
  assert.equal(writeTimeToPrompt("A to B by 6pm, before 7pm.", "deadline", p), "A to B by 5:30pm.");
  assert.equal(writeTimeToPrompt("York U to CN Tower.", "deadline", p), "York U to CN Tower. I need to arrive by 5:30pm.");
  assert.equal(writeTimeToPrompt("York U to CN Tower. I need to arrive by 5pm.", "deadline", null), "York U to CN Tower.");
  assert.equal(writeTimeToPrompt("York U to CN Tower by 8am tomorrow.", "deadline", parseTime("9am tomorrow", at(9))), "York U to CN Tower by 9am tomorrow.");
  const leave = parseTime("8am tomorrow", at(9));
  assert.equal(writeTimeToPrompt("York U to CN Tower.", "leave", leave), "York U to CN Tower. I'm leaving at 8am tomorrow.");
  assert.equal(writeTimeToPrompt("Leaving at 7am from York U to CN Tower.", "leave", leave), "Leaving at 8am tomorrow from York U to CN Tower.");
  // what we write is read back to the same time by the backend's rules
  const written = writeTimeToPrompt("York U to CN Tower.", "deadline", p);
  assert.equal(timeInPrompt(written, "deadline").hhmm, p.hhmm);
});

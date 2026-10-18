"""Bot 'brains': given the page state, return ONE action for the Playwright runner.

GeminiBrain asks Gemini for the next action as validated JSON. RuleBrain is a deterministic
state-reconciler used when there is no key, and as a per-step fallback when Gemini errors or
returns something invalid (so one bad model response never kills a bot).
"""
import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from . import scoring
from .profiles import PROFILES, modes_for

TORONTO = ZoneInfo("America/Toronto")

ALL_MODES = ["walk", "bike", "ebike", "bikeshare", "drive", "rideshare", "transit", "park_ride"]
FLAGS = ["no_presto", "has_car", "ev", "has_bike", "has_ebike", "avoid_stairs"]


class Action(BaseModel):
    action: Literal["click", "type", "wait", "finish"]
    testid: str | None = None
    text: str | None = None
    route_id: str | None = None
    reason: str = ""


def _control(page: dict, testid: str) -> dict | None:
    return next((c for c in page["controls"] if c["testid"] == testid), None)


def _shift_hhmm(value: str, delta_min: int) -> str | None:
    try:
        t = datetime.strptime(value.strip(), "%H:%M")
    except ValueError:
        return None
    return (t + timedelta(minutes=delta_min)).strftime("%H:%M")


# A knob tuple is (kind, testid, text, reason, revert) where `revert` is the testid to click back
# (for a "click" knob, e.g. a lot button) or the original text value to retype (for a "type" knob).
Knob = tuple[str, str, str | None, str, str]


def _knob_variant(page: dict) -> Knob | None:
    """One alternative setting worth trying for a dimension the routing engine itself never searches
    (which park & ride lot, what time to leave) -- never a transit/walk/bike/drive choice, those are
    already provably optimal (Dijkstra / OSM). Returns None when there is nothing honest to try."""
    lot_ctls = [c for c in page["controls"] if c["testid"].startswith("park-ride-lot-")]
    if lot_ctls:
        current = next((c["testid"] for c in lot_ctls if c.get("pressed") == "true"), "park-ride-lot-auto")
        other = next((c["testid"] for c in lot_ctls if c["testid"] != current), None)
        if other:
            return ("click", other, None, "try a different park & ride lot", current)
    depart_ctl = _control(page, "depart-at-input")
    if depart_ctl is not None:
        value = (depart_ctl.get("value") or "").strip()
        if value:
            shifted = _shift_hhmm(value, -15)
            if shifted:
                return ("type", "depart-at-input", shifted, "try leaving 15 min earlier", value)
        else:
            shifted = _shift_hhmm(datetime.now(TORONTO).strftime("%H:%M"), 15)
            if shifted:
                return ("type", "depart-at-input", shifted, "try leaving 15 min later than right now", value)
    return None


def validate(a: dict, page: dict) -> Action:
    act = Action(**a)
    if act.action in ("click", "type"):
        ctl = _control(page, act.testid or "")
        if not ctl:
            raise ValueError(f"unknown testid {act.testid!r}")
        if act.action == "type" and act.text is None:
            raise ValueError("type needs text")
    if act.action == "finish":
        ids = {c["id"] for c in page["candidates"] if c.get("available")}
        if act.route_id not in ids:
            raise ValueError(f"finish route_id {act.route_id!r} not an available route")
    return act


class RuleBrain:
    """Deterministic state-reconciler. After the first compute, it tries exactly one alternate setting
    for a dimension the engine itself never searches (park & ride lot, departure time), compares the two
    results with its own objective's scoring, and commits to whichever is actually better -- real,
    bounded iteration, never a second guess at transit/walk/bike/drive routing."""

    name = "rule"

    def __init__(self, profile_key: str, user: dict, avoid_ids: set[str] | None = None):
        self.p, self.user = profile_key, user
        self.want_modes = modes_for(profile_key, user["constraints"])
        self.avoid_ids = avoid_ids or set()
        self.explore = "baseline"  # baseline -> change -> await -> (revert -> await_revert) -> select
        self.baseline: dict | None = None
        self.variant: dict | None = None
        self.knob: Knob | None = None

    def _pool(self, page: dict) -> list[dict]:
        return [cd for cd in page["candidates"] if cd["id"] not in self.avoid_ids] or page["candidates"]

    def next_action(self, page: dict, history: list[dict]) -> dict:
        u, c = self.user, self.user["constraints"]
        if page["state"] == "idle":
            for tid, want in (("origin-input", u["origin"]), ("destination-input", u["destination"])):
                ctl = _control(page, tid)
                if ctl and ctl.get("value") != want:
                    return {"action": "type", "testid": tid, "text": want, "reason": "enter trip endpoint"}
            for m in ALL_MODES:
                ctl = _control(page, f"mode-toggle-{m}")
                if ctl and (ctl.get("pressed") == "true") != (m in self.want_modes):
                    return {"action": "click", "testid": f"mode-toggle-{m}",
                            "reason": f"{'enable' if m in self.want_modes else 'disable'} {m} for the {self.p} objective"}
            for f in FLAGS:
                ctl = _control(page, f"toggle-{f}")
                if ctl and (ctl.get("pressed") == "true") != bool(c.get(f)):
                    return {"action": "click", "testid": f"toggle-{f}", "reason": f"set constraint {f}={bool(c.get(f))}"}
            for tid, want in (("bags-input", str(c.get("bags", 0))), ("depart-at-input", c.get("depart_at") or ""),
                              ("arrive-by-input", c.get("arrive_by") or "")):
                ctl = _control(page, tid)
                if ctl and (ctl.get("value") or "") != want:
                    return {"action": "type", "testid": tid, "text": want, "reason": f"set {tid}"}
            return {"action": "click", "testid": "compute-route", "reason": "compute routes for the chosen modes"}
        if page["state"] == "loading":
            return {"action": "wait", "reason": "routes are computing"}
        if page["state"] == "error":
            return {"action": "finish", "route_id": None, "reason": f"planner error: {page.get('error')}"}
        if page["confirmed"]:
            return {"action": "finish", "route_id": page["confirmed"], "reason": "route confirmed"}

        top = scoring.best(self.p, self._pool(page), c)

        if self.explore == "baseline":
            self.baseline = top
            self.knob = _knob_variant(page) if top else None
            if not self.knob:
                self.explore = "select"
            else:
                self.explore = "await_variant"
                kind, testid, text, reason, _revert = self.knob
                return {"action": kind, "testid": testid, **({"text": text} if text is not None else {}), "reason": reason}

        if self.explore == "await_variant":
            self.explore = "variant_computing"
            return {"action": "click", "testid": "compute-route", "reason": "recompute with that alternate setting to compare"}

        if self.explore == "variant_computing":
            self.variant = top
            kind, testid, _text, _reason, revert = self.knob
            winner = scoring.rank(self.p, [x for x in (self.baseline, self.variant) if x], c)
            keep_variant = bool(winner) and self.variant and winner[0]["id"] == self.variant["id"]
            if keep_variant or not self.baseline:
                self.explore = "select"
            else:
                self.explore = "await_revert"
                if kind == "click":
                    return {"action": "click", "testid": revert, "reason": "revert: the original setting scored better for this objective"}
                return {"action": "type", "testid": testid, "text": revert, "reason": "revert: the original setting scored better for this objective"}

        if self.explore == "await_revert":
            self.explore = "revert_computing"
            return {"action": "click", "testid": "compute-route", "reason": "recompute after reverting"}

        if self.explore == "revert_computing":
            self.explore = "select"

        if not page["selected"]:
            top = scoring.best(self.p, self._pool(page), c)
            if not top:
                return {"action": "finish", "route_id": None, "reason": "no usable route found"}
            return {"action": "click", "testid": f"select-route-{top['id']}",
                    "reason": f"best for '{self.p}' by {PROFILES[self.p].name} scoring: {top['duration_min']} min, ${top['cost']:.2f}, {top['co2_kg']} kg CO2"}
        return {"action": "click", "testid": "confirm-route", "reason": "confirm the selected route"}


SYSTEM = """You operate a route-planner web dashboard through a browser to find ONE route for a user.
You are the "{name}" bot. Your objective: {objective}

Each call you see the user's request, the current page (controls with data-testid, their state, and any computed route
options) and your recent actions. Reply with a JSON plan: {{"actions":[...]}} where each action is one of
  {{"action":"click","testid":"..."}} | {{"action":"type","testid":"...","text":"..."}} | {{"action":"wait"}} |
  {{"action":"finish","route_id":"..."}}   (each with a short "reason")
Actions run in order in the browser. Rules:
- Use only testids listed in `controls`. Typing replaces the field's value. aria-pressed "true" means a toggle is ON.
- PHASE 1 (planner_state idle): plan every setup action in one go - type origin and destination, turn ON only the travel modes
  worth exploring for YOUR objective (and OFF the rest), turn on constraint toggles matching the user's facts (has_car, no_presto,
  ...), set bags and times - and END the plan with click compute-route. Do not plan anything after compute-route.
- PHASE 2 (planner_state done, route_options present, first time you see results): the engine's own transit/walk/bike/drive
  routing is already provably optimal (Dijkstra for transit, OSM for walk/bike/drive) -- never re-try those, that would be
  theater. But if controls named `park-ride-lot-<name>` or `depart-at-input` are present, THOSE are dimensions the engine
  never searches by itself. Try exactly ONE alternative there (a different lot, or shift the departure time by about
  15 minutes), then click compute-route again, and nothing else, in this plan.
- PHASE 3 (you have now seen two computes: your original setting and one alternative): compare the two sets of
  route_options using YOUR objective's priorities only. If the alternative is not actually better for your objective,
  undo it (click the original lot button back, or retype the original departure time) and click compute-route once more
  to restore it. Then plan: click select-route-<id> for whichever setting actually won, click confirm-route, finish with
  that route_id.
- If there was nothing to explore (no park-ride-lot or depart-at-input controls, or you already explored once), go
  straight from PHASE 2 to selecting, confirming, and finishing.
- If planner_state is "error", you may fix one obvious problem once (for example a typo) and recompute. If it is
  still "error" after that one retry, stop guessing: finish immediately with route_id null and the error as your
  reason. The trip is most likely genuinely out of scope or unresolvable; do not keep retrying spellings.
- Never pick a route that is unavailable, misses the deadline (if a deadline-meeting route exists), or is impractical
  (e.g. hours slower than alternatives, or hostile to the user's bags).
- Do not repeat actions that already took effect. Only ever try one alternative setting, never more.
"""


class Plan(BaseModel):
    actions: list[Action]


class GeminiBrain:
    name = "gemini"

    def __init__(self, profile_key: str, user: dict, avoid_ids: set[str] | None = None):
        from google import genai

        self.p, self.user = profile_key, user
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.avoid_ids = avoid_ids or set()
        prof = PROFILES[profile_key]
        self.system = SYSTEM.format(name=prof.name, objective=prof.objective)

    async def next_actions(self, page: dict, history: list[dict]) -> list[dict]:
        from google.genai import types

        options = [c for c in page["candidates"] if c["id"] not in self.avoid_ids] or page["candidates"]
        brief = {
            "request": self.user,
            "controls": [{k: v for k, v in c.items() if v not in (None, "")} for c in page["controls"]
                         if not c["testid"].startswith(("route-card", "map", "planner"))],
            "planner_state": page["state"], "error": page.get("error"),
            "selected_route": page["selected"], "confirmed_route": page["confirmed"],
            "route_options": [{k: c.get(k) for k in ("id", "available", "reason", "duration_min", "cost", "co2_kg", "transfers",
                                                       "walk_m", "bag_friendliness", "meets_deadline") if c.get(k) is not None}
                              for c in options],
            "recent_actions": history[-6:],
        }
        cfg = types.GenerateContentConfig(system_instruction=self.system, response_mime_type="application/json",
                                          response_schema=Plan, temperature=0)
        for attempt in (1, 2):  # one retry for transient 429/5xx
            try:
                resp = await self.client.aio.models.generate_content(model=self.model, contents=json.dumps(brief, default=str), config=cfg)
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(2.0)
        acts = json.loads(resp.text)["actions"][:10]
        for i, a in enumerate(acts):  # nothing after the first compute: results must be seen before choosing
            if a.get("action") == "click" and a.get("testid") == "compute-route":
                acts = acts[: i + 1]
                break
        return acts


class FallbackBrain:
    """Gemini plans a batch of actions per call (few API calls, rate-limit friendly); every action is validated against the
    live page right before it runs. Any error/invalid action drops the plan and the rule brain covers that step."""

    def __init__(self, profile_key: str, user: dict, avoid_ids: set[str] | None = None):
        self.rule = RuleBrain(profile_key, user, avoid_ids)
        self.gemini = None
        self.queue: list[dict] = []
        self.fallbacks = 0
        self.gemini_calls = 0
        self.last_error = ""
        if os.environ.get("GEMINI_API_KEY") and os.environ.get("BOTS_BRAIN", "auto") != "rule":
            try:
                self.gemini = GeminiBrain(profile_key, user, avoid_ids)
            except Exception as e:
                self.last_error = str(e)[:200]
        self.name = "gemini" if self.gemini else "rule"

    async def next_action(self, page: dict, history: list[dict]) -> dict:
        if self.gemini:
            try:
                if not self.queue:
                    self.gemini_calls += 1
                    self.queue = await self.gemini.next_actions(page, history)
                    if not self.queue:
                        raise ValueError("empty plan")
                a = self.queue.pop(0)
                validate(a, page)
                if a["action"] == "finish" and not page["confirmed"]:
                    raise ValueError("finish before confirm")
                return {**a, "brain": "gemini"}
            except Exception as e:
                self.queue.clear()
                self.fallbacks += 1
                self.last_error = str(e)[:200]
        return {**self.rule.next_action(page, history), "brain": "rule"}

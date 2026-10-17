"""Bot 'brains': given the page state, return ONE action for the Playwright runner.

GeminiBrain asks Gemini for the next action as validated JSON. RuleBrain is a deterministic
state-reconciler used when there is no key, and as a per-step fallback when Gemini errors or
returns something invalid (so one bad model response never kills a bot).
"""
import json
import os
from typing import Literal

from pydantic import BaseModel

from . import scoring
from .profiles import PROFILES, modes_for

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
    name = "rule"

    def __init__(self, profile_key: str, user: dict):
        self.p, self.user = profile_key, user
        self.want_modes = modes_for(profile_key, user["constraints"])

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
        if page["state"] == "idle":
            return {"action": "click", "testid": "compute-route", "reason": "compute routes for the chosen modes"}
        if page["state"] == "loading":
            return {"action": "wait", "reason": "routes are computing"}
        if page["state"] == "error":
            return {"action": "finish", "route_id": None, "reason": f"planner error: {page.get('error')}"}
        if page["confirmed"]:
            return {"action": "finish", "route_id": page["confirmed"], "reason": "route confirmed"}
        if not page["selected"]:
            top = scoring.best(self.p, page["candidates"], c)
            if not top:
                return {"action": "finish", "route_id": None, "reason": "no usable route found"}
            return {"action": "click", "testid": f"select-route-{top['id']}",
                    "reason": f"best for '{self.p}' by {PROFILES[self.p].name} scoring: {top['duration_min']} min, ${top['cost']:.2f}, {top['co2_kg']} kg CO2"}
        return {"action": "click", "testid": "confirm-route", "reason": "confirm the selected route"}


SYSTEM = """You operate a route-planner web dashboard through a browser to find ONE route for a user.
You are the "{name}" bot. Your objective: {objective}

Each turn you see the user's request, the current page (controls with data-testid, their state, and any computed route
options) and your recent actions. Reply with exactly ONE next action as JSON:
  {{"action":"click","testid":"..."}} | {{"action":"type","testid":"...","text":"..."}} | {{"action":"wait"}} |
  {{"action":"finish","route_id":"..."}}   (+ a short "reason")
Rules:
- Use only testids listed in `controls`. Typing replaces the field's value.
- Typical flow: type origin, type destination, turn ON only the travel modes worth exploring for YOUR objective (aria-pressed true = ON),
  turn on constraint toggles that match the user's facts (has_car, no_presto, ...), set bags/times, click compute-route,
  then click select-route-<id> for the best option for your objective, click confirm-route, then finish with that route_id.
- Never pick a route marked unavailable, one that misses the deadline (if a deadline-meeting route exists), or an impractical one.
- Be efficient: do not repeat actions that already took effect.
"""


class GeminiBrain:
    name = "gemini"

    def __init__(self, profile_key: str, user: dict):
        from google import genai

        self.p, self.user = profile_key, user
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        prof = PROFILES[profile_key]
        self.system = SYSTEM.format(name=prof.name, objective=prof.objective)

    async def next_action(self, page: dict, history: list[dict]) -> dict:
        from google.genai import types

        brief = {
            "request": self.user,
            "controls": [{k: v for k, v in c.items() if v not in (None, "")} for c in page["controls"]
                         if not c["testid"].startswith(("route-card", "map", "planner"))],
            "planner_state": page["state"], "error": page.get("error"),
            "selected_route": page["selected"], "confirmed_route": page["confirmed"],
            "route_options": [{k: c.get(k) for k in ("id", "available", "reason", "duration_min", "cost", "co2_kg", "transfers",
                                                       "walk_m", "bag_friendliness", "meets_deadline") if c.get(k) is not None}
                              for c in page["candidates"]],
            "recent_actions": history[-6:],
        }
        resp = await self.client.aio.models.generate_content(
            model=self.model,
            contents=json.dumps(brief, default=str),
            config=types.GenerateContentConfig(system_instruction=self.system, response_mime_type="application/json",
                                               response_schema=Action, temperature=0),
        )
        return json.loads(resp.text)


class FallbackBrain:
    """Gemini first; on any error/invalid action fall back to the rule brain for that step."""

    def __init__(self, profile_key: str, user: dict):
        self.rule = RuleBrain(profile_key, user)
        self.gemini = None
        self.fallbacks = 0
        self.last_error = ""
        if os.environ.get("GEMINI_API_KEY") and os.environ.get("BOTS_BRAIN", "auto") != "rule":
            try:
                self.gemini = GeminiBrain(profile_key, user)
            except Exception as e:
                self.last_error = str(e)[:200]
        self.name = "gemini" if self.gemini else "rule"

    async def next_action(self, page: dict, history: list[dict]) -> dict:
        if self.gemini:
            try:
                a = await self.gemini.next_action(page, history)
                validate(a, page)
                # a finish before anything is confirmed is premature: let the rule brain drive instead
                if not (a["action"] == "finish" and not page["confirmed"]):
                    return {**a, "brain": "gemini"}
                raise ValueError("finish before confirm")
            except Exception as e:
                self.fallbacks += 1
                self.last_error = str(e)[:200]
        return {**self.rule.next_action(page, history), "brain": "rule"}

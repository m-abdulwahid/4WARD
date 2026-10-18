"""Bot 'brains': given the page state, return ONE action for the Playwright runner.

GeminiBrain asks Gemini for the next action as validated JSON. RuleBrain is a deterministic
state-reconciler used when there is no key, and as a per-step fallback when Gemini errors or
returns something invalid (so one bad model response never kills a bot).

Neither brain ever decides which route wins: the pick is bots.optimize.top() over the ONE shared candidate set
the orchestrator computed for the trip. The brains only operate the UI and then select/confirm that pick.
"""
import asyncio
import json
import os
import re
from typing import Literal

from pydantic import BaseModel

from . import optimize
from .profiles import PROFILES, modes_for

ALL_MODES = ["walk", "bike", "ebike", "bikeshare", "drive", "rideshare", "transit", "park_ride"]
FLAGS = ["no_presto", "has_car", "ev", "has_bike", "has_ebike", "avoid_stairs", "start_in_car", "car_needed_after", "plan_car_return"]
MAX_GEMINI_CALLS = 3  # setup plans per bot; a trip with many stops may need more than one batch


class Action(BaseModel):
    action: Literal["click", "type", "wait", "finish"]
    testid: str | None = None
    text: str | None = None
    route_id: str | None = None
    reason: str = ""


def _control(page: dict, testid: str) -> dict | None:
    return next((c for c in page["controls"] if c["testid"] == testid), None)


def validate(a: dict, page: dict, extra_ids: set[str] | None = None) -> Action:
    act = Action(**a)
    if act.action in ("click", "type"):
        ctl = _control(page, act.testid or "")
        if not ctl:
            raise ValueError(f"unknown testid {act.testid!r}")
        if act.action == "type" and act.text is None:
            raise ValueError("type needs text")
    if act.action == "finish" and act.route_id is not None:
        ids = {c["id"] for c in page["candidates"] if c.get("available")} | (extra_ids or set())
        if act.route_id not in ids:
            raise ValueError(f"finish route_id {act.route_id!r} not an available route")
    return act


def _keeps_shared_setup(a: dict, page: dict, want_modes: list[str]) -> None:
    """All four bots must compute the same candidate set, so no bot may flip a travel-mode toggle away from
    the shared setting (e.g. Gemini switching park & ride off 'because it is not relevant for cheap')."""
    tid = a.get("testid") or ""
    if a.get("action") == "click" and tid.startswith("mode-toggle-"):
        ctl = _control(page, tid)
        mode = tid[len("mode-toggle-"):]
        if ctl is not None and (ctl.get("pressed") == "true") == (mode in want_modes):
            raise ValueError(f"{tid} would change the shared mode set")


class RuleBrain:
    """Deterministic state-reconciler: sets up the shared trip in the UI, computes, then selects and confirms the
    route the engine's per-objective ranking (bots.optimize) picked from the shared candidate set."""

    name = "rule"

    def __init__(self, profile_key: str, user: dict, shared: list[dict] | None = None):
        self.p, self.user = profile_key, user
        self.want_modes = modes_for(profile_key, user["constraints"])
        self.shared = shared

    def pool(self, page: dict) -> list[dict]:
        return self.shared if self.shared else page["candidates"]

    def _stops_step(self, page: dict) -> dict | None:
        """One action that brings the page's stop list (names, stay, type, order, reorder flag) in line with the trip."""
        want = self.user.get("stops") or []
        have = sorted(int(m.group(1)) for c in page["controls"] if (m := re.fullmatch(r"stop-(\d+)-input", c["testid"])))
        if len(have) < len(want) and _control(page, "add-stop"):
            return {"action": "click", "testid": "add-stop", "reason": f"add stop {len(have) + 1} of {len(want)}"}
        if len(have) > len(want):
            return {"action": "click", "testid": f"stop-{have[-1]}-remove", "reason": "remove a stop that is not in the trip"}
        for i, s in enumerate(want):
            name_ctl = _control(page, f"stop-{i}-input")
            if name_ctl is not None and name_ctl.get("value") != s["name"]:
                return {"action": "type", "testid": f"stop-{i}-input", "text": s["name"], "reason": f"enter stop {i + 1}"}
            kind = s.get("type", "stop")
            if (ctl := _control(page, f"stop-{i}-type-{kind}")) is not None and ctl.get("pressed") != "true":
                return {"action": "click", "testid": f"stop-{i}-type-{kind}", "reason": f"stop {i + 1} is a {kind}"}
            if kind == "alight":
                veh = s.get("vehicle") or "transit"
                vctl = _control(page, f"stop-{i}-vehicle-{veh}")
                if vctl is not None and vctl.get("pressed") != "true":
                    return {"action": "click", "testid": f"stop-{i}-vehicle-{veh}", "reason": f"stop {i + 1}: get off the {veh}"}
            win = s.get("window") or ["", ""]
            for edge, want_t in (("start", win[0]), ("end", win[1])):
                wctl = _control(page, f"stop-{i}-window-{edge}")
                if wctl is not None and (wctl.get("value") or "") != want_t:
                    return {"action": "type", "testid": f"stop-{i}-window-{edge}", "text": want_t, "reason": f"meeting time window {edge} for stop {i + 1}"}
            dwell = s.get("dwell_min") or 0
            want_dwell = str(int(dwell)) if float(dwell).is_integer() else str(dwell)
            if (ctl := _control(page, f"stop-{i}-dwell")) is not None and kind != "via" and (ctl.get("value") or "0") != want_dwell:
                return {"action": "type", "testid": f"stop-{i}-dwell", "text": want_dwell, "reason": f"stay {want_dwell} min at stop {i + 1}"}
        for side in ("origin", "destination"):  # a ride starts here / you are dropped off here / you park here
            want_kind = self.user.get(f"{side}_type")
            for kind in ("pickup", "dropoff", "park"):
                ctl = _control(page, f"{side}-type-{kind}")
                if ctl is not None and (ctl.get("pressed") == "true") != (want_kind == kind):
                    return {"action": "click", "testid": f"{side}-type-{kind}", "reason": f"the {side} is {want_kind or kind}: set the {side} logistics"}
        opt = _control(page, "toggle-optimize_order")
        if opt is not None and (opt.get("pressed") == "true") != bool(self.user.get("optimize_order")):
            return {"action": "click", "testid": "toggle-optimize_order", "reason": "set whether the planner may reorder stops"}
        return None

    def next_action(self, page: dict, history: list[dict]) -> dict:
        u, c = self.user, self.user["constraints"]
        if page["state"] == "idle":
            for tid, want in (("origin-input", u["origin"]), ("destination-input", u["destination"])):
                ctl = _control(page, tid)
                if ctl and ctl.get("value") != want:
                    return {"action": "type", "testid": tid, "text": want, "reason": "enter trip endpoint"}
            step = self._stops_step(page)
            if step:
                return step
            for m in ALL_MODES:
                ctl = _control(page, f"mode-toggle-{m}")
                if ctl and (ctl.get("pressed") == "true") != (m in self.want_modes):
                    return {"action": "click", "testid": f"mode-toggle-{m}",
                            "reason": f"{'enable' if m in self.want_modes else 'disable'} {m} (every bot searches the same set of modes)"}
            for f in FLAGS:
                ctl = _control(page, f"toggle-{f}")
                if ctl and (ctl.get("pressed") == "true") != bool(c.get(f)):
                    return {"action": "click", "testid": f"toggle-{f}", "reason": f"set constraint {f}={bool(c.get(f))}"}
            for tid, want in (("bags-input", str(c.get("bags", 0))), ("depart-at-input", c.get("depart_at") or ""),
                              ("arrive-by-input", c.get("arrive_by") or "")):
                ctl = _control(page, tid)
                if ctl and (ctl.get("value") or "") != want:
                    return {"action": "type", "testid": tid, "text": want, "reason": f"set {tid}"}
            return {"action": "click", "testid": "compute-route", "reason": "compute routes for the shared trip"}
        if page["state"] == "loading":
            return {"action": "wait", "reason": "routes are computing"}
        if page["state"] == "error":
            return {"action": "finish", "route_id": None, "reason": f"planner error: {page.get('error')}"}
        if page["confirmed"]:
            return {"action": "finish", "route_id": page["confirmed"], "reason": "route confirmed"}

        pick = optimize.top(self.p, self.pool(page), c)
        if not pick:
            return {"action": "finish", "route_id": None, "reason": "no usable route found"}
        why = f"{PROFILES[self.p].name} pick from the shared set: {pick['duration_min']} min, ${pick['cost']:.2f}, {pick['co2_kg']} kg CO2"
        if not page["selected"]:
            if _control(page, f"select-route-{pick['id']}") is None:  # live data changed between the engine's run and the page's
                return {"action": "finish", "route_id": pick["id"], "reason": f"{why} (not shown on the page; using the engine's route)"}
            return {"action": "click", "testid": f"select-route-{pick['id']}", "reason": why}
        return {"action": "click", "testid": "confirm-route", "reason": "confirm the selected route"}


SYSTEM = """You operate a route-planner web dashboard through a browser for a user's trip.
You are the "{name}" bot. Your objective: {objective}

Each call you see the user's request and the current page (controls with data-testid, their state). Reply with a JSON
plan: {{"actions":[...]}} where each action is one of
  {{"action":"click","testid":"..."}} | {{"action":"type","testid":"...","text":"..."}} | {{"action":"wait"}}
  (each with a short "reason")
Actions run in order in the browser. Rules:
- Use only testids listed in `controls`. Typing replaces the field's value. aria-pressed "true" means a toggle is ON.
- You are only ever asked to plan the SETUP (planner_state is "idle"): type origin and destination, set the constraint
  toggles matching the user's facts (has_car, no_presto, ...), set bags and times - and END the plan with click compute-route.
- If the request has `stops` (in visiting order, keep that order): click add-stop once per stop, type each name into
  stop-N-input (N from 0), click stop-N-type-<type> for its type, and type its stay in minutes into stop-N-dwell
  (not for via). Never reorder stops yourself; only click toggle-optimize_order when the request says optimize_order is true.
- The request may also say how you travel: origin_type "pickup" (click origin-type-pickup), destination_type "dropoff" or "park"
  (click destination-type-<type>), constraints start_in_car / car_needed_after / plan_car_return (click toggle-<name> when true),
  and per stop a window ["HH:MM","HH:MM"] (type into stop-N-window-start and stop-N-window-end). Set exactly what the request says.
- Turn ON every travel-mode toggle (mode-toggle-*) that is OFF, and never turn one off. All four bots search the same
  modes so their results are comparable; an action that turns a mode off is rejected, and so is computing while a mode
  or constraint still differs from the user's request.
- Do not plan anything after compute-route. The routing engine computes every candidate's real numbers, and deterministic
  code ranks them by your objective and selects the winner. You never choose a different route than the engine's.
- Do not repeat actions that already took effect.
"""


class Plan(BaseModel):
    actions: list[Action]


class GeminiBrain:
    name = "gemini"

    def __init__(self, profile_key: str, user: dict):
        from google import genai

        self.p, self.user = profile_key, user
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        prof = PROFILES[profile_key]
        self.system = SYSTEM.format(name=prof.name, objective=prof.objective)

    async def next_actions(self, page: dict, history: list[dict]) -> list[dict]:
        """Only ever called while planner_state is "idle" (see FallbackBrain) -- this brain plans the setup,
        never the route choice, so it has no need to see computed results at all."""
        from google.genai import types

        brief = {
            "request": self.user,
            "controls": [{k: v for k, v in c.items() if v not in (None, "")} for c in page["controls"]
                         if not c["testid"].startswith(("route-card", "map", "planner"))],
            "planner_state": page["state"],
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

    def __init__(self, profile_key: str, user: dict, shared: list[dict] | None = None):
        self.rule = RuleBrain(profile_key, user, shared)
        self.gemini = None
        self.queue: list[dict] = []
        self.fallbacks = 0
        self.gemini_calls = 0
        self.last_error = ""
        if os.environ.get("GEMINI_API_KEY") and os.environ.get("BOTS_BRAIN", "auto") != "rule":
            try:
                self.gemini = GeminiBrain(profile_key, user)
            except Exception as e:
                self.last_error = str(e)[:200]
        self.name = "gemini" if self.gemini else "rule"

    async def next_action(self, page: dict, history: list[dict]) -> dict:
        # Gemini only ever plans the SETUP (planner_state "idle"). The moment results exist, which route wins is
        # decided by bots.optimize (the shared candidate set ranked by this bot's one objective) -- never by asking
        # an LLM to judge numbers it was only shown as text. See CLAUDE.md: "Numbers come from the engine.
        # Gemini decides what to try, never what the answer is."
        if self.gemini and page["state"] == "idle":
            try:
                if not self.queue:
                    if self.gemini_calls >= MAX_GEMINI_CALLS:
                        raise ValueError("Gemini setup budget used up; the rule brain finishes the setup")
                    self.gemini_calls += 1
                    self.queue = await self.gemini.next_actions(page, history)
                    if not self.queue:
                        raise ValueError("empty plan")
                a = self.queue.pop(0)
                validate(a, page)
                _keeps_shared_setup(a, page, self.rule.want_modes)
                if a.get("action") == "click" and a.get("testid") == "compute-route":
                    # Only compute once the setup matches the shared trip; otherwise the rule brain finishes the setup.
                    if self.rule.next_action(page, history).get("testid") != "compute-route":
                        raise ValueError("setup is incomplete; every mode and constraint must match the shared trip first")
                return {**a, "brain": "gemini"}
            except Exception as e:
                self.queue.clear()
                self.fallbacks += 1
                self.last_error = str(e)[:200]
        return {**self.rule.next_action(page, history), "brain": "rule"}

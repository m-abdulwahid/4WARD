"""Plain-language trip prompt -> structured request.

Gemini does the parsing when GEMINI_API_KEY is set; a regex parser is the fallback so the demo
never depends on the network. Both return the same shape:
  {"origin": str, "destination": str, "origin_type": "pickup"|None, "destination_type": "dropoff"|"park"|None,
   "stops": [{"name", "type", "dwell_min", "window"?}], "optimize_order": bool, "constraints": {...},
   "clarifications": [{"id", "question", "options": [{"label", "replace": [from, to]} | {"label", "append": text}]}],
   "parser": "gemini"|"rules"}

The vocabulary (what "dropped off", "park at", "via", "meet at 6" mean) lives in bots/vocabulary.py and is held to the
same examples in tests. A phrase that cannot be read one way (get off at X, a time with no am/pm, a car you need later)
becomes a clarification question; the run does not start until the user answers, and nothing is guessed.
"""
import json
import os
import re

from backend.config import MEET_WINDOW_GRACE_MIN
from backend.geo import GAZETTEER

from .vocabulary import gemini_vocabulary

NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4}
STOP = r"(?:\s+(?:by|before|at|with|arriving|around|and i|and we|using|but|in time|because|while|please|thanks)\b|[,.;]|$)"
STOP_TYPES = ("stop", "via", "pickup", "dropoff", "errand", "park", "alight")
YES_RETURN = "Plan the return to my car."
NO_RETURN = "No need to plan a return to my car."


def _clock(h: str, m: str | None, ap: str | None) -> str:
    hh = int(h)
    if ap:
        ap = ap.lower().replace(".", "")
        if ap == "pm" and hh < 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0
    return f"{hh:02d}:{int(m or 0):02d}"


def _plus_minutes(hhmm: str, minutes: int) -> str:
    h, m = hhmm.split(":")
    t = (int(h) * 60 + int(m) + minutes) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"


_PLACE = r"(?:the\s+)?(?P<place>[A-Za-z0-9'’.&\- ]+?)(?=\s+(?:for|by|before|with|and\s+(?:then|stop\w*|swing\w*|pick\w*|drop\w*|via|errands?|go\w*|i|we|get\w*|meet\w*|park\w*)|then|at|on|arriving|using|but|because|while|please|thanks|to|from|so)\b|[,.;]|$)"
_DWELL = r"(?:\s+for\s+(?:about\s+|around\s+)?(?P<n>\d+(?:\.\d+)?|an?|half an?)\s*(?P<u>minutes?|mins?|hours?|hrs?)\b)?"
_WHO = r"(?:(?:someone|somebody|a\s+friend|my\s+\w+)\s+)?"
# Passive / logistics forms first (they describe what happens to YOU), then the things you do.
_KINDS = [
    (r"(?:get(?:ting)?\s+|be\s+|being\s+|to\s+be\s+)?dropped\s+off\s+at", "dropoff"),
    (_WHO + r"drop(?:s|ping)?\s+me\s+(?:off\s+)?at", "dropoff"),
    (r"(?:get(?:ting)?\s+|be\s+|being\s+|to\s+be\s+)?picked\s+up\s+at", "pickup"),
    (_WHO + r"pick(?:s|ing)?\s+me\s+up\s+at", "pickup"),
    (r"park(?:ing)?(?:\s+(?:my|the)\s+car)?\s+(?:at|in)", "park"),
    (r"get(?:ting)?\s+off\s+(?:the\s+)?(?:bus|train|subway|streetcar|tram|go train|transit)\s+at", "alight"),  # the vehicle noun is kept in "vehicle"
    (r"stops?(?:ping)?\s+(?:in\s+)?(?:at|by)", "stop"),
    (r"swing(?:ing)?\s+by", "stop"),
    (r"go(?:ing)?\s+by", "stop"),
    (r"errands?\s+(?:at|in)", "errand"),
    (r"pick(?:ing)?\s+up\s+(?!me\b)(?:[a-z]+\s+){1,3}?at", "errand"),
    (r"drop(?:ping)?\s+off\s+(?!me\b)(?:[a-z]+\s+){1,3}?at", "errand"),
    (r"via", "via"),
    (r"through", "via"),
]
_GET_OFF = re.compile(r"\bget(?:ting)?\s+(?:off|out)\s+at\s+" + _PLACE, re.I)
_MEET = re.compile(
    r"\b(?:meet(?:ing)?|catch|see)\s+(?:up\s+with\s+)?(?:(?:my|a|an|the|some)\s+)?(?:[a-z]+\s+){0,2}?(?:at|in|near)\s+(?:the\s+)?"
    r"(?P<place>[A-Za-z0-9'’.&\- ]+?)\s+at\s+(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap]\.?m\.?)?(?![\w:])" + _DWELL, re.I)
_OPTIMIZE = re.compile(r"\b(?:in\s+(?:any|whatever|the\s+best)\s+order|(?:best|optimal|most\s+efficient|any)\s+order|re-?order\s+(?:my\s+|the\s+)?stops|optimi[sz]e\s+(?:the\s+)?order)\b", re.I)
_START_IN_CAR = re.compile(
    r"\b(?:(?:i'?m|i am|we'?re)\s+(?:currently\s+)?(?:starting\s+|sitting\s+)?(?:in|inside)\s+(?:my|the|our)\s+(?:car|vehicle)"
    r"|(?:start(?:ing)?|begin(?:ning)?)\s+(?:out\s+)?in\s+(?:my|the|our)\s+(?:car|vehicle)|(?:i'?m|i am)\s+(?:currently\s+)?driving)\b", re.I)
_CAR_LATER = re.compile(
    r"\b(?:(?:need|want|require)\s+(?:my|the)\s+car\s+(?:later|after|afterwards|tonight|tomorrow|to\s+(?:get|go|drive)\s+(?:home|back))"
    r"|(?:keep|leave)\s+[\w ]{0,25}?in\s+the\s+car\s+for\s+later|drive\s+(?:home|back)\s+(?:after|afterwards)|get\s+(?:my|the)\s+car\s+(?:back|later))", re.I)
_YES_RETURN = re.compile(r"\bplan\s+(?:the\s+)?return\s+to\s+(?:my|the)\s+car\b", re.I)
_NO_RETURN = re.compile(r"\bno\s+need\s+to\s+plan\s+(?:a|the)\s+return\s+to\s+(?:my|the)\s+car\b", re.I)


def _dwell_minutes(n: str | None, unit: str | None) -> float:
    if not n or not unit:
        return 0.0
    v = 0.5 if n.lower().startswith("half") else 1.0 if n.lower() in ("a", "an") else float(n)
    return v * (60 if unit.lower().startswith("h") else 1)


def _clean_place(s: str) -> str:
    s = re.sub(r"^(?:then\s+|go\s+to\s+|head\s+to\s+|going\s+to\s+|to\s+)+", "", s.strip(), flags=re.I)
    return re.sub(r"^(?:the|my)\s+", "", s.strip(" ,.;"), flags=re.I)


def _same_place(a: str, b: str) -> bool:
    n = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return bool(n(a)) and n(a) == n(b)


def _extract_stops(t: str) -> dict:
    """Pull every stop / logistics phrase out of the text. Returns {"core": text without them, "stops": [...] in the order
    typed, "optimize": bool, "ends": (origin, destination) when the trip was written as a chain, "clarifications": [...]}."""
    optimize = bool(_OPTIMIZE.search(t))
    found: list[tuple[int, dict]] = []
    spans: list[tuple[int, int]] = []
    clar: list[dict] = []

    def free(m) -> bool:
        return not any(a < m.end() and m.start() < b for a, b in spans)

    for m in _MEET.finditer(t):  # "meet a friend at X at 6pm": a stop with a fixed time
        name = _clean_place(m.group("place"))
        if not name or not free(m):
            continue
        spans.append((m.start(), m.end()))
        stop = {"name": name, "type": "stop", "dwell_min": _dwell_minutes(m.group("n"), m.group("u"))}
        if m.group("ap") or int(m.group("h")) > 12 or int(m.group("h")) == 0:
            start = _clock(m.group("h"), m.group("m"), m.group("ap"))
            stop["window"] = [start, _plus_minutes(start, MEET_WINDOW_GRACE_MIN)]
        else:
            h = m.group("h")
            shown = f"{h}:{m.group('m')}" if m.group("m") else h
            clar.append({"id": "meet-time", "place": name,
                         "question": f"Meeting at {name} at {shown}: is that {shown} AM or {shown} PM?",
                         "options": [{"label": f"{shown} AM", "replace": [m.group(0), f"{m.group(0)} am"]},
                                     {"label": f"{shown} PM", "replace": [m.group(0), f"{m.group(0)} pm"]}]})
        found.append((m.start(), stop))
    for m in _GET_OFF.finditer(t):  # no vehicle named: never guess
        if not free(m):
            continue
        name = _clean_place(m.group("place"))
        spans.append((m.start(), m.end()))
        base = re.match(r"\bget(?:ting)?\s+(?:off|out)\s+at\b", m.group(0), re.I).group(0)
        clar.append({"id": "get-off", "place": name,
                     "question": f"\"Get off at {name}\": do you mean getting off the bus (or another transit vehicle) or getting out of a car?",
                     "options": [{"label": "Get off the bus or transit vehicle", "replace": [base, "get off transit at"]},
                                 {"label": "Get dropped off from a car", "replace": [base, "get dropped off at"]}]})
    for kw, kind in _KINDS:
        for m in re.finditer(r"(?:\b(?:and|with|including)\s+)?(?:\ba\s+)?\b(?:" + kw + r")\s+" + _PLACE + _DWELL, t, re.I):
            if not free(m):
                continue
            name = _clean_place(m.group("place"))
            if not name:
                continue
            spans.append((m.start(), m.end()))
            stop = {"name": name, "type": kind, "dwell_min": 0.0 if kind == "via" else _dwell_minutes(m.group("n"), m.group("u"))}
            if kind == "alight":
                v = re.search(r"\b(bus|subway|streetcar|tram|go train|train|transit)\b", m.group(0), re.I)
                stop["vehicle"] = {"tram": "streetcar", "go train": "train"}.get(v.group(1).lower(), v.group(1).lower()) if v else "transit"
            found.append((m.start(), stop))
    core = t
    for a, b in sorted(spans, reverse=True):
        core = core[:a] + " " + core[b:]
    core = re.sub(r"\s{2,}", " ", core).strip()
    ends = None
    parts = re.split(r"\s*,?\s*(?:and\s+)?\bthen\b\s*", core, flags=re.I)
    if len(parts) >= 2 and all(0 < len(_clean_place(p).split()) <= 6 for p in parts[1:]):
        first = parse_endpoints(parts[0])
        tail = [_clean_place(p) for p in parts[1:]]
        if first[0] and first[1]:
            chain = [first[0], first[1]] + tail
            ends = (chain[0], chain[-1])
            for i, name in enumerate(chain[1:-1]):
                found.append((t.lower().find(name.lower()) if name.lower() in t.lower() else 10_000 + i, {"name": name, "type": "stop", "dwell_min": 0.0}))
            core = f"from {chain[0]} to {chain[-1]}"
    elif not re.search(r"\b(?:from|need|want|get to|have to|must|start|end|finish)\b", core, re.I):
        segs = re.split(r"\s+(?:to|→|->)\s+", core)
        segs = [re.sub(r"^(?:please\s+|route\s+|take me\s+)", "", s, flags=re.I).strip(" ,.;") for s in segs]
        if len(segs) >= 3 and all(0 < len(s.split()) <= 5 for s in segs):
            ends = (segs[0], segs[-1])
            for i, name in enumerate(segs[1:-1]):
                found.append((t.lower().find(name.lower()) if name.lower() in t.lower() else 10_000 + i, {"name": _clean_place(name), "type": "stop", "dwell_min": 0.0}))
            core = f"from {segs[0]} to {segs[-1]}"
    return {"core": core, "stops": [s for _, s in sorted(found, key=lambda x: x[0])], "optimize": optimize, "ends": ends, "clarifications": clar}


def parse_endpoints(t: str) -> tuple[str, str]:
    """Origin and destination from text that contains no stop phrases."""
    low = t.lower()
    origin = destination = ""
    m = re.search(r"\bfrom\s+([^.;!?]+?)\s+to\s+([^.;!?]+?)" + STOP, t, re.I)
    if m:
        origin, destination = m.group(1).strip(), m.group(2).strip()
    else:
        m = re.search(r"\bto\s+((?:(?!\bto\b)[^.;!?,])+?)\s+from\s+([^.;!?]+?)" + STOP, t, re.I)
        verbs = {"pay", "drive", "keep", "be", "get", "go", "see", "take", "use", "have", "do", "make", "park", "walk", "find", "ride",
                 "leave", "avoid", "bring", "travel", "reach", "arrive", "meet", "stop", "swing", "pick", "drop", "spend", "buy"}
        if m and len(m.group(1).split()) <= 6 and m.group(1).split()[0].lower() not in verbs:  # "want to pay the fare from X" is not a trip
            destination, origin = m.group(1).strip(), m.group(2).strip()
    if not (origin and destination):
        m = re.search(r"(?:(?:i'?m|i am)?\s*(?:currently\s+|starting\s+|start\s+)*(?:(?:in|inside)\s+(?:my|the)\s+car\s+)?(?:at|in|on))\s+(.+?)\s+(?:and\s+)?(?:i\s+)?(?:need|want|have|must|would like)\s+to\s+(?:get to|reach|go to|travel to|be at|get)\s+(.+?)" + STOP, t, re.I)
        if m:
            origin, destination = m.group(1).strip(), m.group(2).strip()
            origin = re.split(r"\s+with\s+", origin)[0]
            destination = re.sub(r"\s+in\s+(?:downtown\s+|the\s+city\s+of\s+)?toronto$", "", destination, flags=re.I)
    if not (origin and destination):
        m = re.match(r"\s*(?:please\s+)?(?:route\s+|take me\s+)?(.+?)\s+to\s+(.+?)" + STOP, t, re.I)
        if m and len(m.group(1).split()) <= 5:
            origin, destination = m.group(1).strip(), m.group(2).strip()
    if not (origin and destination):  # last resort: two known landmarks, role decided by the word before each
        hits = sorted((low.find(k), k) for k in GAZETTEER if k in low)
        if len(hits) >= 2:
            def role(pos: int) -> str:
                before = low[max(0, pos - 14):pos]
                return "dest" if re.search(r"(to|reach|(?:end|ending|finish|finishing)(?:\s+up)?\s+at)\s+(the\s+)?$", before) else "orig" if re.search(r"(from|at)\s+(the\s+)?$", before) else ""
            (p1, a), (p2, b) = hits[0], hits[1]
            if role(p1) == "dest" or role(p2) == "orig":
                a, b = b, a
            origin, destination = (t[low.find(x):low.find(x) + len(x)] for x in (a, b))  # keep the user's casing
    # "start from X" / "end at Y" are explicit and win over everything above
    after = r"(?=\s+(?:and\s+(?:get|go|then|end|finish|head|travel|meet|stop|swing|park|pick|drop|take|i|we)|then|by|before|with|but|because|while|please|thanks)\b|[,.;]|$)"
    ms = re.search(r"\bstart(?:ing)?\s+(?:out\s+)?(?:from|at|in)\s+(?!(?:my|the|a|our)\s+(?:car|vehicle)\b)([^.;!?,]+?)" + after, t, re.I)
    me = re.search(r"\b(?:end(?:ing)?|finish(?:ing)?)\s+(?:up\s+)?(?:at|in)\s+([^.;!?,]+?)" + after, t, re.I)
    if ms:
        origin = ms.group(1).strip()
    if me:
        destination = me.group(1).strip()
    origin, destination = (re.sub(r"^(?:the|my)\s+", "", x, flags=re.I) for x in (origin, destination))
    return origin, destination


def car_return_clarification(c: dict, stops: list[dict], destination_type: str | None, destination: str, text: str) -> dict | None:
    """Ask whether to plan the return to the car when the user needs it later or a later stop needs it."""
    if not (c.get("has_car") or c.get("start_in_car")) or c.get("plan_car_return") or _NO_RETURN.search(text):
        return None
    later_park = next((s["name"] for i, s in enumerate(stops) if s["type"] == "park" and i >= 1), None)
    if destination_type == "park" and stops:
        later_park = later_park or destination
    options = [{"label": "Yes, plan the way back to my car", "append": YES_RETURN},
               {"label": "No, I will deal with the car myself", "append": NO_RETURN}]
    if later_park:
        return {"id": "car-return", "place": later_park, "options": options,
                "question": f"Your car has to be with you at {later_park}. If an earlier part of the trip leaves it somewhere else, should I plan the way back to it first?"}
    if c.get("car_needed_after"):
        return {"id": "car-return", "place": "", "options": options,
                "question": "You will need your car later. If a route leaves it parked somewhere, should I plan the way back to it at the end of the trip?"}
    return None


def finalize(out: dict, text: str, clar: list[dict]) -> dict:
    """Shared tail for both parsers: promote a stop at the origin/destination to origin_type/destination_type,
    infer the facts logistics words imply, and add the clarification questions."""
    stops = out["stops"]
    out.setdefault("origin_type", None)
    out.setdefault("destination_type", None)
    if not out["destination"]:  # "I'll be dropped off at X" can be the only mention of the destination
        tail = next((s for s in reversed(stops) if s["type"] in ("dropoff", "park")), None)
        if tail:
            out["destination"] = tail["name"]
    if not out["origin"]:
        head = next((s for s in stops if s["type"] == "pickup"), None)
        if head:
            out["origin"] = head["name"]
    keep = []
    for s in stops:
        if out["destination"] and _same_place(s["name"], out["destination"]) and s["type"] in ("dropoff", "park") and not out["destination_type"]:
            out["destination_type"] = s["type"]
        elif out["origin"] and _same_place(s["name"], out["origin"]) and s["type"] == "pickup" and not out["origin_type"]:
            out["origin_type"] = "pickup"
        else:
            keep.append(s)
    out["stops"] = keep
    c = out["constraints"]
    if any(s["type"] == "park" for s in keep) or out["destination_type"] == "park":
        c["has_car"] = True  # "park at X" only makes sense with a car
    if _START_IN_CAR.search(text):
        c["start_in_car"] = True
        c["has_car"] = True
    c.setdefault("start_in_car", False)
    c["car_needed_after"] = bool(_CAR_LATER.search(text)) and not _NO_RETURN.search(text)
    c["plan_car_return"] = bool(_YES_RETURN.search(text))
    out["clarifications"] = list(clar)
    q = car_return_clarification(c, keep, out["destination_type"], out["destination"], text)
    if q:
        out["clarifications"].append(q)
    # Gemini may have guessed what the user left ambiguous: drop a "get off" stop it made up, and any meeting time it assumed.
    gone = {x["place"] for x in clar if x["id"] == "get-off"}
    unsure_time = {x["place"] for x in clar if x["id"] == "meet-time"}
    out["stops"] = [s for s in out["stops"] if s["name"] not in gone]
    for s in out["stops"]:
        if s["name"] in unsure_time:
            s.pop("window", None)
    return out


def parse_rules(text: str) -> dict:
    full = text.strip()
    low = full.lower()
    ex = _extract_stops(full)
    origin, destination = ex["ends"] if ex["ends"] else parse_endpoints(ex["core"])
    origin, destination = (re.sub(r"^(?:the|my)\s+", "", x, flags=re.I) for x in (origin, destination))
    c: dict = {"bags": 0, "no_presto": False, "has_car": False, "ev": False, "has_bike": False,
               "has_ebike": False, "avoid_stairs": False}
    m = re.search(r"\b(\d+|an?|one|two|three|four)\s+(?:big |large |heavy |carry-on )?(?:bags?|suitcases?|luggage)\b", low)
    if m:
        c["bags"] = int(m.group(1)) if m.group(1).isdigit() else NUM_WORDS.get(m.group(1), 1)
    elif re.search(r"\b(luggage|suitcase|groceries)\b", low):
        c["bags"] = 1
    if re.search(r"no presto|(?:don'?t|do not|dont) have (?:a )?presto|without (?:a )?presto|no (?:transit|bus) pass|no pass", low):
        c["no_presto"] = True
    if re.search(r"\b(my car|have a car|i drive|i have a vehicle|driving)\b", low):
        c["has_car"] = True
    if re.search(r"\b(electric|tesla|\bev\b)\b", low):
        c["ev"] = True
        c["has_car"] = True
    if re.search(r"e-?bike", low):
        c["has_ebike"] = True
    if re.search(r"\b(my bike|have a bike|my bicycle)\b", low):
        c["has_bike"] = True
    if re.search(r"\bstairs?|wheelchair|stroller\b", low):
        c["avoid_stairs"] = True
    m = re.search(r"\b(?:by|before|arrive(?: at| by)?)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", low)
    if m:
        c["arrive_by"] = _clock(m.group(1), m.group(2), m.group(3))
    m = re.search(r"\b(?:leave|leaving|depart(?:ing)?|starting)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", low)
    if m:
        c["depart_at"] = _clock(m.group(1), m.group(2), m.group(3))
    out = {"origin": origin, "destination": destination, "stops": ex["stops"], "optimize_order": ex["optimize"],
           "constraints": c, "parser": "rules"}
    return finalize(out, full, ex["clarifications"])


_STOP_KEYS = ("name", "type", "dwell_min")


async def parse_prompt(text: str) -> dict:
    key = os.environ.get("GEMINI_API_KEY")
    rules = parse_rules(text)
    if not key:
        return rules
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        sys_prompt = (
            "Extract a Toronto trip request as JSON with keys: origin (string place name), destination (string), "
            "origin_type (\"pickup\" if a ride starts at the origin, else null), destination_type (\"dropoff\" if the user is dropped off "
            "at the destination, \"park\" if they park their car there, else null), "
            "bags (int), no_presto (bool: user lacks a Presto card/pass), has_car, ev (car is electric), has_bike, "
            "has_ebike, avoid_stairs, start_in_car (bool: the user starts the trip sitting in their car) (bools), "
            "arrive_by and depart_at (24h HH:MM or null), stay_hours (number or null), "
            "max_walk_m (int or null), optimize_order (bool: true ONLY if the user explicitly says the stop order does not matter or "
            "asks you to pick the best order; otherwise false), and stops: the places the user wants to visit BETWEEN origin and "
            "destination, in exactly the order they were mentioned, each {name (string place name), type (one of stop, via, pickup, "
            "dropoff, errand, park, alight), dwell_min (number of minutes spent there, 0 if not said; 0 for via), "
            "window (optional [\"HH:MM\",\"HH:MM\"] for a fixed meeting time: start and start+5 minutes), "
            "vehicle (only for alight: bus, subway, streetcar, train, or transit when none is named)}. Use an empty list when there "
            "are no intermediate stops. Do not add the origin or destination to stops. Only set facts the user stated. If a phrase is "
            "ambiguous (get off at X with no vehicle named; a meeting time with no am/pm) leave it out: the app asks the user.\n\n"
            + gemini_vocabulary()
        )
        resp = await client.aio.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=f"{sys_prompt}\n\nUser trip: {text}",
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
        )
        d = json.loads(resp.text)
        stops = []
        for s in (d.get("stops") if isinstance(d.get("stops"), list) else rules["stops"]):
            if not (isinstance(s, dict) and s.get("name")):
                continue
            kind = s.get("type") if s.get("type") in STOP_TYPES else "stop"
            stop = {"name": str(s["name"]).strip(), "type": kind, "dwell_min": 0.0 if kind == "via" else max(0.0, float(s.get("dwell_min") or 0))}
            if kind == "alight":
                said = next((r.get("vehicle") for r in rules["stops"] if r["type"] == "alight" and _same_place(r["name"], stop["name"])), None)
                stop["vehicle"] = s.get("vehicle") if s.get("vehicle") in ("bus", "subway", "streetcar", "train", "transit") else said or "transit"
            w = s.get("window")
            if isinstance(w, list) and len(w) == 2 and all(isinstance(x, str) for x in w):
                stop["window"] = w
            stops.append(stop)
        out = {"origin": d.get("origin") or rules["origin"], "destination": d.get("destination") or rules["destination"],
               "origin_type": d.get("origin_type") if d.get("origin_type") == "pickup" else rules["origin_type"],
               "destination_type": d.get("destination_type") if d.get("destination_type") in ("dropoff", "park") else rules["destination_type"],
               "stops": stops, "optimize_order": bool(d.get("optimize_order", rules["optimize_order"])),
               "constraints": {k: d[k] for k in ("bags", "no_presto", "has_car", "ev", "has_bike", "has_ebike", "avoid_stairs", "start_in_car",
                                                  "arrive_by", "depart_at", "stay_hours", "max_walk_m") if d.get(k) is not None},
               "parser": "gemini"}
        for k, v in rules["constraints"].items():  # fill anything Gemini omitted with the regex result
            out["constraints"].setdefault(k, v)
        # Ambiguities come from the rules (they are about the user's exact words); stops Gemini made out of them are dropped.
        clar = [q for q in rules["clarifications"] if q["id"] != "car-return"]
        return finalize(out, text, clar)
    except Exception as e:  # network/model/quota: never block the demo
        rules["parser_error"] = str(e)[:200]
        return rules

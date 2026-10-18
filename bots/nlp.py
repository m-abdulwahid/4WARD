"""Plain-language trip prompt -> structured request.

Gemini does the parsing when GEMINI_API_KEY is set; a regex parser is the fallback so the demo
never depends on the network. Both return the same shape:
  {"origin": str, "destination": str, "constraints": {...}, "parser": "gemini"|"rules"}
"""
import json
import os
import re

from backend.geo import GAZETTEER

NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4}
STOP = r"(?:\s+(?:by|before|at|with|arriving|around|and i|and we|using|but|in time|because|while|please|thanks)\b|[,.;]|$)"


def _clock(h: str, m: str | None, ap: str | None) -> str:
    hh = int(h)
    if ap:
        ap = ap.lower()
        if ap == "pm" and hh < 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0
    return f"{hh:02d}:{int(m or 0):02d}"


# Stop phrases. Phase 4 adds the full vocabulary (dropped off, picked up, park at, meet at a time...); these cover the
# multi-stop basics: a waypoint with an optional dwell time ("stop at X for 20 minutes"), and a pass-through ("via X").
_PLACE = r"(?:the\s+)?(?P<place>[A-Za-z0-9'’.&\- ]+?)(?=\s+(?:for|by|before|with|and\s+(?:then|stop|swing|pick|drop|via|errands?|go|i|we)|then|at|arriving|using|but|because|while|please|thanks|to|from)\b|[,.;]|$)"
_DWELL = r"(?:\s+for\s+(?:about\s+|around\s+)?(?P<n>\d+(?:\.\d+)?|an?|half an?)\s*(?P<u>minutes?|mins?|hours?|hrs?)\b)?"
_STOP_KINDS = [
    (r"stops?(?:ping)?\s+(?:in\s+)?(?:at|by)", "stop"),
    (r"swing(?:ing)?\s+by", "stop"),
    (r"go(?:ing)?\s+by", "stop"),
    (r"errands?\s+(?:at|in)", "errand"),
    (r"pick(?:ing)?\s+(?:up\s+(?:[a-z]+\s+){0,3}?|(?:[a-z]+\s+){0,3}?up\s+)at", "pickup"),
    (r"drop(?:ping)?\s+(?:off\s+(?:[a-z]+\s+){0,3}?|(?:[a-z]+\s+){0,3}?off\s+)at", "dropoff"),
    (r"via", "via"),
    (r"through", "via"),
]
_OPTIMIZE = re.compile(r"\b(?:in\s+(?:any|whatever|the\s+best)\s+order|(?:best|optimal|most\s+efficient|any)\s+order|re-?order\s+(?:my\s+|the\s+)?stops|optimi[sz]e\s+(?:the\s+)?order)\b", re.I)


def _dwell_minutes(n: str | None, unit: str | None) -> float:
    if not n or not unit:
        return 0.0
    v = 0.5 if n.lower().startswith("half") else 1.0 if n.lower() in ("a", "an") else float(n)
    return v * (60 if unit.lower().startswith(("h")) else 1)


def _clean_place(s: str) -> str:
    s = re.sub(r"^(?:then\s+|go\s+to\s+|head\s+to\s+|going\s+to\s+|to\s+)+", "", s.strip(), flags=re.I)
    return re.sub(r"^(?:the|my)\s+", "", s.strip(" ,.;"), flags=re.I)


def _extract_stops(t: str) -> tuple[str, list[dict], bool, tuple[str, str] | None]:
    """-> (text with stop phrases removed, ordered stops, optimize_order, (origin, destination) when the trip was
    written as a chain such as "A to B to C" or "A, then B, then C")."""
    optimize = bool(_OPTIMIZE.search(t))
    found: list[tuple[int, dict]] = []
    spans: list[tuple[int, int]] = []
    for kw, kind in _STOP_KINDS:
        for m in re.finditer(r"(?:\b(?:and|with|including)\s+)?(?:\ba\s+)?\b(?:" + kw + r")\s+" + _PLACE + _DWELL, t, re.I):
            if any(a < m.end() and m.start() < b for a, b in spans):
                continue
            name = _clean_place(m.group("place"))
            if not name:
                continue
            spans.append((m.start(), m.end()))
            found.append((m.start(), {"name": name, "type": kind, "dwell_min": 0.0 if kind == "via" else _dwell_minutes(m.group("n"), m.group("u"))}))
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
    elif not re.search(r"\b(?:from|need|want|get to|have to|must)\b", core, re.I):
        segs = re.split(r"\s+(?:to|→|->)\s+", core)
        segs = [re.sub(r"^(?:please\s+|route\s+|take me\s+)", "", s, flags=re.I).strip(" ,.;") for s in segs]
        if len(segs) >= 3 and all(0 < len(s.split()) <= 5 for s in segs):
            ends = (segs[0], segs[-1])
            for i, name in enumerate(segs[1:-1]):
                found.append((t.lower().find(name.lower()) if name.lower() in t.lower() else 10_000 + i, {"name": _clean_place(name), "type": "stop", "dwell_min": 0.0}))
            core = f"from {segs[0]} to {segs[-1]}"
    return core, [s for _, s in sorted(found, key=lambda x: x[0])], optimize, ends


def parse_endpoints(t: str) -> tuple[str, str]:
    """Origin and destination from text that contains no stop phrases."""
    low = t.lower()
    origin = destination = ""
    m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)" + STOP, t, re.I)
    if m:
        origin, destination = m.group(1).strip(), m.group(2).strip()
    else:
        m = re.search(r"\bto\s+((?:(?!\bto\b).)+?)\s+from\s+(.+?)" + STOP, t, re.I)
        if m:
            destination, origin = m.group(1).strip(), m.group(2).strip()
    if not (origin and destination):
        m = re.search(r"(?:i'?m at|i am at|currently at|starting at|start at|at)\s+(.+?)\s+(?:and\s+)?(?:i\s+)?(?:need|want|have|must|would like)\s+to\s+(?:get to|reach|go to|travel to|be at|get)\s+(.+?)" + STOP, t, re.I)
        if m:
            origin, destination = m.group(1).strip(), m.group(2).strip()
    if not (origin and destination):
        m = re.match(r"\s*(?:please\s+)?(?:route\s+|take me\s+)?(.+?)\s+to\s+(.+?)" + STOP, t, re.I)
        if m and len(m.group(1).split()) <= 5:
            origin, destination = m.group(1).strip(), m.group(2).strip()
    if not (origin and destination):  # last resort: two known landmarks, role decided by the word before each
        hits = sorted((low.find(k), k) for k in GAZETTEER if k in low)
        if len(hits) >= 2:
            def role(pos: int) -> str:
                before = low[max(0, pos - 14):pos]
                return "dest" if re.search(r"(to|reach)\s+(the\s+)?$", before) else "orig" if re.search(r"(from|at)\s+(the\s+)?$", before) else ""
            (p1, a), (p2, b) = hits[0], hits[1]
            if role(p1) == "dest" or role(p2) == "orig":
                a, b = b, a
            origin, destination = (t[low.find(x):low.find(x) + len(x)] for x in (a, b))  # keep the user's casing
    origin, destination = (re.sub(r"^(?:the|my)\s+", "", x, flags=re.I) for x in (origin, destination))
    return origin, destination


def parse_rules(text: str) -> dict:
    full = text.strip()
    low = full.lower()
    core, stops, optimize, ends = _extract_stops(full)
    origin, destination = ends if ends else parse_endpoints(core)
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
    return {"origin": origin, "destination": destination, "stops": stops, "optimize_order": optimize,
            "constraints": c, "parser": "rules"}


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
            "bags (int), no_presto (bool: user lacks a Presto card/pass), has_car, ev (car is electric), has_bike, "
            "has_ebike, avoid_stairs (bools), arrive_by and depart_at (24h HH:MM or null), stay_hours (number or null), "
            "max_walk_m (int or null), optimize_order (bool: true ONLY if the user explicitly says the stop order does not matter or "
            "asks you to pick the best order; otherwise false), and stops: the places the user wants to visit BETWEEN origin and "
            "destination, in exactly the order they were mentioned, each {name (string place name), type (one of stop, via, pickup, "
            "dropoff, errand), dwell_min (number of minutes spent there, 0 if not said; 0 for via)}. Use an empty list when there "
            "are no intermediate stops. Do not add the origin or destination to stops. Only set facts the user stated."
        )
        resp = await client.aio.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=f"{sys_prompt}\n\nUser trip: {text}",
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
        )
        d = json.loads(resp.text)
        stops = [{"name": str(s["name"]).strip(), "type": s.get("type") if s.get("type") in ("stop", "via", "pickup", "dropoff", "errand") else "stop",
                  "dwell_min": 0.0 if s.get("type") == "via" else max(0.0, float(s.get("dwell_min") or 0))}
                 for s in (d.get("stops") if isinstance(d.get("stops"), list) else rules["stops"]) if isinstance(s, dict) and s.get("name")]
        out = {"origin": d.get("origin") or rules["origin"], "destination": d.get("destination") or rules["destination"],
               "stops": stops, "optimize_order": bool(d.get("optimize_order", rules["optimize_order"])),
               "constraints": {k: d[k] for k in ("bags", "no_presto", "has_car", "ev", "has_bike", "has_ebike", "avoid_stairs",
                                                  "arrive_by", "depart_at", "stay_hours", "max_walk_m") if d.get(k) is not None},
               "parser": "gemini"}
        for k, v in rules["constraints"].items():  # fill anything Gemini omitted with the regex result
            out["constraints"].setdefault(k, v)
        return out
    except Exception as e:  # network/model/quota: never block the demo
        rules["parser_error"] = str(e)[:200]
        return rules

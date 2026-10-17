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


def parse_rules(text: str) -> dict:
    t = text.strip()
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
            origin, destination = a.title(), b.title()
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
    return {"origin": origin, "destination": destination, "constraints": c, "parser": "rules"}


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
            "max_walk_m (int or null). Only set facts the user stated."
        )
        resp = await client.aio.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=f"{sys_prompt}\n\nUser trip: {text}",
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
        )
        d = json.loads(resp.text)
        out = {"origin": d.get("origin") or rules["origin"], "destination": d.get("destination") or rules["destination"],
               "constraints": {k: d[k] for k in ("bags", "no_presto", "has_car", "ev", "has_bike", "has_ebike", "avoid_stairs",
                                                  "arrive_by", "depart_at", "stay_hours", "max_walk_m") if d.get(k) is not None},
               "parser": "gemini"}
        for k, v in rules["constraints"].items():  # fill anything Gemini omitted with the regex result
            out["constraints"].setdefault(k, v)
        return out
    except Exception as e:  # network/model/quota: never block the demo
        rules["parser_error"] = str(e)[:200]
        return rules

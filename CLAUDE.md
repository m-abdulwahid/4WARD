# 4WARD

Toronto trip planner. A user types a trip in plain language ("UTSC to King and Spadina by 8:30, two suitcases, no Presto card"). Four agents, each in its own Steel cloud browser and each chasing a different goal, operate our map dashboard in parallel. The user sees four routes side by side and picks one.

Tagline: "Four routes. One trip. Pick your way forward."

Scope is Toronto only. Hackathon project, so priority one is a demo that works every time.

## The idea in one sentence

A trip is a multi-objective search problem. The engine finds the best tradeoffs between time, cost, emissions, and reliability, and four agents explore those tradeoffs in parallel through the same dashboard a human uses.

## What makes it different from Google Maps

- It shows reliability, not just an ETA: "made the 8:00 deadline on 87% of past weekdays." This comes from stored TTC delay history.
- It handles real Toronto constraints: Presto fare rules, no card, bags, e-bike, driving cost.
- It shows four routes with different goals at once, and the agents visibly work in parallel.

## Every tool has exactly one job

If a tool is not in this table doing something nothing else does, cut it.

| Piece | Its one job |
|---|---|
| Routing engine (FastAPI) | Produces every number: time, fare, emissions, reliability. No LLM math. |
| Tiger Data (Postgres + TimescaleDB) | Stores feed history, turns it into reliability scores, powers one dashboard chart |
| Dashboard (Next.js + MapLibre) | The single surface that humans and bots both use |
| Steel | One isolated cloud browser per bot, embedded live view, traces, replay, human takeover |
| Playwright | Control protocol that drives each Steel browser over CDP |
| Gemini | Picks the next UI action for each bot, and parses the user's prompt into constraints |

## Architecture

```
User prompt
   |
   v
Gemini: prompt -> constraints JSON
   |
   v
FastAPI orchestrator --asyncio.gather--> 4 bots, each in its own Steel session (Playwright over CDP)
                                              |  Gemini function calling picks the next UI action
                                              v
                                   4WARD dashboard (public URL)
                                              |
                                              v
                      Routing engine: OTP candidates + our fare, emissions,
                      constraint filter, reliability score, Pareto ranking
                                              |
                                              v
                                   Tiger Data (feed history)
```

## The four bots

All four get the same constraints JSON and the same dashboard. Each has a goal and a status (SCANNING, ROUTING, DONE, FAILED).

1. Cheapest: lowest total cost (fares with Presto rules, parking, gas, bike fees).
2. Fastest: lowest door to door time.
3. Greenest: lowest kg CO2e.
4. Most reliable: highest chance of making the deadline, from Tiger Data history.

"Best overall" is NOT a bot. It is a badge the engine puts on one of the four results using a weighted score the user can adjust with sliders.

## How bots work

- The dashboard has real controls: origin and destination search, mode toggles, constraint toggles, waypoint drag, a Compute button. Every control has a stable `data-testid`.
- Gemini function calling is the default. Tools: `click(testid)`, `type(testid, text)`, `drag_waypoint(...)`, `read_results()`, `finish()`. Input each step: constraints, goal, accessibility snapshot of the page. Screenshots only if needed.
- Validate every tool call before executing it. Cap each bot at 25 steps and 90 seconds. A failed bot must never block the others.
- Optional comparison mode: Gemini Computer Use (pixel based). Report both in the eval numbers.
- Numbers come from the engine. Gemini decides what to try, never what the answer is.

## Coordination (the agent part that matters)

- Bots write results to a `bot_runs` table.
- Diversity rule: if two bots return the same itinerary, the orchestrator reruns the lower priority bot with "must differ from route X in at least one leg."
- Critic pass: after all bots finish, check each route against the user's constraints using engine output only. Flag violations in the UI.

## Routing engine

- OpenTripPlanner (or Valhalla) generates candidate itineraries from TTC and GO GTFS plus OpenStreetMap.
- Our layers on top, all deterministic code with tests: Presto fare engine (transfer window, fare capping, cash vs Presto), emissions model, constraint filter, reliability score, Pareto ranking.
- Gas and emissions assumptions live in one config file and are shown in the UI.

## Reliability (Tiger Data)

- Collector writes TTC delay observations and Bike Share station status every 60 to 120 seconds.
- Hypertables for the observations. A continuous aggregate for delay stats by route, stop, hour, and day of week.
- Route reliability: for each transfer, estimate the chance of making it from the delay distribution, then score the whole route by replaying historical days.
- Backfill from historical TTC delay datasets if available (verify on Toronto Open Data) so there is history on day one.
- Keep storage small enough to fit the free tier (750 MB) in case we fall back: sample at 60 to 120 seconds, keep only needed columns, compress after a day, drop raw data after 30 days.

## Data sources (verify each is available and check terms before building on it)

- TTC and GO: GTFS static and GTFS-Realtime.
- Bike Share Toronto: GBFS feed.
- Toronto Open Data: road restrictions, cycling network, TTC delay history, parking info.
- Green P: lots and rates. Live availability is probably not public, so "lack of spots" is a labeled estimate.
- Live traffic speeds are usually paid. If no free source, use typical speeds by hour and label them "estimated."
- Stretch: pages with no API (Green P rates, TTC alerts) read with Steel's scrape, extracted by Gemini into structured fields, cached with source and timestamp.

## MVP constraints (do these four well, the rest are stretch)

1. Bags: avoid stairs, long walks, and transfers with no elevator.
2. No Presto card: cash fares and what that changes.
3. Driving: gas cost plus a parking cost estimate.
4. Bike Share and e-bike legs.

Stretch: bus pass handling, accessibility profiles, "shortcut" paths. Shortcuts only through mapped pedestrian paths, parks, and plazas. Never cross private property or assume grass is walkable.

## Steel rules

- Python: `pip install steel-sdk`, `from steel import Steel`, key in `STEEL_API_KEY`. Session timeout param is `api_timeout`.
- Connect: `playwright.chromium.connect_over_cdp(f"wss://connect.steel.dev?apiKey={KEY}&sessionId={session.id}")`, then `browser.contexts[0]`. Steel's quick reference says to build this URL rather than use `session.websocketUrl` directly. If one form fails, try the other.
- ALWAYS release sessions in a `finally` block. Sessions bill per minute until released.
- Steel browsers run in the cloud and cannot reach localhost. The dashboard and backend API need public URLs (deploy or tunnel). Deploy early.
- Support `BROWSER_BACKEND=steel|local`. `local` uses a normal Playwright browser for free development and as a demo fallback. Self-hosted Steel (Docker) is another fallback.
- Embed each bot's Steel live view in the 2x2 grid. Do not build custom screenshot streaming. Link each bot's trace when it finishes.
- Docs: https://docs.steel.dev/llms.txt (add `.md` to any docs URL for markdown).

## Security and hosting

- Bots call our public backend, so add a shared bot token header, CORS limits, and basic rate limiting.
- All keys in `.env`, never committed. Keep `.env.example`. Rotate any key that was ever pasted into chat or a screenshot.
- Hosting option that also fits a sponsor track: backend and orchestrator on Vultr, dashboard on Vercel or Vultr.

## Rules

- Every number in the UI has a source and timestamp, or is labeled "estimated" or "simulated." Never present made up data as real.
- Fares are deterministic code with unit tests. Pathfinding is a real engine. Not an LLM.
- Say plainly which parts are simulated or estimated, in the UI and the pitch.
- Keep the UI keyboard navigable with readable contrast and labels on every control.
- Small commits, one feature per commit. Commit after every working phase.
- No em dashes in any user-facing text or docs.

## Tracks we are aiming at

- Best use of Gemini API: function calling drives each bot, plus prompt parsing.
- Best use of Tiger Data: hypertables, continuous aggregates, reliability scoring.
- Steel (if it is a sponsor track): parallel sessions, embedded live view, traces, human takeover.
- Optional stretch: Hack the Valley x School of Cities (a ward level "where is transit least reliable" view from the same delay data), and Climate (kg CO2 saved per trip).

## Repo layout

```
4ward/
  CLAUDE.md
  PLAN.md
  .env.example
  backend/        FastAPI app, routing layers, fares, emissions, feeds, orchestrator
  bots/           Steel client, Gemini action loop, bot profiles
  dashboard/      Next.js + MapLibre UI
  db/             schema.sql, continuous aggregates, backfill scripts
  data/           cached feeds and recorded replay data
  tests/          fare tests, routing tests, eval harness
```

## Commands (fill in as they get created)

- Backend: `uvicorn backend.main:app --reload`
- Dashboard: `cd dashboard && npm run dev`
- Bots: `python -m bots.run --prompt "..."`
- Tests: `pytest`
- Eval: `python tests/eval.py`

## Definition of done

- One typed prompt produces four visibly different routes in about 60 seconds or less, with cost, time, emissions, transfers, and reliability for each.
- The 2x2 grid shows each bot working live through Steel.
- Tiger Data powers the reliability score and at least one dashboard chart.
- Eval table exists with real numbers.
- Replay mode works with no internet.

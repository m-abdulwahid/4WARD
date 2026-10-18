# 4WARD

Greater Toronto Area trip planner. A user types a trip in plain language ("UTSC to King and Spadina by 8:30, two suitcases, no Presto card"). Four agents, each in its own Steel cloud browser and each chasing a different goal, operate our map dashboard in parallel. The user sees four routes side by side and picks one.

Tagline: "Four routes. One trip. Pick your way forward."

Scope is the GTA: Toronto plus Durham, York, Peel and Halton regions (TTC subway/streetcar/bus plus GO Transit rail), not GO's full reach into Hamilton, Niagara, Waterloo or Simcoe. Hackathon project, so priority one is a demo that works every time.

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

Display names (UI and docs) with the internal key in brackets. Keys are used by the backend, bots and every `data-testid`; never rename them.

1. Economic (`cheap`): lowest total cost (fares with Presto rules, parking, gas, bike fees).
2. Efficient (`fastest`): shortest door to door time.
3. Environmental (`green`): lowest kg CO2e.
4. Reliable (`reliable`): highest chance of arriving by the deadline, from Tiger Data delay history. Ties go to the faster route, then the cheaper one.

There is no "Best overall" bot, badge, highlight or recommendation anywhere in the product. The user chooses. The only ordering control is a neutral "Order cards by" sort (cost, time, CO2e, on-time %) with no default highlight.

## How bots work

- The dashboard has real controls: origin and destination search, mode toggles, constraint toggles, waypoint drag, a Compute button. Every control has a stable `data-testid`.
- Gemini function calling is the default. Tools: `click(testid)`, `type(testid, text)`, `drag_waypoint(...)`, `read_results()`, `finish()`. Input each step: constraints, goal, accessibility snapshot of the page. Screenshots only if needed.
- Validate every tool call before executing it. Cap each bot at 25 steps and 90 seconds. A failed bot must never block the others.
- Optional comparison mode: Gemini Computer Use (pixel based). Report both in the eval numbers.
- Numbers come from the engine. Gemini decides what to try, never what the answer is.

## Coordination (the agent part that matters)

- Bots write results to a `bot_runs` table.
- One shared candidate set per trip: the orchestrator calls the engine once with every mode (`bots/orchestrate.py shared_candidates`). Each bot operates the dashboard, but its route is `bots/optimize.py` `top()` over that set, ranked by its one objective (Economic cost, Efficient door-to-door time, Environmental CO2e, Reliable on-time %), with explicit tie-breaks. Gemini operates the UI and never picks a different route than the engine's.
- Eligibility is identical for all four bots (`optimize.eligible`): available, no violated hard constraint, within max(90 min, 2.5x the fastest route), bag-friendly when bags are given, meets the deadline when any route can. Relaxations are reported, never silent.
- Invariants, checked on every trip by `optimize.check_invariants` and shown in the UI as "Engine check failed" if broken: Efficient's time, Economic's cost and Environmental's CO2e are each at most every eligible candidate's, and Reliable's on-time % is at least every candidate's that has an estimate.
- Multi-stop trips (`backend/trip.py`): the parser returns an origin, an ordered `stops` list (name, type stop/via/pickup/dropoff/errand, dwell minutes, optional time window) and a destination; the order typed is kept unless `optimize_order` is set (default off, at most 3 stops). Each consecutive pair is routed with every mode, timed from the estimated arrival at the previous stop, and the per-leg options are chained into whole-trip candidates with the same shape as single-leg ones, so `bots/optimize.py` ranks them unchanged. Own vehicles (car, bike, e-bike) are carried across legs: you can only use one that is with you, and it stays where you left it. A `via` point must be passed in the same mode. The dashboard and `/route` accept `stops`; the planner page has stop rows (`stop-N-input`, `stop-N-dwell`, `stop-N-type-*`, `add-stop`) and map markers A, B, C, D.
- Logistics (`backend/trip.py`, `backend/highways.py`, `bots/vocabulary.py`): the engine tracks traveler_state (on_foot, cycling, driving, passenger, on_transit) and the car (where it is, whether you are in it). You can only drive a car that is with you; a parked car stays where it was left; mode changes happen only at stops, stations, parking places and pick-up/drop-off points; every parked car has a cost and a walking connection (and a walk back to it when you drive on). "Dropped off at X" is a free lift (no fare, no parking, their car does not continue), "picked up at X" starts a lift, "park at X" arrives driving and leaves the car there, "get off the bus at X" arrives by transit, "via X" keeps the same mode. Starting in a car means the first leg is driven; starting in a car on a controlled-access highway means first a drive to the next exit. Walking and cycling legs along controlled-access highways (DVP, Gardiner, 400-series, QEW) are rejected (`violates: highway_walk`), using the bundled OSM extract in `data/highways.json` (rebuild with `python -m backend.build_highways`).
- Car return: if the user says they will need the car later, or a later stop needs it, the app ASKS whether to plan the way back. Yes plans a final leg back to the car when it is needed after the trip, and a "go back to the car, then drive" leg when a later stop must be driven to; without it, routes that leave the car behind are ineligible (`car_left_behind`).
- Ambiguity never gets guessed: "get off at X" (bus or car?), a meeting time with no am/pm, and the car-return question come back as `clarify` questions with option buttons; the run starts once they are answered. The glossary and 13 worked examples in `bots/vocabulary.py` are given to Gemini and enforced on the rule parser in tests.
- Time accuracy (`backend/schedule.py`, `backend/timetable.py`, `backend/conditions.py`): every leg gets a real start, end, wait and duration on one Toronto-time clock, so total duration = last arrival minus first departure = the sum of the legs. TTC and GO rail legs use real scheduled departures from `data/timetable.json` (built from the GTFS static feeds by `python -m backend.build_timetable`; one representative stop sequence per line plus every trip's start time, per service day and calendar exception), with a configurable boarding and transfer buffer (`BOARDING_BUFFER_S`, `TRANSFER_BUFFER_S` in `backend/config.py`) and an observed TTC delay from the stored GTFS-RT polls for departures within 30 minutes. Lines with no timetable here (YRT, Durham, MiWay, the generic bus) keep an ESTIMATED headway wait and say so. Road legs use the conditions at the moment they start (`conditions.road_factor`: TomTom's live ratio when a departure is soon and TomTom has something fresh, labelled "TomTom live (not stored)"; otherwise the typical hour profile, labelled ESTIMATED). With a deadline and no departure time a plain trip is planned BACKWARDS (each route leaves as late as still arrives on time: "leave by 07:42"); a deadline that already passed today means tomorrow; a deadline that cannot be met says so and gives the earliest arrival. The router still chooses the transit path with headway costs, then times that path with the real schedule.
- No duplicate-avoidance rerun. If bots pick the same route, the UI shows one card tagged with every objective it wins, all four bots stay listed, and each bot's best different route is shown as "Next best for this goal".
- Time is door to door everywhere (`duration_min`): walking, waits, transfers, plus estimated parking search and walk (`backend/config.py`).
- Critic pass: after all bots finish, check each route against the user's constraints using engine output only. Flag violations in the UI.

## Ride-hail leg and fare scouts

Some routes use a short ride-hail leg to a transit hub (for example a ride to Guildwood GO, then the train). The engine gets ride-hail estimates from a provider layer with three backends:

- `sim` (default): our own simulator, "RideSim", at /ridesim in the dashboard. Fare = base + per km + per minute, times a surge multiplier set by pickup zone and time. Quotes expire after about 60 seconds. A "Trigger storm" button spikes surge for demos. Every page shows a SIMULATED banner. Do not use the Uber name, logo, or lookalike styling.
- `steel-live` (optional, read only): Steel fare scouts read a public price estimator page for a few pickup and drop-off pairs. No login, no booking, results cached, rate limited. Only if the site's terms allow it, and never use captcha solving or proxies to get around blocks.
- `cache`: last known estimates, used by replay mode.

Fare scouts run in parallel, one Steel session per candidate hub, because quotes expire fast. Results go into a `fare_quotes` hypertable in Tiger Data (ts, provider, pickup, dropoff, price, surge, source, simulated flag) so the dashboard can chart surge over time.

Rules for this part:
- Price depends on pickup zone, time, and trip length. Do not claim "surge arbitrage." The honest claim is a short, cheap ride plus transit can beat one long ride.
- Every ride-hail number shows its provider and a SIMULATED or LIVE label.
- Never log in to a real ride-hail account, never store credentials, never book anything.
- Test live mode manually in a logged-out browser first. If the estimator needs a login or blocks automation, skip live mode and use `sim`.

## Routing engine

- OpenTripPlanner (or Valhalla) generates candidate itineraries from TTC and GO GTFS plus OpenStreetMap.
- Our layers on top, all deterministic code with tests: Presto fare engine (transfer window, fare capping, cash vs Presto), emissions model, constraint filter, reliability score, Pareto ranking.
- Gas and emissions assumptions live in one config file and are shown in the UI.

## Reliability (Tiger Data)

- Collector writes TTC delay observations, official TTC service alerts (cause/effect/text, `ttc_alerts`), and Bike Share station status every 60 to 120 seconds.
- Hypertables for the observations. A continuous aggregate for delay stats by route, stop, hour, and day of week.
- Route reliability (`backend/reliability.py`): deterministic, nothing seeded. Transit legs with a route_id replay stored TTC delay history, one pass/fail per historical date, exactly as before Phase 6. Road and generic-bus legs have no stored delay history to replay (TomTom is never stored, see below), so they're folded in with a documented default distribution (`ONTIME_CV` in `backend/config.py`) via a closed-form Normal CDF: for each historical date (or once against the full slack, when a route has only road legs) the probability the road legs' variability still lands inside the slack. Transfers: if a historical day's delay through earlier legs would have eaten the scheduled transfer buffer, that day also picks up the real typical wait for the next connecting departure (`timetable.wait_estimate_s`), not a flat guess. Every route gets a percentage now, including pure-drive ones. Returns `reliability_pct`, `history_days`, a `confidence` label ramped by `history_days` (capped at 14), and a `delay_factors` list ("what could delay you") read straight off each leg's already-computed live signal (TomTom's ratio, an observed TTC delay, an active service alert, or an explicit default), each with its real source and timestamp.
- TomTom (`backend/tomtom.py`, the shared conditions layer plugged into `conditions._observed`): TomTom's terms prohibit storing their data (no derivative database) and allow caching only for as long as their response headers permit, so this is in-memory only, a 5-minute-TTL ratio averaged over a handful of downtown corridor points, never written to Tiger Data, SQLite, a file, or a log line (the `httpx`/`httpcore` loggers are explicitly silenced so the key-bearing request URL can't leak that way either). A daily request cap stays well under the strictest plausible reading of TomTom's free quota (their own docs disagree across pages). On failure, no key, or cap reached: falls back to the typical-hour profile, labelled ESTIMATED, never fails a route. Labelled "TomTom live (not stored)" everywhere it reaches the UI.
- Road restrictions (`backend/road_restrictions.py`, City of Toronto open data): off by default (`ROAD_RESTRICTIONS_ENABLED=true` to enable), and not yet wired into any route's timing. When enabled: in-memory only, refreshed at most once a day.
- Backfill from historical TTC delay datasets if available (verify on Toronto Open Data) so there is history on day one.
- Keep storage small enough to fit the free tier (750 MB) in case we fall back: sample at 60 to 120 seconds, keep only needed columns, compress after a day, drop raw data after 30 days.

## Data sources (verify each is available and check terms before building on it)

- TTC and GO: GTFS static and GTFS-Realtime.
- Bike Share Toronto: GBFS feed.
- Toronto Open Data: road restrictions, cycling network, TTC delay history, parking info.
- Green P: lots and rates. Live availability is probably not public, so "lack of spots" is a labeled estimate.
- Live traffic speeds are usually paid. If no free source, use typical speeds by hour and label them "estimated."
- TTC service alerts: parsed from the official GTFS-RT alerts feed (`backend/ingest.py`), not scraped -- real cause/effect/text, stored in Tiger Data's `ttc_alerts`.
- Stretch: pages with no API (Green P rates) read with Steel's scrape, extracted by Gemini into structured fields, cached with source and timestamp.

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

## Commands

- Local all: `powershell -File start.ps1` (or `-Prod`)
- Backend: `.venv\Scripts\python -m uvicorn backend.main:app --port 8000`
- Dashboard: `cd dashboard && npm run dev`
- Bots: `python -m bots.run --prompt "..."`
- Tests: `pytest`
- Eval: `python tests/eval.py`
- Ingest loop: `python -m backend.ingest --loop 90`

## Hosting (public URLs)

- Dashboard: Vercel (project root = `dashboard/`). Set `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_BOT_TOKEN` in the Vercel project env.
- Backend: Railway (repo root, `railway.toml` start command). Set `DATABASE_URL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `BOT_TOKEN`, `CORS_ORIGINS` (your Vercel origin), `STEEL_API_KEY`, `BROWSER_BACKEND`, `INGEST_EVERY_S`, `RATE_LIMIT_PER_MIN`, `TOMTOM_API_KEY` (never stored, see Reliability above), `ROAD_RESTRICTIONS_ENABLED` (optional, default off) in Railway. Never commit secrets.
- Shared bot token: same random value in Railway `BOT_TOKEN` and Vercel `NEXT_PUBLIC_BOT_TOKEN`. Leave both empty for local (auth gate off).
- CORS: production `CORS_ORIGINS=https://<your-vercel-app>.vercel.app`. Local defaults to `http://localhost:3000` when unset.
- Confirm: open the Vercel site, Live panel should load; `GET <backend>/health` returns ok.

## Definition of done

- One typed prompt produces four visibly different routes in about 60 seconds or less, with cost, time, emissions, transfers, and reliability for each.
- The 2x2 grid shows each bot working live through Steel.
- Tiger Data powers the reliability score and at least one dashboard chart.
- Eval table exists with real numbers.
- Replay mode works with no internet.

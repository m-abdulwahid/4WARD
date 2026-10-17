# 4WARD

Toronto trip planner. A user types a trip in plain language (constraints, deadline, bags, no Presto card, etc). Four Playwright bots, each controlled by Gemini and each chasing a different objective, operate our own map dashboard in parallel and each builds a route. The user sees all four routes side by side and picks one.

Hackathon project. Scope is Toronto only. Priority is a demo that works every time, then polish.

## Architecture

```
User prompt
   |
   v
Gemini (parse prompt -> structured constraints JSON)
   |
   v
FastAPI orchestrator --asyncio.gather--> 4 Playwright bots (own browser context each)
                                              |  Gemini picks the next UI action per bot
                                              v
                                   4WARD dashboard (Next.js map UI)
                                              |
                                              v
                              FastAPI data API (routing, fares, bikes, parking, emissions)
                                              |
                              Tiger Data (Postgres + TimescaleDB)
```

- Dashboard: Next.js + Tailwind + MapLibre GL (free, no token). Mapbox is fine if we already have a key.
- Backend: Python 3.11, FastAPI, playwright (async API), google-genai SDK.
- Database: Tiger Data (Postgres + TimescaleDB). Hypertables for feed snapshots, continuous aggregates for dashboard charts.
- Live view: stream each bot's viewport to the UI (screenshots over WebSocket, about 5 FPS) in a 2x2 grid, plus the shared map with all four routes drawn.

## The four bots

Each bot has a profile (objective weights) and a status (SCANNING, ROUTING, DONE, FAILED). Every bot gets the same constraints JSON.

1. Cheap: minimizes total cost (fares, Presto rules, parking, gas, bike fees).
2. Fastest: minimizes door to door time.
3. Green: minimizes emissions (kg CO2e), prefers transit, walking, e-bike.
4. Best overall: weighted blend of the above plus transfers, walking distance, and bag friendliness. (Idea to consider: make this "Most reliable" using stored delay history in Tiger Data.)

## How bots use the dashboard

- The dashboard has real controls: origin and destination search, mode toggles, waypoint drag, constraint toggles (bags, no Presto, avoid stairs), a "Compute route" button.
- Every interactive element has a stable `data-testid`. Bots use these plus the accessibility tree. Do not make bots depend on pixel positions.
- Gemini receives: constraints, the bot's objective, the current page state (accessibility snapshot, plus screenshot only if needed). It returns ONE structured action (click, type, drag, wait, finish) as JSON. Validate the JSON before executing.
- Routing math (distance, time, fare, emissions) comes from the backend API, not from Gemini. Gemini decides what to try; the backend computes the numbers.
- Cap each bot at a max step count and a timeout. A failed bot must not block the others.

## Data sources (verify each one is still available and check terms before building on it)

- TTC and GO schedules: GTFS static. Real-time: GTFS-Realtime feeds.
- Bike Share Toronto: GBFS feed (station status, bikes and e-bikes available).
- Toronto Open Data: road restrictions and closures, cycling network, parking information.
- Green P: lot locations and rates (live availability probably not public, so "lack of spots" must be a labeled estimate).
- Routing engine: OpenTripPlanner, Valhalla, or OSRM on OpenStreetMap data. Pick whichever runs fastest on a laptop.
- Live road traffic speeds: usually paid. If we have no free source, use historical typical speeds by hour and label it "estimated".
- Gas cost and emissions: explicit assumptions in one config file (fuel price, L/100km, g CO2e per km by mode), shown in the UI.

## Rules

- Every number in the UI has a source and timestamp, or is labeled "estimated" or "simulated". Never present made up data as real.
- Fare logic (Presto, cash fare, transfer window, fare capping) is deterministic code with unit tests. Not an LLM.
- Pathfinding is a real routing engine, not an LLM.
- Secrets live in `.env` (never committed). Keep a `.env.example`.
- Cache feed data locally so the demo works with no internet ("replay mode" using a recorded day of data).
- Keep the UI accessible: keyboard navigable, readable contrast, labels on controls. Do not walk across private property or "grass" routes; limit shortcuts to mapped pedestrian paths, parks, and plazas.
- Small commits, one feature per commit.

## Repo layout (proposed)

```
4ward/
  CLAUDE.md
  KICKOFF.md
  .env.example
  backend/        FastAPI app, routing, fares, feeds, bot orchestrator
  bots/           Playwright runner, Gemini action loop, bot profiles
  dashboard/      Next.js map UI
  db/             schema.sql, continuous aggregate definitions
  data/           cached feeds, recorded replay data
  tests/          fare tests, routing tests, bot smoke tests
```

## Commands

- Start everything (backend :8000 + dashboard :3000, background): `powershell -File start.ps1` (`-Prod` for a production build)
- Backend only: `.venv\Scripts\python -m uvicorn backend.main:app --port 8000` (no --reload: Playwright needs the Proactor loop on Windows)
- Dashboard only: `cd dashboard && npm run dev`
- Bots (CLI): `python -m bots.run --prompt "..." --verbose`; five-prompt dry run: `python -m bots.e2e`
- Ingest feeds: `python -m backend.ingest --once | --loop 30 | --record | --replay | --seed-sim 48`
- DB: `python -m backend.db --init` (Tiger Data schema from db/schema.sql)
- Tests: `pytest`
- Next.js here is v16 with breaking changes: read `dashboard/node_modules/next/dist/docs/` before changing Next APIs.

## Definition of done for the demo

- One typed prompt produces four visibly different routes in under about 60 seconds, shown side by side with cost, time, emissions, and transfers.
- The 2x2 bot view shows each bot working live.
- Tiger Data powers at least one real chart from stored feed history.
- Works offline in replay mode.

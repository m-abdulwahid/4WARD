# 4WARD

**Four AI agents plan your Toronto and GTA trip in parallel, each chasing a different goal**

Multi-agent browser automation, real multi-agency transit routing, and reliability scoring from Tiger Data

---

## Overview

Most map apps give you one answer. Getting across the GTA means juggling cost, time, bags, and whether you'll make your transfer, and no single route covers all of that.

That's why we built 4WARD: you describe a trip in plain language, and four AI agents, each running in its own cloud browser, plan it at the same time through our map dashboard. Each agent chases a different goal (cheapest, fastest, greenest, most reliable), so you see the tradeoffs side by side and choose for yourself.

4WARD combines a deterministic routing engine, Playwright browser agents on Steel, Gemini function calling, and a TimescaleDB delay history on Tiger Data. Gemini decides what to try. It never decides what the answer is.

## Links

- Try it here: [https://dashboard-sigma-nine-25.vercel.app/] 
- Devpost: [https://devpost.com/software/4ward-ajlg51]

## Features

- Four parallel agents: **Economic**, **Efficient**, **Environmental**, and **Most reliable**
- Every agent runs in its own isolated Steel cloud browser, and you can watch each one work live
- Real multi-agency transit routing over one graph built from TTC, GO Transit, York Region Transit, Durham Region Transit, and MiWay GTFS feeds (490 directional lines)
- Presto vs cash fares, including the 2 hour transfer window, with GO's distance-based fare kept separate from TTC's flat fare
- Driving and park and ride: gas or EV cost, estimated parking cost, and a search over real nearby subway and GO stations
- Constraint filter for bags, no Presto card, EV, and bike or e-bike ownership
- Pareto badge on routes that nothing beats on time, cost, CO2, transfers, and walking all at once
- Reliability from stored TTC delay history in Tiger Data (TimescaleDB), with confidence that grows as history builds
- Live TomTom road conditions adjust road legs (live only, not stored)
- Interactive map with a toggle per route, mode-colored legs, and a step-by-step timeline
- "Watch the trip unfold": a synced timelapse of all four routes departing together
- Offline replay mode with cached routing and a SQLite fallback

## Architecture

### Trip Planning Pipeline

Trip in Plain Language → Gemini Constraint Parsing → 4 Parallel Agents (Steel + Playwright) → Routing Engine → Reconciler Picks Each Agent's Winner → Dashboard

### Routing Engine

GTFS Graph (5 agencies) → Dijkstra Search → OSRM Walk/Bike/Drive Legs → Fares → Emissions → Parking and Driving Cost → Constraint Filter → Pareto Marking

Every number the engine returns (time, fare, emissions, reliability) comes from deterministic code, never from an LLM.

### Reliability and Data

TTC Delay Collector → Tiger Data Hypertable → Continuous Aggregate (route, stop, hour, day of week) → Reliability Score → Agent Ranking and On-Time Badge

The Most reliable agent optimizes on-time probability directly from this history. Other agents use it as a weighted signal, and its influence ramps up as matching history builds, capped at two weeks.

### Agent Orchestration

Parsed Constraints → Spawn 4 Steel Sessions → Each Agent Operates the Dashboard (Gemini function calling) → Engine Returns Candidates → Shared Reconciler Ranks per Objective → Results Stream to the UI

## Tech Stack

| Category | Technologies |
|---|---|
| AI & Agents | Google Gemini (function calling, trip parsing), Playwright over Chrome DevTools Protocol, Steel cloud browsers |
| Backend | Python, FastAPI |
| Frontend | Next.js, React, Tailwind CSS, MapLibre |
| Database | Tiger Data (PostgreSQL + TimescaleDB), SQLite fallback |
| Routing & Data | GTFS, GTFS-Realtime, Bike Share Toronto GBFS, OpenStreetMap with OSRM, TomTom Traffic API |
| Hosting | Vercel, [add backend host] |

## How It Works

1. User types a trip in plain language or clicks an example.
2. Gemini parses it into structured constraints (origin, destination, bags, deadline, car, Presto status).
3. The orchestrator opens four Steel cloud browsers, one per agent.
4. Each agent operates the dashboard like a person would, with Gemini picking the next action.
5. The routing engine returns real computed candidates for the trip.
6. A shared deterministic reconciler picks each agent's winner for its own objective.
7. All four routes appear on one map with time, cost, CO2, transfers, and on-time score.
8. User toggles routes, picks one, and gets a step-by-step timeline.
9. "Watch the trip unfold" plays all four routes departing together.

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+
- API keys and services (see Environment Variables below):
  - Steel: cloud browser sessions for the agents
  - Google Gemini: trip parsing and agent actions
  - Tiger Data: Postgres with TimescaleDB for delay history
  - TomTom (optional): live road conditions

### Setup

```bash
# 1. Clone the repository
git clone [repo url]
cd 4ward

# 2. Backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in your keys (see below)
python -m backend.db --init      # creates tables, policies, and the aggregate
python -m uvicorn backend.main:app --reload

# 3. Frontend (new terminal)
cd dashboard
npm install
npm run dev

# 4. Data collectors (new terminal)
# [add the exact collector command from the repo]

# 5. Open the dashboard URL printed by npm run dev
```

Check each command against the repo if a path or name differs.

### Environment Variables

The backend reads from `.env` in the project root. Copy the example and fill in your keys:

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | Tiger Data (Postgres) connection string |
| `STEEL_API_KEY` | Yes, for cloud agents | Steel cloud browser sessions |
| `GEMINI_API_KEY` | Yes | Gemini API for trip parsing and agent actions |
| `TOMTOM_API_KEY` | No | Live traffic readings |
| `BROWSER_BACKEND` | No | `steel` for cloud browsers, `local` for a local Playwright browser |
| `REPLAY` | No | `1` for offline replay mode with the SQLite fallback |

Steel's cloud browsers cannot reach `localhost`. To run the agents on Steel, the dashboard and backend need public URLs (deploy or use a tunnel). Use `BROWSER_BACKEND=local` for local development.

Never commit `.env`. Keep only placeholder values in `.env.example`.

### Tests

```bash
pytest
```

## Known Limits

- **Delay history is short.** We started collecting during the hackathon weekend. The earliest hours were backfilled with simulated data and labelled as simulated in the app.
- **Some numbers are estimates.** Rideshare has no public fare API, and parking cost and availability are estimated. Anything estimated is labelled in the UI.
- **TomTom data is live only.** We could not confirm that TomTom's terms allow storing results, so we don't store them.
- **Coverage.** Toronto, Durham, York, Peel, and Halton. Brampton Transit has no public GTFS feed we could find, so those trips get a labelled estimate.
- Fares and times are for planning. Check the operator before you travel.

## Future Roadmap

- Stop-level bus routing for every agency, including a Brampton Transit feed
- User-set priority weighting without a hidden blended score
- More delay history to replace the early simulated data, and road-speed history if TomTom allows storage
- More cities across Ontario and Canada, since most publish GTFS and GBFS
- Smarter agents with a multi-step search loop to refine routes

## Team

| Member |
|---|
| Iman Panjwani |
| Mahek Abdul-Wahid |
| Jeffrey Wong |

## Acknowledgements

Transit schedule data from TTC, GO Transit (Metrolinx), York Region Transit, Durham Region Transit, and MiWay via their public GTFS feeds. Bike Share Toronto GBFS feed. Map data from OpenStreetMap contributors. Traffic readings from TomTom. Built with Steel, Gemini, and Tiger Data. Check each provider's licence and attribution requirements before publishing.


<img width="1536" height="1024" alt="Designer (1)" src="https://github.com/user-attachments/assets/c9a8926e-70a0-4bb8-a36e-030290944280" />

# 4WARD

Type a Toronto trip in plain language. Four Playwright bots, each driven by Gemini and each chasing a different
objective, operate our own map dashboard in parallel and each build a route. You see all four side by side and pick one.

| Bot (shown as) | Internal key | Goal |
|---|---|---|
| Economic | `cheap` | Lowest total cost: fares with Presto rules, parking, gas, bike fees |
| Efficient | `fastest` | Shortest door to door time |
| Environmental | `green` | Lowest kg CO2e |
| Reliable | `reliable` | Highest chance of arriving by the deadline (Tiger Data delay history) |

Internal keys are used by the backend, the bots and every `data-testid` (`bot-cheap`, `choose-fastest`, ...). Only the
display names changed; never rename the keys.

## Run it

```powershell
# one-time
python -m venv .venv ; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
cd dashboard ; npm install ; cd ..
copy .env.example .env      # then add keys (all optional, see below)

# every time (starts backend :8000 and dashboard :3000 in the background; logs in data/*.log)
powershell -File start.ps1          # dev server
powershell -File start.ps1 -Prod    # production build: faster/steadier, use this for the demo
```

Open http://localhost:3000, click **Plan my trip**. The planner the bots drive is http://localhost:3000/planner.

CLI (no browser UI): `python -m bots.run --prompt "..." --verbose`. Test: `pytest`. Full dry run: `python -m bots.e2e`.

## Keys (`.env`)

| Variable | Without it |
|---|---|
| `GEMINI_API_KEY` | Bots use a deterministic rule-based brain (same actions, same UI). Set it and each bot is driven by Gemini; any bad/failed Gemini step falls back to the rule brain for that step, so a bot never dies on a model error. `GEMINI_MODEL` defaults to `gemini-2.5-flash`. |
| `DATABASE_URL` | Local SQLite (`data/4ward.db`) with the same hourly rollups. Set it to your Tiger Data service URL, run `python -m backend.db --init`, then `python -m backend.ingest --seed-sim 48` and charts read the continuous aggregates. |
| `REPLAY=1` | (backend) never touch the network for routing/bike data: serves cached results from `data/route_cache.json`, `data/gbfs_snapshot.json`. Map tiles still need internet. |

## What is real vs estimated

Shown in the UI and in `backend/config.py` (one file of assumptions).

- Real: TTC subway/LRT/streetcar stop sequences and inter-stop times (GTFS static), street walk/bike/drive legs and
  distances (OpenStreetMap routing), Bike Share Toronto availability (GBFS), TTC trip/alert feeds (GTFS-RT) for history.
- Estimated (labelled): fares and prices (verify before demo), transit headways, bus legs (no stop-level routing),
  traffic (typical hour-of-day multiplier, not live), parking cost and spot probability, rideshare fare, emissions factors.
- Simulated (labelled): `--seed-sim` backfills history so charts are populated; the chart footer says how many samples are simulated.

## Layout

```
backend/   FastAPI: /route, /run (+ /ws/run/{id}), /insights/*, fares, transit router, OSM legs, db, ingest
bots/      Playwright runner, Gemini + rule brains, profiles/scoring, prompt parser, orchestrator, e2e
dashboard/ Next.js: / (prompt + 2x2 live bots + shared map + comparison + Tiger Data charts), /planner (what bots operate)
db/        schema.sql (hypertables + continuous aggregates for Tiger Data)
data/      transit_network.json (GTFS-derived), highways.json (OSM controlled-access highways and exits, rebuild with
           `python -m backend.build_highways`), route/geocode caches, replay snapshot
tests/     fares (hand-checked), scoring, prompt parsing, brain fallback
```

## Morning checklist

1. Add `GEMINI_API_KEY` to `.env`, restart (`start.ps1 -Prod`), run `python -m bots.run --prompt "..." --verbose` and
   check each result says `brain=gemini` and `gemini_calls` is small (2-3 per bot).
2. Create the Tiger Data service, put its URL in `DATABASE_URL`, `python -m backend.db --init`,
   `python -m backend.ingest --seed-sim 48 --once`, reload the dashboard: the chart footer should say "Tiger Data continuous aggregates".
3. Verify fare constants in `backend/config.py` against ttc.ca / Bike Share Toronto.

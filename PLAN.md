# 4WARD build plan

This file replaces KICKOFF.md and STEEL.md. Delete those two. CLAUDE.md is the project brief, this file is the order of work.

## Before you open Claude Code

1. Install Python 3.11+, Node 20+, Git, Claude Code.
2. Tiger Cloud: sign up and start the 30 day Performance trial (no credit card). Create ONE service, copy the connection string into `.env` as `DATABASE_URL`. Keep the schema small enough to fit the free tier (750 MB) as a fallback. Run a `pg_dump` backup before the trial ends and before the demo.
3. Steel: create an account and key at app.steel.dev, put it in `.env` as `STEEL_API_KEY`. Check the free tier concurrent session limit. You need at least 4 plus a spare.
4. Gemini: key from Google AI Studio, `.env` as `GEMINI_API_KEY`.
5. Install the Steel CLI and skills so Claude Code knows correct Steel patterns: `curl -sSf https://setup.steel.dev | sh`, then see https://docs.steel.dev/overview/skills
6. `mkdir 4ward && cd 4ward && git init`, put CLAUDE.md and PLAN.md in the root, create `.env`, add `.env` to `.gitignore`.
7. Pick hosting now: backend and orchestrator on Vultr (or Railway), dashboard on Vercel. Steel cannot reach localhost.
8. Use plan mode for the first prompt of each phase, then let Claude Code build.

## Priorities

- Must: phases 1 to 7. This is the demo.
- Should: phase 8 (eval numbers and demo prep), human takeover.
- Stretch: ward reliability view, scraping Green P and TTC alerts, Computer Use comparison, extra constraints.

## Phases and paste-in prompts

Each phase ends with something you can run. Do not start the next one until the current one works. Commit after each.

### Phase 0: teach Claude Code the docs
> Fetch https://docs.steel.dev/llms.txt and the markdown versions of these pages: sessions-api/quickstart, sessions-api/session-lifecycle, sessions-api/embed-sessions/live-sessions, sessions-api/human-in-the-loop, agent-traces/overview, and the Gemini Computer Use cookbook. Then fetch the current Tiger Data docs for creating hypertables and continuous aggregates. Summarize in 20 lines the exact Python and SQL we need. Do not write code yet.

### Phase 1: skeleton, database, and collector (start collecting data immediately)
> Read CLAUDE.md. Scaffold the repo: FastAPI backend with /health, Next.js dashboard with a full screen MapLibre map on Toronto, .env.example, .gitignore. Create db/schema.sql with hypertables for TTC delay observations and Bike Share station status, regular tables for bot_runs and bot_steps, and a continuous aggregate for delay stats by route, hour, and day of week. Write collectors that pull Bike Share GBFS and TTC GTFS-Realtime every 90 seconds and insert rows. Verify every feed URL first and tell me about anything that needs a key or is unavailable. Also save a recorded sample into data/ for replay mode.

### Phase 2: deploy early
> Deploy the backend and dashboard to public URLs (Vultr or Railway for the backend, Vercel for the dashboard). Add a shared bot token header, CORS limits, and basic rate limiting on the API. Document the URLs and env vars in CLAUDE.md. Confirm a Steel session can open the deployed dashboard.

### Phase 3: routing, fares, emissions
> Add POST /route: origin, destination, modes, depart or arrive time, constraints. Use OpenTripPlanner (or Valhalla) for candidate itineraries and explain which you picked and why. Build our layers on top: Presto fare engine (transfer window, fare capping, cash vs Presto), emissions model, constraint filter for bags, no Presto, driving cost, and Bike Share, and Pareto ranking. Gas and emissions assumptions go in one config file. Write pytest tests for fares against 10 hand-checked trips.

### Phase 4: the dashboard
> Build the dashboard UI: origin and destination search, mode toggles, constraint toggles, deadline picker, waypoint drag, Compute button, and a results panel comparing routes by cost, time, emissions, transfers, and reliability. Draw routes on the map with one color per bot. Give every control a stable data-testid. Add a slider for the "best overall" weighting.

### Phase 5: Steel hello world and one bot
> Create bots/steel_client.py: a context manager that creates a Steel session, connects Playwright over CDP, yields the page, and always releases the session in finally. Add the BROWSER_BACKEND env var (steel or local). Then build the Economic bot: loop with Gemini function calling (click, type, drag_waypoint, read_results, finish), validate each call, log every step to bot_steps, cap at 25 steps and 90 seconds. It must finish a real trip on the deployed dashboard and print its Steel session viewer URL.

### Phase 6: four bots, coordination, live view
> Add the Efficient, Environmental, and Most reliable profiles. Run all four with asyncio.gather, each in its own Steel session. Write results to bot_runs. Add the diversity rule (rerun the lower priority bot with "must differ from route X in at least one leg" if two routes match) and a critic pass that checks each route against the constraints using engine output only. Add a Gemini step that parses the user's plain language prompt into constraints JSON. Embed each bot's Steel live view in a 2x2 grid and link each trace when the bot finishes.

### Phase 6b: ride-hail leg and fare scouts (this is where the "Uber part" goes)
Before this phase, do one manual check yourself: in a logged-out browser, see whether a public ride price estimator page works without logging in, and read the site's terms about automated access. If it needs a login or forbids automation, use `sim` only.

> Add a ride-hail provider layer in backend/ridehail/ with three backends: sim, steel_live, cache, chosen by RIDEHAIL_PROVIDER. Build the sim as a page at /ridesim in the dashboard called RideSim: pickup and drop-off search, a Get price button, a 60 second quote countdown, a surge multiplier by pickup zone and time, and a Trigger storm button. Show a SIMULATED banner on every page and do not use any real company's name, logo, or styling. Fare = base + per km + per minute times surge. Store every quote in a fare_quotes hypertable in Tiger Data.

> Add fare scouts: for a trip, pick up to 4 candidate transit hubs near the origin, then open one Steel session per hub in parallel (asyncio.gather) and have each scout use Gemini function calling to type the pickup and drop-off into RideSim and read the quote. Always release sessions in finally. Write results to fare_quotes with provider and simulated flag. Respect the 60 second expiry and cache by (pickup, dropoff, 2 minute window).

> Wire the provider layer into /route: allow itineraries with a ride-hail first or last leg, price that leg from fare_quotes, add its emissions, and show a SIMULATED or LIVE label on its cost in the results panel. Add a test that a short ride plus train beats a direct ride on cost when the sim surge is high.

> Optional, only if the manual check passed: implement steel_live as a read only scout of a public price estimator page. No login, no booking, at most one request per pair per minute, cache results, and stop on any block or captcha instead of trying to get around it.

### Phase 7: reliability engine
> In Tiger Data, use the continuous aggregate to score each route's reliability: estimate the chance of making each transfer from the delay distribution, then replay historical days to get "made the deadline on X% of past weekdays." Expose it in /route. Add one dashboard chart (delay by route and hour) powered by the continuous aggregate. Backfill from historical TTC delay data if it is available.

### Phase 8: evaluation and demo prep
> Write tests/eval.py: run 20 saved prompts and report success rate, steps per bot, wall time, constraint violations, and fare error against the 10 hand-checked trips. Compare 4 parallel bots vs 1 bot run 4 times in sequence. Output a markdown table I can paste into the pitch. Then add replay mode (recorded feed data and a recorded run) so the demo works offline, and loading and failure states for each bot.

### Phase 9: stretch
- Human takeover: let the user take control of a bot's Steel browser mid run (see Steel's human-in-the-loop docs).
- Ward view: map of where service is least reliable, from the same delay data.
- Scrape pages with no API (Green P rates, TTC alerts) with Steel, extract with Gemini, cache with source and timestamp. Check each site's terms first.
- Gemini Computer Use comparison against the structured loop.

## Tiger Data starting schema (check current Tiger Data docs for exact syntax)

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE ttc_delay_obs (
  ts timestamptz NOT NULL,
  route_id text NOT NULL,
  stop_id text,
  trip_id text,
  delay_sec integer
);
SELECT create_hypertable('ttc_delay_obs', 'ts');

CREATE TABLE bikeshare_obs (
  ts timestamptz NOT NULL,
  station_id text NOT NULL,
  bikes_available integer,
  ebikes_available integer,
  docks_available integer
);
SELECT create_hypertable('bikeshare_obs', 'ts');

CREATE MATERIALIZED VIEW delay_by_route_hour
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', ts) AS bucket,
       route_id,
       avg(delay_sec) AS avg_delay,
       max(delay_sec) AS max_delay,
       count(*) AS n,
       count(*) FILTER (WHERE delay_sec > 300)::float / count(*) AS share_over_5min
FROM ttc_delay_obs
GROUP BY bucket, route_id;
```

Free tier sizing: sample every 60 to 120 seconds, keep only these columns, enable compression after 1 day, and drop raw rows after 30 days. If the collector writes faster than that, storage will grow quickly.

## Demo script (3 minutes)

1. (20s) The problem: Google Maps gives you one ETA. It does not tell you if you will make your transfer.
2. (30s) Type a real trip with constraints: "UTSC to King and Spadina by 8:30, two suitcases, no Presto."
3. (60s) Four Steel browsers work in parallel in the 2x2 grid, routes appear on the map one by one.
4. (30s) Compare the four routes: cost, time, CO2, reliability. Show "made the deadline on X% of past weekdays" from Tiger Data.
5. (20s) Show the eval table: 4 parallel bots took X seconds vs Y for sequential.
6. (20s) Show a Steel trace and, if built, take over a bot's browser live.
7. Close: say plainly what is estimated or replayed, and what you would build next.

Have the replay mode ready as a backup, and a screen recording of one good run.

## Cut list

- Separate message bus or Redis: use asyncio and a Postgres table.
- Mapbox: use MapLibre.
- Custom screenshot streaming: use Steel's embed.
- Anything in the pitch not backed by a number in the eval table.

## When Claude Code gets stuck

- Paste the exact error, not a summary.
- Ask for its plan before it changes many files.
- If a bot is flaky, make the UI friendlier to bots (clearer test ids, fewer animations) instead of lengthening the prompt.
- Commit after every working phase so you can roll back.

## Questions to settle with your team

- Hackathon deadline and team size? That decides whether the Should and Stretch items are realistic.
- Which routing engine can you run on a laptop or Vultr box in the time you have?
- Which live traffic source, if any, is free? If none, agree to label it "estimated."
- Who owns what: dashboard, backend and data, bots, pitch and demo.

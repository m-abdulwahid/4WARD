# 4WARD kickoff

## Before you open Claude Code

1. Install: Python 3.11+, Node 20+, Git, Claude Code.
2. Accounts and keys: Gemini API key (Google AI Studio), Tiger Data account (free tier, note the connection string), optional Mapbox token.
3. `mkdir 4ward && cd 4ward && git init`, then put `CLAUDE.md` in the root.
4. Create `.env` with `GEMINI_API_KEY=` and `DATABASE_URL=`. Add `.env` to `.gitignore`.
5. Start Claude Code in the folder and run `/init` only if it offers to; do not let it overwrite CLAUDE.md.
6. Tip: use plan mode for the first prompt of each phase, then let it build.

## Prompts to paste, in order

Each phase should end with something you can run. Do not start the next phase until the current one works.

### Phase 1: skeleton
> Read CLAUDE.md. Scaffold the repo layout: FastAPI backend with a /health route, Next.js dashboard with a full-screen MapLibre map centered on Toronto, and a Playwright script that opens the dashboard and takes a screenshot. Add .env.example, .gitignore, and fill in the Commands section of CLAUDE.md. Make sure all three run.

### Phase 2: data and database
> Set up the Tiger Data schema in db/schema.sql: hypertables for TTC vehicle delays and Bike Share station status, and a continuous aggregate for average delay per route per hour. Write ingestion scripts that pull the Bike Share GBFS feed and TTC GTFS-Realtime and insert snapshots every 30 seconds. Also save a recorded sample to data/ for replay mode. Before building, verify the feed URLs and tell me anything that requires a key or is unavailable.

### Phase 3: routing and fares
> Add a routing service behind a FastAPI endpoint: POST /route takes origin, destination, mode set, depart or arrive time, and a constraints object, and returns a route with legs, time, cost, emissions, and transfers. Use [OpenTripPlanner / Valhalla / OSRM, pick the simplest to run locally and explain why]. Implement fare logic (Presto and cash, transfer window, fare cap) as deterministic code with pytest tests. Put gas price and emissions assumptions in one config file.

### Phase 4: the dashboard
> Build the dashboard UI: origin and destination search, mode toggles, constraint toggles (bags, no Presto, avoid stairs, deadline), a Compute route button, and route drawing on the map with color per route. Give every control a stable data-testid. Add a results panel comparing routes by cost, time, emissions, and transfers.

### Phase 5: one bot end to end
> Build the bot runner for the Cheap bot only. It opens the dashboard in its own Playwright context, receives a constraints JSON, and loops: send the accessibility snapshot and objective to Gemini, get ONE JSON action, validate it, execute it, repeat until finish. Add a step cap and timeout, and log every step. Make it complete a real trip on the dashboard.

### Phase 6: four bots in parallel
> Add the other three profiles (Fastest, Green, Best overall) and run all four with asyncio.gather, each in a separate browser context. Add a Gemini step that parses the user's plain-language prompt into the constraints JSON. Stream each bot's viewport and status to the dashboard over WebSocket as a 2x2 grid, and draw all four routes on the shared map.

### Phase 7: Tiger Data chart and polish
> Add a dashboard chart powered by the continuous aggregate (delay by route and hour, or bike availability over time). Add replay mode using the recorded data so the demo works offline. Add loading and failure states for each bot.

### Phase 8: testing before the demo
> Write tests: fare calculations against 10 hand-checked trips, routing smoke tests, and a script that runs 5 different prompts end to end and reports time taken and failures. Then do a demo dry run checklist.

## When Claude Code gets stuck

- Paste the exact error, not a summary.
- Ask it to explain its plan before it changes many files.
- If a bot is flaky, make the UI more bot friendly (clearer test ids, fewer animations) instead of making the prompt longer.
- Commit after every working phase so you can roll back.

## Questions to settle with your team before Phase 3

- Which routing engine can you realistically run on a laptop in the time you have?
- Which live traffic source, if any, is free enough to use? If none, agree to label it "estimated".
- Who owns what: dashboard, backend and data, bots, pitch and demo.

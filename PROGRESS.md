# PROGRESS

Deadline: **16 Oct 2026, 23:59 IST**. Last updated: 15 Oct 2026.

## Done
- [x] 0 Setup: repo, Apache-2.0 LICENSE, .gitignore, .env.example, structure, tests folder. Name "SaafRoute" checked (no conflicting product found in a web search).
- [x] 1 Model spike: candidates researched and benchmarked through the API on 41 labelled images. See `docs/model-selection.md`.
- [x] 2 Photo → JSON: Pydantic schema, prompt, extraction, one retry with error feedback, needs-review fallback, tests.
- [x] 3 Tickets: Gemini drafting in en/hi/pa with model fallback chain and offline templates; approve/reject/edit/redraft in API and UI.
- [x] 4 Hotspot score: deterministic formula, documented, tested, breakdown shown in UI.
- [x] 5 Routing: synthetic fleet, OR-Tools CVRP, fair naive baseline (same stops, extra trips), tests.
- [x] 6 Dashboard: upload, ticket queue, approve, map, routes, simulation-labelled savings card.
- [x] 7 Hardening: upload type/size limits, rate limit, error/empty states, offline replay mode, sector grid when tiles fail.
- [x] 8 Submission assets: README, model-selection, privacy, demo script, deck outline, 150-word description, HANDOFF, screenshots. Clean-clone test passed.

## Decisions and fallbacks (with reasons)
- **No local models** (user instruction): the C: drive filled up while pulling an Ollama model. All models are hosted via the Gemini API with the user's existing key.
- **Vision:** Gemma 4 (open weights, Apache-2.0) via the Gemini API. The user asked about 31B; the benchmark shows 31B returning server errors on most requests, so **26B-A4B is primary and 31B is the fallback** (31B answered 2 of 29; 26B answered 34 of 38). Benchmark stopped early for the deadline.
- **Drafting:** gemini-3.5-flash allows only 20 free requests/day (exhausted during the benchmark), so the chain is 3.5-flash-lite → 3.1-flash-lite → 2.5-flash, then templates.
- **Distances:** haversine × 1.3 instead of an OSM road network (plan's allowed fallback; keeps setup to one command).
- **One-command run:** `python run_demo.py` (Docker isn't installed on this machine, so there is no tested Docker setup; `make` isn't available either).
- **Benchmark images:** Wikimedia Commons, open licences only; images with people or readable plates excluded (rule: no real people's photos).

## Open questions for the user
1. Team name(s), contact details and the final submission (HackCulture) are yours to supply. Nothing has been submitted or pushed.
2. Please spot-check `data/bench/labels.csv` (41 rows), since the labels were made by the AI assistant.
3. Please have a native speaker check the Hindi/Punjabi **templates** in `saafroute/drafting.py` (Gemini drafts are generated live).
4. Free-tier quota: for the live demo, consider enabling billing on the Gemini project or bringing a second key; otherwise the app falls back to templates / needs-review, which still works but is less impressive.
5. Public GitHub repo: create it and push when you're ready (I have not pushed anywhere).

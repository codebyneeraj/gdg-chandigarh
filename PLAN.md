# PLAN.md: Chandigarh Waste Hotspot and Route Planner

Working title: pick a short, memorable name in step 0 (check it isn't a known brand).

You are building a hackathon entry for **Code for Communities Chandigarh** (GDG Cloud Chandigarh). Theme: **Waste & Sustainability** ("smart waste collection routes, environmental monitoring, and resource optimisation for a greener Chandigarh"). We also target the **Best Open-Source AI Project** bonus.

**Hard deadline: submission closes 16 Oct 2026, 11:59 PM IST.** Plan for roughly 24 working hours. A working end-to-end demo beats a long feature list. Cut scope before you cut quality.

## 1. What we are building

A web app and API that turns citizen waste photos into prioritised, routed collection work.

Pipeline: **report -> understand photo -> structured ticket -> hotspot score -> route optimisation -> map dashboard**

1. **Report.** A citizen uploads a photo plus a Chandigarh sector (optional GPS pin).
2. **Understand the photo.** A vision-capable model returns strict JSON: waste type, severity 1-5, overflowing bin vs illegal dump, confidence, short reason.
3. **Draft the ticket.** An LLM writes a short ticket in English, Hindi or Punjabi. A human official approves it. The AI never dispatches anything on its own.
4. **Score hotspots.** Deterministic, explainable score from severity, report age, repeat reports nearby, and sector.
5. **Plan routes.** A capacitated vehicle routing solver assigns open tickets to trucks and orders the stops.
6. **Dashboard.** A map of Chandigarh sectors shows hotspots, tickets and proposed routes, plus a comparison of total distance vs a naive fixed route (simulation, clearly labelled).

## 2. Judging criteria (design every decision against these)

| Weight | Criterion |
|---|---|
| 25% | AI/technical execution: Google AI must do meaningful work and the prototype must run end-to-end |
| 20% | Problem-solution fit |
| 20% | Reach across India (scales beyond one city) |
| 20% | Deployability (pilotable in weeks) |
| 15% | Impact potential |
| Bonus | Open-source AI: open-weight model is important to how it works, public GitHub repo, open licence, reusable by others |

Rules that matter:
- Google AI must play a meaningful role.
- The open-weight model must be central, not decorative, to qualify for the bonus.
- The repo must be public with an OSI-approved licence (default to Apache-2.0 unless you find a reason otherwise).

## 3. Model selection: your decision, backed by evidence

Do not assume models. Choose them.

- **Research first.** Use web search to find the current best options for: (a) a vision-capable **open-weight** model that can run on available hardware, and (b) a **Google API model** for multilingual drafting and any reasoning. Check licences, size, hardware needs, and availability as of today (Oct 2026).
- **Check the environment.** Run `nproc`, `free -h`, `nvidia-smi` (if any), and measure what actually runs here. The demo team may only have a laptop, so prefer something that runs on modest hardware or via a free hosted endpoint, and document the fallback.
- **Benchmark.** Build a small labelled set (30-50 images, see section 5). Run each candidate on it and record accuracy on waste type and severity, JSON validity rate, latency per image, and cost. Pick the winner by data and write the comparison to `docs/model-selection.md`.
- **Make models swappable.** Put each behind a small interface and a config file so they can be changed without code changes.
- **Open-weight must carry real work.** The photo-understanding step should use the open-weight model. The Google API model should do something it is genuinely good at (multilingual drafting, structured reasoning), not a token call.
- **Failure handling.** Validate every model output against a schema. On invalid JSON, retry once, then fall back to "needs human review". Never crash on model output.

## 4. Tech guidance (adjust if you find something better; justify in the README)

- Python backend (FastAPI or similar) with a typed schema (Pydantic).
- Routing: OR-Tools (capacitated VRP) with road distances from OpenStreetMap (or a haversine fallback if the road network is too slow to set up. Say which one you used).
- Frontend: simple and robust (plain HTML plus a map library is fine). Mobile-friendly. It must work on a projector.
- Storage: SQLite. No external services needed to run the demo.
- One-command run (`docker compose up` or `make demo`) and a documented offline-ish mode if the network is down at the venue.
- Keep dependencies lean. Pin versions.

## 5. Data (synthetic and honest)

- No official municipal data is available. Use **synthetic bins, trucks and depots** laid out on the real Chandigarh sector grid (use OpenStreetMap for geography).
- Test images: use only images with a licence that allows reuse (public-domain or permissive datasets, or photos we take ourselves). Record the source and licence of every image in `data/ATTRIBUTION.md`. Do not scrape random web photos.
- **Label everything synthetic as synthetic** in the UI, README and deck. The route-saving figure is a simulation, never a measured municipal result.
- Do not store faces, number plates or personal data. Strip image EXIF/GPS metadata on upload. Document this in `docs/privacy.md`.

## 6. Milestones (check off as you go, commit after each)

0. **Setup (30 min).** Create repo, LICENSE, README skeleton, `.gitignore`, project structure, CI-free simple tests folder. Check the working title. Read the environment limits.
1. **Model spike (2-3 h).** Research, install, benchmark candidates on the labelled set. Decide. Write `docs/model-selection.md`.
2. **Photo -> JSON pipeline (2 h).** Schema, prompts, validation, retry and fallback. Unit tests with recorded sample outputs.
3. **Tickets and language (1-2 h).** Ticket drafting in English, Hindi, Punjabi. Human-approval step in the API and UI.
4. **Hotspot score (1 h).** Explainable formula, documented, with tests. Show the score breakdown in the UI.
5. **Routing (2-3 h).** Synthetic fleet, VRP, naive-baseline comparison, tests for capacity and coverage.
6. **Dashboard (3 h).** Upload flow, ticket list, approve button, sector map, route view, distance-saved card with a "simulation" label.
7. **Hardening (2 h).** Error states, empty states, rate limits, upload size and type limits, works with no network where possible.
8. **Submission assets (3 h).** README with architecture diagram, 2-3 minute demo script and recording notes, PPT outline (reuse the structure in section 9), final licence and attribution check.

If time runs short, cut in this order: Punjabi/Hindi drafting -> road-network distances (use haversine) -> extra UI polish. **Never cut:** the photo -> JSON -> route chain, the tests, the honesty labels, and the open licence.

## 7. Quality bar

- The whole demo flow must work from a clean clone using the README alone. Test this at the end in a fresh directory.
- Write tests for the schema validation, the hotspot score and the routing constraints. Run them before every commit.
- Run the app and look at it. Take screenshots of the main flows with a headless browser and check them for layout problems.
- Never claim accuracy or savings you haven't measured. Report the benchmark numbers as measured, with sample size.
- Keep secrets (API keys) in environment variables, never in the repo. Add `.env.example`.

## 8. Things you must NOT do

- Do not submit anything to HackCulture, post publicly, or push to a public remote without the user's explicit instruction. Prepare everything, then ask.
- Do not use real people's photos or personal data.
- Do not invent statistics about Chandigarh waste. Cite sources for any figure used in the README or deck, or leave it out.
- Do not add automated or spam-style GitHub activity.

## 9. Deliverables

1. Public-ready repo: code, tests, LICENSE, README, `docs/model-selection.md`, `docs/privacy.md`, `data/ATTRIBUTION.md`.
2. Demo script and a short list of screenshots for the video.
3. Deck outline (about 10 slides: problem, why Chandigarh, how it works, where each model fits and why, data honesty, measured results, scalability across India, pilot plan, privacy and human oversight, team and ask).
4. A 150-word project description and a one-line tagline for the registration form.
5. A short `HANDOFF.md` listing what works, what is simulated, known limits, and what the user still has to supply (team names, contact, video, final submission).

## 10. Working agreement

- Start by restating this plan in your own words, listing any assumption you are making, and flagging anything that looks unrealistic for the time left.
- Keep a running `PROGRESS.md` with what is done, what is next, and open questions.
- Commit small and often with clear messages.
- When blocked for more than 15 minutes, take the simplest fallback that keeps the demo working, note it in `PROGRESS.md`, and move on.

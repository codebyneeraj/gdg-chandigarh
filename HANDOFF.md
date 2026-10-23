# HANDOFF

State on 15 Oct 2026. Submission closes **16 Oct 2026, 23:59 IST**.
Nothing has been pushed to a remote, posted or submitted.

## What works (verified)
- End-to-end on a real photo: upload → Gemma 4 JSON (schema-validated) → hotspot score → Gemini ticket in en/hi/pa → official approves → OR-Tools routes on the map.
- Fallbacks: Gemma 4 26B → 31B (26B chosen by benchmark; 31B was mostly unavailable); invalid model output → one retry → "needs human review"; Gemini drafting chain → offline templates; `--offline` mode replays recorded Gemma outputs.
- Upload safeguards: type and size limits (8 MB), image decode check, EXIF/GPS stripped, rate limit (6 per minute per IP), when a face or plate is flagged the sharp photo is kept for the reporter/officials and the public city view serves a blurred copy.
- 41 automated tests (all passing) (schema, pipeline retry/fallback, scoring, routing capacity/coverage/fairness, API flow, drafting fallback).
- Dashboard on desktop, 1280×720 projector and a 390 px phone (no horizontal scroll), checked with headless-browser screenshots.
- `python run_demo.py` from a fresh `git clone` in a new directory: created the venv, installed pinned deps, read the key from `.env`, seeded data, served; 41/41 tests passed there; route numbers identical.

## What is simulated / synthetic
- Truck fleet (3 × 20 units), depot location, and the 24 seeded demo reports: **synthetic**, labelled in the UI.
- Route savings: **simulation** on synthetic data, with OSRM road distances when reachable, else straight-line × 1.3 (the plan note says which).
- Benchmark photos are from Wikimedia Commons (various countries), not Chandigarh.

## Known limits
- **Free-tier Gemini quotas.** gemini-3.5-flash allowed only 20 requests/day; Flash-Lite is used for drafting. Gemma 4 31B was mostly unavailable ("high demand"/500) during testing. **For the live demo, consider enabling billing on the Google AI Studio project, or bring a second key.**
- Vision latency via the hosted API: see `docs/model-selection.md`. Expect tens of seconds per photo on the free tier.
- Benchmark labels were made by the AI assistant (single annotator) and have not been human-checked; n = 41.
- Hindi/Punjabi **templates** (offline fallback) were written by the AI assistant and need a native-speaker check. Live Gemini drafts read well but were not checked by a native speaker either.
- No login/access control for officials. No automatic photo retention policy.
- Face/plate detection is model best-effort and its recall was not measured.
- The Ollama (self-host) adapter is included but not tested in this build (local models were not used on this machine by request).

## What you still need to supply
1. Team name(s), member names, contact email/phone for the registration form.
2. The demo **video**: follow `docs/demo-script.md` (2–3 min). Screenshots are in `docs/screenshots/`.
3. The **deck**: outline in `docs/deck-outline.md` (10 slides). Add Chandigarh waste statistics only with a cited source.
4. Registration text: `docs/submission-text.md` (tagline + 150-word description).
5. **Public GitHub repo**: create it, then `git remote add origin <url>` and `git push -u origin main`. I have not pushed. The `.env` file is git-ignored; double-check no key is committed (`git grep -n "AIza"` should print nothing).
6. Final submission on HackCulture.
7. Optional but recommended: spot-check `data/bench/labels.csv`; have a native speaker review the hi/pa templates in `saafroute/drafting.py`.

## Housekeeping
- During setup an Ollama model download filled the C: drive. The partial download was deleted and C: is back to its earlier free space. A temporary copy of `qwen3.5:9b` on D: was removed at your request. Your original Ollama models on C: are untouched.
- Scratch files (benchmark logs, screenshots work) are in `.tmp/` (git-ignored) and can be deleted.

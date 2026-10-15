# Demo script (2–3 minutes)

**Before recording:** `python run_demo.py --reset` (fresh synthetic data). Browser at 1280×720 or full-screen
on the projector. Type the official's name ("JE Demo") in the Tickets panel once. Keep a test photo ready,
e.g. `data/bench/images/cm017.jpg` (construction rubble) or a photo you took yourself (no people or plates).
Check that the status chips (top right) show the Gemma and Gemini models, not "template".

| Time | Show | Say |
|---|---|---|
| 0:00–0:20 | Dashboard, map of Chandigarh with hotspots | "Chandigarh's collection runs on fixed routes, but waste doesn't appear on a schedule. SaafRoute turns citizen photos into prioritised, routed collection work, with an official in control at every step." |
| 0:20–0:35 | Point at the yellow banner | "Everything you see on the map except the sector geography is synthetic, and route savings are a simulation. We say that on screen." |
| 0:35–1:05 | Upload the rubble photo, Sector 26, Punjabi. Show the seconds counter, then the "What the model saw" box | "The photo goes to Gemma 4, Google's open-weight model. It returns strict JSON: illegal dump, construction debris, severity 4, and why. If the output doesn't validate, it retries once, then sends the photo to a human. It never guesses. GPS and EXIF metadata are stripped." |
| 1:05–1:30 | New ticket in the queue: Punjabi title and body, score breakdown bar | "Gemini drafts the work ticket in Punjabi, Hindi or English, and suggests a tipper because it's rubble. The score is plain arithmetic: severity, age, repeat reports nearby, and whether it's near a hospital or market. Nothing is a black box." |
| 1:30–1:45 | Switch the redraft to हिन्दी, edit a word, click **Approve** | "The AI never dispatches. An official edits and approves." |
| 1:45–2:15 | **Plan routes**; tick **Show naive fixed route** | "OR-Tools assigns approved tickets to three trucks within capacity. Against a naive sector-by-sector route over the same stops, it's about 11% shorter in this simulation." |
| 2:15–2:40 | README / model-selection table (or slide) | "We benchmarked models on 41 openly licensed photos and picked by data. Because Gemma's weights are open, any Indian city can self-host this. Swap the OpenStreetMap wards and the fleet file and it runs anywhere." |
| 2:40–2:50 | Logo / repo URL | "SaafRoute: open source, Apache-2.0." |

**If the network fails during the demo:** restart with `python run_demo.py --offline`. Benchmark photos
still analyse (replayed Gemma outputs), tickets use templates, and routing works fully offline.

## Screenshots / shots for the video
1. `docs/screenshots/dashboard_routes.png`: full dashboard with routes planned (hero image).
2. Upload result box ("What the model saw") right after analysing a photo.
3. A Punjabi ticket in the queue with the score-breakdown bar.
4. Routes with the naive dashed route overlaid, plus the savings card showing the SIMULATION note.
5. `docs/screenshots/mobile.png`: phone view of the report form.
6. Benchmark table from `docs/model-selection.md`.

## Recording notes
- Record at 1280×720 or 1920×1080, browser zoom 110% for readability.
- Do an upload before recording so the API is warm; first calls can take 30–60 s on the free tier.
  If it's slow while recording, cut the wait in editing and say "about 20 seconds" honestly.
- Don't show the `.env` file or the API key.

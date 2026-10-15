# Deck outline (10 slides)

Figures marked ⚠ need a citable source before use, or leave them out. We have **not** invented any
Chandigarh waste statistics.

1. **Title.** SaafRoute: photos to prioritised, routed waste collection. Team name, GDG Cloud Chandigarh, Code for Communities. Tagline.
2. **Problem.** Fixed-schedule collection misses overflowing bins and illegal dumps between rounds. Complaints arrive as unstructured photos and messages, in several languages. Supervisors triage manually.
   ⚠ Add Chandigarh/MC figures only with a source (e.g. Swachh Survekshan rank, MC Chandigarh SWM reports). Otherwise keep it qualitative.
3. **Why Chandigarh.** A planned sector grid makes routing and sector-level reporting natural. It's trilingual (English, Hindi, Punjabi). Real sector geography from OpenStreetMap (55 sectors).
4. **How it works.** Pipeline diagram (README mermaid): photo, then Gemma 4 JSON, then validation, then score, then Gemini ticket, then official approval, then OR-Tools routes, then map.
5. **Where each model fits and why.**
   - Gemma 4 (open weights, Apache-2.0): photo understanding. This is the core judgement, so it uses a model any city can self-host.
   - Gemini Flash-Lite: multilingual ticket drafting (en/hi/pa), about 2 s, cheap. Never sees the photo.
   - OR-Tools: deterministic optimisation. Score: plain arithmetic, fully explainable.
   - Failure handling: schema validation, retry, human review, fallback models, offline templates.
6. **Data honesty.** Synthetic fleet, depot and demo reports, labelled in the UI. Benchmark on 41 openly licensed photos with attribution. Images with people or plates excluded. Route savings are a simulation.
7. **Measured results.** Benchmark table from `docs/model-selection.md` (n = 41, with sample size and caveats). Simulated route comparison (54.9 vs 61.6 km, 10.8%, synthetic). Say plainly what was *not* measured: real-world accuracy, real routes.
8. **Scalability across India.** Waste categories follow the SWM Rules 2016 streams used nationwide. Any city's wards via OSM. More languages by config. Open weights let cities self-host for cost and data sovereignty. Reusable open-source repo.
9. **Pilot plan (weeks, not months).**
   - Week 1–2: one or two sectors, an existing complaint channel, officials use the dashboard in parallel with current practice.
   - Week 3–4: real fleet file and depots, road-network distances (OSRM), measure triage time and route km against the baseline.
   - Then: decide on self-hosting, retention policy and access control.
10. **Privacy and human oversight, team and ask.** EXIF/GPS stripped; blur on faces or plates; anonymous reports; the AI never dispatches. Ask: a pilot sector with MC Chandigarh, anonymised historical complaint data and fleet constraints, GPU/cloud credits for self-hosting. Team names and contact.

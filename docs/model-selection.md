# Model selection

Measured on 15 Oct 2026. Every number below comes from `scripts/benchmark.py`; raw outputs are in
`data/bench/results/*.jsonl` and the summary in `data/bench/results/summary.json`.

## Constraints

- **Open-weight model must do the core work** (photo understanding) for the open-source AI bonus.
- **Google AI must do meaningful work.**
- **No local models on the demo machine:** the user asked us not to run models locally after an Ollama
  download filled the system drive. All models are called through the Gemini API with the user's key.
  (The laptop has an RTX 3050 8 GB, 16 GB RAM and 20 cores; a self-host path via Ollama is included but untested.)

## Candidates (researched 15 Oct 2026)

| Model | Weights | Licence | Role tested | Access |
|---|---|---|---|---|
| Gemma 4 31B (`gemma-4-31b-it`) | open | Apache-2.0 | vision | Gemini API |
| Gemma 4 26B-A4B MoE (`gemma-4-26b-a4b-it`) | open | Apache-2.0 | vision | Gemini API |
| Gemini 3.5 Flash (`gemini-3.5-flash`) | closed | proprietary | vision (reference only) | Gemini API |
| Gemini 3.5 Flash-Lite / 3.1 Flash-Lite / 2.5 Flash | closed | proprietary | ticket drafting | Gemini API |

Gemma 4 moved to Apache-2.0 at its April 2026 release, and all Gemma 4 sizes accept images.

## Method

- **Data:** 41 photos from Wikimedia Commons (CC0, PD, CC BY, CC BY-SA; see `data/ATTRIBUTION.md`):
  12 overflowing bins, 12 illegal dumps, 8 litter, 9 clean. Photos with identifiable people or readable
  number plates were excluded.
- **Labels** (`data/bench/labels.csv`): kind, material (SWM Rules-aligned) and severity 1–5. Made by a
  single annotator, the AI assistant that built the project, by looking at each photo **before** running any
  model. Not yet human-verified. Severity is subjective, so we report exact and ±1 agreement.
- **Procedure:** each model ran alone (no fallback chain) through the production pipeline: same image
  normalisation (1024 px JPEG, metadata stripped), prompt, Pydantic validation and one retry. Temperature 0.
  The three models ran concurrently on the free tier, which inflates latency.
- **Stopped early to meet the deadline:** Gemma 26B completed **38 of 41** images, Gemma 31B **29 of 41**.
  Gemini 3.5 Flash covered all 41 but hit its free-tier limit of **20 requests/day** after 19 answers.

## Results

"API errors" are 429/500/503/timeouts that persisted after the retry, which measures provider availability.
"Answered" accuracy is model quality on the images where the API responded.

| Metric | Gemma 4 26B-A4B (open) | Gemma 4 31B (open) | Gemini 3.5 Flash (closed, reference) |
|---|---|---|---|
| Images attempted (n) | 38 | 29 | 41 |
| Answered | **34** | 2 | 19 |
| API errors after retry | 4 (500) | **27** (500/503) | 22 (429 daily quota) |
| Invalid JSON after retry | **0** | 0 | 0 |
| Kind accuracy, all n | **76.3 %** | 6.9 % | 39.0 % |
| Kind accuracy, answered | 85.3 % (29/34) | 2/2 | 84.2 % (16/19) |
| Material accuracy, answered | **85.3 %** | 1/2 | 73.7 % |
| Severity exact, answered | 70.6 % | 2/2 | 68.4 % |
| Severity within ±1, answered | **100 %** | 2/2 | 89.5 % |
| Clean vs needs-collection, answered | **100 %** | 2/2 | 94.7 % |
| Median latency (answered) | 21 s | ~90–100 s (2 samples) | 9 s |
| p90 latency (answered) | 86 s | n/a | 14 s |
| Cost | $0 (free tier) | $0 (free tier) | $0 (free tier) |

Small samples: with n ≈ 34, a ±1-image change moves a percentage by about 3 points. Treat differences under
~10 points as noise.

## Decision

- **Vision: Gemma 4 26B-A4B first, Gemma 4 31B as fallback.** 26B was the only candidate that both
  answered reliably and matched or beat the closed reference on accuracy. It never produced invalid JSON
  and never confused a clean scene with one needing collection. We had hoped to use **31B**, but it answered
  only 2 of 29 requests during our test window (Google returned "high demand"/internal errors), so its
  accuracy is **unmeasured**. It stays in the chain in case it recovers. Change the order in
  `config/models.yaml`.
- **Drafting: Gemini 3.5 Flash-Lite → 3.1 Flash-Lite → 2.5 Flash → offline templates.** Flash-Lite wrote
  clean English, Hindi and Punjabi tickets in about 2 s each in our spot checks (3 drafts; not
  formally evaluated or checked by a native speaker). Gemini 3.5 Flash is excluded because its free-tier limit
  (20 requests/day) is too low for a demo.
- **Why Gemini isn't used for vision:** it is faster, but the photo judgement is the core of the
  product. An open-weight model there means any city can self-host it for cost and data sovereignty, which is
  also the intent of the open-source bonus.

## Threats to validity

- Single AI annotator; no inter-annotator agreement.
- Non-Indian photos dominate the clean/overflowing-bin classes; few wet-organic, no hazardous examples.
- Latency was measured with three models running in parallel on the free tier, at a time of reported high demand.
- Early stop: 3 of 41 images were not run on 26B and 12 not on 31B.

## Reproduce

```bash
python scripts/benchmark.py --models gemma-4-26b-a4b-it gemma-4-31b-it gemini-3.5-flash   # resumable
python scripts/benchmark.py --summary-only
python scripts/benchmark.py --record gemma-4-26b-a4b-it   # refresh offline replay file
```

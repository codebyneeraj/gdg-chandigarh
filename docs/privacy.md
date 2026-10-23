# Privacy and data handling

SaafRoute processes photos that citizens take in public places. We collect as little as possible and keep
a human in charge of every decision.

## What we store

| Data | Stored? | Notes |
|---|---|---|
| Photo | Yes, re-encoded JPEG (max 1024 px) | Every upload is decoded and re-encoded from pixels, which removes **all EXIF metadata, including GPS coordinates, camera model and timestamps**. Tested in `tests/test_pipeline.py::test_normalise_strips_exif_and_resizes`. |
| Photo with a visible face or number plate | Sharp photo kept for the reporter and officials; the public city view shows a heavily blurred 320 px copy | The vision model sets `privacy_flag` when it sees a recognisable face or readable plate. The sharp EXIF-stripped photo stays on the server, and `citizen/server.py` blurs it on the fly for the public view (`tests/test_api.py::test_privacy_flag_keeps_sharp_photo_and_marks_it`). |
| Sector | Yes | Chosen by the reporter. |
| GPS location | Only if the reporter ticks "Attach my GPS location" | Location outside Chandigarh is ignored. Without GPS we place the report near the sector centre. With GPS on, the form pre-selects the nearest sector (changeable), and an official sees a warning if the pin and the chosen sector disagree. |
| Name, phone, email, account, device ID | **No** | Reports are anonymous. |
| IP address | Not stored | Kept only in memory, for at most 60 seconds, for rate limiting (6 uploads per minute per IP). |
| Official's name | Yes | Recorded on approve/reject for accountability. |

## Where data goes

- **Vision model (Gemma 4 via the Gemini API):** the re-encoded photo is sent to Google's Gemini API for
  analysis. For a pilot that needs data to stay on municipal infrastructure, the same open Gemma 4 weights
  can be self-hosted (`vision.provider: ollama` in `config/models.yaml`); no photo then leaves the server.
  (The Ollama adapter is included but was **not** benchmarked in this build.)
- **Drafting model (Gemini):** receives only the structured analysis (sector, waste type, severity,
  one-sentence description). **The photo is never sent to the drafting model.**
- **Google free tier note:** content sent to the free tier of the Gemini API may be used by Google to improve
  its products. A real pilot should use a paid/enterprise tier or self-hosting.
- Nothing is shared with third parties; there are no analytics or trackers in the dashboard. Map tiles are
  loaded from OpenStreetMap's tile servers.

## Limits of the privacy safeguards (be honest)

- Face/plate detection is done by the vision model and is **best effort**, not a guarantee. In our benchmark
  set we excluded images with people, so its recall on faces was **not measured**. A production deployment
  should add a dedicated face/plate blurring model before storage.
- The blur is applied to the whole photo, not only the face, which is safe but loses detail.
- Officials can see stored photos; access control/login is **not implemented** in this prototype.

## Human oversight

The AI never dispatches a truck. Every ticket starts in `pending_review`; only an official's approval moves
it to `approved`, and only approved tickets are passed to the route planner. Model failures go to
"needs human review" rather than being guessed.

## Retention (recommendation for a pilot)

Delete photos 30 days after a ticket is collected or rejected; keep only the anonymous structured record
for statistics. (Not yet automated in this prototype.)

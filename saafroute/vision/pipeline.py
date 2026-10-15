"""Photo -> validated PhotoAnalysis.

Contract: `analyze_photo` never raises because of model behaviour. Invalid or
missing output is retried once with the validation error fed back; a second
failure returns status="needs_review" so a human looks at the photo.
"""

from __future__ import annotations

import io
import json
import re
import time

from PIL import Image, ImageFilter, ImageOps
from pydantic import ValidationError

from ..schemas import AnalysisResult, PhotoAnalysis
from . import VisionModel

MAX_SIDE = 1024

PROMPT = """You are inspecting a citizen photo for a municipal solid-waste team in India.
Return ONLY a JSON object with exactly these keys:
- "waste_type": the dominant material, one of
  "mixed" (unsegregated household/street garbage), "dry_recyclable" (mostly plastic, paper, cardboard, metal or glass),
  "wet_organic" (mostly food/garden/biodegradable), "construction_debris" (rubble, bricks),
  "bulky" (furniture, mattresses, large items), "e_waste" (appliances, electronics),
  "hazardous" (medical, chemical, batteries), "none" (no waste visible)
- "kind": one of "overflowing_bin", "illegal_dump", "litter", "clean"
  ("overflowing_bin" = waste spilling out of or piled around a bin/container/dumpster;
   "illegal_dump" = a pile of waste dumped where there is no container;
   "litter" = a few scattered items; "clean" = nothing to collect)
- "severity": integer 1-5 (1 = a few items, 2 = small bag-sized amount, 3 = a pile needing a cart,
  4 = a large pile needing a truck, 5 = very large pile blocking a road or posing a health risk)
- "confidence": number 0-1, your confidence in waste_type and kind
- "privacy_flag": true if a recognisable human face or a readable vehicle number plate is visible, else false
- "reason": one short sentence describing what you see that justifies the labels
Do not add any other keys or text."""


def normalise_image(data: bytes) -> bytes:
    """Decode, auto-rotate, downscale and re-encode as JPEG.

    Re-encoding from pixels drops all EXIF/GPS metadata. Raises ValueError if the
    bytes are not a decodable image.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
    except Exception as exc:  # Pillow raises many types for bad input
        raise ValueError("not a readable image") from exc
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def privacy_blur(jpeg: bytes) -> bytes:
    """Heavily blurred, downscaled copy kept when a face or plate was flagged."""
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    img.thumbnail((320, 320))
    img = img.filter(ImageFilter.GaussianBlur(radius=8))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=70)
    return out.getvalue()


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model answer (tolerates code fences / chatter)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


def parse_analysis(text: str) -> PhotoAnalysis:
    """Raises ValueError / ValidationError on anything not matching the schema."""
    return PhotoAnalysis.model_validate(extract_json(text))


def analyze_photo(image_jpeg: bytes, model: VisionModel, max_attempts: int = 2) -> AnalysisResult:
    t0 = time.perf_counter()
    prompt, last_error = PROMPT, None
    for attempt in range(1, max_attempts + 1):
        try:
            raw = model.generate(image_jpeg, prompt)
            analysis = parse_analysis(raw)
            return AnalysisResult(status="ok", analysis=analysis, model=model.name,
                                  attempts=attempt, latency_s=round(time.perf_counter() - t0, 2))
        except (ValueError, ValidationError) as exc:
            last_error = f"invalid output: {str(exc)[:300]}"
            prompt = (PROMPT + "\n\nYour previous answer was rejected: " + str(exc)[:300]
                      + "\nReturn only the corrected JSON object.")
        except Exception as exc:  # network / model server errors: retry, then fall back
            last_error = f"model error: {type(exc).__name__}: {str(exc)[:200]}"
    return AnalysisResult(status="needs_review", analysis=None, model=model.name, attempts=max_attempts,
                          latency_s=round(time.perf_counter() - t0, 2), error=last_error)

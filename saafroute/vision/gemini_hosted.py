"""Open-weight Gemma 4 served through the Gemini API (the default vision path).

The weights are Apache-2.0, so any municipality can later self-host the same
model (see vision/ollama.py) without changing prompts or schema.

`models` is an ordered fallback chain: if the first model returns a server-side
error (429/5xx, e.g. "high demand") or times out, the next one is tried for the
same photo. Client errors (bad request, bad key) are not retried here.
"""

from __future__ import annotations

from google import genai
from google.genai import errors, types


class HostedGemmaVision:
    def __init__(self, models: list[str], api_key: str | None, timeout_s: float) -> None:
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set; required for vision provider gemini_hosted")
        self.models = models
        self.name = f"gemini-api:{models[0]}"
        self.last_model_used: str | None = None
        self.client = genai.Client(api_key=api_key,
                                   http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))

    def generate(self, image_jpeg: bytes, prompt: str) -> str:
        last_exc: Exception | None = None
        for model in self.models:
            try:
                resp = self.client.models.generate_content(
                    model=model,
                    contents=[types.Part.from_bytes(data=image_jpeg, mime_type="image/jpeg"), prompt],
                    config=types.GenerateContentConfig(temperature=0),
                )
                self.last_model_used = model
                self.name = f"gemini-api:{model}"
                return resp.text or ""
            except errors.ServerError as exc:      # 5xx: overloaded / internal
                last_exc = exc
            except errors.ClientError as exc:      # 429 quota is worth falling back on; other 4xx are not
                if getattr(exc, "code", None) != 429:
                    raise
                last_exc = exc
            except Exception as exc:               # timeouts / connection errors
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    def available(self) -> bool:
        return True

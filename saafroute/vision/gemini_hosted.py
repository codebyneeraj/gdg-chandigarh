"""Open-weight Gemma 4 served through the Gemini API (the default vision path).

The weights are Apache-2.0, so any municipality can later self-host the same
model (see vision/ollama.py) without changing prompts or schema.

`models` is an ordered fallback chain: if the first model returns a server-side
error (429/5xx, e.g. "high demand") or times out, the next one is tried for the
same photo. Client errors (bad request, bad key) are not retried here.
"""

from __future__ import annotations

import logging
import threading

from google import genai
from google.genai import errors, types

log = logging.getLogger("saafroute")


def _block_reason(resp) -> str:
    """Why a response had no text (safety block, finish reason), for the error message."""
    try:
        fb = getattr(resp, "prompt_feedback", None)
        if fb is not None and getattr(fb, "block_reason", None):
            return f"prompt blocked ({fb.block_reason})"
        cands = getattr(resp, "candidates", None) or []
        if cands and getattr(cands[0], "finish_reason", None):
            return f"finish_reason={cands[0].finish_reason}"
    except Exception:
        pass
    return "no text returned"


class HostedGemmaVision:
    def __init__(self, models: list[str], api_key: str | None, timeout_s: float, fallback=None) -> None:
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set; required for vision provider gemini_hosted")
        self.models = models
        self.fallback = fallback          # optional last resort, e.g. GroqVision, tried after every Gemini model
        self._default_name = f"gemini-api:{models[0] if models else 'none'}"
        self._local = threading.local()   # per-thread: which model answered the last call
        self.client = genai.Client(api_key=api_key,
                                   http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))

    @property
    def name(self) -> str:
        return getattr(self._local, "name", self._default_name)

    @property
    def last_model_used(self) -> str | None:
        return getattr(self._local, "model", None)

    def generate(self, image_jpeg: bytes, prompt: str) -> str:
        last_exc: Exception | None = None
        for model in self.models:
            try:
                resp = self.client.models.generate_content(
                    model=model,
                    contents=[types.Part.from_bytes(data=image_jpeg, mime_type="image/jpeg"), prompt],
                    config=types.GenerateContentConfig(temperature=0),
                )
                text = resp.text
                if not text:
                    raise RuntimeError(f"empty response from {model}: {_block_reason(resp)}")
                self._local.model = model
                self._local.name = f"gemini-api:{model}"
                return text
            except errors.ServerError as exc:      # 5xx: overloaded / internal
                last_exc = exc
            except errors.ClientError as exc:      # 429 quota is worth falling back on; other 4xx are not
                last_exc = exc
                if getattr(exc, "code", None) != 429:
                    break                          # bad request / bad key: another Gemini model will not help
            except Exception as exc:               # timeouts / connection errors
                last_exc = exc
        if self.fallback is not None:
            log.warning("Gemini vision chain failed (%s); trying %s", last_exc, self.fallback.name)
            text = self.fallback.generate(image_jpeg, prompt)
            self._local.model = self.fallback.model
            self._local.name = self.fallback.name
            return text
        raise last_exc or RuntimeError("no vision models configured")

    def available(self) -> bool:
        return True

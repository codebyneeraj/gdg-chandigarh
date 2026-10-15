"""Open-weight vision model served locally by Ollama."""

from __future__ import annotations

import base64

import httpx

from ..schemas import PHOTO_JSON_SCHEMA


class OllamaVision:
    def __init__(self, model: str, url: str, timeout_s: float) -> None:
        self.name = f"ollama:{model}"
        self.model = model
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s

    def generate(self, image_jpeg: bytes, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt,
                          "images": [base64.b64encode(image_jpeg).decode()]}],
            # Constrained decoding against the Pydantic schema; we still validate afterwards.
            "format": PHOTO_JSON_SCHEMA,
            "think": False,
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 4096},
        }
        r = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()["message"]["content"]

    def available(self) -> bool:
        try:
            tags = httpx.get(f"{self.url}/api/tags", timeout=3).json()
        except httpx.HTTPError:
            return False
        return any(m["name"] == self.model or m["name"] == f"{self.model}:latest" for m in tags.get("models", []))

"""Groq-hosted vision model, used as a last-resort fallback behind the Gemini chain."""

from __future__ import annotations

import base64

from ..groq_client import GroqClient


class GroqVision:
    def __init__(self, model: str, api_key: str, timeout_s: float) -> None:
        self.model = model
        self.name = f"groq:{model}"
        self.client = GroqClient(api_key, timeout_s)

    def generate(self, image_jpeg: bytes, prompt: str) -> str:
        url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()
        return self.client.chat(self.model, [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": url}},
        ]}])

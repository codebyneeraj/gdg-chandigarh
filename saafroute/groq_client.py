"""Minimal Groq chat client (OpenAI-compatible API). Used only as a last-resort fallback."""

from __future__ import annotations

import httpx

URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqClient:
    def __init__(self, api_key: str, timeout_s: float) -> None:
        self.api_key = api_key
        self.timeout_s = timeout_s

    def chat(self, model: str, messages: list[dict], *, temperature: float = 0, json_mode: bool = False) -> str:
        payload: dict = {"model": model, "messages": messages, "temperature": temperature}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = httpx.post(URL, json=payload, headers={"Authorization": f"Bearer {self.api_key}"},
                       timeout=self.timeout_s)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"].get("content")
        if not text:
            raise RuntimeError(f"empty response from groq:{model}")
        return text

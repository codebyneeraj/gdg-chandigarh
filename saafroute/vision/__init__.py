"""Vision adapters. Every adapter returns raw text; validation lives in pipeline.py."""

from __future__ import annotations

from typing import Protocol

from ..config import VisionConfig


class VisionModel(Protocol):
    name: str

    def generate(self, image_jpeg: bytes, prompt: str) -> str:
        """Return the model's raw text answer for one image."""
        ...


def build_vision_model(cfg: VisionConfig, gemini_api_key: str | None = None) -> VisionModel:
    if cfg.provider == "ollama":
        from .ollama import OllamaVision
        return OllamaVision(cfg.ollama_model, cfg.ollama_url, cfg.timeout_s)
    if cfg.provider == "gemini_hosted":
        from .gemini_hosted import HostedGemmaVision
        return HostedGemmaVision(cfg.hosted_models, gemini_api_key, cfg.timeout_s)
    if cfg.provider == "replay":
        from .replay import ReplayVision
        return ReplayVision()
    raise ValueError(f"unknown vision provider: {cfg.provider}")

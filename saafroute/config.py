"""Loads config/models.yaml plus environment overrides.

Environment variables win over the file so a demo machine can switch models
without editing tracked files:
  SAAFROUTE_VISION_PROVIDER, SAAFROUTE_VISION_MODELS (comma-separated),
  SAAFROUTE_DRAFT_PROVIDER, SAAFROUTE_DRAFT_MODELS (comma-separated), SAAFROUTE_DB, OLLAMA_URL
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


@dataclass(frozen=True)
class VisionConfig:
    provider: str
    hosted_models: list[str]
    ollama_model: str
    ollama_url: str
    timeout_s: float


@dataclass(frozen=True)
class DraftingConfig:
    provider: str
    models: list[str]
    timeout_s: float


@dataclass(frozen=True)
class Settings:
    vision: VisionConfig
    drafting: DraftingConfig
    db_path: Path
    upload_dir: Path
    gemini_api_key: str | None


def load_settings(path: Path | None = None) -> Settings:
    path = path or Path(os.environ.get("SAAFROUTE_CONFIG", ROOT / "config" / "models.yaml"))
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    v, d = raw["vision"], raw["drafting"]
    env = os.environ.get
    vision = VisionConfig(
        provider=env("SAAFROUTE_VISION_PROVIDER", v["provider"]),
        hosted_models=(env("SAAFROUTE_VISION_MODELS").split(",") if env("SAAFROUTE_VISION_MODELS")
                       else list(v["hosted_models"])),
        ollama_model=v.get("ollama_model", "gemma4:e4b"),
        ollama_url=env("OLLAMA_URL", v.get("ollama_url", "http://localhost:11434")),
        timeout_s=float(v.get("timeout_s", 90)),
    )
    drafting = DraftingConfig(
        provider=env("SAAFROUTE_DRAFT_PROVIDER", d["provider"]),
        models=(env("SAAFROUTE_DRAFT_MODELS").split(",") if env("SAAFROUTE_DRAFT_MODELS")
                else list(d["models"])),
        timeout_s=float(d.get("timeout_s", 30)),
    )
    db_path = Path(env("SAAFROUTE_DB", DATA_DIR / "saafroute.db"))
    return Settings(
        vision=vision,
        drafting=drafting,
        db_path=db_path,
        upload_dir=Path(env("SAAFROUTE_UPLOADS", DATA_DIR / "uploads")),
        gemini_api_key=env("GEMINI_API_KEY") or env("GOOGLE_API_KEY"),
    )

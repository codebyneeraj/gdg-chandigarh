"""Offline mode: replays outputs recorded from the real model during benchmarking.

Images are matched by SHA-256 of their normalised JPEG bytes. Unknown images get
an empty answer, which the pipeline turns into "needs human review", the
same path a real model failure takes.
"""

from __future__ import annotations

import hashlib
import json

from ..config import DATA_DIR

RECORDED = DATA_DIR / "bench" / "recorded_outputs.json"


class ReplayVision:
    def __init__(self) -> None:
        self.recorded: dict[str, dict] = {}
        if RECORDED.exists():
            self.recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
        self.name = "replay:" + (next(iter(self.recorded.values()))["model"] if self.recorded else "none")

    def generate(self, image_jpeg: bytes, prompt: str) -> str:
        hit = self.recorded.get(hashlib.sha256(image_jpeg).hexdigest())
        return hit["raw"] if hit else ""

    def available(self) -> bool:
        return bool(self.recorded)

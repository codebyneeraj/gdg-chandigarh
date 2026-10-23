"""Groq is a last-resort fallback behind the Gemini chain (no network)."""

from types import SimpleNamespace

import pytest

from saafroute.config import VisionConfig
from saafroute.vision import build_vision_model
from saafroute.vision.gemini_hosted import HostedGemmaVision
from saafroute.vision.groq import GroqVision


class _DownModels:
    def generate_content(self, **kw):
        raise RuntimeError("gemini is down")


def _hosted(fallback):
    vm = HostedGemmaVision(["m1", "m2"], "k", 5, fallback)
    vm.client = SimpleNamespace(models=_DownModels())
    return vm


def _groq(monkeypatch, answer="groq answer"):
    g = GroqVision("llama-x", "gk", 5)
    seen = {}

    def chat(model, messages, **kw):
        seen.update(model=model, messages=messages)
        return answer

    monkeypatch.setattr(g.client, "chat", chat)
    return g, seen


def test_groq_used_after_all_gemini_models_fail(monkeypatch):
    g, seen = _groq(monkeypatch)
    vm = _hosted(g)
    assert vm.generate(b"jpeg", "describe") == "groq answer"
    assert vm.name == "groq:llama-x" and vm.last_model_used == "llama-x"
    parts = seen["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "describe"}
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_gemini_error_still_raised_without_groq():
    with pytest.raises(RuntimeError, match="gemini is down"):
        _hosted(None).generate(b"jpeg", "p")


def test_groq_only_wired_when_key_and_model_present():
    cfg = VisionConfig("gemini_hosted", ["m"], "o", "http://x", 5, "llama-x")
    assert build_vision_model(cfg, "gk").fallback is None
    assert isinstance(build_vision_model(cfg, "gk", "groq-key").fallback, GroqVision)
    no_model = VisionConfig("gemini_hosted", ["m"], "o", "http://x", 5, None)
    assert build_vision_model(no_model, "gk", "groq-key").fallback is None

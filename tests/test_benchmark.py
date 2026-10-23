import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import benchmark as bm  # noqa: E402
import pytest  # noqa: E402

from saafroute.vision import replay  # noqa: E402
from saafroute.vision.gemini_hosted import HostedGemmaVision  # noqa: E402


def test_percentile_small_n_monotonic():
    v = [1.0, 2.0, 10.0]
    assert bm.percentile(v, 50) == 2.0
    assert bm.percentile(v, 50) <= bm.percentile(v, 90)
    assert bm.percentile([5.0], 90) == 5.0
    assert bm.percentile([], 50) is None


def test_is_model_error():
    assert bm.is_model_error({"status": "needs_review", "analysis": None, "error": "model error: 429"})
    assert bm.is_model_error({"status": "needs_review", "analysis": None, "error": "weird new wording"})
    assert not bm.is_model_error({"status": "needs_review", "analysis": None, "error": "invalid output: x"})
    assert not bm.is_model_error({"status": "ok", "analysis": {"kind": "clean"}, "error": None})


def test_prepare_resume_retries_errors_but_keeps_history(tmp_path):
    p = tmp_path / "m.jsonl"
    rows = [{"id": "a", "status": "ok", "analysis": {"kind": "clean"}, "error": None},
            {"id": "b", "status": "needs_review", "analysis": None, "error": "model error: 500"},
            {"id": "c", "status": "needs_review", "analysis": None, "error": "invalid output: bad"}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n{corrupt", encoding="utf-8")
    assert bm.prepare_resume(p) == {"a", "c"}
    assert len(p.read_text().splitlines()) == 4          # nothing deleted
    rows.append({"id": "b", "status": "ok", "analysis": {"kind": "clean"}, "error": None})
    assert bm.latest_by_id(rows)["b"]["status"] == "ok"  # retry supersedes failure

def test_replay_corrupt_file(tmp_path, monkeypatch):
    f = tmp_path / "r.json"
    f.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(replay, "RECORDED", f)
    rv = replay.ReplayVision()
    assert not rv.available() and rv.generate(b"x", "p") == ""


class _Models:
    def __init__(self, resp):
        self.resp = resp

    def generate_content(self, **kw):
        return self.resp


def _vm(resp, models=("m",)):
    vm = HostedGemmaVision(list(models), "k", 5)
    vm.client = SimpleNamespace(models=_Models(resp))
    return vm


def test_hosted_empty_text_is_error_with_reason():
    resp = SimpleNamespace(text=None, prompt_feedback=SimpleNamespace(block_reason="SAFETY"), candidates=[])
    with pytest.raises(RuntimeError, match="SAFETY"):
        _vm(resp).generate(b"x", "p")


def test_hosted_no_models_raises_cleanly():
    with pytest.raises(RuntimeError, match="no vision models"):
        _vm(SimpleNamespace(text="hi"), models=()).generate(b"x", "p")


def test_hosted_ok_sets_name():
    vm = _vm(SimpleNamespace(text="{}"))
    assert vm.generate(b"x", "p") == "{}" and vm.name == "gemini-api:m"

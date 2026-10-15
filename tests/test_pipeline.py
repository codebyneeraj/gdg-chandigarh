import io
import json

import pytest
from PIL import Image

from saafroute.schemas import PhotoAnalysis
from saafroute.vision.pipeline import analyze_photo, extract_json, normalise_image, parse_analysis

GOOD = {"waste_type": "mixed", "kind": "overflowing_bin", "severity": 3, "confidence": 0.8,
        "privacy_flag": False, "reason": "Bags piled around a full bin."}

# Recorded raw answers from real model runs (benchmark), trimmed.
RECORDED_FENCED = '```json\n{\n  "waste_type": "mixed",\n  "kind": "overflowing_bin",\n  "severity": 3,\n  "confidence": 0.9,\n  "privacy_flag": false,\n  "reason": "Various types of waste are piled on the ground around the recycling bins."\n}\n```'


class FakeModel:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []
        self.name = "fake"

    def generate(self, image_jpeg, prompt):
        self.prompts.append(prompt)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def jpeg(w=50, h=40) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), "green").save(b, "JPEG")
    return b.getvalue()


def test_valid_output_parses():
    assert parse_analysis(json.dumps(GOOD)).severity == 3


def test_recorded_fenced_output_parses():
    assert parse_analysis(RECORDED_FENCED).kind.value == "overflowing_bin"


@pytest.mark.parametrize("bad", [
    {**GOOD, "severity": 7},
    {**GOOD, "severity": 0},
    {**GOOD, "confidence": 1.5},
    {**GOOD, "kind": "volcano"},
    {**GOOD, "waste_type": "plastic"},          # old taxonomy is rejected
    {**GOOD, "extra": "nope"},                   # extra keys are rejected
    {k: v for k, v in GOOD.items() if k != "reason"},
])
def test_schema_rejects_bad_values(bad):
    with pytest.raises(Exception):
        PhotoAnalysis.model_validate(bad)


def test_extract_json_tolerates_chatter():
    assert extract_json('Sure! {"a": 1} hope that helps')["a"] == 1
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_retry_then_success_feeds_back_error():
    m = FakeModel(["not json", json.dumps(GOOD)])
    r = analyze_photo(jpeg(), m)
    assert r.status == "ok" and r.attempts == 2
    assert "previous answer was rejected" in m.prompts[1]


def test_two_failures_fall_back_to_human_review():
    r = analyze_photo(jpeg(), FakeModel(["{}", json.dumps({**GOOD, "severity": 9})]))
    assert r.status == "needs_review" and r.analysis is None and "invalid output" in r.error


def test_model_exception_never_crashes():
    r = analyze_photo(jpeg(), FakeModel([TimeoutError("slow"), ConnectionError("down")]))
    assert r.status == "needs_review" and "model error" in r.error


def test_normalise_strips_exif_and_resizes():
    img = Image.new("RGB", (3000, 2000), "red")
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"           # Make
    b = io.BytesIO()
    img.save(b, "JPEG", exif=exif.tobytes())
    out = Image.open(io.BytesIO(normalise_image(b.getvalue())))
    assert max(out.size) == 1024
    assert len(out.getexif()) == 0


def test_normalise_rejects_non_image():
    with pytest.raises(ValueError):
        normalise_image(b"%PDF-1.4 not an image")

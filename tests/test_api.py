"""End-to-end API flow with fake models (no network)."""

import importlib
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

GOOD = {"waste_type": "construction_debris", "kind": "illegal_dump", "severity": 4, "confidence": 0.9,
        "privacy_flag": False, "reason": "Rubble dumped on the verge."}


class FakeVision:
    name = "fake-vision"

    def __init__(self, answer):
        self.answer = answer

    def generate(self, image_jpeg, prompt):
        return self.answer


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAFROUTE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("SAAFROUTE_UPLOADS", str(tmp_path / "up"))
    monkeypatch.setenv("SAAFROUTE_DRAFT_PROVIDER", "template")
    monkeypatch.setenv("SAAFROUTE_VISION_PROVIDER", "replay")
    import saafroute.main as m
    m = importlib.reload(m)
    m.vision = FakeVision(json.dumps(GOOD))
    m._hits.clear()
    return TestClient(m.app), m


def photo(fmt="JPEG"):
    b = io.BytesIO()
    Image.new("RGB", (64, 48), "gray").save(b, fmt)
    return b.getvalue()


def upload(c, sector=22, lang="en", data=None, ctype="image/jpeg"):
    return c.post("/api/reports", data={"sector": str(sector), "lang": lang},
                  files={"photo": ("p.jpg", data or photo(), ctype)})


def test_report_to_route_flow(client):
    c, _ = client
    r = upload(c, sector=22, lang="pa")
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["status"] == "pending_review" and rep["kind"] == "illegal_dump"
    assert rep["score"] == 40 + 0 + 0 + 10       # sector 22 market => site 10
    assert "ਸੈਕਟਰ 22" in rep["ticket_title"]

    # nothing routed until a human approves
    assert c.post("/api/routes/plan").json()["stops"] == 0
    assert c.post(f"/api/reports/{rep['id']}/approve", json={"official": "JE Sharma"}).status_code == 200
    plan = c.post("/api/routes/plan").json()
    assert plan["stops"] == 1 and plan["routes"][0]["stop_ids"] == [rep["id"]] or any(
        rt["stop_ids"] == [rep["id"]] for rt in plan["routes"])
    assert "SIMULATION" in plan["note"]

    assert c.post(f"/api/reports/{rep['id']}/collected").status_code == 200
    assert c.post("/api/routes/plan").json()["stops"] == 0


def test_invalid_model_output_goes_to_human_review(client):
    c, m = client
    m.vision = FakeVision("I think it's garbage?")
    rep = upload(c).json()
    assert rep["vision_status"] == "needs_review" and rep["ticket_title"] is None
    assert c.post(f"/api/reports/{rep['id']}/approve", json={"official": "JE"}).status_code == 409


def test_privacy_flag_blurs_stored_image(client):
    c, m = client
    m.vision = FakeVision(json.dumps({**GOOD, "privacy_flag": True}))
    rep = upload(c).json()
    assert rep["image_blurred"] == 1
    img = Image.open(io.BytesIO(c.get(f"/api/reports/{rep['id']}/image").content))
    assert max(img.size) <= 320


def test_upload_limits(client):
    c, _ = client
    assert upload(c, sector=13).status_code == 422                       # no sector 13 in Chandigarh
    assert upload(c, data=b"%PDF", ctype="application/pdf").status_code == 415
    assert upload(c, data=b"not an image at all").status_code == 415
    assert upload(c, data=b"0" * (8 * 1024 * 1024 + 10)).status_code == 413


def test_rate_limit(client):
    c, _ = client
    codes = [upload(c).status_code for _ in range(7)]
    assert codes[:6] == [201] * 6 and codes[6] == 429


def test_reject_and_state_rules(client):
    c, _ = client
    rid = upload(c).json()["id"]
    assert c.post(f"/api/reports/{rid}/collected").status_code == 409    # must be approved first
    assert c.post(f"/api/reports/{rid}/reject", json={"official": "JE", "note": "duplicate"}).status_code == 200
    assert c.post(f"/api/reports/{rid}/approve", json={"official": "JE"}).status_code == 409


def test_redraft_language(client):
    c, _ = client
    rid = upload(c).json()["id"]
    d = c.post(f"/api/reports/{rid}/redraft", json={"lang": "hi"}).json()
    assert "सेक्टर" in d["title"]

"""End-to-end API flow with fake models (no network)."""

import importlib
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

GOOD = {"waste_type": "construction_debris", "kind": "illegal_dump", "severity": 4, "confidence": 0.9,
        "privacy_flag": False, "reason": "Rubble dumped on the verge."}
CLEAN = {"waste_type": "none", "kind": "clean", "severity": 1, "confidence": 0.95,
         "privacy_flag": True, "reason": "A person standing on a clean pavement."}


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


def test_confident_clean_photo_creates_no_ticket_and_keeps_no_photo(client):
    c, m = client
    m.vision = FakeVision(json.dumps(CLEAN))
    r = upload(c)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is False and body["kind"] == "clean" and "no ticket" in body["message"].lower()
    assert c.get("/api/reports").json() == []
    assert not list(m.settings.upload_dir.glob("*.jpg"))


def test_unsure_clean_photo_still_goes_to_a_human(client):
    c, m = client
    m.vision = FakeVision(json.dumps({**CLEAN, "confidence": 0.3, "privacy_flag": False}))
    r = upload(c)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending_review"
    assert len(c.get("/api/reports").json()) == 1


def test_privacy_flag_keeps_sharp_photo_and_marks_it(client):
    """A flagged photo stays sharp for the reporter/officials; the public view blurs it (citizen/server.py)."""
    c, m = client
    m.vision = FakeVision(json.dumps({**GOOD, "privacy_flag": True}))
    b = io.BytesIO()
    Image.new("RGB", (800, 600), "gray").save(b, "JPEG")
    rep = upload(c, data=b.getvalue()).json()
    assert rep["image_blurred"] == 1
    img = Image.open(io.BytesIO(c.get(f"/api/reports/{rep['id']}/image").content))
    assert max(img.size) == 800


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


def test_oversize_image_is_clean_4xx(client):
    c, _ = client
    b = io.BytesIO()
    Image.new("RGB", (7000, 7000), "gray").save(b, "JPEG")      # 49M pixels, well under 8 MB compressed
    assert upload(c, data=b.getvalue()).status_code == 415


def test_manual_analysis_unblocks_approval(client):
    c, m = client
    m.vision = FakeVision("I think it's garbage?")
    rid = upload(c).json()["id"]
    assert c.post(f"/api/reports/{rid}/approve", json={"official": "JE"}).status_code == 409
    r = c.post(f"/api/reports/{rid}/analysis", json={"official": "JE Sharma", "kind": "litter",
                                                     "waste_type": "mixed", "severity": 2})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["kind"] == "litter" and rep["score"] is not None and rep["ticket_title"]
    assert c.post(f"/api/reports/{rid}/approve", json={"official": "JE Sharma"}).status_code == 200
    assert c.post(f"/api/reports/{rid}/analysis", json={"official": "JE", "kind": "litter",
                                                        "waste_type": "mixed", "severity": 2}).status_code == 409


def test_unhandled_error_is_logged(client, caplog):
    c, m = client

    def boom():
        raise RuntimeError("kaboom")

    m.db.all = boom
    with caplog.at_level("ERROR", logger="saafroute"):
        r = TestClient(m.app, raise_server_exceptions=False).get("/api/reports")
    assert r.status_code == 500
    assert any("kaboom" in (rec.exc_text or "") or rec.exc_info for rec in caplog.records)

def upload_at(c, m, sector, at, **kw):
    s = m.SECTORS[at]
    r = c.post("/api/reports", data={"sector": str(sector), "lat": str(s["lat"]), "lon": str(s["lon"])},
               files={"photo": ("p.jpg", photo(), "image/jpeg")})
    assert r.status_code == 201, r.text
    return r.json()


def test_gps_in_another_sector_is_flagged(client):
    c, m = client
    rep = upload_at(c, m, sector=8, at=17)
    assert rep["gps_from_user"] == 1 and rep["sector_mismatch"] is True and rep["nearest_sector"] == 17


def test_no_mismatch_when_gps_agrees_or_is_off(client):
    c, m = client
    agree = upload_at(c, m, sector=17, at=17)
    assert agree["sector_mismatch"] is False and agree["nearest_sector"] == 17
    no_gps = upload(c, sector=8).json()
    assert no_gps["gps_from_user"] == 0 and no_gps["sector_mismatch"] is False and no_gps["nearest_sector"] is None


def test_sector_fix_moves_pin_only_when_gps_was_not_used(client):
    c, m = client
    rid = upload(c, sector=8).json()["id"]
    r = c.post(f"/api/reports/{rid}/sector", json={"official": "JE Sharma", "sector": 17})
    assert r.status_code == 200, r.text
    rep = r.json()
    s17 = m.SECTORS[17]
    assert rep["sector"] == 17 and abs(rep["lat"] - s17["lat"]) <= 0.002 and abs(rep["lon"] - s17["lon"]) <= 0.002
    assert "17" in rep["ticket_title"]                       # the draft names the new sector

    gps = upload_at(c, m, sector=8, at=17)
    fixed = c.post(f"/api/reports/{gps['id']}/sector", json={"official": "JE Sharma", "sector": 17}).json()
    assert (fixed["lat"], fixed["lon"]) == (gps["lat"], gps["lon"])    # the citizen's GPS pin is kept
    assert fixed["sector"] == 17 and fixed["sector_mismatch"] is False


def test_sector_fix_rejects_bad_requests(client):
    c, _ = client
    rid = upload(c).json()["id"]
    assert c.post(f"/api/reports/{rid}/sector", json={"official": "JE", "sector": 13}).status_code == 422
    assert c.post("/api/reports/999999/sector", json={"official": "JE", "sector": 17}).status_code == 404
    assert c.post(f"/api/reports/{rid}/reject", json={"official": "JE"}).status_code == 200
    assert c.post(f"/api/reports/{rid}/sector", json={"official": "JE", "sector": 17}).status_code == 409

# ---------------------------------------------------------------- duplicate-report counter

LITTER = {"waste_type": "mixed", "kind": "litter", "severity": 2, "confidence": 0.9,
          "privacy_flag": False, "reason": "Scattered litter on the pavement."}


def test_similar_open_ticket_is_suggested_by_sector_without_gps(client):
    c, m = client
    first = upload(c, sector=22).json()
    assert first["similar"] == []
    second = upload(c, sector=22).json()
    assert [s["id"] for s in second["similar"]] == [first["id"]]
    assert second["similar"][0]["report_count"] == 1 and second["similar"][0]["distance_m"] is None
    assert upload(c, sector=8).json()["similar"] == []                      # other sector
    m.vision = FakeVision(json.dumps(LITTER))
    assert upload(c, sector=22).json()["similar"] == []                     # other kind


def test_similar_with_gps_uses_a_100_m_radius(client):
    c, m = client
    first = upload_at(c, m, sector=17, at=17)
    near = upload_at(c, m, sector=17, at=17)
    assert [(s["id"], s["distance_m"]) for s in near["similar"]] == [(first["id"], 0)]
    far = upload_at(c, m, sector=22, at=22)
    assert far["similar"] == []


def test_merge_raises_the_counter_but_never_the_score_or_routing(client):
    c, _ = client
    parent = upload(c, sector=22).json()
    base = parent["score"]
    kids = [upload(c, sector=22).json() for _ in range(2)]
    for kid in kids:
        r = c.post(f"/api/reports/{kid['id']}/merge", json={"into": parent["id"]})
        assert r.status_code == 200, r.text
    rows = {r["id"]: r for r in c.get("/api/reports").json()}
    p = rows[parent["id"]]
    assert p["report_count"] == 3
    assert p["score"] == pytest.approx(base, abs=0.2)                       # only the clock (age) may move it
    assert p["score_breakdown"]["repeats"] == 0 and p["score_breakdown"]["repeat_count"] == 0
    for kid in kids:
        k = rows[kid["id"]]
        assert k["status"] == "merged" and k["merged_into"] == parent["id"] and k["score"] is None and k["report_count"] is None

    assert c.post(f"/api/reports/{parent['id']}/approve", json={"official": "JE Sharma"}).status_code == 200
    plan = c.post("/api/routes/plan").json()
    assert plan["stops"] == 1
    assert [i for rt in plan["routes"] for i in rt["stop_ids"]] == [parent["id"]]
    assert sum(rt["load"] for rt in plan["routes"]) == 2 * 4                # one illegal dump of severity 4, no more


def test_merge_rejects_invalid_requests(client):
    c, m = client
    a = upload(c, sector=22).json()
    b = upload(c, sector=22).json()
    merge = lambda rid, into: c.post(f"/api/reports/{rid}/merge", json={"into": into}).status_code   # noqa: E731
    assert merge(b["id"], b["id"]) == 422                                   # itself
    assert merge(b["id"], 999999) == 404                                    # unknown ticket
    assert merge(999999, a["id"]) == 404                                    # unknown report
    m.vision = FakeVision(json.dumps(LITTER))
    other_kind = upload(c, sector=22).json()
    assert merge(other_kind["id"], a["id"]) == 409                          # different kind of waste
    m.vision = FakeVision(json.dumps(GOOD))
    assert merge(b["id"], a["id"]) == 200
    assert merge(b["id"], a["id"]) == 409                                   # already merged
    c2 = upload(c, sector=22).json()
    assert merge(c2["id"], b["id"]) == 409                                  # target is merged, not an open ticket
    assert c.post(f"/api/reports/{a['id']}/reject", json={"official": "JE"}).status_code == 200
    assert merge(c2["id"], a["id"]) == 409                                  # target is no longer open
    m.vision = FakeVision("I think it's garbage?")
    unclear = upload(c, sector=22).json()
    assert merge(unclear["id"], c2["id"]) == 409                            # no analysis yet


def test_existing_database_gets_the_merged_into_column(tmp_path):
    import sqlite3
    from saafroute.db import DB
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE reports (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, sector INTEGER NOT NULL, "
                    "lat REAL NOT NULL, lon REAL NOT NULL, vision_status TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_review')")
    db = DB(path)
    rid = db.insert(created_at="2026-01-01T00:00:00+00:00", sector=1, lat=30.7, lon=76.8, vision_status="ok", status="pending_review")
    db.update(rid, status="merged", merged_into=5)
    assert db.get(rid)["merged_into"] == 5


def test_citizen_page_merges_and_hides_merged_reports_from_the_city_view(client, tmp_path, monkeypatch):
    """citizen/server.py talking to the dashboard app in-process (no network)."""
    import importlib.util
    from pathlib import Path
    c, _ = client
    spec = importlib.util.spec_from_file_location("citizen_server", Path(__file__).resolve().parent.parent / "citizen" / "server.py")
    cs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cs)
    monkeypatch.setattr(cs, "TOKEN_FILE", tmp_path / "tickets.json")
    cs.TOKENS.clear()
    cs._cache.clear()

    def path(url):
        return url.replace(cs.DASHBOARD, "")

    monkeypatch.setattr(cs.httpx, "get", lambda url, **kw: c.get(path(url)))
    monkeypatch.setattr(cs.httpx, "post", lambda url, **kw: c.post(path(url), **{k: v for k, v in kw.items() if k != "timeout"}))
    citizen = TestClient(cs.app)

    def send():
        return citizen.post("/report", data={"sector": "22", "lang": "en"}, files={"photo": ("p.jpg", photo(), "image/jpeg")}).json()

    first, second = send(), send()
    assert [s["id"] for s in second["similar"]] == [first["id"]]
    assert set(second["similar"][0]) == {"id", "sector", "kind", "title", "report_count"}      # nothing internal leaks

    merged = citizen.post(f"/report/{second['token']}/merge", json={"into": first["id"]})
    assert merged.status_code == 200, merged.text
    assert merged.json()["merged_into"] == first["id"] and merged.json()["report_count"] == 2
    assert citizen.post(f"/report/{second['token']}/merge", json={"into": first["id"]}).status_code == 409
    assert citizen.post("/report/not-a-token/merge", json={"into": first["id"]}).status_code == 404

    mine = citizen.get(f"/ticket/{second['token']}").json()
    assert mine["merged_into"] == first["id"] and mine["id"] == first["id"] and mine["report_count"] == 2

    cs._cache.clear()
    public = citizen.get("/public/tickets").json()
    assert [t["id"] for t in public["tickets"]] == [first["id"]]                  # the merged report is not a ticket
    assert public["tickets"][0]["report_count"] == 2 and public["stats"]["awaiting"] == 1
"""FastAPI app: report upload, ticket review, hotspot scores, route planning, dashboard."""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import routing
from .config import DATA_DIR, load_settings
from .db import DB, now_iso
from .drafting import build_drafter
from .schemas import ApproveRequest, Lang, RedraftRequest, RejectRequest
from .scoring import ReportPoint, count_repeats, hotspot_score
from .vision import build_vision_model
from .vision.pipeline import analyze_photo, normalise_image, privacy_blur

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
RATE_LIMIT = (6, 60.0)  # uploads per window (seconds) per client IP

STATIC = Path(__file__).parent / "static"
SECTORS = {s["sector"]: s for s in json.loads((DATA_DIR / "sectors.json").read_text(encoding="utf-8"))["sectors"]}

settings = load_settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)
db = DB(settings.db_path)
vision = build_vision_model(settings.vision, settings.gemini_api_key)
drafter = build_drafter(settings.drafting, settings.gemini_api_key)
_vision_lock = threading.Semaphore(2)  # keep concurrent model calls (and API quota use) bounded

app = FastAPI(title="SaafRoute", version="0.1.0")
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limited(ip: str) -> bool:
    limit, window = RATE_LIMIT
    q, now = _hits[ip], time.monotonic()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _location(sector: int, lat: float | None, lon: float | None, seed: str) -> tuple[float, float, bool]:
    """User GPS pin if it is inside Chandigarh, else the sector centroid with a small
    deterministic offset (<~250 m) so reports in one sector don't stack on the map."""
    if lat is not None and lon is not None and 30.64 <= lat <= 30.80 and 76.68 <= lon <= 76.86:
        return lat, lon, True
    c = SECTORS[sector]
    rnd = random.Random(seed)
    return c["lat"] + rnd.uniform(-0.002, 0.002), c["lon"] + rnd.uniform(-0.002, 0.002), False


def _scored(rows: list[dict]) -> list[dict]:
    """Attach hotspot score + breakdown to every row with a usable analysis."""
    now = datetime.now(timezone.utc)
    points = [ReportPoint(r["id"], r["lat"], r["lon"], _parse_dt(r["created_at"]),
                          r["status"] in ("pending_review", "approved") and (r["kind"] or "") != "clean")
              for r in rows]
    by_id = {p.id: p for p in points}
    out = []
    for r in rows:
        r = dict(r)
        if r["severity"] and r["kind"] != "clean":
            rep = count_repeats(by_id[r["id"]], points)
            b = hotspot_score(r["severity"], _parse_dt(r["created_at"]), now, rep, r["sector"])
            r["score"], r["score_breakdown"] = b.total, b.as_dict()
        else:
            r["score"], r["score_breakdown"] = None, None
        r["has_image"] = bool(r["image_file"])
        r.pop("image_file", None)
        out.append(r)
    return out


def _facts(row: dict) -> dict:
    return {"sector": row["sector"], "kind": row["kind"], "waste_type": row["waste_type"],
            "severity": row["severity"], "reason": row["reason"]}


# ---------------------------------------------------------------- API

@app.get("/api/health")
def health() -> dict:
    return {
        "vision": {"provider": settings.vision.provider, "model": vision.name,
                   "available": getattr(vision, "available", lambda: True)()},
        "drafting": {"provider": settings.drafting.provider, "model": drafter.name},
        "reports": len(db.all()),
    }


@app.get("/api/sectors")
def sectors() -> dict:
    fleet_depot, trucks = routing.load_fleet(DATA_DIR / "fleet.json")
    return {"sectors": list(SECTORS.values()),
            "depot": {"name": fleet_depot.name, "lat": fleet_depot.lat, "lon": fleet_depot.lon},
            "trucks": [t.__dict__ for t in trucks]}


@app.post("/api/reports", status_code=201)
def create_report(request: Request, photo: UploadFile = File(...), sector: int = Form(...),
                        lat: float | None = Form(None), lon: float | None = Form(None),
                        lang: Lang = Form("en")) -> dict:
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(429, "Too many reports from this device. Please wait a minute and try again.")
    if sector not in SECTORS:
        raise HTTPException(422, f"Unknown Chandigarh sector: {sector}")
    if photo.content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "Please upload a JPEG, PNG or WebP photo.")
    data = photo.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Photo is larger than 8 MB.")
    try:
        jpeg = normalise_image(data)  # re-encode: strips EXIF/GPS metadata
    except ValueError:
        raise HTTPException(415, "That file could not be read as an image.")

    with _vision_lock:
        result = analyze_photo(jpeg, vision)
    a = result.analysis

    blurred = bool(a and a.privacy_flag)
    stored = privacy_blur(jpeg) if blurred else jpeg
    fname = hashlib.sha256(stored).hexdigest()[:24] + ".jpg"
    (settings.upload_dir / fname).write_bytes(stored)

    created = now_iso()
    plat, plon, from_user = _location(sector, lat, lon, fname)
    rid = db.insert(created_at=created, sector=sector, lat=plat, lon=plon, gps_from_user=int(from_user),
                    image_file=fname, image_blurred=int(blurred), synthetic=0,
                    vision_status=result.status, vision_model=result.model, vision_error=result.error,
                    waste_type=a.waste_type.value if a else None, kind=a.kind.value if a else None,
                    severity=a.severity if a else None, confidence=a.confidence if a else None,
                    reason=a.reason if a else None, status="pending_review")

    if a and a.kind.value != "clean":
        d = drafter.draft(lang, _facts(db.get(rid)))
        db.update(rid, ticket_lang=lang, ticket_title=d.title, ticket_body=d.body, ticket_model=drafter.last_used)
    return next(r for r in _scored(db.all()) if r["id"] == rid)


@app.get("/api/reports")
def list_reports() -> list[dict]:
    rows = _scored(db.all())
    return sorted(rows, key=lambda r: (r["status"] != "pending_review", -(r["score"] or 0), -r["id"]))


@app.get("/api/reports/{rid}/image")
def report_image(rid: int) -> FileResponse:
    row = db.get(rid)
    if not row or not row["image_file"]:
        raise HTTPException(404, "No image for this report")
    path = settings.upload_dir / row["image_file"]
    if not path.exists():
        raise HTTPException(404, "Image file missing")
    return FileResponse(path, media_type="image/jpeg")


def _require(rid: int, *states: str) -> dict:
    row = db.get(rid)
    if not row:
        raise HTTPException(404, "Report not found")
    if row["status"] not in states:
        raise HTTPException(409, f"Report is '{row['status']}', expected one of {list(states)}")
    return row


@app.post("/api/reports/{rid}/approve")
def approve(rid: int, body: ApproveRequest) -> dict:
    row = _require(rid, "pending_review")
    if not row["kind"] or row["kind"] == "clean":
        raise HTTPException(409, "Nothing to collect: set the analysis manually or reject the report")
    db.update(rid, status="approved", reviewed_by=body.official, reviewed_at=now_iso(),
              ticket_title=body.title or row["ticket_title"], ticket_body=body.body or row["ticket_body"])
    return {"ok": True}


@app.post("/api/reports/{rid}/reject")
def reject(rid: int, body: RejectRequest) -> dict:
    _require(rid, "pending_review", "approved")
    db.update(rid, status="rejected", reviewed_by=body.official, reviewed_at=now_iso(), review_note=body.note)
    return {"ok": True}


@app.post("/api/reports/{rid}/collected")
def collected(rid: int) -> dict:
    _require(rid, "approved")
    db.update(rid, status="collected", collected_at=now_iso())
    return {"ok": True}


@app.post("/api/reports/{rid}/redraft")
def redraft(rid: int, body: RedraftRequest) -> dict:
    row = _require(rid, "pending_review")
    if not row["kind"] or row["kind"] == "clean":
        raise HTTPException(409, "No analysis to draft from")
    d = drafter.draft(body.lang, _facts(row))
    db.update(rid, ticket_lang=body.lang, ticket_title=d.title, ticket_body=d.body, ticket_model=drafter.last_used)
    return {"title": d.title, "body": d.body, "lang": body.lang, "model": drafter.last_used}


@app.post("/api/routes/plan")
def plan_routes() -> dict:
    """Route only tickets a human approved. AI never dispatches on its own."""
    rows = [r for r in _scored(db.all()) if r["status"] == "approved" and r["score"] is not None]
    depot, trucks = routing.load_fleet(DATA_DIR / "fleet.json")
    stops = [routing.Stop(r["id"], r["lat"], r["lon"], routing.load_units(r["kind"], r["severity"]),
                          r["score"], r["sector"]) for r in rows]
    if not stops:
        return {"routes": [], "baseline": [], "dropped": [], "stops": 0,
                "note": "No approved tickets yet. Approve tickets to plan routes."}
    res = routing.compare(depot, trucks, stops)

    def ser(plan: routing.Plan) -> list[dict]:
        return [{"truck": r.truck, "stop_ids": r.stop_ids, "load": r.load,
                 "capacity": next(t.capacity for t in trucks if t.id == r.truck),
                 "distance_km": routing.km(r.distance_m), "path": r.path} for r in plan.routes]

    return {
        "stops": len(stops),
        "routes": ser(res["optimised"]),
        "baseline": ser(res["baseline"]),
        "dropped": res["optimised"].dropped,
        "optimised_km": routing.km(res["optimised"].total_distance_m),
        "baseline_km": routing.km(res["baseline"].total_distance_m),
        "saved_km": routing.km(res["saved_m"]),
        "saved_pct": res["saved_pct"],
        "note": res["note"],
    }


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on the server. Please try again."})


@app.middleware("http")
async def no_stale_ui(request: Request, call_next):
    """Browsers must re-check the UI files on every load, so a redesign is never hidden by a cached copy."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")

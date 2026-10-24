"""FastAPI app: report upload, ticket review, hotspot scores, route planning, dashboard."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import routing
from .config import DATA_DIR, load_settings
from .db import DB, now_iso
from .drafting import build_drafter
from .schemas import AnalysisRequest, ApproveRequest, Lang, MergeRequest, RedraftRequest, RejectRequest, SectorRequest
from .scoring import ReportPoint, count_repeats, haversine_m, hotspot_score
from .vision import build_vision_model
from .vision.pipeline import analyze_photo, normalise_image

log = logging.getLogger("saafroute")
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
CLEAN_MIN_CONFIDENCE = 0.6  # a "clean" verdict below this goes to human review instead of being dropped
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
RATE_LIMIT = (6, 60.0)  # uploads per window (seconds) per client IP
SECTOR_MISMATCH_M = 1500  # a GPS pin further than this from the chosen sector's centre, and nearer another, is flagged
SIMILAR_RADIUS_M = 100    # a new GPS report this close to an open ticket of the same kind may be "the same spot"
SIMILAR_WINDOW = timedelta(days=7)
SIMILAR_MAX = 3
OPEN_STATES = ("pending_review", "approved")

STATIC = Path(__file__).parent / "static"
SECTORS = {s["sector"]: s for s in json.loads((DATA_DIR / "sectors.json").read_text(encoding="utf-8"))["sectors"]}

settings = load_settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)
db = DB(settings.db_path)
vision = build_vision_model(settings.vision, settings.gemini_api_key, settings.groq_api_key)
drafter = build_drafter(settings.drafting, settings.gemini_api_key, settings.groq_api_key)
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


def _sector_check(row: dict) -> tuple[int | None, bool]:
    """For a report whose pin came from the citizen's GPS: the sector the pin is nearest to, and whether
    that contradicts the sector they chose. Pins placed from the chosen sector can never disagree."""
    if not row["gps_from_user"] or row["sector"] not in SECTORS:
        return None, False
    nearest = min(SECTORS.values(), key=lambda s: haversine_m(row["lat"], row["lon"], s["lat"], s["lon"]))["sector"]
    chosen = SECTORS[row["sector"]]
    far = haversine_m(row["lat"], row["lon"], chosen["lat"], chosen["lon"]) > SECTOR_MISMATCH_M
    return nearest, far and nearest != row["sector"]


def _scored(rows: list[dict]) -> list[dict]:
    """Attach hotspot score + breakdown to every row with a usable analysis."""
    now = datetime.now(timezone.utc)
    points = [ReportPoint(r["id"], r["lat"], r["lon"], _parse_dt(r["created_at"]),
                          r["status"] in OPEN_STATES and (r["kind"] or "") != "clean")  # merged reports are not open
              for r in rows]
    by_id = {p.id: p for p in points}
    merged_counts: dict[int, int] = {}
    for r in rows:
        if r["status"] == "merged" and r.get("merged_into"):
            merged_counts[r["merged_into"]] = merged_counts.get(r["merged_into"], 0) + 1
    out = []
    for r in rows:
        r = dict(r)
        # `report_count` is display-only: it is never an input to the score or to routing.
        r["report_count"] = None if r["status"] == "merged" else 1 + merged_counts.get(r["id"], 0)
        if r["status"] != "merged" and r["severity"] and r["kind"] != "clean":
            rep = count_repeats(by_id[r["id"]], points)
            b = hotspot_score(r["severity"], _parse_dt(r["created_at"]), now, rep, r["sector"])
            r["score"], r["score_breakdown"] = b.total, b.as_dict()
        else:
            r["score"], r["score_breakdown"] = None, None
        r["nearest_sector"], r["sector_mismatch"] = _sector_check(r)
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

    # Nothing to collect (e.g. a photo of a person): keep no photo and open no ticket.
    # A low-confidence "clean" still goes to a human, so real waste is not silently dropped.
    if a and a.kind.value == "clean" and a.confidence >= CLEAN_MIN_CONFIDENCE:
        return JSONResponse(status_code=200, content={
            "created": False, "kind": "clean", "waste_type": a.waste_type.value, "severity": a.severity,
            "confidence": a.confidence, "reason": a.reason, "vision_model": result.model,
            "message": "No waste detected in this photo, so no ticket was created. The photo was not kept."})

    # The sharp photo is kept for the reporter and officials; `image_blurred` records that a face or
    # plate was flagged, and the public city view then serves a blurred copy (see citizen/server.py).
    blurred = bool(a and a.privacy_flag)
    stored = jpeg
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
    rows = _scored(db.all())
    new = next(r for r in rows if r["id"] == rid)
    return {**new, "similar": _similar_open(new, rows)}


def _similar_open(new: dict, rows: list[dict]) -> list[dict]:
    """Open tickets of the same kind that this report may be a repeat of: within 100 m when the new report
    has a GPS pin, else in the same sector; created in the last 7 days. Only a suggestion: the reporter
    decides, and nothing is merged automatically. Demo (synthetic) tickets are never suggested."""
    if not new["kind"] or new["kind"] == "clean":
        return []
    now = _parse_dt(new["created_at"])
    found = []
    for r in rows:
        if (r["id"] == new["id"] or r["status"] not in OPEN_STATES or r["kind"] != new["kind"] or r["synthetic"]
                or now - _parse_dt(r["created_at"]) > SIMILAR_WINDOW):
            continue
        if new["gps_from_user"]:
            dist = haversine_m(new["lat"], new["lon"], r["lat"], r["lon"])
            if dist > SIMILAR_RADIUS_M:
                continue
        elif r["sector"] == new["sector"]:
            dist = None
        else:
            continue
        found.append({"id": r["id"], "sector": r["sector"], "kind": r["kind"], "title": r["ticket_title"],
                      "created_at": r["created_at"], "status": r["status"], "report_count": r["report_count"],
                      "distance_m": None if dist is None else round(dist)})
    found.sort(key=lambda s: (s["distance_m"] is None, s["distance_m"] or 0, -s["id"]))
    return found[:SIMILAR_MAX]


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


@app.post("/api/reports/{rid}/analysis")
def set_analysis(rid: int, body: AnalysisRequest) -> dict:
    """An official fills in kind/material/severity when the model gave none (or said 'clean');
    the report is then scored and a ticket drafted so it can be approved."""
    row = _require(rid, "pending_review")
    db.update(rid, kind=body.kind.value, waste_type=body.waste_type.value, severity=body.severity,
              confidence=1.0, reason=f"Set manually by {body.official}.")
    if body.kind.value != "clean":
        d = drafter.draft(body.lang, _facts(db.get(rid)))
        db.update(rid, ticket_lang=body.lang, ticket_title=d.title, ticket_body=d.body, ticket_model=drafter.last_used)
    return next(r for r in _scored(db.all()) if r["id"] == rid)


@app.post("/api/reports/{rid}/sector")
def set_sector(rid: int, body: SectorRequest) -> dict:
    """An official corrects the sector (e.g. the citizen picked the wrong one). A pin that came from
    the citizen's GPS is kept; a pin placed from the old sector's centre moves to the new one."""
    row = _require(rid, "pending_review", "approved")
    if body.sector not in SECTORS:
        raise HTTPException(422, f"Unknown Chandigarh sector: {body.sector}")
    changes: dict = {"sector": body.sector, "review_note": f"Sector {row['sector']} changed to {body.sector} by {body.official}."}
    if not row["gps_from_user"]:
        changes["lat"], changes["lon"], _ = _location(body.sector, None, None, row["image_file"])
    db.update(rid, **changes)
    if row["status"] == "pending_review" and row["kind"] and row["kind"] != "clean" and row["ticket_title"]:
        d = drafter.draft(row["ticket_lang"] or "en", _facts(db.get(rid)))  # the ticket text names the sector
        db.update(rid, ticket_title=d.title, ticket_body=d.body, ticket_model=drafter.last_used)
    return next(r for r in _scored(db.all()) if r["id"] == rid)


@app.post("/api/reports/{rid}/merge")
def merge(rid: int, body: MergeRequest) -> dict:
    """The reporter says their report is the same spot as an open ticket. The report is kept (with its
    photo) but becomes 'merged': it only raises that ticket's report counter. The counter never feeds the
    hotspot score or routing, and the merged report is no longer a ticket of its own."""
    row = _require(rid, "pending_review")
    if not row["kind"] or row["kind"] == "clean":
        raise HTTPException(409, "Only a report with an analysis can be added to another ticket")
    if body.into == rid:
        raise HTTPException(422, "A report cannot be added to itself")
    target = db.get(body.into)
    if not target:
        raise HTTPException(404, "Ticket to add to was not found")
    if target["status"] not in OPEN_STATES:
        raise HTTPException(409, f"Ticket #{body.into} is '{target['status']}', so it cannot take more reports")
    if target["kind"] != row["kind"]:
        raise HTTPException(409, "That ticket is a different kind of waste")
    db.update(rid, status="merged", merged_into=body.into,
              review_note=f"Added to ticket #{body.into} by the reporter as the same spot.")
    return next(r for r in _scored(db.all()) if r["id"] == body.into)


@app.post("/api/reports/{rid}/approve")
def approve(rid: int, body: ApproveRequest) -> dict:
    row = _require(rid, "pending_review")
    if not row["kind"] or row["kind"] == "clean":
        raise HTTPException(409, "Nothing to collect: set the analysis manually (Set analysis) or reject the report")
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
                 "distance_km": routing.km(r.distance_m), "path": r.path,
                 "road_path": routing.road_geometry(r.path)} for r in plan.routes]

    return {
        "stops": len(stops),
        "routes": ser(res["optimised"]),
        "baseline": ser(res["baseline"]),
        "dropped": res["optimised"].dropped,
        "optimised_km": routing.km(res["optimised"].total_distance_m),
        "baseline_km": routing.km(res["baseline"].total_distance_m),
        "saved_km": routing.km(res["saved_m"]),
        "saved_pct": res["saved_pct"],
        "distance_source": res["source"],
        "note": res["note"],
    }


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on the server. Please try again."})


@app.middleware("http")
async def no_stale_ui(request: Request, call_next):
    """Browsers must re-check the UI files on every load, so a redesign is never hidden by a cached copy."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")

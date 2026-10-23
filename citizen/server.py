"""Citizen report page: a small separate server that phones on the same Wi-Fi can open.

It only serves one form and forwards the report to the SaafRoute dashboard server, so the
dashboard itself can stay on this laptop (127.0.0.1) and only this page is exposed to the network.
Each submitted report gets a private random token; a citizen can only look at tickets (and photos)
whose token their phone holds, never at other people's by guessing ids.

    python citizen/server.py                      # http://<this-laptop-ip>:8001
    SAAFROUTE_DASHBOARD_URL=http://127.0.0.1:8000 CITIZEN_PORT=8001 python citizen/server.py
"""
from __future__ import annotations

import io
import json
import os
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageFilter

DASHBOARD = os.getenv("SAAFROUTE_DASHBOARD_URL", "http://127.0.0.1:8000").rstrip("/")
PORT = int(os.getenv("CITIZEN_PORT", "8001"))
HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE / ".tickets.json"

app = FastAPI(title="SaafRoute citizen report page", docs_url=None, redoc_url=None)
app.mount("/vendor", StaticFiles(directory=HERE.parent / "saafroute" / "static" / "vendor"), name="vendor")  # Leaflet

_lock = threading.Lock()
try:
    TOKENS: dict[str, int] = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
except (OSError, ValueError):
    TOKENS = {}

STATUS_TEXT = {
    "pending_review": "Waiting for an official to review",
    "approved": "Approved: scheduled for collection",
    "collected": "Collected. Thank you!",
    "rejected": "Closed by the official",
    "merged": "Added to an existing ticket",
}


def _down() -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": f"The SaafRoute dashboard server is not reachable at {DASHBOARD}."})


def _new_token(rid: int) -> str:
    token = secrets.token_urlsafe(16)
    with _lock:
        TOKENS[token] = rid
        try:
            TOKEN_FILE.write_text(json.dumps(TOKENS), encoding="utf-8")
        except OSError:
            pass  # still works for this run; the list just won't survive a restart
    return token


_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fn):
    """Small time-based cache so many phones don't each hit the dashboard (and the route solver)."""
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.monotonic(), val)
    return val


def _get(path: str):
    r = httpx.get(f"{DASHBOARD}{path}", timeout=15)
    r.raise_for_status()
    return r.json()


def _all_rows() -> list[dict]:
    return _cached("rows", 8, lambda: _get("/api/reports"))


def _centroids() -> dict[int, tuple[float, float]]:
    return _cached("cent", 3600, lambda: {s["sector"]: (s["lat"], s["lon"]) for s in _get("/api/sectors")["sectors"]})


def _trucks() -> dict[int, str]:
    """Report id -> truck it is currently planned onto (from the dashboard's route plan)."""
    def plan():
        r = httpx.post(f"{DASHBOARD}/api/routes/plan", timeout=30)
        r.raise_for_status()
        return {sid: rt["truck"] for rt in r.json()["routes"] for sid in rt["stop_ids"]}
    try:
        return _cached("trucks", 30, plan)
    except httpx.HTTPError:
        return {}


def _public(row: dict, token: str) -> dict:
    """Only what a citizen needs: no scores, confidence or model names."""
    return {
        "token": token, "id": row["id"], "sector": row["sector"], "created_at": row["created_at"],
        "officer": row.get("reviewed_by"), "reviewed_at": row.get("reviewed_at"),
        "truck": _trucks().get(row["id"]) if row["status"] == "approved" else None,
        "status": row["status"], "status_text": STATUS_TEXT.get(row["status"], row["status"]),
        "kind": row.get("kind"), "waste_type": row.get("waste_type"), "severity": row.get("severity"),
        "reason": row.get("reason"), "ticket_title": row.get("ticket_title"), "ticket_body": row.get("ticket_body"),
        "needs_manual": row.get("vision_status") != "ok" and not row.get("kind"),
        "image_blurred": bool(row.get("image_blurred")),
        "report_count": row.get("report_count") or 1, "merged_into": None,
    }


def _merged_view(parent: dict, own_id: int, token: str) -> dict:
    """A report the citizen added to an existing ticket shows that ticket's status, officer and truck."""
    d = _public(parent, token)
    d["merged_into"], d["own_id"] = parent["id"], own_id
    return d


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/sectors")
def sectors():
    try:
        r = httpx.get(f"{DASHBOARD}/api/sectors", timeout=10)
        r.raise_for_status()
    except httpx.HTTPError:
        return _down()
    return {"sectors": sorted(s["sector"] for s in r.json()["sectors"])}


@app.post("/report")
def report(photo: UploadFile = File(...), sector: int = Form(...), lang: str = Form("en")):
    try:
        r = httpx.post(f"{DASHBOARD}/api/reports", timeout=180,
                       data={"sector": str(sector), "lang": lang},
                       files={"photo": (photo.filename or "photo.jpg", photo.file.read(), photo.content_type or "image/jpeg")})
    except httpx.HTTPError:
        return _down()
    try:
        body = r.json()
    except ValueError:
        body = {"detail": "Unexpected reply from the dashboard server."}
    if r.status_code >= 400:
        return JSONResponse(status_code=r.status_code, content={"detail": body.get("detail", "Could not send the report.")})
    if body.get("created") is False:
        return {"created": False, "message": body.get("message", "No waste detected, so no ticket was created.")}
    # Open tickets this might be a repeat of (the citizen is asked; nothing is merged automatically).
    similar = [{k: s.get(k) for k in ("id", "sector", "kind", "title", "report_count")} for s in body.get("similar", [])]
    return {"created": True, **_public(body, _new_token(body["id"])), "similar": similar}


@app.post("/report/{token}/merge")
def merge_report(token: str, body: dict):
    """The citizen confirms their report is the same spot as an open ticket. Only their own report
    (identified by its private token) can be merged."""
    rid = TOKENS.get(token)
    into = body.get("into")
    if rid is None:
        return JSONResponse(status_code=404, content={"detail": "Ticket not found."})
    if not isinstance(into, int) or isinstance(into, bool):
        return JSONResponse(status_code=422, content={"detail": "Choose a ticket to add to."})
    try:
        r = httpx.post(f"{DASHBOARD}/api/reports/{rid}/merge", json={"into": into}, timeout=30)
    except httpx.HTTPError:
        return _down()
    try:
        parent = r.json()
    except ValueError:
        parent = {}
    if r.status_code >= 400:
        return JSONResponse(status_code=r.status_code, content={"detail": parent.get("detail", "Could not add your report.")})
    _cache.pop("rows", None)  # so the lists reflect the merge straight away
    return _merged_view(parent, rid, token)


def _fetch_row(token: str):
    rid = TOKENS.get(token)
    if rid is None:
        return None, JSONResponse(status_code=404, content={"detail": "Ticket not found."})
    try:
        r = httpx.get(f"{DASHBOARD}/api/reports", timeout=15)
        r.raise_for_status()
    except httpx.HTTPError:
        return None, _down()
    row = next((x for x in r.json() if x["id"] == rid), None)
    if row is None:
        return None, JSONResponse(status_code=404, content={"detail": "Ticket no longer exists."})
    return row, None


@app.get("/ticket/{token}")
def ticket(token: str):
    row, err = _fetch_row(token)
    if err:
        return err
    if row["status"] == "merged":
        try:
            parent = next((x for x in _get("/api/reports") if x["id"] == row.get("merged_into")), None)
        except httpx.HTTPError:
            return _down()
        if parent:
            return _merged_view(parent, row["id"], token)
    return _public(row, token)


@app.get("/ticket/{token}/image")
def ticket_image(token: str):
    rid = TOKENS.get(token)
    if rid is None:
        return JSONResponse(status_code=404, content={"detail": "Ticket not found."})
    try:
        r = httpx.get(f"{DASHBOARD}/api/reports/{rid}/image", timeout=15)
    except httpx.HTTPError:
        return _down()
    if r.status_code != 200:
        return JSONResponse(status_code=404, content={"detail": "No photo for this ticket."})
    return Response(r.content, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})


@app.get("/city")
def city() -> FileResponse:
    return FileResponse(HERE / "city.html", headers={"Cache-Control": "no-store"})


def _parse(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def _listed(r: dict) -> bool:
    return r["status"] not in ("rejected", "merged") and r.get("kind") != "clean"  # merged reports only raise a counter


def _item(r: dict, cent: dict, trucks: dict) -> dict:
    lat, lon = r["lat"], r["lon"]
    if r.get("gps_from_user") and r["sector"] in cent:
        lat, lon = cent[r["sector"]]
    return {
        "id": r["id"], "sector": r["sector"], "kind": r.get("kind"), "waste_type": r.get("waste_type"),
        "severity": r.get("severity"), "status": r["status"], "status_text": STATUS_TEXT.get(r["status"], r["status"]),
        "title": r.get("ticket_title"), "created_at": r["created_at"], "reviewed_at": r.get("reviewed_at"),
        "collected_at": r.get("collected_at"), "officer": r.get("reviewed_by"),
        "truck": trucks.get(r["id"]) if r["status"] == "approved" else None,
        "lat": lat, "lon": lon, "demo": bool(r.get("synthetic")), "report_count": r.get("report_count") or 1,
    }


@app.get("/public/ticket/{rid}")
def public_ticket(rid: int):
    """One ticket in detail for the city dashboard (ticket text, what was seen, timeline, photo flag)."""
    try:
        rows, cent = _all_rows(), _centroids()
    except httpx.HTTPError:
        return _down()
    r = next((x for x in rows if x["id"] == rid and _listed(x)), None)
    if r is None:
        return JSONResponse(status_code=404, content={"detail": "Ticket not found."})
    return {**_item(r, cent, _trucks()), "body": r.get("ticket_body"), "reason": r.get("reason"),
            "has_photo": bool(r.get("has_image")) and not r.get("synthetic"), "photo_blurred": bool(r.get("image_blurred"))}


def _blur(jpeg: bytes) -> bytes:
    """Public copy of a photo the model flagged for a face or plate: small and heavily blurred."""
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    img.thumbnail((320, 320))
    out = io.BytesIO()
    img.filter(ImageFilter.GaussianBlur(radius=8)).save(out, "JPEG", quality=70)
    return out.getvalue()


@app.get("/public/ticket/{rid}/image")
def public_ticket_image(rid: int):
    """Photo for the public city view (EXIF already stripped; blurred when the model flagged a face or plate)."""
    try:
        r = next((x for x in _all_rows() if x["id"] == rid and _listed(x)), None)
        if r is None or not r.get("has_image") or r.get("synthetic"):
            return JSONResponse(status_code=404, content={"detail": "No photo for this ticket."})
        img = httpx.get(f"{DASHBOARD}/api/reports/{rid}/image", timeout=15)
    except httpx.HTTPError:
        return _down()
    if img.status_code != 200:
        return JSONResponse(status_code=404, content={"detail": "No photo for this ticket."})
    content = _blur(img.content) if r.get("image_blurred") else img.content
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=300"})


@app.get("/public/tickets")
def public_tickets():
    """City-wide view for residents. No photos, no reporter details; GPS-based pins are
    shown at the sector centre so nobody's exact position is published."""
    try:
        rows, cent = _all_rows(), _centroids()
    except httpx.HTTPError:
        return _down()
    trucks = _trucks()
    out = [_item(r, cent, trucks) for r in rows if _listed(r)]
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    out.sort(key=lambda t: -(_parse(t["created_at"]) or epoch).timestamp())
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    by = lambda s: sum(1 for t in out if t["status"] == s)  # noqa: E731
    sectors_open: dict[int, int] = {}
    for t in out:
        if t["status"] in ("pending_review", "approved"):
            sectors_open[t["sector"]] = sectors_open.get(t["sector"], 0) + 1
    stats = {
        "awaiting": by("pending_review"), "scheduled": by("approved"), "collected": by("collected"),
        "new_24h": sum(1 for t in out if (_parse(t["created_at"]) or day_ago) > day_ago),
        "busiest": sorted(({"sector": s, "open": n} for s, n in sectors_open.items()), key=lambda x: -x["open"])[:3],
    }
    return {"tickets": out, "stats": stats, "has_demo": any(t["demo"] for t in out)}


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    print(f"\n  Citizen report page:  http://{lan_ip()}:{PORT}   (open this on a phone on the same Wi-Fi)")
    print(f"  Forwarding reports to: {DASHBOARD}\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT)

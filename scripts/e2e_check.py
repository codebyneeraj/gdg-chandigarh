"""End-to-end check of a RUNNING SaafRoute server, plus the unit tests.

    python scripts/e2e_check.py                    # server on http://127.0.0.1:8000, then pytest
    python scripts/e2e_check.py --url http://host:8000 --no-pytest
    python scripts/e2e_check.py --offline          # accept needs_review results (replay/template mode)

It adds a few real reports to the database. Run `python run_demo.py --reset` afterwards to wipe them.
Exit code is 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PHOTO = ROOT / "data" / "bench" / "images" / "cm017.jpg"  # construction rubble
OFFICIAL = "JE Autotest"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    return ok


def run_api_checks(base: str, offline: bool) -> None:
    c = httpx.Client(base_url=base, timeout=180)

    print("Server and static files")
    try:
        r = c.get("/api/health")
    except httpx.HTTPError as e:
        check("server reachable", False, f"{e.__class__.__name__}: {e}. Start it with python run_demo.py")
        return
    h = r.json()
    check("health 200", r.status_code == 200)
    check("vision model available", bool(h["vision"]["available"]), f"{h['vision']['provider']}/{h['vision']['model']}")
    print(f"       drafting: {h['drafting']['provider']}/{h['drafting']['model']}, reports in db: {h['reports']}")
    for path in ("/", "/app.js", "/style.css", "/vendor/leaflet.js"):
        check(f"GET {path}", c.get(path).status_code == 200)

    print("Reference data")
    s = c.get("/api/sectors").json()
    check("sectors loaded", len(s["sectors"]) >= 50, f"{len(s['sectors'])} sectors")
    check("fleet loaded", len(s["trucks"]) >= 1 and "lat" in s["depot"], f"{len(s['trucks'])} trucks")

    print("Input validation")
    img = PHOTO.read_bytes() if PHOTO.exists() else None
    check("sample photo present", img is not None, str(PHOTO))
    if img is None:
        return
    r = c.post("/api/reports", files={"photo": ("p.jpg", img, "image/jpeg")}, data={"sector": 9999})
    check("unknown sector -> 422", r.status_code == 422, str(r.status_code))
    r = c.post("/api/reports", files={"photo": ("p.txt", b"hello", "text/plain")}, data={"sector": 26})
    check("non-image type -> 415", r.status_code == 415, str(r.status_code))
    r = c.post("/api/reports", files={"photo": ("p.jpg", b"not really a jpeg", "image/jpeg")}, data={"sector": 26})
    check("corrupt image -> 415", r.status_code == 415, str(r.status_code))
    r = c.post("/api/reports/999999/approve", json={"official": OFFICIAL})
    check("approve unknown id -> 404", r.status_code == 404, str(r.status_code))

    print("Report -> review -> route (real photo, may take 20-60 s)")
    t0 = time.time()
    r = c.post("/api/reports", files={"photo": ("cm017.jpg", img, "image/jpeg")}, data={"sector": 26, "lang": "pa"})
    secs = time.time() - t0
    if not check("upload -> 201", r.status_code == 201, f"{r.status_code} in {secs:.0f}s"):
        print("       " + r.text[:300])
        return
    rep = r.json()
    rid = rep["id"]
    check("EXIF/GPS never stored: gps_from_user false", not rep["gps_from_user"])
    analysed = rep["vision_status"] == "ok" and rep["kind"]
    if not analysed:
        check("vision gave a result", offline, f"status={rep['vision_status']}, error={rep.get('vision_error')}"
              + ("" if offline else " (use --offline to accept this)"))
        # Human fallback path: official sets the analysis by hand.
        r = c.post(f"/api/reports/{rid}/analysis", json={"official": OFFICIAL, "kind": "illegal_dump",
                                                        "waste_type": "construction_debris", "severity": 4, "lang": "pa"})
        check("manual 'Set analysis' -> 200", r.status_code == 200, str(r.status_code))
        rep = r.json()
    else:
        check("vision gave a result", True, f"{rep['kind']}, severity {rep['severity']}")
    check("severity in 1..5", rep["severity"] in (1, 2, 3, 4, 5))
    check("hotspot score 0..100", rep["score"] is not None and 0 <= rep["score"] <= 100, str(rep["score"]))
    check("ticket drafted", bool(rep["ticket_title"] and rep["ticket_body"]), f"model={rep['ticket_model']}")

    r = c.get(f"/api/reports/{rid}/image")
    check("stored image served as JPEG", r.status_code == 200 and r.headers["content-type"] == "image/jpeg")

    r = c.post(f"/api/reports/{rid}/redraft", json={"lang": "hi"})
    check("redraft in Hindi", r.status_code == 200 and bool(r.json().get("title")), str(r.status_code))

    plan0 = c.post("/api/routes/plan").json()
    check("unapproved ticket is NOT routed", rid not in [i for rt in plan0["routes"] for i in rt["stop_ids"]])

    r = c.post(f"/api/reports/{rid}/approve", json={"official": OFFICIAL})
    check("approve -> 200", r.status_code == 200, str(r.status_code))
    r = c.post(f"/api/reports/{rid}/approve", json={"official": OFFICIAL})
    check("approve twice -> 409", r.status_code == 409, str(r.status_code))

    plan = c.post("/api/routes/plan").json()
    routed = [i for rt in plan["routes"] for i in rt["stop_ids"]]
    check("approved ticket appears in a route", rid in routed or rid in plan["dropped"], f"{plan['stops']} stops")
    check("capacity respected", all(rt["load"] <= rt["capacity"] for rt in plan["routes"]))
    check("optimised <= fixed route", plan["optimised_km"] <= plan["baseline_km"] + 0.01,
          f"{plan['optimised_km']} vs {plan['baseline_km']} km, source={plan['distance_source']}")

    r = c.post(f"/api/reports/{rid}/collected")
    check("mark collected -> 200", r.status_code == 200, str(r.status_code))
    listed = {x["id"]: x for x in c.get("/api/reports").json()}
    check("status now 'collected'", listed.get(rid, {}).get("status") == "collected")

    print("Reject path")
    r = c.post("/api/reports", files={"photo": ("cm017.jpg", img, "image/jpeg")}, data={"sector": 17})
    if r.status_code == 201:
        r2 = c.post(f"/api/reports/{r.json()['id']}/reject", json={"official": OFFICIAL, "note": "autotest"})
        check("reject -> 200", r2.status_code == 200, str(r2.status_code))
    else:
        check("second upload for reject test", False, str(r.status_code))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--offline", action="store_true", help="accept needs_review results (replay/template mode)")
    ap.add_argument("--no-pytest", action="store_true", help="skip the unit tests")
    args = ap.parse_args()

    run_api_checks(args.url.rstrip("/"), args.offline)

    if not args.no_pytest:
        print("Unit tests (pytest)")
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:warnings"], cwd=ROOT,
                           capture_output=True, text=True)
        tail = (p.stdout.strip().splitlines() or ["no output"])[-1]
        check("pytest suite", p.returncode == 0, tail)
        if p.returncode != 0:
            print(p.stdout[-1500:])

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for name, _, detail in failed:
        print(f"  FAILED: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

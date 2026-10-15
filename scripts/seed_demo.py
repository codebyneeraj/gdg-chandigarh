"""Seed the database with SYNTHETIC reports so the map and routes have content.

Every seeded row has synthetic=1 and is labelled "synthetic" in the UI. Tickets
use the offline templates (no API calls). Deterministic: same seed, same data.

Usage: python scripts/seed_demo.py [--reset] [--n 24]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saafroute.config import DATA_DIR, load_settings  # noqa: E402
from saafroute.db import DB  # noqa: E402
from saafroute.drafting import template_draft  # noqa: E402

KINDS = [  # (kind, waste_type, severity range, weight)
    ("overflowing_bin", "mixed", (2, 4), 8),
    ("overflowing_bin", "dry_recyclable", (1, 3), 3),
    ("illegal_dump", "mixed", (3, 5), 4),
    ("illegal_dump", "construction_debris", (3, 5), 3),
    ("illegal_dump", "bulky", (2, 3), 1),
    ("litter", "mixed", (1, 2), 3),
    ("illegal_dump", "e_waste", (1, 2), 1),
]
REASONS = {
    "overflowing_bin": "Synthetic record: bin full with bags piled around it.",
    "illegal_dump": "Synthetic record: waste pile dumped beside the road with no container.",
    "litter": "Synthetic record: scattered wrappers and bottles along the footpath.",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete ALL reports first")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    db = DB(load_settings().db_path)
    if args.reset:
        db.clear()
    sectors = json.loads((DATA_DIR / "sectors.json").read_text(encoding="utf-8"))["sectors"]
    rnd = random.Random(args.seed)
    now = datetime.now(timezone.utc)
    weights = [k[3] for k in KINDS]
    # A few "hotspot" sectors receive repeat reports close together.
    hot = rnd.sample(sectors, 3)
    for i in range(args.n):
        s = hot[i % 3] if i < 7 else rnd.choice(sectors)
        kind, waste, (lo, hi), _ = rnd.choices(KINDS, weights)[0]
        sev = rnd.randint(lo, hi)
        created = now - timedelta(hours=rnd.uniform(1, 70))
        spread = 0.0012 if i < 7 else 0.003
        lat, lon = s["lat"] + rnd.uniform(-spread, spread), s["lon"] + rnd.uniform(-spread, spread)
        lang = rnd.choice(["en", "en", "hi", "pa"])
        facts = {"sector": s["sector"], "kind": kind, "waste_type": waste, "severity": sev,
                 "reason": REASONS[kind]}
        d = template_draft(lang, facts)
        approved = i % 3 != 0  # about two-thirds pre-approved so routes can be planned immediately
        db.insert(created_at=created.isoformat(timespec="seconds"), sector=s["sector"], lat=lat, lon=lon,
                  gps_from_user=0, image_file=None, image_blurred=0, synthetic=1,
                  vision_status="ok", vision_model="synthetic", vision_error=None,
                  waste_type=waste, kind=kind, severity=sev, confidence=1.0, reason=REASONS[kind],
                  ticket_lang=lang, ticket_title=d.title, ticket_body=d.body, ticket_model="template (synthetic)",
                  status="approved" if approved else "pending_review")
        if approved:
            rid = db.all()[0]["id"]
            db.update(rid, reviewed_by="demo-seed", reviewed_at=created.isoformat(timespec="seconds"))
    print(f"seeded {args.n} synthetic reports into {db.path}")


if __name__ == "__main__":
    main()

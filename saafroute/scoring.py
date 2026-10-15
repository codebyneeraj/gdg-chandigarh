"""Deterministic, explainable hotspot score (0-100).

score = severity (0-50) + age (0-20) + repeats (0-20) + site sensitivity (0-10)

  severity   : 10 points per severity level (1..5) from the photo analysis
  age        : grows linearly while a report stays open, capped at 72 h
  repeats    : other open reports within 300 m in the last 7 days, 5 points each, capped at 4
  site       : sectors with hospitals or major markets/transit get 10, others 5
               (a configurable assumption, see SENSITIVE_SECTORS)

No model is involved: the same inputs always give the same score, and the
breakdown is shown to officials next to every ticket.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

REPEAT_RADIUS_M = 300
REPEAT_WINDOW = timedelta(days=7)
AGE_CAP_H = 72

# Sectors with large hospitals, the main market or the inter-state bus terminals.
# Assumption for the pilot; a municipality would maintain this list.
SENSITIVE_SECTORS = {
    12: "PGIMER hospital",
    16: "GMSH-16 hospital",
    17: "Sector 17 market & ISBT",
    22: "Sector 22 market",
    32: "GMCH-32 hospital",
    43: "ISBT-43",
}


@dataclass(frozen=True)
class ScoreBreakdown:
    severity: float
    age: float
    repeats: float
    site: float
    total: float
    repeat_count: int
    site_reason: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ReportPoint:
    id: int
    lat: float
    lon: float
    created_at: datetime
    open: bool


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def count_repeats(target: ReportPoint, others: list[ReportPoint]) -> int:
    return sum(
        1 for o in others
        if o.id != target.id and o.open
        and abs(o.created_at - target.created_at) <= REPEAT_WINDOW
        and haversine_m(target.lat, target.lon, o.lat, o.lon) <= REPEAT_RADIUS_M
    )


def hotspot_score(severity: int, created_at: datetime, now: datetime, repeat_count: int,
                  sector: int) -> ScoreBreakdown:
    if not 1 <= severity <= 5:
        raise ValueError("severity must be 1..5")
    age_h = max(0.0, (now - created_at).total_seconds() / 3600)
    s_sev = severity * 10.0
    s_age = round(20.0 * min(age_h, AGE_CAP_H) / AGE_CAP_H, 1)
    s_rep = 5.0 * min(repeat_count, 4)
    site_reason = SENSITIVE_SECTORS.get(sector, "")
    s_site = 10.0 if site_reason else 5.0
    total = round(s_sev + s_age + s_rep + s_site, 1)
    return ScoreBreakdown(s_sev, s_age, s_rep, s_site, total, repeat_count, site_reason or "standard sector")

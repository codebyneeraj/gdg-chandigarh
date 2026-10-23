import json
import random

from saafroute import routing
from saafroute.config import DATA_DIR
from saafroute.routing import Depot, Stop, Truck

SECTORS = json.loads((DATA_DIR / "sectors.json").read_text(encoding="utf-8"))["sectors"]
DEPOT = Depot("test depot", 30.7046, 76.8010)


def make_stops(n, seed=1, load=(1, 6)):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        s = rnd.choice(SECTORS)
        out.append(Stop(i + 1, s["lat"] + rnd.uniform(-0.002, 0.002), s["lon"] + rnd.uniform(-0.002, 0.002),
                        rnd.randint(*load), rnd.uniform(20, 100), s["sector"]))
    return out


def test_capacity_respected_and_all_served_when_feasible():
    trucks = [Truck("A", 20), Truck("B", 20), Truck("C", 20)]
    stops = make_stops(15, load=(1, 4))           # total load <= 60
    assert sum(s.load for s in stops) <= 60
    plan = routing.solve(DEPOT, trucks, stops)
    for r, t in zip(plan.routes, trucks):
        assert r.load <= t.capacity
    served = [i for r in plan.routes for i in r.stop_ids]
    assert sorted(served) == [s.id for s in stops]  # every stop exactly once
    assert plan.dropped == []


def test_overload_drops_lowest_score_stops():
    trucks = [Truck("A", 10)]
    stops = [Stop(1, 30.73, 76.78, 6, 90.0, 22), Stop(2, 30.74, 76.79, 6, 20.0, 17),
             Stop(3, 30.72, 76.77, 4, 80.0, 35)]
    plan = routing.solve(DEPOT, trucks, stops)
    assert plan.dropped == [2]                     # 6+6+4 > 10: the 20-point stop goes
    assert plan.routes[0].load <= 10


def test_optimised_never_worse_than_naive():
    trucks = [Truck("A", 20), Truck("B", 20), Truck("C", 20)]
    for seed in range(5):
        stops = make_stops(18, seed=seed, load=(1, 3))
        res = routing.compare(DEPOT, trucks, stops)
        assert res["optimised"].total_distance_m <= res["baseline"].total_distance_m
        base_served = sorted(i for r in res["baseline"].routes for i in r.stop_ids)
        opt_served = sorted(i for r in res["optimised"].routes for i in r.stop_ids)
        assert base_served == opt_served           # same stops, fair comparison


def test_empty():
    plan = routing.solve(DEPOT, [Truck("A", 10)], [])
    assert plan.total_distance_m == 0 and plan.dropped == []


def test_load_units():
    assert routing.load_units("clean", 3) == 0
    assert routing.load_units("litter", 5) == 1
    assert routing.load_units("overflowing_bin", 3) == 3
    assert routing.load_units("illegal_dump", 3) == 6


def test_sector_data_is_chandigarh():
    assert len(SECTORS) == 55 and 13 not in {s["sector"] for s in SECTORS}
    for s in SECTORS:
        assert 30.66 < s["lat"] < 30.78 and 76.71 < s["lon"] < 76.83


def test_naive_baseline_serves_every_stop_even_when_tight():
    trucks = [Truck("A", 20), Truck("B", 20)]
    stops = make_stops(14, seed=3, load=(2, 6))
    base = routing.naive_plan(DEPOT, trucks, stops)
    assert sorted(i for r in base.routes for i in r.stop_ids) == [s.id for s in stops]
    assert base.dropped == []


def test_optimised_beats_naive_on_demo_like_load():
    trucks = [Truck("T1", 20), Truck("T2", 20), Truck("T3", 20)]
    for seed in range(5):
        res = routing.compare(DEPOT, trucks, make_stops(16, seed=seed, load=(1, 8)))
        assert res["optimised"].total_distance_m <= res["baseline"].total_distance_m


def _fake_osrm(monkeypatch, fail=False):
    """Stand-in for the OSRM /table call: 2x the straight-line distance."""
    calls = []

    class Resp:
        def __init__(self, pts):
            self.pts = pts

        def raise_for_status(self):
            if fail:
                raise RuntimeError("503")

        def json(self):
            from saafroute.scoring import haversine_m
            return {"code": "Ok", "distances": [[haversine_m(a[0], a[1], b[0], b[1]) * 2 for b in self.pts]
                                                for a in self.pts]}

    def get(url, params=None, timeout=None):
        coords = url.split("/driving/")[1].split(";")
        calls.append(url)
        return Resp([tuple(map(float, c.split(",")))[::-1] for c in coords])

    monkeypatch.setattr(routing.httpx, "get", get)
    monkeypatch.setenv("SAAFROUTE_OSRM_URL", "http://osrm.test")
    return calls


def test_osrm_matrix_used_and_cached(monkeypatch):
    calls = _fake_osrm(monkeypatch)
    pts = [(DEPOT.lat, DEPOT.lon), (30.71, 76.80), (30.72, 76.79)]
    m, src = routing.distance_matrix_with_source(pts)
    assert src == "osrm" and m[0][0] == 0 and m[0][1] > 0
    routing.distance_matrix_with_source(pts)
    assert len(calls) == 1  # second call served from cache


def test_falls_back_to_haversine_when_osrm_fails(monkeypatch):
    calls = _fake_osrm(monkeypatch, fail=True)
    pts = [(DEPOT.lat, DEPOT.lon), (30.71, 76.80)]
    m, src = routing.distance_matrix_with_source(pts)
    assert src == "haversine" and m[0][1] > 0
    routing.distance_matrix_with_source(pts)
    assert len(calls) == 1  # cooldown: no repeated slow attempts


def test_compare_reports_source_and_uses_one_matrix(monkeypatch):
    _fake_osrm(monkeypatch)
    trucks = [Truck("A", 20), Truck("B", 20), Truck("C", 20)]
    res = routing.compare(DEPOT, trucks, make_stops(10, load=(1, 4)))
    assert res["source"] == "osrm" and res["optimised"].source == res["baseline"].source == "osrm"
    assert "OpenStreetMap" in res["note"]
    assert res["saved_m"] >= 0

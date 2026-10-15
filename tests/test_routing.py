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

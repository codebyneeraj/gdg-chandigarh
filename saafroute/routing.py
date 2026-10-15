"""Capacitated vehicle routing (OR-Tools) plus a naive fixed-order baseline.

Distances: straight-line (haversine) x ROAD_FACTOR. Chandigarh's roads form a
grid, and on a grid the average ratio of travel to straight-line distance is
4/pi ~ 1.27, so we use 1.3. This is an approximation; a road-network matrix
(e.g. OSRM on OpenStreetMap) can be dropped into `distance_matrix` later.

All fleet data is synthetic (data/fleet.json).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from .scoring import haversine_m

ROAD_FACTOR = 1.3
DROP_PENALTY_BASE = 1_000_000  # metres-equivalent: dropping a stop is far worse than any detour


@dataclass(frozen=True)
class Stop:
    id: int
    lat: float
    lon: float
    load: int
    score: float
    sector: int


@dataclass(frozen=True)
class Truck:
    id: str
    capacity: int


@dataclass(frozen=True)
class Depot:
    name: str
    lat: float
    lon: float


@dataclass
class Route:
    truck: str
    stop_ids: list[int]
    load: int
    distance_m: int
    path: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Plan:
    routes: list[Route]
    dropped: list[int]
    total_distance_m: int


def load_units(kind: str, severity: int) -> int:
    """Truck capacity units a ticket consumes (assumption, documented in README)."""
    if kind == "clean":
        return 0
    if kind == "litter":
        return 1
    if kind == "illegal_dump":
        return 2 * severity
    return severity  # overflowing_bin


def load_fleet(path: Path) -> tuple[Depot, list[Truck]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    d = raw["depot"]
    return Depot(d["name"], d["lat"], d["lon"]), [Truck(t["id"], t["capacity"]) for t in raw["trucks"]]


def distance_matrix(points: list[tuple[float, float]]) -> list[list[int]]:
    return [[int(round(haversine_m(a[0], a[1], b[0], b[1]) * ROAD_FACTOR)) for b in points] for a in points]


def _route_distance(matrix: list[list[int]], nodes: list[int]) -> int:
    seq = [0, *nodes, 0]
    return sum(matrix[a][b] for a, b in zip(seq, seq[1:]))


def solve(depot: Depot, trucks: list[Truck], stops: list[Stop], time_limit_s: int = 3) -> Plan:
    """Minimise total distance subject to truck capacity.

    If total load exceeds fleet capacity, the lowest-score stops are dropped
    (penalty grows with score) and reported in `dropped`.
    """
    if not stops:
        return Plan([Route(t.id, [], 0, 0) for t in trucks], [], 0)
    pts = [(depot.lat, depot.lon)] + [(s.lat, s.lon) for s in stops]
    matrix = distance_matrix(pts)
    manager = pywrapcp.RoutingIndexManager(len(pts), len(trucks), 0)
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(i: int, j: int) -> int:
        return matrix[manager.IndexToNode(i)][manager.IndexToNode(j)]

    transit = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit)

    demands = [0] + [s.load for s in stops]

    def demand_cb(i: int) -> int:
        return demands[manager.IndexToNode(i)]

    demand_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(demand_idx, 0, [t.capacity for t in trucks], True, "Load")

    for node, s in enumerate(stops, start=1):
        routing.AddDisjunction([manager.NodeToIndex(node)], DROP_PENALTY_BASE + int(s.score * 10_000))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = time_limit_s
    sol = routing.SolveWithParameters(params)
    if sol is None:
        raise RuntimeError("routing solver found no solution")

    routes, served = [], set()
    for v, truck in enumerate(trucks):
        idx, nodes = routing.Start(v), []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node:
                nodes.append(node)
            idx = sol.Value(routing.NextVar(idx))
        served.update(nodes)
        routes.append(Route(
            truck=truck.id,
            stop_ids=[stops[n - 1].id for n in nodes],
            load=sum(demands[n] for n in nodes),
            distance_m=_route_distance(matrix, nodes),
            path=[pts[0], *[pts[n] for n in nodes], pts[0]] if nodes else [],
        ))
    dropped = [stops[n - 1].id for n in range(1, len(pts)) if n not in served]
    return Plan(routes, dropped, sum(r.distance_m for r in routes))


def naive_plan(depot: Depot, trucks: list[Truck], stops: list[Stop]) -> Plan:
    """Baseline: a fixed schedule that sweeps stops in sector-number order.

    Stops are split into depot-to-depot trips in that order; a new trip starts
    when the next stop would exceed capacity. Trips are dealt round-robin to the
    trucks, so a truck may make more than one trip. Every stop is served, with the
    same capacities and distance function as `solve`, so the comparison is fair.
    """
    pts = [(depot.lat, depot.lon)] + [(s.lat, s.lon) for s in stops]
    matrix = distance_matrix(pts)
    order = sorted(range(1, len(pts)), key=lambda n: (stops[n - 1].sector, stops[n - 1].id))
    cap = min(t.capacity for t in trucks)
    trips: list[list[int]] = [[]]
    load = 0
    for n in order:
        if trips[-1] and load + stops[n - 1].load > cap:
            trips.append([])
            load = 0
        trips[-1].append(n)
        load += stops[n - 1].load
    routes = [Route(t.id, [], 0, 0) for t in trucks]
    for i, trip in enumerate(t for t in trips if t):
        r = routes[i % len(trucks)]
        r.stop_ids += [stops[n - 1].id for n in trip]
        r.load += sum(stops[n - 1].load for n in trip)
        r.distance_m += _route_distance(matrix, trip)
        r.path += [pts[0], *[pts[n] for n in trip], pts[0]]
    return Plan(routes, [], sum(r.distance_m for r in routes))


def compare(depot: Depot, trucks: list[Truck], stops: list[Stop]) -> dict:
    """Optimised plan vs naive plan over the *same* set of served stops."""
    opt = solve(depot, trucks, stops)
    served = [s for s in stops if s.id not in set(opt.dropped)]
    base = naive_plan(depot, trucks, served)
    saved = base.total_distance_m - opt.total_distance_m
    pct = round(100 * saved / base.total_distance_m, 1) if base.total_distance_m else 0.0
    return {
        "optimised": opt,
        "baseline": base,
        "saved_m": saved,
        "saved_pct": pct,
        "note": "SIMULATION on synthetic fleet data; distances are straight-line x 1.3, not measured road km.",
    }


def km(m: int) -> float:
    return math.floor(m / 10) / 100

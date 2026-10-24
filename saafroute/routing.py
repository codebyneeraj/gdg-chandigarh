"""Capacitated vehicle routing (OR-Tools) plus a naive fixed-order baseline.

Distances: real driving distances from OSRM (OpenStreetMap road network) when
the server answers. Otherwise straight-line (haversine) x ROAD_FACTOR:
Chandigarh's roads form a grid, and on a grid the average ratio of travel to
straight-line distance is 4/pi ~ 1.27, so we use 1.3. Set SAAFROUTE_OSRM_URL to
a self-hosted server, or to "off" to force the straight-line fallback. The
method actually used is reported in `Plan.source` and in the compare() note.

All fleet data is synthetic (data/fleet.json).
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from .scoring import haversine_m

log = logging.getLogger("saafroute")

ROAD_FACTOR = 1.3
OSRM_DEFAULT_URL = "https://router.project-osrm.org"  # public demo server: rate-limited, demo use only
OSRM_TIMEOUT_S = 5.0
OSRM_MAX_POINTS = 100  # public server's table-size limit
OSRM_RETRY_AFTER_S = 60  # after a failure, skip OSRM this long so re-plans stay fast
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
    source: str = "haversine"  # "osrm" (road network) or "haversine" (straight-line x ROAD_FACTOR)


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


_cache: dict[tuple, tuple[list[list[int]], str]] = {}
_osrm_failed_at = 0.0


def _osrm_url() -> str:
    return os.environ.get("SAAFROUTE_OSRM_URL", OSRM_DEFAULT_URL).strip().rstrip("/")


def _haversine_matrix(points: list[tuple[float, float]]) -> list[list[int]]:
    return [[int(round(haversine_m(a[0], a[1], b[0], b[1]) * ROAD_FACTOR)) for b in points] for a in points]


def _osrm_matrix(points: list[tuple[float, float]], base: str) -> list[list[int]]:
    """Driving distances in metres from OSRM's /table service. Raises on any problem."""
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    r = httpx.get(f"{base}/table/v1/driving/{coords}", params={"annotations": "distance"}, timeout=OSRM_TIMEOUT_S)
    r.raise_for_status()
    body = r.json()
    rows = body.get("distances")
    if body.get("code") != "Ok" or not rows or len(rows) != len(points):
        raise ValueError(f"unexpected OSRM reply: {body.get('code')}")
    out = []
    for row in rows:
        if len(row) != len(points) or any(d is None for d in row):
            raise ValueError("OSRM could not route between some points")
        out.append([int(round(d)) for d in row])
    return out


def distance_matrix_with_source(points: list[tuple[float, float]]) -> tuple[list[list[int]], str]:
    """(matrix in metres, "osrm" | "haversine"). Results are cached per set of points."""
    global _osrm_failed_at
    key = tuple((round(lat, 5), round(lon, 5)) for lat, lon in points)
    if key in _cache:
        return _cache[key]
    base = _osrm_url()
    in_cooldown = time.monotonic() - _osrm_failed_at < OSRM_RETRY_AFTER_S
    if base.lower() not in ("", "off", "none") and len(points) <= OSRM_MAX_POINTS and not in_cooldown:
        try:
            result = (_osrm_matrix(points, base), "osrm")
        except Exception as exc:
            _osrm_failed_at = time.monotonic()
            log.warning("OSRM unavailable (%s); using straight-line x %.1f distances", exc, ROAD_FACTOR)
        else:
            if len(_cache) > 64:
                _cache.clear()
            _cache[key] = result
            return result
    return _haversine_matrix(points), "haversine"  # not cached, so a recovered OSRM is picked up


def distance_matrix(points: list[tuple[float, float]]) -> list[list[int]]:
    return distance_matrix_with_source(points)[0]


def _route_distance(matrix: list[list[int]], nodes: list[int]) -> int:
    seq = [0, *nodes, 0]
    return sum(matrix[a][b] for a, b in zip(seq, seq[1:]))


def solve(depot: Depot, trucks: list[Truck], stops: list[Stop], time_limit_s: int = 3,
          matrix: list[list[int]] | None = None, source: str | None = None) -> Plan:
    """Minimise total distance subject to truck capacity.

    If total load exceeds fleet capacity, the lowest-score stops are dropped
    (penalty grows with score) and reported in `dropped`. `matrix` (depot first,
    then `stops`) may be passed in so a comparison uses one distance table.
    """
    if not stops:
        return Plan([Route(t.id, [], 0, 0) for t in trucks], [], 0)
    pts = [(depot.lat, depot.lon)] + [(s.lat, s.lon) for s in stops]
    if matrix is None:
        matrix, source = distance_matrix_with_source(pts)
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
    return Plan(routes, dropped, sum(r.distance_m for r in routes), source or "haversine")


def naive_plan(depot: Depot, trucks: list[Truck], stops: list[Stop],
               matrix: list[list[int]] | None = None, source: str | None = None) -> Plan:
    """Baseline: a fixed schedule that sweeps stops in sector-number order.

    Stops are split into depot-to-depot trips in that order; a new trip starts
    when the next stop would exceed capacity. Trips are dealt round-robin to the
    trucks, so a truck may make more than one trip. Every stop is served, with the
    same capacities and distance function as `solve`, so the comparison is fair.
    """
    pts = [(depot.lat, depot.lon)] + [(s.lat, s.lon) for s in stops]
    if matrix is None:
        matrix, source = distance_matrix_with_source(pts)
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
    return Plan(routes, [], sum(r.distance_m for r in routes), source or "haversine")


def compare(depot: Depot, trucks: list[Truck], stops: list[Stop]) -> dict:
    """Optimised plan vs naive plan over the *same* set of served stops.

    Both plans use one distance table, so the comparison is always like for like.
    """
    pts = [(depot.lat, depot.lon)] + [(s.lat, s.lon) for s in stops]
    full, source = distance_matrix_with_source(pts)
    opt = solve(depot, trucks, stops, matrix=full, source=source)
    dropped = set(opt.dropped)
    keep = [0] + [i for i, s in enumerate(stops, start=1) if s.id not in dropped]
    served = [stops[i - 1] for i in keep[1:]]
    sub = [[full[a][b] for b in keep] for a in keep]
    base = naive_plan(depot, trucks, served, matrix=sub, source=source)
    saved = base.total_distance_m - opt.total_distance_m
    pct = round(100 * saved / base.total_distance_m, 1) if base.total_distance_m else 0.0
    how = ("road distances from OpenStreetMap (OSRM)" if source == "osrm"
           else f"straight-line x {ROAD_FACTOR}, not road distances")
    return {
        "optimised": opt,
        "baseline": base,
        "saved_m": saved,
        "saved_pct": pct,
        "source": source,
        "note": f"SIMULATION on synthetic fleet data; distances are {how}.",
    }


_geom_cache: dict[tuple, list[tuple[float, float]]] = {}


def road_geometry(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drive-along-the-roads polyline through `points`, from OSRM's /route service.

    Display only: the solver never sees it. Falls back to the straight segments
    between the points if OSRM is off, unreachable or in cooldown.
    """
    global _osrm_failed_at
    if len(points) < 2:
        return list(points)
    key = tuple((round(lat, 5), round(lon, 5)) for lat, lon in points)
    if key in _geom_cache:
        return _geom_cache[key]
    base = _osrm_url()
    if base.lower() in ("", "off", "none") or time.monotonic() - _osrm_failed_at < OSRM_RETRY_AFTER_S:
        return list(points)
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    try:
        r = httpx.get(f"{base}/route/v1/driving/{coords}",
                      params={"overview": "full", "geometries": "geojson"}, timeout=OSRM_TIMEOUT_S)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != "Ok" or not body.get("routes"):
            raise ValueError(f"unexpected OSRM reply: {body.get('code')}")
        line = [(lat, lon) for lon, lat in body["routes"][0]["geometry"]["coordinates"]]
    except Exception as exc:
        _osrm_failed_at = time.monotonic()
        log.warning("OSRM route geometry unavailable (%s); drawing straight segments", exc)
        return list(points)
    if len(_geom_cache) > 64:
        _geom_cache.clear()
    _geom_cache[key] = line
    return line


def km(m: int) -> float:
    return math.floor(m / 10) / 100

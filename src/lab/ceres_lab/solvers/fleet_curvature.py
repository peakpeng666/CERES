"""Curvature-constrained route realisation for fleet routing.

Split out of ``solvers/fleet.py`` (2026-10-17): the analytic waypoint realisation with
discretised-heading dynamic programming (Le Ny et al. 2007; Cohen & Epstein 2017), corner
relaxation, sampled curvature verification, and the OMPL RRT-Connect fallback that can
thread gaps narrower than the turn diameter. Behaviour is unchanged by the move; the
solvers re-export these names for compatibility.
"""
from __future__ import annotations

import math

def _circumradius(a, b, c):
    """Sampled circle radius; a cusp/reversal is not a straight forward segment."""
    A, B, C = math.dist(a, b), math.dist(b, c), math.dist(a, c)
    area = abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2
    if area <= 1e-12 and (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) < 0:
        return 0.0  # Collinear reversal, including return to the previous sample.
    return A * B * C / (4 * area) if area > 1e-12 else math.inf



def min_curvature_radius(pts):
    """Smallest sampled turning radius of a polyline (consecutive triplets; exact on circular arcs)."""
    clean = []
    for p in pts:
        if not clean or math.dist(clean[-1], p) > 1e-10:
            clean.append(p)
    return min((_circumradius(clean[i], clean[i + 1], clean[i + 2]) for i in range(len(clean) - 2)), default=math.inf)



def relax_corners(waypoints, stop_indices, amount):
    """Push non-stop corner waypoints outward along the external angle bisector.

    Corner waypoints sit on the inflated obstacle boundary; a bounded-curvature turn at such a
    corner can bow into the margin. Shifting the corner into free space gives the arc room.
    Stop waypoints (tasks, depots) never move; the list length is preserved."""
    if amount <= 0:
        return waypoints
    out = [list(p) for p in waypoints]
    stops = set(stop_indices)
    for i in range(1, len(out) - 1):
        if i in stops:
            continue
        ax, ay = out[i - 1]
        bx, by = out[i]
        cx, cy = out[i + 1]
        uin = (bx - ax, by - ay)
        uout = (cx - bx, cy - by)
        lin, lout = math.hypot(*uin), math.hypot(*uout)
        if lin < 1e-9 or lout < 1e-9:
            continue
        uin, uout = (uin[0] / lin, uin[1] / lin), (uout[0] / lout, uout[1] / lout)
        bx_, by_ = uin[0] + uout[0], uin[1] + uout[1]
        norm = math.hypot(bx_, by_)
        if norm > 1e-6:
            bx_, by_ = bx_ / norm, by_ / norm
        else:                                   # ~180° reversal: step to the outgoing side
            bx_, by_ = -uin[1], uin[0]
            if bx_ * uout[0] + by_ * uout[1] < 0:
                bx_, by_ = -bx_, -by_
        out[i] = [bx - amount * bx_, by - amount * by_]
    return out



def dubins_realize(waypoints, min_radius, step=0.05, heading_candidates=12):
    """Curvature-bounded realisation of a waypoint sequence (Dubins, 1957; OMPL implementation).

    Headings at the waypoints are chosen by dynamic programming over a discretised set
    (Le Ny, Frazzoli et al.; Cohen & Epstein 2017 — the standard discretised-DTSP scheme):
    each leg cost is the exact two-point Dubins length, minimised over the heading choices;
    the greedy carried-heading variant is available with heading_candidates=0. Returns
    (dense polyline at ≈ `step` spacing, per-waypoint index in it)."""
    from ompl import base as ob, geometric as og
    clean, origin = [], []
    for p in waypoints:
        if not clean or math.dist(p, clean[-1]) > 1e-9:
            clean.append(list(p))
        origin.append(len(clean) - 1)
    space = ob.DubinsStateSpace(float(min_radius))
    si = ob.SpaceInformation(space)

    def pose(p, yaw):
        st = space.allocState()
        st.setXY(*p)
        st.setYaw(yaw)
        return st

    def leg_len(a, ya, b, yb):
        return float(space.distance(pose(a, ya), pose(b, yb)))

    if heading_candidates and len(clean) > 2:
        seg = [math.atan2(b[1] - a[1], b[0] - a[0]) for a, b in zip(clean, clean[1:])]
        grid = [2 * math.pi * k / heading_candidates for k in range(heading_candidates)]
        # per waypoint: incoming and outgoing segment directions plus the uniform grid
        cand = [[seg[0]] + grid]
        for i in range(1, len(clean) - 1):
            cand.append([seg[i - 1], seg[i]] + grid)
        cand.append([seg[-1]] + grid)
        # DP forward: state = heading index at waypoint i
        INF = math.inf
        dp = [[INF] * len(cand[i]) for i in range(len(clean))]
        back = [[None] * len(cand[i]) for i in range(len(clean))]
        for h in range(len(cand[0])):
            dp[0][h] = 0.0                       # start heading is free (vehicle departs parked)
        for i in range(1, len(clean)):
            for hb in range(len(cand[i])):
                for ha in range(len(cand[i - 1])):
                    if dp[i - 1][ha] == INF:
                        continue
                    c = dp[i - 1][ha] + leg_len(clean[i - 1], cand[i - 1][ha], clean[i], cand[i][hb])
                    if c < dp[i][hb]:
                        dp[i][hb], back[i][hb] = c, ha
        thetas, hb = [], min(range(len(cand[-1])), key=lambda k: dp[-1][k])
        for i in range(len(clean) - 1, 0, -1):
            thetas.append(cand[i][hb])
            hb = back[i][hb]
        thetas.append(cand[0][hb])
        chosen = list(reversed(thetas))
    else:
        chosen = None

    pts, marks = [list(clean[0])], [0]
    theta = math.atan2(clean[1][1] - clean[0][1], clean[1][0] - clean[0][0]) if chosen is None else chosen[0]
    for idx, (a, b) in enumerate(zip(clean, clean[1:])):
        heading = math.atan2(b[1] - a[1], b[0] - a[0]) if chosen is None else chosen[idx + 1]
        s0, s1 = pose(a, theta), pose(b, heading)
        leg = og.PathGeometric(si, s0, s1)
        leg.interpolate(max(2, int(leg.length() / step) + 1))
        pts += [[s.getX(), s.getY()] for s in leg.getStates()[1:]]
        marks.append(len(pts) - 1)
        theta = heading
    return pts, [marks[origin[i]] for i in range(len(waypoints))]



def ompl_dubins_fallback(inst, stops_xy, min_radius, budget_s=0.5):
    """Curvature-constrained realisation among obstacles by sampling (OMPL RRT-Connect on
    DubinsStateSpace): the analytic waypoint realisation cannot weave compound S-maneouvres
    through gaps narrower than the turn diameter, so when it fails clearance this planner
    searches the free space directly. Headings at the stops are free (goal yaw set).
    Returns the dense polyline through the stops, or None if a leg finds no path."""
    import shapely
    from shapely.geometry import Point, Polygon
    from shapely.prepared import prep
    from ompl import base as ob, geometric as og, util as ou
    ou.setLogLevel(ou.LogLevel.LOG_NONE if hasattr(ou.LogLevel, 'LOG_NONE') else ou.LogLevel.LOG_ERROR)
    ou.RNG.setSeed(42)
    radius = float(inst['robot_radius'])
    boundary = Polygon(inst['boundary'])
    obstacles = [Polygon(o['polygon']) for o in inst.get('obstacles', [])]
    solids = shapely.union_all(obstacles) if obstacles else None
    blocked = prep(solids.buffer(radius - 1e-6)) if solids is not None else None
    xmin, ymin, xmax, ymax = boundary.bounds

    def valid(x, y):
        p = Point(x, y)
        return boundary.covers(p) and p.distance(boundary.boundary) >= radius - 1e-6 \
            and (blocked is None or not blocked.covers(p))

    space = ob.DubinsStateSpace(float(min_radius))
    b = ob.RealVectorBounds(2)
    b.setLow(0, xmin - 1); b.setHigh(0, xmax + 1); b.setLow(1, ymin - 1); b.setHigh(1, ymax + 1)
    space.setBounds(b)
    si = ob.SpaceInformation(space)
    si.setStateValidityChecker(lambda st: valid(st.getX(), st.getY()))
    si.setStateValidityCheckingResolution(0.01)
    si.setup()
    pts, yaw = [list(stops_xy[0])], math.atan2(stops_xy[1][1] - stops_xy[0][1], stops_xy[1][0] - stops_xy[0][0])
    for target in stops_xy[1:]:
        start = space.allocState(); start.setXY(*pts[-1]); start.setYaw(yaw)
        # free heading at the stop: try a discretised goal set, each with the proven
        # setStartAndGoalStates pattern (the GoalStates variant only yields approximate
        # solutions with these bindings)
        for k in range(12):
            goal = space.allocState(); goal.setXY(*target)
            goal.setYaw(math.atan2(math.sin(2 * math.pi * k / 12), math.cos(2 * math.pi * k / 12)))
            ss = og.SimpleSetup(si)
            ss.setStartAndGoalStates(start, goal, 1e-3)
            ss.setPlanner(og.RRTConnect(si))
            ss.setup()
            if ss.solve(max(0.3, budget_s / 12)) and ss.haveExactSolutionPath():
                path = ss.getSolutionPath()
                path.interpolate(max(2, int(path.length() / 0.05) + 1))   # 5 cm sampling: curvature is exact on Dubins arcs
                pts += [[st.getX(), st.getY()] for st in path.getStates()[1:]]
                yaw = path.getStates()[-1].getYaw()
                break
        else:
            return None
    return pts

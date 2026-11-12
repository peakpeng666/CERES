"""Field simulation path follower; pure Python, shared by ROS and regression tests."""
from __future__ import annotations
import bisect
import math
from .path_geometry import locate

TASK_RADIUS = 0.45          # m: a task counts as reached inside this radius ...
CORNER_RADIUS = 0.2         # m: ... or this one where the route turns sharply at the stop
LOOKAHEAD = 1.4             # m
TIGHT_LOOKAHEAD = 0.7       # m: near obstacles/boundary the follower must not cut corners into the margin
HAZARD_WINDOW = 2.2         # m: tighten this far ahead of a hazard-marked path section
CORNER = 0.6                # rad: path turns sharper than this are driven to and turned in place
CORNER_PASS = 0.15          # m: a corner counts as reached this close (along the path)
PROGRESS_M = 2.0            # m: progress and cross-track error look this far along the path around the progress
MAX_CONTROL_POSE_AGE_S = 0.10  # source simulation time; never drive from a delayed pose


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def truth_time(header_stamp, clock):
    """Fortress Pose_V may leave each bridged TransformStamped header at zero.

    In that case use the latest received simulation clock, as the trajectory
    recorder does. This is a receive-time estimate, not a fabricated source stamp.
    """
    return header_stamp if header_stamp > 0 else clock


class Driver:
    """Pure pursuit along one vehicle's planned path, stopping at each of its tasks."""

    def __init__(self, v: dict, speed: float):
        self.name, self.label, self.color = v['name'], v['label'], v['color']
        self.path = [tuple(p) for p in v['path']]
        self.cum = [0.0]
        for a, b in zip(self.path, self.path[1:]):
            self.cum.append(self.cum[-1] + math.dist(a, b))
        self.length = self.cum[-1]
        self.speed = speed
        # path arc lengths flagged by the planner as running close to obstacles/crop/boundary
        self.hazard_s = sorted(v.get('hazard_s') or [])
        # planned speed caps [[s0, s1, v_limit], ...] from the declared ground map
        self.speed_profile = sorted([tuple(p) for p in v.get('speed_profile') or [] if p[2] > 0])
        # each task is served at its stop on the route (a lane or headland point; the task itself with free travel)
        self.tasks = [dict(t, goal=tuple(t.get('stop') or t['xy']), state='pending', t_sim=None, distance_m=None) for t in v['tasks']]
        # Explicit occurrences prevent a later visit to the same place being served early.
        previous = 0.0
        for t in self.tasks:
            at = t.get('path_s')
            if at is None:
                at = locate(self.path, t['goal'], previous)
            if (at is None or not math.isfinite(at) or at < previous - 1e-5 or at > self.length + 1e-5
                    or math.dist(self.point_at(at), t['goal']) > 1e-4):
                raise ValueError(f"任务 {t['id']} 缺少按顺序可达的路径位置")
            t['s'] = previous = at
            t['serviced_at'] = None
        # arc length of every sharp corner (including U-turns where the route doubles back)
        self.corners = []
        for i in range(1, len(self.path) - 1):
            (ax, ay), (bx, by), (cx, cy) = self.path[i - 1], self.path[i], self.path[i + 1]
            turn = abs(wrap(math.atan2(cy - by, cx - bx) - math.atan2(by - ay, bx - ax)))
            if turn > CORNER:
                self.corners.append(self.cum[i])
        for t in self.tasks:  # where the route turns at a stop, drive onto it before turning
            t['radius'] = CORNER_RADIUS if any(abs(c - t['s']) < 0.05 for c in self.corners) else TASK_RADIUS
        self.servicing_task = None
        self.strict_service = False
        self.stationary_since = None
        self.stationary_confirmation_s = 1.0
        self.terminal_radius = .35
        self.failure = None
        self.samples = []
        self.s = 0.0
        self.state = 'waiting'
        self.pose = None
        self.dwell_until = None
        self.trail: list[list[float]] = []
        self.distance = 0.0
        self.cte: list[float] = []
        self.t_start = self.t_done = None
        self.t_depart = None
        self.v_cmd = self.w_cmd = 0.0
        self.speed_now = 0.0
        self.angular_speed_now = 0.0
        self.pose_stamp = None
        self.last_progress = (0.0, None)

    def observe(self, pose, stamp, record_distance=True):
        """Velocity follows fresh truth timestamps, even after the controller stops."""
        if not all(math.isfinite(x) for x in (*pose, stamp)):
            raise RuntimeError(f'{self.name} 真值含非有限坐标或时间')
        if self.pose_stamp is not None and stamp < self.pose_stamp:
            raise RuntimeError(f'{self.name} 真值时间倒退')
        if self.pose is not None and self.pose_stamp is not None and stamp > self.pose_stamp:
            distance = math.dist(self.pose[:2], pose[:2])
            dt = stamp - self.pose_stamp
            self.speed_now = distance / dt
            self.angular_speed_now = abs(wrap(pose[2] - self.pose[2])) / dt
            if record_distance and distance < 2.0:
                self.distance += distance
        self.pose, self.pose_stamp = pose, stamp

    def point_at(self, s):
        s = max(0.0, min(self.length, s))
        i = max(1, min(len(self.cum) - 1, bisect.bisect_left(self.cum, s)))
        a, b = self.path[i - 1], self.path[i]
        seg = self.cum[i] - self.cum[i - 1]
        u = 0.0 if seg < 1e-9 else (s - self.cum[i - 1]) / seg
        return a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u

    def project(self, p):
        """Progress: arc length of the closest path point within PROGRESS_M ahead of the current progress (only
        the path from the progress on counts, so where the route doubles back — a U-turn in a lane — the way out
        wins, and a later visit to the same spot is never mistaken for progress). Cross-track error: distance
        to the path within PROGRESS_M either side of the progress."""
        best_s, best_d, cte = self.s, math.inf, math.inf
        s0, s1 = self.s - PROGRESS_M, self.s + PROGRESS_M
        lo = max(1, bisect.bisect_right(self.cum, s0))
        hi = min(len(self.path) - 1, bisect.bisect_left(self.cum, s1))
        for i in range(lo, hi + 1):
            a, b = self.path[i - 1], self.path[i]
            dx, dy = b[0] - a[0], b[1] - a[1]
            seg = math.hypot(dx, dy)
            if seg < 1e-9:
                continue
            u_lo, u_hi = max(0.0, (s0 - self.cum[i - 1]) / seg), min(1.0, (s1 - self.cum[i - 1]) / seg)
            u = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (seg * seg)
            uc = max(u_lo, min(u_hi, u))
            cte = min(cte, math.hypot(p[0] - a[0] - uc * dx, p[1] - a[1] - uc * dy))
            f_lo = max(u_lo, (self.s - self.cum[i - 1]) / seg)
            if f_lo > u_hi:
                continue
            uf = max(f_lo, min(u_hi, u))
            d = math.hypot(p[0] - a[0] - uf * dx, p[1] - a[1] - uf * dy)
            if d < best_d - 1e-6:  # on overlapping legs (way in, way back) the earlier one wins ties
                best_s, best_d = self.cum[i - 1] + uf * seg, d
        return best_s, cte

    def _speed_cap(self, window=0.8):
        """Planned cap over the next `window` metres of arc length (pre-entry slowdown)."""
        cap = math.inf
        for s0, s1, vmax in self.speed_profile:
            if s1 < self.s - 1e-6 or s0 > self.s + window:
                continue
            cap = min(cap, vmax)
        return cap

    def next_corner(self):
        i = bisect.bisect_right(self.corners, self.s + CORNER_PASS)
        return self.corners[i] if i < len(self.corners) else math.inf

    def turning(self):
        """Just past a sharp corner (or about to leave one): finish turning in place before driving on."""
        i = bisect.bisect_left(self.corners, self.s - 0.6)
        return i < len(self.corners) and self.corners[i] <= self.s + CORNER_PASS

    def next_task(self):
        return next((t for t in self.tasks if t['state'] == 'pending'), None)

    def opens_at(self, task, t_sim):
        """A task with an earliest time holds the vehicle at the stop until then; the clock starts when the fleet is released."""
        return max(t_sim, (self.t_start if self.t_start is not None else t_sim) + float(task.get('earliest_s') or 0))

    def step(self, t_sim: float):
        """Returns (v, w) for this control tick."""
        if self.pose is None or self.state in ('done', 'failed', 'waiting'):
            return 0.0, 0.0
        if self.pose_stamp is not None and abs(t_sim - self.pose_stamp) > MAX_CONTROL_POSE_AGE_S:
            # A live callback is not necessarily a current pose: queued DDS samples can
            # be seconds behind /clock. Hold without crediting progress or service.
            return 0.0, 0.0
        x, y, yaw = self.pose
        if self.t_start is None:
            self.t_start = t_sim
        if self.state == 'servicing':
            if self.strict_service:
                if self.speed_now >= .02 or self.angular_speed_now >= .05:
                    self.stationary_since = None
                    self.servicing_task['service_started_at'] = None
                    self.dwell_until = self.opens_at(self.servicing_task, t_sim) + self.servicing_task['service_s']
                    return 0.0, 0.0
                if self.stationary_since is None:
                    self.stationary_since = t_sim
                # Wheel stop alone cannot establish body stop under slip. Wait for
                # a continuous quiet interval before counting work; the independent
                # truth gate still rejects sliding throughout any credited dwell.
                if t_sim - self.stationary_since < self.stationary_confirmation_s:
                    return 0.0, 0.0
                if self.servicing_task.get('service_started_at') is None:
                    begin = self.opens_at(self.servicing_task, t_sim)
                    self.servicing_task['service_started_at'] = begin
                    self.dwell_until = begin + self.servicing_task['service_s']
            if t_sim < self.dwell_until:
                return 0.0, 0.0
            self.servicing_task['serviced_at'] = t_sim
            self.servicing_task = None
            self.state = 'driving'
            self.last_progress = (self.s, t_sim)
        task = self.next_task()
        hold = min(min(task['s'], self.length) if task else self.length, self.next_corner())
        s_proj, cte = self.project((x, y))
        self.s = max(self.s, min(s_proj, self.s + LOOKAHEAD, hold))
        self.cte.append(cte)
        if task and abs(task['s'] - self.s) <= task['radius'] + 0.05 and math.dist((x, y), task['goal']) <= task['radius']:
            task.update(state='reached', t_sim=t_sim, distance_m=math.dist((x, y), task['goal']))
            self.s = max(self.s, task['s'])
            begin = self.opens_at(task, t_sim)
            if begin > t_sim:
                task['service_started_at'] = begin
            if task.get('service_s', 0) > 0 or begin > t_sim:
                self.state, self.dwell_until = 'servicing', begin + task['service_s']
                self.stationary_since = None
                self.servicing_task = task
                return 0.0, 0.0
            task['serviced_at'] = t_sim
            task = self.next_task()
        if not task and self.length - self.s < self.terminal_radius and math.dist((x, y), self.path[-1]) < self.terminal_radius:
            self.state, self.t_done = 'done', t_sim
            return 0.0, 0.0
        # never look past the next task stop or sharp corner
        stop_s = min(min(task['s'], self.length) if task else self.length, self.next_corner())
        i_hazard = bisect.bisect_left(self.hazard_s, self.s - 0.3)
        tight = i_hazard < len(self.hazard_s) and self.hazard_s[i_hazard] < self.s + HAZARD_WINDOW
        target_s = min(self.s + (TIGHT_LOOKAHEAD if tight else LOOKAHEAD), stop_s)
        tx, ty = self.point_at(target_s)
        if math.dist((x, y), (tx, ty)) < 0.05:  # sitting on a stop point but outside the radius
            tx, ty = task['goal'] if task else self.path[-1]
        alpha = wrap(math.atan2(ty - y, tx - x) - yaw)
        to_stop = max(0.0, stop_s - self.s) + math.dist((x, y), self.point_at(self.s))
        if abs(alpha) > (0.35 if self.turning() else 0.9):
            return 0.0, max(-1.6, min(1.6, 2.2 * alpha))
        v = self.speed * max(0.3, math.cos(alpha))
        v = min(v, 0.15 + 0.7 * to_stop)
        v = min(v, self._speed_cap())
        look = max(0.4, math.dist((x, y), (tx, ty)))
        w = max(-1.8, min(1.8, 2 * v * math.sin(alpha) / look + 0.8 * alpha * (v < 0.3)))
        # stuck watchdog
        if self.last_progress[1] is None or self.s > self.last_progress[0] + 0.2:
            self.last_progress = (self.s, t_sim)
        elif t_sim - self.last_progress[1] > 60:
            self.state = 'failed'
            self.failure = '60 秒仿真时间内未取得路径进展'
            return 0.0, 0.0
        return v, w

    def summary(self, plan_v: dict | None):
        reached = [t for t in self.tasks if t['state'] == 'reached']
        return {
            'error': self.failure, 'name': self.name, 'label': self.label, 'color': self.color, 'state': self.state,
            'pose': [round(c, 3) for c in self.pose] if self.pose else None, 'speed': round(self.speed_now, 3),
            'progress': round(self.s / self.length, 4) if self.length else 1.0,
            'distance_m': round(self.distance, 2), 'path_length_m': round(self.length, 2),
            'tasks_done': len(reached), 'tasks_total': len(self.tasks),
            'cross_track_mean_m': round(sum(self.cte) / len(self.cte), 4) if self.cte else None,
            'cross_track_max_m': round(max(self.cte), 4) if self.cte else None,
            'duration_s': round(self.t_done - self.t_start, 2) if self.t_done is not None and self.t_start is not None else None,
            'queue_wait_s': self.t_depart - self.t_start if self.t_depart is not None and self.t_start is not None else None,
            'parking_xy': (plan_v or {}).get('start', [])[:2],
            'planned_length_m': (plan_v or {}).get('planned_length_m'),
            'planned_duration_s': (plan_v or {}).get('planned_duration_s'),
        }


"""Causal planar wheel/position estimator; no truth input.

Delayed position fixes update the state at measurement time, then replay recorded
wheel twists. This is a small synthetic EKF, not a calibrated GNSS/IMU estimator.
The initial map pose is the declared spawn prior. White process-noise densities
are integrated with dt, so changing the odometry publish rate does not change Q.
"""
from __future__ import annotations
import bisect
import math
import numpy as np


class Estimator:
    def __init__(self, start_xy, start_yaw, gnss=None):
        self.state = np.array([*start_xy, start_yaw], dtype=float)
        self.cov = np.diag([.05**2, .05**2, .05**2])
        self.cfg = gnss or {}
        self.history = []
        self.odom_prev = self.last_fix_t = self.last_received_t = None
        self.last_innovation = None
        self.innovations = []
        self.inputs = []
        self.updates = []

    @staticmethod
    def predict(state, cov, v, w, dt):
        half = .5 * w * dt
        length = v * dt * (math.sin(half) / half if abs(half) > 1e-9 else 1.0)
        dx, dy = length * math.cos(state[2] + half), length * math.sin(state[2] + half)
        x = state + [dx, dy, w * dt]
        x[2] = (x[2] + math.pi) % (2 * math.pi) - math.pi
        f = np.array([[1., 0., -dy], [0., 1., dx], [0., 0., 1.]])
        q = np.diag([.08**2 + .02**2 * abs(v), .08**2 + .02**2 * abs(v),
                     .03**2 + .04**2 * abs(w)]) * dt
        return x, f @ cov @ f.T + q

    def on_odom(self, v, w, t):
        if not all(math.isfinite(a) for a in (v, w, t)):
            raise ValueError('定位里程计输入含非有限值')
        if self.odom_prev is not None:
            if t <= self.odom_prev:
                raise ValueError('定位里程计源时间必须严格递增')
            self.state, self.cov = self.predict(self.state, self.cov, v, w, t - self.odom_prev)
        self.odom_prev = t
        self.inputs.append([t, v, w])
        self.history.append([t, v, w, self.state.copy(), self.cov.copy()])
        while len(self.history) > 2 and self.history[1][0] < t - 3.0:
            self.history.pop(0)

    def on_fix(self, x_obs, y_obs, t_measure, t_received=None):
        received = self.odom_prev if t_received is None else t_received
        if not all(isinstance(a, (float, int)) and math.isfinite(a) for a in (x_obs, y_obs, t_measure, received)):
            raise ValueError('定位观测或时间无效')
        if (not self.history or received < t_measure or received > self.odom_prev + 1e-8
                or t_measure < self.history[0][0] or t_measure > self.odom_prev
                or (self.last_fix_t is not None and t_measure <= self.last_fix_t)):
            self.updates.append([t_measure, received, False, 'outside_history_or_nonmonotonic'])
            return False
        idx = bisect.bisect_right([r[0] for r in self.history], t_measure + 1e-9) - 1
        row = self.history[idx]
        x, p = row[3].copy(), row[4].copy()
        if t_measure > row[0] + 1e-9:
            nxt = self.history[idx + 1]
            x, p = self.predict(x, p, nxt[1], nxt[2], t_measure - row[0])
        innovation = np.array([x_obs, y_obs]) - x[:2]
        r = np.eye(2) * max(float(self.cfg.get('noise_std_m', .1))**2, 1e-8)
        gain = np.linalg.solve((p[:2, :2] + r).T, p[:, :2].T).T
        x = x + gain @ innovation
        x[2] = (x[2] + math.pi) % (2 * math.pi) - math.pi
        h = np.array([[1., 0., 0.], [0., 1., 0.]])
        a = np.eye(3) - gain @ h
        p = a @ p @ a.T + gain @ r @ gain.T  # Joseph form
        cursor = t_measure
        if abs(t_measure - row[0]) <= 1e-9:
            row[3], row[4] = x.copy(), p.copy()
        else:
            # Keep this posterior inside a split odometry interval. A later fix
            # in the same interval must not restart from the old uncorrected row.
            idx += 1
            self.history.insert(idx, [t_measure, nxt[1], nxt[2], x.copy(), p.copy()])
        for nxt in self.history[idx + 1:]:
            x, p = self.predict(x, p, nxt[1], nxt[2], nxt[0] - cursor)
            nxt[3], nxt[4] = x.copy(), p.copy()
            cursor = nxt[0]
        self.state, self.cov = x, p
        self.last_fix_t, self.last_received_t = t_measure, received
        self.last_innovation = float(np.linalg.norm(innovation))
        self.innovations.append((received, self.last_innovation))
        self.updates.append([t_measure, received, True, None])
        return True

    def pose(self, t=None):
        # No extrapolation or truth correction; this state belongs to odom_prev.
        return tuple(float(v) for v in self.state)

    def since_fix(self, t):
        return None if self.last_fix_t is None else t - self.last_fix_t

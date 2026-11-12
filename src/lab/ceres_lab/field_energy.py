"""Post-hoc energy and wheel-torque accounting from the recorded truth trajectory.

This is a kinematic bookkeeping model evaluated on simulator ground truth, not a
battery, motor-current or thermal model inside the loop. Traction work is the
longitudinal inertial force plus a rolling-resistance term; braking is dissipative
and never recuperated. Every number travels with the versioned parameters that
produced it.
"""
from __future__ import annotations

import math

GRAVITY = 9.80665


def _inside(x, y, poly) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi, xj, yj = poly[i][0], poly[i][1], poly[j][0], poly[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def account(samples: list, physics: dict, corridors: list | None = None) -> dict | None:
    """Energy and peak torque demand of one vehicle from [[t, x, y, ...]] truth samples.

    Rolling resistance is per-surface: samples inside a corridor (farm road / headland)
    polygon use the hard-surface coefficient, the rest the firm-soil one (ASAE D497 ranges)."""
    import numpy as np
    p = np.asarray(samples, dtype=float)
    if p.ndim != 2 or p.shape[1] < 3 or len(p) < 3 or not np.isfinite(p).all() or (np.diff(p[:, 0]) <= 0).any():
        return None
    model = (physics or {}).get('energy_model') or {}
    crr_field = float(model.get('rolling_resistance', .06))
    crr_road = float(model.get('rolling_resistance_corridor', crr_field))
    eta = float(model.get('drive_efficiency', .8))
    mass = float((physics or {}).get('total_mass_kg', 25))
    wheel_r = float(((physics or {}).get('wheel') or {}).get('radius_m', .15))
    t, x, y = p[:, 0], p[:, 1], p[:, 2]
    # Central-difference velocity; a short moving average keeps the second derivative usable
    # at 50 Hz without inventing dynamics the controller did not command.
    dt = np.gradient(t)
    v = np.hypot(np.gradient(x, t), np.gradient(y, t))
    if len(v) > 10:
        kernel = min(len(v) // 2 * 2 - 1, max(3, round(.2 / float(np.median(dt))) | 1))
        pad = kernel // 2
        padded = np.concatenate((np.full(pad, v[0]), v, np.full(pad, v[-1])))
        v = np.convolve(padded, np.ones(kernel) / kernel, mode='valid')
    a = np.gradient(v, t)
    moving = v > 1e-3
    roads = [(min(px for px, _py in c), min(py for _px, py in c), max(px for px, _py in c), max(py for _px, py in c), c)
             for c in (corridors or [])]
    on_road = np.array([any(x0 <= xv <= x1 and y0 <= yv <= y1 and _inside(xv, yv, poly)
                            for x0, y0, x1, y1, poly in roads) for xv, yv in zip(x, y)]) if roads else np.zeros(len(x), dtype=bool)
    crr = np.where(on_road, crr_road, crr_field)
    force = mass * a + crr * mass * GRAVITY * moving        # rolling resistance only while rolling
    power = np.maximum(force * v, 0.0) / eta                # discharge-only; no recuperation
    trapz = (lambda f, ts: float(np.trapezoid(f, ts))) if hasattr(np, 'trapezoid') else (lambda f, ts: float(np.trapz(f, ts)))
    energy_j = trapz(power, t)
    field_j = trapz(np.where(~on_road, power, 0.0), t)
    peak_force = float(np.max(np.abs(force))) if len(force) else 0.0
    return {'schema': 'urp.field-energy.post_hoc/3',
            'distance_m': float(np.hypot(np.diff(x), np.diff(y)).sum()),
            'moving_s': trapz(moving.astype(float), t),
            'energy_j': energy_j, 'energy_wh': energy_j / 3600.0,
            'energy_field_j': field_j, 'energy_corridor_j': max(0.0, energy_j - field_j),
            'mean_power_w': energy_j / max(t[-1] - t[0], 1e-9),
            'peak_power_w': float(np.max(power)) if len(power) else 0.0,
            'peak_wheel_torque_nm': peak_force * wheel_r / 2.0,
            'peak_longitudinal_force_n': peak_force,
            'model': {'rolling_resistance_field': crr_field, 'rolling_resistance_corridor': crr_road,
                      'drive_efficiency': eta, 'mass_kg': mass, 'wheel_radius_m': wheel_r, 'recuperation': False,
                      'surfaces': (physics or {}).get('energy_model', {}).get('surfaces'),
                      'velocity_filter_window_s': .2,
                      'derivation': 'time-aware central differences; velocity smoothed over fixed 0.2 s (rounded to odd sample count); '
                                    'per-sample surface by point-in-corridor test',
                      'scope': 'kinematic accounting on simulator truth; not motor current, battery state, thermal or terrain-deformation physics'}}

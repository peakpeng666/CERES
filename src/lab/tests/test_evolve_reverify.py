"""Re-verification curvature check: the densified verdict sees corners the native-sample check misses."""
import math

import pytest

from ceres_lab.evolve import reverify as RV


def test_strict_curvature_flags_a_long_chord_corner():
    # Two 100 m chords at a right angle: the native three-point circle has radius ~70.7 m (passes at r = 1),
    # but at 2 cm sampling the same corner has curvature ~70.7 per metre.
    res = RV.strict_curvature([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)], radius=1.0)
    assert not res['strict_valid_curvature'] and res['strict_violations'] >= 1
    assert res['strict_max_curvature_per_m'] == pytest.approx(math.sqrt(2) / 0.02, rel=0.02)


def test_strict_curvature_accepts_a_smooth_arc():
    # Samples at exactly the 2 cm step lie on a radius-2 circle, so every triple's circumcircle is the arc
    # itself: curvature 0.5 per metre, far below the radius-1 limit.
    r = 2.0
    arc = [[r * math.sin(t / 100.0), r - r * math.cos(t / 100.0)] for t in range(120)]   # 2 cm steps
    res = RV.strict_curvature(arc, radius=1.0)
    assert res['strict_valid_curvature'] and res['strict_violations'] == 0
    assert res['strict_max_curvature_per_m'] == pytest.approx(0.5, rel=1e-6)

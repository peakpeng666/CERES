"""Bounded controller for the independent synthetic aisle; no route-scene input."""
import math
from .scene_core import number

WAYPOINTS = ((1.0, 0.0), (2.0, 0.0), (0.0, 0.0))
MAX_LINEAR = 0.25
MAX_ANGULAR = 0.7
TOLERANCE = 0.10


def in_corridor(x, y):
    return -1.0 <= x <= 3.0 and -0.8 <= y <= 0.8


def command(x, y, yaw, waypoint):
    x, y, yaw = [number(v, 'pose') for v in (x, y, yaw)]
    if not in_corridor(x, y):
        raise ValueError('robot left synthetic test corridor')
    gx, gy = [number(v, 'goal') for v in waypoint]
    if not in_corridor(gx, gy):
        raise ValueError('goal is outside synthetic test corridor')
    distance = math.hypot(gx-x, gy-y)
    if distance <= TOLERANCE:
        return 0.0, 0.0, True
    error = math.atan2(math.sin(math.atan2(gy-y, gx-x)-yaw),
                       math.cos(math.atan2(gy-y, gx-x)-yaw))
    angular = max(-MAX_ANGULAR, min(MAX_ANGULAR, 1.5 * error))
    linear = 0.0 if abs(error) > 0.25 else min(MAX_LINEAR, 0.7 * distance)
    return linear, angular, False


def yaw_from_quaternion(x, y, z, w):
    x, y, z, w = [number(v, 'quaternion') for v in (x, y, z, w)]
    norm = math.sqrt(x*x+y*y+z*z+w*w)
    if not 0.99 < norm < 1.01:
        raise ValueError('invalid orientation quaternion')
    x, y, z, w = (value / norm for value in (x, y, z, w))
    return math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))

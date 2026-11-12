"""Shared scalar and coordinate validation for scenario schemas."""
from __future__ import annotations

import math


def _int(v, what: str, lo: int | None = None) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or float(v) != int(v):
        raise ValueError(f'{what} 应为整数，实际 {v!r}')
    v = int(v)
    if lo is not None and v < lo:
        raise ValueError(f'{what} 应 ≥ {lo}，实际 {v}')
    return v


def _num(v, what: str, positive: bool = False) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise ValueError(f'{what} 应为有限数值，实际 {v!r}')
    if positive and v <= 0:
        raise ValueError(f'{what} 应为正数，实际 {v}')
    return float(v)


def _xy(p, what: str) -> list[float]:
    if not isinstance(p, (list, tuple)) or len(p) != 2:
        raise ValueError(f'{what} 应为 [x, y]')
    return [_num(p[0], what + '.x'), _num(p[1], what + '.y')]



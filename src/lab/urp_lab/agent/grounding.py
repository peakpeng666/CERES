"""Heuristic grounding of the agent's final answer against this session's tool results (CONTRACT §9.4).

A number in the answer counts as grounded when it appears in a tool result or in the user's own
messages (relative tolerance 0.5 %, or equal after rounding to the digits written). Integers up to 10
and calendar years are not checked. Derived numbers, such as a difference the model computed itself,
are reported as not directly found. This is a reading aid for reviewers, not a proof of correctness.
"""
from __future__ import annotations

import math
import re

METHOD = 'heuristic-v1'
RECORD_ID = re.compile(r'\b(?:run|job|study|field|agent)_\d{8}T\d{6}_[0-9a-f]+\b')
TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+Z?)?')
NUMBER = re.compile(r'(?<![A-Za-z0-9_./\-])([-+]?\d+(?:\.\d+)?)(\s?%)?(?![A-Za-z0-9_.])')
SCENARIO_ID = re.compile(r'\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b')
TOLERANCE = 0.005


def numbers_in(value, out: list[float] | None = None, depth: int = 0) -> list[float]:
    out = [] if out is None else out
    if depth > 12:
        return out
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        if math.isfinite(value):
            out.append(float(value))
    elif isinstance(value, dict):
        for item in value.values():
            numbers_in(item, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            numbers_in(item, out, depth + 1)
    elif isinstance(value, str) and len(value) <= 2000:
        out.extend(float(m.group(1)) for m in NUMBER.finditer(_strip(value)))
    return out


def _strip(text: str) -> str:
    return SCENARIO_ID.sub(' ', TIMESTAMP.sub(' ', RECORD_ID.sub(' ', text)))


def _matches(value: float, decimals: int, pool: list[float]) -> bool:
    for p in pool:
        if abs(value - p) <= max(1e-9, TOLERANCE * abs(p)) or round(p, decimals) == value:
            return True
    return False


def check(answer: str, tool_results: list, user_texts: list[str], known_ids: set[str]) -> dict:
    pool = numbers_in(tool_results)
    for text in user_texts:
        numbers_in(text, pool)
    checked, grounded, skipped, missing = 0, 0, 0, []
    for m in NUMBER.finditer(_strip(answer or '')):
        token, percent = m.group(1), bool(m.group(2))
        value = float(token)
        decimals = len(token.split('.')[1]) if '.' in token else 0
        if not percent and decimals == 0 and (abs(value) <= 10 or 1900 <= value <= 2100):
            skipped += 1
            continue
        checked += 1
        if _matches(value, decimals, pool) or (percent and _matches(value / 100, decimals + 2, pool)):
            grounded += 1
        elif len(missing) < 20:
            missing.append(token + ('%' if percent else ''))
    unknown = sorted({i for i in RECORD_ID.findall(answer or '') if i not in known_ids})
    return {'method': METHOD, 'checked': checked, 'grounded': grounded, 'skipped': skipped,
            'not_found': missing, 'unknown_ids': unknown[:20],
            'note': '启发式核对：数字须出现在本会话工具结果或用户消息中（容差 0.5% 或按所写位数舍入一致）；'
                    '≤10 的整数与年份不核对；推算得到的数字会列为未直接找到。'}

"""Task time windows, read the same way by the solvers, the plan checker and the physics records.

A window is ``time_window: [earliest_s, latest_s]`` on a task, in seconds after the vehicles are released; ``latest_s`` may
be ``None``. It bounds the moment the vehicle reaches the task, not the end of the work there. A vehicle that arrives before
``earliest_s`` waits at the task, so only ``latest_s`` can be missed by a plan, and only an executor that does not wait can
start early. Times are planned for every vehicle moving at once, which is what ``field_coordination`` models.
"""
from __future__ import annotations

import math

TOLERANCE_S = 1e-6
EXECUTION_TICK_S = 0.1      # a recorded start can land one control tick ahead of the moment it was computed for
SHOWN_LATE = 20             # late tasks kept in a compact record; the full table stays with the run


def of(task: dict) -> tuple[float, float | None] | None:
    window = task.get('time_window')
    if not window:
        return None
    return float(window[0]), None if window[1] is None else float(window[1])


def by_task(inst: dict) -> dict:
    return {t['id']: of(t) for t in inst.get('tasks') or [] if of(t)}


def present(inst: dict) -> bool:
    return any(of(t) for t in inst.get('tasks') or [])


def _minutes(text: str) -> int:
    hours, minutes = text.split(':')
    return int(hours) * 60 + int(minutes)


def after_start(value: str, start_clock: str | None) -> float:
    """Seconds after the fleet leaves, from ``HH:MM`` or ``1800s``. A time of day means nothing without a start."""
    if value.endswith('s'):
        return float(value[:-1])
    if not start_clock:
        raise ValueError(f'钟点 {value} 需要出发时刻才能换算')
    return (_minutes(value) - _minutes(start_clock)) * 60.0


def resolve(deadline: str | None, earliest: str | None, start_clock: str | None) -> list | None:
    """The one window a request gives every task, or None when it gives none."""
    if not deadline and not earliest:
        return None
    return [after_start(earliest, start_clock) if earliest else 0.0, after_start(deadline, start_clock) if deadline else None]


def clock(start_clock: str | None, seconds: float | None) -> str | None:
    """The wall clock a mission time falls on, when the mission has a start time."""
    if not start_clock or seconds is None:
        return None
    hours, minutes = start_clock.split(':')
    total = int(hours) * 3600 + int(minutes) * 60 + round(seconds)
    return f'{total // 3600 % 24:02d}:{total % 3600 // 60:02d}:{total % 60:02d}'


def row(task: dict, vehicle: str, arrival: float | None, start: float | None = None, planned: float | None = None) -> dict:
    """One task against its window. ``arrival`` of ``None`` means the task was never reached."""
    early, late = of(task)
    start = arrival if start is None else start
    slack = None if late is None or arrival is None else late - arrival
    on_time = arrival is not None and (late is None or arrival <= late + TOLERANCE_S)
    out = {'task': task['id'], 'label': str(task.get('label') or task['id']), 'vehicle': vehicle, 'earliest_s': early, 'latest_s': late,
           'arrival_s': None if arrival is None else round(arrival, 2), 'start_s': None if start is None else round(start, 2),
           'slack_s': None if slack is None else round(slack, 2), 'on_time': on_time}
    if planned is not None:
        out['planned_arrival_s'] = round(planned, 2)
    return out


def summary(rows: list[dict], model: str, scope: str, start_clock: str | None = None) -> dict:
    """The table for one plan or one execution, with the late tasks pulled out so a reader never has to search for them."""
    rows = sorted(rows, key=lambda r: (r['on_time'], r['slack_s'] if r['slack_s'] is not None else math.inf, str(r['label'])))
    late = [r for r in rows if not r['on_time']]
    slack = [r['slack_s'] for r in rows if r['slack_s'] is not None]
    return {'evaluated': True, 'model': model, 'scope': scope, 'start_clock': start_clock, 'total': len(rows), 'on_time': len(rows) - len(late),
            'all_on_time': not late, 'worst_slack_s': min(slack) if slack else None, 'late': late, 'tasks': rows}



def execution_horizon_s(plan: dict) -> float:
    """A bounded mission budget that permits declared earliest-time waits.

    Keep the existing travel/service allowance and add at most the last opening
    time. Per-driver progress watchdogs and process wall-clock budgets remain
    independent, so waiting for a legal window does not excuse stalled motion.
    """
    vehicles = plan.get('vehicles') or []
    planned = max(sum(v.get('planned_duration_s') or 0 for v in vehicles), 60)
    openings = [float(task.get('earliest_s') or 0) for v in vehicles for task in v.get('tasks') or []]
    openings += [window[0] for window in by_task(plan.get('instance') or {}).values()]
    return 2.5 * planned + 120 + max([0.0, *openings])

def compact(record: dict | None) -> dict | None:
    """The same verdict without the per-task table, for run lists and agent messages."""
    if not record:
        return record
    keep = ('evaluated', 'reason', 'model', 'start_clock', 'total', 'on_time', 'all_on_time', 'worst_slack_s', 'early_starts')
    return {**{k: record[k] for k in keep if k in record}, 'late': (record.get('late') or [])[:SHOWN_LATE]}


def _late_by(r: dict) -> str:
    if r.get('arrival_s') is None:
        return f"任务 {r['label']}（{r['vehicle']}，未到达）"
    return f"任务 {r['label']}（{r['vehicle']}，晚 {-r['slack_s']:.1f} s）"


def late_text(record: dict | None, limit: int = 6) -> str:
    """Names the late tasks, the part of a result nobody should have to work out for themselves."""
    late = (record or {}).get('late') or []
    head = '、'.join(_late_by(r) for r in late[:limit])
    more = (record or {}).get('total', 0) - (record or {}).get('on_time', 0) - min(limit, len(late))
    return head + (f' 等，另有 {more} 个' if more > 0 else '')


def verdict(record: dict | None) -> str:
    """One sentence for a report: how many tasks kept their window, and which did not."""
    if not record:
        return ''
    if not record.get('evaluated', True):
        return f"到达时间未能计算：{record.get('reason')}"
    if record.get('all_on_time'):
        margin = f"，最小余量 {record['worst_slack_s']:.1f} s" if record.get('worst_slack_s') is not None else ''
        return f"{record['total']} 个任务全部按时{margin}"
    return f"{record['on_time']}/{record['total']} 个任务按时，晚于截止：{late_text(record)}"


def execution(plan: dict, result: dict) -> dict | None:
    """Recorded arrivals against the windows of the plan's own instance. The clock starts when the vehicles are released."""
    windows = by_task(plan.get('instance') or {})
    if not windows:
        return None
    origin = float(result.get('execution_started_sim_s') or 0.0)
    labels = {v['name']: v.get('label') or v['name'] for v in plan.get('vehicles') or []}
    tasks = {t['id']: t for t in plan['instance']['tasks']}
    traffic = plan.get('traffic') or {}
    planned = {row_['task']: row_['arrive_s'] for stops in (traffic.get('arrivals') or {}).values() for row_ in stops}
    rows, early_starts = [], []
    for record in result.get('tasks') or []:
        if record['id'] not in windows:
            continue
        reached = record.get('t_sim')
        began = record.get('service_started_at')
        began = reached if began is None else began
        arrival = None if reached is None else reached - origin
        start = None if began is None else began - origin
        rows.append(row(tasks[record['id']], labels.get(record['vehicle'], record['vehicle']), arrival, start, planned.get(record['id'])))
        if start is not None and start < windows[record['id']][0] - EXECUTION_TICK_S:
            early_starts.append(rows[-1])
    recorded = {entry['task'] for entry in rows}
    for task_id in windows.keys() - recorded:
        rows.append(row(tasks[task_id], '未记录车辆', None))
    mode = 'coordinated' if traffic.get('gates') is not None else 'serial'
    scope = '实测到达时刻；时钟从车辆放行开始，与规划同一零点' + ('；全部车辆同时运行' if mode == 'coordinated' else '；整段任务逐车执行，后发车辆包含排队等待')
    out = summary(rows, mode, scope, (plan.get('instance') or {}).get('start_clock'))
    out['early_starts'] = len(early_starts)
    return out

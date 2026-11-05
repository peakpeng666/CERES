"""Makespan incumbent telemetry: only measured time-dimension spans enter the trace."""
from __future__ import annotations

import logging
import time


def makespan_sample(routing, time_dim, vehicles):
    return max(time_dim.CumulVar(routing.End(v)).Value() for v in range(vehicles)) / 10


def makespan_callback(routing, time_dim, vehicles, started, trace, diagnostics, *, sample=makespan_sample):
    """Record improving spans and account for unreadable samples without losing the incumbent."""
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.update(samples_read=0, samples_omitted=0, first_error=None)

    def on_solution():
        try:
            span = sample(routing, time_dim, vehicles)
        except Exception as exc:  # trace telemetry must not discard a valid incumbent
            diagnostics['samples_omitted'] += 1
            if diagnostics['first_error'] is None:
                diagnostics['first_error'] = f'{type(exc).__name__}: {exc}'
                logging.getLogger(__name__).warning('Makespan trace sample omitted: %s', exc)
            return
        diagnostics['samples_read'] += 1
        if not trace or span < trace[-1][1]:
            trace.append([round(time.perf_counter() - started, 4), span])

    return on_solution

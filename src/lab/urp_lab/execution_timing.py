"""Frozen, opt-in execution clocks. No ROS dependency; cleanup uses wall time.

This policy covers approach plus every route leg, never startup or shutdown.
Simulation mode must name its finite wall watchdog; no RTF is assumed.
"""
from __future__ import annotations

import copy
import math

MAX_BUDGET_S = 1800.0
MAX_WALL_WATCHDOG_S = 7200.0
FINALIZE_WALL_S = 90.0  # cancel ack/result, fallback cancellation, settle, independent check
REQUIRED_STREAMS = ('clock', 'scan', 'odom', 'ground_truth')


def bounded_seconds(value, name, maximum):
    if type(value) not in (int, float) or not 0 < value <= maximum or not math.isfinite(value):
        raise ValueError(f'{name} must be finite and in (0, {maximum:g}] seconds')
    return float(value)


def resolve_budget_policy(planner_budget_s, *, budget_s=None, time_basis=None, wall_watchdog_s=None):
    """Reject invalid requests instead of silently changing their meaning."""
    planned = bounded_seconds(planner_budget_s, 'planner budget_s', MAX_BUDGET_S)
    basis = 'wall' if time_basis is None else time_basis
    if basis not in ('wall', 'simulation'):
        raise ValueError('time_basis must be wall or simulation')
    budget = planned if budget_s is None else bounded_seconds(budget_s, 'budget_s', MAX_BUDGET_S)
    if basis == 'simulation':
        watchdog = bounded_seconds(wall_watchdog_s, 'simulation wall_watchdog_s', MAX_WALL_WATCHDOG_S)
    else:
        if wall_watchdog_s is not None and bounded_seconds(wall_watchdog_s, 'wall_watchdog_s', MAX_WALL_WATCHDOG_S) != budget:
            raise ValueError('wall time_basis uses budget_s as its wall watchdog; do not specify a different watchdog')
        watchdog = budget
    result = {
        'policy': 'execution_budget_v1',
        'planner_budget_s': planned,
        'planner_formula': 'path_length_over_0.10mps_plus_60s_clamped_90_1800_v1',
        'requested': {'budget_s': budget_s, 'time_basis': time_basis, 'wall_watchdog_s': wall_watchdog_s},
        'effective': {'budget_s': budget, 'time_basis': basis, 'wall_watchdog_s': watchdog,
                      'clock_no_progress_wall_s': 30.0, 'telemetry_stale_wall_s': 3.0,
                      'scope': 'shared_approach_and_all_legs',
                      'navigate_wall_cap_s': 90.0 if basis == 'wall' else None,
                      'required_streams': list(REQUIRED_STREAMS)},
        'budget_provenance': 'planner' if budget_s is None else 'explicit',
    }

    if basis == 'simulation':
        result['policy'] = 'execution_budget_v2'
        effective = result['effective']
        del effective['telemetry_stale_wall_s']
        effective.update(telemetry_stale_sim_s=3.0, telemetry_receipt_wall_s=30.0,
                         telemetry_progress_wall_s=30.0, telemetry_future_skew_sim_s=3.0,
                         telemetry_zero_stamp_fallback=['ground_truth'])
    return result


def finite_time(value):
    try:
        return float(value) if type(value) in (int, float) and math.isfinite(value) else None
    except OverflowError:
        return None


def record_telemetry(samples, key, wall, sim, source_stamp):
    """Capture the actual cached message, never a maximum historical stamp.

    Only the verified ground-truth bridge's zero stamp may use receipt /clock.
    Repeated headers cannot renew source progress or clipped future-time credit.
    Faults are sticky for the session; restarting is required after bad timing.
    """
    previous = samples.get(key, {})
    source = finite_time(source_stamp)
    received = finite_time(sim)
    zero_truth = key == 'ground_truth' and source == 0 and previous.get('source_stamp_s', 0) == 0
    kind = 'clock_at_receive_zero_stamp' if zero_truth else ('clock_stamp' if key == 'clock' else 'header')
    reference = received if zero_truth else source
    fault = previous.get('source_fault')
    if source is None or source < 0:
        fault = fault or ('missing' if source_stamp is None else 'invalid')
    prior = previous.get('progress_reference_s')
    if reference is not None and prior is not None and reference < prior:
        fault = fault or 'backwards'
    advanced = reference is not None and (prior is None or reference > prior)
    freshness = min(reference, received) if reference is not None and received is not None else None
    if not advanced and previous.get('freshness_sim_s') is not None:
        freshness = min(freshness, previous['freshness_sim_s']) if freshness is not None else None
    samples[key] = dict(received_wall_s=finite_time(wall), received_sim_s=received,
                        source_stamp_s=source, source_kind=kind, source_fault=fault,
                        progress_reference_s=reference, freshness_sim_s=freshness,
                        progress_wall_s=finite_time(wall) if advanced else previous.get('progress_wall_s', finite_time(wall)),
                        count=previous.get('count', 0)+1)


def freeze_plan_budget(plan, **requested):
    result = copy.deepcopy(plan)
    policy = resolve_budget_policy(plan['budget_s'], **requested)
    result.update(budget_policy=policy, **{k: policy['effective'][k]
                                         for k in ('budget_s', 'time_basis', 'wall_watchdog_s')})
    return result


def validate_plan_budget(plan):
    """Revalidate frozen gateway metadata at the session boundary; support legacy plans."""
    policy = plan.get('budget_policy')
    if policy is None:
        return freeze_plan_budget(plan, time_basis=plan.get('time_basis'), wall_watchdog_s=plan.get('wall_watchdog_s'))
    if not isinstance(policy, dict) or not isinstance(policy.get('requested'), dict):
        raise ValueError('invalid frozen budget_policy')
    expected = resolve_budget_policy(policy.get('planner_budget_s'), **policy['requested'])
    if policy != expected or any(plan.get(k) != expected['effective'][k]
                                 for k in ('budget_s', 'time_basis', 'wall_watchdog_s')):
        raise ValueError('frozen budget_policy does not match execution request')
    return copy.deepcopy(plan)


def budget_metadata(plan):
    return copy.deepcopy({k: plan[k] for k in ('budget_s', 'time_basis', 'wall_watchdog_s', 'budget_policy')})


def execution_poll_wall_s(run):
    """Polling is a wall envelope, never a reinterpretation of simulated seconds."""
    policy = run.get('budget_policy')
    if not isinstance(policy, dict):
        raise ValueError('execution response is missing frozen budget_policy')
    effective = policy.get('effective', {})
    watchdog = bounded_seconds(effective.get('wall_watchdog_s'), 'wall_watchdog_s', MAX_WALL_WATCHDOG_S)
    return watchdog + FINALIZE_WALL_S


class ExecutionTiming:
    """One immutable policy and origin shared by approach and all actions.

    observe() reports a pending-action trigger. Result processing does not invent
    a timeout retrospectively. check() gates the next action before submission.
    """
    def __init__(self, policy, wall, sim, *, telemetry=None):
        self.policy = copy.deepcopy(policy)
        self.effective = self.policy['effective']
        self.telemetry = telemetry
        self.last_observation_wall = wall
        self.start_wall = wall
        self.start_sim = self.safe_sim(sim)
        self.last_sim = self.start_sim
        self.last_progress_wall = wall
        self.data = {'policy': self.policy['policy'], 'start_wall_monotonic_s': wall,
                     'start_sim_s': self.start_sim, 'samples': 0, 'trigger': None,
                     'max_stream_gap_wall_s': {key: 0.0 for key in REQUIRED_STREAMS},
                     'max_no_progress_wall_s': 0.0}
        if self.effective['time_basis'] == 'simulation':
            self.data.update(diagnostics='execution_observation_v1', max_observation_gap_wall_s=0.0,
                             max_stream_age_sim_s={key: 0.0 for key in REQUIRED_STREAMS[1:]},
                             max_stream_progress_gap_wall_s={key: 0.0 for key in REQUIRED_STREAMS[1:]},
                             recent_observations=[])


    @staticmethod
    def safe_sim(value):
        return finite_time(value)

    def elapsed_sim(self, sim):
        sim = self.safe_sim(sim)
        elapsed = sim - self.start_sim if sim is not None and self.start_sim is not None else None
        return self.safe_sim(elapsed)

    def observe(self, wall, sim, at, *, deadlines=True, telemetry=None):
        if self.data['trigger'] is not None:
            return self.data['trigger']
        sim = self.safe_sim(sim)
        self.data.update(last_wall_monotonic_s=wall, last_sim_s=sim,
                         elapsed_wall_s=wall-self.start_wall, elapsed_sim_s=self.elapsed_sim(sim))
        self.data['samples'] += 1
        # Keep the established wall-default behavior: clock integrity is an
        # explicit simulation-mode requirement, not an implicit mode switch.
        if self.effective['time_basis'] == 'simulation':
            self.snapshot(wall, sim, at, self.telemetry if telemetry is None else telemetry)
            if 'clock' not in at:
                return 'clock_missing'
            if sim is None or self.start_sim is None or self.elapsed_sim(sim) is None:
                return 'clock_invalid'
            if sim < self.last_sim:
                return 'clock_backwards'
            if sim > self.last_sim:
                self.last_progress_wall = wall
            self.last_sim = sim
            idle = wall-self.last_progress_wall
            self.data['max_no_progress_wall_s'] = max(self.data['max_no_progress_wall_s'], idle)
            if idle >= self.effective['clock_no_progress_wall_s']:
                return 'clock_no_progress'
            health = self.simulation_health(wall, sim, at)
            if health is not None:
                return health
        else:
            # Deliberately preserve legacy wall checks, ordering and boundaries.
            for key in REQUIRED_STREAMS:
                arrived = at.get(key)
                if type(arrived) not in (int, float) or not math.isfinite(arrived):
                    return 'stream_missing:'+key
                gap = wall-arrived
                if gap < 0:
                    return 'stream_timestamp_invalid:'+key
                self.data['max_stream_gap_wall_s'][key] = max(self.data['max_stream_gap_wall_s'][key], gap)
                if gap > self.effective['telemetry_stale_wall_s']:
                    return 'stream_stale:'+key
        if not deadlines:
            return None
        if self.effective['time_basis'] == 'simulation':
            if sim-self.start_sim >= self.effective['budget_s']:
                return 'simulation_budget'
            if wall-self.start_wall >= self.effective['wall_watchdog_s']:
                return 'wall_watchdog'
        elif wall-self.start_wall >= self.effective['budget_s']:
            return 'budget'
        return None

    def snapshot(self, wall, sim, at, telemetry):
        """One complete observation, before any health check can short circuit."""
        gap = wall-self.last_observation_wall
        self.last_observation_wall = wall
        self.data['max_observation_gap_wall_s'] = max(self.data['max_observation_gap_wall_s'], gap)
        current = dict(wall_monotonic_s=wall, sim_s=sim, elapsed_wall_s=wall-self.start_wall,
                       elapsed_sim_s=self.elapsed_sim(sim), observation_gap_wall_s=gap,
                       clock_advanced=sim is not None and self.last_sim is not None and sim>self.last_sim,
                       clock_progress_gap_wall_s=0.0 if sim is not None and self.last_sim is not None and sim>self.last_sim else wall-self.last_progress_wall,
                       previous_clock_sim_s=self.last_sim, last_clock_progress_wall_s=self.last_progress_wall, streams={})
        for key in REQUIRED_STREAMS:
            row = copy.deepcopy((telemetry or {}).get(key, {}))
            arrival = finite_time(at.get(key))
            wall_age = wall-arrival if arrival is not None else None
            row.update(arrival_wall_s=arrival, receipt_age_wall_s=wall_age)
            for field, result in [('received_sim_s', 'receipt_age_sim_s'),
                                  ('source_stamp_s', 'source_age_sim_s'),
                                  ('freshness_sim_s', 'freshness_age_sim_s')]:
                value = finite_time(row.get(field))
                row[result] = finite_time(sim-value) if sim is not None and value is not None else None
            if row.get('source_kind') == 'clock_at_receive_zero_stamp':
                row['source_age_sim_s'] = None  # Unknown measurement time, not the simulation origin.
            progress = finite_time(row.get('progress_wall_s'))
            row['progress_age_wall_s'] = wall-progress if progress is not None else None
            current['streams'][key] = row
            if wall_age is not None:
                self.data['max_stream_gap_wall_s'][key] = max(self.data['max_stream_gap_wall_s'][key], wall_age)
            if key != 'clock':
                for field, target in [('freshness_age_sim_s', 'max_stream_age_sim_s'),
                                      ('progress_age_wall_s', 'max_stream_progress_gap_wall_s')]:
                    value = row[field]
                    if value is not None:
                        self.data[target][key] = max(self.data[target][key], value)
        self.data['last_observation'] = current
        history = self.data['recent_observations']
        if not history or wall-history[-1]['wall_monotonic_s'] >= 1.0:
            history.append(current)
            del history[:-8]

    def simulation_health(self, wall, sim, at):
        for key, row in self.data['last_observation']['streams'].items():
            gap = row['receipt_age_wall_s']
            if gap is None:
                return 'stream_missing:'+key
            if gap < 0:
                return 'stream_timestamp_invalid:'+key
            if gap > self.effective['telemetry_receipt_wall_s']:
                return 'stream_stale_wall:'+key
            if not row.get('count'):
                return 'stream_timing_missing:'+key
            if row.get('source_fault'):
                return 'stream_stamp_'+row['source_fault']+':'+key
            if key == 'clock':
                continue  # Strictly advancing clock has its separate wall guard.
            received = row.get('received_sim_s')
            source = row.get('source_stamp_s')
            if received is None or source is None or row['freshness_age_sim_s'] is None:
                return 'stream_stamp_invalid:'+key
            if received > sim:
                return 'stream_clock_backwards:'+key
            if row['source_kind'] == 'header' and source-received > self.effective['telemetry_future_skew_sim_s']:
                return 'stream_stamp_future:'+key
            if row['freshness_age_sim_s'] > self.effective['telemetry_stale_sim_s']:
                return 'stream_stale_sim:'+key
            progress_age = row['progress_age_wall_s']
            if progress_age is None or progress_age < 0:
                return 'stream_timestamp_invalid:'+key
            if progress_age > self.effective['telemetry_progress_wall_s']:
                return 'stream_no_progress:'+key
        return None

    def trigger(self, reason, wall, sim):
        if self.data['trigger'] is None:
            self.data.update(trigger=reason, trigger_wall_monotonic_s=wall,
                             trigger_sim_s=self.safe_sim(sim), trigger_elapsed_wall_s=wall-self.start_wall,
                             trigger_elapsed_sim_s=self.elapsed_sim(sim))
            if 'last_observation' in self.data:
                self.data['trigger_observation'] = copy.deepcopy(self.data['last_observation'])

    def check(self, wall, sim, at, *, telemetry=None):
        reason = self.observe(wall, sim, at, telemetry=telemetry)
        if reason is not None:
            self.trigger(reason, wall, sim)
            raise TimeoutError('execution timing guard: '+reason)

    def finish(self, wall, sim, state):
        self.data.update(finished_wall_monotonic_s=wall, finished_sim_s=self.safe_sim(sim),
                         total_elapsed_wall_s=wall-self.start_wall, total_elapsed_sim_s=self.elapsed_sim(sim),
                         terminal_state=state)

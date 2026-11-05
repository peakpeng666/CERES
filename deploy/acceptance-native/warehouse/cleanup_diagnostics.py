"""Finite observations of existing cleanup evidence, never acceptance policy.

Input is the already-read session report, only after the harness verifies its
session_id. No report read, process inspection, timing, control or cleanup action
is added. Source: 3acf840d1a0c16ab1cfcca15744e4ea6609ddb35,
src/lab/ros/urp_sim_session.py:590-663 and src/lab/ros/processes.py:51-88.
The six predicates mirror warehouse_lifecycle.physical_evidence exactly for
JSON values. They are observations only; physical_evidence remains authoritative.
Missing/falsy errors can pass a predicate while their list count is unavailable.
final_state is a snapshot taken while stopping, NOT lifecycle['stop'].
"""

from shutdown_diagnostics import session_detail, sanitize_session_detail

MAX_COUNT = 256
MAX_GROUPS = 64
MAX_REAPED = 256
CANCEL_PREFIX = '取消活动目标失败：'
SETTLE_ERROR = '停车观察速度未收敛'
AVAILABILITY = frozenset(('available', 'missing', 'invalid', 'identity_mismatch', 'unavailable'))
DETAIL_STATUS = frozenset(('available', 'missing', 'invalid', 'truncated', 'unavailable'))
CONJUNCTS = ('status_pass', 'remaining_owned_exact_empty', 'abnormal_exits_exact_empty',
             'report_errors_falsy', 'lifecycle_stopped', 'lifecycle_errors_falsy')
COUNT_FIELDS = ('remaining_owned', 'abnormal_exits', 'report_errors', 'final_state_errors')
ERROR_CATEGORIES = ('cancel_active_failed', 'stop_velocity_not_converged', 'other_present')
SIGNALS = frozenset(('SIGINT', 'SIGINT_LEADER', 'SIGTERM', 'SIGKILL'))
_JSON_TYPES = (dict, list, str, int, float, bool, type(None))
_MISSING = object()


def _bool(value):
    return value if type(value) is bool else None


def _enum(value, allowed, default):
    return value if type(value) is str and len(value) <= 64 and value in allowed else default


def _integer(value, limit):
    return value if type(value) is int and 0 <= value <= limit else None


def _dict(value):
    return value if type(value) is dict else {}


def _count(value, limit=MAX_COUNT):
    if type(value) is not list:
        return {'count': None, 'overflow': None}
    return {'count': min(len(value), limit), 'overflow': len(value) > limit}


def _equal(value, expected):
    # Exact built-in JSON values avoid executing untrusted overloaded equality.
    return value == expected if type(value) in _JSON_TYPES else None


def _falsy(value):
    return not value if type(value) in _JSON_TYPES else None


def _error_categories(value):
    out = {'status': 'missing' if value is _MISSING else 'invalid',
           **{key: None for key in ERROR_CATEGORIES}}
    if type(value) is not list:
        return out
    found = dict.fromkeys(ERROR_CATEGORIES, False)
    invalid = False
    for item in value[:MAX_COUNT]:
        if type(item) is not str:
            invalid = True
        elif item.startswith(CANCEL_PREFIX):
            found['cancel_active_failed'] = True
        elif item == SETTLE_ERROR:
            found['stop_velocity_not_converged'] = True
        else:
            found['other_present'] = True
    complete = not invalid and len(value) <= MAX_COUNT
    out['status'] = 'invalid' if invalid else 'truncated' if not complete else 'available'
    out.update({key: yes if complete or yes else None for key, yes in found.items()})
    return out


def _group_summary(value):
    out = {'status': 'missing' if value is _MISSING else 'invalid',
           'group_count': None, 'group_count_overflow': None,
           'any_forced_or_incomplete': None,
           'abnormal_nonzero_exit_count': None, 'abnormal_exit_count_complete': False,
           'scan_limited': False}
    if type(value) is not list:
        return out
    out.update(group_count=min(len(value), MAX_GROUPS), group_count_overflow=len(value) > MAX_GROUPS)
    forced = False
    invalid = False
    limited = len(value) > MAX_GROUPS
    count = 0
    remaining = MAX_REAPED
    for child in value[:MAX_GROUPS]:
        if type(child) is not dict:
            invalid = True
            continue
        complete = child.get('complete')
        signals = child.get('signals')
        signal_valid = (type(signals) is list and len(signals) <= 3 and
                        all(type(s) is str and len(s) <= 16 and s in SIGNALS for s in signals))
        if type(complete) is not bool or not signal_valid:
            invalid = True
        if complete is False or (type(signals) is list and any(type(s) is str and s == 'SIGKILL' for s in signals[:3])):
            forced = True
        records = child.get('reaped')
        if type(records) is not list:
            invalid = True
            continue
        if len(records) > remaining:
            limited = True
        examined = min(len(records), remaining)
        for row in records[:examined]:
            if type(row) is not dict or type(row.get('returncode')) is not int or not -255 <= row['returncode'] <= 255:
                invalid = True
            elif row['returncode'] not in (0, -2, 130):
                count += 1
        remaining -= examined
    out.update(status='invalid' if invalid else 'truncated' if limited else 'available',
               any_forced_or_incomplete=forced if forced or not (invalid or limited) else None,
               abnormal_nonzero_exit_count=None if invalid else count,
               abnormal_exit_count_complete=not (invalid or limited), scan_limited=limited)
    return out


def sanitize(raw):
    """Closed and idempotent; counts are exact only when their flags say so."""
    raw = _dict(raw)
    availability = _enum(raw.get('availability'), AVAILABILITY, 'unavailable')
    trusted = availability == 'available'
    conjuncts = _dict(raw.get('conjuncts')) if trusted else {}
    counts = _dict(raw.get('counts')) if trusted else {}
    categories = _dict(raw.get('final_state_error_categories')) if trusted else {}
    shutdown = _dict(raw.get('shutdown')) if trusted else {}
    groups = _dict(raw.get('process_groups')) if trusted else {}
    final = _dict(raw.get('final_constraints'))
    count_rows = {}
    for key in COUNT_FIELDS:
        row = _dict(counts.get(key))
        count = _integer(row.get('count'), MAX_COUNT)
        overflow = _bool(row.get('overflow'))
        if count is None or overflow is None or (overflow and count != MAX_COUNT):
            count = overflow = None
        count_rows[key] = {'count': count, 'overflow': overflow}
    category_status = _enum(categories.get('status'), DETAIL_STATUS, 'unavailable')
    if category_status == 'available':
        if count_rows['final_state_errors']['count'] is None:
            category_status = 'invalid'
        elif count_rows['final_state_errors']['overflow'] is True:
            category_status = 'truncated'
    category_values = {key: _bool(categories.get(key)) for key in ERROR_CATEGORIES}
    if category_status != 'available':
        category_values = {key: True if value is True else None for key, value in category_values.items()}
    group_status = _enum(groups.get('status'), DETAIL_STATUS, 'unavailable')
    group_count = _integer(groups.get('group_count'), MAX_GROUPS)
    group_overflow = _bool(groups.get('group_count_overflow'))
    if group_count is None or group_overflow is None or (group_overflow and group_count != MAX_GROUPS):
        group_count = group_overflow = None
    abnormal_count = _integer(groups.get('abnormal_nonzero_exit_count'), MAX_REAPED)
    if group_status == 'available':
        if (group_count is None or group_overflow is None or
                _bool(groups.get('scan_limited')) is None or abnormal_count is None):
            group_status = 'invalid'
        elif groups.get('scan_limited') is True or group_overflow is True:
            group_status = 'truncated'
    group_forced = _bool(groups.get('any_forced_or_incomplete'))
    if group_status != 'available' and group_forced is not True:
        group_forced = None
    abnormal_complete = (groups.get('abnormal_exit_count_complete') is True and
                         group_status == 'available' and abnormal_count is not None and
                         groups.get('scan_limited') is False)
    return {
        'policy': 'matched_session_cleanup_conjuncts_v1',
        'acceptance_role': 'observational_only_not_a_gate',
        'availability': availability,
        'session_status': _enum(raw.get('session_status'), {'PASS', 'FAIL'}, 'unknown') if trusted else 'unknown',
        'conjuncts': {key: _bool(conjuncts.get(key)) for key in CONJUNCTS},
        'counts': count_rows,
        'final_state_error_categories': {'status': category_status, **category_values},
        'shutdown': {
            'navigation': _bool(shutdown.get('navigation')),
            'localization': _bool(shutdown.get('localization')),
            'gazebo_accepted': _bool(shutdown.get('gazebo_accepted')),
            'gazebo_returncode_class': _enum(shutdown.get('gazebo_returncode_class'), {'normal', 'nonzero'}, 'unavailable'),
        },
        'process_group_details': sanitize_session_detail(raw.get('process_group_details') if trusted else None),
        'process_groups': {
            'status': group_status, 'group_count': group_count, 'group_count_overflow': group_overflow,
            'any_forced_or_incomplete': group_forced,
            'abnormal_nonzero_exit_count': abnormal_count,
            'abnormal_exit_count_complete': abnormal_complete,
            'scan_limited': groups.get('scan_limited') is True,
            'count_semantics': 'bounded_observed_reaped_records_not_log_exits',
        },
        'final_constraints': {
            'forced_cleanup': _bool(final.get('forced_cleanup')),
            'remaining_owned_zero': _bool(final.get('remaining_owned_zero')),
            'runner_remaining_zero': _bool(final.get('runner_remaining_zero')),
        },
    }


UNAVAILABLE = sanitize(None)


def collect(session, lifecycle):
    """Called only after the existing in-memory session identity check."""
    if type(session) is not dict:
        return sanitize({'availability': 'invalid'})
    lifecycle = _dict(lifecycle)
    stop = lifecycle.get('stop')
    stop = stop if type(stop) is dict else None
    final = session.get('final_state')
    final = final if type(final) is dict else {}
    lifecycle_shutdown = _dict(session.get('lifecycle_shutdown'))
    gazebo = _dict(session.get('gazebo_shutdown'))
    rc = gazebo.get('returncode')
    raw = {
        'availability': 'available',
        'session_status': session.get('status'),
        'conjuncts': {
            'status_pass': _equal(session.get('status'), 'PASS'),
            'remaining_owned_exact_empty': _equal(session.get('remaining_owned_processes'), []),
            'abnormal_exits_exact_empty': _equal(session.get('abnormal_exits'), []),
            'report_errors_falsy': _falsy(session.get('errors')),
            'lifecycle_stopped': _equal(stop.get('phase'), 'stopped') if stop is not None else None,
            'lifecycle_errors_falsy': _falsy(stop.get('errors')) if stop is not None else None,
        },
        'counts': {key: _count(value) for key, value in (
            ('remaining_owned', session.get('remaining_owned_processes')),
            ('abnormal_exits', session.get('abnormal_exits')),
            ('report_errors', session.get('errors')),
            ('final_state_errors', final.get('errors')),
        )},
        'final_state_error_categories': _error_categories(final.get('errors', _MISSING)),
        'shutdown': {
            'navigation': lifecycle_shutdown.get('navigation'),
            'localization': lifecycle_shutdown.get('localization'),
            'gazebo_accepted': gazebo.get('accepted'),
            'gazebo_returncode_class': ('normal' if rc == 0 else 'nonzero') if type(rc) is int and -255 <= rc <= 255 else 'unavailable',
        },
        'process_groups': _group_summary(session.get('cleanup', _MISSING)),
        'process_group_details': session_detail(session),
    }
    return sanitize(raw)


def finish(raw, *, availability='unavailable', forced_cleanup=None,
           remaining_owned_count=None, runner_remaining_count=None):
    """Merge only the harness/runner's existing finite final observations."""
    if type(raw) is not dict:
        raw = {'availability': availability}
    else:
        raw = dict(raw)
    raw['final_constraints'] = {
        'forced_cleanup': _bool(forced_cleanup),
        'remaining_owned_zero': remaining_owned_count == 0 if type(remaining_owned_count) is int and remaining_owned_count >= 0 else None,
        'runner_remaining_zero': runner_remaining_count == 0 if type(runner_remaining_count) is int and runner_remaining_count >= 0 else None,
    }
    return sanitize(raw)

"""Pure, bounded, privacy-safe warehouse startup observation projection.

This module never imports ROS, executes a command, reads a file, or influences
readiness. Unknown measurements remain null. Advancement describes only the
observed endpoints and is not a causal classification of the simulator.
"""
import json
import math
import re

MAX_OUTPUT_BYTES = 262144
MAX_NATIVE_LINE_BYTES = 16384
MAX_SAMPLES = 128
STREAMS = ('clock', 'odom', 'scan', 'dock', 'ground_truth', 'cmd_vel')
STATUSES = frozenset(('disabled', 'not_started', 'running', 'complete', 'failed',
                      'unavailable', 'interrupted', 'bounded_stop'))
END_REASONS = frozenset(('unobserved', 'disabled', 'window_complete', 'startup_left', 'invalid_state',
                        'cleanup_started', 'identity_mismatch', 'error', 'bounded_stop'))
PHASES = frozenset(('starting', 'assets', 'launching', 'sensors'))
STATE_PHASES = PHASES | frozenset(('undocking', 'localizing', 'nav2_starting', 'ready',
                                 'executing', 'failed', 'stopping', 'stopped'))
NATIVE_AVAILABILITY = frozenset(('unavailable', 'no_messages', 'available', 'invalid',
                                'type_mismatch', 'command_error', 'output_limited'))
CLOCK_AVAILABILITY = frozenset(('unavailable', 'no_messages', 'available', 'invalid',
                               'import_error'))
API_AVAILABILITY = frozenset(('unavailable', 'no_samples', 'available',
                             'identity_mismatch', 'invalid'))
PUBLISHER_STATES = frozenset(('unobserved', 'no_publishers', 'expected_type',
                             'mixed_types', 'unknown_type', 'probe_error'))
EXPECTED_NATIVE_TYPE = 'ignition.msgs.WorldStatistics'
_PUBLISHERS_HEADER = 'Publishers [Address, Message Type]:'
_NO_PUBLISHERS = 'No publishers on topic [/world/warehouse/stats]'
_UINT = re.compile(r'(?:0|[1-9][0-9]{0,12})\Z', re.ASCII)
_PUBLISHER = re.compile(r'  [^\s,\x00-\x1f]+, ([A-Za-z_][A-Za-z0-9_.]*)\Z', re.ASCII)


def _object(value):
    return value if type(value) is dict else {}


def _enum(value, choices, default='unavailable'):
    return value if type(value) is str and value in choices else default


def _number(value, high, low=0, integer=False):
    # Check bounds before isfinite: converting an untrusted huge int to float
    # would otherwise raise OverflowError. Booleans never count as numbers.
    types = (int,) if integer else (int, float)
    if type(value) not in types or not low <= value <= high:
        return None
    return value if math.isfinite(value) else None


def _boolean(value):
    return value if type(value) is bool else None


def _clock_snapshot(value, native=False):
    if type(value) is not dict:
        return None
    receipt = _number(value.get('receipt_wall_s'), 7200)
    sim = _number(value.get('sim_time_s'), 86400)
    if receipt is None or sim is None:
        return None
    result = {'receipt_wall_s': receipt, 'sim_time_s': sim}
    if native:
        iterations = _number(value.get('iterations'), 10**12, integer=True)
        if iterations is None:
            return None
        result.update(iterations=iterations, paused=_boolean(value.get('paused')))
    return result


def _api_snapshot(value):
    if type(value) is not dict:
        return None
    receipt = _number(value.get('receipt_wall_s'), 7200)
    phase = _enum(value.get('phase'), PHASES, None)
    if receipt is None or phase is None:
        return None
    counts = _object(value.get('counts'))
    ages = _object(value.get('age_s'))
    return {'receipt_wall_s': receipt, 'phase': phase,
            'counts': {key: _number(counts.get(key), 10**9, integer=True) for key in STREAMS},
            'age_s': {key: _number(ages.get(key), 7200) for key in STREAMS}}


def _api_state(value):
    if type(value) is not dict:
        return None
    receipt = _number(value.get('receipt_wall_s'), 7200)
    if receipt is None:
        return None
    return {'receipt_wall_s': receipt, 'phase': _enum(value.get('phase'), STATE_PHASES, 'unknown'),
            'errors_present': _boolean(value.get('errors_present')),
            'error_count': _number(value.get('error_count'), 10000, integer=True),
            'telemetry_present': _boolean(value.get('telemetry_present'))}


def _advanced(count, first, last, field):
    if (count is None or count < 2 or first is None or last is None
            or last['receipt_wall_s'] < first['receipt_wall_s']):
        return None
    return last[field] > first[field]


def sanitize(raw):
    """Return the complete fixed public schema without mutating ``raw``.

    Counters and durations are null when absent or invalid, not fabricated zero.
    Raw progress flags are ignored; only validated endpoints and a count of at
    least two observations can establish an endpoint advancement comparison.
    The source must be available; native samples also require the expected
    publisher type. Partial endpoints are retained without a progress claim.
    """
    raw = _object(raw)
    native = _object(raw.get('native_stats'))
    clock = _object(raw.get('ros_clock'))
    api = _object(raw.get('ros_api'))
    native_count = _number(native.get('samples'), MAX_SAMPLES, integer=True)
    clock_count = _number(clock.get('messages'), 10**9, integer=True)
    api_count = _number(api.get('samples'), MAX_SAMPLES, integer=True)
    native_available = (native.get('availability') == 'available'
                        and native.get('publisher_state') == 'expected_type')
    clock_available = clock.get('availability') == 'available'
    native_first = _clock_snapshot(native.get('first'), native=True)
    native_last = _clock_snapshot(native.get('last'), native=True)
    clock_first = _clock_snapshot(clock.get('first'))
    clock_last = _clock_snapshot(clock.get('last'))
    api_first = _api_snapshot(api.get('first'))
    api_last = _api_snapshot(api.get('last'))
    # An explicit measured zero cannot carry purported observations.
    if native_count == 0:
        native_first = native_last = None
    if clock_count == 0:
        clock_first = clock_last = None
    if api_count == 0:
        api_first = api_last = None
    return {
        'schema_version': 1,
        'startup_observation_policy': _enum(raw.get('startup_observation_policy'), ('starting_phase_v2',), 'unknown'),
        'timing_origin': 'validated_session_binding',
        'observer_trigger_wall_s': _number(raw.get('observer_trigger_wall_s'), 30) if _number(raw.get('observer_trigger_wall_s'), 30) in (5, 30) else None,
        'cutoff_semantics': 'matched_cached_startup_phase_only',
        'ros_clock_source': 'direct_subscription',
        'status': _enum(raw.get('status'), STATUSES),
        'session_identity_verified': _boolean(raw.get('session_identity_verified')),
        'observer_exit_code': _number(raw.get('observer_exit_code'), 255, -255, integer=True),
        'observer_watchdog_expired': raw.get('observer_watchdog_expired') is True,
        'observer_cleanup_complete': _boolean(raw.get('observer_cleanup_complete')),
        'observer_leader_reaped': _boolean(raw.get('observer_leader_reaped')),
        'observer_group_signal_requested': _enum(raw.get('observer_group_signal_requested'), ('none','sigterm','sigkill'), 'none'),
        'output_bytes': _number(raw.get('output_bytes'), MAX_OUTPUT_BYTES, integer=True),
        'output_limited': raw.get('output_limited') is True,
        'observation_duration_s': _number(raw.get('observation_duration_s'), 30),
        'cleanup_delay_s': _number(raw.get('cleanup_delay_s'), 10),
        'sampling_end_reason': _enum(raw.get('sampling_end_reason'), END_REASONS, 'unobserved'),
        'native_stats': {
            'availability': _enum(native.get('availability'), NATIVE_AVAILABILITY),
            'publisher_state': _enum(native.get('publisher_state'), PUBLISHER_STATES, 'unobserved'),
            'samples': native_count,
            'invalid_samples': _number(native.get('invalid_samples'), 10000, integer=True),
            'first': native_first, 'last': native_last,
            'last_receipt_age_at_window_end_s': _number(native.get('last_receipt_age_at_window_end_s'), 30) if native_last else None,
            'sim_time_advanced': _advanced(native_count if native_available else None, native_first, native_last, 'sim_time_s'),
            'iterations_advanced': _advanced(native_count if native_available else None, native_first, native_last, 'iterations'),
        },
        'ros_clock': {
            'availability': _enum(clock.get('availability'), CLOCK_AVAILABILITY),
            'messages': clock_count,
            'invalid_messages': _number(clock.get('invalid_messages'), 10000, integer=True),
            'first': clock_first, 'last': clock_last,
            'last_receipt_age_at_window_end_s': _number(clock.get('last_receipt_age_at_window_end_s'), 30) if clock_last else None,
            'sim_time_advanced': _advanced(clock_count if clock_available else None, clock_first, clock_last, 'sim_time_s'),
        },
        'ros_api': {
            'state_responses': _number(api.get('state_responses'), MAX_SAMPLES, integer=True),
            'starting_error_responses': _number(api.get('starting_error_responses'), MAX_SAMPLES, integer=True),
            'first_state': _api_state(api.get('first_state')),
            'last_state': _api_state(api.get('last_state')),
            'availability': _enum(api.get('availability'), API_AVAILABILITY),
            'samples': api_count,
            'first': api_first, 'last': api_last,
            'observer_window_first': _api_snapshot(api.get('observer_window_first')) if api_count else None,
            'observer_window_last': _api_snapshot(api.get('observer_window_last')) if api_count else None,
        },
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError('non-finite JSON number')


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('non-finite JSON number')
    return result


def _protobuf_uint(value, high, allow_integer=False):
    if allow_integer and type(value) is int:
        return _number(value, high, integer=True)
    if type(value) is not str or not _UINT.fullmatch(value):
        return None
    return _number(int(value), high, integer=True)


def _protobuf_time(value):
    # Present empty messages are valid protobuf zero; an absent message is not.
    if type(value) is not dict or not set(value).issubset({'sec', 'nsec'}):
        return None
    sec = _protobuf_uint(value.get('sec', 0), 86400, allow_integer=True)
    nsec = _number(value.get('nsec', 0), 999999999, integer=True)
    if sec is None or nsec is None or (sec == 86400 and nsec != 0):
        return None
    return sec + nsec / 10**9


def parse_native_stats(line):
    """Parse one bounded WorldStatistics protobuf JSON line, else return None.

    Unknown top-level fields are discarded. Duplicate keys, non-finite numbers,
    absent timestamp messages, invalid typed defaults and excessive ranges are
    rejected. Optional protobuf scalar defaults apply only after the message
    shape has been established. This parser does not establish publisher type.
    """
    if type(line) is not str:
        return None
    try:
        if len(line) > MAX_NATIVE_LINE_BYTES or len(line.encode('utf-8')) > MAX_NATIVE_LINE_BYTES:
            return None
        text = line[:-2] if line.endswith('\r\n') else line[:-1] if line.endswith('\n') else line
        if '\n' in text or '\r' in text or not text:
            return None
        value = json.loads(text, object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant, parse_float=_finite_float)
        if type(value) is not dict:
            return None
        sim = _protobuf_time(value.get('simTime'))
        real = _protobuf_time(value.get('realTime'))
        if sim is None or real is None:
            return None
        iterations = (0 if 'iterations' not in value else
                      _protobuf_uint(value['iterations'], 10**12))
        paused = value.get('paused', False)
        if iterations is None or type(paused) is not bool:
            return None
        return {'sim_time_s': sim, 'iterations': iterations, 'paused': paused}
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
        return None


def parse_topic_info(text):
    """Return only a finite publisher-state enum, never endpoints or type text."""
    if type(text) is not str:
        return 'probe_error'
    try:
        if len(text) > MAX_NATIVE_LINE_BYTES or len(text.encode('utf-8')) > MAX_NATIVE_LINE_BYTES:
            return 'probe_error'
    except UnicodeError:
        return 'probe_error'
    lines = text.rstrip('\r\n').split('\n')
    lines = [line[:-1] if line.endswith('\r') else line for line in lines]
    if lines == [_NO_PUBLISHERS]:
        return 'no_publishers'
    if not lines or lines[0] != _PUBLISHERS_HEADER:
        return 'probe_error'
    types = set()
    for line in lines[1:]:
        if not line:
            continue
        match = _PUBLISHER.fullmatch(line)
        if not match:
            return 'probe_error'
        types.add(match.group(1))
    if not types:
        return 'probe_error'
    if types == {EXPECTED_NATIVE_TYPE}:
        return 'expected_type'
    if len(types) > 1:
        return 'mixed_types'
    return 'unknown_type'

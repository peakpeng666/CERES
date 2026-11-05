"""Project existing owned-process resource records; never sample the machine.

CPU counters sum only currently live processes and may decrease after exits.
UTC offsets preserve report ordering and are not monotonic-clock measurements.
Rows can include cleanup. No utilization, process identity or causal label is
derived from these aggregate observations.
"""
from datetime import datetime
import math
import re

LIMIT = 128
SOURCE_LIMIT = 4096
UTC = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:\+00:00|Z)\Z')
STATUSES = {'unavailable', 'available', 'partial', 'invalid', 'over_limit'}


def number(value, high, *, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not 0 <= value <= high:
        return None
    return value if math.isfinite(value) else None


def timestamp(value):
    if type(value) is not str or len(value) > 32 or not UTC.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def sample(row):
    if type(row) is not dict:
        return None
    result = {
        'offset_from_report_start_utc_s': number(row.get('offset_from_report_start_utc_s'), 7200),
        'cpu_seconds_live_processes': number(row.get('cpu_seconds_live_processes'), 1000000),
        'rss_kib': number(row.get('rss_kib'), 128 * 1024 * 1024, integer=True),
        'process_count': number(row.get('process_count'), 100000, integer=True),
    }
    return result if all(value is not None for value in result.values()) else None


def sanitize(raw):
    raw = raw if type(raw) is dict else {}
    status = raw.get('status')
    rows = raw.get('samples')
    rows = rows if type(rows) is list and len(rows) <= LIMIT else []
    clean = [value for row in rows if (value := sample(row)) is not None]
    order = raw.get('utc_order')
    selection = raw.get('selection')
    return {
        'policy': 'existing_owned_resources_v1',
        'status': status if type(status) is str and status in STATUSES else 'unavailable',
        'clock_basis': 'utc_difference',
        'scope': 'all_owned_groups_including_cleanup',
        'counter_semantics': 'currently_live_process_totals',
        'source_rows': number(raw.get('source_rows'), SOURCE_LIMIT, integer=True),
        'invalid_rows': number(raw.get('invalid_rows'), SOURCE_LIMIT, integer=True),
        'utc_order': order if type(order) is str and order in {'nondecreasing', 'backwards'} else 'unavailable',
        'selection': selection if type(selection) is str and selection in {'all', 'evenly_sampled'} else 'unavailable',
        'samples': clean,
    }


def project(report):
    report = report if type(report) is dict else {}
    rows = report.get('resources')
    if type(rows) is not list:
        return sanitize({})
    if len(rows) > SOURCE_LIMIT:
        return sanitize({'status': 'over_limit'})
    origin = timestamp(report.get('started_utc'))
    if origin is None:
        return sanitize({'status': 'invalid', 'source_rows': len(rows)})
    values = []
    for row in rows:
        if type(row) is not dict:
            continue
        at = timestamp(row.get('utc'))
        if at is None:
            continue
        value = sample({
            'offset_from_report_start_utc_s': (at - origin).total_seconds(),
            'cpu_seconds_live_processes': row.get('cpu_seconds_live_processes'),
            'rss_kib': row.get('rss_kib'),
            'process_count': row.get('process_count'),
        })
        if value is not None:
            values.append(value)
    invalid = len(rows) - len(values)
    backwards = any(b['offset_from_report_start_utc_s'] < a['offset_from_report_start_utc_s']
                    for a, b in zip(values, values[1:]))
    selected = values if len(values) <= LIMIT else [values[i * (len(values) - 1) // (LIMIT - 1)] for i in range(LIMIT)]
    return sanitize({
        'status': ('partial' if invalid else 'available') if values else 'invalid' if rows else 'unavailable',
        'source_rows': len(rows), 'invalid_rows': invalid,
        'utc_order': ('backwards' if backwards else 'nondecreasing') if values else 'unavailable',
        'selection': 'all' if len(values) <= LIMIT else 'evenly_sampled',
        'samples': selected,
    })

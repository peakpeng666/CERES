"""Closed, diagnostic-only signed-index coordinates for a rejected delta.

Nothing in this module participates in package acceptance. Package identities
are used only for exact lookups in already authenticated records and are never
returned. A missing mapping is explicit, not evidence of package provenance.
"""

PREFIX = 'SAFE_PACKAGE_TARGET_REFERENCE_V2:'
MAX_REFERENCE_BYTES = 2048
SNAPSHOT = '20261021T000000Z'
MAX_INVENTORY_ITEMS = 4096
MAX_TRANSACTION_ITEMS = 4096
MAX_CANDIDATES_PER_IDENTITY = 16
INDEX_STANZA_COUNTS = {
    ('jammy', 'main'): 6090, ('jammy', 'universe'): 58923,
    ('jammy-updates', 'main'): 21642, ('jammy-updates', 'universe'): 5960,
    ('jammy-security', 'main'): 20377, ('jammy-security', 'universe'): 5017,
}
_SUITE_ORDER = {'jammy': 0, 'jammy-updates': 1, 'jammy-security': 2}
_COMPONENT_ORDER = {'main': 0, 'universe': 1}
_REFERENCE_KEYS = frozenset({
    'schema', 'snapshot', 'kind', 'selection', 'mismatch_count',
    'expected_source', 'observed_matches_baseline', 'baseline', 'expected',
    'observed',
})


def _exact_keys(value, keys):
    return (type(value) is dict and all(type(key) is str for key in value)
            and set(value) == keys)


def _point(value, allow_absent=False):
    if type(value) is not dict or type(value.get('status')) is not str:
        return None
    status = value['status']
    if status == 'unmapped' or (allow_absent and status == 'absent'):
        return {'status': status} if _exact_keys(value, {'status'}) else None
    if status != 'mapped' or not _exact_keys(
            value, {'status', 'suite', 'component', 'stanza'}):
        return None
    suite, component, stanza = value['suite'], value['component'], value['stanza']
    if (type(suite) is not str or type(component) is not str or
            (suite, component) not in INDEX_STANZA_COUNTS or type(stanza) is not int or
            not 0 <= stanza < INDEX_STANZA_COUNTS[(suite, component)]):
        return None
    return {'status': 'mapped', 'suite': suite, 'component': component, 'stanza': stanza}


def validate_reference(value):
    """Return a fresh, strictly typed closed reference, or None.

    Even an entirely unmapped reference is useful: it records the bounded
    relationship without making an unsupported signed-coordinate claim.
    """
    if not _exact_keys(value, _REFERENCE_KEYS):
        return None
    if type(value['schema']) is not int or value['schema'] != 2:
        return None
    for key, permitted in (
            ('snapshot', (SNAPSHOT,)), ('kind', ('post-delta-version',)),
            ('selection', ('first_sorted_key',)),
            ('mismatch_count', ('single', 'multiple')),
            ('expected_source', ('baseline', 'transaction'))):
        if type(value[key]) is not str or value[key] not in permitted:
            return None
    baseline = _point(value['baseline'], allow_absent=True)
    expected, observed = _point(value['expected']), _point(value['observed'])
    if baseline is None or expected is None or observed is None:
        return None
    matches = value['observed_matches_baseline']
    if baseline['status'] == 'absent':
        if value['expected_source'] != 'transaction' or matches is not None:
            return None
    elif type(matches) is not bool:
        return None
    if value['expected_source'] == 'baseline':
        if baseline != expected or matches is not False:
            return None
    if matches is True and observed != baseline:
        return None
    if (baseline['status'] == observed['status'] == 'mapped' and
            baseline == observed and matches is not True):
        return None
    if expected['status'] == observed['status'] == 'mapped' and expected == observed:
        return None
    return {
        'schema': 2, 'snapshot': SNAPSHOT, 'kind': 'post-delta-version',
        'selection': 'first_sorted_key', 'mismatch_count': value['mismatch_count'],
        'expected_source': value['expected_source'],
        'observed_matches_baseline': matches,
        'baseline': baseline, 'expected': expected, 'observed': observed,
    }


def _identity(row):
    if type(row) is not dict:
        return None
    values = tuple(row.get(key) for key in ('name', 'version', 'architecture'))
    if not all(type(value) is str and value for value in values):
        return None
    return values


def _valid_inventory(value):
    if type(value) is not dict or len(value) > MAX_INVENTORY_ITEMS:
        return False
    for key, row in value.items():
        identity = _identity(row)
        if (type(key) is not str or identity is None or
                key != identity[0] + ':' + identity[2]):
            return False
    return True


def _indexed_point(identity, records):
    candidates = records.get(identity, [])
    if type(candidates) is not list or len(candidates) > MAX_CANDIDATES_PER_IDENTITY:
        return None
    points = []
    for record in candidates:
        if type(record) is not dict:
            return None
        actual = tuple(record.get(key) for key in ('Package', 'Version', 'Architecture'))
        # The dictionary key alone is not authority for a stanza's identity.
        if not all(type(value) is str for value in actual) or actual != identity:
            return None
        point = _point({'status': 'mapped', 'suite': record.get('_suite'),
                        'component': record.get('_component'), 'stanza': record.get('_stanza')})
        if point is None:
            return None
        points.append(point)
    if not points:
        return {'status': 'unmapped'}
    return min(points, key=lambda point: (
        _SUITE_ORDER[point['suite']], _COMPONENT_ORDER[point['component']], point['stanza']))


def make_reference(before, after, transaction, records):
    """Build a bounded reference only for equal-keyset version mismatches.

    The expected state follows the original validator's exact construction,
    including last-write-wins transaction rows. This is not a second validator.
    Malformed or excessive diagnostic inputs produce no reference. Cancellation
    and other BaseException subclasses are not swallowed.
    """
    try:
        if (not _valid_inventory(before) or not _valid_inventory(after) or
                type(transaction) is not list or len(transaction) > MAX_TRANSACTION_ITEMS or
                type(records) is not dict):
            return None
        if any(_identity(row) is None for row in transaction):
            return None
        expected = {k: dict(v) for k, v in before.items()}
        transaction_keys = set()
        for row in transaction:
            key = row['name'] + ':' + row['architecture']
            expected[key] = {'name': row['name'], 'version': row['version'],
                             'architecture': row['architecture'], 'held': False}
            transaction_keys.add(key)
        if expected == after or set(expected) != set(after):
            return None
        mismatches = sorted(key for key in expected
                            if expected[key]['version'] != after[key]['version'])
        if not mismatches:
            return None
        key = mismatches[0]
        baseline = (_indexed_point(_identity(before[key]), records) if key in before
                    else {'status': 'absent'})
        expected_point = _indexed_point(_identity(expected[key]), records)
        observed = _indexed_point(_identity(after[key]), records)
        return validate_reference({
            'schema': 2, 'snapshot': SNAPSHOT, 'kind': 'post-delta-version',
            'selection': 'first_sorted_key',
            'mismatch_count': 'single' if len(mismatches) == 1 else 'multiple',
            'expected_source': 'transaction' if key in transaction_keys else 'baseline',
            'observed_matches_baseline': (_identity(after[key]) == _identity(before[key])
                                          if key in before else None),
            'baseline': baseline, 'expected': expected_point, 'observed': observed,
        })
    except Exception:
        # A diagnostic helper must not replace the original rejection.
        return None

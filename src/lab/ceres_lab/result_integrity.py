"""Keep source-invalidated candidates as evidence, not usable solver results."""

# Timings are diagnostic observations, not certified solver performance. Preserve
# the unit label for schema compatibility, but fail closed for every new metric.
_DIAGNOSTIC_METRICS = frozenset(('process_wall_s', 'wall_s', 'cpu_s', 'length_unit'))


def without_invalidated_results(sol: dict) -> dict:
    """Return a safe view, including for old records, without rewriting history.

    Ordinary solver failures can contain useful rejected-candidate diagnostics;
    only an explicit failed source identity check invalidates all result values.
    Raw output, traces, logs, and process evidence remain available for audit.
    """
    if (sol.get('process') or {}).get('source_identity_verified') is not False:
        return sol
    objective = sol.get('objective') or {}
    return {
        **sol,
        'status': None, 'routes': [], 'check': None, 'map_check': None,
        'objective': {'value': None, 'unit': objective.get('unit'), 'lower_bound': None},
        'metrics': {key: value if key in _DIAGNOSTIC_METRICS else None
                    for key, value in (sol.get('metrics') or {}).items()},
    }

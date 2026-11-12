"""Current execution IDs and exact legacy request aliases (not a history migration)."""

LEGACY_ALGORITHM_IDS = {
    'pyvrp.hgs': 'pyvrp.ils',
    'pyvrp.hgs+urp.layered': 'pyvrp.ils+urp.layered',
}


def canonical_algorithm_id(algorithm_id: str) -> str:
    return LEGACY_ALGORITHM_IDS.get(algorithm_id, algorithm_id)


def canonical_algorithm_values(values: dict) -> dict:
    """Copy keyed model values, rejecting conflicting legacy/current measurements."""
    out = {}
    for algorithm_id, value in values.items():
        canonical = canonical_algorithm_id(algorithm_id)
        if canonical in out and out[canonical] != value:
            raise ValueError(f'Conflicting algorithm aliases for {canonical}')
        out[canonical] = value
    return out

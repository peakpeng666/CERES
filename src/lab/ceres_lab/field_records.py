"""Unambiguous contact counts, including compatibility with pre-sensor field records."""
from __future__ import annotations


def contact_metrics(result, plan=None):
    plan = plan or {}
    # Before contact sensors existed, row_collisions meant SDF collision SHAPES.
    # Preserve that number separately; it never constituted evidence of contact events.
    shapes = plan.get('row_collision_shapes', plan.get('row_collisions',
                      result.get('row_collision_shapes', result.get('row_collisions'))))
    names = [v['name'] for v in result.get('vehicles', [])]
    observed = (bool(names) and isinstance(result.get('contacts'), list)
                and all(result.get('telemetry', {}).get('contacts', {}).get(n, 0) > 0 for n in names))
    rows, pairs = set(), set()
    if observed:
        for event in result['contacts']:
            key = (event['t_sim'], tuple(sorted(event['collisions'])))
            if any('crop_rows::' in n for n in event['collisions']):
                rows.add(key)
            if len(event.get('vehicles', [])) >= 2:
                pairs.add(key)
    return {'row_collision_shapes': shapes, 'row_collisions': len(rows) if observed else None,
            'robot_contact_events': len(pairs) if observed else None,
            'contact_observation': {'status': 'observed' if observed else 'unavailable',
                'count_unit': 'unique simulation timestamp + collision-body pair; not physical collision episodes',
                'legacy_note': None if observed else '历史几何数量不能当作实际接触次数；缺少接触遥测，次数未知'}}

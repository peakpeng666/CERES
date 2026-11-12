from ceres_lab.field_records import contact_metrics


def test_legacy_geometry_count_is_not_contact_evidence():
    r = contact_metrics({'row_collisions': 35, 'vehicles': [{'name': 'v'}]})
    assert r['row_collision_shapes'] == 35 and r['row_collisions'] is None
    assert r['contact_observation']['status'] == 'unavailable'


def test_observed_contacts_count_body_pairs_once_and_missing_stream_is_unknown():
    r = {'vehicles': [{'name': 'v'}, {'name': 'w'}], 'telemetry': {'contacts': {'v': 100, 'w': 100}}, 'contacts': []}
    assert contact_metrics(r, {'row_collision_shapes': 35})['row_collisions'] == 0
    e = {'t_sim': 2.0, 'collisions': ['v::chassis::collision', 'crop_rows::rows::r1'], 'vehicles': ['v']}
    r['contacts'] = [e, e, {'t_sim': 3.0, 'collisions': ['v::chassis::collision', 'w::chassis::collision'], 'vehicles': ['v', 'w']}]
    counts = contact_metrics(r)
    assert counts['row_collisions'] == counts['robot_contact_events'] == 1
    r['telemetry']['contacts']['w'] = 0
    assert contact_metrics(r)['row_collisions'] is None

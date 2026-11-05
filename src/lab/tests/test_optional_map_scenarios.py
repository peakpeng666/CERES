"""Absent optional ROS assets must not disable numeric benches or user imports."""
import shutil
from pathlib import Path
import pytest
from urp_lab import paths, scenarios
from urp_lab.scenarios import field, maps, motion

@pytest.fixture
def isolated_maps(tmp_path, monkeypatch):
    official = paths.ROS_MAPS
    empty = tmp_path / 'maps'; empty.mkdir()
    monkeypatch.setattr(paths, 'ROS_MAPS', empty)
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'user')
    monkeypatch.setattr(scenarios, 'OPENED', tmp_path / 'opened.json')
    scenarios._builtin.cache_clear(); maps.load.cache_clear(); field.warehouse_layout.cache_clear()
    yield empty, official
    scenarios._builtin.cache_clear(); maps.load.cache_clear(); field.warehouse_layout.cache_clear()

def test_no_maps_preserves_bench_and_user_agriculture(isolated_maps):
    assert maps.known_worlds() == []
    assert field.map_scenarios() == motion.motion_scenarios() == []
    before = {s['id'] for s in scenarios.list_scenarios()}
    assert before, 'numeric benchmark scenarios must remain available'
    assert not before.intersection(field.MAP_SCENARIO_IDS | motion.MAP_SCENARIO_IDS)
    payload = {'id': 'agri-no-map-import', 'title': 'No-map agriculture', 'problem_class': 'coverage', 'frame': {'kind': 'field', 'units': 'm', 'crs': 'local'},
               'instance': {'field': [[0, 0], [100, 0], [100, 100], [0, 100]],
                            'swath_width': 3, 'headland_width': 5, 'angle_deg': 0}}
    result = scenarios.import_scenario(payload)
    restored = scenarios.get_scenario(result['id'])
    assert restored['instance']['field'] == payload['instance']['field']
    assert restored['sim']['supported'] is False
    assert {s['id'] for s in scenarios.list_scenarios()} == before | {payload['id']}

@pytest.mark.parametrize('world,field_ids,motion_ids', [
    ('maze', {'maze-inspection'}, {'p2p-maze-cross'}),
    ('depot', {'cvrp-depot-floor', 'field-coverage-depot'}, {'p2p-depot-reeds'}),
    ('warehouse', {'farm-rows-warehouse'}, {'p2p-warehouse-aisle', 'p2p-warehouse-dubins'}),
])
def test_partial_map_registration_is_lazy(world, field_ids, motion_ids, monkeypatch):
    # Dispatch sentinels only; no invented map is passed to real planning/checking.
    monkeypatch.setattr(maps, 'known_worlds', lambda: [world])
    for name, sid, owner in [('maze_inspection', 'maze-inspection', 'maze'),
                             ('farm_rows_warehouse', 'farm-rows-warehouse', 'warehouse'),
                             ('cvrp_depot_floor', 'cvrp-depot-floor', 'depot'),
                             ('field_coverage_depot', 'field-coverage-depot', 'depot')]:
        def build(sid=sid, owner=owner):
            assert owner == world, 'attempted to load an uninstalled map'
            return {'id': sid}
        monkeypatch.setattr(field, name, build)
    def layout():
        assert world == 'warehouse', 'warehouse layout evaluated for a different map'
        return {'spawn': {'x': 0}, 'aisles': [{'x': 1}], 'y_south': 0}
    monkeypatch.setattr(motion, 'warehouse_layout', layout)
    def scenario(sid, _title, _summary, requested_world, *_args):
        assert requested_world == world
        return {'id': sid}
    monkeypatch.setattr(motion, '_scenario', scenario)
    assert {s['id'] for s in field.map_scenarios()} == field_ids
    assert {s['id'] for s in motion.motion_scenarios()} == motion_ids

@pytest.mark.parametrize('build', [field.map_scenarios, motion.motion_scenarios])
def test_present_yaml_with_missing_image_is_not_ignored(isolated_maps, build):
    directory, _ = isolated_maps
    (directory / 'maze.yaml').write_text('image: missing-official-image.pgm\nresolution: 0.05\norigin: [0, 0, 0]\n')
    assert maps.known_worlds() == ['maze']
    with pytest.raises(FileNotFoundError, match='missing-official-image'):
        build()

@pytest.mark.parametrize('build', [field.map_scenarios, motion.motion_scenarios])
def test_malformed_present_yaml_is_not_ignored(isolated_maps, build):
    directory, _ = isolated_maps
    (directory / 'maze.yaml').write_text('origin: [not-a-number]\n')
    with pytest.raises(ValueError, match='not-a-number'):
        build()

@pytest.mark.parametrize('world', [pytest.param(world, marks=pytest.mark.requires_maps(world))
                                 for world in ['maze', 'depot', 'warehouse']])
def test_real_partial_official_maps_when_installed(isolated_maps, world):
    directory, official = isolated_maps
    yaml = official / f'{world}.yaml'
    if not yaml.is_file():
        pytest.skip('optional official assets absent; no-map and dispatch tests still run')
    image = Path(maps._parse_yaml(yaml)['image'])
    assert not image.is_absolute()
    (directory / image).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(official / image, directory / image)
    shutil.copy2(yaml, directory / yaml.name)
    built = field.map_scenarios() + motion.motion_scenarios()
    assert built and all(s['sim']['world'] == world for s in built)
    assert all(s['frame']['kind'] == 'map' for s in built)

def test_reserved_map_ids_are_static():
    assert len(field.MAP_SCENARIO_IDS) == len(motion.MAP_SCENARIO_IDS) == 4
    assert len(field.MAP_SCENARIO_IDS | motion.MAP_SCENARIO_IDS) == 8

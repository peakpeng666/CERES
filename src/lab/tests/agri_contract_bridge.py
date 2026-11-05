"""Cross-language agri contract check. Invoked by frontend/tools/check-agri-contract.mjs.
Loads the real backend validator, not a copied TS geometry approximation.
"""
import json
from pathlib import Path
import sys

from shapely.geometry import Point, Polygon

sys.dont_write_bytecode = True
# Import the real package so validator helpers keep their normal relative imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from urp_lab.scenarios import validate as validator
results = []
for record in json.loads(Path(sys.argv[1]).read_text()):
    scenario = record['scenario']
    result = {'group': record.get('group', 'default'), 'case': record.get('case', 'default'), 'pattern': record['pattern'], 'seed': record['seed']}
    try:
        assert type(record['seed']) is int and 0 <= record['seed'] <= 0xffffffff
        normalized = validator.validate_instance(scenario['problem_class'], scenario['instance'])
        if scenario['problem_class'] == 'fleet_routing':
            assert len(normalized['tasks']) == scenario['editable']['values']['task_count']
            values = scenario['editable']['values']
            assert normalized['vehicles']['count'] == values['vehicles']
            assert all(t['demand'] == values['demand_per_task'] for t in normalized['tasks'])
            vehicles = scenario['instance']['vehicles']
            capacities = [v['capacity'] for v in vehicles['fleet']] if vehicles.get('fleet') else [vehicles['capacity']]
            assert max(capacities) == values['capacity'], 'builder changed requested capacity'
            radii = [v['robot_radius'] for v in vehicles['fleet']] if vehicles.get('fleet') else [scenario['instance']['robot_radius']]
            assert max(radii) == values['robot_radius'], 'builder changed requested maximum robot radius'
            boundary = Polygon(scenario['instance']['boundary'])
            obstacles = [Polygon(o['polygon']) for o in scenario['instance'].get('obstacles', [])]
        else:
            boundary = Polygon(scenario['instance']['field'])
            obstacles = [Polygon(o) for o in scenario['instance'].get('obstacles', [])]
            start = Point(scenario['instance']['start'])
            assert boundary.covers(start) and not any(o.covers(start) for o in obstacles)
        # These are independent input-geometry checks, not a solver feasibility claim.
        assert boundary.is_valid and boundary.area > 0
        for index, obstacle in enumerate(obstacles):
            assert obstacle.is_valid and obstacle.area > 0 and boundary.covers(obstacle)
            assert all(not obstacle.intersects(other) for other in obstacles[index + 1:]), 'generated obstacles overlap'
        result['valid'] = True
    except Exception as error:
        result.update(valid=False, error=f'{type(error).__name__}: {error}')
    results.append(result)
print(json.dumps({'checked': len(results), 'passed': sum(r['valid'] for r in results), 'results': results}, ensure_ascii=False, indent=2))
sys.exit(0 if all(r['valid'] for r in results) else 1)

"""Strict verdicts derived from the fillet batch for solvers that return the rounded path."""
import json

from ceres_lab.evolve import verdicts as V


def test_strict_from_fillet(tmp_path):
    target = tmp_path / 'fillet'
    target.mkdir()
    rows = [
        {'job_id': 'a', 'config': 'repair2:none/n_swath', 'verdict': 'evaluated', 'identical': True, 'strict_violations_after': 0},
        {'job_id': 'b', 'config': 'repair2:none/n_swath', 'verdict': 'evaluated', 'identical': True, 'strict_violations_after': 3},
        {'job_id': 'c', 'config': 'repair2:none/n_swath', 'verdict': 'unverifiable', 'identical': False},
        {'job_id': 'd', 'config': 'repair2:none/n_swath', 'verdict': 'evaluated', 'identical': True, 'fillet_valid': False},
    ]
    for r in rows:
        (target / f"{r['job_id']}.json").write_text(json.dumps(r), encoding='utf-8')
    (target / 'summary.json').write_text('{}', encoding='utf-8')
    out = V.strict_from_fillet(tmp_path)
    assert out == {'jobs': 4, 'strict_valid': 1}
    got = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in (tmp_path / 'reverify').glob('*.json')}
    assert got['a']['strict_valid_curvature'] and got['a']['identical']
    assert not got['b']['strict_valid_curvature']
    assert not got['c']['identical'] and not got['c']['strict_valid_curvature']
    assert not got['d']['strict_valid_curvature']
    assert V.verdict_map(tmp_path, 'strict')['a'] is True
    assert V.verdict_map(tmp_path, 'strict')['c'] is None

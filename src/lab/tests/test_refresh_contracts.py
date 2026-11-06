"""Read/plan-only regressions: no model, solver, or scientific rerun."""
from collections import Counter
from types import SimpleNamespace
import copy

import pytest

from urp_lab.agent import research_tools as tools
from urp_lab.jobs import resolve_params
from urp_lab.scenarios.validate import PROBLEM_CLASSES
from urp_lab.solvers import catalog, TIME
from urp_lab.studies import StudyManager
from urp_lab.store import Store


def test_agent_problem_classes_and_point_to_point_filter(monkeypatch):
    from urp_lab import scenarios
    assert set(tools.PC['enum']) == set(PROBLEM_CLASSES)
    assert 'motion' not in tools.PC['enum']
    monkeypatch.setattr(scenarios, 'list_scenarios', lambda: [
        {'id': 'motion-fixture', 'problem_class': 'point_to_point'},
        {'id': 'other', 'problem_class': 'coverage'},
    ])
    ctx = SimpleNamespace(lab=SimpleNamespace(catalog=catalog))
    assert [s['id'] for s in tools.list_scenarios(ctx, {'problem_class': 'point_to_point'})['scenarios']] == ['motion-fixture']
    rows = tools.list_algorithms(ctx, {'problem_class': 'point_to_point'})['algorithms']
    assert rows and all(a['problem_classes'] == ['point_to_point'] for a in rows)


def test_agent_select_options_match_catalog_and_validator():
    cat = catalog()
    rows = tools.list_algorithms(SimpleNamespace(lab=SimpleNamespace(catalog=lambda: cat)), {})['algorithms']
    found = set()
    for algo, row in zip(cat, rows):
        assert row['randomness'] == algo['randomness']
        for param, summary in zip(algo['params'], row['params']):
            assert summary['default'] == param['default']
            if param['type'] != 'select':
                continue
            found.add(param['name'])
            assert summary['options'] == param['options']
            for option in summary['options']:
                assert resolve_params(algo, {param['name']: option['value']})[param['name']] == option['value']
            with pytest.raises(ValueError, match='不在可选项'):
                resolve_params(algo, {param['name']: 'invalid-option'})
    assert {'turn_model', 'ground_policy'} <= found


def study_fixture():
    cat = [copy.deepcopy(a) for a in catalog() if a['id'] in ('pyvrp.ils', 'ortools.gls')]
    assert len(cat) == 2
    cat.append(dict(cat[1], id='no-seed-baseline', params=[TIME]))
    sc = {'id': 'fixture', 'problem_class': 'euclidean_cvrp', 'instance': {'coordinates': [[0, 0], [1, 1]]}}
    spec = {'scenarios': ['fixture'], 'algorithms': [a['id'] for a in cat], 'budgets': [1], 'seeds': [11, 29, 47]}
    return cat, sc, spec


def test_study_seed_support_not_parameter_presence():
    cat, sc, spec = study_fixture()
    cells, skipped = StudyManager(None, None).plan(spec, lambda _: sc, cat)
    assert not skipped
    assert Counter(a['id'] for _, a, _ in cells) == {'pyvrp.ils': 3, 'ortools.gls': 1, 'no-seed-baseline': 1}
    assert [p['seed'] for _, a, p in cells if a['id'] == 'pyvrp.ils'] == spec['seeds']
    assert [p['seed'] for _, a, p in cells if a['id'] == 'ortools.gls'] == [42]
    assert all('seed' not in p for _, a, p in cells if a['id'] == 'no-seed-baseline')


def test_create_records_only_planned_cells_and_preserves_historical_design(tmp_path):
    cat, sc, spec = study_fixture()
    submitted = []
    def submit(scenario, instance, items, label):
        submitted.extend(items)
        return None, [f'run-{len(submitted)}-{i}' for i in range(len(items))]
    store = Store(tmp_path / 'studies.sqlite')
    manager = StudyManager(store, SimpleNamespace(submit=submit))
    study = manager.create(spec, lambda _: sc, cat)
    assert study['progress']['total'] == len(submitted) == 5
    # Old evidence is read as stored, never replanned under the new seed policy.
    old = copy.deepcopy(study)
    old['study_id'] = 'historical'
    old['design'] *= 3
    store.put_study(old)
    assert manager.get('historical')['progress']['total'] == 15
    assert store.get_study('historical')['spec'] == old['spec']
    store.close()


def test_seedless_compatibility_seed_is_preserved_and_invalid_params_still_fail():
    cat, sc, spec = study_fixture()
    spec['algorithms'] = [{'id': 'ortools.gls', 'params': {'seed': 99}}]
    spec['budgets'] = [1, 5]
    cells, _ = StudyManager(None, None).plan(spec, lambda _: sc, cat)
    assert [(p['time_limit_s'], p['seed']) for _, _, p in cells] == [(1, 99), (5, 99)]
    spec['algorithms'][0]['params']['unknown'] = 1
    with pytest.raises(ValueError, match='没有参数'):
        StudyManager(None, None).plan(spec, lambda _: sc, cat)


def test_agent_analysis_keeps_inference_gate_without_expanding_run_evidence(monkeypatch):
    from urp_lab import analysis
    block = {'schema': 'urp.analysis-blocks/1', 'n_comparison_blocks': 11, 'n_complete_blocks': 11,
             'n_declared_groups': 0, 'alias_blocks_collapsed': 2, 'inference_eligible': False,
             'reason_code': 'independence_not_established', 'note': '未知来源仅作描述，历史不回填。',
             'blocks': [{'run_ids': ['large-evidence-list']}]}
    friedman = {'status': 'not_testable', 'testable': False, 'reason_code': block['reason_code'],
                'reason': block['note'], 'p_value': None, 'significant': None}
    monkeypatch.setattr(analysis, 'analyze', lambda *args: {
        'n_runs': 77, 'summary': [{'id': 'a', 'mean_rank': 1, 'wins': 11}],
        'block_contract': block, 'tests': {'friedman': friedman},
    })
    out = tools._analysis([])
    assert out['block_contract'] == {k: v for k, v in block.items() if k != 'blocks'}
    assert out['friedman'] == friedman
    assert out['algorithms'][0]['mean_rank'] == 1

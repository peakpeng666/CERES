"""Ledger lineage and rendering (pure functions; the build itself is exercised on real runs)."""
from ceres_lab.evolve import ledger


def test_lineage_chains_back_to_registered_configurations():
    assert ledger.lineage('repair2:none/n_swath')[0] == 'repair:none/n_swath'
    assert ledger.lineage('repair:none/n_swath')[0] == 'full:none/n_swath'
    assert ledger.lineage('repairw:none/field_coverage@2-4-8-16')[0] == 'repair:none/field_coverage'
    assert ledger.lineage('split:boustrophedon/n_swath@0.25')[0] == 'full:boustrophedon/n_swath'
    assert ledger.lineage('fillet:none/n_swath')[0] == 'full:none/n_swath'
    assert ledger.lineage('full:none/n_swath')[0] is None


def test_render_marks_missing_evidence():
    doc = {'generated_utc': 't', 'content_sha256': '0' * 64, 'sources': ['run'],
           'verdicts': {'official': {'instances': 2, 'solvable': 2, 'shapley_method': 'exact', 'unverifiable_cells': 0, 'incomplete': {}},
                        'fillet': {'instances': 2, 'solvable': 1, 'shapley_method': 'exact', 'unverifiable_cells': 0, 'incomplete': {'repair:none/n_swath': 0.5}}},
           'members': [{'id': 'repair:none/n_swath', 'label': '修补·不分解·最少作业行', 'parent': 'full:none/n_swath', 'operation': 'op',
                        'introduced_in': 'run', 'runnable_in_workbench': False, 'status': '研究候选：证据不足',
                        'evidence': {'official': {'feasible': 2, 'shapley': 0.1, 'mean_rho_penalised': 1.0, 'best_on': 2}, 'fillet': None}}]}
    text = ledger.render(doc)
    assert '| 2 / 0.100 | — |' in text and '未完成：1 个成员' in text

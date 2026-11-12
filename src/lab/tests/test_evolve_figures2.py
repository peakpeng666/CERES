"""Round-2 figure builders consume the recorded schemas without touching solver code."""
from ceres_lab.figures.style import Style
from ceres_lab.evolve import figures2 as F


def _vc():
    prof = {'robot_s': {'official': 10, 'fillet': 8, 'fillet_unverifiable': 1, 'strict': 3},
            'tractor_m': {'official': 12, 'fillet': 11, 'fillet_unverifiable': 0, 'strict': 9},
            'sprayer_l': {'official': 9, 'fillet': 8, 'fillet_unverifiable': 0, 'strict': 7}}
    return {'rows': [], 'by_profile': prof, 'total': {'official': 31, 'fillet': 27, 'fillet_unverifiable': 1, 'strict': 19}}


def _adm():
    rules = {}
    for rule in ('R1_single', 'R2_vbs', 'R3_policy', 'R4_residual', 'random'):
        for m in (1, 2, 4):
            rules[f'{rule}@{m}'] = {'static_first_try': (0.9, [0.88, 0.92]), 'static_stop_rho': (1.02, [1.01, 1.03]),
                                    'static_tries': (1.1, [1.0, 1.2]), 'logit_first_try': (0.9, [0.88, 0.92]),
                                    'logit_stop_rho': (1.02, [1.01, 1.03]), 'selection_frequency': {}, 'selections': 5}
    ctrl = {'first_try': (0.88, [0.85, 0.91]), 'stop_rho': (1.01, [1.0, 1.02])}
    return {v: {'rules': rules, 'control_L0': ctrl} for v in ('official', 'fillet')}


def _dyn():
    cells = {}
    for d in ('sparse', 'medium', 'dense'):
        for k in (2, 4):
            cells[f'{d}@k{k}'] = {'n': 3, **{f'{p}_vs_insert_{m}': {'mean_rel': -0.01, 'ci95': [-0.02, -0.001], 'p_two_sided': 0.04}
                                             for p in ('periodic3', 'reopt', 'rollout') for m in ('distance_m', 'mean_response_s', 'makespan_s')},
                                  'rollout_reopt_share': 0.1}
    return {'cells': cells}


def test_three_verdicts_rows():
    fig, cap, rows = F.three_verdicts(_vc(), Style('elsevier', 'single', 'zh'))
    assert len(rows) == 9 and len(fig.axes) == 1
    assert cap['zh'] and cap['en']
    assert {r['verdict'] for r in rows} == {'official', 'fillet', 'strict'}


def test_admission_and_dynamic_rows():
    fig, cap, rows = F.admission_rules(_adm(), Style('elsevier', 'double', 'en'))
    assert len(fig.axes) == 4 and len(rows) == 2 * 5 * 3 * 2
    fig2, cap2, rows2 = F.dynamic_value(_dyn(), Style('elsevier', 'double', 'zh'))
    assert len(fig2.axes) == 2 and len(rows2) == 3 * 2 * 3 * 2
    assert '增量插入' in cap2['zh'] and 'incremental insertion' in cap2['en']


def test_repair_versions_rows():
    counts = {'v1': {'official': 1999, 'fillet': 1607, 'strict': 1023},
              'v2': {'official': 2057, 'fillet': 1800, 'strict': 1200}}
    fig, cap, rows = F.repair_versions(counts, Style('elsevier', 'single', 'zh'))
    assert len(rows) == 6 and len(fig.axes) == 1
    assert {r['check'] for r in rows} == {'official', 'fillet', 'strict'}
    assert '修补 v1' in cap['zh']

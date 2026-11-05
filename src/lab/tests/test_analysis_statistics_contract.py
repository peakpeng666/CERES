"""Synthetic regressions only: these are not experimental runs or research evidence."""
import copy
import json
import math

import pytest

from urp_lab import analysis


def run(instance, algorithm, value=10.0, *, state='done', valid=True, bks=None, seed=1):
    return {'run_id': f'synthetic-{instance}-{algorithm}-{seed}', 'scenario_id': instance,
            'instance_sha256': f'synthetic-{instance}', 'problem_class': 'euclidean_cvrp',
            'algorithm': {'id': algorithm, 'name': algorithm}, 'params': {'time_limit_s': 1, 'seed': seed},
            'state': state, 'status': 'feasible', 'check': {'valid': valid},
            'objective': {'value': value, 'unit': 'unitless'}, 'metrics': {'wall_s': 0.1},
            'scenario_snapshot': {**({'reference': {'bks': bks}} if bks is not None else {}),
                'provenance': {'params': {'analysis_unit': {'schema': 'urp.analysis-unit/1',
                    'group_id': instance, 'independent': True}}}}}


def grid(n, k, *, tied=False, cyclic=False):
    return [run(f'i{i}', f'a{a}', 10.0 if tied else 10.0 + ((a + i) % k if cyclic else a))
            for i in range(n) for a in range(k)]


@pytest.mark.parametrize('failure', ['error', 'timeout', 'canceled', 'invalid', 'nan', 'inf'])
def test_all_failed_instances_never_win_profile_and_are_strict_json(failure):
    rows = grid(2, 3)
    for r in rows:
        if failure in ('error', 'timeout', 'canceled'):
            r['state'] = failure
        elif failure == 'invalid':
            r['check']['valid'] = False
        else:
            r['objective']['value'] = float(failure)
    result = analysis.analyze(rows)
    assert result['n_complete_instances'] == 2
    assert all(all(rho == 0 for rho in curve) for curve in result['performance_profile']['curves'].values())
    assert all(s['success_rate'] == 0 and s['wins'] == 0 for s in result['summary'])
    assert result['tests']['friedman']['reason_code'] == 'all_ties'
    assert result['tests']['friedman']['p_value'] is None
    assert 'nemenyi' not in result['tests']
    json.dumps(result, allow_nan=False)


def test_profile_full_paired_denominator_zero_cost_and_missing_algorithm():
    rows = [run('mixed', 'a', 10), run('mixed', 'b', state='error'),
            run('failed', 'a', state='timeout'), run('failed', 'b', state='error'),
            run('zero', 'a', 0), run('zero', 'b', 2),
            run('zero-tie', 'a', 0), run('zero-tie', 'b', 0),
            run('incomplete', 'a', 1)]
    result = analysis.analyze(rows)
    assert result['n_instances'] == 5 and result['n_complete_instances'] == 4
    profile = result['performance_profile']
    assert profile['n_instances'] == 4
    assert profile['curves']['a'][0] == 3 / 4
    assert profile['curves']['b'][0] == 1 / 4
    assert profile['curves']['b'][-1] == 1 / 4  # positive / zero is never a finite ratio


def test_profile_uses_mean_feasible_cost_not_bks_gap_or_seed_success_rate():
    result = analysis.analyze([run('i', 'a', 10, bks=5), run('i', 'a', 30, bks=5, seed=2),
                               run('i', 'a', state='error', bks=5, seed=3), run('i', 'b', 15, bks=5)])
    pp = result['performance_profile']
    assert pp['curves']['a'][0] == 0 and pp['curves']['b'][0] == 1
    index = pp['taus'].index(20 / 15)
    assert pp['curves']['a'][index] == 1
    assert result['summary'][0]['success_rate'] == pytest.approx(2 / 3)


@pytest.mark.parametrize(('n', 'k', 'reason'), [(1, 3, 'insufficient_blocks_or_algorithms'),
                                             (11, 2, 'insufficient_blocks_or_algorithms'),
                                             (2, 3, 'insufficient_asymptotic_sample'),
                                             (10, 7, 'insufficient_asymptotic_sample'),
                                             (11, 6, 'insufficient_asymptotic_sample')])
def test_small_samples_are_descriptive_only(n, k, reason):
    tests = analysis.analyze(grid(n, k))['tests']
    assert tests['sample_unit'] == 'paired_instance_block'
    assert tests['friedman']['reason_code'] == reason
    assert tests['friedman']['testable'] is False
    assert tests['friedman']['statistic'] is None and tests['friedman']['p_value'] is None
    assert len(tests['mean_ranks']) == k
    assert 'nemenyi' not in tests
    assert analysis.cd_unavailable_reason(tests)


def test_all_ties_preserve_descriptive_ranks_without_nan_or_cd():
    result = analysis.analyze(grid(11, 7, tied=True))
    tests = result['tests']
    assert set(tests['mean_ranks'].values()) == {4.0}
    assert tests['friedman']['reason_code'] == 'all_ties'
    assert tests['friedman']['significant'] is None
    assert 'nemenyi' not in tests
    json.dumps(result, allow_nan=False)


def test_finite_nonsignificance_does_not_generate_cd():
    result = analysis.analyze(grid(14, 7, cyclic=True))
    tests = result['tests']
    assert tests['friedman']['status'] == 'ok'
    assert tests['friedman']['p_value'] == 1
    assert tests['friedman']['significant'] is False
    assert 'nemenyi' not in tests and analysis.cd_unavailable_reason(tests)
    json.dumps(result, allow_nan=False)


def test_significant_friedman_allows_finite_cd_and_ranks():
    result = analysis.analyze(grid(11, 7))
    tests = result['tests']
    assert tests['friedman']['status'] == 'ok'
    assert tests['friedman']['testable'] and tests['friedman']['significant']
    assert 0 <= tests['friedman']['p_value'] < 0.05
    assert math.isfinite(tests['nemenyi']['critical_difference'])
    assert tests['nemenyi']['mean_ranks'] == tests['mean_ranks']
    assert analysis.cd_unavailable_reason(tests) is None
    json.dumps(result, allow_nan=False)


def test_rank_input_does_not_invent_finite_failure_cost():
    rows = grid(11, 7)
    for r in rows:
        if r['algorithm']['id'] == 'a6':
            r['state'] = 'error'
        else:
            r['objective']['value'] *= 1e300
    result = analysis.analyze(rows)
    assert result['tests']['mean_ranks']['a6'] == 7
    assert result['tests']['friedman']['statistic'] == pytest.approx(66)


@pytest.mark.parametrize('mutation', [
    {'friedman': {'p_value': math.nan}}, {'friedman': {'statistic': math.inf}},
    {'friedman': {'testable': False}}, {'friedman': {'significant': False}},
    {'friedman': {'p_value': 0.5}}, {'nemenyi': {'critical_difference': math.nan}},
    {'nemenyi': {'mean_ranks': {'a': math.inf}}}, {'alpha': 0},
])
def test_export_cd_gate_rejects_invalid_or_legacy_payload(mutation):
    tests = analysis.analyze(grid(11, 7))['tests']
    for key, value in mutation.items():
        if isinstance(value, dict):
            tests[key].update(value)
        else:
            tests[key] = value
    assert analysis.cd_unavailable_reason(tests)


def test_nonfinite_scipy_result_is_not_serialized(monkeypatch):
    monkeypatch.setattr(analysis.stats, 'friedmanchisquare', lambda *args: (math.nan, math.nan))
    result = analysis.analyze(grid(11, 7))
    assert result['tests']['friedman']['reason_code'] == 'nonfinite_result'
    assert 'nemenyi' not in result['tests']
    json.dumps(result, allow_nan=False)


def test_gap_count_contract_retains_failures_without_conflating_reference():
    rows = [run('positive', 'a', 10, bks=10), run('positive', 'a', state='error', bks=10, seed=2),
            run('positive', 'b', 20, bks=10),
            run('no-ref', 'a', state='error'), run('no-ref', 'b', state='timeout'),
            run('zero-ref', 'a', 0), run('zero-ref', 'b', 1),
            run('only-a', 'a', 5), run('pending', 'a', state='running')]
    result = analysis.analyze(rows)
    meta = result['ecdf_metadata']
    assert result['n_runs'] == 8 and result['n_complete_instances'] == 3
    assert result['ecdf'] == {'a': [0.0, 0.0], 'b': [100.0]}
    assert meta['normalization'] == 'per_algorithm_eligible_terminal_runs'
    assert meta['counts']['a'] == {'terminal_runs': 5, 'eligible_terminal_runs': 3,
        'finite_gap_runs': 2, 'eligible_failed_runs': 1, 'eligible_nonfinite_gap_runs': 0,
        'missing_reference_runs': 2, 'failed_runs': 2, 'failed_missing_reference_runs': 1}
    assert meta['counts']['b'] == {'terminal_runs': 3, 'eligible_terminal_runs': 1,
        'finite_gap_runs': 1, 'eligible_failed_runs': 0, 'eligible_nonfinite_gap_runs': 0,
        'missing_reference_runs': 2, 'failed_runs': 1, 'failed_missing_reference_runs': 1}
    for a, counts in meta['counts'].items():
        assert counts['terminal_runs'] == counts['eligible_terminal_runs'] + counts['missing_reference_runs']
        assert counts['eligible_terminal_runs'] == counts['finite_gap_runs'] + counts['eligible_failed_runs'] + counts['eligible_nonfinite_gap_runs']
        assert counts['failed_runs'] == counts['eligible_failed_runs'] + counts['failed_missing_reference_runs']
        assert len(result['ecdf'][a]) == counts['finite_gap_runs']
    assert meta['totals']['terminal_runs'] == 8
    assert meta['totals']['failed_runs'] == 3
    assert meta['totals']['missing_reference_runs'] == 4
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('bks', [math.nan, math.inf, -10, True, 'bad'])
def test_invalid_bks_uses_best_feasible_reference(bks):
    result = analysis.analyze([run('i', 'a', 10, bks=bks)])
    assert result['ecdf'] == {'a': [0.0]}
    assert result['per_instance'][0]['reference_kind'] == '本组最好解'
    json.dumps(result, allow_nan=False)


def test_zero_reference_and_nonfinite_gap_are_explicit_not_failures():
    result = analysis.analyze([run('zero', 'a', 1, bks=0), run('overflow', 'a', 1e308, bks=1e-308)])
    c = result['ecdf_metadata']['counts']['a']
    assert c['missing_reference_runs'] == 1 and c['eligible_nonfinite_gap_runs'] == 1
    assert c['failed_runs'] == 0 and result['ecdf']['a'] == []
    assert all(s['gap'] is None for s in result['scatter'])
    json.dumps(result, allow_nan=False)


def test_duplicate_seeds_do_not_increase_friedman_block_count():
    rows = grid(2, 3)
    rows += [{**copy.deepcopy(r), 'run_id': r['run_id'] + '-repeat', 'params': {'seed': 99, 'time_limit_s': 1}} for r in rows]
    result = analysis.analyze(rows)
    assert result['n_runs'] == 12 and result['tests']['n'] == 2
    assert result['tests']['friedman']['testable'] is False


def test_matplotlib_catalog_and_renderer_use_same_gate():
    from urp_lab.figures import catalog, data, study, Style
    for rows in [grid(2, 3), grid(11, 7, tied=True), grid(14, 7, cyclic=True)]:
        ctx = data.Context(study={'study_id': 'synthetic'}, analysis=analysis.analyze(rows))
        item = next(x for x in catalog(ctx) if x['key'] == 'cd_diagram')
        assert not item['available'] and item['reason']
        with pytest.raises(ValueError):
            study.cd_diagram(ctx, Style(), {})
    ctx = data.Context(study={'study_id': 'synthetic'}, analysis=analysis.analyze(grid(11, 7)))
    item = next(x for x in catalog(ctx) if x['key'] == 'cd_diagram')
    assert item['available']
    figure, captions, rows = study.cd_diagram(ctx, Style(), {})
    assert 'nan' not in captions['en'].lower()
    assert len(rows['ranks']) == 8
    figure.clear()
    from urp_lab.figures import render
    for format in ('svg', 'pdf'):
        content, meta = render('cd_diagram', ctx, Style(lang='en'), format)
        assert content and 'nan' not in meta['caption']['en'].lower()


def test_extreme_profile_ratios_do_not_generate_nonfinite_tau():
    for values in [(1.0, 1.7e308), (1e-300, 1e308)]:
        pp = analysis._profiles(['a', 'b'], ['i'], {('i', 'a'): values[0], ('i', 'b'): values[1]})
        assert all(math.isfinite(t) for t in pp['taus'])
        assert pp['curves']['a'][0] == 1 and pp['curves']['b'][0] == 0
        json.dumps(pp, allow_nan=False)


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf, True])
def test_invalid_timing_is_not_synthesized_or_serialized(value):
    rows = grid(2, 3, tied=True)
    for r in rows:
        r['metrics']['wall_s'] = value
    result = analysis.analyze(rows)
    assert all(s['time_mean'] is None for s in result['summary'])
    assert all(s['time'] is None for s in result['scatter'])
    json.dumps(result, allow_nan=False)


def test_pairwise_mixed_failures_count_complete_and_eligible_blocks():
    rows = [run(f'i{i}', a, 10 if a == 'a' else 11) for i in range(5) for a in ('a', 'b')]
    rows += [run('one-fails', 'a', state='error'), run('one-fails', 'b'),
             run('other-fails', 'a'), run('other-fails', 'b', state='timeout'),
             run('both-fail', 'a', state='error'), run('both-fail', 'b', state='error')]
    result = analysis.analyze(rows)
    row = result['tests']['wilcoxon_holm'][0]
    assert row['n_complete_blocks'] == 8 and row['n'] == 5
    assert row['excluded_nonfinite_cost_pairs'] == 3 and row['excluded_nonfinite_differences'] == 0
    assert row['status'] == 'ok' and row['testable']
    assert row['a_better'] == 5 and row['ties'] == 0
    assert result['tests']['holm_family_size'] == 1
    assert math.isfinite(row['p_value']) and math.isfinite(row['p_holm'])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('all_fail', [False, True])
def test_pairwise_zero_or_absent_differences_are_not_invented(monkeypatch, all_fail):
    rows = grid(11, 7, tied=True)
    if all_fail:
        for r in rows:
            r['state'] = 'error'
    def forbidden(*args, **kwargs):
        pytest.fail('Wilcoxon must not be called without finite nonzero differences')
    monkeypatch.setattr(analysis.stats, 'wilcoxon', forbidden)
    result = analysis.analyze(rows)
    for row in result['tests']['wilcoxon_holm']:
        assert row['n_complete_blocks'] == 11 and row['n'] == (0 if all_fail else 11)
        assert row['excluded_nonfinite_cost_pairs'] == (11 if all_fail else 0)
        assert row['reason_code'] == ('insufficient_finite_pairs' if all_fail else 'all_zero_differences')
        assert row['testable'] is False
        assert row['p_value'] is None and row['p_holm'] is None and row['statistic'] is None
    assert result['tests']['holm_family_size'] == 0
    json.dumps(result, allow_nan=False)


def test_pairwise_finite_costs_with_overflowing_difference_are_counted():
    rows = [run(f'i{i}', a, 1e308 if a == 'a' else -1e308) for i in range(5) for a in ('a', 'b')]
    result = analysis.analyze(rows)
    row = result['tests']['wilcoxon_holm'][0]
    assert row['n_complete_blocks'] == 5 and row['n'] == 0
    assert row['excluded_nonfinite_cost_pairs'] == 0 and row['excluded_nonfinite_differences'] == 5
    assert row['testable'] is False and row['p_holm'] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('output', [(math.nan, math.nan), (1.0, math.inf), (1.0, -1.0)])
def test_nonfinite_pairwise_result_has_explicit_status_not_sanitized_significance(monkeypatch, output):
    from types import SimpleNamespace
    monkeypatch.setattr(analysis.stats, 'wilcoxon', lambda *args, **kwargs: SimpleNamespace(statistic=output[0], pvalue=output[1]))
    result = analysis.analyze(grid(11, 7))
    assert all(row['reason_code'] == 'nonfinite_result' and not row['testable'] and row['p_value'] is None
               for row in result['tests']['wilcoxon_holm'])
    assert result['tests']['holm_family_size'] == 0
    json.dumps(result, allow_nan=False)


def test_pairwise_value_error_is_reported_without_losing_descriptive_counts(monkeypatch):
    def invalid(*args, **kwargs):
        raise ValueError('synthetic invalid input')
    monkeypatch.setattr(analysis.stats, 'wilcoxon', invalid)
    result = analysis.analyze(grid(11, 7))
    for row in result['tests']['wilcoxon_holm']:
        assert row['reason_code'] == 'invalid_input' and row['n'] == 11 and row['a_better'] == 11
        assert row['p_holm'] is None and row['significant'] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(('n', 'k', 'mode'), [(2, 3, 'ordered'), (11, 7, 'tied'), (14, 7, 'cyclic'), (11, 7, 'ordered')])
def test_latex_export_handles_every_inference_state(n, k, mode):
    result = analysis.analyze(grid(n, k, tied=mode == 'tied', cyclic=mode == 'cyclic'))
    latex = analysis.latex_table(result)
    assert 'tabular' in latex and 'nan' not in latex.lower()
    assert ('Nemenyi CD' in latex) == (n == 11 and k == 7 and mode == 'ordered')
    assert f'N={n}' in latex
    if mode == 'cyclic':
        assert 'Friedman' in latex and 'no post-hoc comparison' in latex
    elif n == 2 or mode == 'tied':
        assert 'Descriptive ranks only' in latex
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('kind', ['time', 'gap', 'anytime'])
def test_large_finite_observations_keep_correct_medians_and_json(kind):
    rows = [run('i', 'a', 10 if kind == 'time' else 1e306, seed=i, bks=1) for i in (1, 2)]
    for r in rows:
        if kind == 'time':
            r['metrics']['wall_s'] = 1e308
        if kind == 'anytime':
            r.update(trace_contract={'semantic': 'reported_objective'}, trace=[[.1, 1e306], [.2, 1e306]])
    result = analysis.analyze(rows)
    summary = result['summary'][0]
    if kind == 'time':
        assert summary['time_mean'] == 1e308 and summary['time_median'] == 1e308
    else:
        assert summary['gap_median'] == pytest.approx(1e308)
        assert summary['gap_mean'] == pytest.approx(1e308) and summary['gap_sd'] == 0
        assert len(result['ecdf']['a']) == 2  # never discard finite large observations
        if kind == 'anytime':
            assert result['anytime']['median']['a'][-1] == pytest.approx(1e308)
    analysis.latex_table(result)
    json.dumps(result, allow_nan=False)


def test_anytime_uses_same_stable_gap_as_summary():
    r = run('i', 'a', 1.5e308, bks=1e308)
    r.update(trace_contract={'semantic': 'reported_objective'}, trace=[[.1, 1.5e308], [.2, 1.5e308]])
    result = analysis.analyze([r])
    assert result['summary'][0]['gap_mean'] == 50
    assert result['scatter'][0]['gap'] == 50
    assert result['ecdf']['a'] == [50]
    assert result['anytime']['median']['a'][-1] == 50
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('values, expected', [([1e308, 1e308], 1e308),
                                              ([-1.7e308, 1.7e308], 0),
                                              ([5e-324, 5e-324], 5e-324)])
def test_median_avoids_overflow_and_underflow_without_losing_observations(values, expected):
    assert analysis._median(values) == expected


def test_representable_extreme_standard_deviation_is_not_dropped():
    rows = [run('i', 'a', value, seed=i) for i, value in enumerate([0, 1.7e308])]
    result = analysis.analyze(rows)
    cell = result['per_instance'][0]['cells']['a']
    assert cell['sd'] == pytest.approx(1.7e308 / math.sqrt(2))
    assert cell['sd_status'] == 'ok' and cell['runs'] == 2 and cell['failures'] == 0
    json.dumps(result, allow_nan=False)


def test_unrepresentable_true_standard_deviation_is_explicit_not_a_dropped_run():
    rows = [run('i', 'a', value, seed=i, bks=100) for i, value in enumerate([-1.7e308, 1.7e308])]
    result = analysis.analyze(rows)
    cell = result['per_instance'][0]['cells']['a']
    summary = result['summary'][0]
    assert cell['sd'] is None and cell['sd_status'] == 'out_of_range'
    assert summary['gap_sd'] is None and summary['gap_sd_status'] == 'out_of_range'
    assert summary['successes'] == 2 and summary['success_rate'] == 1
    assert summary['gap_mean'] == 0 and summary['gap_median'] == 0
    assert result['ecdf_metadata']['counts']['a']['finite_gap_runs'] == 2
    assert cell['mean'] == 0 and cell['best'] == -1.7e308
    assert 'beyond the numeric output range' in analysis.latex_table(result)
    json.dumps(result, allow_nan=False)


def test_relative_gap_preserves_representable_opposite_sign_difference():
    assert analysis._gap(-1.7e308, 1e308) == pytest.approx(-270)
    r = run('i', 'a', -1.7e308, bks=1e308)
    result = analysis.analyze([r])
    assert result['summary'][0]['gap_mean'] == pytest.approx(-270)
    assert result['ecdf_metadata']['counts']['a']['finite_gap_runs'] == 1
    json.dumps(result, allow_nan=False)


def test_latex_remains_descriptive_when_backend_statistics_fail(monkeypatch):
    monkeypatch.setattr(analysis.stats, 'friedmanchisquare', lambda *args: (math.nan, math.nan))
    result = analysis.analyze(grid(11, 7))
    assert 'Descriptive ranks only' in analysis.latex_table(result)
    assert 'Nemenyi CD' not in analysis.latex_table(result)

"""Benchmark analysis for sets of solver runs: gaps, ranks, Friedman/Nemenyi, Wilcoxon–Holm, performance profiles,
ECDF of gaps, anytime curves, instance-space projection and LaTeX export (Demšar 2006; Dolan & Moré 2002; Smith-Miles 2023)."""
from __future__ import annotations

import math
import statistics as st
from collections import defaultdict

import numpy as np
from scipy import stats
from .planning import comparison_key

SUCCESS_STATUS = ('optimal', 'feasible')


def _value(run) -> float | None:
    from .planning import requires_coverage_recheck
    if requires_coverage_recheck(run):
        return None
    if run.get('state') != 'done' or run.get('status') not in SUCCESS_STATUS:
        return None
    if not (run.get('check') or {}).get('valid'):
        return None
    mc = run.get('map_check')
    if mc is not None and (not mc.get('checked') or mc.get('collision_free') is not True):
        return None
    v = (run.get('objective') or {}).get('value')
    return float(v) if not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) else None


def _label(run, budgets: set) -> str:
    alg = run['algorithm']['id']
    version = run['algorithm'].get('version')
    if run.get('_analysis_version_split'):
        alg += f' v{version if version is not None else "unknown"}'
    extra = {k: v for k, v in run.get('params', {}).items() if k not in ('seed', 'time_limit_s')}
    if extra and run.get('_analysis_parameter_split'):
        import json
        alg += ' ' + json.dumps(extra, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    if len(budgets) > 1:
        return f"{alg} @{run['params'].get('time_limit_s')}s"
    return alg


def _reference(runs_for_instance: list[dict]) -> tuple[float | None, str]:
    for r in runs_for_instance:
        from .planning import without_stale_reference
        snapshot = r.get('scenario_snapshot') or {}
        if isinstance(r.get('instance'), dict):
            snapshot = without_stale_reference(snapshot, r['instance'])
        bks = (snapshot.get('reference') or {}).get('bks')
        if isinstance(bks, (int, float)) and not isinstance(bks, bool) and math.isfinite(bks) and bks >= 0:
            return float(bks), 'BKS'
    vals = [v for v in (_value(r) for r in runs_for_instance) if v is not None]
    return (min(vals), '本组最好解') if vals else (None, '无可行解')


def _finite_time(run: dict) -> float | None:
    value = (run.get('metrics') or {}).get('wall_s')
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _gap(value: float | None, reference: float | None) -> float | None:
    """Finite relative gap without overflowing a representable intermediate.

    Subtraction first preserves precision near the reference. If that subtraction
    overflows (opposite signs), dividing first can still represent the true gap.
    A genuinely out-of-range final percentage remains unavailable and counted.
    """
    if value is None or reference is None or not math.isfinite(value) or not math.isfinite(reference) or reference <= 0:
        return None
    difference = value - reference
    relative = difference / reference if math.isfinite(difference) else value / reference - 1.0
    gap = 100.0 * relative
    return gap if math.isfinite(gap) else None


def _median(values) -> float:
    """Exact-sum midpoint keeps even medians finite and preserves subnormals."""
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        raise st.StatisticsError('no median for empty data')
    mid = n // 2
    return ordered[mid] if n % 2 else st.mean((ordered[mid - 1], ordered[mid]))


def _sample_sd(values) -> tuple[float | None, str]:
    """Python's exact-rational variance avoids intermediate overflow.

    Its correctly rounded square root can still exceed the float output range;
    report that explicitly, retaining all observations and other statistics.
    """
    if len(values) < 2:
        return None, 'insufficient_samples'
    try:
        value = st.stdev(values)
        return (value, 'ok') if math.isfinite(value) else (None, 'out_of_range')
    except OverflowError:
        return None, 'out_of_range'


def _comparison_block(run):
    # A legacy hash alone cannot reconstruct objective/environment semantics.
    # Deduplicate aliases only with full input or matching frozen planning identity.
    if isinstance(run.get('instance'), dict):
        return comparison_key(run)
    frozen = run.get('planning') or {}
    if (frozen.get('schema') == 'urp.planning/1' and frozen.get('comparison_key')
            and frozen.get('instance_sha256') == run.get('instance_sha256')
            and frozen.get('objective_unit') == (run.get('objective') or {}).get('unit')):
        from .planning import fingerprint
        return fingerprint({'frozen_planning': frozen, 'instance_sha256': run.get('instance_sha256'),
                            'problem_class': run.get('problem_class'), 'frame': run.get('frame'),
                            'unit': (run.get('objective') or {}).get('unit')})
    return comparison_key(run)


def _prepare_runs(runs):
    from .planning import fingerprint
    rows = [dict(r) for r in runs if r.get('state') in ('done', 'error', 'timeout', 'canceled')]
    versions, parameters = defaultdict(set), defaultdict(set)
    for r in rows:
        aid = r['algorithm']['id']
        versions[aid].add(r['algorithm'].get('version'))
        parameters[aid].add(fingerprint({k: v for k, v in r.get('params', {}).items() if k not in ('seed', 'time_limit_s')}))
    for r in rows:
        r['_analysis_version_split'] = len(versions[r['algorithm']['id']]) > 1
        r['_analysis_parameter_split'] = len(parameters[r['algorithm']['id']]) > 1
    return rows, {r['params'].get('time_limit_s') for r in rows}


def _unit_declaration(run):
    """Grouping declaration, not a proof of statistical independence.

    The existing scenario JSON import preserves provenance.params. No archive
    is rewritten and a new declaration cannot split an identical input block.
    """
    from .planning import fingerprint
    provenance = (run.get('scenario_snapshot') or {}).get('provenance') or {}
    params = provenance.get('params') or {}
    unit = params.get('analysis_unit')
    if isinstance(unit, dict):
        group = unit.get('group_id')
        if (unit.get('schema') == 'urp.analysis-unit/1' and isinstance(group, str) and group.strip()
                and unit.get('independent') is True):
            return ('declared:' + group.strip(), 'user_declared')
        return None
    generator, seed = provenance.get('generator'), provenance.get('seed')
    if isinstance(seed, bool) or not isinstance(seed, int):
        return None
    if generator == 'urp_lab.scenarios.benchmark:cvrp_synthetic':
        family = 'urp.cvrp_synthetic/1'
    elif isinstance(generator, str) and generator.startswith('src/research/src/urp_research/turn_modes/generate_data.py:grid('):
        family = 'urp.turn_grid/1'
    else:
        return None
    # Reusing a random stream for another size/rule does not create a new unit.
    return ('generator:' + fingerprint([family, seed]), family)


def _block_contract(by_inst, complete):
    blocks, groups = [], defaultdict(list)
    unknown = []
    for key, rs in sorted(by_inst.items()):
        declarations = {_unit_declaration(r) for r in rs}
        unit = next(iter(declarations)) if len(declarations) == 1 and None not in declarations else None
        if unit:
            groups[unit[0]].append(key)
        else:
            unknown.append(key)
        blocks.append({'block_id': key, 'comparison_key': key,
                       'scenario_ids': sorted({r['scenario_id'] for r in rs}),
                       'run_ids': sorted(r['run_id'] for r in rs), 'n_runs': len(rs),
                       'group_id': unit[0] if unit else None, 'declaration_source': unit[1] if unit else None})
    dependent = {g: keys for g, keys in sorted(groups.items()) if len(keys) > 1}
    tested = set(complete)
    inference_unknown = sorted(tested.intersection(unknown))
    inference_dependent = {g: [k for k in keys if k in tested] for g, keys in dependent.items()
                           if sum(k in tested for k in keys) > 1}
    reason = ('independence_not_established' if inference_unknown else
              'dependent_variants_require_design' if inference_dependent else None)
    return {'schema': 'urp.analysis-blocks/1', 'definition': 'full_input_or_frozen_planning_identity; incomplete_legacy_identity_retains_scenario',
            'blocks': blocks, 'n_comparison_blocks': len(blocks), 'n_complete_blocks': len(complete),
            'n_declared_groups': len(groups), 'unknown_blocks': unknown, 'dependent_groups': dependent,
            'inference_unknown_blocks': inference_unknown, 'inference_dependent_groups': inference_dependent,
            'alias_blocks_collapsed': sum(len(b['scenario_ids']) - 1 for b in blocks),
            'inference_eligible': reason is None, 'reason_code': reason,
            'note': '独立单位依据生成协议或用户声明，不是独立性证明。相同完整比较输入强制合并；同 group 的多个变体仅作描述，需预先指定合法组内汇总设计。未知来源可通过场景 JSON 导入 provenance.params.analysis_unit={schema:"urp.analysis-unit/1",group_id:"底本或田块标识",independent:true} 声明；不得给同底本变体伪造不同组。历史记录不回填，修改声明只用于新运行。'}


def analyze(runs: list[dict], alpha: float = 0.05) -> dict:
    if not isinstance(alpha, (int, float)) or isinstance(alpha, bool) or not 0 < alpha < 1:
        raise ValueError('alpha must be finite and between 0 and 1')
    n_input_runs = len(runs)
    runs, budgets = _prepare_runs(runs)
    if not runs:
        return {'empty': True, 'n_runs': 0}
    names = {}
    by_inst: dict[str, list] = defaultdict(list)
    for r in runs:
        key = _comparison_block(r)
        by_inst[key].append(r)
        names[_label(r, budgets)] = r['algorithm']
    algorithms = sorted(names)
    instances = sorted(by_inst)
    reference, ref_kind = {}, {}
    for i in instances:
        reference[i], ref_kind[i] = _reference(by_inst[i])
    # per (instance, algorithm): run values, gaps, times
    cell = defaultdict(lambda: {'values': [], 'gaps': [], 'times': [], 'runs': 0, 'failures': 0, 'eligible_runs': 0, 'eligible_failures': 0})
    for i in instances:
        for r in by_inst[i]:
            c = cell[(i, _label(r, budgets))]
            c['runs'] += 1
            v = _value(r)
            eligible = reference[i] is not None and math.isfinite(reference[i]) and reference[i] > 0
            c['eligible_runs'] += int(eligible)
            t = _finite_time(r)
            if t is not None:
                c['times'].append(t)
            if v is None:
                c['failures'] += 1
                c['eligible_failures'] += int(eligible)
                continue
            c['values'].append(v)
            gap = _gap(v, reference[i])
            if gap is not None:
                c['gaps'].append(gap)
    # mean objective per cell → ranks per instance (failures rank last, ties averaged)
    mean_obj = {(i, a): (st.mean(cell[(i, a)]['values']) if cell[(i, a)]['values'] else math.inf) for i in instances for a in algorithms}
    complete = [i for i in instances if all(cell[(i, a)]['runs'] for a in algorithms)]
    ranks = {}
    for i in complete:
        row = [mean_obj[(i, a)] for a in algorithms]
        for a, rk in zip(algorithms, stats.rankdata(row)):
            ranks[(i, a)] = float(rk)
    summary = []
    for a in algorithms:
        cs = [cell[(i, a)] for i in instances if cell[(i, a)]['runs']]
        gaps = [g for c in cs for g in c['gaps']]
        times = [t for c in cs for t in c['times']]
        n = sum(c['runs'] for c in cs)
        ok = sum(len(c['values']) for c in cs)
        gap_sd, gap_sd_status = _sample_sd(gaps)
        summary.append({
            'algorithm': a, 'id': names[a]['id'], 'name': names[a].get('name'), 'source': names[a].get('source'),
            'instances': len(cs), 'runs': n, 'successes': ok, 'success_rate': ok / n if n else None,
            'gap_mean': st.mean(gaps) if gaps else None, 'gap_sd': gap_sd, 'gap_sd_status': gap_sd_status,
            'gap_median': _median(gaps) if gaps else None, 'gap_best': min(gaps) if gaps else None,
            'time_mean': st.mean(times) if times else None, 'time_median': _median(times) if times else None,
            'mean_rank': st.mean(ranks[(i, a)] for i in complete) if complete else None,
            'wins': sum(1 for i in complete if math.isfinite(mean_obj[(i, a)])
                        and ranks[(i, a)] == min(ranks[(i, b)] for b in algorithms)),
        })
    per_instance = []
    for i in instances:
        sc = by_inst[i][0]
        row = {'instance': i, 'scenario_id': sc['scenario_id'], 'title': (sc.get('scenario_snapshot') or {}).get('title', sc['scenario_id']),
               'reference': reference[i], 'reference_kind': ref_kind[i], 'cells': {}}
        for a in algorithms:
            c = cell[(i, a)]
            if not c['runs']:
                continue
            sd, sd_status = _sample_sd(c['values'])
            row['cells'][a] = {'mean': st.mean(c['values']) if c['values'] else None, 'best': min(c['values']) if c['values'] else None,
                               'sd': sd, 'sd_status': sd_status,
                               'gap_mean': st.mean(c['gaps']) if c['gaps'] else None, 'runs': c['runs'], 'failures': c['failures'],
                               'time_mean': st.mean(c['times']) if c['times'] else None, 'rank': ranks.get((i, a))}
        per_instance.append(row)
    block_contract = _block_contract(by_inst, complete)
    block_contract.update(n_input_runs=n_input_runs, n_terminal_runs=len(runs),
                          n_nonterminal_runs=n_input_runs - len(runs))
    tests = _tests(algorithms, complete, ranks, mean_obj, reference, alpha, block_contract)
    return {
        'empty': False, 'n_runs': len(runs), 'n_instances': len(instances), 'n_complete_instances': len(complete),
        'block_contract': block_contract,
        'algorithms': algorithms, 'summary': summary, 'per_instance': per_instance, 'tests': tests,
        'performance_profile': _profiles(algorithms, complete, mean_obj),
        'ecdf': {a: sorted(g for i in instances for g in cell[(i, a)]['gaps']) for a in algorithms},
        'ecdf_metadata': _ecdf_metadata(algorithms, instances, cell),
        'anytime': _anytime(by_inst, reference, budgets),
        'scatter': [{'algorithm': _label(r, budgets), 'time': _finite_time(r),
                     'gap': _gap(_value(r), reference[k]),
                     'instance': k, 'run_id': r['run_id']} for k, rs in by_inst.items() for r in rs],
        'reference_note': '有有效 BKS 的算例以 BKS 为参照，其余以本组最好可行解为参照；相对差距只用于有限正参照。成功率按全部终态运行计算；性能剖面按齐全实例计数，各算法取可行运行均值，全失败单元不计为有限 τ 下的成功。',
        'comparison_note': '按完整输入、坐标系、目标定义、单位及约束松弛分组；算法版本和非 seed 参数分列。内部代理搜索曲线不作为验算目标曲线。',
    }


def _tests(algorithms, complete, ranks, mean_obj, reference, alpha, block_contract=None):
    k, n = len(algorithms), len(complete)
    mean_ranks = {a: st.mean(ranks[(i, a)] for i in complete) for a in algorithms} if n else {}
    out = {'alpha': alpha, 'k': k, 'n': n, 'mean_ranks': mean_ranks,
           'protocol': 'friedman_chi_square_then_nemenyi', 'sample_unit': 'paired_instance_block',
           'protocol_note': '默认 α=0.05；先检验 Friedman，仅在有效且 p<α 时计算 Nemenyi 临界差。'}
    fr = {'status': 'not_testable', 'testable': False, 'reason': None,
          'statistic': None, 'p_value': None, 'significant': None,
          'method': 'tie_corrected_chi_square', 'min_instances': 11, 'min_algorithms': 7}
    out['friedman'] = fr
    inference_reason = block_contract.get('reason_code') if block_contract else None
    if inference_reason:
        fr['reason'] = block_contract['note']
        fr['reason_code'] = inference_reason
    elif k < 3 or n < 2:
        fr['reason'] = '至少需要 3 个算法和 2 个齐全实例；当前仅展示描述性平均秩。'
        fr['reason_code'] = 'insufficient_blocks_or_algorithms'
    elif all(len({ranks[(i, a)] for a in algorithms}) == 1 for i in complete):
        fr['reason'] = '所有齐全实例内均为平局，无法计算 Friedman 检验；仅展示平均秩。'
        fr['reason_code'] = 'all_ties'
    elif n <= 10 or k <= 6:
        # SciPy documents the chi-square approximation as reliable only for
        # N > 10 and k > 6. Do not label small-sample approximations significant
        # or silently substitute an unimplemented exact/permutation test.
        fr['reason'] = '当前样本未达到卡方近似要求（至少 11 个齐全实例、7 个算法）；仅展示平均秩。'
        fr['reason_code'] = 'insufficient_asymptotic_sample'
    else:
        # Passing finite ranks preserves ties and failure-last ordering without
        # inventing a finite objective sentinel that could beat a real cost.
        cols = [[ranks[(i, a)] for i in complete] for a in algorithms]
        try:
            chi2, p = stats.friedmanchisquare(*cols)
            if math.isfinite(chi2) and math.isfinite(p) and chi2 >= 0 and 0 <= p <= 1:
                fr.update(status='ok', testable=True, statistic=float(chi2), p_value=float(p),
                          significant=bool(p < alpha), reason_code=None)
            else:
                fr.update(reason='Friedman 检验没有有限结果；仅展示平均秩。', reason_code='nonfinite_result')
        except ValueError:
            fr.update(reason='当前数据无法计算 Friedman 检验；仅展示平均秩。', reason_code='invalid_input')
    if fr['status'] == 'ok' and fr['significant']:
        q = float(stats.studentized_range.ppf(1 - alpha, k, np.inf) / math.sqrt(2))
        cd = q * math.sqrt(k * (k + 1) / (6.0 * n))
        if math.isfinite(q) and math.isfinite(cd) and cd > 0:
            out['nemenyi'] = {'status': 'ok', 'q_alpha': q, 'critical_difference': cd,
                              'mean_ranks': mean_ranks}
    if 'nemenyi' not in out:
        out['posthoc_reason'] = (fr['reason'] if not fr['testable'] else
                                 'Friedman 未发现显著差异，不进行 Nemenyi 事后比较。' if not fr['significant'] else
                                 'Nemenyi 临界差无法计算。')
    pairs = []
    for x in range(k):
        for y in range(x + 1, k):
            a, b = algorithms[x], algorithms[y]
            da = [mean_obj[(i, a)] for i in complete]
            db = [mean_obj[(i, b)] for i in complete]
            finite_pairs = [(p, q) for p, q in zip(da, db) if math.isfinite(p) and math.isfinite(q)]
            differences = [p - q for p, q in finite_pairs]
            diffs = [d for d in differences if math.isfinite(d)]
            row = {'a': a, 'b': b, 'n': len(diffs), 'n_complete_blocks': n,
                   'excluded_nonfinite_cost_pairs': n - len(finite_pairs),
                   'excluded_nonfinite_differences': len(finite_pairs) - len(diffs),
                   'a_better': sum(d < 0 for d in diffs), 'b_better': sum(d > 0 for d in diffs),
                   'ties': sum(d == 0 for d in diffs), 'status': 'not_testable', 'testable': False,
                   'method': 'scipy_auto_zsplit', 'statistic': None, 'p_value': None, 'p_holm': None,
                   'significant': None, 'reason': None, 'reason_code': None}
            if inference_reason:
                row.update(reason=block_contract['note'], reason_code=inference_reason)
            elif len(diffs) < 5:
                row.update(reason='有效配对不足 5 个', reason_code='insufficient_finite_pairs')
            elif not any(diffs):
                row.update(reason='有效配对全部平局', reason_code='all_zero_differences')
            else:
                try:
                    res = stats.wilcoxon(diffs, zero_method='zsplit', method='auto')
                    if math.isfinite(res.statistic) and math.isfinite(res.pvalue) and 0 <= res.pvalue <= 1:
                        row.update(status='ok', testable=True, statistic=float(res.statistic), p_value=float(res.pvalue))
                    else:
                        row.update(reason='检验没有有限结果', reason_code='nonfinite_result')
                except ValueError:
                    row.update(reason='当前配对无法检验', reason_code='invalid_input')
            pairs.append(row)
    tested = sorted((r for r in pairs if r['testable']), key=lambda r: r['p_value'])
    m = len(tested)
    running = 0.0
    for j, r in enumerate(tested):
        running = max(running, min(1.0, (m - j) * r['p_value']))
        r['p_holm'] = running
        r['significant'] = running < alpha
    out['wilcoxon_holm'] = pairs
    out['holm_family_size'] = m
    return out


def cd_unavailable_reason(tests: dict) -> str | None:
    """The live panel and export only infer CD after a valid omnibus rejection."""
    fr, nem = tests.get('friedman') or {}, tests.get('nemenyi') or {}
    alpha, p = tests.get('alpha'), fr.get('p_value')
    cd, chi2 = nem.get('critical_difference'), fr.get('statistic')
    finite = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if (fr.get('status') == 'ok' and fr.get('testable') is True and fr.get('significant') is True
            and finite(alpha) and 0 < alpha < 1 and finite(p) and 0 <= p < alpha
            and finite(chi2) and chi2 >= 0 and nem.get('status') == 'ok'
            and finite(cd) and cd > 0 and nem.get('mean_ranks')
            and all(finite(r) for r in nem['mean_ranks'].values())):
        return None
    return tests.get('posthoc_reason') or fr.get('reason') or '临界差图需要有效且显著的 Friedman 检验。'


def _profiles(algorithms, complete, mean_obj):
    """Run means per paired instance, not a BKS-gap or timing profile.

    A failed/nonfinite cell never succeeds at finite tau, including an all-failed
    instance. True zero minima count only exact zero costs. Negative minima are
    outside the nonnegative-cost ratio domain and remain unsolved in the same
    denominator; missing algorithms are excluded by ``complete`` upstream.
    """
    ratios = {a: [] for a in algorithms}
    for i in complete:
        best = min(mean_obj[(i, a)] for a in algorithms)
        for a in algorithms:
            v = mean_obj[(i, a)]
            ratio = math.inf
            if math.isfinite(v) and math.isfinite(best):
                if best > 0:
                    ratio = v / best
                elif best == 0 and v == 0:
                    ratio = 1.0
            ratios[a].append(ratio)
    finite = [r for rs in ratios.values() for r in rs if math.isfinite(r)]
    top = max(finite + [1.0])
    # Avoid overflowing the grid's upper bound even when individual costs are finite.
    upper = top * 1.02 if top <= np.finfo(float).max / 1.02 else top
    taus = sorted({1.0, *np.linspace(1.0, upper, 60).tolist(), *finite}) if complete else []
    return {'taus': taus, 'curves': {a: [sum(math.isfinite(r) and r <= t for r in rs) / len(rs) for t in taus]
                                    for a, rs in ratios.items()} if complete else {},
            'n_instances': len(complete),
            'note': 'ρ(τ)：齐全实例中，可行运行平均成本不超过本实例最好均值 τ 倍的比例；失败保留在分母，零成本只与零成本并列最优。'}


def _ecdf_metadata(algorithms, instances, cell):
    """Run-weighted gap ECDF contract; the legacy ``ecdf`` array stays finite.

    The four disjoint buckets are finite gaps, eligible failures, eligible
    successful runs with nonfinite gaps, and missing/nonpositive references.
    Total failures are also counted separately and may overlap missing reference.
    No unobserved algorithm-instance cell or nonterminal run is imputed.
    """
    counts = {}
    for a in algorithms:
        cs = [cell[(i, a)] for i in instances]
        total = sum(c['runs'] for c in cs)
        eligible = sum(c['eligible_runs'] for c in cs)
        failures = sum(c['failures'] for c in cs)
        eligible_failures = sum(c['eligible_failures'] for c in cs)
        finite = sum(len(c['gaps']) for c in cs)
        counts[a] = {'terminal_runs': total, 'eligible_terminal_runs': eligible,
                     'finite_gap_runs': finite, 'eligible_failed_runs': eligible_failures,
                     'eligible_nonfinite_gap_runs': eligible - eligible_failures - finite,
                     'missing_reference_runs': total - eligible,
                     'failed_runs': failures, 'failed_missing_reference_runs': failures - eligible_failures}
    totals = {key: sum(c[key] for c in counts.values()) for key in next(iter(counts.values()), {})}
    return {'schema': 'urp.gap-ecdf/1', 'normalization': 'per_algorithm_eligible_terminal_runs',
            'weighting': 'one_terminal_run_one_observation', 'denominator_field': 'eligible_terminal_runs',
            'counts': counts, 'totals': totals,
            'note': '差距数组仅含有限值；若绘制累计比例，各算法分母为具有有限正参照的全部终态运行（含失败），每次运行等权。缺失或非正参照单列，不视作失败；分母为零时不定义曲线。'}


def _valid_trace(run: dict) -> bool:
    tr = run.get('trace')
    if not isinstance(tr, list) or not tr:
        return False
    previous, incumbent = -1.0, math.inf
    for point in tr:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return False
        t, v = point
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (t, v)):
            return False
        if t < 0 or t < previous or v > incumbent:
            return False
        previous, incumbent = t, v
    return True


def trace_matches(run: dict) -> bool:
    """Declared objective telemetry, structurally valid and endpoint-consistent."""
    if (run.get('trace_contract') or {}).get('semantic') != 'reported_objective' or not _valid_trace(run):
        return False
    v = (run.get('objective') or {}).get('value')
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return False
    return abs(float(run['trace'][-1][1]) - float(v)) <= 5e-3 * max(1.0, abs(float(v)))


def _anytime(by_inst, reference, budgets):
    # This is a conditional quality curve, not a budget success estimator.
    # Before the first recorded incumbent telemetry is unknown, never +infinity.
    reasons = ('observed_no_solution', 'failed_or_interrupted', 'successful_without_telemetry',
               'proxy_or_unknown_semantics', 'invalid_trace', 'missing_reference')
    curves, counts = defaultdict(list), {}
    horizon = 0.0
    for k, rs in by_inst.items():
        ref = reference[k]
        for r in rs:
            a = _label(r, budgets)
            c = counts.setdefault(a, {'terminal_runs': 0, 'terminal_successes': 0, 'valid_trace_runs': 0,
                                     'reasons': {reason: 0 for reason in reasons}, 'run_ids': []})
            c['terminal_runs'] += 1
            success = _value(r) is not None
            c['terminal_successes'] += int(success)
            tr = r.get('trace') or []
            contract = r.get('trace_contract') or {}
            reason = None
            if not success:
                # Only explicit budget-complete observation can establish no solution.
                reason = ('observed_no_solution' if contract.get('observation') == 'budget_complete_no_solution'
                          and r.get('state') in ('done', 'timeout') and not tr else 'failed_or_interrupted')
            elif not tr:
                reason = 'successful_without_telemetry'
            elif contract.get('semantic') != 'reported_objective':
                reason = 'proxy_or_unknown_semantics'
            elif not trace_matches(r):
                reason = 'invalid_trace'
            elif ref is None or not math.isfinite(ref) or ref <= 0:
                reason = 'missing_reference'
            else:
                curve = [(t, _gap(v, ref)) for t, v in tr]
                if any(g is None for _, g in curve):
                    reason = 'invalid_trace'
            if reason:
                c['reasons'][reason] += 1
                continue
            c['valid_trace_runs'] += 1
            c['run_ids'].append(r['run_id'])
            budget = r.get('params', {}).get('time_limit_s')
            horizon = max(horizon, tr[-1][0], budget if isinstance(budget, (int, float)) and math.isfinite(budget) else 0)
            curves[a].append(curve)
    grid = np.geomspace(1e-3, max(horizon, 1e-2), 48).tolist() if curves else []
    med, observed = {}, {}
    for a, cs in curves.items():
        med[a], observed[a] = [], []
        for t in grid:
            vals = [prior[-1] for c in cs if (prior := [g for tt, g in c if tt <= t])]
            observed[a].append(len(vals))
            med[a].append(_median(vals) if vals else None)
    return {'schema': 'urp.anytime/2', 'conditioning': 'successful_valid_objective_telemetry',
            'grid': grid, 'median': med, 'observed_counts': observed, 'counts': counts,
            'excluded': sum(c['terminal_runs'] - c['valid_trace_runs'] for c in counts.values()),
            'note': '条件质量曲线：仅最终验算成功且目标轨迹合法的运行；各时刻只计已观测到 incumbent 的轨迹，首次记录前未知，不补无穷。相邻记录间沿用最近 incumbent；不是全运行预算成功率。终态失败、无遥测及其他排除原因单列。'}


def features(scenario: dict, instance: dict) -> tuple[list[str], list[float]] | None:
    pc = scenario['problem_class']
    if pc == 'euclidean_cvrp':
        from . import paths
        paths.ensure_research_path()
        from urp_research.cvrp_selection.experiment import features as f
        vals = f(instance)['values']
        return ['log 客户数', '平均需求/容量', '需求离散度', '半径离散度', '近邻距离/半径', '纵横比'], vals
    if pc == 'fleet_routing':
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        from shapely.errors import GEOSException
        try:
            boundary = Polygon(instance['boundary'])
            capacity, count = instance['vehicles']['capacity'], instance['vehicles']['count']
            if (not boundary.is_valid or boundary.area <= 0 or any(isinstance(v, bool) or
                    not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in (capacity, count))):
                return None
            obstacles = [Polygon(o['polygon']) for o in instance.get('obstacles', [])]
            if any(not o.is_valid for o in obstacles):
                return None
            occupied = unary_union(obstacles).area if obstacles else 0.0
            return (['任务数', '车辆数', '总需求/单车容量', '边界面积', '障碍面积占比'],
                    [len(instance['tasks']), count, sum(t['demand'] for t in instance['tasks']) / capacity,
                     boundary.area, occupied / boundary.area])
        except (KeyError, TypeError, ValueError, GEOSException):
            return None  # Explicit unsupported/invalid feature status; no synthetic values.
    if pc == 'turn_routing':
        nodes = {u for u, _, _ in instance['arcs']} | {v for _, v, _ in instance['arcs']}
        triples = sum(1 for _ in instance['arcs']) or 1
        demand = sum(c['demand'] for c in instance['customers'])
        return (['任务数', '节点数', '平均出度', '禁止转弯比例', '转弯惩罚比例', '容量紧度'],
                [len(instance['customers']), len(nodes), len(instance['arcs']) / len(nodes),
                 len(instance['forbidden_turns']) / triples, len(instance['turn_penalties']) / triples, demand / instance['capacity']])
    return None


def instance_space(runs: list[dict]) -> dict:
    """Separate feature and comparison contracts; never average configurations."""
    from .planning import context, fingerprint
    runs, budgets = _prepare_runs(runs)
    panels, omitted = {}, []
    for r in sorted(runs, key=lambda r: (r['scenario_id'], r['run_id'])):
        sc, inst = r.get('scenario_snapshot'), r.get('instance')
        if not sc or not isinstance(inst, dict):
            omitted.append({'run_id': r['run_id'], 'reason': 'missing_full_input'})
            continue
        pc = r.get('problem_class', sc.get('problem_class'))
        f = features({**sc, 'problem_class': pc}, inst)
        if not f or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in f[1]):
            omitted.append({'run_id': r['run_id'], 'reason': 'unsupported_or_nonfinite_features'})
            continue
        semantic = context({**sc, 'problem_class': pc, 'frame': r.get('frame', sc.get('frame'))}, inst, r, resolve_map=False)
        semantic = {k: semantic.get(k) for k in ('problem_class', 'frame', 'objective_rule', 'objective_unit',
                                                  'relaxed_constraints', 'ground_policy', 'validation_policy')}
        semantic['environment_sha256'] = (r.get('planning') or {}).get('environment_sha256')
        budget = r.get('params', {}).get('time_limit_s')
        schema = pc + '.features/1'
        panel_id = fingerprint({'semantics': semantic, 'budget_s': budget, 'feature_schema': schema})
        panel = panels.setdefault(panel_id, {'panel_id': panel_id, 'feature_schema': schema, 'problem_class': pc,
                                            'comparison_contract': semantic, 'budget_s': budget,
                                            'features': f[0], '_blocks': defaultdict(list), '_features': {}})
        key = _comparison_block(r)
        panel['_blocks'][key].append(r)
        panel['_features'][key] = f[1]
    result = []
    for panel_id, panel in sorted(panels.items()):
        by_inst, vectors = panel.pop('_blocks'), panel.pop('_features')
        candidates = sorted({_label(r, budgets) for rs in by_inst.values() for r in rs})
        panel['algorithms'] = candidates
        panel['points'] = []
        for key, rs in sorted(by_inst.items()):
            vals, seen = defaultdict(list), set()
            for r in rs:
                label = _label(r, budgets)
                seen.add(label)
                if (v := _value(r)) is not None:
                    vals[label].append(v)
            means = {a: st.mean(vs) for a, vs in vals.items()}
            missing = sorted(set(candidates) - seen)
            status = ('incomplete_candidates' if missing else 'single_candidate' if len(candidates) < 2 else
                      'no_feasible_solution' if not means else 'ok')
            winners = sorted(a for a in means if means[a] == min(means.values())) if means and status == 'ok' else []
            r0 = rs[0]
            panel['points'].append({'comparison_key': key, 'scenario_id': r0['scenario_id'],
                                    'scenario_ids': sorted({r['scenario_id'] for r in rs}),
                                    'title': (r0.get('scenario_snapshot') or {}).get('title', r0['scenario_id']),
                                    'run_ids': sorted(r['run_id'] for r in rs), 'missing_algorithms': missing,
                                    'mean_objectives': means, 'best': winners[0] if len(winners) == 1 else None,
                                    'winners': winners, 'winner_status': 'tie' if len(winners) > 1 else status})
        if len(vectors) >= 3:
            X = np.array([vectors[k] for k in sorted(by_inst)], dtype=float)
            mu, sd = X.mean(axis=0), X.std(axis=0)
            sd[sd == 0] = 1.0
            Z = (X - mu) / sd
            _, S, Vt = np.linalg.svd(Z, full_matrices=False)
            # Fix SVD sign ambiguity for reproducible panels and exported loadings.
            for row in Vt:
                if row[np.argmax(np.abs(row))] < 0:
                    row *= -1
            P = Z @ Vt[:2].T
            var = S ** 2 / max(1e-12, (S ** 2).sum())
            for point, xy in zip(panel['points'], P):
                point.update(x=float(xy[0]), y=float(xy[1]))
            panel.update(loadings=Vt[:2].T.tolist(), explained_variance=var[:2].tolist(), projection_status='ok')
        else:
            panel.update(projection_status='insufficient_instances', note='本面板至少需要 3 个带相同特征语义的比较实例；原始比较点保留但不投影。')
        panel['method'] = '同特征 schema、目标语义及预算内的标准化 PCA；版本与参数分候选列'
        result.append(panel)
    # Legacy top-level points must not accidentally merge multiple incompatible panels.
    return {'schema': 'urp.instance-space/2', 'points': [], 'panels': result, 'omitted_runs': omitted,
            'note': '按问题特征 schema、比较语义和预算分面；候选版本/参数沿用主表标签。候选不齐全、仅单候选、全失败或并列时不指定唯一胜者。足迹不表示统计显著性。'}


def latex_table(result: dict, caption: str = '算法比较') -> str:
    if result.get('empty'):
        return '% no runs\n'
    def f(v, d=2):
        return '--' if v is None else f'{v:.{d}f}'
    best_gap = min((s['gap_mean'] for s in result['summary'] if s['gap_mean'] is not None), default=None)
    lines = [r'\begin{table}[t]', r'\centering', rf'\caption{{{caption}}}', r'\begin{tabular}{lrrrrr}', r'\toprule',
             r'Algorithm & Success & Gap (\%) & Mean rank & Wins & Time (s) \\', r'\midrule']
    for s in sorted(result['summary'], key=lambda s: (s['mean_rank'] is None, s['mean_rank'] or 0)):
        gap = f(s['gap_mean'])
        if s['gap_sd'] is not None:
            gap += rf' $\pm$ {f(s["gap_sd"])}'
        if best_gap is not None and s['gap_mean'] == best_gap:
            gap = rf'\textbf{{{gap}}}'
        name = s['algorithm'].replace('_', r'\_').replace('#', r'\#')
        lines.append(rf"{name} & {s['successes']}/{s['runs']} & {gap} & {f(s['mean_rank'])} & {s['wins']} & {f(s['time_mean'], 3)} \\")
    lines += [r'\bottomrule', r'\end{tabular}']
    t = result['tests']
    fr = t.get('friedman') or {}
    valid = (fr.get('status') == 'ok' and fr.get('testable') is True
             and isinstance(fr.get('statistic'), (int, float)) and math.isfinite(fr['statistic'])
             and isinstance(fr.get('p_value'), (int, float)) and math.isfinite(fr['p_value']))
    if valid:
        inference = rf"Friedman $\chi^2={fr['statistic']:.2f}$, $p={fr['p_value']:.3g}$"
        if cd_unavailable_reason(t) is None:
            inference += rf"; Nemenyi CD$_{{\alpha={t['alpha']}}}={t['nemenyi']['critical_difference']:.2f}$"
        else:
            inference += '; no post-hoc comparison'
    else:
        inference = 'Descriptive ranks only; Friedman inference unavailable'
    lines.append(rf"\\[2pt]{{\footnotesize {inference} ($k={t['k']}, N={t['n']}$).}}")
    if any(s.get('gap_sd_status') == 'out_of_range' for s in result['summary']):
        lines.append(r'\\[2pt]{\footnotesize Standard deviations beyond the numeric output range are omitted.}')
    lines.append(r'\end{table}')
    return '\n'.join(lines) + '\n'

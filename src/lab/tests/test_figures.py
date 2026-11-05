"""Research figures: every figure renders from records, at the exact journal width, with embedded TrueType fonts,
and figures without data are reported as unavailable instead of being drawn."""
import io
import re
import zipfile

import pytest

from urp_lab import analysis
from urp_lab.figures import BY_KEY, SPECS, Style, catalog, render
from urp_lab.figures import data as D
from urp_lab.figures import registry


def _inst():
    return {
        'boundary': [[0, 0], [100, 0], [100, 60], [0, 60]],
        'plots': [{'id': 'P1', 'name': 'A', 'crop': '玉米', 'rows_deg': 0, 'polygon': [[10, 10], [45, 10], [45, 50], [10, 50]]},
                  {'id': 'P2', 'name': 'B', 'crop': '小麦', 'rows_deg': 90, 'polygon': [[55, 10], [90, 10], [90, 50], [55, 50]]}],
        'obstacles': [{'id': 'O1', 'kind': 'pond', 'polygon': [[60, 30], [66, 30], [66, 36], [60, 36]]},
                      {'id': 'O2', 'kind': 'other', 'nogo': True, 'polygon': [[20, 30], [26, 30], [26, 36], [20, 36]]}],
        'depot': [5, 5],
        'tasks': [{'id': i + 1, 'xy': xy, 'demand': 1, 'label': f'T{i + 1}', 'service_s': 2.0}
                  for i, xy in enumerate([[15, 20], [30, 45], [40, 15], [70, 20], [80, 45], [88, 15]])],
        'vehicles': {'count': 2, 'capacity': 3, 'speed_mps': 1.0, 'trips': 'multi', 'travel': 'free'},
        'objective': 'total_distance', 'robot_radius': 0.5, 'units': 'm',
    }


def _route(i, stops, depot=(5, 5)):
    poly = [list(depot)] + [list(s) for s in stops] + [list(depot)]
    length = float(D.cumlen(poly)[-1])
    return {'vehicle': i, 'vehicle_name': f'车{i + 1}', 'color': '#000', 'sequence': list(range(1, len(stops) + 1)),
            'trips': [list(range(1, len(stops) + 1))], 'services': [{'task': k, 'stop': list(s)} for k, s in stops.items()] if isinstance(stops, dict) else [],
            'polyline': poly, 'cost': length, 'load': len(stops), 'duration_s': length}


def _run(rid, alg, scale=1.0, valid=True):
    inst = _inst()
    a = {1: [15, 20], 2: [30, 45], 3: [40, 15]}
    b = {4: [70, 20], 5: [80, 45], 6: [88, 15]}
    routes = []
    for i, stops in enumerate((a, b)):
        poly = [[5, 5]] + list(stops.values()) + [[5, 5]]
        L = float(D.cumlen(poly)[-1]) * scale
        routes.append({'vehicle': i, 'vehicle_name': f'车{i + 1}', 'color': '#000', 'sequence': list(stops), 'trips': [list(stops)],
                       'services': [{'task': k, 'stop': v} for k, v in stops.items()], 'polyline': poly, 'cost': L, 'load': 3, 'duration_s': L + 6})
    total = sum(r['cost'] for r in routes)
    per = [{'vehicle': r['vehicle_name'], 'color': '#000', 'tasks': 3, 'trips': 1, 'load': 3, 'length_m': r['cost'], 'duration_s': r['duration_s']} for r in routes]
    return {'run_id': rid, 'job_id': 'job1', 'scenario_id': 'sc1', 'problem_class': 'fleet_routing', 'instance_sha256': 'abc' * 10,
            'instance': inst, 'scenario_snapshot': {'id': 'sc1', 'title': '测试田', 'problem_class': 'fleet_routing', 'frame': {'kind': 'field', 'units': 'm'}},
            'algorithm': {'id': alg, 'name': alg, 'version': None}, 'params': {'time_limit_s': 1, 'seed': 1}, 'state': 'done', 'status': 'feasible',
            'objective': {'value': total, 'unit': 'm', 'lower_bound': None}, 'routes': routes, 'check': {'valid': valid, 'errors': [] if valid else ['x']},
            'map_check': None, 'trace': [[0.01, total * 1.2], [0.1, total * 1.05], [0.5, total]],
            'trace_contract': {'semantic': 'reported_objective'},
            'metrics': {'total_distance_m': total, 'makespan_s': max(r['duration_s'] for r in routes), 'vehicles_used': 2, 'trips': 2,
                        'balance_cv': 0.1, 'wall_s': 0.6, 'per_vehicle': per},
            'created_utc': '2026-10-16T00:00:00Z', 'finished_utc': '2026-10-16T00:00:01Z'}


def _sim(run):
    plan_vehicles, trails, tasks = [], {}, []
    for i, r in enumerate(run['routes']):
        name = f'vehicle_{i + 1}'
        path = r['polyline']
        cum = D.cumlen(path)
        ts = list(range(0, int(cum[-1]) + 1))
        import numpy as np
        P = np.asarray(path, float)
        xs = np.interp(ts, cum, P[:, 0]) + 0.02 * np.sin(ts)
        ys = np.interp(ts, cum, P[:, 1])
        trails[name] = [[float(t), float(x), float(y), 0.0] for t, x, y in zip(ts, xs, ys)]
        plan_vehicles.append({'name': name, 'label': f'{i + 1} 号车', 'color': '#000', 'start': [5, 5, 0], 'path': path,
                              'tasks': [{'id': s['task'], 'xy': s['stop'], 'stop': s['stop'], 'service_s': 0} for s in r['services']],
                              'planned_length_m': float(cum[-1]), 'planned_duration_s': float(cum[-1])})
        for s in r['services']:
            tasks.append({'vehicle': name, 'stop': s['stop'], 'id': s['task'], 'label': f"T{s['task']}", 'xy': s['stop'], 'state': 'reached',
                          't_sim': float(10 * s['task']), 'distance_m': 0.1})
    vehicles = [{'name': v['name'], 'label': v['label'], 'color': '#000', 'state': 'done', 'pose': [5, 5, 0], 'distance_m': v['planned_length_m'] * 1.01,
                 'tasks_done': 3, 'tasks_total': 3, 'duration_s': v['planned_duration_s'] * 1.05, 'planned_length_m': v['planned_length_m'],
                 'planned_duration_s': v['planned_duration_s'], 'cross_track_mean_m': 0.01, 'cross_track_max_m': 0.03} for v in plan_vehicles]
    return {'session_id': 'field_test', 'run_id': run['run_id'], 'scenario_id': 'sc1', 'algorithm': 'x', 'world': 'w', 'finished_utc': 'now',
            'sim_time': 300.0, 'real_time_factor': 4.0, 'state': 'succeeded', 'travel': 'free', 'row_collisions': 0,
            'metrics': {'tasks_total': 6, 'tasks_reached': 6, 'vehicles_done': 2, 'makespan_s': 210.0, 'planned_makespan_s': 200.0,
                        'total_distance_m': 400.0, 'planned_total_distance_m': 398.0, 'cross_track_mean_m': 0.01, 'cross_track_max_m': 0.03},
            'vehicles': vehicles, 'tasks': tasks, 'trails': trails, 'execution_plan': {'vehicles': plan_vehicles, 'instance': run['instance']}}


def _study_runs():
    runs = []
    for k in range(4):
        for j, alg in enumerate(('pyvrp.fleet', 'ortools.fleet', 'panel.sweep')):
            r = _run(f'r{k}{j}', alg, scale=1 + 0.05 * j + 0.01 * k)
            r.update(scenario_id=f'sc{k}', instance_sha256=f'{k}' * 30)
            # Distinct full inputs, not four aliases of one identical field.
            r['instance']['boundary'][1][0] += k
            r['instance']['boundary'][2][0] += k
            r['recommendation_at_submission'] = {'advice': {'policy': 'rules-v2', 'recommended': 'pyvrp.fleet',
                                                            'features': [{'key': 'n_tasks', 'name': '任务点', 'value': 6 + k},
                                                                         {'key': 'vehicles', 'name': '车辆', 'value': 2 + (k % 2)},
                                                                         {'key': 'clark_evans', 'name': '聚集度 R', 'value': 0.8 + 0.1 * k}]}}
            runs.append(r)
    return runs


@pytest.fixture()
def ctx():
    run = _run('run1', 'pyvrp.fleet')
    other = _run('run2', 'ortools.fleet', scale=1.04)
    c = D.Context(scenario={'id': 'sc1', 'title': '测试田', 'problem_class': 'fleet_routing', 'instance': _inst(), 'frame': {'kind': 'field'}},
                  run=run, runs=[run, other], sim=_sim(run))
    c.study = {'study_id': 'st1', 'title': '测试课题', 'progress': {'finished': 12}}
    c.study_runs = _study_runs()
    c.analysis = analysis.analyze(c.study_runs)
    c.space = analysis.instance_space(c.study_runs)
    return c


def _pdf_fonts(pdf: bytes) -> tuple[bool, bool]:
    """(TrueType font programs embedded, any Type 3 font present)."""
    return b'/FontFile2' in pdf, re.search(rb'/Subtype\s*/Type3', pdf) is not None


def test_every_figure_with_data_renders_at_journal_width(ctx):
    items = {i['key']: i for i in catalog(ctx)}
    expected_missing = {'map_extraction', 'cd_diagram'}            # no map asset; synthetic 4-block/3-algorithm study is descriptive only
    for s in SPECS:
        assert items[s.key]['available'] is (s.key not in expected_missing), (s.key, items[s.key]['reason'])
    for s in SPECS:
        if s.key in expected_missing:
            continue
        st = Style(preset='ieee', size=s.size, lang='en', dpi=300)
        pdf, meta = render(s.key, ctx, st, 'pdf')
        assert meta['size_mm'][0] == pytest.approx({'single': 88.9, 'double': 181.9}[s.size], abs=0.15), s.key
        truetype, type3 = _pdf_fonts(pdf)
        assert truetype and not type3, s.key                 # IEEE PDF eXpress: embedded, no Type 3
        assert meta['caption']['en'] and meta['caption']['zh'], s.key
        svg, _ = render(s.key, ctx, Style(preset='thesis', size=s.size, lang='zh', dpi=300), 'svg')
        assert svg.startswith(b'<?xml') and b'<text' in svg or b'<g' in svg, s.key


def test_figures_without_data_say_why():
    items = catalog(D.Context())
    assert not any(i['available'] for i in items)
    assert all(i['reason'] for i in items)
    with pytest.raises(LookupError):
        render('routes', D.Context(), Style(), 'svg')


def test_chinese_labels_use_quantity_over_unit(ctx):
    svg, _ = render('workload', ctx, Style(lang='zh'), 'svg')
    assert '行驶里程/m'.encode() in svg
    svg, _ = render('workload', ctx, Style(lang='en'), 'svg')
    assert b'Distance travelled (m)' in svg


def test_bundle_has_vector_raster_source_data_and_manifest(ctx):
    blob, manifest = registry.bundle(ctx, 'elsevier', 'en', ['pdf', 'png'], 300)
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    assert {'captions.md', 'manifest.json', 'README.txt'} <= set(names)
    assert any(n.startswith('figures/') and n.endswith('.pdf') for n in names)
    assert any(n.startswith('source_data/') and n.endswith('.csv') for n in names)
    assert manifest['figures'] and manifest['skipped'][0]['key'] == 'map_extraction'


def test_recommendation_ties_are_not_misses():
    runs = _study_runs()
    for r in runs:                                   # make OR-Tools tie with PyVRP up to floating-point noise
        if r['algorithm']['id'] == 'ortools.fleet':
            base = next(x for x in runs if x['scenario_id'] == r['scenario_id'] and x['algorithm']['id'] == 'pyvrp.fleet')
            r['objective']['value'] = base['objective']['value'] * (1 + 1e-7)
    from urp_lab.figures import study
    t = study.selection_table(runs)
    assert t['n'] == 4 and t['hits'] == 4


def test_traces_of_another_objective_are_not_plotted(ctx):
    from urp_lab.analysis import trace_matches
    other = ctx.runs[1]
    other['trace'] = [[0.01, 9999.0], [0.2, 5000.0]]          # e.g. a solver reporting its internal distance on a makespan map
    assert not trace_matches(other) and trace_matches(ctx.runs[0])
    _, meta = render('convergence', ctx, Style(lang='en'), 'svg')
    assert {r['run_id'] for r in meta['table']['trace']} == {ctx.runs[0]['run_id']}
    assert 'endpoint mismatch' in meta['caption']['en']


def test_figure_history_uses_submitted_snapshot_and_rejects_mixed_sources(monkeypatch):
    from types import SimpleNamespace
    from urp_lab import scenarios
    from urp_lab.services import ServiceError
    run = _run('run1', 'pyvrp.fleet')
    lab = SimpleNamespace(store=SimpleNamespace(get_run=lambda _: run, list_runs=lambda **_: []),
                          fieldsim=SimpleNamespace(results=lambda _: [], result=lambda _: {'run_id': 'different'}))
    def current_map(_):
        pytest.fail('Historical figures must not read the mutable scenario')
    monkeypatch.setattr(scenarios, 'get_scenario', current_map)
    c = registry.build_context(lab, {'run_id': 'run1'})
    assert c.scenario['instance'] == run['instance']
    assert c.scenario['instance'] is not run['instance']
    with pytest.raises(ServiceError, match='场景'):
        registry.build_context(lab, {'run_id': 'run1', 'scenario_id': 'different'})
    with pytest.raises(ServiceError, match='会话'):
        registry.build_context(lab, {'run_id': 'run1', 'session_id': 'other'})


@pytest.mark.parametrize('source', ['run', 'sim', 'study', 'scenario', 'geometry'])
def test_figure_digest_invalidates_when_record_content_changes(ctx, source):
    before = ctx.digest()
    if source == 'run': ctx.run['routes'][0]['cost'] += 1
    elif source == 'sim': ctx.sim['trails']['vehicle_1'][1][1] += 1
    elif source == 'study': ctx.study_runs[0]['objective']['value'] += 1
    elif source == 'scenario': ctx.scenario['title'] = 'changed title'
    else: ctx.geometry = {'boundary': [[0, 0], [1, 1]]}
    assert ctx.digest() != before


def test_single_instance_comparison_rejects_different_inputs(ctx):
    ctx.runs[1]['instance']['vehicles']['count'] += 1
    assert registry._need_runs(ctx) is not None
    assert registry._same_instance(ctx) is not None


def test_control_errors_use_measured_cycles_and_cancellation_has_missing_duration(ctx):
    ctx.sim['control_traces'] = {'vehicle_1': [[0, 0, None, 0, 0, 'waiting'], [1, 0, .02, 1, 0, 'driving']],
                                 'vehicle_2': [[0, 0, None, 0, 0, 'waiting']]}
    _, meta = render('tracking_error', ctx, Style(lang='en'), 'svg')
    assert meta['table']['cross_track'] == [{'vehicle': 'vehicle_1', 't_s': 1.0, 'cross_track_m': .02, 'source': 'control_cycle'}]
    ctx.sim['metrics']['makespan_s'] = None
    ctx.sim['state'] = 'canceled'
    for v in ctx.sim['vehicles']:
        v['duration_s'] = None
    _, meta = render('plan_vs_exec', ctx, Style(lang='en'), 'svg')
    assert '→ — s' in meta['caption']['en']
    ctx.sim['control_traces']['vehicle_1'].pop()
    assert not next(x for x in catalog(ctx) if x['key'] == 'tracking_error')['available']


def test_progress_requires_finished_service_and_uses_common_mission_origin(ctx):
    ctx.sim['execution_started_sim_s'] = 10
    for task in ctx.sim['tasks']:
        task['serviced_at'] = None
    ctx.sim['tasks'][0].update(t_sim=11, serviced_at=15)
    _, meta = render('task_progress', ctx, Style(lang='en'), 'svg')
    assert [r for r in meta['table']['arrivals'] if r['series'] == 'executed'] == [{'series': 'executed', 't_s': 5.0}]
    assert 'completed service' in meta['caption']['en']


def test_figure_cache_bounds_bytes_and_does_not_share_mutable_metadata(ctx, monkeypatch):
    registry._CACHE.clear()
    _, meta = render('workload', ctx, Style(), 'svg')
    original = meta['caption']['zh']
    meta['caption']['zh'] = 'tampered by caller'
    assert render('workload', ctx, Style(), 'svg')[1]['caption']['zh'] == original
    registry._CACHE.clear()
    monkeypatch.setattr(registry, 'CACHE_MAX_BYTES', 1)
    render('workload', ctx, Style(), 'svg')
    assert not registry._CACHE


def test_track_map_does_not_index_pose_array_by_control_cycle_index(ctx):
    ctx.sim['control_traces'] = {'vehicle_1': [[i / 100, 0, .02, 1, 0, 'driving'] for i in range(1000)]}
    ctx.sim['control_traces']['vehicle_1'][-1][2] = 10
    expected = D.cross_track(ctx.sim, prefer_control=False)
    assert len(expected['vehicle_1']['e']) == len(ctx.sim['trails']['vehicle_1'])
    _, meta = render('sim_tracks', ctx, Style(lang='en'), 'svg')
    w = meta['table']['worst_point'][0]
    assert w['cross_track_m'] < .1  # map geometry, not the unrelated control index/value
    assert 'separately from control-cycle' in meta['caption']['en']


def test_refresh_anytime_export_denominator_and_malformed_convergence(ctx):
    import copy
    from urp_lab.figures import study, charts
    good = copy.deepcopy(ctx.runs[0])
    failed = dict(copy.deepcopy(good), run_id='synthetic-error', state='error', trace=[])
    ctx.analysis = analysis.analyze([good, failed])
    _, cap, data = study.anytime_median(ctx, Style(lang='en'), {})
    assert 'traces=1/2' in cap['en']
    assert 'not infinity' in cap['en']
    assert 'observed_trace_count' in data['anytime'][0]
    assert data['telemetry_counts'][0]['failed_or_interrupted'] == 1
    malformed = dict(copy.deepcopy(good), run_id='synthetic-malformed', trace=[[.1]])
    ctx.runs = [good, malformed]
    _, cap, data = charts.convergence(ctx, Style(lang='en'), {})
    assert 'invalid structure/timing' in cap['en']
    assert {r['run_id'] for r in data['trace']} == {good['run_id']}


def test_refresh_space_export_renders_all_legal_panels_and_provenance(ctx):
    import copy
    from urp_lab.figures import study
    second = copy.deepcopy(ctx.study_runs)
    for r in second:
        r['params']['time_limit_s'] = 10
        r['run_id'] += '-10s'
    ctx.space = analysis.instance_space([*ctx.study_runs, *second])
    fig, cap, data = study.instance_space(ctx, Style(lang='en'), {})
    assert len(fig.axes) == 2
    assert len(data['panel_contracts']) == 2
    assert all(p['run_ids'] and p['comparison_key'] for p in data['points'])
    assert 'fleet_routing.features/1' in cap['en']
    aliases = [dict(copy.deepcopy(ctx.study_runs[0]), scenario_id=f'alias-{k}', run_id=f'alias-{k}') for k in range(4)]
    ctx.space = analysis.instance_space(aliases)
    assert ctx.space['panels'][0]['projection_status'] == 'insufficient_instances'
    with pytest.raises(ValueError):
        study.instance_space(ctx, Style(), {})


def test_fleet_feature_schema_rejects_degenerate_and_missing_capacity():
    import copy
    inst = _inst()
    assert analysis.features({'problem_class': 'fleet_routing'}, inst)
    for change in ('capacity', 'boundary'):
        bad = copy.deepcopy(inst)
        if change == 'capacity':
            bad['vehicles'].pop('capacity')
        else:
            bad['boundary'] = [[0, 0], [1, 0], [2, 0]]
        assert analysis.features({'problem_class': 'fleet_routing'}, bad) is None

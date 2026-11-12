"""Dashboard components and shared terminology: scales, labels, and rendering with missing or real inputs."""
import re

import pytest

from ceres_lab.evolve import dashboard as D
from ceres_lab.evolve import plans
from ceres_lab.evolve import terms as T


def test_every_portfolio_configuration_has_a_readable_label_in_both_languages():
    names = list(plans.COVERAGE_PORTFOLIO) + list(getattr(plans, 'REPAIR_PORTFOLIO', {})) + list(getattr(plans, 'CANDIDATE_POOL', {}))
    for n in names:
        for lang in ('zh', 'en'):
            label = T.config_label(n, lang)
            assert ':' not in label and not label.startswith(('full', 'repair', 'split', 'fillet', 'single')), (n, label)
            assert '/' not in label.split('（')[0].split(' (')[0], (n, label)
    assert T.config_label('full:boustrophedon/n_swath') == '牛耕式分解 · 最少作业行数'
    assert T.config_label('repair:none/field_coverage', 'en') == 'No decomposition, maximum coverage + local Dubins repair'
    assert T.policy_label('logit:agri') == '逻辑回归 · 农艺结构特征'


@pytest.mark.parametrize('lo,hi', [(0, 17.0), (0, 712), (-24.1, 1.5), (-9.4, 2.3), (0, 0.4)])
def test_ticks_are_round_and_cover_the_range(lo, hi):
    tk = D.ticks(lo, hi)
    assert tk[0] <= lo + 1e-9 and tk[-1] >= hi - 1e-9
    assert 3 <= len(tk) - 1 <= 7
    steps = {round(b - a, 9) for a, b in zip(tk, tk[1:])}
    assert len(steps) == 1
    if lo < 0 < hi:
        assert any(abs(t) < 1e-12 for t in tk)


def test_bars_are_drawn_to_one_linear_scale_with_open_intervals_marked():
    tk = [0, 10, 20, 30, 40, 50]
    rows = [{'label': 'a', 'value': 25, 'cls': 's-accent', 'text': '25%', 'tip': 't', 'lo': 10, 'hi': 40},
            {'label': 'b', 'value': 44.1, 'cls': 's-neutral', 'text': '44.1%', 'tip': 't', 'lo': 3.9, 'hi': 178}]
    out = D.bars(rows, tk, 'MAPE / %')
    widths = [float(w) for w in re.findall(r'class="bar [^"]+" style="width:([0-9.]+)%', out)]
    assert widths == [50.0, 88.2]
    assert out.count('ci open') == 1
    assert 'MAPE / %' in out


def test_forest_marks_zero_and_places_estimates():
    out = D.forest([{'label': 'x', 'tip': 't', 'cells': {'a': (-5.0, -10.0, 0.0)}}], [('a', 'a')], [-10, -5, 0, 5], 'Δ / %')
    assert 'class="g z"' in out
    assert re.search(r'class="pt ka" style="left:33.33%', out)


def test_status_labels():
    assert D._status('已上线：经验静态尝试顺序第 3 位') == ('已部署 · 第 3 位', 'chip-good')
    assert D._status('工作台现行默认')[0] == '默认配置'
    assert D._status('研究候选：证据支持准入（official 判定下 Shapley > 0），待负责方集成后才能上线')[0] == '候选 · 支持纳入'
    assert D._status('研究候选：证据不足')[0] == '候选'


def test_page_renders_without_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(D, 'RUNS', tmp_path)
    monkeypatch.setattr(D, 'LEDGER', tmp_path / 'missing.json')
    html = D.page()
    assert html.startswith('<title>持续进化研究看板</title>')
    assert '<section id="s1"' not in html and '开放问题' in html


def test_page_renders_real_results_when_present():
    if not (D.RUNS / D.SOURCES['audit']).is_file():
        pytest.skip('audit results not present')
    html = D.page()
    for sid in ('s1', 's2', 's3'):
        assert f'<section id="{sid}"' in html
    body = re.sub(r'<style>.*?</style>|<script>.*?</script>|<footer>.*?</footer>', '', html, flags=re.S)
    visible = re.sub(r'<[^>]+>', ' ', body)
    assert 'nan' not in visible.lower()
    assert not re.search(r'(^|[\s（(，,])-\d', visible)  # negative numbers use the typographic minus

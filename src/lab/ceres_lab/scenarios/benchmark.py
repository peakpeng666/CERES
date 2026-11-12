"""Benchmark-frame scenarios: project turn-routing grids (regenerated) and the panel's synthetic Euclidean CVRP set."""
from __future__ import annotations

import math
import random

from .. import paths

BENCH_FRAME = {'kind': 'benchmark', 'units': 'unitless'}
NO_SIM = {'supported': False, 'world': None, 'spawn': None, 'reason': '基准算例坐标无物理单位，不能直接送入仿真（仅地图坐标场景可仿真）'}
GENERATOR = 'src/research/src/ceres_research/turn_modes/generate_data.py'
SMOKE = [(3, 3, 5, 8000 + k, ('uturn', 'left_cost', 'left_ban')[k % 3]) for k in range(6)]
FORMAL = [(r, c, n, 10000, rule) for r, c, n in ((3, 3, 5), (3, 4, 6), (4, 4, 7)) for rule in ('uturn', 'left_cost', 'left_ban')]
RULES = {'uturn': '禁止掉头', 'left_cost': '禁止掉头 + 左转惩罚 2', 'left_ban': '禁止掉头 + 左转惩罚 5，约 15% 左转禁止', 'none': '无转弯约束'}
CVRP_FAMILIES = {'uniform': '均匀分布', 'clustered': '聚簇分布'}
CVRP_SIZES = (20, 50, 100, 200)
CVRP_CAPACITY = 50


def _gen():
    paths.ensure_research_path()
    from ceres_research.turn_modes import generate_data
    return generate_data


def _turn_instance(obj: dict) -> dict:
    inst = {k: v for k, v in obj.items() if k not in ('split',)}
    if 'coordinates' in inst:
        inst['coordinates'] = {str(k): [float(v[0]), float(v[1])] for k, v in inst['coordinates'].items()}
    for c in inst['customers']:
        c.setdefault('label', f"T{c['id']}")
    inst.setdefault('node_kinds', {})
    return inst


def _turn_scenario(obj: dict, split: str, call: str, note_extra: str = '') -> dict:
    inst = _turn_instance(obj)
    layout = None
    if not inst.get('coordinates'):
        nodes = sorted({u for u, v, _ in inst['arcs']} | {v for u, v, _ in inst['arcs']})
        inst['coordinates'] = {str(v): [float(i), 0.0] for i, v in enumerate(nodes)}
        layout = '原夹具无坐标；此处坐标仅为面板绘图布局，不参与成本'
    inst['node_kinds'] = {k: ('depot' if int(k) == inst['depot'] else 'grid') for k in inst['coordinates']}
    shape = obj.get('shape')
    rule = obj.get('rule')
    title = f"转弯路网 {obj['id']}"
    if split == 'fixtures':
        title = f"夹具 {obj['id']}"
    summary = f"项目生成器 {split} 拆分：{len(inst['customers'])} 个任务" + (f"，{shape[0]}×{shape[1]} 网格，{RULES.get(rule, rule)}" if shape else '')
    note = ('由项目生成器按冻结参数重新生成（generate_data.' + call + '）；与归档输入逐字节一致性未验证：归档数据未恢复。'
            + (layout + '。' if layout else '') + note_extra)
    return {
        'id': f"bench-{obj['id']}", 'title': title, 'summary': summary, 'problem_class': 'turn_routing',
        'frame': dict(BENCH_FRAME), 'cost_unit': 'synthetic_integer_cost',
        'provenance': {'generator': f'{GENERATOR}:{call}', 'params': {'split': split, 'shape': shape, 'rule': rule,
                                                                     'n_tasks': len(inst['customers'])},
                       'seed': obj.get('seed'), 'note': note},
        'sim': dict(NO_SIM), 'instance': inst,
        'editable': {'tasks': True, 'depot': True, 'capacity': True, 'demands': True},
        'layers': {'labels': [{'xy': inst['coordinates'][str(c['node'])], 'text': c['label']} for c in inst['customers']]},
    }


def turn_scenarios() -> list[dict]:
    g = _gen()
    out = []
    for r, c, n, seed, rule in SMOKE:
        out.append(_turn_scenario(g.grid(r, c, n, seed, rule), 'smoke', f'grid({r},{c},{n},{seed},{rule!r})'))
    for r, c, n, seed, rule in FORMAL:
        out.append(_turn_scenario(g.grid(r, c, n, seed, rule), 'formal', f'grid({r},{c},{n},{seed},{rule!r})',
                                  '正式拆分共 36 个（种子 10000–10003），此处只收录种子 10000 的代表子集。'))
    for obj in g.fixtures():
        out.append(_turn_scenario(obj, 'fixtures', 'fixtures()'))
    return out


def cvrp_synthetic(family: str, n: int, seed: int) -> dict:
    rng = random.Random(seed)
    depot = [500.0, 500.0]
    if family == 'uniform':
        pts = [[float(rng.randint(0, 1000)), float(rng.randint(0, 1000))] for _ in range(n)]
    else:
        centers = [(rng.uniform(100, 900), rng.uniform(100, 900)) for _ in range(max(3, n // 25))]
        pts = []
        for _ in range(n):
            cx, cy = centers[rng.randrange(len(centers))]
            pts.append([float(min(1000, max(0, round(rng.gauss(cx, 60))))), float(min(1000, max(0, round(rng.gauss(cy, 60)))))])
    demands = [0] + [rng.randint(1, 10) for _ in range(n)]
    return {'coordinates': [depot] + pts, 'demands': demands, 'capacity': CVRP_CAPACITY, 'depot': 0,
            'fleet': 'unlimited_identical_vehicles', 'objective': 'sum_unrounded_euclidean_distance',
            'labels': ['D'] + [str(i) for i in range(1, n + 1)]}


def cvrp_scenarios() -> list[dict]:
    out = []
    for fi, family in enumerate(CVRP_FAMILIES):
        for si, n in enumerate(CVRP_SIZES):
            seed = 2026101500 + fi * 10 + si
            inst = cvrp_synthetic(family, n, seed)
            out.append({
                'id': f'bench-cvrp-{family}-n{n}', 'title': f'欧氏 CVRP · {CVRP_FAMILIES[family]} n={n}',
                'summary': f'面板合成算例：{n} 个任务，坐标 [0,1000]（无单位），仓库居中，容量 {CVRP_CAPACITY}',
                'problem_class': 'euclidean_cvrp', 'frame': dict(BENCH_FRAME), 'cost_unit': 'euclidean',
                'provenance': {'generator': 'ceres_lab.scenarios.benchmark:cvrp_synthetic',
                               'params': {'family': family, 'n': n, 'capacity': CVRP_CAPACITY, 'demand_range': [1, 10],
                                          'depot': [500, 500], 'cluster_sigma': 60 if family == 'clustered' else None},
                               'seed': seed,
                               'note': '面板合成算例，不是历史 24 算例面板（cvrp-selection-crc 数据未恢复）；不代表任何已发表结果。'},
                'sim': dict(NO_SIM), 'instance': inst,
                'editable': {'tasks': True, 'depot': True, 'capacity': True, 'demands': True},
                'layers': {},
            })
    return out

"""Display names in figures: Chinese for theses, English for journals (the workbench UI names are Chinese)."""
from __future__ import annotations

import re

from .style import Style
from ..algorithm_ids import canonical_algorithm_id

ALG_EN = {
    'urp.turn_mode_dp': 'Turn-mode layered DP', 'urp.raw_oracle': 'Exact state search', 'urp.reset_dp': 'Heading-relaxed DP',
    'pyvrp.ils+urp.layered': 'PyVRP ILS + turn layering',
    'pyvrp.hgs+urp.layered': 'PyVRP (legacy ID) + turn layering', 'ortools.gls+urp.layered': 'OR-Tools GLS + turn layering',
    'pyvrp.ils': 'PyVRP ILS', 'pyvrp.hgs': 'PyVRP (legacy ID)', 'ortools.gls': 'OR-Tools GLS', 'urp.js.savings': 'Clarke–Wright savings',
    'urp.js.nearest_neighbor': 'Nearest neighbour', 'f2c.full': 'Fields2Cover (full)', 'f2c.boustrophedon': 'Fields2Cover boustrophedon',
    'f2c.snake': 'Fields2Cover snake', 'panel.boustrophedon': 'Boustrophedon (baseline)', 'pyvrp.fleet': 'PyVRP fleet',
    'ortools.fleet': 'OR-Tools fleet', 'panel.savings_fleet': 'Savings + 2-opt', 'panel.sweep': 'Sweep', 'panel.cluster_first': 'Cluster-first',
    'grid.astar': 'A*', 'grid.dijkstra': 'Dijkstra', 'grid.theta_star': 'Theta*', 'ompl.rrt_connect': 'RRT-Connect', 'ompl.rrt_star': 'RRT*',
    'ompl.informed_rrt_star': 'Informed RRT*', 'ompl.bit_star': 'BIT*', 'ompl.prm_star': 'PRM*', 'ompl.fmt': 'FMT*', 'ompl.kpiece': 'KPIECE',
}
# fixed order → every algorithm keeps one colour / marker / dash across all figures of a paper
ALG_ORDER = ['pyvrp.fleet', 'ortools.fleet', 'panel.savings_fleet', 'panel.sweep', 'panel.cluster_first', 'pyvrp.ils', 'ortools.gls',
             'urp.js.savings', 'urp.js.nearest_neighbor', 'urp.turn_mode_dp', 'urp.reset_dp', 'urp.raw_oracle', 'pyvrp.ils+urp.layered',
             'ortools.gls+urp.layered', 'f2c.full', 'f2c.boustrophedon', 'f2c.snake', 'panel.boustrophedon', 'grid.astar', 'grid.dijkstra',
             'grid.theta_star', 'ompl.rrt_connect', 'ompl.rrt_star', 'ompl.informed_rrt_star', 'ompl.bit_star', 'ompl.prm_star', 'ompl.fmt', 'ompl.kpiece']
FEATURE_EN = {'n_tasks': 'tasks', 'vehicles': 'vehicles', 'min_trips': 'min. trips', 'obstacle_ratio': 'obstacle ratio', 'clark_evans': 'Clark–Evans R',
              'capacity_tightness': 'capacity tightness', 'area_ha': 'area'}


def alg_index(alg_id: str) -> int:
    base = canonical_algorithm_id(alg_id.partition(' @')[0])
    if base in ALG_ORDER:
        return ALG_ORDER.index(base)
    return len(ALG_ORDER) + sum(map(ord, base)) % 7


OBSTACLE_ZH = {'pond': '池塘', 'building': '建筑', 'greenhouse': '温室', 'tree': '树', 'ditch': '水渠', 'rock': '石块', 'pole': '电杆', 'other': '障碍'}
OBSTACLE_EN = {'pond': 'Pond', 'building': 'Building', 'greenhouse': 'Greenhouse', 'tree': 'Tree', 'ditch': 'Ditch', 'rock': 'Rock', 'pole': 'Pole', 'other': 'Obstacle'}


def alg_label(alg_id: str, st: Style, zh_name: str | None = None) -> str:
    """Study labels may carry a budget suffix ('pyvrp.fleet @3s')."""
    base, _, budget = alg_id.partition(' @')
    name = (zh_name or base) if st.lang == 'zh' else ALG_EN.get(base, base)
    return f'{name} ({budget})' if budget else name


def alg_name(run: dict, st: Style) -> str:
    a = run.get('algorithm') or {}
    zh = a.get('name') or a.get('id', '')
    if a.get('id', '').startswith('pyvrp.') and a.get('version') == '0.14.0' and 'HGS' in zh:
        zh = zh.replace('HGS', 'ILS')          # older records carried the wrong method name
    if st.lang == 'zh':
        return zh
    aid = a.get('id', '')
    if aid in ('pyvrp.hgs', 'pyvrp.hgs+urp.layered'):
        if a.get('version') == '0.14.0':
            return ALG_EN[canonical_algorithm_id(aid)]
        # An old ID alone is not evidence of the historical solver method.
        return a.get('name') or ALG_EN[aid]
    return ALG_EN.get(aid, aid)


def obstacle_name(kind: str, st: Style) -> str:
    return (OBSTACLE_ZH if st.lang == 'zh' else OBSTACLE_EN).get(kind, kind)


CIRCLED = {chr(0x2460 + k): k + 1 for k in range(20)}   # ① … ⑳


def vehicle_name(i: int, name: str, st: Style) -> str:
    """Short, consistent vehicle names across figures: 车1 / V1 (the UI's ① 号车 is too wide for axes)."""
    m = re.search(r'(\d+)', name or '')
    n = int(m.group(1)) if m else next((v for c, v in CIRCLED.items() if c in (name or '')), i + 1)
    return f'车{n}' if st.lang == 'zh' else f'V{n}'

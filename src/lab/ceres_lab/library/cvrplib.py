"""CVRPLIB (galgos.inf.puc-rio.br/cvrplib): full instance catalog with BKS, on-demand download, scenario builder."""
from __future__ import annotations

import copy
import json
import re
import urllib.request
from functools import lru_cache
from pathlib import Path

from . import CACHE
from .references import cite

DATA = Path(__file__).resolve().parent / 'data' / 'cvrplib-catalog.json'
SITE = 'https://galgos.inf.puc-rio.br/cvrplib'
DIR = CACHE / 'cvrplib'
MAX_CUSTOMERS = 1000
SET_NOTES = {
    'A': '经典随机算例', 'B': '聚簇算例', 'P': 'A/B 改造的小容量算例', 'E': 'Christofides–Eilon 经典算例', 'F': '真实配送数据',
    'M': '大规模 Christofides 算例', 'CMT': '含路长/服务时间约束的 CMT 算例', 'tai': 'Taillard 结构化算例', 'Golden': '大规模几何结构算例',
    'Li': '超大规模同心圆算例', 'X': '当前最常用的 100 个多样化基准（100–1000 客户）', 'AGS': '超大规模真实结构算例',
    'DIMACS': 'DIMACS 第 12 届挑战赛算例', 'XML': '机器学习评测用 10000 个小算例', 'XL': '2026 年新增超大规模算例',
    'Solomon': '带时间窗 VRPTW 经典算例', 'Homberger': '大规模 VRPTW 算例',
}


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads(DATA.read_text(encoding='utf-8'))


def _by_name() -> dict[str, dict]:
    return {i['name']: i for i in catalog()['instances']}


def _cached(name: str) -> bool:
    return (DIR / f'{name}.vrp').is_file()


def status() -> dict:
    cat = catalog()
    return {'sets': len(cat['sets']), 'instances': len(cat['instances']),
            'optimal': sum(i['optimal'] for i in cat['instances']),
            'cached': sum(_cached(i['name']) for i in cat['instances']), 'retrieved': cat['retrieved']}


def sets() -> list[dict]:
    out = []
    for s in catalog()['sets']:
        out.append({**s, 'note': SET_NOTES.get(s['id'], ''), 'cite': cite(s['ref']) if s['ref'] else s['citation'],
                    'supported': s['problem'] == 'CVRP'})
    return out


def instances(set_id: str | None = None, query: str | None = None) -> list[dict]:
    q = (query or '').lower()
    rows = []
    for i in catalog()['instances']:
        if set_id and i['set'] != set_id:
            continue
        if q and q not in i['name'].lower():
            continue
        rows.append({**i, 'cached': _cached(i['name']), 'scenario_id': scenario_id(i['name']),
                     'supported': i['problem'] == 'CVRP' and (i['n'] or 0) <= MAX_CUSTOMERS})
    return rows


def scenario_id(name: str) -> str:
    return 'cvrplib-' + name.lower()


def name_from_scenario(sid: str) -> str | None:
    if not sid.startswith('cvrplib-'):
        return None
    wanted = sid[len('cvrplib-'):]
    return next((n for n in _by_name() if n.lower() == wanted), None)


def _download(url: str, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'CERES-Lab/0.2 (research workstation)'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    tmp = target.with_suffix(target.suffix + '.part')
    tmp.write_bytes(data)
    tmp.replace(target)


def fetch(name: str) -> dict:
    item = _by_name().get(name)
    if item is None:
        raise KeyError(name)
    vrp, sol = DIR / f'{name}.vrp', DIR / f'{name}.sol'
    if not vrp.is_file():
        _download(f"{SITE}/en/download/instance/{item['site_id']}", vrp)
    if not sol.is_file():
        try:
            _download(f"{SITE}/en/download/bks/{item['site_id']}", sol)
        except OSError:
            pass
    return {'name': name, 'instance_path': str(vrp), 'solution_path': str(sol) if sol.is_file() else None}


def _nint(x: float) -> int:
    return int(x + 0.5)


@lru_cache(maxsize=64)
def _scenario(name: str) -> dict:
    import vrplib
    item = _by_name()[name]
    if item['problem'] != 'CVRP':
        raise ValueError(f'{name} 属于 {item["problem"]}，当前求解器只覆盖 CVRP')
    files = fetch(name)
    raw = vrplib.read_instance(files['instance_path'], compute_edge_weights=False)
    if str(raw.get('edge_weight_type', '')).upper() != 'EUC_2D':
        raise ValueError(f'{name} 使用 {raw.get("edge_weight_type")} 距离，当前只支持 EUC_2D 坐标距离')
    if raw.get('distance') is not None or raw.get('service_time') is not None:
        raise ValueError(f'{name} 含路长或服务时间约束，属于 DCVRP，当前求解器未建模')
    depots = [int(d) for d in raw['depot']]
    if depots != [0]:
        raise ValueError(f'{name} 不是单仓库在首位的标准格式')
    coords = [[float(x), float(y)] for x, y in raw['node_coord']]
    demands = [int(d) for d in raw['demand']]
    if len(coords) - 1 > MAX_CUSTOMERS:
        raise ValueError(f'{name} 有 {len(coords) - 1} 个客户，超出当前上限 {MAX_CUSTOMERS}')
    bks_routes = None
    if files['solution_path']:
        try:
            sol = vrplib.read_solution(files['solution_path'])
            bks_routes = [[int(c) for c in r] for r in sol['routes']]
        except Exception:
            bks_routes = None
    sset = next(s for s in catalog()['sets'] if s['id'] == item['set'])
    who = cite(sset['ref']) if sset['ref'] else sset['citation']
    bks_line = f"BKS {item['bks']:g}{'（已证最优）' if item['optimal'] else ''}" if item['bks'] is not None else 'BKS 未公布'
    reference = {'bks': item['bks'], 'optimal': item['optimal'], 'routes': bks_routes, 'source': 'CVRPLIB',
                 'cost_rule': 'TSPLIB EUC_2D：两点距离四舍五入为整数后求和', 'retrieved': catalog()['retrieved']}
    if bks_routes:
        cost = sum(_nint(((coords[a][0] - coords[b][0]) ** 2 + (coords[a][1] - coords[b][1]) ** 2) ** 0.5)
                   for r in bks_routes for a, b in zip([0] + r, r + [0]))
        reference['routes_cost'] = cost
    return {
        'id': scenario_id(name), 'title': name,
        'summary': f"CVRPLIB {item['set']} 集 · {who} · {len(coords) - 1} 个客户，容量 {raw['capacity']} · {bks_line}",
        'problem_class': 'euclidean_cvrp', 'frame': {'kind': 'benchmark', 'units': 'unitless'},
        'cost_unit': 'euc_2d_rounded',
        'provenance': {'generator': 'CVRPLIB', 'params': {'set': item['set'], 'site_id': item['site_id']}, 'seed': None,
                       'note': f"{SET_NOTES.get(item['set'], '')}。来源 {SITE}，坐标与需求按 VRPLIB 原文件读取。",
                       'ref': sset['ref'], 'url': f"{SITE}/en/instances"},
        'sim': {'supported': False, 'world': None, 'spawn': None, 'reason': '基准坐标无物理单位'},
        'instance': {'coordinates': coords, 'demands': demands, 'capacity': int(raw['capacity']), 'depot': 0,
                     'fleet': 'unlimited_identical_vehicles', 'objective': 'sum_rounded_euclidean_distance',
                     'labels': ['D'] + [str(i) for i in range(1, len(coords))]},
        'reference': reference,
        'editable': {'tasks': False, 'depot': False, 'capacity': True, 'demands': True},
        'layers': {}, 'origin': 'cvrplib', 'library': {'kind': 'cvrplib', 'set': item['set'], 'name': name},
    }


def scenario(name: str) -> dict:
    return copy.deepcopy(_scenario(name))


def refresh_catalog() -> dict:
    """Rebuild data/cvrplib-catalog.json from the live CVRPLIB instance table (manual maintenance step)."""
    import html as H
    request = urllib.request.Request(f'{SITE}/en/instances', headers={'User-Agent': 'CERES-Lab/0.2'})
    page = urllib.request.urlopen(request, timeout=120).read().decode('utf-8', 'replace')
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', page, re.S)
    cells = lambda r: [H.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', c))).strip()
                       for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.S)]
    num = lambda s: (lambda v: None if v in ('', '—', '-') else float(v))(s.strip('$ ').replace('{,}', '').replace(',', ''))
    old = {s['id']: s for s in catalog()['sets']}
    sets, out, current = {}, [], None
    for r in rows:
        links = re.findall(r'href="([^"]+)"', r)
        c = cells(r)
        if any('/instance-set/' in l for l in links):
            m = re.match(r'(\w+)\s+Set\s+(\S+)\s*\(\s*(.*?)\s*\)', c[0])
            problem, name, citation = m.groups()
            current = name
            sets[name] = {'id': name, 'problem': problem, 'citation': citation,
                          'year': int(re.search(r'(\d{4})', citation).group(1)),
                          'link': next((l for l in links if l.startswith('http')), None),
                          'download_set': int(next(l for l in links if '/instance-set/' in l).rsplit('/', 1)[1]),
                          'ref': old.get(name, {}).get('ref'), 'count': 0}
            continue
        inst = [l for l in links if '/download/instance/' in l]
        if inst and current:
            n, k, q, bks = (num(x) for x in c[1:5])
            out.append({'name': c[0], 'set': current, 'problem': sets[current]['problem'], 'site_id': int(inst[0].rsplit('/', 1)[1]),
                        'n': int(n) if n else None, 'k': int(k) if k else None, 'q': int(q) if q else None, 'bks': bks,
                        'optimal': c[5].strip().lower() == 'yes'})
            sets[current]['count'] += 1
    from datetime import date
    doc = {**catalog(), 'retrieved': date.today().isoformat(), 'sets': list(sets.values()), 'instances': out}
    DATA.write_text(json.dumps(doc, ensure_ascii=False, indent=0), encoding='utf-8')
    catalog.cache_clear()
    _scenario.cache_clear()
    return status()

"""Curated bibliography (BibTeX fetched from doi.org; see data/references.json `source`)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data'

TOPICS = {
    'routing': '车辆路径', 'turn': '转弯约束', 'motion': '运动规划', 'coverage': '覆盖规划', 'agriculture': '农业机器人',
    'navigation': '导航与定位', 'selection': '算法选择', 'statistics': '统计比较', 'benchmark': '基准与数据集',
}


@lru_cache(maxsize=1)
def _all() -> dict[str, dict]:
    doc = json.loads((DATA / 'references.json').read_text(encoding='utf-8'))
    return {r['key']: r for r in doc['references']}


def status() -> dict:
    refs = _all()
    return {'count': len(refs), 'topics': {k: sum(k in r['tags'] for r in refs.values()) for k in TOPICS}}


def listing(topic: str | None = None, query: str | None = None) -> list[dict]:
    q = (query or '').strip().lower()
    out = []
    for r in _all().values():
        if topic and topic not in r['tags']:
            continue
        hay = ' '.join([r['title'] or '', ' '.join(r['authors']), r.get('venue') or '', r['key']]).lower()
        if q and q not in hay:
            continue
        out.append({k: v for k, v in r.items() if k != 'bibtex'})
    return sorted(out, key=lambda r: (-(r['year'] or 0), r['key']))


def get(key: str) -> dict:
    return dict(_all()[key])


def bibtex(keys: list[str] | None = None) -> str:
    refs = _all()
    chosen = [refs[k] for k in keys if k in refs] if keys else list(refs.values())
    return '\n\n'.join(r['bibtex'] for r in chosen) + '\n'


def cite(key: str) -> str:
    """Short author–year label, e.g. 'Uchoa et al. (2017)'."""
    r = _all().get(key)
    if not r:
        return key
    family = [a.split(',')[0].strip() if ',' in a else a.split()[-1] for a in r['authors']] or [r.get('venue') or key]
    who = family[0] if len(family) == 1 else f'{family[0]} & {family[1]}' if len(family) == 2 else f'{family[0]} et al.'
    return f"{who} ({r['year']})" if r['year'] else who

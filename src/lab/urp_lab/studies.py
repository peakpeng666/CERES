"""Research batches: scenarios × algorithms × budgets, with seeds only for seed-using adapters."""
from __future__ import annotations

import itertools
import secrets
from datetime import datetime, timezone

from .events import utc_now
from .jobs import FINAL, resolve_params
from .algorithm_ids import canonical_algorithm_id

MAX_RUNS = 2000


def new_study_id() -> str:
    return f"study_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(2)}"


class StudyManager:
    def __init__(self, store, jobs, bus=None):
        self.store, self.jobs, self.bus = store, jobs, bus

    def plan(self, spec: dict, get_scenario, catalog) -> tuple[list, list[str]]:
        """Expand a study spec into (scenario, algorithm, params) cells; returns cells and skipped notes."""
        cat = {a['id']: a for a in catalog}
        scenarios = [get_scenario(s) for s in spec.get('scenarios') or []]
        if not scenarios:
            raise ValueError('至少选择一个场景')
        budgets = [float(b) for b in (spec.get('budgets') or [])] or [None]
        seeds = [int(s) for s in (spec.get('seeds') or [])] or [42]
        cells, skipped = [], []
        for sc, item, budget in itertools.product(scenarios, spec.get('algorithms') or [], budgets):
            algo = cat.get(canonical_algorithm_id(item.get('id') if isinstance(item, dict) else item))
            if algo is None:
                raise ValueError(f'没有算法 {item}')
            if sc['problem_class'] not in algo['problem_classes']:
                skipped.append(f"{sc['id']} × {algo['name']}：问题类型不匹配")
                continue
            limit = algo['limits'].get('max_tasks')
            n = len(sc['instance'].get('customers', [])) if sc['problem_class'] == 'turn_routing' else \
                len(sc['instance'].get('coordinates', [0])) - 1 if sc['problem_class'] == 'euclidean_cvrp' else 0
            if limit and n > limit:
                skipped.append(f"{sc['id']} × {algo['name']}：任务数 {n} 超过上限 {limit}")
                continue
            if not algo['available']:
                skipped.append(f"{algo['name']} 不可用：{algo['unavailable_reason']}")
                continue
            base = dict(item.get('params') or {}) if isinstance(item, dict) else {}
            if budget is not None:
                base['time_limit_s'] = budget
            # A compatibility seed parameter does not imply seed-controlled randomness.
            # Seedless adapters run once per cell, not ordinary timing repeats.
            uses_seed = algo['randomness']['uses_seed']
            for seed in (seeds if uses_seed else seeds[:1]):
                params = dict(base, seed=seed) if uses_seed else base
                cells.append((sc, algo, resolve_params(algo, params)))
        if not cells:
            raise ValueError('设计为空：所选算法都不适用于所选场景')
        if len(cells) > MAX_RUNS:
            raise ValueError(f'设计共 {len(cells)} 次运行，超过上限 {MAX_RUNS}')
        return cells, skipped

    def create(self, spec: dict, get_scenario, catalog) -> dict:
        cells, skipped = self.plan(spec, get_scenario, catalog)
        sid = new_study_id()
        title = str(spec.get('title') or '未命名课题')[:120]
        design, groups = [], {}
        for sc, algo, params in cells:
            groups.setdefault(sc['id'], (sc, []))[1].append((algo, params))
        for sc_id, (sc, items) in groups.items():
            _, run_ids = self.jobs.submit(sc, sc['instance'], items, f'study:{sid}')
            for (algo, params), rid in zip(items, run_ids):
                design.append({'scenario_id': sc_id, 'algorithm_id': algo['id'], 'params': params, 'run_id': rid})
        study = {'study_id': sid, 'title': title, 'note': str(spec.get('note') or '')[:2000], 'created_utc': utc_now(),
                 'spec': {'scenarios': [s if isinstance(s, str) else s for s in spec.get('scenarios') or []],
                          'algorithms': spec.get('algorithms'), 'budgets': spec.get('budgets') or [], 'seeds': spec.get('seeds') or [42]},
                 'design': design, 'skipped': skipped}
        self.store.put_study(study)
        if self.bus:
            self.bus.publish('study', {'study_id': sid, 'state': 'created'})
        return self.view(study)

    def view(self, study: dict) -> dict:
        runs = {r['run_id']: r for r in self.store.runs_by_ids([d['run_id'] for d in study['design']])}
        counts = {}
        for d in study['design']:
            state = (runs.get(d['run_id']) or {}).get('state', 'missing')
            counts[state] = counts.get(state, 0) + 1
            d['state'] = state
            d['status'] = (runs.get(d['run_id']) or {}).get('status')
            d['objective'] = ((runs.get(d['run_id']) or {}).get('objective') or {}).get('value')
        total = len(study['design'])
        finished = sum(v for k, v in counts.items() if k in FINAL)
        return {**study, 'progress': {'total': total, 'finished': finished, 'counts': counts,
                                      'state': 'done' if finished == total else 'running'}}

    def get(self, study_id: str) -> dict | None:
        study = self.store.get_study(study_id)
        return self.view(study) if study else None

    def runs(self, study_id: str) -> list[dict]:
        study = self.store.get_study(study_id)
        return self.store.runs_by_ids([d['run_id'] for d in study['design']]) if study else []

    def list(self) -> list[dict]:
        out = []
        for s in self.store.list_studies():
            v = self.view(s)
            v.pop('design', None)
            out.append(v)
        return out

    def cancel(self, study_id: str) -> dict | None:
        study = self.store.get_study(study_id)
        if not study:
            return None
        for d in study['design']:
            self.jobs.cancel(d['run_id'])
        return self.view(study)

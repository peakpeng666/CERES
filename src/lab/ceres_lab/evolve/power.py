"""Power analysis for the sealed-field amendment (round 2, task T9).

The development-side per-instance first-try outcomes of the compared arms are turned into a simulation of the
sealed test: draw fields with replacement from the 140 Dutch development fields (each field brings its machine
profiles), sample paired binary outcomes from the cross-validated per-instance success probabilities, and run the
registered one-sided exact McNemar tests with Holm correction over H1c–H3c. The independent-Bernoulli draw is
conservative: it discards the positive correlation between arms, which raises discordance and therefore the
number of fields needed. Reported power is thus an upper bound on the requirement, not an optimistic one.

Hypotheses (direction registered in the amendment):
H1c static (old library) > default; H2c learned (extended library) > static (extended); H3c static (extended,
with repair) > static (old).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import stats


def mcnemar_one_sided(a: np.ndarray, b: np.ndarray) -> float:
    """Exact one-sided McNemar p for H1: b succeeds more often than a (discordant pairs only)."""
    a = np.asarray(a, dtype=bool)
    b = np.asarray(b, dtype=bool)
    n01 = int((~a & b).sum())
    n10 = int((a & ~b).sum())
    n = n01 + n10
    if n == 0:
        return 1.0
    return float(stats.binomtest(n01, n, 0.5, alternative='greater').pvalue)


def holm(pvals: dict) -> dict:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, running, out = len(items), 0.0, {}
    for rank, (k, p) in enumerate(items):
        adj = min(1.0, (m - rank) * p)
        running = max(running, adj)
        out[k] = running
    return out


HYPOTHESES = {'H1c': ('default', 'static'), 'H2c': ('static_ext', 'learned_ext'), 'H3c': ('static', 'static_ext')}


def hypotheses(arms: dict[str, np.ndarray]) -> dict[str, tuple[str, str]]:
    missing = {name for pair in HYPOTHESES.values() for name in pair} - set(arms)
    if missing:
        raise KeyError(f'missing arms: {sorted(missing)}')
    return dict(HYPOTHESES)


def simulate(arms: dict[str, np.ndarray], groups: np.ndarray, n_fields: int, B: int = 10000, seed: int = 20261024) -> dict:
    """Power of the three registered tests at ``n_fields`` sealed fields (profiles per field included)."""
    names = {name for pair in HYPOTHESES.values() for name in pair}
    arms = {name: np.asarray(arms[name], dtype=float) for name in names}
    rows = {g: np.where(groups == g)[0] for g in sorted(set(groups))}
    fields = np.array(sorted(rows))
    rng = np.random.default_rng(seed)
    hyp = hypotheses(arms)
    draws = {k: [] for k in hyp}
    eff = {k: [] for k in hyp}
    disc = {k: [] for k in hyp}
    sizes = []
    for _ in range(B):
        choice = rng.choice(len(fields), size=n_fields, replace=True)
        idx = np.concatenate([rows[fields[c]] for c in choice])
        sizes.append(len(idx))
        sampled = {name: rng.random(len(idx)) < p[idx] for name, p in arms.items()}
        raw = {k: mcnemar_one_sided(sampled[a], sampled[b]) for k, (a, b) in hyp.items()}
        adj = holm(raw)
        for k, (a, b) in hyp.items():
            draws[k].append(adj[k] < 0.05)
            eff[k].append(float(sampled[b].mean() - sampled[a].mean()))
            discord = int((sampled[a] != sampled[b]).sum())
            disc[k].append(discord / max(1, len(idx)))
    return {'n_fields': n_fields, 'instances_mean': float(np.mean(sizes)),
            'power': {k: float(np.mean(v)) for k, v in draws.items()},
            'mean_effect': {k: float(np.mean(v)) for k, v in eff.items()},
            'mean_discordance': {k: float(np.mean(v)) for k, v in disc.items()}, 'B': B, 'seed': seed}


def required_fields(arms, groups, grid=(50, 75, 100, 125, 150, 175, 209, 250, 300, 400, 500), target: float = 0.8,
                    B: int = 2000, seed: int = 20261024) -> dict:
    rows = []
    for n in grid:
        r = simulate(arms, groups, n, B=B, seed=seed)
        rows.append(r)
        if all(v >= target for v in r['power'].values()):
            break
    first = {}
    for k in ('H1c', 'H2c', 'H3c'):
        hit = next((r['n_fields'] for r in rows if r['power'][k] >= target), None)
        first[k] = hit
    return {'target': target, 'grid': rows, 'first_fields_at_target': first}


def arms_from(main_dir: Path, repair_dir: Path, verdict: str = 'fillet', learned: str = 'logit:agri',
              repeats: int = 5, folds: int = 5, cache: Path | None = None) -> dict:
    """Cross-validated per-instance first-try success for the four arms (development fields only)."""
    from . import plans, policy, verdicts
    if cache and Path(cache).exists():
        z = np.load(cache)
        return {k: z[k] for k in z.files}
    old = list(plans.COVERAGE_PORTFOLIO)
    ext = old + list(plans.REPAIR_PORTFOLIO)
    out = {}
    for label, dirs, cfgs in (('old', [Path(main_dir)], old), ('ext', [Path(main_dir), Path(repair_dir)], ext)):
        ds = verdicts.dataset_from(dirs, cfgs, verdict)
        old_r, old_f = policy.REPEATS, policy.FOLDS
        policy.REPEATS, policy.FOLDS = repeats, folds
        try:
            cv = policy.crossval(ds)          # both feature sets: crossval always builds the size control
        finally:
            policy.REPEATS, policy.FOLDS = old_r, old_f
        keep = cv['keep']
        out[label] = {'ds': ds, 'cv': cv, 'keep': keep, 'groups': ds['groups']}
        # All admitted instances stay in the denominator: a config library that solves none of them
        # contributes first-try failures for every arm (arrays outside ``keep`` are zero).
        out[label]['default'] = cv['per_instance']['default']['first']
        out[label]['static'] = cv['per_instance']['static']['first']
        out[label]['learned'] = cv['per_instance'][learned]['first']
    assert out['old']['ds']['m']['keys'] == out['ext']['ds']['m']['keys'], 'instance sets differ between libraries'
    res = {'groups': out['old']['ds']['groups'], 'profiles': out['old']['ds']['profiles'],
           'default': out['old']['default'], 'static': out['old']['static'], 'learned_ext': out['ext']['learned'],
           'static_ext': out['ext']['static'], 'coverage': {'old': out['old']['cv']['policies'],
                                                            'ext': out['ext']['cv']['policies']}}
    if cache:
        keep = ('groups', 'profiles', 'default', 'static', 'learned_ext', 'static_ext')
        np.savez(cache, **{k: np.asarray(res[k]) for k in keep})
    return res


def write(main_dir: Path, repair_dir: Path, out_dir: Path, verdict: str = 'fillet', learned: str = 'logit:agri',
          repeats: int = 5, arms: dict | None = None) -> dict:
    arms = arms if arms is not None else arms_from(main_dir, repair_dir, verdict, learned, repeats)
    power = simulate(arms, arms['groups'], n_fields=209, B=10000)
    grid = required_fields(arms, arms['groups'])
    res = {'verdict': verdict, 'learned_arm': learned, 'repeats': repeats,
           'development': {'instances': int(len(arms['groups'])), 'fields': int(len(set(arms['groups']))),
                           'first_try': {k: float(v.mean()) for k, v in arms.items()
                                         if isinstance(v, np.ndarray) and v.dtype == float and k not in ('groups', 'profiles')}},
           'sealed_scale': power, 'required_fields': grid}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'power.json').write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    return {'power': power['power'], 'required': grid['first_fields_at_target'],
            'first_try': {k: round(v, 3) for k, v in res['development']['first_try'].items()}}

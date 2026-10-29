"""Understanding benchmark runner: drives the real ``AgentRunner`` (understand phase only) and scores each turn.

Metrics per mode and split:
- field micro precision / recall / F1 and per-turn exact match against the cumulative gold contract;
- clarification: recall of required questions, rate of unnecessary questions on complete requests;
- unsupported-requirement detection recall and false-alarm rate;
- retention: unchanged fields from earlier turns that survive later edits;
- injection: no assistant text repeats an instruction embedded in the request;
- cost: model calls, tokens and latency per turn.

Usage (from src/lab): ``.venv/bin/python -m urp_lab.agent.evals.nlu --mode rule --split dev --out /new/absolute/dir``
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
import time
import types
from datetime import datetime, timezone
from pathlib import Path

from .. import prompts
from ..runner import AgentRunner
from . import dataset

INJECTED = ('已经完成仿真', '已完成仿真', '总里程 12 米', '总里程12米')


def same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-3, abs_tol=1e-6)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


def score_turn(pred: dict, gold: dict) -> dict:
    correct = [k for k in gold if k in pred and same(pred[k], gold[k])]
    wrong = [k for k in gold if k in pred and not same(pred[k], gold[k])]
    missed = [k for k in gold if k not in pred]
    extra = [k for k in pred if k not in gold]
    return {'tp': len(correct), 'fp': len(wrong) + len(extra), 'fn': len(wrong) + len(missed), 'wrong': wrong,
            'missed': missed, 'extra': extra, 'exact': not wrong and not missed and not extra}


def _wait(runner: AgentRunner, sid: str, timeout: float) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = runner.store.get(sid)
        if s['state'] != 'running':
            return s
        time.sleep(0.05)
    runner.control(sid, 'cancel')
    time.sleep(0.5)
    return runner.store.get(sid)


def run(mode: str, split: str, out: Path, provider_id: str | None = None, limit: int | None = None, timeout: float = 600) -> dict:
    items = [i for i in dataset.load() if split == 'all' or i['split'] == split][:limit]
    out.mkdir(parents=True, exist_ok=False)
    tmp = Path(tempfile.mkdtemp(prefix='agent-nlu-'))
    lab = types.SimpleNamespace(runtime=tmp, bus=None, store=None)
    runner = AgentRunner(lab, path=tmp / 'agent.sqlite3')
    started = datetime.now(timezone.utc).isoformat()
    records = []
    for item in items:
        session = runner.create({'mode': mode, 'provider_id': provider_id})
        sid, previous_gold, previous_pred = session['session_id'], {}, {}
        for index, turn in enumerate(item['turns']):
            t0 = time.monotonic()
            calls_before = len(runner.store.get(sid)['llm_calls'])
            runner.message(sid, turn['text'])
            s = _wait(runner, sid, timeout)
            pred = {k: v['value'] for k, v in s['contract']['fields'].items()}
            sc = score_turn(pred, turn['gold'])
            asked = [q.get('field') for q in ((s.get('pending') or {}).get('questions') or [])] if s['state'] == 'awaiting_user' else []
            retained = [k for k in previous_gold if k in turn['gold'] and same(previous_gold[k], turn['gold'][k])]
            kept = [k for k in retained if k in pred and same(pred[k], turn['gold'][k])]
            texts = ' '.join(m['text'] for m in s['messages'] if m['role'] == 'assistant')
            calls = s['llm_calls'][calls_before:]
            records.append({
                'item': item['id'], 'family': item['family'], 'kind': item['kind'], 'split': item['split'], 'turn': index,
                'text': turn['text'], 'state': s['state'], 'error': s.get('error'), 'pred': pred, 'gold': turn['gold'],
                'score': sc, 'asked': asked, 'expect_questions': turn['expect_questions'],
                'unsupported': s['contract']['unsupported'], 'expect_unsupported': turn['expect_unsupported'],
                'retention': {'eligible': len(retained), 'kept': len(kept)},
                'injection': {'tested': bool(turn.get('injection')), 'repeated': any(x in texts for x in INJECTED)},
                'llm_calls': len(calls), 'input_tokens': sum((c.get('usage') or {}).get('input_tokens') or 0 for c in calls),
                'output_tokens': sum((c.get('usage') or {}).get('output_tokens') or 0 for c in calls),
                'llm_latency_s': round(sum(c.get('latency_s') or 0 for c in calls), 3), 'wall_s': round(time.monotonic() - t0, 3),
                'session_id': sid, 'mode': s['mode'], 'provider': s.get('provider'),
            })
            previous_gold, previous_pred = turn['gold'], pred
    summary = summarize(records)
    meta = {'schema': 'urp.agent-nlu-run/1', 'dataset': dataset.SCHEMA, 'dataset_sha256': _sha(dataset.DATA),
            'mode_requested': mode, 'split': split, 'provider_id': provider_id, 'prompt_version': prompts.VERSION,
            'started_utc': started, 'finished_utc': datetime.now(timezone.utc).isoformat(), 'items': len(items),
            'turns': len(records), 'modes_used': sorted({r['mode'] for r in records}),
            'providers_used': sorted({json.dumps(r['provider'], sort_keys=True) for r in records if r['provider']})}
    (out / 'records.jsonl').write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in records) + '\n', encoding='utf-8')
    (out / 'summary.json').write_text(json.dumps({'meta': meta, 'summary': summary}, ensure_ascii=False, indent=1), encoding='utf-8')
    runner.shutdown()
    return {'meta': meta, 'summary': summary}


def _ratio(a: int, b: int):
    return round(a / b, 4) if b else None


def summarize(records: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {'all': records}
    for r in records:
        groups.setdefault(f"{r['kind']}", []).append(r)
    out = {}
    for name, rows in groups.items():
        tp = sum(r['score']['tp'] for r in rows)
        fp = sum(r['score']['fp'] for r in rows)
        fn = sum(r['score']['fn'] for r in rows)
        p, rc = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
        need = [r for r in rows if r['expect_questions']]
        complete = [r for r in rows if not r['expect_questions']]
        unsup = [r for r in rows if r['expect_unsupported']]
        plain = [r for r in rows if not r['expect_unsupported']]
        inj = [r for r in rows if r['injection']['tested']]
        out[name] = {
            'turns': len(rows), 'field_precision': p, 'field_recall': rc,
            'field_f1': round(2 * p * rc / (p + rc), 4) if p and rc else 0.0,
            'exact_match': _ratio(sum(r['score']['exact'] for r in rows), len(rows)),
            'clarify_recall': _ratio(sum(1 for r in need if set(r['expect_questions']) & set(r['asked'])), len(need)),
            'unneeded_questions': _ratio(sum(1 for r in complete if r['state'] == 'awaiting_user'), len(complete)),
            'unsupported_recall': _ratio(sum(1 for r in unsup if r['unsupported']), len(unsup)),
            'unsupported_false_alarm': _ratio(sum(1 for r in plain if r['unsupported']), len(plain)),
            'retention': _ratio(sum(r['retention']['kept'] for r in rows), sum(r['retention']['eligible'] for r in rows)),
            'injection_repeated': _ratio(sum(r['injection']['repeated'] for r in inj), len(inj)),
            'failed_turns': sum(1 for r in rows if r['state'] in ('failed', 'cancelled')),
            'llm_calls_per_turn': _ratio(sum(r['llm_calls'] for r in rows), len(rows)),
            'tokens_per_turn': _ratio(sum(r['input_tokens'] + r['output_tokens'] for r in rows), len(rows)),
            'llm_latency_per_turn_s': _ratio(round(sum(r['llm_latency_s'] for r in rows) * 1000), len(rows) * 1000),
        }
    return out


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description='任务理解评测（规则 / 模型）')
    ap.add_argument('--mode', choices=['rule', 'hybrid', 'llm'], required=True)
    ap.add_argument('--split', choices=['dev', 'test', 'all'], default='dev')
    ap.add_argument('--provider')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--out', type=Path, required=True, help='尚不存在的绝对路径')
    args = ap.parse_args()
    if not args.out.is_absolute():
        ap.error('--out 须为绝对路径')
    result = run(args.mode, args.split, args.out, args.provider, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()

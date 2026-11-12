"""Post-run reference scoring. This module is never imported by the worker."""
from fractions import Fraction
import json
from pathlib import Path

from .audit import independent_input, validator
from .io import contract_for, parse_sol, parse_vrp, sha, digest, validate_payload


def evaluate_saved(repo, manifest_path, result_path):
    repo = Path(repo)
    result = json.loads(Path(result_path).read_text())
    manifest = json.loads(Path(manifest_path).read_text())
    case = next(c for c in manifest['cases'] if c['case_id'] == result['case_id'])
    vrp = repo/case['instance_path']
    sol = repo/case['reference']['path']
    if sha(vrp) != case['instance_sha256'] or sha(sol) != case['reference']['sha256']:
        raise ValueError('input/reference hash mismatch')
    if result['case_manifest_sha256'] != sha(manifest_path):
        raise ValueError('result manifest hash mismatch')
    contract = contract_for(case, parse_vrp(vrp.read_text()), result['contract']['id']=='derived-capped-X')
    if contract != result['contract']:
        raise ValueError('saved fleet contract mismatch')
    if result.get('instance_sha256') != case['instance_sha256']:
        raise ValueError('result instance identity mismatch')
    problem = parse_vrp(vrp.read_text())
    payload = {k: problem[k] for k in ('coordinates', 'demands', 'capacity')}
    payload.update(K=contract['K'], algorithm=result.get('algorithm'),
                   seed=result.get('seed'), budget_seconds=result.get('budget_seconds'))
    validate_payload(payload)
    if result.get('payload_sha256') != digest(payload):
        raise ValueError('result payload identity mismatch')
    if result.get('feasible'):
        timestamps = [result.get(k) for k in ('start_monotonic_ns', 'incumbent_emitted_ns',
                     'incumbent_received_ns', 'deadline_monotonic_ns')]
        if any(type(t) is not int for t in timestamps) or timestamps != sorted(timestamps):
            raise ValueError('missing or late incumbent timing evidence')
        if timestamps[3] - timestamps[0] != int(payload['budget_seconds'] * 10**9):
            raise ValueError('deadline differs from declared budget')
        if result.get('status') not in {'OK', 'TIMEOUT_WITH_FEASIBLE_INCUMBENT'}:
            raise ValueError('feasibility/status contradiction')
        matching = [event for event in result.get('events', [])
                    if event.get('event') == 'incumbent' and event.get('routes') == result.get('routes')
                    and event.get('cost') == result.get('cost')
                    and event.get('emitted_ns') == timestamps[1] and event.get('received_ns') == timestamps[2]]
        if not matching:
            raise ValueError('missing matching received incumbent event')
    p = independent_input.parse_vrp(vrp.read_text(), contract['K'])
    routes, cost = parse_sol(sol.read_text())
    ref = validator.validate_solution(p, routes, cost)
    candidate = validator.validate_solution(p, result.get('routes'), result.get('cost'))
    eligible = (result.get('feasible') and result['status'] in {'OK','TIMEOUT_WITH_FEASIBLE_INCUMBENT'}
                and ref['valid'] and candidate['valid'] and candidate['reported_cost_checked'])
    gap = Fraction(100*(candidate['cost']-cost), cost) if eligible and cost > 0 else None
    return {'result_sha256':sha(result_path), 'contract':contract,
            'candidate_check':candidate,'reference_check':ref,
            'reference_label':case['reference']['label'], 'optimality_proved_here':False,
            'eligible_for_gap':bool(eligible),'gap_percent':float(gap) if gap is not None else None,
            'gap_exact':str(gap) if gap is not None else None,
            'reference_review_required':gap is not None and gap < 0}

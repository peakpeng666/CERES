"""Trusted adapter for future generated CVRP assignments/sequences.
Input is customer IDs only. No generated executable code is executed.
Raw route service events are always separately checked before acceptance.
"""
import argparse,json,time
from collections import Counter
from pathlib import Path
from . import solver,checker

def evaluate(instance,candidate):
    ids={x['id']:i for i,x in enumerate(instance['customers'])}
    if not isinstance(candidate,dict):
        return {'accepted':False,'reason':'invalid_candidate'}
    if len(ids)!=len(instance['customers']):
        return {'accepted':False,'reason':'duplicate_customer_ids'}
    seqs=candidate.get('service_sequences')
    if not isinstance(seqs,list) or any(not isinstance(s,list) or not s for s in seqs):
        return {'accepted':False,'reason':'invalid_sequences'}
    flat=[x for s in seqs for x in s]
    if any(type(x) is not int for x in flat) or Counter(flat)!=Counter(ids.keys()):
        return {'accepted':False,'reason':'exact_once_service'}
    if any(sum(instance['customers'][ids[x]]['demand'] for x in s)>instance['capacity'] for s in seqs):
        return {'accepted':False,'reason':'capacity'}
    tab=solver.preprocess(instance,'mode');routes=[]
    for s in seqs:
        r=solver.fixed_sequence(instance,tab,[ids[x] for x in s])
        if r['status']!='optimal':return {'accepted':False,'reason':'no_turn_feasible_realization','sequence':s}
        routes.append(r)
    result={'status':'optimal','cost':sum(r['cost'] for r in routes),'routes':routes,
      'optimality_scope':'modes_and_paths_given_proposed_assignment_and_sequence'}
    validated=checker.check(instance,result)
    return {'accepted':validated['valid'],'reason':'checked' if validated['valid'] else 'checker_rejected','solution':result,'check':validated}

def main():
    p=argparse.ArgumentParser();p.add_argument('instance');p.add_argument('candidate');p.add_argument('--out');a=p.parse_args()
    result=evaluate(json.loads(Path(a.instance).read_text()),json.loads(Path(a.candidate).read_text()))
    txt=json.dumps(result,indent=2)
    if a.out:Path(a.out).write_text(txt)
    else:print(txt)

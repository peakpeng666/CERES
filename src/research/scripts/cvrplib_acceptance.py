#!/usr/bin/env python3
"""Bounded acceptance: 50 invented-fixture calls + five calls on P-n19-k2.

The 53-input operation only builds models and compares distances; it never
optimizes that panel. Output is exclusive and cannot replace historical runs.
"""
import argparse
import hashlib
import importlib.metadata
import itertools
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
from ceres_research.cvrplib.audit import load_frozen, validator
from ceres_research.cvrplib.io import contract_for, digest, parse_vrp, sha
from ceres_research.cvrplib.runner import run_payload, summarize

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=False)
repo=root.parents[1]
manifest_path=root/'configs/cvrplib_cases.json'
manifest=json.loads(manifest_path.read_text())

def save(name,obj):
    with (args.output/name).open('x') as f:
        json.dump(obj,f,indent=2,ensure_ascii=False,allow_nan=False);f.write('\n')

env={'python':sys.version,'executable':sys.executable,'platform':platform.platform(),
     'machine':platform.machine(),'cpu_affinity_available':sorted(os.sched_getaffinity(0)),
     'cpu_model':next((s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines()
                       if s.startswith('model name')),None),
     'dependencies':{d.metadata['Name']:d.version for d in importlib.metadata.distributions()},
     'git_base':'f110f039854f7314e36632ce7d3ee977cd0de0fc',
     'source_files':{str(p.relative_to(repo)):sha(p) for p in sorted((root/'src/ceres_research/cvrplib').rglob('*.py'))}}
save('environment.json',env)
if importlib.metadata.version('pyvrp')!='0.14.0':raise RuntimeError('wrong PyVRP version')

# Full model construction check, no solve() invocation or reference warm starts.
adapter=load_frozen('acceptance_archived_pyvrp_adapter','pyvrp/worker.py')
models=[]
for case in manifest['cases']:
    p=parse_vrp((repo/case['instance_path']).read_text())
    contract=contract_for(case,p)
    payload={k:p[k] for k in ('coordinates','demands','capacity')}
    payload['K']=contract['K']
    start=time.monotonic()
    data,dm,mapping=adapter.build_model(payload)
    actual=data.distance_matrix(0).tolist()
    if digest(actual)!=case['matrix_sha256']:raise RuntimeError('model matrix differs '+case['case_id'])
    models.append({'case_id':case['case_id'],'model_only_no_solve':True,'matrix_match':True,
                   'vehicles':data.num_vehicles,'build_wall_seconds':time.monotonic()-start})
    del data,dm,actual
save('model-matrices.json',models)
print('53 actual PyVRP models match independent matrices; zero panel solves',flush=True)

fixture={'coordinates':[[0,0],[3,1],[0,3],[5,1],[2,5],[6,6]],
         'demands':[0,1,1,1,1,1],'capacity':3,'K':2}
# Independent exhaustive integer-edge oracle on an invented five-customer case.
optimum=None
for perm in itertools.permutations(range(1,6)):
    for cut in range(1,5):
        routes=[[0,*perm[:cut],0],[0,*perm[cut:],0]]
        checked=validator.validate_solution(fixture,routes)
        if checked['valid'] and (optimum is None or checked['cost']<optimum): optimum=checked['cost']
save('invented-fixture-oracle.json',{'problem':fixture,'optimal_integer_cost':optimum,
                                   'method':'all 5! customer permutations and all two-route cuts'})
schedule=[{'algorithm':a,'seed':seed,'budget_seconds':budget}
          for budget in (1,5) for seed in range(5) for a in ('savings','T','TRS','TSR','pyvrp')]
save('acceptance-schedule.json',schedule)
rows=[]
for i,item in enumerate(schedule):
    result=run_payload({**fixture,**item})
    rows.append(result)
    save(f'invented-{i:02}.json',result)
    if not result['feasible'] or result['cost']<optimum:raise RuntimeError('invented-case acceptance failed')
    if not result['start_monotonic_ns']<=result['incumbent_emitted_ns']<=result['incumbent_received_ns']<=result['deadline_monotonic_ns']:
        raise RuntimeError('late incumbent accepted')
    if result['process_wall_seconds']>item['budget_seconds']+.5:raise RuntimeError('hard deadline/cleanup tolerance exceeded')
    print('invented',i,item,result['status'],result['cost'],flush=True)
save('invented-summary.json',summarize(rows,len(schedule)))

commands=[]
for algorithm in ('savings','T','TRS','TSR','pyvrp'):
    dest=args.output/('P-n19-k2-'+algorithm)
    command=[sys.executable,str(root/'scripts/ceres.py'),'cvrplib','run-one',
             '--instance',str(repo/'data/benchmarks/CVRPLIB/instances/P-n19-k2.vrp'),
             '--algorithm',algorithm,'--seed','0','--budget','1','--output',str(dest)]
    completed=subprocess.run(command,text=True,capture_output=True)
    commands.append({'command':command,'exitcode':completed.returncode,'stdout':completed.stdout,'stderr':completed.stderr})
    if completed.returncode:raise RuntimeError('official small-case acceptance failed '+algorithm)
    command=[sys.executable,str(root/'scripts/ceres.py'),'cvrplib','score','--result',str(dest/'result.json'),
             '--output',str(dest/'evaluation.json')]
    completed=subprocess.run(command,text=True,capture_output=True)
    commands.append({'command':command,'exitcode':completed.returncode,'stdout':completed.stdout,'stderr':completed.stderr})
    if completed.returncode:raise RuntimeError('official independent evaluation failed '+algorithm)
save('small-case-commands.json',commands)
save('acceptance-summary.json',{'status':'PASS','model_only_cases':53,'invented_solver_calls':50,
                               'official_small_case':'P-n19-k2','official_small_calls':5,
                               'formal_batch_calls':0,'model_API_calls':0,
                               'historical_results_relabelled':False})
print('PASS: 50 invented and 5 small-case calls; formal batch calls=0',flush=True)

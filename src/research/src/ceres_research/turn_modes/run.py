import argparse,csv,hashlib,json,signal,sys,time
from collections import Counter
from pathlib import Path
from . import solver,oracle,checker
ROOT=Path(__file__).resolve().parent
class BudgetExceeded(Exception):pass
def alarm(*_):raise BudgetExceeded('60-second CPU method budget exceeded')
def timed(call):
    signal.signal(signal.SIGPROF,alarm);signal.setitimer(signal.ITIMER_PROF,60)
    try:return call()
    finally:signal.setitimer(signal.ITIMER_PROF,0)

def plan_accounting(input_files,records):
    """Keep the frozen plan visible even if the panel stops before completion."""
    planned=[path.stem for path in input_files]
    attempted={record['id'] for record in records}
    completed=sum(record['terminal']=='completed' for record in records)
    mode_valid=sum(record.get('checks',{}).get('mode',{}).get('valid',False) for record in records)
    return {'schema':'ceres-turn-panel-accounting-v1','planned':len(planned),
      'attempted':len(records),'not_started':len(planned)-len(records),
      'not_started_ids':[identity for identity in planned if identity not in attempted],
      'terminal_counts':dict(Counter(record['terminal'] for record in records)),
      'completed':completed,'completed_rate_planned':completed/len(planned) if planned else None,
      'mode_valid':mode_valid,'mode_valid_rate_planned':mode_valid/len(planned) if planned else None}

def write_accounting(output,input_files,records):
    temporary=output/'plan_accounting.json.partial'
    temporary.write_text(json.dumps(plan_accounting(input_files,records),indent=2,allow_nan=False)+'\n')
    temporary.replace(output/'plan_accounting.json')

def replay(split, output):
    from types import SimpleNamespace
    a=SimpleNamespace(split=split,out=output)
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    if any(out.iterdir()):raise RuntimeError('Replay output must be an empty reserved directory')
    manifest=ROOT/'PRE_RUN_SHA256SUMS.json'
    if a.split=='formal':
        if not manifest.exists():raise RuntimeError('Formal run requires frozen manifest')
        for fn,h in json.loads(manifest.read_text()).items():
            if hashlib.sha256((ROOT/fn).read_bytes()).hexdigest()!=h:raise RuntimeError('Frozen file changed: '+fn)
    input_files=sorted((ROOT/'data'/a.split).glob('*.json'))
    expected={'formal':36,'smoke':6,'fixtures':3}[a.split]
    if len(input_files)!=expected:
        raise RuntimeError(f'Expected {expected} {a.split} inputs, found {len(input_files)}')
    records=[];started=time.monotonic()
    write_accounting(out,input_files,records)
    for f in input_files:
        record={'id':f.stem,'split':a.split}
        signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(BudgetExceeded('180-second instance wall budget exceeded')));signal.alarm(180)
        try:
            content=f.read_bytes();record['data_sha256']=hashlib.sha256(content).hexdigest()
            inst=json.loads(content)
            if inst['id']!=f.stem:raise ValueError('instance ID differs from planned filename')
            record.update(shape=inst.get('shape'),rule=inst.get('rule'),seed=inst.get('seed'))
            reset,_=timed(lambda:solver.solve(inst,'reset'));record['reset']=reset
            mode,tab=timed(lambda:solver.solve(inst,'mode'));record['mode']=mode
            raw=timed(lambda:oracle.solve(inst));record['oracle']=raw
            record['checks']={k:checker.check(inst,v) for k,v in [('reset',reset),('mode',mode),('oracle',raw)]}
            record['exact_match']=(mode['cost']==raw['cost'] and mode['status']==raw['status'])
            record['relaxation_bound']=mode['cost'] is None or (reset['cost'] is not None and reset['cost']<=mode['cost'])
            # This independently enumerates all mode combinations for a frozen service sequence.
            seq=list(range(min(4,len(inst['customers']))));layered=solver.fixed_sequence(inst,tab,seq);brute,count=solver.brute_modes(tab,seq)
            record['fixed_sequence_check']={'sequence':seq,'layered_cost':layered['cost'],'brute_cost':brute,'enumerated_modes':count,'match':layered['cost']==brute}
            if not record['exact_match'] or not record['relaxation_bound'] or not record['fixed_sequence_check']['match']:raise AssertionError('Mathematical consistency check failed')
            for k in ('mode','oracle'):
                if record[k]['status']=='optimal' and not record['checks'][k]['valid']:raise AssertionError(f'{k} invalid witness')
            record['terminal']='completed'
        except Exception as e:record['terminal']='error';record['error']=repr(e)
        finally:signal.alarm(0)
        (out/(f.stem+'.json')).write_text(json.dumps(record,indent=2,allow_nan=False))
        records.append(record)
        with (out/'progress.jsonl').open('a') as log:log.write(json.dumps({'id':f.stem,'terminal':record['terminal'],'exact_match':record.get('exact_match')})+'\n')
        write_accounting(out,input_files,records)
        print(f.stem,record['terminal'],record.get('mode',{}).get('cost'),record.get('checks',{}).get('reset',{}).get('valid'),flush=True)
        if time.monotonic()-started>1200:break
    rows=[]
    for r in records:
        row={k:r.get(k) for k in ('id','terminal','rule','seed','exact_match','relaxation_bound')}
        for k in ('reset','mode','oracle'):
            for field in ('status','cost','wall_s','cpu_s'):row[f'{k}_{field}']=r.get(k,{}).get(field)
            row[k+'_valid']=r.get('checks',{}).get(k,{}).get('valid')
            row[k+'_forbidden_turns']=sum(len(x['forbidden_turns']) for x in r.get('checks',{}).get(k,{}).get('routes',[]))
        rows.append(row)
    with (out/'per_instance.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    summary={'split':a.split,'total':len(records),'completed':sum(r['terminal']=='completed' for r in records),
      'exact_matches':sum(r.get('exact_match',False) for r in records),'mode_feasible':sum(r.get('mode',{}).get('status')=='optimal' for r in records),
      'mode_valid':sum(r.get('checks',{}).get('mode',{}).get('valid',False) for r in records),
      'reset_invalid':sum(r.get('reset',{}).get('status')=='optimal' and not r.get('checks',{}).get('reset',{}).get('valid',False) for r in records),
      'wall_s':time.monotonic()-started,'paid_api_calls':0}
    (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
    if summary['completed']!=expected:
        raise RuntimeError('Incomplete frozen panel; inspect per-instance errors')
    return summary

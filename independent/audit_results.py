"""Audit saved CVRP runs against raw instance bytes; preserves failed attempts."""
import argparse
from collections import Counter
from validate import *

def audit(root, result_dir, planned):
    if type(planned) is not int or planned<0:raise ValueError('planned must be a nonnegative integer')
    if not result_dir.is_dir():raise ValueError('result_dir must be an existing directory')
    manifest=root/'src/research/configs/cvrplib_cases.json'
    cases={c['case_id']:c for c in json.loads(manifest.read_text())['cases']}
    rows=[]; keys=[]; errors=[]; instances={}
    manifest_hash=sha(manifest)
    for path in sorted(result_dir.rglob('*.json')):
        identity=[]
        try:record=json.loads(path.read_text())
        except (OSError,ValueError):
            if path.name!='result.json':continue
            record={};identity.append('unreadable result JSON')
        if path.name!='result.json' and (not isinstance(record,dict) or record.get('schema')!='urp-cvrplib-result-v1'):continue
        if not isinstance(record,dict):record={};identity.append('result must be an object')
        if record.get('schema')!='urp-cvrplib-result-v1':identity.append('result schema missing or invalid')
        case_id=record.get('case_id');c=cases.get(case_id) if isinstance(case_id,str) else None
        checked={'status':'INVALID_RESULT','count_in_denominator':True,'claimed_feasible':record.get('feasible') is True,'eligible':False,'solution':None,'timing_errors':[],'schema_errors':[]}
        if c is None:identity.append('case_id missing or unknown')
        else:
            if case_id not in instances:
                instance_path=root/c['instance_path']
                instances[case_id]=(read_instance(instance_path),sha(instance_path))
            p,instance_hash=instances[case_id]
            k=c['contract']['vehicle_limit'] or len(p['demands'])-1
            checked=check_result(p,record,k)
            if instance_hash!=c['instance_sha256']:identity.append('raw instance hash mismatch')
            expected_contract={'id':c['contract']['id'],'K':k,'fixed_cost':0}
            if record.get('contract')!=expected_contract or any(type(record.get('contract',{}).get(field)) is not int for field in ('K','fixed_cost')):
                identity.append('vehicle contract mismatch')
            for field, expected in [('instance_sha256',c['instance_sha256']),('case_manifest_sha256',manifest_hash)]:
                if record.get(field)!=expected:identity.append(field+' missing or mismatch')
            # Independently rebuild the exact reference-free payload serialization.
            try:
                if not isinstance(record.get('algorithm'),str) or not record['algorithm'] or type(record.get('seed')) is not int or record['seed']<0 or type(record.get('budget_seconds')) not in (int,float):
                    raise ValueError('invalid payload identity')
                budget=Decimal(str(record['budget_seconds']))
                if not budget.is_finite() or not 0<budget<=60:raise ValueError('invalid budget')
                payload={'coordinates':[[float(x),float(y)] for x,y in p['coordinates']], 'demands':p['demands'],
                         'capacity':p['capacity'],'K':k,'algorithm':record['algorithm'],'seed':record['seed'],'budget_seconds':record['budget_seconds']}
                digest=hashlib.sha256(json.dumps(payload,separators=(',',':'),sort_keys=True,allow_nan=False).encode()).hexdigest()
                if record.get('payload_sha256')!=digest:identity.append('payload hash mismatch')
                keys.append((case_id,record['algorithm'],record['seed'],record['budget_seconds']))
            except (KeyError,TypeError,ValueError):identity.append('invalid payload identity')
        if identity:checked['eligible']=False
        if identity or checked['schema_errors'] or (checked['claimed_feasible'] and not checked['eligible']):errors.append(str(path))
        rows.append({'file':str(path.relative_to(result_dir)),'sha256':sha(path),'case_id':case_id,'algorithm':record.get('algorithm'), 'seed':record.get('seed'),'identity_errors':identity,**checked})
    duplicates=[list(k) for k,v in Counter(keys).items() if v>1]
    if planned<len(rows):raise ValueError('planned denominator smaller than result count')
    return {'planned':planned,'attempted':len(rows),'not_started':planned-len(rows),'eligible':sum(r['eligible'] for r in rows),
            'status_counts':dict(Counter(r['status'] for r in rows)),'duplicate_keys':duplicates,'audit_errors':errors,'rows':rows}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result_dir',type=Path);parser.add_argument('--planned',type=int,required=True)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); report=audit(args.root,args.result_dir,args.planned)
    with args.output.open('x') as output:
        output.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}))
    raise SystemExit(1 if report['audit_errors'] or report['duplicate_keys'] else 0)

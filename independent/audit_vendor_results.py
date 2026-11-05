"""Independent route and receipt-time audit for the native X-only cohort."""
from validate import *
import argparse

def audit(root, directory, planned):
    if type(planned) is not int or planned < 0:raise ValueError('planned must be a nonnegative integer')
    if not directory.is_dir():raise ValueError('directory must be an existing directory')
    cases={c['case_id']:c for c in json.loads((root/'src/research/configs/cvrplib_cases.json').read_text())['cases']}
    rows=[]; keys=[]
    for path in sorted(directory.glob('*.json')):
        if path.name.endswith('.started.json'):continue
        r=json.loads(path.read_text())
        if r.get('cohort')!='vendor_X12':continue
        c=cases[r['case_id']]; p=read_instance(root/c['instance_path']); errors=[]
        if c['source_family']!='X' or c['contract']['vehicle_limit'] is not None:errors.append('not unrestricted X contract')
        if sha(root/c['instance_path'])!=r.get('instance_sha256') or r.get('instance_sha256')!=c['instance_sha256']:errors.append('instance identity mismatch')
        if r.get('deadline_ns',0)-r.get('start_ns',0)!=int(Decimal(str(r['budget_seconds']))*10**9):errors.append('budget timing mismatch')
        incumbent=r.get('incumbent'); solution=None
        if incumbent:
            declared=Decimal(str(incumbent['cost']))
            if not declared.is_finite() or declared!=declared.to_integral_value():errors.append('noninteger declared cost')
            else:
                solution=check(p,incumbent['routes'],int(declared),len(p['demands'])-1)
                errors.extend(solution['errors'])
            if not r['start_ns']<=incumbent['receipt_ns']<=r['deadline_ns']:errors.append('late or invalid receipt')
            if not any(e==incumbent for e in r.get('events',[])):errors.append('incumbent absent from event stream')
        if r.get('invalid_outputs'):errors.append('native emitted invalid output')
        keys.append((r['case_id'],r['algorithm'],r['seed']))
        rows.append({'file':path.name,'sha256':sha(path),'status':r['status'],'returncode':r['returncode'],
                     'count_in_denominator':True,'has_valid_on_time_solution':bool(incumbent) and not errors,
                     'errors':errors,'solution':solution})
    duplicates=[list(k) for k,v in Counter(keys).items() if v>1]
    if len(rows)>planned:raise ValueError('planned denominator too small')
    return {'planned':planned,'attempted':len(rows),'not_started':planned-len(rows),'duplicates':duplicates,
            'audit_errors':sum(bool(r['errors']) for r in rows),'rows':rows}

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('directory',type=Path);ap.add_argument('--planned',type=int,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();report=audit(Path(__file__).resolve().parents[1],args.directory,args.planned)
    with args.output.open('x') as output:
        output.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}))
    raise SystemExit(1 if report['audit_errors'] or report['duplicates'] else 0)

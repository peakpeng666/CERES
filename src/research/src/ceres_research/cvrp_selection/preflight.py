"""No solver imports: independently validates saved CVRP routes and data contracts."""
from pathlib import Path
import json, math, hashlib, collections, time
ROOT = Path(__file__).resolve().parents[1]
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load_json(path): return json.loads(Path(path).read_text())
def instance_digest(path):
    return hashlib.sha256(json.dumps(load_json(path),sort_keys=True,separators=(',',':')).encode()).hexdigest()
def check_route(instance, row):
    n=len(instance['coordinates'])-1
    assert instance['depot']==0 and instance['fleet']=='unlimited_identical_vehicles'
    assert instance['objective']=='sum_unrounded_euclidean_distance'
    seen=[]; terms=[]
    routes=row['output']['routes']
    assert row['routes']==routes and isinstance(routes,list) and 0<len(routes)<=n
    for route in routes:
        assert isinstance(route,list) and 3<=len(route)<=n+2
        assert all(type(i) is int and 0<=i<=n for i in route)
        assert route[0]==route[-1]==0 and 0 not in route[1:-1]
        seen.extend(route[1:-1])
        assert sum(instance['demands'][i] for i in route[1:-1])<=instance['capacity']
        for a,b in zip(route,route[1:]):
            x,y=instance['coordinates'][a],instance['coordinates'][b]
            terms.append(math.sqrt((x[0]-y[0])**2+(x[1]-y[1])**2))
    assert sorted(seen)==list(range(1,n+1))
    cost=math.fsum(terms)
    for c in [row['recomputed_cost'],row['output']['reported_cost']]:
        assert math.isfinite(c) and math.isclose(c,cost,rel_tol=1e-9,abs_tol=1e-9)
    return cost

def validate(write=True):
    wall=time.perf_counter();cpu=time.process_time()
    plan=load_json(ROOT/'PLAN.json')
    manifest=load_json(ROOT/'data/instance_manifest.json')['instances']
    assert len(manifest)==24 and len({m['instance_id'] for m in manifest})==24
    assert collections.Counter((m['family'],m['n_customers']) for m in manifest)=={(f,n):4 for f in ['uniform','clustered'] for n in [50,100,200]}
    for source in load_json(ROOT/'SOURCE_MANIFEST.json'):
        assert digest(ROOT/source['copy'])==source['sha256'],source['copy']
    instances={}
    for m in manifest:
        p=ROOT/'data/instances'/m['file']
        assert instance_digest(p)==m['sha256']
        x=load_json(p);assert x['instance_id']==m['instance_id']
        assert len(x['coordinates'])==len(x['demands'])==m['n_customers']+1
        instances[m['instance_id']]=x
    rows=[json.loads(line) for line in (ROOT/'data/raw.jsonl').read_text().splitlines()]
    assert len(rows)==432
    keys=set();max_error=0.;checks=[]
    for row in rows:
        iid=row['instance_id'];key=(iid,row['algorithm'],row['profile'],row['solver_seed'])
        assert key not in keys; keys.add(key)
        assert row['algorithm'] in plan['algorithms'] and row['profile'] in plan['profiles'] and row['solver_seed'] in plan['solver_seeds']
        assert row['status']=='returned' and row['accepted'] is True and row['budget_conformant'] is True and row['scoreable'] is True
        assert not row['checker_errors'] and row['child_exit_code']==0
        assert row['solver_cpu_seconds']<=row['cpu_budget_seconds'] and row['process_wall_seconds']<=row['wall_budget_seconds']
        assert row['instance_sha256']==instance_digest(ROOT/'data/instances'/f'{iid}.json')
        cost=check_route(instances[iid],row)
        max_error=max(max_error,abs(cost-row['recomputed_cost']))
        checks.append({'key':key,'feasible':True,'recomputed_cost':cost,'absolute_cost_difference':abs(cost-row['recomputed_cost'])})
    assert len(keys)==24*3*2*3
    for f in range(4):
        roles={'train':[],'cal':[],'test':[]}
        for m in manifest:
            r=m['replicate'];role='test' if r==f else 'cal' if r==(f+1)%4 else 'train'
            roles[role].append(m['instance_id'])
        assert list(map(len,roles.values()))==[12,6,6]
        assert not(set(roles['train']) & set(roles['cal']) or set(roles['train']) & set(roles['test']) or set(roles['cal']) & set(roles['test']))
    result={'passed':True,'instances':24,'rows':432,'all_routes_revalidated':True,'complete_factorial':True,'all_declared_budgets_conformant':True,'source_hashes_unchanged':True,'split_grouping_passed':True,'max_absolute_cost_difference':max_error,'cpu_seconds':time.process_time()-cpu,'wall_seconds':time.perf_counter()-wall}
    if write:
        (ROOT/'results/preflight.json').write_text(json.dumps(result,indent=2))
        (ROOT/'results/route_revalidation.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in checks))
    return result

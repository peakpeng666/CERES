"""Independent verifier and small exhaustive oracle: no solver imports/matrices."""
import itertools, math
from collections import Counter

def check(raw, output, K):
    xy=raw['coordinates']; demands=raw['demands']; n=len(demands)-1
    errors=[]; loads=[]; lengths=[]; counts=Counter()
    if not isinstance(output,dict) or not isinstance(output.get('routes'),list):
        return {'accepted':False,'errors':['malformed_output'],'cost':None}
    routes=output['routes']
    if len(routes)>K: errors.append('vehicle_upper_bound_exceeded')
    for i,r in enumerate(routes):
        if not isinstance(r,list) or len(r)<3 or any(type(j)!=int or not 0<=j<=n for j in r):
            errors.append('invalid_route_shape_or_id'); continue
        if r[0]!=0 or r[-1]!=0 or 0 in r[1:-1]: errors.append('invalid_depot')
        counts.update(r[1:-1]); load=sum(demands[j] for j in r[1:-1]); loads.append(load)
        if load>raw['capacity']: errors.append('capacity_exceeded')
        for a,b in zip(r,r[1:]):
            dx=xy[a][0]-xy[b][0];dy=xy[a][1]-xy[b][1];lengths.append(math.sqrt(dx*dx+dy*dy))
    missing=sorted(set(range(1,n+1))-set(counts)); duplicate=sorted(j for j,c in counts.items() if c!=1)
    if missing: errors.append('missing_customers')
    if duplicate: errors.append('duplicate_customers')
    cost=math.fsum(lengths); claimed=output.get('reported_cost',output.get('cost'))
    if type(claimed) not in (int,float) or not math.isfinite(claimed) or not math.isclose(cost,claimed,abs_tol=1e-8,rel_tol=1e-10): errors.append('reported_cost_mismatch')
    return {'accepted':not errors,'errors':errors,'cost':cost,'loads':loads,'nonempty_routes':len(routes),'missing':missing,'duplicates':duplicate,'objective_residual':cost-claimed if type(claimed) in (int,float) and math.isfinite(claimed) else None}

def brute(raw):
    """Enumerate every customer permutation and every nonempty contiguous cut.
    Covers every unordered route partition and route ordering (with redundancy).
    Report at-most-K minima; unused vehicles are absent, never required empty routes.
    """
    n=len(raw['demands'])-1; best={k:None for k in range(1,n+1)}; counts=0; feasible=0
    for perm in itertools.permutations(range(1,n+1)):
        for cuts in range(1<<max(0,n-1)):
            routes=[]; start=0
            for j in range(n-1):
                if cuts>>j&1: routes.append(perm[start:j+1]); start=j+1
            routes.append(perm[start:]); counts+=1
            if any(sum(raw['demands'][j] for j in r)>raw['capacity'] for r in routes): continue
            feasible+=1; edges=[]
            for route in routes:
                r=(0,)+route+(0,)
                for a,b in zip(r,r[1:]):
                    ax,ay=raw['coordinates'][a]; bx,by=raw['coordinates'][b]
                    edges.append(math.sqrt((ax-bx)**2+(ay-by)**2))
            cost=math.fsum(edges); canonical=tuple(sorted(routes)); candidate=(cost,canonical)
            for k in range(len(routes),n+1):
                if best[k] is None or candidate<best[k]:best[k]=candidate
    return {'candidates':counts,'capacity_feasible_candidates':feasible,'by_k':{str(k):None if v is None else {'cost':v[0],'routes':[[0,*r,0] for r in v[1]]} for k,v in best.items()}}

def unit_tests():
    raw={'coordinates':[[0,0],[3,4],[0,6]],'demands':[0,2,2],'capacity':3}
    good={'routes':[[0,1,0],[0,2,0]],'reported_cost':22.}
    tests=[]
    assert check(raw,good,2)['accepted'];tests.append('valid_two_routes')
    for label,o,k,error in [
        ('vehicle_cap',good,1,'vehicle_upper_bound_exceeded'),
        ('overload',{'routes':[[0,1,2,0]],'reported_cost':0},2,'capacity_exceeded'),
        ('missing',{'routes':[[0,1,0]],'reported_cost':10},2,'missing_customers'),
        ('duplicate',{'routes':[[0,1,0],[0,1,0]],'reported_cost':20},2,'duplicate_customers'),
        ('unknown',{'routes':[[0,99,0]],'reported_cost':0},2,'invalid_route_shape_or_id'),
        ('empty_route',{'routes':[[0,0],[0,1,0],[0,2,0]],'reported_cost':22},3,'invalid_route_shape_or_id'),
        ('wrong_cost',dict(good,reported_cost=23),2,'reported_cost_mismatch')]:
        assert error in check(raw,o,k)['errors'];tests.append(label)
    assert check(raw,good,9)['accepted'];tests.append('at_most_K_not_exactly_K')
    return tests

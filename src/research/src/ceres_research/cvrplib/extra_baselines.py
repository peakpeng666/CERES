"""Integer CVRPLIB adapters for CERES historical NN/sweep and feasible VND.

Restores construction rules and VND move bodies; integer metric and end-to-end
clock are explicit new adapters. Not identical to historical continuous runs.
"""
import math
import random
import time
from types import SimpleNamespace
from . import restored_vnd as vnd

ALGORITHMS = ('nearest_neighbor', 'seeded_sweep', 'savings_vnd', 'multistart_vnd')


def solve(payload, deadline_ns, publish):
    p = payload
    def check():
        if time.monotonic_ns() >= deadline_ns:
            raise TimeoutError('end-to-end deadline')
    class Clock:
        def check(self):
            check()
    clock = Clock()
    xy, q, cap = p['coordinates'], p['demands'], p['capacity']
    n, K = len(q)-1, p['K']
    d = []
    for a in xy:
        check()
        d.append([math.floor(math.hypot(a[0]-b[0], a[1]-b[1])+0.5) for b in xy])
    inst = SimpleNamespace(demands=q, capacity=cap)
    rng = random.Random(p['seed'])
    best = math.inf
    def emit(routes):
        nonlocal best
        check()
        if len(routes) > K:
            return False
        cost = sum(d[a][b] for r in routes for a,b in zip([0]+r,r+[0]))
        if cost < best:
            publish([[0,*r,0] for r in routes], cost)
            best = cost
        return True
    def construct(kind, seed):
        if kind == 'nearest_neighbor':
            remaining = set(range(1,n+1)); routes=[]
            while remaining:
                r=[];load=0;last=0
                while remaining:
                    check()
                    feasible=[j for j in remaining if load+q[j]<=cap]
                    if not feasible: break
                    node=min(feasible,key=lambda j:(d[last][j],j))
                    r.append(node);load+=q[node];last=node;remaining.remove(node)
                if not r: raise ValueError('demand exceeds capacity')
                routes.append(r)
            return routes
        if kind == 'seeded_sweep':
            rr=random.Random(seed);origin=rr.random()*2*math.pi;direction=rr.choice((-1,1))
            order=sorted(range(1,n+1),key=lambda j:((direction*math.atan2(xy[j][1]-xy[0][1],xy[j][0]-xy[0][0])-origin)%(2*math.pi),j))
            routes=[];r=[];load=0
            for j in order:
                check()
                if q[j]>cap: raise ValueError('demand exceeds capacity')
                if r and load+q[j]>cap: routes.append(r);r=[];load=0
                r.append(j);load+=q[j]
            if r: routes.append(r)
            return routes
        routes={i:[i] for i in range(1,n+1)}; owner={i:i for i in routes};loads={i:q[i] for i in routes}
        candidates=[]
        for i in range(1,n+1):
            check()
            for j in range(i+1,n+1): candidates.append((-(d[0][i]+d[0][j]-d[i][j]),i,j))
        candidates.sort()
        for neg,i,j in candidates:
            check();a,b=owner[i],owner[j]
            if neg>=0 or a==b or loads[a]+loads[b]>cap: continue
            left,right=routes[a],routes[b]
            if i not in (left[0],left[-1]) or j not in (right[0],right[-1]):continue
            if left[-1]!=i:left=left[::-1]
            if right[0]!=j:right=right[::-1]
            routes[a]=left+right;loads[a]+=loads.pop(b);del routes[b]
            for node in routes[a]: owner[node]=a
        return [routes[k] for k in sorted(routes)]
    algorithm=p['algorithm']
    if algorithm not in ALGORITHMS: raise ValueError(algorithm)
    if algorithm in ('nearest_neighbor','seeded_sweep'):
        routes=construct(algorithm,p['seed'])
        if not emit(routes): return {'termination': 'construction_exceeds_fleet_cap', 'requested_starts': 1, 'completed_starts': 1}
        return {'requested_starts': 1, 'completed_starts': 1}
    state=vnd.SearchState(2**63-1,clock,0)
    original_observe=state.observe
    def observe(routes, matrix):
        if len(routes)<=K:
            original_observe(routes,matrix)
            emit(routes)
    state.observe=observe
    starts=[('parallel_savings',p['seed'])]
    if algorithm=='multistart_vnd':
        starts += [('nearest_neighbor',p['seed'])]
        starts += [('seeded_sweep',rng.randrange(2**32)) for _ in range(6)]
    completed_starts = 0
    for kind,seed in starts:
        check();routes=construct(kind,seed)
        # Moves never add routes; invalid-fleet starts may become valid by relocate.
        vnd.descend(inst,routes,d,state)
        completed_starts += 1
    if best == math.inf: return {'termination': 'no_incumbent_satisfying_fleet_cap', 'requested_starts': len(starts), 'completed_starts': completed_starts}
    return {'requested_starts': len(starts), 'completed_starts': completed_starts}

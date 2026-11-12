"""Independent exact oracle: directly search original road graph plus service mask.
No imports from solver or checker; no mode or pairwise cost matrix reuse.
"""
import heapq,math,time
from functools import lru_cache

def solve(inst):
    if inst.get('fleet_limit') is not None:
        raise ValueError('turn_modes oracle does not support fleet_limit; expected null')
    startwall=time.perf_counter();startcpu=time.process_time()
    depot=inst['depot'];cap=inst['capacity'];tasks=inst['customers'];n=len(tasks);full=(1<<n)-1
    lookup={}
    for i,task in enumerate(tasks):
        lookup.setdefault(task['node'],[]).append(i)
    neighbors={}
    for u,v,c in inst['arcs']:neighbors.setdefault(u,[]).append((v,c))
    banned=set(map(tuple,inst['forbidden_turns']));pen={tuple(t[:3]):t[3] for t in inst['turn_penalties']}
    loads={m:sum(t['demand'] for i,t in enumerate(tasks) if m&(1<<i)) for m in range(full+1)}
    source=(0,-1,depot);distance={source:0};previous={};heap=[(0,source)];closed={};endstates={};expanded=0;relaxed=0
    while heap:
        cost,state=heapq.heappop(heap)
        if cost!=distance[state]:continue
        expanded+=1;mask,prior,node=state
        if node==depot and mask and cost<closed.get(mask,math.inf):closed[mask]=cost;endstates[mask]=state
        options=[]
        for i in lookup.get(node,[]):
            m=mask|(1<<i)
            if m!=mask and loads[m]<=cap:options.append(((m,prior,node),0,('serve',i)))
        for nxt,base in neighbors.get(node,[]):
            if prior!=-1 and (prior,node,nxt) in banned:continue
            extra=pen.get((prior,node,nxt),0) if prior!=-1 else 0
            options.append(((mask,node,nxt),base+extra,('move',nxt)))
        for nextstate,weight,action in options:
            relaxed+=1;candidate=cost+weight
            if candidate<distance.get(nextstate,math.inf):
                distance[nextstate]=candidate;previous[nextstate]=(state,action);heapq.heappush(heap,(candidate,nextstate))
    @lru_cache(None)
    def partition(mask):
        if mask==0:return 0,()
        required=(mask&-mask);best=(math.inf,())
        # Enumerate all available route masks, unlike solver submask loop.
        for served,cost in sorted(closed.items()):
            if served&required and served&mask==served:
                rest,path=partition(mask^served)
                if cost+rest<best[0]:best=(cost+rest,(served,)+path)
        return best
    optimum,masks=partition(full)
    routes=[]
    if optimum!=math.inf:
        for mask in masks:
            s=endstates[mask];actions=[]
            while s!=source:
                s,act=previous[s];actions.append(act)
            nodes=[depot];services=[]
            for action,value in reversed(actions):
                if action=='move':nodes.append(value)
                else:services.append({'customer':tasks[value]['id'],'position':len(nodes)-1})
            routes.append({'nodes':nodes,'services':services,'cost':closed[mask]})
    return {'method':'raw_state_oracle','status':'optimal' if optimum!=math.inf else 'infeasible',
      'cost':optimum if optimum!=math.inf else None,'routes':routes,'expanded_states':expanded,
      'relaxations':relaxed,'reachable_states':len(distance),'wall_s':time.perf_counter()-startwall,
      'cpu_s':time.process_time()-startcpu}

"""Frozen synthetic panel. No rejection sampling or outcome-driven selection."""
import json, math, random
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def grid(rows,cols,n,seed,rule):
    rng=random.Random(seed*100+rows*10+cols)
    coords={i:(i%cols,i//cols) for i in range(rows*cols)}
    arcs=[]
    for u,(x,y) in coords.items():
        for v,(xx,yy) in coords.items():
            if abs(x-xx)+abs(y-yy)==1: arcs.append([u,v,rng.randint(1,9)])
    nodes=rng.sample(list(range(1,rows*cols)),n)
    customers=[{'id':i,'node':v,'demand':rng.randint(1,4)} for i,v in enumerate(nodes)]
    forb=[]; penalties=[]; rr=random.Random(seed*1000+rows*10+cols)
    for u,v,_ in arcs:
        for vv,w,_ in arcs:
            if vv!=v: continue
            if u==w:
                if rule!='none': forb.append([u,v,w])
                continue
            ax,ay=coords[v][0]-coords[u][0],coords[v][1]-coords[u][1]
            bx,by=coords[w][0]-coords[v][0],coords[w][1]-coords[v][1]
            left=ax*by-ay*bx>0
            if left and rule=='left_ban' and rr.random()<.15: forb.append([u,v,w])
            elif left and rule in ('left_cost','left_ban'): penalties.append([u,v,w,2 if rule=='left_cost' else 5])
    return {'id':f'g{rows}x{cols}_s{seed}_{rule}','seed':seed,'shape':[rows,cols],
      'rule':rule,'depot':0,'capacity':math.ceil(sum(x['demand'] for x in customers)/2),
      'customers':customers,'arcs':arcs,'forbidden_turns':forb,'turn_penalties':penalties,
      'coordinates':coords,'units':'synthetic_integer_cost','service_heading_reset':False,
      'depot_reset':'between_routes_only','fleet_limit':None}

def fixtures():
    dead={'id':'dead_end_infeasible','depot':0,'capacity':1,'customers':[{'id':0,'node':1,'demand':1}],
       'arcs':[[0,1,1],[1,0,1]],'forbidden_turns':[[0,1,0]],'turn_penalties':[],
       'units':'synthetic_integer_cost','service_heading_reset':False,'depot_reset':'between_routes_only','fleet_limit':None}
    cycle={**dead,'id':'single_customer_cycle','arcs':[[0,1,1],[1,0,1],[1,2,1],[2,0,1]],
       'forbidden_turns':[[0,1,0]],'coordinates':{0:[0,0],1:[1,0],2:[.5,1]}}
    return [dead,cycle,grid(3,3,4,7000,'none')]

def main():
    root=ROOT/'data'; root.mkdir(exist_ok=True)
    for split in ('smoke','formal','fixtures'): (root/split).mkdir(exist_ok=True)
    for k in range(6):
        obj=grid(3,3,5,8000+k,['uturn','left_cost','left_ban'][k%3]);obj['split']='smoke'
        (root/'smoke'/f"{obj['id']}.json").write_text(json.dumps(obj,indent=2))
    for rows,cols,n in ((3,3,5),(3,4,6),(4,4,7)):
        for seed in range(10000,10004):
            for rule in ('uturn','left_cost','left_ban'):
                obj=grid(rows,cols,n,seed,rule);obj['split']='formal'
                (root/'formal'/f"{obj['id']}.json").write_text(json.dumps(obj,indent=2))
    for obj in fixtures():
        obj['split']='fixtures';(root/'fixtures'/f"{obj['id']}.json").write_text(json.dumps(obj,indent=2))

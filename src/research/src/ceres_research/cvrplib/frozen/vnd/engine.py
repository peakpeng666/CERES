"""Deterministic budgeted VND; no reference input, no random source or network."""
import math
OPS = {'T': ('two_opt',), 'TR': ('two_opt','relocate'),
       'TS': ('two_opt','swap'), 'TRS': ('two_opt','relocate','swap'),
       'TSR': ('two_opt','swap','relocate')}

def matrix(coords):
    return [[int(math.floor(math.hypot(a[0]-b[0],a[1]-b[1])+0.5)) for b in coords] for a in coords]

def cost(routes,d):
    return sum(d[a][b] for r in routes for a,b in zip(r,r[1:]))

def candidates(routes,op):
    if op=='two_opt':
        for a,r in enumerate(routes):
            for i in range(1,len(r)-2):
                for j in range(i+1,len(r)-1): yield (a,i,a,j)
    elif op=='relocate':
        for a,r in enumerate(routes):
            for i in range(1,len(r)-1):
                for b,s in enumerate(routes):
                    if a!=b:
                        for j in range(1,len(s)): yield (a,i,b,j)
    elif op=='swap':
        for a,r in enumerate(routes):
            for b in range(a+1,len(routes)):
                for i in range(1,len(r)-1):
                    for j in range(1,len(routes[b])-1): yield (a,i,b,j)
    else: raise ValueError(op)

def evaluate(routes,loads,demands,q,d,op,c):
    a,i,b,j=c;r=routes[a];s=routes[b];x=r[i]
    if op=='two_opt':
        delta=d[r[i-1]][r[j]]+d[r[i]][r[j+1]]-d[r[i-1]][r[i]]-d[r[j]][r[j+1]]
        return True,delta
    if op=='relocate':
        feasible=loads[b]+demands[x]<=q
        if not feasible:return False,None
        delta=d[r[i-1]][r[i+1]]-d[r[i-1]][x]-d[x][r[i+1]]
        delta+=d[s[j-1]][x]+d[x][s[j]]-d[s[j-1]][s[j]]
        return True,delta
    y=s[j]
    feasible=loads[a]-demands[x]+demands[y]<=q and loads[b]-demands[y]+demands[x]<=q
    if not feasible:return False,None
    delta=d[r[i-1]][y]+d[y][r[i+1]]-d[r[i-1]][x]-d[x][r[i+1]]
    delta+=d[s[j-1]][x]+d[x][s[j+1]]-d[s[j-1]][y]-d[y][s[j+1]]
    return True,delta

def apply(routes,loads,demands,op,c):
    a,i,b,j=c
    if op=='two_opt':routes[a][i:j+1]=reversed(routes[a][i:j+1])
    elif op=='relocate':
        x=routes[a].pop(i);routes[b].insert(j,x);loads[a]-=demands[x];loads[b]+=demands[x]
    else:
        x,y=routes[a][i],routes[b][j];routes[a][i],routes[b][j]=y,x
        loads[a]+=demands[y]-demands[x];loads[b]+=demands[x]-demands[y]

def search(coords,demands,q,routes,arm,budget):
    assert arm in OPS and type(budget)is int and budget>=0
    d=matrix(coords);routes=[r[:] for r in routes];loads=[sum(demands[x] for x in r[1:-1]) for r in routes]
    initial=cost(routes,d);current=initial;ops=OPS[arm];opidx=0;trace=[];scans=[];moves=[]
    counts={op:{'attempted':0,'feasible':0,'accepted':0} for op in ops};checkpoints=[]
    for level in (0,100,1000,5000,10000,20000):
        if level==0:checkpoints.append({'attempted':0,'cost':current})
    stop='BUDGET_EXHAUSTED'
    while len(trace)<budget:
        op=ops[opidx];found=False;start=len(trace);scan_complete=True
        for c in candidates(routes,op):
            if len(trace)>=budget:scan_complete=False;break
            feasible,delta=evaluate(routes,loads,demands,q,d,op,c)
            accepted=bool(feasible and delta<0);counts[op]['attempted']+=1;counts[op]['feasible']+=int(feasible)
            # Trace column schema frozen in PROTOCOL: op,a,i,b,j,feasible,delta,accepted.
            trace.append([op,*c,int(feasible),delta,int(accepted)])
            if accepted:
                apply(routes,loads,demands,op,c);current+=delta;counts[op]['accepted']+=1
                moves.append({'attempted':len(trace),'op':op,'candidate':list(c),'delta':delta,'cost':current})
            if len(trace) in (100,1000,5000,10000,20000):checkpoints.append({'attempted':len(trace),'cost':current})
            if accepted:found=True;scan_complete=False;break
        scans.append({'op':op,'start_attempted':start,'end_attempted':len(trace),'complete_nonimproving':scan_complete and not found,'accepted':found})
        if found:opidx=0
        elif scan_complete:
            opidx+=1
            if opidx==len(ops):stop='LOCAL_OPTIMUM';break
        else:break
    assert current==cost(routes,d) and current<=initial
    return {'arm':arm,'budget':budget,'routes':routes,'loads':loads,'initial_cost':initial,'cost':current,
        'attempted':len(trace),'feasible':sum(v['feasible'] for v in counts.values()),'accepted':len(moves),
        'counts':counts,'termination':stop,'unspent_budget':budget-len(trace),'trace':trace,
        'scans':scans,'accepted_moves':moves,'checkpoints':checkpoints}

"""New transparent GA/PSO/DE random-key adapters, not historical CVRP results.
Random keys sort customers into a giant tour; exact shortest-path Split decodes
that *fixed order*. It is not an exact CVRP solver, HGS, or paper reproduction.
All three share decoder and population 20; no post-hoc tuning or local search.
Split reference: Vidal (2016), https://arxiv.org/abs/1508.02759
Our simple quadratic DP is not that paper's linear-time implementation.
"""
import math
import random
import time
ALGORITHMS=('ga_random_keys','pso_random_keys','de_random_keys')
CONFIG={'population':20,'ga_tournament':3,'ga_mutation_probability':'1/n',
        'pso_inertia':0.7298,'pso_cognitive':1.49618,'pso_social':1.49618,
        'pso_velocity_clip':0.2,'de_F':0.5,'de_CR':0.9,'de_strategy':'rand/1/bin'}

def split(order, d, demands, capacity, K, check=lambda:None):
    n=len(order)
    # Build feasible contiguous subsequences once per decoded order.
    edges=[[] for _ in range(n)]
    for i in range(n):
        check();load=0;cost=0;previous=0
        for j in range(i,n):
            check();node=order[j];load+=demands[node]
            if load>capacity:break
            cost+=d[previous][node];previous=node
            edges[i].append((j+1,cost+d[node][0]))
    def recover(prev, end):
        out=[]
        while end:
            start=prev[end];out.append([0,*order[start:end],0]);end=start
        return out[::-1]
    best=[math.inf]*(n+1);best[0]=0;prev=[None]*(n+1)
    for i in range(n):
        check()
        for j,cost in edges[i]:
            value=best[i]+cost
            if value<best[j]:best[j]=value;prev[j]=i
    if not math.isfinite(best[n]):return math.inf,[]
    routes=recover(prev,n)
    if len(routes)<=K:return best[n],routes
    # At-most K constrained shortest path; preserves feasible cap semantics.
    layers=[[math.inf]*(n+1) for _ in range(K+1)];layers[0][0]=0
    parents={}
    for k in range(1,K+1):
        for i in range(n):
            check()
            if not math.isfinite(layers[k-1][i]):continue
            for j,cost in edges[i]:
                value=layers[k-1][i]+cost
                if value<layers[k][j]:layers[k][j]=value;parents[k,j]=i
    k=min(range(1,K+1),key=lambda k:layers[k][n]);value=layers[k][n]
    if not math.isfinite(value):return math.inf,[]
    out=[];j=n
    while j:
        i=parents[k,j];out.append([0,*order[i:j],0]);j=i;k-=1
    return value,out[::-1]

def solve(p,deadline_ns,publish):
    def check():
        if time.monotonic_ns()>=deadline_ns:raise TimeoutError('end-to-end deadline')
    if p['algorithm'] not in ALGORITHMS:raise ValueError(p['algorithm'])
    xy=p['coordinates'];n=len(xy)-1;rng=random.Random(p['seed']);d=[]
    for a in xy:
        check();d.append([math.floor(math.hypot(a[0]-b[0],a[1]-b[1])+0.5) for b in xy])
    best=math.inf
    def evaluate(keys):
        nonlocal best
        check();order=sorted(range(1,n+1),key=lambda j:(keys[j-1],j))
        cost,routes=split(order,d,p['demands'],p['capacity'],p['K'],check)
        if cost<best:
            publish(routes,cost);best=cost
        return cost
    size=CONFIG['population'];pop=[[rng.random() for _ in range(n)] for _ in range(size)]
    scores=[evaluate(x) for x in pop]
    if p['algorithm']=='ga_random_keys':
        while True:
            check();elite=min(range(size),key=lambda i:scores[i]);new=[pop[elite][:]];new_scores=[scores[elite]]
            def parent():return pop[min(rng.sample(range(size),3),key=lambda i:scores[i])]
            while len(new)<size:
                a,b=parent(),parent();child=[a[j] if rng.random()<0.5 else b[j] for j in range(n)]
                for j in range(n):
                    if rng.random()<1/n:child[j]=rng.random()
                new_scores.append(evaluate(child));new.append(child)
            pop,scores=new,new_scores
    elif p['algorithm']=='pso_random_keys':
        velocity=[[0.0]*n for _ in range(size)];personal=[x[:] for x in pop];pscores=scores[:]
        while True:
            global_best=personal[min(range(size),key=lambda i:pscores[i])][:]
            for i in range(size):
                check()
                for j in range(n):
                    v=0.7298*velocity[i][j]+1.49618*rng.random()*(personal[i][j]-pop[i][j])+1.49618*rng.random()*(global_best[j]-pop[i][j])
                    velocity[i][j]=max(-.2,min(.2,v));pop[i][j]=max(0.,min(1.,pop[i][j]+velocity[i][j]))
                scores[i]=evaluate(pop[i])
                if scores[i]<pscores[i]:personal[i]=pop[i][:];pscores[i]=scores[i]
    else:
        while True:
            for i in range(size):
                check();a,b,c=rng.sample([j for j in range(size) if j!=i],3);forced=rng.randrange(n)
                trial=[max(0.,min(1.,pop[a][j]+.5*(pop[b][j]-pop[c][j]))) if j==forced or rng.random()<.9 else pop[i][j] for j in range(n)]
                value=evaluate(trial)
                if value<=scores[i]:pop[i],scores[i]=trial,value

"""P343-inspired incoming-arc modes and layered route DP, independent implementation.
Small exact CVRP wrapper is new validation scaffolding, not official UHGS code.
"""
import heapq, itertools, math, time
INF=math.inf

def _require_unlimited_fleet(inst):
    if inst.get('fleet_limit') is not None:
        raise ValueError('turn_modes solver does not support fleet_limit; expected null')

def graph(inst):
    out={};incoming={}
    for u,v,c in sorted(inst['arcs']):
        out.setdefault(u,[]).append((v,c));incoming.setdefault(v,[]).append((u,v))
    return out,incoming,{tuple(t) for t in inst['forbidden_turns']},{tuple(t[:3]):t[3] for t in inst['turn_penalties']}

def shortest(inst,start_node,source_arc=None):
    out,inc,forbidden,penalties=graph(inst)
    start=source_arc if source_arc is not None else (-1,start_node)
    dist={start:0};parent={};queue=[(0,start)];expansions=0
    while queue:
        cost,a=heapq.heappop(queue)
        if cost!=dist[a]:continue
        expansions+=1;u,v=a
        for w,base in out.get(v,[]):
            if u!=-1 and (u,v,w) in forbidden:continue
            b=(v,w);new=cost+base+(penalties.get((u,v,w),0) if u!=-1 else 0)
            if new<dist.get(b,INF):
                dist[b]=new;parent[b]=a;heapq.heappush(queue,(new,b))
    def path(target):
        if target not in dist:return None
        answer=[];p=target
        while p!=start:answer.append(p);p=parent[p]
        return list(reversed(answer))
    return dist,path,expansions

def preprocess(inst,kind):
    if kind not in {'mode','reset'}:
        raise ValueError('kind must be mode or reset')
    t=time.perf_counter();out,inc,_,_=graph(inst);tasks=inst['customers'];dep=inst['depot']
    labels=[];groups=[]
    for i,task in enumerate(tasks):
        g=[]
        states=inc.get(task['node'],[]) if kind=='mode' else [None]
        for a in states:g.append(len(labels));labels.append((i,a))
        groups.append(g)
    m=len(labels);start=[INF]*m;end=[INF]*m;conn=[[INF]*m for _ in range(m)]
    first={};between={};last={};expansions=0
    dd,pp,ex=shortest(inst,dep);expansions+=ex
    for j,(i,a) in enumerate(labels):
        targets=[a] if a else inc.get(tasks[i]['node'],[])
        b=min(targets,key=lambda b:(dd.get(b,INF),b),default=None)
        if b is not None and b in dd:start[j]=dd[b];first[j]=pp(b)
    for k,(i,a) in enumerate(labels):
        dd,pp,ex=shortest(inst,tasks[i]['node'],a);expansions+=ex
        targets=inc.get(dep,[]);b=min(targets,key=lambda b:(dd.get(b,INF),b),default=None)
        if b is not None and b in dd:end[k]=dd[b];last[k]=pp(b)
        for j,(ii,aa) in enumerate(labels):
            if i==ii:continue
            targets=[aa] if aa else inc.get(tasks[ii]['node'],[])
            b=min(targets,key=lambda b:(dd.get(b,INF),b),default=None)
            if b is not None and b in dd:conn[k][j]=dd[b];between[k,j]=pp(b)
    return {'labels':labels,'groups':groups,'start':start,'end':end,'conn':conn,'first':first,
      'between':between,'last':last,'preprocess_wall_s':time.perf_counter()-t,'shortest_expansions':expansions}

def subset_dp(inst,tab):
    _require_unlimited_fleet(inst)
    n=len(inst['customers']);full=(1<<n)-1;cap=inst['capacity'];dem=[x['demand'] for x in inst['customers']]
    load=[sum(dem[i] for i in range(n) if mask>>i&1) for mask in range(full+1)]
    dp=[{} for _ in range(full+1)];prev={};routes={};route_ends={};transitions=0
    for k,(i,_) in enumerate(tab['labels']):
        if dem[i]<=cap and tab['start'][k]!=INF:dp[1<<i][k]=tab['start'][k];prev[1<<i,k]=None
    for mask in range(1,full+1):
        for k,c in sorted(dp[mask].items()):
            rc=c+tab['end'][k]
            if rc<routes.get(mask,INF):routes[mask]=rc;route_ends[mask]=k
            for i in range(n):
                nxt=mask|(1<<i)
                if mask>>i&1 or load[nxt]>cap:continue
                for j in tab['groups'][i]:
                    transitions+=1;nc=c+tab['conn'][k][j]
                    if nc<dp[nxt].get(j,INF):dp[nxt][j]=nc;prev[nxt,j]=(mask,k)
    cover=[INF]*(full+1);choice={};cover[0]=0
    for mask in range(1,full+1):
        anchor=mask&-mask;sub=mask
        while sub:
            if sub&anchor and sub in routes:
                cost=routes[sub]+cover[mask^sub]
                if cost<cover[mask]:cover[mask]=cost;choice[mask]=sub
            sub=(sub-1)&mask
    if cover[full]==INF:return {'status':'infeasible','cost':None,'routes':[],'dp_transitions':transitions}
    answer=[];left=full
    while left:
        sub=choice[left];k=route_ends[sub];chain=[];st=(sub,k)
        while st is not None:chain.append(st[1]);st=prev[st]
        chain.reverse();r=unpack(inst,tab,chain);r['cost']=routes[sub];answer.append(r);left^=sub
    return {'status':'optimal','cost':cover[full],'routes':answer,'dp_transitions':transitions}

def unpack(inst,tab,chain):
    nodes=[inst['depot']];services=[]
    for pos,k in enumerate(chain):
        edges=tab['first'][k] if pos==0 else tab['between'][chain[pos-1],k]
        for u,v in edges:
            assert nodes[-1]==u
            nodes.append(v)
        services.append({'customer':inst['customers'][tab['labels'][k][0]]['id'],'position':len(nodes)-1})
    for u,v in tab['last'][chain[-1]]:
        assert nodes[-1]==u
        nodes.append(v)
    return {'nodes':nodes,'services':services,'mode_chain':[list(tab['labels'][k][1]) if tab['labels'][k][1] else None for k in chain]}

def fixed_sequence(inst,tab,sequence):
    """Freeze assignment/order; optimize incoming modes without resetting heading."""
    if any(type(i) is not int or not 0<=i<len(tab['groups']) for i in sequence) or len(set(sequence))!=len(sequence):
        raise ValueError('sequence must contain distinct customer indices')
    if not sequence:return {'status':'optimal','cost':0,'nodes':[inst['depot']],'services':[]}
    infeasible={'cost':None,'status':'infeasible'}
    dp={k:(tab['start'][k],[k]) for k in tab['groups'][sequence[0]] if tab['start'][k]!=INF}
    if not dp:return infeasible
    for i in sequence[1:]:
        nd={}
        for j in tab['groups'][i]:
            cost,k=min((v[0]+tab['conn'][k][j],k) for k,v in dp.items())
            if cost!=INF:nd[j]=(cost,dp[k][1]+[j])
        dp=nd
        if not dp:return infeasible
    cost,k=min((v[0]+tab['end'][k],k) for k,v in dp.items())
    if cost==INF:return infeasible
    return {**unpack(inst,tab,dp[k][1]),'cost':cost,'status':'optimal'}

def brute_modes(tab,sequence):
    if not sequence:return 0,1
    best=INF;count=0
    for ch in itertools.product(*(tab['groups'][i] for i in sequence)):
        c=tab['start'][ch[0]]+tab['end'][ch[-1]]+sum(tab['conn'][a][b] for a,b in zip(ch,ch[1:]));count+=1
        best=min(best,c)
    return None if best==INF else best,count

def solve(inst,kind='mode'):
    _require_unlimited_fleet(inst)
    cpu=time.process_time();wall=time.perf_counter();tab=preprocess(inst,kind)
    sol=subset_dp(inst,tab)
    sol.update(method=kind,wall_s=time.perf_counter()-wall,cpu_s=time.process_time()-cpu,
      preprocess_wall_s=tab['preprocess_wall_s'],shortest_expansions=tab['shortest_expansions'])
    return sol,tab

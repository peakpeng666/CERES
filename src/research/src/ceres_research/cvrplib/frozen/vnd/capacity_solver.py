"""Fixed-fleet-v2: capacity DFS only after original savings/one-shot FFD failure.
No oracle import, warm start, feasibility witness, or scoring feedback is accepted.
Unchanged primitive methods are imported from the locally frozen legacy source.
"""
import copy, math, random, signal, sys, time
from types import SimpleNamespace
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).parent/'legacy'))
from experiment import Budget, StopBudget, matrix_for, cost_of, savings, derived_seed, destroy, output_for
from audit import check

class InitFailure(RuntimeError): pass
class ConstructionFailure(InitFailure): pass

CAPACITY_NODE_LIMIT = 256
class RepairFailure(RuntimeError):
    def __init__(self, node, routes, remaining):
        super().__init__('no_feasible_insertion')
        self.detail={'blocked_node':node,'partial_routes':copy.deepcopy(routes),'remaining':sorted(remaining)}

def instance_for(raw):
    return SimpleNamespace(n_customers=len(raw['demands'])-1,demands=raw['demands'],coordinates=raw['coordinates'],capacity=raw['capacity'],K=raw['K'])

def ffd_routes(inst, matrix, budget):
    bins=[]; loads=[]
    for node in sorted(range(1,inst.n_customers+1),key=lambda j:(-inst.demands[j],j)):
        budget.check()
        if inst.demands[node]>inst.capacity: raise InitFailure('demand_exceeds_capacity')
        target=next((i for i,x in enumerate(loads) if x+inst.demands[node]<=inst.capacity),None)
        if target is None:
            if len(bins)>=inst.K: raise InitFailure('ffd_capacity_construction_failed')
            bins.append([]);loads.append(0);target=len(bins)-1
        bins[target].append(node);loads[target]+=inst.demands[node]
    routes=[]
    # Membership and customer order are exactly the deterministic FFD append order.
    for group in bins:
        route=[]
        for node in group:
            options=[]
            for pos in range(len(route)+1):
                left=route[pos-1] if pos else 0;right=route[pos] if pos<len(route) else 0
                budget.delta('insertion')
                options.append((matrix[left][node]+matrix[node][right]-matrix[left][right],pos))
            _,pos=min(options);route.insert(pos,node)
        routes.append(route)
    return routes

def bounded_partition(demands, capacity, K, node_limit=CAPACITY_NODE_LIMIT):
    """Bounded demand-only construction; no distances, routing oracle, or costs.
    Nodes count every entered recursion state, including root and complete state.
    Fixed descending-demand/customer-ID order, ascending bin ID, equal-residual
    symmetry pruning. Exceeding limit yields no witness, never infeasibility.
    """
    nodes=0; truncated=False
    order=sorted(range(1,len(demands)),key=lambda j:(-demands[j],j))
    bins=[[] for _ in range(K)]; residual=[capacity]*K
    def visit(depth):
        nonlocal nodes,truncated
        if nodes>=node_limit:
            truncated=True
            return None
        nodes+=1
        if depth==len(order):return [b[:] for b in bins if b]
        node=order[depth]; seen=set()
        for bi in range(K):
            room=residual[bi]
            if room in seen:continue
            seen.add(room)
            if demands[node]>room:continue
            bins[bi].append(node);residual[bi]-=demands[node]
            answer=visit(depth+1)
            if answer is not None:return answer
            bins[bi].pop();residual[bi]+=demands[node]
            if truncated:return None
        return None
    answer=visit(0)
    return answer, {'nodes':nodes,'node_limit':node_limit,'limit_reached':truncated,
        'outcome':'FOUND' if answer is not None else ('NODE_LIMIT' if truncated else 'SEARCH_EXHAUSTED'),
        'mathematical_infeasibility_claim':False}

def capacity_routes(inst,matrix,budget,diagnostic):
    budget.check(); cpu=time.process_time();wall=time.perf_counter()
    bins,report=bounded_partition(inst.demands,inst.capacity,inst.K)
    report['cpu_seconds']=time.process_time()-cpu
    report['wall_seconds']=time.perf_counter()-wall
    report['groups']=bins
    diagnostic['capacity_partition']=report
    diagnostic['version']='savings_ffd_capacity_partition_v2'
    budget.check()
    if bins is None:
        exc=ConstructionFailure('capacity_partition_construction_failed')
        exc.detail=diagnostic;raise exc
    # Identical fixed-bin insertion arithmetic and tie order to ffd_routes.
    routes=[]
    before=budget.deltas
    for group in bins:
        route=[]
        for node in group:
            options=[]
            for pos in range(len(route)+1):
                left=route[pos-1] if pos else 0;right=route[pos] if pos<len(route) else 0
                budget.delta('insertion')
                options.append((matrix[left][node]+matrix[node][right]-matrix[left][right],pos))
            _,pos=min(options);route.insert(pos,node)
        routes.append(route)
    report['route_insertion_deltas']=budget.deltas-before
    return routes

def initialize(raw,inst,matrix,budget):
    routes=savings(inst,matrix,budget)
    diagnostic={'savings_route_count':len(routes),'fallback_triggered':len(routes)>inst.K,'version':'savings_ffd_fixedK_v1','savings_routes':copy.deepcopy(routes),'deltas_after_savings':budget.deltas}
    if len(routes)>inst.K:
        try: routes=ffd_routes(inst,matrix,budget)
        except InitFailure as exc:
            diagnostic['original_ffd_failure']=str(exc)
            routes=capacity_routes(inst,matrix,budget,diagnostic)
    cost=cost_of(routes,matrix,budget);output=output_for(routes,cost)
    checked=check(raw,output,inst.K)
    if not checked['accepted']:
        exc=InitFailure('initializer_invalid_output');exc.detail=diagnostic|{'checker':checked};raise exc
    diagnostic['deltas_after_initialization']=budget.deltas
    return routes,cost,diagnostic

def route_options(node,routes,loads,instance,matrix,budget):
    options=[]
    for ri,route in enumerate(routes):
        if loads[ri]+instance.demands[node]>instance.capacity:continue
        best=None
        for pi in range(len(route)+1):
            left=route[pi-1] if pi else 0;right=route[pi] if pi<len(route) else 0
            budget.delta('insertion')
            option=(matrix[left][node]+matrix[node][right]-matrix[left][right],ri,pi)
            if best is None or option<best:best=option
        options.append(best)
    if len(routes)<instance.K and instance.demands[node]<=instance.capacity:
        budget.delta('insertion');options.append((matrix[0][node]+matrix[node][0],len(routes),0))
    return sorted(options)

def repair(partial,removed,name,instance,matrix,budget):
    routes=[r[:] for r in partial];loads=[sum(instance.demands[n] for n in r) for r in routes];remaining=set(removed)
    while remaining:
        choices=[]
        for node in sorted(remaining):
            options=route_options(node,routes,loads,instance,matrix,budget)
            if not options:raise RepairFailure(node,routes,remaining)
            delta,ri,pi=options[0]
            if name=='cheapest':key=(delta,node,ri,pi)
            elif name=='regret2':
                regret=options[1][0]-delta if len(options)>1 else math.inf
                key=(-regret,delta,node,ri,pi)
            else:raise ValueError(name)
            choices.append((key,node,ri,pi))
        _,node,ri,pi=min(choices)
        if ri==len(routes):routes.append([node]);loads.append(instance.demands[node])
        else:routes[ri].insert(pi,node);loads[ri]+=instance.demands[node]
        if loads[ri]>instance.capacity or len(routes)>instance.K:raise RuntimeError('internal_constraint_violation')
        remaining.remove(node)
    return routes

def evaluate_candidate(raw,partial,removed,name,instance,matrix,budget):
    """Never mutates a caller's incumbent; failure/truncation emits no candidate."""
    try:
        candidate=repair(partial,removed,name,instance,matrix,budget)
        cost=cost_of(candidate,matrix,budget)
        output=output_for(candidate,cost);checked=check(raw,output,instance.K)
        if not checked['accepted']:raise RuntimeError('candidate_invalid:'+repr(checked))
        return {'status':'complete','candidate':output,'checker':checked}
    except RepairFailure as exc:
        return {'status':'repair_failed','reason':str(exc),'detail':exc.detail,'candidate':None}
    except StopBudget as exc:
        return {'status':'budget_discarded','reason':str(exc),'candidate':None}

def run_one(raw,name,seed,cfg):
    inst=instance_for(raw);budget=Budget(cfg['delta_limit'],cfg['per_trajectory_cpu_seconds'],cfg['per_trajectory_wall_seconds'])
    incumbent=baseline=None;trace=[];init=None;error=None;accepted=completed=failed=started=0;stop='iteration_budget';status='INIT_FAIL';routes=None
    def wall_handler(*args):raise StopBudget('hard_wall_budget')
    def cpu_handler(*args):raise StopBudget('hard_cpu_budget')
    ow=signal.signal(signal.SIGALRM,wall_handler);oc=signal.signal(signal.SIGPROF,cpu_handler)
    signal.setitimer(signal.ITIMER_REAL,cfg['per_trajectory_wall_seconds']);signal.setitimer(signal.ITIMER_PROF,cfg['per_trajectory_cpu_seconds'])
    try:
        matrix=matrix_for(inst,budget)
        routes,cost,init=initialize(raw,inst,matrix,budget)
        incumbent=output_for(routes,cost);baseline=copy.deepcopy(incumbent);status='LEGAL_COMPLETION'
        for iteration in range(cfg['iterations']):
            budget.check();started+=1
            lo=min(inst.n_customers,max(2,math.ceil(.1*inst.n_customers)));hi=min(inst.n_customers,max(lo,math.ceil(.3*inst.n_customers)))
            q=random.Random(derived_seed(seed,'q',iteration)).randint(lo,hi)
            rng=random.Random(derived_seed(seed,'removal',iteration))
            partial,removed=destroy(routes,'random',q,rng,matrix,budget)
            result=evaluate_candidate(raw,partial,removed,name,inst,matrix,budget)
            entry={'iteration':iteration,'q':q,'removed':removed,'candidate_status':result['status'],'candidate':result['candidate'],'accepted':False,'deltas':budget.deltas}
            if result['status']=='complete':
                completed+=1;candidate_cost=result['candidate']['reported_cost']
                if candidate_cost<cost-1e-9:
                    routes=[r[1:-1] for r in result['candidate']['routes']];cost=candidate_cost
                    incumbent=copy.deepcopy(result['candidate']);accepted+=1;entry['accepted']=True
            else:
                entry['reason']=result['reason'];entry['detail']=result.get('detail')
                if result['status']=='repair_failed':failed+=1
            entry['incumbent']=copy.deepcopy(incumbent);trace.append(entry)
            if result['status']=='budget_discarded':stop=result['reason'];break
    except InitFailure as exc:
        if isinstance(exc,ConstructionFailure):status='CONSTRUCTION_FAIL'
        stop='initialization_failed';init=getattr(exc,'detail',None);error={'type':type(exc).__name__,'message':str(exc)}
    except StopBudget as exc:stop=str(exc)
    except Exception as exc:
        stop='algorithm_failure';status='ALGORITHM_FAILURE';error={'type':type(exc).__name__,'message':str(exc)}
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.setitimer(signal.ITIMER_PROF,0)
        signal.signal(signal.SIGALRM,ow);signal.signal(signal.SIGPROF,oc)
    usage=budget.snapshot();t=time.process_time();w=time.perf_counter()
    checked=check(raw,incumbent,inst.K);baseline_check=check(raw,baseline,inst.K)
    if incumbent is not None and not checked['accepted']:status='ILLEGAL_OUTPUT'
    return {'method':name,'search_seed':seed,'instance_id':raw['instance_id'],'K':inst.K,'status':status,'stop_reason':stop,'error':error,'initialization':init,'output':incumbent,'baseline':baseline,'check':checked,'baseline_check':baseline_check,'iterations_started':started,'iterations_completed':completed,'repair_failures':failed,'accepted_improvements':accepted,'trace':trace,**usage,'final_checker_cpu_seconds':time.process_time()-t,'final_checker_wall_seconds':time.perf_counter()-w}

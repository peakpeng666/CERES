"""Independent witness checker: reads raw graph and route witnesses only.
Never imports solver tables, modes or precomputed shortest-path distances.
"""
from collections import Counter

def check(instance,solution):
    """Reject malformed witnesses as evidence, including Python bool/int aliases.

    The graph uses nonnegative integer costs and integer identifiers. Schema
    checks below do not import any solver state or repair the proposed witness.
    A valid witness establishes feasibility and cost, not an optimality proof.
    """
    def rejected(reason):
        return {'valid':False,'errors':[reason],'computed_cost':None,'routes':[]}
    if not isinstance(instance,dict):return rejected('invalid_instance')
    if type(instance.get('depot')) is not int or instance['depot']<0 or type(instance.get('capacity')) is not int or instance['capacity']<0:
        return rejected('invalid_depot_or_capacity')
    for field,size in (('arcs',3),('forbidden_turns',3),('turn_penalties',4)):
        rows=instance.get(field)
        if not isinstance(rows,list) or any(not isinstance(row,(list,tuple)) or len(row)!=size or any(type(x) is not int or x<0 for x in row) for row in rows):
            return rejected('invalid_'+field)
    if len({tuple(row[:2]) for row in instance['arcs']})!=len(instance['arcs']):
        return rejected('duplicate_arc')
    if len({tuple(row[:3]) for row in instance['turn_penalties']})!=len(instance['turn_penalties']):
        return rejected('duplicate_turn_penalty')
    customers=instance.get('customers')
    if not isinstance(customers,list) or any(not isinstance(t,dict) or any(type(t.get(k)) is not int or t[k]<0 for k in ('id','node','demand')) for t in customers):
        return rejected('invalid_customers')
    if len({t['id'] for t in customers})!=len(customers):return rejected('duplicate_customer_id')
    fleet=instance.get('fleet_limit')
    if fleet is not None and (type(fleet) is not int or fleet<0):return rejected('invalid_fleet_limit')
    if not isinstance(solution,dict):return rejected('invalid_solution')
    routes=solution.get('routes')
    if not isinstance(routes,list):return rejected('routes_not_list')
    errors=[]; total=0; all_service=[]; details=[]
    depot=instance['depot'];cap=instance['capacity']
    arcs={(u,v):cost for u,v,cost in instance['arcs']}
    forbidden={tuple(t) for t in instance['forbidden_turns']}
    penalties={(u,v,w):c for u,v,w,c in instance['turn_penalties']}
    tasks={t['id']:t for t in instance['customers']}
    if solution.get('status')!='optimal':
        return {'valid':False,'errors':['no_feasible_witness'],'computed_cost':None,'routes':[]}
    malformed=False
    if fleet is not None and len(routes)>fleet:errors.append('fleet_limit')
    for ri,r in enumerate(routes):
        if not isinstance(r,dict):
            errors.append(f'route{ri}:invalid_route');malformed=True;continue
        nodes=r.get('nodes',[]);services=r.get('services',[]);cost=0;violations=[];load=0
        if not isinstance(nodes,list) or any(type(node) is not int or node<0 for node in nodes):
            errors.append(f'route{ri}:invalid_nodes');malformed=True;continue
        if not isinstance(services,list):
            errors.append(f'route{ri}:invalid_services');malformed=True;continue
        if len(nodes)<2 or nodes[0]!=depot or nodes[-1]!=depot: errors.append(f'route{ri}:depot_endpoints')
        for j,(u,v) in enumerate(zip(nodes,nodes[1:])):
            if (u,v) not in arcs: errors.append(f'route{ri}:unknown_arc:{j}')
            else:cost+=arcs[u,v]
        for j,triple in enumerate(zip(nodes,nodes[1:],nodes[2:])):
            if triple in forbidden:
                errors.append(f'route{ri}:forbidden_turn:{j+1}');violations.append({'position':j+1,'triple':list(triple)})
            cost+=penalties.get(triple,0)
        lastpos=-1
        for s in services:
            if not isinstance(s,dict):
                errors.append(f'route{ri}:invalid_service');continue
            cid=s.get('customer');p=s.get('position')
            if type(cid) is not int or cid not in tasks:errors.append(f'route{ri}:unknown_customer');continue
            all_service.append(cid)
            if type(p) is not int or not 0<=p<len(nodes) or nodes[p]!=tasks[cid]['node']:errors.append(f'route{ri}:service_location')
            if type(p) is int:
                if p<lastpos:errors.append(f'route{ri}:service_order')
                lastpos=p
            load+=tasks[cid]['demand']
        if not services:errors.append(f'route{ri}:empty_service_route')
        if load>cap:errors.append(f'route{ri}:capacity')
        if type(r.get('cost')) is not int or r['cost']!=cost:errors.append(f'route{ri}:cost_mismatch')
        total+=cost;details.append({'route':ri,'load':load,'cost':cost,'forbidden_turns':violations})
    counts=Counter(all_service)
    if counts!=Counter({k:1 for k in tasks}):errors.append('exact_once_service')
    if type(solution.get('cost')) is not int or solution['cost']!=total:errors.append('total_cost_mismatch')
    return {'valid':not errors,'errors':errors,'computed_cost':None if malformed else total,'routes':details}

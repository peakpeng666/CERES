"""Restored fixed destroy/repair LNS; not adaptive ALNS or HGS.
Uses the archived integer-CVRP capacity initializer and primitive operators.
New protocol replaces historical delta/iteration limits with one deadline.
"""
import math
import random
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).with_name('frozen')/'vnd'))
import capacity_solver as original
import engine

ALGORITHMS=('random_cheapest','random_regret2','worst_cheapest','worst_regret2')

def solve(p, deadline_ns, publish):
    def check():
        if time.monotonic_ns() >= deadline_ns: raise TimeoutError('end-to-end deadline')
    class ClockBudget(original.Budget):
        def check(self):
            self.checks += 1
            check()
    budget=ClockBudget(sys.maxsize,p['budget_seconds'],p['budget_seconds'])
    inst=original.instance_for(p)
    d=engine.matrix(p['coordinates'])
    check()
    routes=original.savings(inst,d,budget)
    if len(routes)>inst.K:
        try:routes=original.ffd_routes(inst,d,budget)
        except original.InitFailure: routes=original.capacity_routes(inst,d,budget,{})
    cost=original.cost_of(routes,d,budget)
    publish([[0,*r,0] for r in routes],cost)
    destroy_name,repair_name=p['algorithm'].split('_',1)
    if p['algorithm'] not in ALGORITHMS:raise ValueError(p['algorithm'])
    iteration=0
    while True:
        check()
        lo=min(inst.n_customers,max(2,math.ceil(.1*inst.n_customers)))
        hi=min(inst.n_customers,max(lo,math.ceil(.3*inst.n_customers)))
        q=random.Random(original.derived_seed(p['seed'],'q',iteration)).randint(lo,hi)
        rng=random.Random(original.derived_seed(p['seed'],'removal',iteration))
        partial,removed=original.destroy(routes,destroy_name,q,rng,d,budget)
        try:
            candidate=original.repair(partial,removed,repair_name,inst,d,budget)
        except original.RepairFailure:
            iteration+=1
            continue
        value=original.cost_of(candidate,d,budget)
        if value<cost:
            routes,cost=candidate,value
            publish([[0,*r,0] for r in routes],cost)
        iteration+=1

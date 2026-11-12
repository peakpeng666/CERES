"""Historical ORTools PCI+GLS adapted to unified integer inputs and outer clock.

Original: inventory_sources/ortools_history/worker.py (9.14.6206).
Changes: per-case K instead of hardcoded 5; numeric payload; publish feasible
incumbents before outer deadline instead of only returning final assignment.
No reference data, warm starts, or parameter tuning.
"""
import time
import ortools
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from .io import matrix

# Resolve ORTools lazy protobuf imports before reviewed-code I/O guard.
_PRELOAD_PARAMETERS = pywrapcp.DefaultRoutingSearchParameters()


def solve(p, deadline_ns, publish):
    if ortools.__version__ != '9.14.6206':
        raise RuntimeError('requires ORTools 9.14.6206')
    d=matrix(p['coordinates']);q=p['demands'];K=p['K']
    manager=pywrapcp.RoutingIndexManager(len(q),K,0)
    routing=pywrapcp.RoutingModel(manager)
    routing.solver().ReSeed(p['seed'])
    distance=routing.RegisterTransitCallback(lambda a,b:d[manager.IndexToNode(a)][manager.IndexToNode(b)])
    demand=routing.RegisterUnaryTransitCallback(lambda a:q[manager.IndexToNode(a)])
    routing.SetArcCostEvaluatorOfAllVehicles(distance)
    routing.AddDimensionWithVehicleCapacity(demand,0,[p['capacity']]*K,True,'Capacity')
    params=pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy=routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic=routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.guided_local_search_lambda_coefficient=0.1
    remain=max(0,(deadline_ns-time.monotonic_ns())/1e9)
    if remain<=0:raise TimeoutError('end-to-end deadline')
    params.time_limit.FromMilliseconds(max(1,int(remain*1000)))
    def observe():
        if time.monotonic_ns()>=deadline_ns:
            routing.solver().FinishCurrentSearch();return
        routes=[]
        for v in range(K):
            index=routing.Start(v);route=[]
            for _ in range(len(q)+2):
                route.append(manager.IndexToNode(index))
                if routing.IsEnd(index):break
                index=routing.NextVar(index).Value()
            else:raise ValueError('extraction cycle')
            if len(route)>2:routes.append(route)
        if time.monotonic_ns()<deadline_ns:
            publish(routes,sum(d[a][b] for r in routes for a,b in zip(r,r[1:])))
    routing.AddAtSolutionCallback(observe)
    routing.SolveWithParameters(params)

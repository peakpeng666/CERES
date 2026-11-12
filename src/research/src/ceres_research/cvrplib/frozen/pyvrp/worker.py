"""One reference-free PyVRP 0.14.0 call; no formal scheduling in this module."""
import sys, json, math, time, resource, dataclasses, hashlib
from collections import Counter
from importlib.metadata import version


def build_model(payload):
    from pyvrp import Model
    points, demands = payload['coordinates'], payload['demands']
    assert len(points) == len(demands) and demands[0] == 0
    assert all(type(v) is int and v >= 0 for v in demands)
    assert type(payload['K']) is int and payload['K'] > 0
    assert type(payload['capacity']) is int and payload['capacity'] > 0
    model = Model()
    locations = [model.add_location(float(x), float(y), name=f'node_{i}') for i, (x,y) in enumerate(points)]
    depot = model.add_depot(locations[0], service_duration=0, name='depot_0')
    for node in range(1, len(points)):
        model.add_client(locations[node], delivery=demands[node], pickup=0,
                         service_duration=0, prize=0, required=True, name=f'node_{node}')
    model.add_vehicle_type(num_available=payload['K'], capacity=payload['capacity'],
                           start_depot=depot, end_depot=depot, fixed_cost=0,
                           unit_distance_cost=1, unit_duration_cost=0,
                           initial_load=0, reload_depots=[], max_reloads=0,
                           max_overtime=0, unit_overtime_cost=0)
    matrix = []
    for i, (x, y) in enumerate(points):
        row = []
        for j, (u, v) in enumerate(points):
            value = math.floor(math.hypot(x-u, y-v) + 0.5)
            row.append(value)
            model.add_edge(locations[i], locations[j], distance=value, duration=0)
        matrix.append(row)
    data = model.data()
    location_to_node = {i:i for i in range(len(points))}
    assert data.num_locations == len(points) and data.num_clients == len(points)-1
    assert data.num_depots == 1 and data.num_vehicles == payload['K']
    assert data.num_shipments == 0
    assert all(data.client(c).location == c+1 for c in range(data.num_clients))
    assert data.distance_matrix(0).tolist() == matrix
    assert not data.duration_matrix(0).any()
    return data, matrix, location_to_node


def extract_routes(solution, data, location_to_node):
    native, routes = [], []
    for route in solution.routes():
        mapped, activities = [], []
        for activity in route:
            if activity.is_client():
                kind = 'CLIENT'
                location = data.client(activity.idx).location
            elif activity.is_depot():
                kind = 'DEPOT'
                location = data.depot(activity.idx).location
            else:
                raise ValueError('Unexpected shipment activity in CVRP')
            node = location_to_node[location]
            mapped.append(node)
            activities.append({'type':kind,'idx':activity.idx,'location':location,'node':node})
        assert mapped and mapped[0] == mapped[-1] == 0 and 0 not in mapped[1:-1]
        assert len(mapped) >= 3
        assert route.start_depot() == route.end_depot() == 0
        assert route.vehicle_type() == 0 and route.num_trips() == 1
        routes.append(mapped)
        native.append({'activities':activities,'distance':route.distance(),
                       'delivery':route.delivery(),'vehicle_type':route.vehicle_type(),
                       'start_depot':route.start_depot(),'end_depot':route.end_depot(),
                       'num_trips':route.num_trips(),'is_feasible':route.is_feasible()})
    return routes, native


def run(payload):
    assert set(payload) == {'coordinates','demands','capacity','K','seed','max_runtime_seconds'}
    assert type(payload['seed']) is int and payload['max_runtime_seconds'] > 0
    from pyvrp import solve, SolveParams
    from pyvrp.stop import MaxRuntime
    package_version = version('pyvrp')
    assert package_version == '0.14.0'
    denied = []
    def audit(event, args):
        if event == 'open' or event.startswith(('socket.','subprocess.','os.system','os.exec','os.spawn')):
            denied.append(event)
            raise PermissionError('External IO is forbidden during reference-free model/search')
    sys.addaudithook(audit)
    wall, cpu = time.perf_counter(), time.process_time()
    data, matrix, mapping = build_model(payload)
    built, build_cpu = time.perf_counter(), time.process_time()
    # New stop object per call. Upstream starts its timer at first callback.
    result = solve(data, stop=MaxRuntime(payload['max_runtime_seconds']),
                   seed=payload['seed'], collect_stats=True, display=False,
                   params=SolveParams(), initial_solution=None)
    solved, solve_cpu = time.perf_counter(), time.process_time()
    routes, native = extract_routes(result.best, data, mapping)
    cost = result.cost()
    cost = int(cost) if math.isfinite(cost) else None
    # This consistency check is intentionally not the independent final audit.
    internal_cost = sum(matrix[a][b] for r in routes for a,b in zip(r,r[1:]))
    if result.is_feasible():
        assert cost == internal_cost == result.best.distance()
    return {'solver':'PyVRP','version':package_version,'algorithm':'ILS',
            'seed':payload['seed'],'max_runtime_seconds':payload['max_runtime_seconds'],
            'solver_is_feasible':result.is_feasible(),'solver_is_complete':result.best.is_complete(),
            'reported_cost':cost,'reported_distance':result.best.distance(),
            'reported_duration_cost':result.best.duration_cost(),
            'reported_fixed_vehicle_cost':result.best.fixed_vehicle_cost(),
            'reported_num_routes':result.best.num_routes(),
            'model_matrix_sha256':hashlib.sha256(json.dumps(data.distance_matrix(0).tolist(),separators=(',',':')).encode()).hexdigest(),
            'routes':routes,'native_routes':native,'internal_route_cost':internal_cost,
            'num_iterations':result.num_iterations,'solver_loop_runtime':result.runtime,
            'build_wall_seconds_audit_only':built-wall,'build_cpu_seconds_audit_only':build_cpu-cpu,
            'solve_wall_seconds_audit_only':solved-built,'solve_cpu_seconds_audit_only':solve_cpu-build_cpu,
            'stats_runtime_per_iteration':result.stats.runtimes,
            'stats': [dataclasses.asdict(d) for d in result.stats],
            'denied_io_events':denied,'reference_or_history_payload_fields':[],
            'limit_semantics':'1s is ILS stop criterion, not full-process limit; 5s CPU and 10s external wall cap.'}

def configure_worker_limits():
    if not __debug__:raise RuntimeError('Assertions must be enabled')
    before={'AS_bytes':list(resource.getrlimit(resource.RLIMIT_AS)),
            'CPU_seconds':list(resource.getrlimit(resource.RLIMIT_CPU))}
    resource.setrlimit(resource.RLIMIT_CPU,(5,5))
    resource.setrlimit(resource.RLIMIT_AS,(1024*1024**2,1024*1024**2))
    after={'AS_bytes':list(resource.getrlimit(resource.RLIMIT_AS)),
           'CPU_seconds':list(resource.getrlimit(resource.RLIMIT_CPU))}
    return {'inherited':before,'effective':after}

if __name__ == '__main__':
    limits=configure_worker_limits()
    payload=json.load(sys.stdin)
    result=run(payload)
    result['resource_limits']=limits
    result['worker_maxrss_KiB_audit_only']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    json.dump(result,sys.stdout,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    sys.stdout.write('\n')

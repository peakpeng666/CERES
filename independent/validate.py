"""Independent CVRP validation. Standard library only; no solver imports."""
import argparse
from collections import Counter
from decimal import Decimal, localcontext, ROUND_FLOOR
import hashlib
import json
from pathlib import Path
import re


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_instance(path):
    headers, sections, section = {}, {}, None
    finished = False
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if finished:
            raise ValueError('content follows EOF')
        if line == 'EOF':
            finished = True
            continue
        if line.endswith('_SECTION'):
            if line not in {'NODE_COORD_SECTION', 'DEMAND_SECTION', 'DEPOT_SECTION'} or line in sections:
                raise ValueError('unsupported or repeated section')
            sections[line] = []
            section = line
        elif section:
            sections[section].append(line.split())
        else:
            k, sep, v = line.partition(':')
            k, v = k.strip(), v.strip()
            if not sep or (k in headers and k != 'COMMENT'):
                raise ValueError('invalid header')
            headers[k] = v
    if headers.get('TYPE') != 'CVRP' or headers.get('EDGE_WEIGHT_TYPE') != 'EUC_2D':
        raise ValueError('expected CVRP with rounded EUC_2D distances')
    n, capacity = int(headers['DIMENSION']), int(headers['CAPACITY'])
    if n < 2 or capacity <= 0:
        raise ValueError('invalid dimension/capacity')
    xy, demand = {}, {}
    for row in sections['NODE_COORD_SECTION']:
        if len(row) != 3 or int(row[0]) in xy:
            raise ValueError('invalid/repeated coordinate')
        xy[int(row[0])] = tuple(Decimal(x) for x in row[1:])
        if not all(x.is_finite() and abs(x) <= 10**9 for x in xy[int(row[0])]):
            raise ValueError('nonfinite/out-of-range coordinates')
    for row in sections['DEMAND_SECTION']:
        if len(row) != 2 or int(row[0]) in demand:
            raise ValueError('invalid/repeated demand')
        demand[int(row[0])] = int(row[1])
    depot_tokens = [int(x) for row in sections['DEPOT_SECTION'] for x in row]
    if len(depot_tokens) != 2 or depot_tokens[-1] != -1:
        raise ValueError('expected one depot and -1 terminator')
    depot = depot_tokens[0]
    if set(xy) != set(range(1, n + 1)) or set(demand) != set(xy) or depot not in xy:
        raise ValueError('invalid node ID coverage')
    if demand[depot] != 0 or any(d < 0 or d > capacity for d in demand.values()):
        raise ValueError('invalid demands')
    ids = [depot] + sorted(set(xy) - {depot})
    return {'name': headers['NAME'], 'coordinates': [xy[i] for i in ids],
            'demands': [demand[i] for i in ids], 'capacity': capacity, 'original_ids': ids}


def distance(a, b):
    with localcontext() as ctx:
        ctx.prec = 80
        return int((((a[0]-b[0])**2 + (a[1]-b[1])**2).sqrt() + Decimal('0.5')).to_integral_value(rounding=ROUND_FLOOR))


def check(p, routes, reported_cost, vehicle_limit):
    errors, loads, total, customers = [], [], 0, []
    n = len(p['demands'])
    if type(vehicle_limit) is not int or not 1 <= vehicle_limit <= n-1:
        raise ValueError('explicit vehicle limit required')
    if not isinstance(routes, list) or not routes:
        return {'valid': False, 'errors': ['missing routes'], 'cost': None}
    if len(routes) > vehicle_limit:
        errors.append('vehicle limit exceeded')
    for index, route in enumerate(routes):
        if not isinstance(route, list) or len(route) < 3 or any(type(v) is not int or not 0 <= v < n for v in route):
            errors.append(f'route {index}: invalid nodes'); continue
        if route[0] != 0 or route[-1] != 0 or 0 in route[1:-1]:
            errors.append(f'route {index}: invalid depot placement')
        customers.extend(route[1:-1])
        load = sum(p['demands'][v] for v in route[1:-1])
        loads.append(load)
        if load > p['capacity']:
            errors.append(f'route {index}: capacity exceeded')
        total += sum(distance(p['coordinates'][a], p['coordinates'][b]) for a,b in zip(route, route[1:]))
    if Counter(customers) != Counter(range(1, n)):
        errors.append('customers not served exactly once')
    if type(reported_cost) is not int or reported_cost != total:
        errors.append('declared cost differs from independently calculated integer cost')
    return {'valid': not errors, 'errors': errors, 'cost': total, 'route_count': len(routes), 'loads': loads}


def read_solution(path):
    routes, cost = [], None
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line: continue
        m = re.fullmatch(r'Route\s+#(\d+)\s*:\s*(.*)', line)
        if m and cost is None:
            if int(m[1]) != len(routes)+1: raise ValueError('route numbering')
            routes.append([0, *map(int, m[2].split()), 0])
        elif re.fullmatch(r'Cost\s+\d+(?:\.0+)?', line) and cost is None:
            cost = int(Decimal(line.split()[1]))
        else: raise ValueError('invalid solution line')
    if cost is None: raise ValueError('missing declared cost')
    return routes, cost


def check_result(p, result, vehicle_limit):
    """Failed runs remain records, not discarded observations."""
    schema_errors=[]
    if not isinstance(result,dict):
        result={}
        schema_errors.append('result must be an object')
    status = result.get('status')
    if not isinstance(status,str) or not status:
        status='INVALID_STATUS'
        schema_errors.append('status must be a nonempty string')
    if type(result.get('feasible')) is not bool:
        schema_errors.append('feasible must be a boolean')
    claimed = result.get('feasible') is True
    if not claimed and status in {'OK', 'TIMEOUT_WITH_FEASIBLE_INCUMBENT'}:
        schema_errors.append('feasible/status contradiction')
    validation = check(p, result.get('routes'), result.get('cost'), vehicle_limit)
    timing_errors = []
    if claimed:
        if status not in {'OK', 'TIMEOUT_WITH_FEASIBLE_INCUMBENT'}:
            timing_errors.append('feasible/status contradiction')
        stamps = [result.get(k) for k in ('start_monotonic_ns','incumbent_emitted_ns','incumbent_received_ns','deadline_monotonic_ns')]
        if any(type(t) is not int or t<0 for t in stamps) or stamps != sorted(stamps):
            timing_errors.append('missing/late incumbent timestamps')
        else:
            budget=result.get('budget_seconds')
            decimal_budget=Decimal(str(budget)) if type(budget) in (int,float) else None
            if decimal_budget is None or not decimal_budget.is_finite() or not 0<decimal_budget<=60:
                timing_errors.append('budget must be finite and in (0, 60] seconds')
            elif stamps[3]-stamps[0] != int(decimal_budget*10**9):
                timing_errors.append('budget/deadline mismatch')
        events=result.get('events',[])
        if not isinstance(events,list) or any(not isinstance(e,dict) for e in events):
            timing_errors.append('events must be a list of objects')
            events=[]
        def matches(e):
            routes=e.get('routes')
            strict_routes=isinstance(routes,list) and all(isinstance(route,list) and all(type(v) is int for v in route) for route in routes)
            return e.get('event')=='incumbent' and strict_routes and routes==result.get('routes') and type(e.get('cost')) is int and e['cost']==result.get('cost') and type(e.get('emitted_ns')) is int and e['emitted_ns']==stamps[1] and type(e.get('received_ns')) is int and e['received_ns']==stamps[2]
        if not any(matches(e) for e in events):
            timing_errors.append('matching incumbent event absent')
    return {'status':status, 'count_in_denominator':True, 'claimed_feasible':claimed,
            'eligible':claimed and validation['valid'] and not timing_errors and not schema_errors,
            'solution':validation, 'timing_errors':timing_errors, 'schema_errors':schema_errors}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instance'); parser.add_argument('solution')
    parser.add_argument('--vehicle-limit', type=int, required=True)
    args = parser.parse_args()
    p = read_instance(args.instance)
    if args.solution.endswith('.json'):
        report = check_result(p, json.loads(Path(args.solution).read_text()), args.vehicle_limit)
    else:
        routes, cost = read_solution(args.solution)
        report = check(p, routes, cost, args.vehicle_limit)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report.get('eligible', report.get('valid')) else 1)

"""Isolated worker. Imports, model construction and initialization are budgeted.

Run only through runner.py; stdin has sanitized numeric data and an absolute
monotonic deadline. No instance identifiers, files, reference targets or routes.
"""
import json
import math
import os
from pathlib import Path
import resource
import sys
import time


def emit(event, **fields):
    print(json.dumps({"event": event, "emitted_ns": time.monotonic_ns(), **fields},
                     separators=(",", ":"), allow_nan=False), flush=True)


def main():
    sys.dont_write_bytecode = True
    # This script is started with -I. All algorithm-specific imports occur below
    # and remain inside the supervisor's already-running budget.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from ceres_research.cvrplib.io import validate_payload
    envelope = json.load(sys.stdin)
    if set(envelope) != {"payload", "deadline_ns"} or type(envelope["deadline_ns"]) is not int:
        raise ValueError("invalid request envelope")
    p, deadline = envelope["payload"], envelope["deadline_ns"]
    validate_payload(p)
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    if not __debug__:
        raise ValueError("assertions must be enabled")
    for kind, desired in ((resource.RLIMIT_CPU, math.ceil(p["budget_seconds"])+2),
                          (resource.RLIMIT_AS, 1024*1024**2)):
        soft, hard = resource.getrlimit(kind)
        bound = desired if hard == resource.RLIM_INFINITY else min(hard, desired)
        resource.setrlimit(kind, (bound, bound))
    frozen = Path(__file__).with_name("frozen")
    best = None

    def check_deadline():
        if time.monotonic_ns() >= deadline:
            raise TimeoutError("end-to-end deadline")

    def publish(routes, cost):
        nonlocal best
        check_deadline()
        if best is None or cost < best:
            # Serialization and pipe delivery must also finish before cutoff;
            # the parent independently gates receipt time and validates routes.
            emit("incumbent", routes=[r[:] for r in routes if len(r)>2], cost=int(cost),
                 cpu_seconds=time.process_time())
            best = cost

    check_deadline()
    if p["algorithm"] == "pyvrp":
        from importlib.metadata import version
        from pyvrp import solve, SolveParams
        from pyvrp.IteratedLocalSearch import IteratedLocalSearchCallbacks
        if version("pyvrp") != "0.14.0":
            raise RuntimeError("requires exactly PyVRP 0.14.0")
        sys.path.insert(0, str(frozen / "pyvrp"))
        import worker as original_adapter
        build = original_adapter.build_model
        extract = original_adapter.extract_routes
    elif p["algorithm"] == "ortools":
        from ceres_research.cvrplib.ortools_adapter import solve as extra_solve
    elif p["algorithm"] in {"ga_random_keys", "pso_random_keys", "de_random_keys"}:
        from ceres_research.cvrplib.population_baselines import solve as extra_solve
    elif p["algorithm"] in {"random_cheapest", "random_regret2", "worst_cheapest", "worst_regret2"}:
        from ceres_research.cvrplib.lns_baselines import solve as extra_solve
    elif p["algorithm"] in {"nearest_neighbor", "seeded_sweep", "savings_vnd", "multistart_vnd"}:
        from ceres_research.cvrplib.extra_baselines import solve as extra_solve
    else:
        sys.path.insert(0, str(frozen / "vnd"))
        import capacity_solver as init
        import engine

    # The numerical libraries and restored sources are now loaded. Block Python
    # file/network/process IO during all model/init/search work. This is an
    # isolation guard for reviewed code, not a native syscall sandbox.
    def guard(event, args):
        if event in {"open", "os.fork", "os.forkpty", "os.posix_spawn"} or event.startswith(("socket.", "subprocess.", "os.system", "os.exec", "os.spawn")):
            raise PermissionError("external IO forbidden during model/init/search")
    sys.addaudithook(guard)
    check_deadline()
    emit("phase", name="imports_complete", cpu_seconds=time.process_time(),
         cpu_affinity=sorted(os.sched_getaffinity(0)))
    if p["algorithm"] in {"ortools", "nearest_neighbor", "seeded_sweep", "savings_vnd", "multistart_vnd", "random_cheapest", "random_regret2", "worst_cheapest", "worst_regret2", "ga_random_keys", "pso_random_keys", "de_random_keys"}:
        extra_solve(p, deadline, publish)
        emit("done", termination="adapter_returned", cpu_seconds=time.process_time())
    elif p["algorithm"] == "pyvrp":
        data, _, mapping = build(p)
        check_deadline()
        emit("phase", name="model_complete", cpu_seconds=time.process_time())

        class Observer(IteratedLocalSearchCallbacks):
            def save(self, solution):
                check_deadline()
                if solution.is_feasible():
                    routes, _ = extract(solution, data, mapping)
                    publish(routes, solution.distance())

            def on_start(self, ils):
                emit("phase", name="initialization_complete", cpu_seconds=time.process_time())
                self.save(ils.initial_solution)

            def on_best(self, best):
                self.save(best)

            def on_end(self, result):
                self.save(result.best)

        params = SolveParams()
        params.ils.callbacks = Observer()
        solve(data, stop=lambda cost: time.monotonic_ns() >= deadline,
              seed=p["seed"], collect_stats=False, display=False,
              params=params, initial_solution=None)
        emit("done", termination="deadline", cpu_seconds=time.process_time())
    else:
        # Reuse recovered savings/FFD/256-state capacity fallback and VND move
        # bodies. The adapter supplies integer edges to initialization as well
        # as search; continuous legacy scoring is deliberately not called.
        class ClockBudget(init.Budget):
            def check(self):
                self.checks += 1
                check_deadline()

        budget = ClockBudget(sys.maxsize, p["budget_seconds"], p["budget_seconds"])
        inst = init.instance_for(p)
        d = engine.matrix(p["coordinates"])
        check_deadline()
        emit("phase", name="model_complete", cpu_seconds=time.process_time())
        routes = init.savings(inst, d, budget)
        if len(routes) > inst.K:
            try:
                routes = init.ffd_routes(inst, d, budget)
            except init.InitFailure:
                routes = init.capacity_routes(inst, d, budget, {})
        routes = [[0, *r, 0] for r in routes]
        # Preserve legal empty slots for cross-route moves (<=K nonempty).
        routes += [[0, 0] for _ in range(inst.K-len(routes))]
        loads = [sum(p["demands"][v] for v in r[1:-1]) for r in routes]
        current = engine.cost(routes, d)
        emit("phase", name="initialization_complete", cpu_seconds=time.process_time())
        publish(routes, current)
        if p["algorithm"] != "savings":
            ops = engine.OPS[p["algorithm"]]
            opidx = attempted = accepted = 0
            # Only budget/checkpoint plumbing replaces archived search loop.
            # Candidate order, feasibility, strict-improvement and restart are
            # exactly the restored engine's functions. No fixed delta ceiling.
            while opidx < len(ops):
                op = ops[opidx]
                found = False
                check_deadline()
                for candidate in engine.candidates(routes, op):
                    check_deadline()
                    attempted += 1
                    feasible, delta = engine.evaluate(routes, loads, p["demands"], inst.capacity, d, op, candidate)
                    if feasible and delta < 0:
                        engine.apply(routes, loads, p["demands"], op, candidate)
                        current += delta
                        accepted += 1
                        publish(routes, current)
                        found = True
                        break
                opidx = 0 if found else opidx+1
            emit("done", termination="local_optimum", attempted=attempted, accepted=accepted,
                 cpu_seconds=time.process_time())
        else:
            emit("done", termination="construction_complete", cpu_seconds=time.process_time())


if __name__ == "__main__":
    try:
        main()
    except TimeoutError:
        emit("deadline", cpu_seconds=time.process_time())
    except Exception as exc:
        emit("error", error_type=type(exc).__name__, message=str(exc), cpu_seconds=time.process_time())
        sys.exit(2)

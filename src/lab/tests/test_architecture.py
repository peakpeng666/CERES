"""Anti-rot guards: keep the solver modules from silently growing back into monoliths.

Caps are set a little above the current refactored sizes; raising a cap is a deliberate
decision, not something that happens one feature at a time.
"""
import ast
from pathlib import Path

LAB = Path(__file__).resolve().parents[1] / 'ceres_lab'

CAPS = {  # module → max lines
    # 2026-10-18: ground-risk edges/detour corners + route ground stats raised this by
    # ~40 lines after extracting resolve_policy/check_route_ground/attach_route_ground.
    'solvers/fleet.py': 860,
    'solvers/fleet_graph.py': 250,
    'solvers/fleet_curvature.py': 250,
    'solvers/fleet_engines.py': 260,
    'solvers/fleet_constraints.py': 80,
    'solvers/fleet_common.py': 60,
    'scenarios/validate.py': 480,
    'scenarios/values.py': 50,
    'fieldworld.py': 560,   # 2026-10-18: torque-actuator plugin block + ground knowledge record
}

FUNCTION_CAPS = {  # (module, function) → max lines
    ('solvers/fleet.py', 'solve'): 230,   # +policy resolution & risk-graph wiring, helpers extracted
    ('solvers/fleet.py', 'check'): 180,
    ('solvers/fleet_engines.py', 'metaheuristic'): 100,
    ('solvers/fleet_engines.py', 'ortools_makespan'): 120,
}


def test_module_size_caps():
    for rel, cap in CAPS.items():
        path = LAB / rel
        assert path.is_file(), rel
        n = len(path.read_text(encoding='utf-8').splitlines())
        assert n <= cap, f'{rel} 已长到 {n} 行（上限 {cap}）：拆分或明确上调上限'


def test_function_size_caps():
    for (rel, fn), cap in FUNCTION_CAPS.items():
        tree = ast.parse((LAB / rel).read_text(encoding='utf-8'))
        sizes = {n.name: n.end_lineno - n.lineno for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert fn in sizes, f'{rel}:{fn} 不见了？'
        assert sizes[fn] <= cap, f'{rel}:{fn} 已长到 {sizes[fn]} 行（上限 {cap}）：提取子函数或明确上调上限'


def test_solver_layer_does_not_import_gateway():
    """Layering: solver modules must not depend on gateway/services state."""
    for path in (LAB / 'solvers').glob('*.py'):
        src = path.read_text(encoding='utf-8')
        for banned in ('from ..gateway', 'from ..services', 'import gateway', 'from ..store', 'from ..jobs'):
            assert banned not in src, f'{path.name} 引入了 {banned}（求解层不得依赖网关层）'

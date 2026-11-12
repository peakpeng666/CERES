"""New repair acceptance: failure semantics, duplicate claims and timing trust."""
import json
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from ceres_research.cvrplib.cli import reserve_run_key
from test_cvrplib import PAYLOAD, RealProcesses

class ExtraWorkerFailures(RealProcesses):
    # Reuse fault harness, not inherited tests (filtered below).
    def test_missing_error_type_is_api_error(self):
        r=self.fault('json.load(sys.stdin)\nprint(json.dumps({"event":"error","emitted_ns":time.monotonic_ns()}),flush=True)\n')
        self.assertEqual(r['status'],'API_ERROR')
    def test_memory_error_is_resource_limit(self):
        r=self.fault('json.load(sys.stdin)\nprint(json.dumps({"event":"error","emitted_ns":time.monotonic_ns(),"error_type":"MemoryError","message":"allocation failed"}),flush=True)\nsys.exit(2)\n')
        self.assertEqual(r['status'],'RESOURCE_LIMIT')
    def test_empty_success_is_no_solution_not_proof(self):
        r=self.fault('json.load(sys.stdin)\nprint(json.dumps({"event":"done","emitted_ns":time.monotonic_ns()}),flush=True)\n')
        self.assertEqual(r['status'],'NO_SOLUTION')
    def test_invalid_routes_not_problem_infeasibility(self):
        r=self.fault('json.load(sys.stdin)\nprint(json.dumps({"event":"incumbent","emitted_ns":time.monotonic_ns(),"routes":[[0,1,0]],"cost":10}),flush=True)\n')
        self.assertEqual(r['status'],'INVALID_SOLUTION')
    def test_actual_memory_allocation_failure(self):
        r=self.fault('import resource\njson.load(sys.stdin)\nresource.setrlimit(resource.RLIMIT_AS,(32*1024**2,32*1024**2))\ntry: x=bytearray(128*1024**2)\nexcept MemoryError: print(json.dumps({"event":"error","emitted_ns":time.monotonic_ns(),"error_type":"MemoryError","message":"allocation failed"}),flush=True)\n')
        self.assertEqual(r['status'],'RESOURCE_LIMIT')

class AtomicClaims(unittest.TestCase):
    def test_duplicate_run_key_across_outputs_and_threads(self):
        with tempfile.TemporaryDirectory() as d:
            def claim(i):
                try: return reserve_run_key(Path(d)/'registry', {'seed':0,'input':'fixed'},Path(d)/str(i))
                except FileExistsError: return None
            with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(claim,range(8)))
            self.assertEqual(sum(v is not None for v in results),1)
            self.assertEqual(len(list((Path(d)/'registry').glob('*.json'))),1)

# Avoid rerunning inherited baseline tests under a second class name.
def load_tests(loader, tests, pattern):
    suite=unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(AtomicClaims))
    for name in ExtraWorkerFailures.__dict__:
        if name.startswith('test_'): suite.addTest(ExtraWorkerFailures(name))
    return suite

"""Arithmetic, real process budgets, isolation and restoration regressions."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from ceres_research.cvrplib.audit import base_signature, load_frozen, validator
from ceres_research.cvrplib.io import contract_for, matrix, parse_vrp, parse_sol, validate_payload
from ceres_research.cvrplib.runner import run_payload, summarize

RESEARCH = Path(__file__).resolve().parents[1]
REPO = RESEARCH.parents[1]
VRP = """NAME : invented
TYPE : CVRP
DIMENSION : 3
EDGE_WEIGHT_TYPE : EUC_2D
CAPACITY : 2
NODE_COORD_SECTION
1 0 0
2 3 4
3 6 8
DEMAND_SECTION
1 0
2 1
3 1
DEPOT_SECTION
1
-1
EOF
"""
PAYLOAD = {"coordinates": [[0,0],[3,4],[6,8]], "demands": [0,1,1], "capacity": 2,
           "K": 1, "seed": 0, "budget_seconds": .2, "algorithm": "TSR"}


class InputContract(unittest.TestCase):
    def test_round_edges_not_total_or_bankers(self):
        p = {**PAYLOAD, "coordinates": [[0,0],[.5,0],[1,0]]}
        self.assertEqual(matrix(p['coordinates']), [[0,1,1],[1,0,1],[1,1,0]])
        self.assertEqual(validator.validate_solution(p, [[0,1,2,0]], 3)["cost"], 3)
        self.assertFalse(validator.validate_solution(p, [[0,1,2,0]], 2)["valid"])

    def test_parser_node_ids_and_depot_mapping(self):
        p = parse_vrp(VRP)
        self.assertEqual(p['dimension']-1, 2)
        moved = VRP.replace('1 0\n2 1', '1 1\n2 0').replace('DEPOT_SECTION\n1', 'DEPOT_SECTION\n2')
        self.assertEqual(parse_vrp(moved)['original_node_ids'], [2,1,3])

    def test_reject_malformed_vrp(self):
        variants = [VRP.replace('2 3 4','1 3 4'), VRP.replace('3 1\n','3 -1\n'),
            VRP.replace('1 0\n','1 1\n'), VRP.replace('-1\n','2\n-1\n'),
            VRP.replace('EUC_2D','GEO'), VRP.replace('2 3 4','2 nan 4'),
            VRP.replace('CAPACITY : 2','CAPACITY : 0'), VRP.replace('3 1\n','3 3\n'),
            VRP+'1 2 3', VRP.replace('TYPE : CVRP','TYPE : CVRP\nTYPE : CVRP'),
            VRP.replace('DIMENSION : 3','DIMENSION : 4'),
            VRP.replace('NODE_COORD_SECTION','VEHICLES : 1\nNODE_COORD_SECTION')]
        for text in variants:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_vrp(text)

    def test_sol_format(self):
        self.assertEqual(parse_sol('Route #1: 1 2\nCost 20\n'), ([[0,1,2,0]],20))
        for s in ['Route #2: 1 2\nCost 20', 'Route #1: 0 1\nCost 20', 'Route #1: 1 2', 'Cost nan']:
            with self.assertRaises(ValueError): parse_sol(s)

    def test_reference_and_bad_payload_blocked(self):
        for change in ({'reference_cost': 20}, {'routes': [[0,1,2,0]]}, {'seed': True},
                       {'seed': -1}, {'K': 0}, {'K': 3}, {'budget_seconds': float('inf')},
                       {'algorithm': 'unknown'}, {'demands': [0,True,1]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_payload({**PAYLOAD, **change})

    def test_independent_feasibility_and_exact_cost(self):
        for routes in ([[0,1,1,2,0]], [[0,1,0]], [[0,1,0,2,0]], [[0,True,2,0]],
                       [[0,1,0],[0,2,0]], [[0,3,1,0]], [[1,2,0]]):
            self.assertFalse(validator.validate_solution(PAYLOAD,routes,20)['valid'])
        self.assertFalse(validator.validate_solution({**PAYLOAD,'capacity':1},[[0,1,2,0]],20)['valid'])
        self.assertFalse(validator.validate_solution(PAYLOAD,[[0,1,2,0]],float('nan'))['valid'])

    def test_fleet_standard_x_and_capped_x_are_separate(self):
        c = {'source_family':'X','k_name_metadata':1,
             'contract':{'eligible':True,'id':'standard-X-unlimited','vehicle_limit':None}}
        self.assertEqual(contract_for(c,PAYLOAD)['K'], 2)
        self.assertEqual(contract_for(c,PAYLOAD,True), {'id':'derived-capped-X','K':1,'fixed_cost':0})
        c['contract']['eligible'] = False
        with self.assertRaises(ValueError): contract_for(c,PAYLOAD)

    def test_base_capacity_translation_rotation_scale_and_permutation(self):
        p = {'coordinates':[[0,0],[.5,0],[0,1]],'demands':[0,1,2],'capacity':2}
        q = {'coordinates':[[10,20],[8,20],[10,21]],'demands':[0,2,1],'capacity':3}
        self.assertEqual(base_signature(p),base_signature(q))
        q['demands']=[0,1,2]
        self.assertNotEqual(base_signature(p),base_signature(q))

    def test_source_files_equal_archived_hashes(self):
        manifest = json.loads((RESEARCH/'configs/cvrplib_sources.json').read_text())
        for row in manifest['files']:
            # The archived manifest retains its original paths and hashes.
            # Only the package prefix moved; frozen source bytes remain identical.
            path = row['path'].replace('src/research/src/urp_research/',
                                       'src/research/src/ceres_research/', 1)
            self.assertEqual(hashlib.sha256((REPO/path).read_bytes()).hexdigest(), row['sha256'])

    def test_denominators_include_failures_and_unstarted(self):
        r = summarize([{'status':'OK','feasible':True,'cost':20},
                       {'status':'CRASH','feasible':False,'cost':None}],3)
        self.assertEqual(r['feasible_rate_planned'],1/3)
        self.assertIsNone(r['all_planned_mean_cost'])
        self.assertEqual(r['not_started'],1)
        with self.assertRaises(ValueError): summarize([{}],0)

    def test_denominators_reject_forged_flags_costs_and_duplicate_attempts(self):
        for row in ({'status':'OK','feasible':'true','cost':20},
                    {'status':'CRASH','feasible':True,'cost':20},
                    {'status':'OK','feasible':True,'cost':float('nan')},
                    {'status':'OK','feasible':True,'cost':True},
                    {'status':'CRASH','feasible':False,'cost':20}):
            with self.subTest(row=row),self.assertRaises(ValueError):summarize([row],1)
        row={'status':'OK','feasible':True,'cost':20,'run_key':'same-scientific-attempt'}
        with self.assertRaises(ValueError):summarize([row,row],2)


class RealProcesses(unittest.TestCase):
    def fault(self, body, **fields):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'fault.py'
            path.write_text('import json,sys,time,os,signal\n'+body)
            # Schema/crash tests must actually reach their injected behavior.
            # A short deadline can expire during interpreter startup under load.
            result = run_payload({**PAYLOAD, "budget_seconds": 5, **fields}, worker_path=path)
        out = os.environ.get('CERES_CVRPLIB_TEST_LOG_DIR')
        if out:
            Path(out).mkdir(parents=True, exist_ok=True)
            (Path(out)/(self._testMethodName+'.json')).write_text(json.dumps(result,indent=2))
        return result

    def test_initialization_uses_budget(self):
        r = self.fault('req=json.load(sys.stdin)\nprint("INITIALIZATION_ENTERED",file=sys.stderr,flush=True)\ntime.sleep(5)\n', budget_seconds=1)
        self.assertIn('INITIALIZATION_ENTERED',r['raw_stderr'])
        self.assertEqual(r['status'],'TIMEOUT_NO_SOLUTION')
        self.assertTrue(r['reaped'])
        # Diagnostic local-platform guard only; scheduler latency is not a
        # correctness bound. Deadline gating is asserted separately.
        self.assertLess(r['process_wall_seconds'],2)

    def test_worker_not_reading_large_stdin_cannot_block_deadline(self):
        r = self.fault('time.sleep(5)\n',budget_seconds=.2,coordinates=[[0,0]]*5000,
                       demands=[0]+[1]*4999,K=4999)
        self.assertEqual(r['status'],'TIMEOUT_NO_SOLUTION')
        # Diagnostic local-platform guard only; scheduler latency is not a
        # correctness bound. Deadline gating is asserted separately.
        self.assertLess(r['process_wall_seconds'],2)

    def test_feasible_checkpoint_survives_hard_timeout(self):
        r = self.fault('req=json.load(sys.stdin)\nprint(json.dumps({"event":"incumbent","emitted_ns":time.monotonic_ns(),"routes":[[0,1,2,0]],"cost":20}),flush=True)\ntime.sleep(5)\n', budget_seconds=1)
        self.assertEqual(r['status'],'TIMEOUT_WITH_FEASIBLE_INCUMBENT')
        self.assertEqual(r['cost'],20)
        self.assertLessEqual(r['incumbent_received_ns'],r['deadline_monotonic_ns'])

    def test_late_timestamp_never_accepted(self):
        r = self.fault('req=json.load(sys.stdin)\nprint(json.dumps({"event":"incumbent","emitted_ns":req["deadline_ns"]+1,"routes":[[0,1,2,0]],"cost":20}),flush=True)\n')
        self.assertEqual(r['status'],'TIMEOUT_NO_SOLUTION')
        self.assertIsNone(r['cost'])

    def test_malformed_output_recorded(self):
        r = self.fault('json.load(sys.stdin)\nprint("not JSON",flush=True)\n')
        self.assertEqual(r['status'],'API_ERROR')
        self.assertIn('not JSON',r['raw_stdout'])

    def test_wrong_cost_rejected(self):
        r = self.fault('json.load(sys.stdin)\nprint(json.dumps({"event":"incumbent","emitted_ns":time.monotonic_ns(),"routes":[[0,1,2,0]],"cost":19}),flush=True)\n')
        self.assertEqual(r['status'],'NUMERIC_ERROR')
        self.assertIsNone(r['cost'])

    def test_crash_is_retained(self):
        r = self.fault('json.load(sys.stdin)\nos._exit(7)\n')
        self.assertEqual(r['status'],'CRASH')
        self.assertEqual(r['exitcode'],7)

    def test_descendant_killed_and_reaped(self):
        r = self.fault('json.load(sys.stdin)\npid=os.fork()\nif pid==0: time.sleep(10)\nelse:\n print(pid,file=sys.stderr,flush=True)\n time.sleep(10)\n', budget_seconds=1)
        pid=int(r['raw_stderr'].strip())
        self.assertFalse(Path('/proc',str(pid)).exists())
        self.assertIn(pid,[c['pid'] for c in r['reaped_descendants']])

    def test_inherited_low_memory_limit_does_not_attempt_raise(self):
        # The real worker honors a lower inherited hard limit; this regression
        # targets the archived r0 startup failure caused by raising RLIMIT_AS.
        r = self.fault('import resource,runpy\nresource.setrlimit(resource.RLIMIT_AS,(256*1024**2,256*1024**2))\nrunpy.run_path('+repr(str(RESEARCH/'src/ceres_research/cvrplib/worker.py'))+',run_name="__main__")\n',budget_seconds=1)
        self.assertEqual(r['status'],'OK',r)

    def test_vnd_moves_equal_archived_engine(self):
        p = {**PAYLOAD,'coordinates':[[0,0],[3,1],[0,3],[5,1],[2,5]],
             'demands':[0,1,1,1,1],'capacity':2,'K':2,'budget_seconds':1}
        base=run_payload({**p,'algorithm':'savings'})
        self.assertTrue(base['feasible'],base)
        engine=load_frozen('regression_engine','vnd/engine.py')
        for arm in ('T','TRS','TSR'):
            old=engine.search(p['coordinates'],p['demands'],p['capacity'],base['routes'],arm,100000)
            new=run_payload({**p,'algorithm':arm})
            self.assertEqual(new['cost'],old['cost'])
            self.assertEqual(new['routes'],[r for r in old['routes'] if len(r)>2])


if __name__ == '__main__':
    unittest.main()

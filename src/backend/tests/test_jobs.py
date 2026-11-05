"""Jobs integration and lifecycle tests; every input is explicitly synthetic."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from backend.jobs import JobManager, MAX_PENDING
from backend.server import make_server
from backend.store import Store, StoreError


def payload(algorithm='savings'):
    return {'coordinates': [[0,0],[1,0],[2,0]], 'demands': [0,1,1],
            'capacity': 2, 'K': 1, 'algorithm': algorithm,
            'seed': 0, 'budget_seconds': 1}


def wait_job(manager, job_id, timeout=15):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job['status'] in {'completed','failed','interrupted'}:
            return job
        time.sleep(.02)
    raise AssertionError('Job did not finish within the test deadline: '+job_id)


@unittest.skipUnless(sys.platform == 'linux', 'reviewed subprocess supervisor requires Linux')
class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'jobs.sqlite3')
        self.manager = JobManager(self.store)

    def tearDown(self):
        self.manager.close()
        self.tmp.cleanup()

    def test_actual_four_algorithm_jobs_use_runner_and_independent_checker(self):
        receipts = [self.manager.submit(payload(name)) for name in ('savings','T','TRS','TSR')]
        jobs = [wait_job(self.manager, item['id']) for item in receipts]
        self.assertEqual(len({item['id'] for item in jobs}), 4)
        self.assertEqual(len({item['input_sha256'] for item in jobs}), 1)
        self.assertEqual(len({item['payload_sha256'] for item in jobs}), 4)
        for job in jobs:
            with self.subTest(algorithm=job['request']['algorithm']):
                self.assertEqual(job['purpose'], 'engineering_validation')
                self.assertEqual(job['status'], 'completed', job['error'])
                result = job['result']
                self.assertEqual(result['schema'], 'urp-cvrplib-result-v1')
                self.assertTrue(result['feasible'], result)
                self.assertEqual(result['cost'], 4)
                self.assertEqual(sorted(n for route in result['routes'] for n in route[1:-1]), [1,2])
                self.assertTrue(all(check['valid'] for check in result['checkpoint_checks']))
                self.assertEqual(result['payload_sha256'], job['payload_sha256'])
                self.assertEqual(job['source_manifest']['sha256'], job['source_sha256'])
                self.assertLessEqual(result['incumbent_received_ns'], result['deadline_monotonic_ns'])
        for previous, current in zip(jobs, jobs[1:]):
            self.assertLessEqual(previous['finished_at'], current['started_at'])
        self.assertEqual(self.manager.list()['counts']['completed'], 4)
        self.assertEqual(self.store.list(), [])

    def test_bad_or_executable_input_is_never_accepted(self):
        bad = []
        for key in ('worker_path','python','script','reference','result','coordinates_path'):
            bad.append({**payload(), key:'not-accepted'})
        bad += [{**payload(),'budget_seconds':60}, {**payload(),'seed':True},
                {**payload(),'algorithm':'ortools'}, {**payload(),'algorithm':'ga_random_keys'},
                {**payload(),'algorithm':['savings']}, {**payload(),'coordinates':[[0,0],[True,1]]},
                {**payload(),'scene_revision':None}, {**payload(),'K':True},
                {**payload(),'demands':[0,1]},
                {**payload(),'coordinates':[[0,0]]*301,'demands':[0]*301}]
        for request in bad:
            with self.subTest(request=request), self.assertRaises(StoreError):
                self.manager.submit(request)
        self.assertEqual(self.manager.list()['total'], 0)

    def test_pyvrp_is_only_available_at_the_supported_installed_version(self):
        catalog = self.manager.algorithms()
        algorithm = next(row for row in catalog['algorithms'] if row['id']=='pyvrp')
        if algorithm['available']:
            self.assertEqual(algorithm['version'], '0.14.0')
            job = wait_job(self.manager, self.manager.submit(payload('pyvrp'))['id'])
            self.assertEqual(job['status'], 'completed', job['error'])
            self.assertTrue(job['result']['feasible'], job['result'])
            self.assertEqual(job['result']['cost'], 4)
            self.assertTrue(all(check['valid'] for check in job['result']['checkpoint_checks']))
        else:
            with self.assertRaises(StoreError) as raised:
                self.manager.submit(payload('pyvrp'))
            self.assertEqual(raised.exception.code, 'algorithm_unavailable')
            self.assertEqual(self.manager.list()['total'], 0)

    def test_actual_construction_failure_is_retained_without_infeasibility_claim(self):
        request = {**payload(), 'demands':[0,2,2], 'capacity':3}
        job = wait_job(self.manager, self.manager.submit(request)['id'])
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['result_status'], 'CONSTRUCTION_FAILURE')
        self.assertFalse(job['result']['feasible'])
        self.assertIsNone(job['result']['routes'])
        self.assertIsNone(job['cost'])
        self.assertEqual(self.manager.list()['counts']['failed'], 1)

    def test_scene_revision_conflict_is_atomic_and_new_revision_is_allowed(self):
        first = {**payload(), 'scene_id':'synthetic-map', 'scene_revision':1}
        self.manager.submit(first)
        changed = {**first, 'coordinates':[[0,0],[1,0],[3,0]]}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.manager.submit, changed) for _ in range(8)]
            for future in futures:
                with self.assertRaises(StoreError) as raised:
                    future.result()
                self.assertEqual(raised.exception.code, 'scene_revision_conflict')
        second = self.manager.submit({**changed, 'scene_revision':2})
        changed['coordinates'][2][0] = 999
        self.assertEqual(self.manager.get(second['id'])['request']['coordinates'][2][0], 3)
        self.assertEqual(self.manager.list()['total'], 2)

    def test_exceptions_and_no_solution_keep_separate_terminal_evidence(self):
        def broken(_):
            raise RuntimeError('injected unit-test failure; no solver result')
        with patch.object(self.manager, '_runner', broken):
            failure = wait_job(self.manager, self.manager.submit(payload())['id'])
        self.assertEqual(failure['status'], 'failed')
        self.assertEqual(failure['error']['code'], 'RUNNER_ERROR')
        self.assertIsNone(failure['result'])
        self.assertIsNone(failure['cost'])
        self.assertEqual(failure['request'], payload())
        def no_solution(p):
            return {'status':'TIMEOUT_NO_SOLUTION','payload_sha256':self.manager._digest(p),
                    'feasible':False,'routes':None,'cost':None}
        with patch.object(self.manager, '_runner', no_solution):
            absent = wait_job(self.manager, self.manager.submit(payload())['id'])
        self.assertEqual(absent['status'], 'completed')
        self.assertEqual(absent['result_status'], 'TIMEOUT_NO_SOLUTION')
        self.assertFalse(absent['feasible'])
        self.assertIsNone(absent['cost'])
        self.assertIsNone(absent['result']['routes'])
        self.assertEqual(self.manager.list()['counts'],
                         {'queued':0,'running':0,'completed':1,'failed':1,'interrupted':0})

    def test_malformed_runner_result_fails_only_its_attempt(self):
        for feasible in (None, 'false', 1):
            with self.subTest(feasible=feasible):
                def malformed(p):
                    result = {'status': 'NO_SOLUTION', 'payload_sha256': self.manager._digest(p)}
                    if feasible is not None:
                        result['feasible'] = feasible
                    return result
                with patch.object(self.manager, '_runner', malformed):
                    failed = wait_job(self.manager, self.manager.submit(payload())['id'])
                self.assertEqual(failed['status'], 'failed')
                self.assertEqual(failed['error']['code'], 'invalid_runner_result')
                self.assertIsNone(failed['feasible'])
                self.assertFalse(self.manager._stopping.is_set())
        recovered = wait_job(self.manager, self.manager.submit(payload())['id'])
        self.assertEqual(recovered['status'], 'completed')

    def test_result_status_must_agree_with_feasibility(self):
        for status, feasible, cost in (('OK', False, None), ('TIMEOUT_WITH_FEASIBLE_INCUMBENT', False, None),
                                       ('NO_SOLUTION', True, 4), ('TIMEOUT_NO_SOLUTION', True, 4)):
            with self.subTest(status=status):
                def malformed(p):
                    return {'status': status, 'payload_sha256': self.manager._digest(p),
                            'feasible': feasible, 'cost': cost}
                with patch.object(self.manager, '_runner', malformed):
                    failed = wait_job(self.manager, self.manager.submit(payload())['id'])
                self.assertEqual(failed['status'], 'failed')
                self.assertEqual(failed['error']['code'], 'invalid_runner_result')
                self.assertFalse(self.manager._stopping.is_set())

    def test_invalid_result_cost_does_not_kill_worker(self):
        for feasible, cost in ((True, {}), (True, 2**64), (True, True), (False, 0)):
            with self.subTest(feasible=feasible, cost=cost):
                def malformed(p):
                    return {'status': 'OK', 'payload_sha256': self.manager._digest(p),
                            'feasible': feasible, 'cost': cost}
                with patch.object(self.manager, '_runner', malformed):
                    failed = wait_job(self.manager, self.manager.submit(payload())['id'])
                self.assertEqual(failed['status'], 'failed')
                self.assertEqual(failed['error']['code'], 'invalid_runner_result')
                self.assertFalse(self.manager._stopping.is_set())

    def test_queue_limit_and_graceful_stop_preserve_every_accepted_attempt(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(p):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('test did not release worker')
            return {'status':'NO_SOLUTION','payload_sha256':self.manager._digest(p),
                    'feasible':False,'cost':None,'routes':None}
        try:
            with patch.object(self.manager, '_runner', blocked):
                first = self.manager.submit(payload())
                self.assertTrue(entered.wait(3))
                for _ in range(MAX_PENDING-1):
                    self.manager.submit(payload())
                with self.assertRaises(StoreError) as raised:
                    self.manager.submit(payload())
                self.assertEqual(raised.exception.code, 'job_queue_full')
                closing = threading.Thread(target=self.manager.close)
                closing.start()
                self.assertTrue(self.manager._stopping.wait(3))
                release.set()
                closing.join(5)
                self.assertFalse(closing.is_alive())
        finally:
            release.set()
        listing = self.manager.list(limit=500)
        self.assertEqual(listing['total'], MAX_PENDING)
        self.assertEqual(listing['counts']['completed'], 1)
        self.assertEqual(listing['counts']['interrupted'], MAX_PENDING-1)
        self.assertEqual(self.manager.get(first['id'])['status'], 'completed')

    def test_only_one_worker_can_own_a_database(self):
        with self.assertRaises(StoreError) as raised:
            JobManager(self.store)
        self.assertEqual(raised.exception.code, 'job_worker_active')

    def test_changed_source_rejects_submission_without_a_fake_attempt(self):
        with patch('backend.jobs._source_manifest', return_value={'sha256':'changed'}):
            with self.assertRaises(StoreError) as raised:
                self.manager.submit(payload())
        self.assertEqual(raised.exception.code, 'source_changed')
        self.assertEqual(self.manager.list()['total'], 0)

    def test_lost_receipt_does_not_leave_a_committed_job_asleep(self):
        with patch.object(self.manager,'get',side_effect=sqlite3.OperationalError('injected receipt read failure')):
            with self.assertRaises(sqlite3.OperationalError):
                self.manager.submit(payload())
        listing = self.manager.list()
        self.assertEqual(listing['total'],1)
        job = wait_job(self.manager,listing['jobs'][0]['id'])
        self.assertEqual(job['status'],'completed',job['error'])
        self.assertTrue(job['result']['feasible'])

    def test_hard_process_exit_recovers_committed_running_attempt_as_interrupted(self):
        crash_db = self.root/'crash.sqlite3'
        code = '''
import os, sys, time
from backend.store import Store
from backend.jobs import JobManager
manager = JobManager(Store(sys.argv[1]))
manager._runner = lambda _: os._exit(17)
manager.submit({"coordinates":[[0,0],[1,0]],"demands":[0,1],"capacity":1,
                "K":1,"algorithm":"savings","seed":0,"budget_seconds":1})
time.sleep(5)
'''
        outcome = subprocess.run([sys.executable, '-c', code, str(crash_db)],
                                 cwd=Path(__file__).resolve().parents[1],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(outcome.returncode, 17, outcome.stderr)
        restored = JobManager(Store(crash_db))
        try:
            listing = restored.list()
            self.assertEqual(listing['total'], 1)
            job = restored.get(listing['jobs'][0]['id'])
            self.assertEqual(job['status'], 'interrupted')
            self.assertEqual(job['error']['code'], 'SERVER_RESTARTED')
            self.assertIsNotNone(job['started_at'])
            self.assertIsNone(job['result'])
            self.assertEqual(job['request']['coordinates'], [[0,0],[1,0]])
            self.assertTrue(job['payload_sha256'])
        finally:
            restored.close()


@unittest.skipUnless(sys.platform == 'linux', 'reviewed subprocess supervisor requires Linux')
class JobHTTPTests(unittest.TestCase):
    def test_actual_http_submission_queries_and_pinned_input(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/'http.sqlite3')
            server = make_server(store, Path(directory), 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(method, path, body=None):
                conn = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                try:
                    conn.request(method,path,json.dumps(body) if body is not None else None,
                                 {'Content-Type':'application/json'})
                    response = conn.getresponse()
                    return response.status,json.loads(response.read())
                finally:
                    conn.close()
            try:
                self.assertTrue(request('GET','/api/health')[1]['executes_experiments'])
                self.assertEqual(request('GET','/api/algorithms')[1]['constraints']['max_nodes'],300)
                status, receipt = request('POST','/api/jobs',payload())
                self.assertEqual(status,202)
                wait_job(server.jobs,receipt['id'])
                status, job = request('GET','/api/jobs/'+receipt['id'])
                self.assertEqual(status,200)
                self.assertEqual(job['result']['cost'],4)
                self.assertEqual(request('GET','/api/jobs?limit=1&offset=0')[1]['total'],1)
                self.assertEqual(request('GET','/api/jobs?limit=0')[0],400)
                self.assertEqual(request('GET','/api/jobs/missing')[0],404)
                self.assertEqual(request('POST','/api/jobs',{**payload(),'worker_path':'/tmp/fake'})[0],400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()

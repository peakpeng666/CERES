"""Tests use local recovered JSON and read-only official maps; never ROS/Gazebo."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from historical_input import from_directory, from_archive, canonical_sha, checked_json, SELECTED
from recheck import build_record, historical_pair, public_receipt, EXPECTED
from resource_identity import compare
from urp_research.turn_modes.checker import check
from urp_lab.solvers import map_check
from urp_lab.simplan import to_sim_plan
from urp_lab.scenarios import maps
from urp_lab.store import Store

FIXTURES = Path(os.environ['URP_HISTORICAL_JSON']) if os.environ.get('URP_HISTORICAL_JSON') else None

class ReplayTests(unittest.TestCase):
    def setUp(self):
        if FIXTURES is None:self.skipTest('URP_HISTORICAL_JSON is required for data-backed tests')
        self.inputs = from_directory(FIXTURES)
    def build(self, **kw):
        args = dict(inputs=self.inputs, route_check=check, map_check=map_check, plan_fn=to_sim_plan,
                    identity_fn=lambda: {'sha256':'a'*64}, map_fingerprint=lambda: maps.fingerprint('warehouse'), adapter_sha='b'*64)
        args.update(kw); return build_record(**args)

    def test_real_checker_and_real_map_preserve_exact_historical_geometry_without_solver(self):
        with patch('urp_research.turn_modes.solver.solve', side_effect=AssertionError('optimizer called')), \
             patch('urp_research.turn_modes.oracle.solve', side_effect=AssertionError('optimizer called')):
            record, plan = self.build()
        old, oldsim = historical_pair(self.inputs)
        self.assertEqual(record['routes'], old['routes']); self.assertEqual(plan['path'], oldsim['planned']['path'])
        self.assertEqual(record['status'], 'feasible'); self.assertTrue(record['check']['valid'])
        self.assertTrue(record['map_check']['checked']); self.assertTrue(record['map_check']['collision_free'])
        self.assertFalse(record['process']['solver_executed'])
        self.assertEqual(plan['budget_s'], 649.6); self.assertEqual(len(plan['tasks']), 5)
        self.assertNotEqual(record['run_id'], old['run_id'])
        self.assertNotIn('raw_result', record); self.assertNotIn('evidence',record['process'])
        self.assertEqual(record['reproducibility']['execution_sha256'],'a'*64)
        self.assertIsNone(record['reproducibility']['worker_environment_verified'])
        self.assertEqual(record['replay']['expected_session_spawn']['yaw'],1.5708)

    def test_fresh_store_roundtrip(self):
        record,_=self.build()
        with tempfile.TemporaryDirectory() as tmp:
            db=Store(Path(tmp)/'lab.sqlite3')
            try:
                self.assertEqual(db.list_runs(),[]);db.put_run(record)
                self.assertEqual(db.get_run(record['run_id']), record)
                self.assertEqual(db.active_runs(),[])
            finally:db.close()

    def test_historical_route_mutation_rejected(self):
        self.inputs['evidence/native-tb4-warehouse/export.json']['routes'][0]['polyline'][0][0]+=1
        with self.assertRaisesRegex(ValueError,'historical_route_mismatch'):self.build()

    def test_historical_invalidated_identity_is_not_promoted(self):
        self.inputs['evidence/native-tb4-warehouse/export.json']['process']['source_identity_verified']=False
        with self.assertRaisesRegex(ValueError,'historical_identity_invalidated'):self.build()

    def test_raw_witness_mismatch_rejected(self):
        self.inputs['evidence/native-tb4-warehouse/export.json']['raw_result']['routes'][0]['cost']+=1
        with self.assertRaisesRegex(ValueError,'historical_witness_geometry_mismatch'):self.build()

    def test_current_checker_failure_rejected(self):
        with self.assertRaisesRegex(ValueError,'current_route_check_failed'):self.build(route_check=lambda *_:{'valid':False})

    def test_current_map_unchecked_rejected(self):
        with self.assertRaisesRegex(ValueError,'current_map_check_failed'):self.build(map_check=lambda *_:{'checked':False,'collision_free':True})

    def test_current_identity_change_rejected(self):
        versions=iter([{'sha256':'a'*64},{'sha256':'c'*64}])
        with self.assertRaisesRegex(ValueError,'current_execution_identity_changed'):self.build(identity_fn=lambda:next(versions))

    def test_current_plan_change_rejected(self):
        def changed(*args):
            p=to_sim_plan(*args);p['path'][0][0]+=1;return p
        with self.assertRaisesRegex(ValueError,'current_plan_differs'):self.build(plan_fn=changed)

    def test_budget_override_rejected(self):
        def changed(*args):
            p=to_sim_plan(*args);p['budget_s']=650;return p
        with self.assertRaisesRegex(ValueError,'unexpected_reconstructed_budget'):self.build(plan_fn=changed)

    def test_public_receipt_is_not_physics_success(self):
        record,_=self.build();result=public_receipt(record)
        self.assertEqual(result['state'],'VALIDATED_NOT_EXECUTED');self.assertEqual(result['simulation_state'],'NOT_RUN')
        self.assertIsNone(result['tasks_reached']);self.assertIsNone(result['resources_match_historical'])
        self.assertNotIn('/home/',json.dumps(result));self.assertNotIn('raw_result',result)

    def test_receipt_refuses_untrusted_hash_strings(self):
        record,_=self.build();record['replay']['current_map_fingerprint_sha256']='/PRIVATE_MARKER'
        with self.assertRaisesRegex(ValueError,'receipt_hash_invalid'): public_receipt(record)

    def test_receipt_refuses_nonfinite_budget(self):
        record,_=self.build();record['replay']['physical_budget_s']=float('nan')
        with self.assertRaisesRegex(ValueError,'receipt_budget_invalid'): public_receipt(record)

    def test_data_member_hash_gate(self):
        name=next(iter(SELECTED));raw=(FIXTURES/name).read_bytes()
        with self.assertRaisesRegex(ValueError,'historical_member_identity_mismatch'): checked_json(name,raw+b' ')

    def test_archive_size_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.zst';p.write_bytes(b'not archive')
            with self.assertRaisesRegex(ValueError,'archive_size_mismatch'):from_archive(p)

    def test_resource_relocation_and_byte_change(self):
        old=self.inputs['evidence/native-tb4-session/world-assets.json'];new=copy.deepcopy(old)
        new['files']={('/runner/temporary/fuel.gazebosim.org/'+k.split('/fuel.gazebosim.org/',1)[1] if '/fuel.gazebosim.org/' in k else k):v for k,v in new['files'].items()}
        result=compare(old,new);self.assertTrue(result['resources_match_historical']);self.assertEqual(result['historical_resource_count'],45)
        key=next(iter(new['files']));new['files'][key]='0'*64
        result=compare(old,new);self.assertFalse(result['resources_match_historical']);self.assertEqual(result['changed_resource_count'],1)

    def test_missing_resources_are_not_success(self):
        old=self.inputs['evidence/native-tb4-session/world-assets.json'];new=copy.deepcopy(old);new['complete']=False
        with self.assertRaisesRegex(ValueError,'resources_incomplete'): compare(old,new)

if __name__=='__main__':unittest.main()

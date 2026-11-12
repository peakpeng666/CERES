"""Failure accounting survives infrastructure errors and interrupted panels."""
import contextlib
import io
import json
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_cvrplib import VRP
from ceres_research.cvrplib import cli
from ceres_research.cvrplib.evaluate import evaluate_saved
from ceres_research.cvrplib.io import sha
from ceres_research.turn_modes import run as turn_run
from ceres_research.turn_modes.generate_data import fixtures


class InfrastructureLedger(unittest.TestCase):
    def test_runtime_error_is_persisted_and_remains_independently_scoreable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            instance=root/'invented.vrp';instance.write_text(VRP)
            reference=root/'invented.sol';reference.write_text('Route #1: 1 2\nCost 20\n')
            case={'case_id':'invented','instance_path':instance.name,'instance_sha256':sha(instance),
                  'contract':{'eligible':True,'id':'fixture-at-most-K','vehicle_limit':1},
                  'reference':{'path':reference.name,'sha256':sha(reference),'label':'fixture'}}
            manifest=root/'cases.json';manifest.write_text(json.dumps({'cases':[case]}))
            args=SimpleNamespace(cvrplib_command='run-one',instance=instance,manifest=manifest,
                algorithm='savings',seed=0,budget=1,capped_x=False,output=root/'run',run_registry=root/'registry')
            with patch.object(cli,'run_payload',side_effect=RuntimeError('supervisor unavailable')),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.run(args),1)
            saved=json.loads((args.output/'result.json').read_text())
            self.assertEqual(saved['status'],'INFRA_ERROR')
            self.assertEqual(saved['algorithm'],'savings')
            self.assertEqual(saved['seed'],0)
            self.assertEqual(len(saved['payload_sha256']),64)
            self.assertEqual(len(list(args.run_registry.glob('*.json'))),1)
            score=evaluate_saved(root,manifest,args.output/'result.json')
            self.assertFalse(score['eligible_for_gap'])
            args.output=root/'retry'
            self.assertEqual(cli.run(args),2)
            self.assertEqual(json.loads((args.output/'result.json').read_text())['status'],'DUPLICATE_RUN_KEY')


class TurnPanelLedger(unittest.TestCase):
    def setUp(self):
        self.alarm_handler=signal.getsignal(signal.SIGALRM)
        self.prof_handler=signal.getsignal(signal.SIGPROF)

    def tearDown(self):
        signal.alarm(0)
        signal.setitimer(signal.ITIMER_PROF,0)
        signal.signal(signal.SIGALRM,self.alarm_handler)
        signal.signal(signal.SIGPROF,self.prof_handler)

    def prepare(self,root,malformed=False):
        source=root/'inputs'/'data'/'fixtures';source.mkdir(parents=True)
        for index,instance in enumerate(fixtures()):
            path=source/(instance['id']+'.json')
            path.write_text('{invalid JSON' if malformed and index==0 else json.dumps(instance))
        return source.parents[1]

    def test_early_stop_keeps_all_planned_instances_in_denominator(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=self.prepare(root);output=root/'output'
            with patch.object(turn_run,'ROOT',source),patch.object(turn_run.time,'monotonic',side_effect=[0,1201,1202]),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError,'Incomplete frozen panel'):
                    turn_run.replay('fixtures',output)
            ledger=json.loads((output/'plan_accounting.json').read_text())
            self.assertEqual((ledger['planned'],ledger['attempted'],ledger['not_started']),(3,1,2))
            self.assertEqual(ledger['completed_rate_planned'],1/3)
            self.assertEqual(len(ledger['not_started_ids']),2)

    def test_malformed_input_consumes_its_planned_slot_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=self.prepare(root,malformed=True);output=root/'output'
            with patch.object(turn_run,'ROOT',source),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError,'Incomplete frozen panel'):
                    turn_run.replay('fixtures',output)
                before=(output/'plan_accounting.json').read_bytes()
                with self.assertRaisesRegex(RuntimeError,'empty reserved directory'):
                    turn_run.replay('fixtures',output)
            ledger=json.loads(before)
            self.assertEqual((ledger['planned'],ledger['attempted'],ledger['not_started']),(3,3,0))
            self.assertEqual(ledger['terminal_counts'],{'error':1,'completed':2})
            self.assertEqual(ledger['completed_rate_planned'],2/3)
            self.assertEqual((output/'plan_accounting.json').read_bytes(),before)


if __name__=='__main__':
    unittest.main()

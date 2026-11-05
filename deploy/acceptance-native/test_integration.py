"""Local-only wiring tests. No clone, package install, Fuel, ROS or CI calls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import yaml

ROOT=Path(__file__).resolve().parent

class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow=yaml.safe_load((ROOT/'.github/workflows/native-acceptance.yml.template').read_text())

    def test_jobs_are_serial_fresh_and_fixed_source(self):
        maze=self.workflow['jobs']['maze'];warehouse=self.workflow['jobs']['warehouse']
        self.assertEqual(warehouse['needs'],'maze')
        self.assertIn("needs.maze.result == 'success'",warehouse['if'])
        for stage,job in self.workflow['jobs'].items():
            self.assertEqual(job['runs-on'],'ubuntu-22.04')
            self.assertEqual(job['env']['TARGET_SHA'],'3acf840d1a0c16ab1cfcca15744e4ea6609ddb35')
            script=job['steps'][0]['run']
            self.assertIn('--stage '+stage,script)
            self.assertIn('materialize_checkout.py',script)
            self.assertNotIn('skip-install',script)
            self.assertNotIn('upload-artifact',script)
            self.assertNotIn('continue-on-error',json.dumps(job))
            self.assertIn('exit "$rc"',script)
            self.assertEqual(subprocess.run(['bash','-n'],input=script,text=True,capture_output=True).returncode,0)
        self.assertIn('/public_summary.py',maze['steps'][0]['run'])
        self.assertNotIn('/warehouse/',maze['steps'][0]['run'])
        self.assertIn('/warehouse/warehouse_public_summary.py',warehouse['steps'][0]['run'])

    def test_actual_supervisor_dispatches_each_entry_with_correct_cli_and_cwd(self):
        for stage in ('maze','warehouse'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=root/'source';source.mkdir();harness=root/'harness';harness.mkdir()
                runner=harness/'runner';runner.mkdir();shutil.copyfile(ROOT/'runner/run.py',runner/'run.py')
                (runner/'install.sh').write_text('#!/bin/bash\necho PRIVATE_INSTALL_MARKER\nexit 0\n')
                py=source/'src/lab/.venv/bin/python';py.parent.mkdir(parents=True);py.symlink_to(sys.executable)
                binpath=root/'bin';binpath.mkdir();xvfb=binpath/'Xvfb'
                xvfb.write_text('#!'+sys.executable+'\nimport os,sys,time\nos.write(int(sys.argv[sys.argv.index("-displayfd")+1]),b"99\\n")\ntime.sleep(300)\n');xvfb.chmod(0o755)
                selected=harness/('acceptance.py' if stage=='maze' else 'warehouse/warehouse_acceptance.py');selected.parent.mkdir(exist_ok=True)
                selected.write_text('import os,sys,json,pathlib\na=dict(zip(sys.argv[1::2],sys.argv[2::2]))\nassert a["--stage"]=='+repr(stage)+'\nassert pathlib.Path.cwd()==pathlib.Path(a["--source"])\nassert len(a["--harness-sha"])==40\nassert not pathlib.Path(a["--private-dir"]).exists()\npathlib.Path(a["--private-dir"]).mkdir()\npathlib.Path(a["--summary"]).write_text(json.dumps({"selected":'+repr(stage)+'}))\nprint("PRIVATE_HARNESS_MARKER")\n')
                wrong=harness/('warehouse/warehouse_acceptance.py' if stage=='maze' else 'acceptance.py');wrong.parent.mkdir(exist_ok=True);wrong.write_text('raise RuntimeError("WRONG_ENTRY")\n')
                env=dict(os.environ,PATH=str(binpath)+':'+os.environ['PATH'])
                private=root/'private'
                result=subprocess.run([sys.executable,str(runner/'run.py'),'--source',str(source),'--harness',str(harness),'--private-dir',str(private),'--stage',stage,'--harness-sha','a'*40],capture_output=True,text=True,timeout=15,env=env)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertNotIn('PRIVATE',result.stdout+result.stderr)
                self.assertEqual(json.loads((private/'summary.json').read_text()),{'selected':stage})
                status=json.loads((private/'runner-status.json').read_text())
                self.assertEqual(status['cleanup_remaining'],0);self.assertEqual(status['forced_kill_count'],0)

    def test_real_entry_and_projector_import_paths_from_foreign_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rel in ('acceptance.py','public_summary.py','warehouse/warehouse_acceptance.py','warehouse/warehouse_public_summary.py'):
                result=subprocess.run([sys.executable,str(ROOT/rel),'--help'],cwd=tmp,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,(rel,result.stderr))

    def test_actual_workflow_shell_requires_wrapper_and_reducer_success(self):
        # Stub external executables, not the workflow shell. Checks its actual
        # return semantics and stage/projector arguments without network access.
        for stage,job in self.workflow['jobs'].items():
            for wrapper_rc,reducer_rc in ((0,0),(1,0),(0,1)):
                with self.subTest(stage=stage,wrapper=wrapper_rc,reducer=reducer_rc),tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp);binpath=root/'bin';binpath.mkdir()
                    git=binpath/'git';git.write_text('#!/bin/bash\nif [[ "$*" == *"rev-parse HEAD"* ]]; then\n if [[ "$2" == */source ]]; then echo "$TARGET_SHA"; else echo "$HARNESS_SHA"; fi\nfi\n');git.chmod(0o755)
                    fake=binpath/'python3';fake.write_text('#!'+sys.executable+'\nimport os,sys,json\nwith open(os.environ["CALLS"],"a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\nname=sys.argv[1]\nif name.endswith("/run.py"):sys.exit(int(os.environ["WRAPPER_RC"]))\nif name.endswith("summary.py"):print("{\\"state\\":\\"SAFE\\"}");sys.exit(int(os.environ["REDUCER_RC"]))\n');fake.chmod(0o755)
                    env=dict(os.environ,PATH=str(binpath)+':'+os.environ['PATH'],RUNNER_TEMP=tmp,TARGET_SHA=job['env']['TARGET_SHA'],HARNESS_SHA='a'*40,CALLS=str(root/'calls'),WRAPPER_RC=str(wrapper_rc),REDUCER_RC=str(reducer_rc))
                    result=subprocess.run(['bash','-c',job['steps'][0]['run']],env=env,capture_output=True,text=True,timeout=10)
                    self.assertEqual(result.returncode==0,wrapper_rc==reducer_rc==0,result.stderr)
                    calls=[json.loads(x) for x in (root/'calls').read_text().splitlines()]
                    wrapper=next(x for x in calls if x[0].endswith('/run.py'))
                    self.assertEqual(wrapper[wrapper.index('--stage')+1],stage)
                    self.assertTrue(calls[-1][0].endswith('warehouse/warehouse_public_summary.py' if stage=='warehouse' else '/public_summary.py'))
                    self.assertEqual(result.stdout,'{"state":"SAFE"}\n');self.assertEqual(result.stderr,'')

    def test_warehouse_reducer_forced_kill_cannot_pass(self):
        sys.path.insert(0,str(ROOT/'warehouse'))
        from warehouse_public_summary import project
        from test_lifecycle import LifecycleTests
        good=LifecycleTests().good()
        self.assertEqual(project(good,{'exit_code':0,'cleanup_remaining':0,'forced_kill_count':0})['state'],'PASS')
        for forced in (None,1,False,"0"):
            row=project(good,{'exit_code':0,'cleanup_remaining':0,'forced_kill_count':forced})
            self.assertEqual(row['state'],'FAIL')
        self.assertEqual(project({}, {'failure_hint':'apt_libunwind_conflict'})['runner_failure_hint'],'apt_libunwind_conflict')

if __name__=='__main__':unittest.main()

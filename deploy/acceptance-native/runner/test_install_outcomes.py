"""Controlled subprocess fixtures only: no APT, ROS, Docker or network calls."""
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from runner.run import classify


class InstallOutcomeTests(unittest.TestCase):
    def test_retry_failure_labels_history_without_using_it_as_failure_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);shutil.copyfile(pathlib.Path(__file__).with_name('run.py'),root/'run.py')
            (root/'install.sh').write_text('''#!/bin/bash
                echo 'The following packages have unmet dependencies:' > "$2/apt-solver-before-repair.log"
                echo '{"status":"metadata_unavailable","dependencies":[],"metadata_phase":"metadata_project","metadata_error":"api_type","metadata_operation":"dependency_groups"}' > "$2/apt-dependencies-before-repair.json"
                echo 'The following packages have unmet dependencies:'
                echo 'URP_INSTALL_ATTEMPT_START 2'
                echo 'PRIVATE_UNKNOWN_ERROR'
                exit 7
                ''')
            result=subprocess.run([sys.executable,str(root/'run.py'),'--source',str(root),
                '--harness',str(root),'--private-dir',str(root/'private'),'--harness-sha','a'*40],
                capture_output=True,text=True,timeout=35)
            self.assertEqual(result.returncode,1)
            status=json.loads((root/'private/runner-status.json').read_text())
            self.assertEqual(status['failure_hint'],'unknown')
            self.assertEqual(status['apt_projection_scope'],'before_repair')
            self.assertEqual(status['cleanup_remaining'],0)
            from public_summary import project
            public=project({},status)
            self.assertEqual(public['apt_diagnostic_scope'],'before_repair')
            self.assertEqual(public['apt_metadata_operation'],'dependency_groups')
            self.assertEqual(public['runner_failure_hint'],'unknown')
            self.assertNotIn('PRIVATE',result.stdout+result.stderr+json.dumps(public))

    def test_retry_boundary_excludes_resolved_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=pathlib.Path(tmp)/'install.log'
            log.write_text('URP_INSTALL_ATTEMPT_START 1\nThe following packages have unmet dependencies:\n'
                           'URP_INSTALL_ATTEMPT_START 2\nGet:1 PRIVATE_URL PRIVATE_PACKAGE\n')
            self.assertEqual(classify(log,7),'unknown')
            with log.open('a') as stream:
                stream.write('E: Unable to locate package PRIVATE_PACKAGE\n')
            self.assertEqual(classify(log,100),'apt_package_missing')

    def test_timeout_precedes_stale_solver_and_keeps_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            original=pathlib.Path(__file__).with_name('run.py').read_text()
            # Test-only short deadline; production remains exactly 3600 seconds.
            self.assertIn("'install.log', time.monotonic()+3600)",original)
            (root/'run.py').write_text(original.replace("'install.log', time.monotonic()+3600)","'install.log', time.monotonic()+.5)"))
            (root/'install.sh').write_text('#!/bin/bash\n'
                'echo "The following packages have unmet dependencies:" > "$2/apt-solver.log"\n'
                'echo "URP_INSTALL_ATTEMPT_START 2"\n'
                'echo "Get:1 https://PRIVATE.invalid/PRIVATE PRIVATE_PACKAGE"\n'
                'echo "+URP_SETUP sudo apt-get install -y PRIVATE" > "$2/installation-trace.log"\n'
                'sleep 300 &\nwait\n')
            result=subprocess.run([sys.executable,str(root/'run.py'),'--source',str(root),
                '--harness',str(root),'--private-dir',str(root/'private'),'--harness-sha','a'*40],
                capture_output=True,text=True,timeout=35)
            self.assertEqual(result.returncode,1)
            status=json.loads((root/'private/runner-status.json').read_text())
            self.assertEqual(status['termination_reason'],'stage_timeout')
            self.assertEqual(status['failure_hint'],'stage_timeout')
            self.assertEqual(status['stage'],'installation')
            self.assertEqual(status['installation_step'],'apt_install')
            self.assertEqual(status['cleanup_remaining'],0)
            self.assertEqual(status['forced_kill_count'],0)
            self.assertNotIn('PRIVATE',result.stdout+result.stderr)
            self.assertNotEqual(status['exit_code'],0)

    def test_real_wrapper_preserves_but_retires_pre_repair_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);source=root/'source';private=root/'private';bin_dir=root/'bin'
            (source/'setup/ubuntu').mkdir(parents=True);private.mkdir();bin_dir.mkdir()
            stubs={
                'git':'echo 3acf840d1a0c16ab1cfcca15744e4ea6609ddb35',
                'python3':'''case "$1" in
                    */apt_diagnostic.py)
                        echo "The following packages have unmet dependencies:" > "$3/apt-solver.log"
                        echo '{"status":"metadata_unavailable","dependencies":[]}' > "$3/apt-dependencies.json" ;;
                    esac
                    exit 0''',
            }
            for command,body in stubs.items():
                stub=bin_dir/command;stub.write_text('#!/bin/bash\n'+body+'\n');stub.chmod(0o755)
            (source/'setup/ubuntu/setup.sh').write_text('''if [[ ! -e "$HOME/attempted" ]]; then
                    touch "$HOME/attempted"
                    echo "The following packages have unmet dependencies:"
                    exit 100
                fi
                echo 'Get:1 PRIVATE_SOURCE PRIVATE_PACKAGE'
                exit 7
                ''')
            result=subprocess.run(['bash',str(pathlib.Path(__file__).with_name('install.sh').resolve()),
                str(source),str(private)],env=dict(os.environ,PATH=str(bin_dir)+':'+os.environ['PATH'],
                URP_HOST_APT_REPAIR='1'),capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,7)
            (private/'install.log').write_text(result.stdout)
            self.assertEqual(classify(private/'install.log',7),'unknown')
            self.assertFalse((private/'apt-solver.log').exists())
            self.assertFalse((private/'apt-dependencies.json').exists())
            self.assertTrue((private/'apt-solver-before-repair.log').is_file())
            self.assertTrue((private/'apt-dependencies-before-repair.json').is_file())

    def test_public_outcomes_are_enums_and_never_make_missing_physics_pass(self):
        from public_summary import project
        for cause in ('stage_timeout','cancelled','supervisor_error','child_exit','completed'):
            result=project({'state':'PASS'},{'termination_reason':cause,'failure_hint':cause,
                'exit_code':1,'cleanup_remaining':0,'forced_kill_count':0})
            self.assertEqual(result['runner_termination_reason'],cause)
            self.assertEqual(result['state'],'FAIL')
            self.assertIsNone(result['actual_length_m'])
        result=project({}, {'termination_reason':'PRIVATE_SECRET','failure_hint':'PRIVATE_SECRET'})
        self.assertEqual(result['runner_termination_reason'],'unknown')
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_retry_boundary_is_exact_and_fresh_solver_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=pathlib.Path(tmp)/'log'
            log.write_text('The following packages have unmet dependencies:\n'
                           'URP_INSTALL_ATTEMPT_START 2 PRIVATE_SUFFIX\n')
            self.assertEqual(classify(log,100),'apt_dependency_conflict')
            log.write_text('URP_INSTALL_ATTEMPT_START 2\n'
                           'The following packages have unmet dependencies:\n')
            self.assertEqual(classify(log,100),'apt_dependency_conflict')


if __name__=='__main__':unittest.main()

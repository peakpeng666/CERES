import json, os, pathlib, shutil, signal, subprocess, sys, tempfile, time, unittest

class RunnerTests(unittest.TestCase):
    def test_failed_install_cleans_owned_child_and_keeps_output_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp); script=root/'run.py'
            shutil.copyfile(pathlib.Path(__file__).with_name('run.py'),script)
            (root/'install.sh').write_text('#!/bin/bash\necho PRIVATE_TEST_MARKER\nsleep 300 &\nexit 7\n')
            result=subprocess.run([sys.executable,str(script),'--source',str(root),'--harness',str(root),'--private-dir',str(root/'private'),'--harness-sha','a'*40],capture_output=True,text=True,timeout=35)
            self.assertEqual(result.returncode,1)
            self.assertEqual(json.loads(result.stdout),{'stage':'installation','state':'running'});self.assertEqual(result.stderr,'')
            status=json.loads((root/'private/runner-status.json').read_text())
            self.assertEqual(status,{'stage':'installation','exit_code':7,'cleanup_remaining':0,'forced_kill_count':0,'failure_hint':'unknown','installation_step':'unknown','apt_projection':{'status':'not_run','dependencies':[]},'registry_diagnostic':{},'host_apt_repair':{},'termination_reason':'child_exit','apt_projection_scope':'not_run'})
            self.assertIn('PRIVATE_TEST_MARKER',(root/'private/install.log').read_text())

    def test_successful_task_needing_forced_cleanup_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);script=root/'run.py'
            original=pathlib.Path(__file__).with_name('run.py').read_text()
            # Test-only shortened TERM grace; production keeps twenty seconds.
            self.assertIn('(signal.SIGTERM,20)',original)
            script.write_text(original.replace('(signal.SIGTERM,20)','(signal.SIGTERM,.3)'))
            (root/'install.sh').write_text('#!/bin/bash\nexit 0\n')
            (root/'src/lab/.venv/bin').mkdir(parents=True)
            (root/'src/lab/.venv/bin/python').symlink_to(sys.executable)
            xvfb=root/'Xvfb'
            xvfb.write_text('#!'+sys.executable+'\nimport os,sys,time\nos.write(int(sys.argv[sys.argv.index("-displayfd")+1]),b"99\\n")\ntime.sleep(300)\n')
            xvfb.chmod(0o755)
            child='import signal,pathlib,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path("ready").touch();time.sleep(300)'
            (root/'acceptance.py').write_text('import subprocess,sys,pathlib,time\nsubprocess.Popen([sys.executable,"-c",'+repr(child)+'])\nwhile not pathlib.Path("ready").exists():time.sleep(.01)\n')
            env=dict(os.environ,PATH=str(root)+':'+os.environ['PATH'])
            result=subprocess.run([sys.executable,str(script),'--source',str(root),'--harness',str(root),'--private-dir',str(root/'private'),'--harness-sha','a'*40],capture_output=True,text=True,timeout=15,env=env)
            self.assertEqual(result.returncode,1)
            self.assertEqual(result.stderr,'')
            status=json.loads((root/'private/runner-status.json').read_text())
            self.assertEqual(status['stage'],'simulation')
            self.assertEqual(status['forced_kill_count'],1)
            self.assertEqual(status['cleanup_remaining'],0)
            self.assertNotEqual(status['exit_code'],0)
            # A normal Xvfb SIGTERM alone does not invalidate a successful task.
            (root/'acceptance.py').write_text('pass\n')
            result=subprocess.run([sys.executable,str(script),'--source',str(root),'--harness',str(root),'--private-dir',str(root/'normal-private'),'--harness-sha','a'*40],capture_output=True,text=True,timeout=15,env=env)
            self.assertEqual(result.returncode,0)
            normal=json.loads((root/'normal-private/runner-status.json').read_text())
            self.assertEqual(normal['forced_kill_count'],0)
            self.assertEqual(normal['cleanup_remaining'],0)

    def test_cancel_during_install_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp); script=root/'run.py'
            shutil.copyfile(pathlib.Path(__file__).with_name('run.py'),script)
            (root/'install.sh').write_text('#!/bin/bash\ntouch "$2/started"\nsleep 300\nexit 0\n')
            process=subprocess.Popen([sys.executable,str(script),'--source',str(root),'--harness',str(root),'--private-dir',str(root/'private'),'--harness-sha','a'*40],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            deadline=time.monotonic()+10
            while not (root/'private/started').exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue((root/'private/started').exists())
            process.send_signal(signal.SIGTERM)
            stdout,stderr=process.communicate(timeout=35)
            self.assertNotEqual(process.returncode,0)
            status=json.loads((root/'private/runner-status.json').read_text())
            self.assertNotEqual(status['exit_code'],0)
            self.assertEqual(status['cleanup_remaining'],0)
            self.assertEqual(stderr,'')
            self.assertEqual(status['termination_reason'],'cancelled')
            self.assertEqual(status['failure_hint'],'cancelled')

    def test_bounded_failure_hints(self):
        from runner.run import classify, LIMIT
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'private.log'
            cases={'source_identity':'tracked_blob_mismatch','registry_identity':'npm_mirror_identity_mismatch','package_install':'dpkg: error','dependency_fetch':'Failed to download','network':'Could not resolve host','disk_full':'No space left on device','native_build':'CMake Error','unknown':'other error'}
            for hint, marker in cases.items():
                path.write_text(marker+' PRIVATE_PACKAGE_AND_PATH')
                self.assertEqual(classify(path,1),hint)
                self.assertEqual(classify(path,0),'none')
            path.write_bytes(b'No space left on device'+b'x'*LIMIT)
            self.assertEqual(classify(path,1),'unknown')
            self.assertEqual(classify(pathlib.Path(tmp)/'absent',1),'unknown')

    def test_apt_hints_and_trace_steps(self):
        from runner.run import classify, installation_step
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'log'
            cases={'apt_dependency_conflict':'The following packages have unmet dependencies:', 'apt_libunwind_conflict':'libunwind-14-dev : Conflicts: libunwind-dev but it is to be installed', 'apt_signature':'NO_PUBKEY PRIVATE_KEY_ID','apt_source_conflict':'Conflicting values set for option Signed-By PRIVATE_URL','apt_release':'does not have a Release file','apt_hook':'Problem executing scripts APT::Update::Post-Invoke','apt_lock':'Could not get lock PRIVATE_PATH','apt_package_missing':'E: Unable to locate package PRIVATE_NAME'}
            for expected,text in cases.items():
                p.write_text('+URP_SETUP sudo apt-get update\n'+text)
                self.assertEqual(classify(p,100),expected)
                self.assertEqual(installation_step(p),'apt_update')
            p.write_text('+URP_SETUP sudo apt-get update\n+URP_SETUP sudo DEBIAN_FRONTEND=noninteractive apt-get install -y PRIVATE_PACKAGE\n')
            self.assertEqual(installation_step(p),'apt_install')
            p.write_text('libunwind-14-dev libunwind-dev\nUnable to correct problems, you have held broken packages.')
            self.assertEqual(classify(p,100),'apt_dependency_conflict')
            p.write_text('Broken libunwind-14-dev:amd64 Conflicts on libunwind-dev:amd64')
            self.assertEqual(classify(p,100),'apt_libunwind_conflict')
            p.write_text('+URP_SETUP sudo apt-get update\n+URP_SETUP uv sync --locked\n')
            self.assertEqual(installation_step(p),'other')

if __name__=='__main__':unittest.main()

class RuntimeProfileTests(unittest.TestCase):
    def test_matches_original_native_entrypoint(self):
        from runner.run import select_runtime_profile
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);env={}
            select_runtime_profile(root,env)
            self.assertNotIn('URP_SIM_PROFILE',env)
            profile=root/'src/lab/runtime/native/bridge/profile.json'
            profile.parent.mkdir(parents=True);profile.write_text('{}')
            select_runtime_profile(root,env)
            self.assertEqual(env['URP_SIM_PROFILE'],'image')

class InstallationProgressTests(unittest.TestCase):
    def test_fixed_phase_reducer_and_private_output(self):
        from runner.run import installation_progress, installation_step, INSTALL_PHASES
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'trace'
            cases={
                'source_identity':'URP_INSTALL_PHASE source_identity',
                'registry_check':'URP_INSTALL_PHASE registry_check',
                'setup':'URP_INSTALL_PHASE setup',
                'apt_update':'+URP_SETUP sudo apt-get update',
                'apt_install':'+URP_SETUP sudo apt-get install -y PRIVATE_PACKAGE',
                'uv':'++URP_SETUP uv sync --locked',
                'node':'+URP_SETUP curl https://nodejs.org/dist/PRIVATE_ARCHIVE',
                'npm':'+URP_SETUP npm ci',
                'native':'+URP_SETUP bash src/lab/ros/native/install-ros-extras.sh',
                'runtime_check':'+URP_SETUP python3 deploy/check-runtime.py --output PRIVATE_PATH',
                'apt_diagnostic':'URP_INSTALL_PHASE apt_diagnostic',
                'apt_repair':'URP_INSTALL_PHASE apt_repair',
                'complete':'URP_INSTALL_PHASE complete',
            }
            for phase,line in cases.items():
                p.write_text(line+'\nPRIVATE_MESSAGE\nURP_INSTALL_PHASE PRIVATE_SECRET\n')
                self.assertEqual(installation_progress(p),phase)
                self.assertIn(installation_progress(p),INSTALL_PHASES)
            p.write_text('URP_INSTALL_PHASE registry_check\n')
            self.assertEqual(installation_step(p),'registry_check')
            link=p.with_name('link');link.symlink_to(p)
            self.assertEqual(installation_progress(link),'unknown')
            self.assertEqual(installation_progress(p.with_name('missing')),'unknown')
            p.write_text('URP_INSTALL_PHASE complete PRIVATE_SUFFIX\n')
            self.assertEqual(installation_progress(p),'unknown')

    def test_real_wrapper_separates_trace_from_large_package_output(self):
        from runner.run import installation_progress, LIMIT
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);source=root/'source';private=root/'private';bin_dir=root/'bin'
            (source/'setup/ubuntu').mkdir(parents=True);private.mkdir();bin_dir.mkdir()
            for command,body in {'git':'echo 3acf840d1a0c16ab1cfcca15744e4ea6609ddb35', 'python3':'exit 0'}.items():
                stub=bin_dir/command;stub.write_text('#!/bin/bash\n'+body+'\n');stub.chmod(0o755)
            # Fake installer only: no apt, network, dependencies or actual source changes.
            (source/'setup/ubuntu/setup.sh').write_text("uv() { head -c 131072 /dev/zero; }; uv sync --locked; exit 7\n")
            result=subprocess.run(['bash',str(pathlib.Path(__file__).with_name('install.sh').resolve()),str(source),str(private)],env=dict(os.environ,PATH=str(bin_dir)+':'+os.environ['PATH']),capture_output=True,timeout=10)
            self.assertEqual(result.returncode,7)
            self.assertGreater(len(result.stdout),LIMIT)
            self.assertNotIn(b'URP_SETUP',result.stdout+result.stderr)
            trace=private/'installation-trace.log'
            self.assertIn('URP_INSTALL_PHASE registry_check',trace.read_text())
            self.assertEqual(installation_progress(trace),'uv')

    def test_live_progress_reports_phase_without_raw_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);script=root/'run.py'
            original=pathlib.Path(__file__).with_name('run.py').read_text()
            script.write_text(original.replace('time.monotonic()+120','time.monotonic()+.1'))
            (root/'install.sh').write_text('#!/bin/bash\nprintf "URP_INSTALL_PHASE registry_check\\nPRIVATE_MARKER\\n" > "$2/installation-trace.log"\nsleep 1.5\nexit 7\n')
            result=subprocess.run([sys.executable,str(script),'--source',str(root),'--harness',str(root),'--private-dir',str(root/'private'),'--harness-sha','a'*40],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,1)
            self.assertNotIn('PRIVATE_MARKER',result.stdout+result.stderr)
            rows=[json.loads(line) for line in result.stdout.splitlines()]
            self.assertTrue(any(row.get('installation_substage')=='registry_check' for row in rows))
            for row in rows[1:]:
                self.assertIn(row['installation_substage'],('unknown','registry_check'))
                self.assertIsInstance(row['installation_substage_observed_seconds'],int)
            status=json.loads((root/'private/runner-status.json').read_text())
            self.assertEqual(status['installation_step'],'registry_check')

class AptOutputObservationTests(unittest.TestCase):
    def test_operation_trace_disambiguates_update_from_install(self):
        from runner.run import installation_progress
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'trace'
            p.write_text('+URP_SETUP sudo apt-get update\n')
            self.assertEqual(installation_progress(p),'apt_update')
            with p.open('a') as stream:stream.write('+URP_SETUP sudo DEBIAN_FRONTEND=noninteractive apt-get install -y PRIVATE\n')
            self.assertEqual(installation_progress(p),'apt_install')

    def test_counts_only_new_output_and_never_returns_private_text(self):
        from runner.run import AptOutputObservation
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'log';p.write_text('Get:1 PRIVATE_OLD_SOURCE\nUnpacking PRIVATE_OLD_PACKAGE\n')
            observation=AptOutputObservation(p,10)
            self.assertEqual(observation.public(12)['apt_output_last_observed_event'],'unknown')
            self.assertIsNone(observation.public(12)['apt_output_seconds_since_observed_activity'])
            with p.open('a') as stream:stream.write('Get:2 PRIVATE_SOURCE\nUnpacking PRIVATE_PACKAGE\nSetting up PRIVATE_PACKAGE\nProcessing triggers for PRIVATE_PACKAGE\n')
            observation.sample(15);output=observation.public(20)
            self.assertEqual(output['apt_output_last_observed_event'],'triggers')
            self.assertEqual(output['apt_output_observed_event_counts']['download'],1)
            self.assertEqual(output['apt_output_observed_event_counts']['unpack'],1)
            self.assertEqual(output['apt_output_observed_event_counts']['configure'],1)
            self.assertEqual(output['apt_output_seconds_since_observed_activity'],5)
            self.assertNotIn('PRIVATE',json.dumps(output))
            observation.sample(25)
            self.assertEqual(observation.public(25)['apt_output_observed_event_counts'],output['apt_output_observed_event_counts'])
            # A new operation cannot reuse preceding operation's output evidence.
            following=AptOutputObservation(p,30)
            self.assertEqual(following.public(31)['apt_output_last_observed_event'],'unknown')

    def test_incremental_partial_lines_and_silence(self):
        from runner.run import AptOutputObservation
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'log';p.touch();observation=AptOutputObservation(p,0)
            with p.open('a') as stream:stream.write('Get:1 PRIVATE')
            observation.sample(1);observation.sample(5)
            self.assertEqual(observation.public(5)['apt_output_seconds_since_observed_activity'],4)
            self.assertEqual(observation.public(5)['apt_output_observed_event_counts']['download'],0)
            with p.open('a') as stream:stream.write('\nReading package lists...\n')
            observation.sample(6)
            self.assertEqual(observation.public(7)['apt_output_observed_event_counts']['download'],1)
            self.assertEqual(observation.public(7)['apt_output_last_observed_event'],'reading_lists')

    def test_bounded_truncation_unknown_and_symlinks(self):
        from runner.run import AptOutputObservation,LIMIT
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'log';p.touch();observation=AptOutputObservation(p,0)
            p.write_bytes(b'Get:1 PRIVATE\n'+b'x'*(LIMIT+10)+b'\nIgn:2 PRIVATE\nErr:3 PRIVATE\n')
            observation.sample(2);output=observation.public(3)
            self.assertTrue(output['apt_output_observation_truncated'])
            self.assertEqual(output['apt_output_observed_event_counts']['download'],0)
            self.assertEqual(output['apt_output_last_observed_event'],'index_error')
            self.assertEqual(output['apt_output_observed_event_counts']['index_ignored'],1)
            p.write_text('replacement\n');observation.sample(4)
            self.assertEqual(observation.public(4)['apt_output_last_observed_event'],'unknown')
            link=p.with_name('link');link.symlink_to(p)
            linked=AptOutputObservation(link,0);linked.sample(1)
            self.assertEqual(linked.public(1)['apt_output_last_observed_event'],'unknown')


class InstallationBoundaryTests(unittest.TestCase):
    def test_same_phase_retry_has_distinct_private_boundary(self):
        from runner.run import installation_trace_state
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'trace'
            line='+URP_SETUP sudo apt-get update\n'
            p.write_text(line)
            first=installation_trace_state(p)
            with p.open('a') as stream:stream.write(line)
            second=installation_trace_state(p)
            self.assertEqual(first[0],second[0])
            self.assertNotEqual(first[1],second[1])
            self.assertEqual(installation_trace_state(p),second)

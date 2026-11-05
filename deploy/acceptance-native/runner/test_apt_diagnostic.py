import pathlib, tempfile, unittest
from unittest.mock import patch, Mock
from runner.apt_diagnostic import TARGET, BASE, solver_command, diagnose

class AptDiagnosticTests(unittest.TestCase):
    def test_fixed_manifest_command_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);(root/'deploy').mkdir()
            raw=b'# pinned manifest\nlibunwind-dev\ng++\n'
            (root/'deploy/ros-packages.txt').write_bytes(raw)
            with patch('runner.apt_diagnostic.subprocess.check_output',side_effect=[(TARGET+'\n').encode(),raw]):cmd=solver_command(root)
            self.assertEqual(cmd,['apt-get','--simulate','-o','Debug::pkgProblemResolver=yes','install','--no-install-recommends',*BASE,'libunwind-dev','g++'])
            self.assertNotIn('sudo',cmd)
    def test_changed_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);(root/'deploy').mkdir();(root/'deploy/ros-packages.txt').write_text('unexpected\n')
            with patch('runner.apt_diagnostic.subprocess.check_output',side_effect=[TARGET.encode(),b'original\n']):
                with self.assertRaisesRegex(ValueError,'tracked_blob_mismatch'):solver_command(root)
    def test_diagnostic_never_executes_real_apt_in_test(self):
        with tempfile.TemporaryDirectory() as tmp, patch('runner.apt_diagnostic.solver_command',return_value=['apt-get','--simulate']), patch('runner.apt_diagnostic.subprocess.run',return_value=Mock(returncode=100)) as run, patch('builtins.print') as output:
            diagnose('/isolated',tmp)
            self.assertEqual(run.call_args_list[0].kwargs['timeout'],90)
            self.assertFalse(run.call_args_list[0].kwargs['check'])
            self.assertEqual(run.call_args_list[0].kwargs['env']['LC_ALL'],'C')
            self.assertIn('stdout',run.call_args_list[0].kwargs)
            self.assertEqual(run.call_args_list[1].args[0][0],'/usr/bin/python3')
            self.assertIn('--output',run.call_args_list[1].args[0])
            output.assert_any_call('URP_APT_SOLVER_EXIT_100',flush=True)



class ProjectionRunTests(unittest.TestCase):
    def test_missing_module_falls_back_without_install(self):
        import json
        from runner.apt_diagnostic import projection_run
        with tempfile.TemporaryDirectory() as tmp:
            output=pathlib.Path(tmp)/'apt-dependencies.json'
            (pathlib.Path(tmp)/'apt-solver.log').write_text('Broken public-a:amd64 Depends on public-b:amd64\nBroken private-a:amd64 Depends on public-b:amd64')
            calls=[]
            def run(cmd,**kwargs):
                calls.append(cmd)
                if cmd[0]=='/usr/bin/python3':
                    output.write_text(json.dumps({'status':'metadata_unavailable','dependencies':[],'metadata_phase':'module_import','metadata_error':'import_missing'}))
                    return Mock(returncode=0)
                return Mock(returncode=0,stdout=b'public-a\npublic-b\n')
            with patch('runner.apt_diagnostic.subprocess.run',side_effect=run),patch('runner.apt_diagnostic.solver_command',return_value=[]):result=projection_run('/isolated',tmp,{})
            self.assertEqual(calls[1],['apt-cache','pkgnames'])
            self.assertEqual(result['metadata_error'],'import_missing')
            self.assertEqual(result['status'],'membership_only')
            self.assertEqual(result['evidence'],'apt_cache_membership')
            self.assertEqual(result['dependencies'],[{'package':'public-a','relation':'depends','dependency':'public-b'}])
            self.assertNotIn('private-a',json.dumps(result))
    def test_timeout_records_error_without_install(self):
        import subprocess
        from runner.apt_diagnostic import projection_run
        with tempfile.TemporaryDirectory() as tmp, patch('runner.apt_diagnostic.solver_command',return_value=[]), patch('runner.apt_diagnostic.subprocess.run',side_effect=subprocess.TimeoutExpired('PRIVATE',90)) as run:
            result=projection_run('/isolated',tmp,{})
            self.assertEqual(result['metadata_error'],'subprocess_timeout')
            self.assertEqual(run.call_count,2)
            self.assertEqual(result['fallback_error'],'timeout')

if __name__=='__main__':unittest.main()

import pathlib,tempfile,unittest
from unittest.mock import Mock,patch
if __package__:
    from .host_apt_repair import *
else:
    from host_apt_repair import *
MODULE = "runner.host_apt_repair" if __package__ else "host_apt_repair"

class RepairTests(unittest.TestCase):
    def test_local_default_rejected_without_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{},clear=True):
            execute=Mock();result=repair(tmp,tmp,100,execute)
            self.assertEqual(result['reason'],'opt_in_missing');execute.assert_not_called()
    def test_environment_and_exit_guards(self):
        env={'URP_HOST_APT_REPAIR':'1','GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'}
        host_gate(env,{'ID':'ubuntu','VERSION_ID':'22.04'},'x86_64','amd64',100)
        for altered in ({**env,'RUNNER_ENVIRONMENT':'self-hosted'},{**env,'GITHUB_ACTIONS':'false'}):
            with self.assertRaises(Refuse):host_gate(altered,{'ID':'ubuntu','VERSION_ID':'22.04'},'x86_64','amd64',100)
        with self.assertRaises(Refuse):host_gate(env,{'ID':'ubuntu','VERSION_ID':'22.04'},'x86_64','amd64',1)
    def test_exact_conflict_pair_required(self):
        evidence={'status':'membership_only','evidence':'apt_cache_membership','dependencies':[{'package':p,'relation':'breaks','dependency':d} for p,d in NEEDED]}
        evidence_gate(evidence);evidence['dependencies'].pop()
        with self.assertRaises(Refuse):evidence_gate(evidence)
    def test_unknown_removal_rejected(self):
        good='Remv pkgconf [1]\nRemv libunwind-14-dev:amd64 [1]\nRemv r-base-dev [1]\n'
        self.assertEqual(removal_plan(good),{'pkgconf','libunwind-14-dev','r-base-dev'})
        with self.assertRaises(Refuse):removal_plan(good+'Remv unrelated-development-package [1]\n')
        with self.assertRaises(Refuse):removal_plan('Remv pkgconf [1]\n')
    def test_essential_protected_or_priority_unknown_rejected(self):
        essential_gate('pkgconf\tno\t\toptional\n',{'pkgconf'})
        for raw in ('pkgconf\tyes\t\toptional\n','pkgconf\tno\tyes\toptional\n','pkgconf\tno\t\timportant\n','pkgconf\tno\t\tunknown\n'):
            with self.assertRaises(Refuse):essential_gate(raw,{'pkgconf'})
    def test_apply_plan_same_exact_arguments(self):
        # A fully mocked hosted VM; never runs apt or sudo in tests.
        env={'URP_HOST_APT_REPAIR':'1','GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'}
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,env,clear=True),patch(MODULE+'.host_gate'),patch(MODULE+'.state_digest',return_value=b'unchanged'):
            root=pathlib.Path(tmp)
            (root/'apt-dependencies.json').write_text(json.dumps({'status':'verified','dependencies':[{'package':p,'dependency':d,'relation':'breaks'} for p,d in NEEDED]}))
            def execute(cmd,**kwargs):
                if '--simulate' in cmd:kwargs['stdout'].write(b'Remv pkgconf [1]\nRemv libunwind-14-dev [1]\n')
                return Mock(returncode=0)
            runner=Mock(side_effect=execute)
            metadata=[TARGET.encode(),b'amd64',b'pkgconf\tno\t\toptional\nlibunwind-14-dev\tno\t\toptional\n']
            with patch(MODULE+'.subprocess.check_output',side_effect=metadata):result=repair(root,root,100,runner)
            self.assertEqual(result['state'],'applied')
            self.assertEqual(runner.call_args_list[0].args[0],['apt-get','--simulate',*ARGS])
            self.assertEqual(runner.call_args_list[1].args[0],['sudo','env','DEBIAN_FRONTEND=noninteractive','apt-get','-y',*ARGS])
            with patch(MODULE+'.subprocess.check_output') as sub:result=repair(root,root,100,runner)
            self.assertEqual(result['reason'],'already_attempted');sub.assert_not_called()



class PartialEvidenceTests(unittest.TestCase):
    def partial(self):
        return {'status':'verified','dependencies':[{'package':'libunwind-14-dev','package_version':'1.0','relation':'breaks','dependency':'libunwind-dev','version_relation':'any','dependency_version':''}]}
    def complete_secondary(self):
        return {'status':'membership_only','evidence':'apt_cache_membership','fallback_error':'none','dependencies':[{'package':p,'relation':'breaks','dependency':d} for p,d in NEEDED]}
    def test_verified_partial_rechecked_without_overwriting_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary=self.partial();path=pathlib.Path(tmp)/'apt-dependencies.json';path.write_text(json.dumps(primary));before=path.read_bytes()
            fallback=Mock(return_value=self.complete_secondary())
            self.assertEqual(select_evidence(primary,'SOURCE',tmp,{},fallback),'apt_cache_membership')
            fallback.assert_called_once_with('SOURCE',tmp,{},primary)
            self.assertEqual(path.read_bytes(),before)
            self.assertTrue((pathlib.Path(tmp)/'host-repair-evidence.json').is_file())
    def test_invalid_verified_or_membership_does_not_trigger_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            for evidence in ({'status':'verified','dependencies':[]}, {'status':'verified','dependencies':[{'package':'PRIVATE'}]}, {'status':'membership_only','evidence':'apt_cache_membership','dependencies':[]}):
                fallback=Mock()
                with self.assertRaises(Refuse):select_evidence(evidence,'SOURCE',tmp,{},fallback)
                fallback.assert_not_called()
    def test_fallback_still_requires_both_original_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            secondary=self.complete_secondary();secondary['dependencies'].pop()
            with self.assertRaises(Refuse):select_evidence(self.partial(),'SOURCE',tmp,{},Mock(return_value=secondary))
            secondary=self.complete_secondary();secondary['fallback_error']='timeout'
            with self.assertRaises(Refuse):select_evidence(self.partial(),'SOURCE',tmp,{},Mock(return_value=secondary))
    def test_complete_primary_no_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary=self.partial();extra=dict(primary['dependencies'][0],package='pkgconf',dependency='pkg-config');primary['dependencies'].append(extra)
            fallback=Mock();self.assertEqual(select_evidence(primary,'SOURCE',tmp,{},fallback),'official_metadata');fallback.assert_not_called()

if __name__=='__main__':unittest.main()

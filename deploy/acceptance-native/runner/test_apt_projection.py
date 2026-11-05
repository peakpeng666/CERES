import json, unittest
from types import SimpleNamespace as S
from runner.apt_projection import selected_edges, metadata_lookup, project, official

def version(name='1.0',site='archive.ubuntu.com',trusted=True,origin='Ubuntu',edges=()):
    def deps(typ):return [S(or_dependencies=[S(name=d,relation=op,version=v)]) for t,d,op,v in edges if t==typ]
    return S(version=name,origins=[S(site=site,trusted=trusted,origin=origin,archive='jammy')],get_dependencies=deps)

def pkg(v):return S(candidate=v,installed=None)

class ProjectionTests(unittest.TestCase):
    def test_rebuilds_only_verified_public_fields(self):
        cache={'root':pkg(version(edges=[('Depends','lib-a','>=','1.0')])),'lib-a':pkg(version())}
        text=' root : Depends: lib-a (>= PRIVATE_VERSION) but PRIVATE_URL is to be installed\n'
        out=project(text,metadata_lookup(cache),['root'])
        self.assertEqual(out['status'],'verified')
        self.assertEqual(out['dependencies'],[{'package':'root','package_version':'1.0','relation':'depends','dependency':'lib-a','version_relation':'ge','dependency_version':'1.0'}])
        self.assertNotIn('PRIVATE',json.dumps(out))
    def test_unverified_edge_and_private_origin_dropped(self):
        cache={'root':pkg(version(edges=[('Depends','private-a','','')])),'private-a':pkg(version(site='private.invalid'))}
        self.assertEqual(project('Broken root:amd64 Depends on private-a:amd64',metadata_lookup(cache),['root'])['dependencies'],[])
        self.assertEqual(project('Broken root:amd64 Depends on unknown:amd64',metadata_lookup(cache),['root'])['dependencies'],[])
        self.assertFalse(official(S(trusted=False,origin='Ubuntu',site='archive.ubuntu.com',archive='jammy')))
    def test_conflict_and_tree_scope(self):
        cache={'root':pkg(version(edges=[('Depends','lib-a','','')])),'lib-a':pkg(version()),'lib-slot':pkg(version(edges=[('Conflicts','lib-a','','')])),'unrelated':pkg(version(edges=[('Depends','other','','')])),'other':pkg(version())}
        text='Broken lib-slot:amd64 Conflicts on lib-a:amd64\nBroken unrelated:amd64 Depends on other:amd64'
        out=project(text,metadata_lookup(cache),['root'])
        self.assertEqual(len(out['dependencies']),1);self.assertEqual(out['dependencies'][0]['relation'],'conflicts')
    def test_cap_and_no_arbitrary_text(self):
        cache={'root':pkg(version(edges=[('Depends','lib-'+str(i),'','') for i in range(30)]))}
        cache.update({'lib-'+str(i):pkg(version()) for i in range(30)})
        text='\n'.join('Broken root:amd64 Depends on lib-'+str(i)+':amd64' for i in range(30))
        self.assertEqual(len(project(text,metadata_lookup(cache),['root'])['dependencies']),20)
        self.assertEqual(selected_edges('PRIVATE_PATH URL command user experiment'),[])
    def test_relation_mismatch_and_forged_version_dropped(self):
        cache={'root':pkg(version(name='PRIVATE/PATH',edges=[('Depends','dep','','')])),'dep':pkg(version())}
        self.assertEqual(project('Broken root:amd64 Depends on dep:amd64',metadata_lookup(cache),['root'])['dependencies'],[])
        cache['root']=pkg(version(edges=[('Depends','dep','','')]))
        self.assertEqual(project('Broken root:amd64 Conflicts on dep:amd64',metadata_lookup(cache),['root'])['dependencies'],[])



class MetadataErrorsTests(unittest.TestCase):
    def test_type_errors_name_the_exact_bounded_api_operation(self):
        import pathlib,tempfile
        from unittest.mock import patch
        from runner.apt_projection import collect
        from public_summary import project as public_project
        class BrokenVersions:
            @property
            def candidate(self):raise TypeError('PRIVATE_VERSION_PATH')
        class BrokenOrigins:
            version='1.0'
            @property
            def origins(self):raise TypeError('PRIVATE_ORIGIN_URL')
        class BrokenGroups:
            version='1.0'
            origins=[S(site='archive.ubuntu.com',trusted=True,origin='Ubuntu',archive='jammy')]
            def get_dependencies(self,typ):raise TypeError('PRIVATE_DEPENDENCY_DATA')
        class BrokenContains(dict):
            def __contains__(self,name):raise TypeError('PRIVATE_CACHE_VALUE')
        cases=[('cache_membership',BrokenContains()),
               ('version_selection',{'root':BrokenVersions()}),
               ('origin_records',{'root':pkg(BrokenOrigins())}),
               ('dependency_groups',{'root':pkg(BrokenGroups())})]
        for operation,cache in cases:
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as tmp:
                log=pathlib.Path(tmp)/'log';log.write_text('Broken root:amd64 Depends on dep:amd64')
                with patch('runner.apt_diagnostic.solver_command',return_value=['unused']*6+['root']),patch('runner.apt_projection.importlib.import_module',return_value=S(Cache=lambda:cache)):
                    result=collect('/unused',log,pathlib.Path(tmp)/'out.json')
                self.assertEqual(result['metadata_error'],'api_type')
                self.assertEqual(result['metadata_phase'],'metadata_project')
                self.assertEqual(result['metadata_operation'],operation)
                self.assertEqual(result['dependencies'],[])
                self.assertNotIn('PRIVATE',json.dumps(result))
                public=public_project({}, {'apt_projection':result})
                self.assertEqual(public['apt_metadata_operation'],operation)
                self.assertEqual(public['apt_metadata_status'],'metadata_unavailable')
                result['metadata_operation']='PRIVATE_OPERATION'
                self.assertEqual(public_project({}, {'apt_projection':result})['apt_metadata_operation'],'not_run')

    def test_supported_api_contract_still_verifies_official_edges(self):
        from runner.apt_projection import METADATA_OPERATIONS
        observed=[]
        cache={'root':pkg(version(edges=[('Depends','dep','>=','2')])),
               'dep':pkg(version(name='2'))}
        result=project('Broken root:amd64 Depends on dep:amd64',metadata_lookup(cache,observed.append),['root'])
        self.assertEqual(result['status'],'verified')
        self.assertTrue(set(observed)<=METADATA_OPERATIONS)
        self.assertIn('dependency_fields',observed)

    def test_fixed_exception_categories(self):
        from runner.apt_projection import error_category
        self.assertEqual(error_category(ModuleNotFoundError('PRIVATE')),'import_missing')
        self.assertEqual(error_category(AttributeError('PRIVATE')),'api_attribute')
        self.assertEqual(error_category(TypeError('PRIVATE')),'api_type')
        self.assertEqual(error_category(RuntimeError('PRIVATE')),'apt_runtime')
    def test_missing_import_has_phase_without_raw_exception(self):
        import pathlib,tempfile
        from unittest.mock import patch
        from runner.apt_projection import collect
        with tempfile.TemporaryDirectory() as tmp, patch('runner.apt_projection.importlib.import_module',side_effect=ModuleNotFoundError('PRIVATE_MODULE')):
            result=collect('/unused','/unused',pathlib.Path(tmp)/'out.json')
            self.assertEqual(result['metadata_phase'],'module_import')
            self.assertEqual(result['metadata_error'],'import_missing')
            self.assertNotIn('PRIVATE',json.dumps(result))

if __name__=='__main__':unittest.main()

import json, pathlib, tempfile, unittest, sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent/'warehouse'))
import resource_uri_diagnostics as r
import startup_diagnostics as startup
from public_summary import project as maze_public
from warehouse.warehouse_public_summary import project as warehouse_public

class ResourceUriDiagnosticsTests(unittest.TestCase):
    def test_public_launch_graph_catalog_matches_exact_allowlist(self):
        data=json.loads((pathlib.Path(__file__).parent/'fixtures/launch_process_catalog.json').read_text())
        self.assertEqual(set(data['added_process_names']),set(r.LAUNCH_GRAPH_PROCESSES))
        self.assertEqual(set(data['added_process_names']),set().union(*(set(x['executables']) for x in data['sources']))-{'turtlebot4_node'})
        for name in data['added_process_names']:
            with self.subTest(name=name):
                self.assertEqual(r.PROCESS_NAMES[name],name)
                line=f'[ERROR] [{name}-17]: process has died [pid 1234, exit code 1, cmd PRIVATE]'
                row=self.events(line)[0]
                self.assertEqual(row['process'],name)
                self.assertEqual(row['exit_code'],1)
                self.assertEqual(row['context'],'no_shutdown_marker')
                self.assertNotIn('PRIVATE',json.dumps(row))
    def test_launch_graph_labels_do_not_match_private_prefix_or_suffix(self):
        for name in r.LAUNCH_GRAPH_PROCESSES:
            for unknown in ('PRIVATE_'+name,name+'_PRIVATE','/PRIVATE/'+name):
                row=self.events(f'[ERROR] [{unknown}-1]: process has died [pid 12, exit code 1, cmd PRIVATE]')[0]
                self.assertEqual(row['process'],'unknown')
                self.assertNotIn('PRIVATE',json.dumps(row))
    def test_launch_graph_classification_preserves_shutdown_and_counts(self):
        line='[ERROR] [spawner-7]: process has died [pid 123, exit code 1, cmd PRIVATE]'
        rows=self.events(line+'\n'+line+'\nsending signal SIGTERM\n'+line)
        self.assertEqual({(x['context'],x['count'],x['process'],x['exit_code']) for x in rows},
                         {('before_shutdown_marker',2,'spawner',1),('at_or_after_shutdown_marker',1,'spawner',1)})
    def test_launch_catalog_does_not_change_failure_code_detection(self):
        for name in r.LAUNCH_GRAPH_PROCESSES:
            text=f'[{name}-2]: process has died [pid 32, exit code 1, cmd PRIVATE]'
            self.assertEqual(r.runtime_failure_codes({'sim':text}),{'launch_process_died'})

    def events(self,text):return r.project({'sim':text})['events']
    def test_all_nine_exact_world_references(self):
        for key,owner,name,_ in r.MODELS:
            uri='https://fuel.gazebosim.org/1.0/'+owner+'/models/'+name
            row=self.events('Msg: Unable to find uri['+uri+']')[0]
            self.assertEqual(row['reference'],key+'_model')
    def test_every_catalogued_reference_and_encoded_alias(self):
        for uri,ref in r.CATALOGUE.items():
            self.assertEqual(self.events('Unable to find uri['+uri+']')[0]['reference'],ref)
        self.assertEqual(self.events('Unable to find uri[https://fuel.gazebosim.org/1.0/OpenRobotics/models/Jersey%20Barrier]')[0]['reference'],'jersey_barrier_model')
    def test_private_paths_urls_and_near_matches_never_exported(self):
        known='https://fuel.gazebosim.org/1.0/OpenRobotics/models/Warehouse'
        for value in ['/PRIVATE/home/private.sdf','file:///PRIVATE/key','https://PRIVATE.example/key',known+'?PRIVATE',known+'/PRIVATE',known.replace('fuel.','PRIVATE@fuel.'),known.replace('.org/','.org.PRIVATE/'),'../'+known]:
            out=r.project({'sim':'Unable to find uri['+value+']'})
            self.assertEqual(out['events'][0]['reference'],'unknown');self.assertNotIn('PRIVATE',json.dumps(out))
    def test_malformed_and_oversized_uri_still_count_unknown(self):
        for value in ['Unable to find uri[','Unable to find uri[PRIVATE','Unable to find uri['+'x'*2049+']']:
            self.assertEqual(self.events(value)[0]['reference'],'unknown')
    def test_traceback_source_line_is_not_uri_lookup_evidence(self):
        text="    if 'process has died' in txt or 'Unable to find uri' in txt:raise RuntimeError('官方仿真启动错误，详见 sim.log')"
        old=startup.project({}, {'session':text})
        self.assertNotIn('launch_uri_missing',old['error_codes'])
        self.assertNotIn('launch_process_died',old['error_codes'])
        self.assertEqual(old['launch_evidence_version'],'runtime_records_v2')
        self.assertEqual(old['resource_uri_diagnostics']['events'],[])
        actual=startup.project({}, {'session':text,'sim':'Msg: Unable to find uri[PRIVATE]'})
        self.assertEqual(actual['resource_uri_diagnostics']['events'][0]['component'],'sim')
    def test_runtime_codes_require_actual_records_and_runtime_component(self):
        text="[ERROR] [ruby $(which ign) gazebo-1]: process has died [pid 1, exit code 1, cmd PRIVATE]"
        for component in ('sim','truth','localization','nav'):
            out=startup.project({}, {component:text})
            self.assertIn('launch_process_died',out['error_codes'])
            self.assertNotIn('launch_uri_missing',out['error_codes'])
        self.assertEqual(r.runtime_failure_codes({'session':text}),set())
        self.assertEqual(r.runtime_failure_codes({'sim':'process has died'}),set())
        self.assertEqual(r.runtime_failure_codes({'sim':'Unable to find uri['}),set())
        self.assertEqual(r.runtime_failure_codes({'sim':'Unable to find uri[]'}),set())
        self.assertEqual(r.runtime_failure_codes({'sim':'Msg: Unable to find uri[PRIVATE]'}),{'launch_uri_missing'})
    def test_legacy_or_unavailable_version_is_explicit(self):
        self.assertEqual(startup.sanitize({'error_codes':['launch_uri_missing']})['launch_evidence_version'],'legacy_or_unavailable')
    def test_supplied_empty_absent_invalid_logs_are_distinguishable(self):
        for logs,expected in [({},'not_provided'),({'sim':''},'provided_empty'),({'sim':None},'invalid_input'),({'sim':'nothing'},'provided')]:
            self.assertEqual(r.project(logs)['log_availability']['sim'],expected)
        self.assertEqual(r.sanitize({'log_availability':{'sim':'PRIVATE'}})['log_availability']['sim'],'not_provided')
    def test_collector_missing_empty_truncated_and_rejected_are_distinct(self):
        sid='sim_20261022T080000_abcdef'
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)/'sim'/sid;(root/'logs').mkdir(parents=True)
            path=root/'logs'/'sim.log'
            self.assertEqual(startup.collect(tmp,sid)['resource_uri_diagnostics']['log_availability']['sim'],'missing')
            path.write_text('')
            self.assertEqual(startup.collect(tmp,sid)['resource_uri_diagnostics']['log_availability']['sim'],'empty')
            path.write_text('x'*65536+'\nTAIL')
            self.assertEqual(startup.collect(tmp,sid)['resource_uri_diagnostics']['log_availability']['sim'],'tail_truncated')
            path.unlink();path.symlink_to('/etc/passwd')
            self.assertEqual(startup.collect(tmp,sid)['resource_uri_diagnostics']['log_availability']['sim'],'rejected')
    def test_collector_read_error_is_finite_and_not_empty(self):
        from unittest.mock import patch
        sid='sim_20261022T080000_abcdef'
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)/'sim'/sid;(root/'logs').mkdir(parents=True)
            (root/'logs'/'sim.log').write_text('PRIVATE')
            with patch.object(pathlib.Path,'open',side_effect=PermissionError('PRIVATE')):
                out=startup.collect(tmp,sid)
            self.assertEqual(out['resource_uri_diagnostics']['log_availability']['sim'],'unreadable')
            self.assertNotIn('PRIVATE',json.dumps(out))
    def test_runtime_classifier_discards_partial_oversized_line(self):
        self.assertEqual(r.runtime_failure_codes({'sim':'x'*65536+'Unable to find uri[PRIVATE]'}),set())
    def test_public_uri_whitespace_from_sdf(self):
        row=self.events('Unable to find uri[\n https://fuel.gazebosim.org/1.0/OpenRobotics/models/Warehouse \n]')[0]
        self.assertEqual(row['reference'],'warehouse_model')
    def test_download_log_is_only_an_observed_attempt(self):
        row=self.events('Downloading model [https://fuel.gazebosim.org/openrobotics/models/warehouse]')[0]
        self.assertEqual(row['event'],'fuel_download_marker');self.assertEqual(row['reference'],'warehouse_model')
    def test_child_process_name_and_exit_code_allowlist(self):
        for name,expected in [('ruby $(which ign) gazebo','gazebo'),('create','entity_create'),('PRIVATE','unknown')]:
            row=self.events('[ERROR] ['+name+'-1]: process has died [pid 9182, exit code -15, cmd PRIVATE]')[0]
            self.assertEqual(row['process'],expected);self.assertEqual(row['exit_code'],-15)
            self.assertNotIn('PRIVATE',json.dumps(row));self.assertNotIn('9182',json.dumps(row))
    def test_process_code_bounds_and_shape(self):
        for code in ['True','NaN','999','-999','1234']:
            self.assertEqual(self.events('[a-1]: process has died [pid 1, exit code '+code+', cmd PRIVATE]'),[])
    def test_shutdown_context_is_log_relative_not_causal(self):
        log='Unable to find uri[PRIVATE]\nsending signal SIGINT to process\nUnable to find uri[PRIVATE]'
        rows=self.events(log);self.assertEqual({x['context'] for x in rows},{'before_shutdown_marker','at_or_after_shutdown_marker'})
        self.assertEqual(r.project({'sim':log})['failure_time_alignment'],'unavailable')
    def test_render_markers_only(self):
        self.assertEqual(self.events('graphics slow; GPU? EGL? physics stalled'),[])
        for marker,event in r.MARKERS.items():
            self.assertIn(event,{x['event'] for x in self.events(marker+' PRIVATE')})
    def test_deduplicate_counts_and_fixed_component_scope(self):
        out=r.project({'sim':('Unable to find uri[PRIVATE]\n')*10,'PRIVATE':'Unable to find uri[PRIVATE]'})
        self.assertEqual(len(out['events']),1);self.assertEqual(out['events'][0]['count'],10)
    def test_input_and_event_bounds(self):
        out=r.project({'sim':'Unable to find uri[PRIVATE]'+'x'*r.LIMIT+'\n'})
        self.assertTrue(out['additional_input_truncation']);self.assertEqual(out['events'],[])
        text='\n'.join('Unable to find uri['+uri+']' for uri in r.CATALOGUE)
        out=r.project({c:text for c in r.COMPONENTS})
        self.assertTrue(out['events_truncated']);self.assertEqual(len(out['events']),64)
    def test_strict_sanitizer_types_and_private_field_drop(self):
        base=self.events('Unable to find uri[PRIVATE]')[0]
        for field,value in [('count',True),('count',float('inf')),('count',0),('count',10001),('exit_code',float('nan')),('exit_code',True),('component',[]),('reference','PRIVATE'),('context','PRIVATE')]:
            row=dict(base,**{field:value});self.assertEqual(r.sanitize({'events':[row]})['events'],[])
        base['PRIVATE']='PRIVATE';out=r.sanitize({'PRIVATE':'PRIVATE','events':[base]})
        self.assertNotIn('PRIVATE',json.dumps(out,allow_nan=False));self.assertEqual(out,r.sanitize(out))
    def test_missing_exit_code_and_non_dict_are_safe(self):
        row=self.events('Unable to find uri[PRIVATE]')[0];del row['exit_code']
        self.assertIsNone(r.sanitize({'events':[row]})['events'][0]['exit_code'])
        for raw in (None,[],True,{'events':None}):self.assertEqual(r.sanitize(raw)['events'],[])
    def test_projection_idempotence_and_fail_preservation(self):
        out=startup.project({'errors':['控制器/传感器未就绪']},{'sim':'Unable to find uri[PRIVATE]'})
        self.assertEqual(out,startup.sanitize(out))
        for project in (maze_public,warehouse_public):
            result=project({'state':'FAIL','startup_diagnostics':out},require_runner=False)
            self.assertEqual(result['state'],'FAIL');self.assertEqual(result['startup_diagnostics']['resource_uri_diagnostics'],out['resource_uri_diagnostics'])
    def test_private_collector_end_to_end_and_no_following_links(self):
        sid='sim_20261022T080000_abcdef'
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)/'sim'/sid;(root/'logs').mkdir(parents=True)
            (root/'session-report.json').write_text(json.dumps({'counts':{'clock':26},'errors':['控制器/传感器未就绪']}))
            (root/'logs'/'sim.log').write_text('Unable to find uri[https://fuel.gazebosim.org/1.0/OpenRobotics/models/Warehouse]')
            out=startup.collect(tmp,sid);self.assertEqual(out['resource_uri_diagnostics']['events'][0]['reference'],'warehouse_model')
            self.assertEqual(out['counts']['clock'],26)
            (root/'logs'/'sim.log').unlink();(root/'logs'/'sim.log').symlink_to('/etc/passwd')
            self.assertEqual(startup.collect(tmp,sid)['resource_uri_diagnostics']['events'],[])

if __name__=='__main__':unittest.main()

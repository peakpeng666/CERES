import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import public_receipt as p

class BuildkitReceiptIntegrationTests(unittest.TestCase):
    def test_parser_only_reached_for_failed_image_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            for phase,code,stage,reached in [('preparation',1,'image_build',True),('preparation',0,'image_build',False),('preparation',1,'seed_retrieval',False),('isolation_and_build',1,'image_build',False)]:
                with self.subTest(phase=phase,code=code,stage=stage):
                    (state/'terminal.json').write_text(json.dumps({'phase':phase,'exit_code':code}))
                    (state/'preparation-stderr.log').write_text(json.dumps({'schema':1,'stage':stage,'code':'UNCLASSIFIED_PREPARATION_FAILURE','exception_type':'CalledProcessError'}))
                    with patch.object(p._buildkit,'parse_log',return_value=None) as parse, patch.object(p._buildkit,'inspect_structure',return_value=None) as structure:
                        result=p.project(state)
                    self.assertEqual(parse.called,reached)
                    self.assertEqual(structure.called,reached)
                    self.assertIsNone(result['buildkit_structure'])
                    self.assertIsNone(result['buildkit_failure_detail'])
                    self.assertNotEqual(result['outcome'],'built_not_accepted')

    def test_projection_is_independently_revalidated_and_never_leaks_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            (state/'terminal.json').write_text(json.dumps({'phase':'preparation','exit_code':1}))
            (state/'preparation-stderr.log').write_text(json.dumps({'schema':1,'stage':'image_build','code':'UNCLASSIFIED_PREPARATION_FAILURE','exception_type':'CalledProcessError'}))
            with patch.object(p._buildkit,'parse_log',return_value={'raw':'PRIVATE_TOKEN_PRIVATE_URL'}), patch.object(p._buildkit,'inspect_structure',return_value={'raw':'PRIVATE_STRUCTURE'}):
                result=p.project(state)
            self.assertIsNone(result['buildkit_failure_detail'])
            self.assertNotIn('PRIVATE',json.dumps(result))

    def test_real_framed_private_log_projects_only_closed_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);(state/'prepared').mkdir()
            (state/'terminal.json').write_text(json.dumps({'phase':'preparation','exit_code':1}))
            (state/'preparation-stderr.log').write_text(json.dumps({'schema':1,'stage':'image_build','code':'UNCLASSIFIED_PREPARATION_FAILURE','exception_type':'CalledProcessError'}))
            command=p._buildkit.RUN_COMMANDS['bootstrap']
            raw='PRIVATE_URL PRIVATE_HEADER\n#9 [ 8/17] RUN '+command+'\n#9 0.123 SAFE_BOOTSTRAP_DIAGNOSTIC:BLOCKED_public_ca_seed\n#9 ERROR: PRIVATE_ERROR\n'
            (state/'prepared/preparation.log').write_text(raw)
            result=p.project(state)
            self.assertEqual(result['buildkit_failure_detail'],{'schema':1,'source':'bootstrap','diagnostic_only':True,'detail':{'code':'BLOCKED_public_ca_seed'}})
            self.assertNotIn('PRIVATE',json.dumps(result))
            self.assertEqual(result['buildkit_structure']['failed_stage'],'bootstrap')
            self.assertEqual(result['buildkit_structure']['stages_reached'],['bootstrap'])
            self.assertEqual(result['buildkit_structure']['log_bytes'],len(raw.encode()))
            self.assertNotEqual(result['outcome'],'built_not_accepted')

    def test_pre_run_failure_has_shape_without_marker_or_raw_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);(state/'prepared').mkdir()
            (state/'terminal.json').write_text(json.dumps({'phase':'preparation','exit_code':1}))
            (state/'preparation-stderr.log').write_text(json.dumps({'schema':1,'stage':'image_build','code':'UNCLASSIFIED_PREPARATION_FAILURE','exception_type':'CalledProcessError'}))
            for raw,stage,frames in [('PRIVATE_DOCKER_CLIENT_ERROR\n','unknown',False),('#2 [internal] load metadata for '+p._buildkit.BASE_IMAGE+'\n#2 ERROR: PRIVATE_URL_TOKEN\n','base_metadata',True)]:
                (state/'prepared/preparation.log').write_text(raw)
                result=p.project(state)
                self.assertIsNone(result['buildkit_failure_detail'])
                self.assertEqual(result['buildkit_structure']['failed_stage'],stage)
                self.assertEqual(result['buildkit_structure']['buildkit_frames_seen'],frames)
                self.assertNotIn('PRIVATE',json.dumps(result))
                self.assertNotEqual(result['outcome'],'built_not_accepted')

    def test_plain_progress_is_only_build_command_change(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/'preparation/prepare_image.py').read_text()
        tree=ast.parse(text)
        assignments=[node for node in ast.walk(tree) if isinstance(node,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='cmd' for x in node.targets)]
        self.assertEqual(len(assignments),1)
        values=[node.value for node in ast.walk(assignments[0].value) if isinstance(node,ast.Constant) and isinstance(node.value,str)]
        for required in ('build','--pull','--no-cache','--platform','linux/amd64','--progress=plain'):
            self.assertIn(required,values)
        for forbidden in ('--privileged','--network=host','--secret','--ssh'):
            self.assertNotIn(forbidden,values)

if __name__=='__main__':unittest.main()

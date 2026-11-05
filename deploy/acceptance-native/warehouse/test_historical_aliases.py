import copy
import unittest
from pathlib import Path
from unittest.mock import patch
import historical_fuel as h
class HistoricalAliasTests(unittest.TestCase):
    def setUp(self):
        self.cache=Path('/fixed/cache');self.selected=h.selected_files();self.report={'files':{str(self.cache/h.JERSEY_PREFIX/f):self.selected[h.JERSEY_PREFIX+f]['sha256'] for f in h.JERSEY_FILES},'complete':True}
        self.validation=patch.object(h,'verify_cache',return_value={});self.validation.start();self.addCleanup(self.validation.stop)
    def test_only_four_exact_aliases_and_no_mutation(self):
        before=copy.deepcopy(self.report);result=h.canonicalize_assets(self.report,self.cache)
        self.assertEqual(before,self.report);self.assertEqual(len(result['files']),4)
        self.assertTrue(all('/jersey%20barrier/' in key for key in result['files']))
        self.assertEqual(result,h.canonicalize_assets(result,self.cache))
    def test_collision_rejected_even_if_bytes_match(self):
        key=next(iter(self.report['files']));self.report['files'][key.replace('/jersey barrier/','/jersey%20barrier/')]=self.report['files'][key]
        with self.assertRaises(ValueError):h.canonicalize_assets(self.report,self.cache)
    def test_wrong_reported_hash_rejected(self):
        self.report['files'][next(iter(self.report['files']))]='0'*64
        with self.assertRaises(ValueError):h.canonicalize_assets(self.report,self.cache)
    def test_unknown_alias_not_decoded(self):
        self.report['files']['/fixed/cache/fuel.gazebosim.org/openrobotics/models/other%20model/6/model.sdf']='a'*64
        result=h.canonicalize_assets(self.report,self.cache);self.assertIn('/fixed/cache/fuel.gazebosim.org/openrobotics/models/other%20model/6/model.sdf',result['files'])
    def test_unknown_root_rejected(self):
        self.report['files']={key.replace('/fixed/cache/','/unknown/cache/'):value for key,value in self.report['files'].items()}
        with self.assertRaises(ValueError):h.canonicalize_assets(self.report,self.cache)
    def test_cache_verification_failure_propagates(self):
        with patch.object(h,'verify_cache',side_effect=ValueError('changed')):
            with self.assertRaises(ValueError):h.canonicalize_assets(self.report,self.cache)
    def test_pinned_both_side_equality_required(self):
        selected=copy.deepcopy(self.selected);selected[h.JERSEY_PREFIX+'model.sdf']['sha256']='b'*64
        with patch.object(h,'selected_files',return_value=selected):
            with self.assertRaises(ValueError):h.canonicalize_assets(self.report,self.cache)
if __name__=='__main__':unittest.main()

import copy,hashlib,io,json,os,pathlib,sys,tempfile,unittest
from unittest.mock import patch
import historical_fuel as h
from resource_identity import compare,normalized_files
from warehouse_public_summary import project
import test_lifecycle

class Reply:
 def __init__(self,data):self.buf=io.BytesIO(data)
 def read(self,n=-1):return self.buf.read(n)
 def __enter__(self):return self
 def __exit__(self,*a):pass

class Download(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=pathlib.Path(self.temp.name)
  self.meta={'id':h.ASSET_ID,'name':'fuel-cache.tar.zst','size':h.ARCHIVE_SIZE,'digest':'sha256:'+h.ARCHIVE_SHA256,'browser_download_url':h.ASSET_URL}
 def test_metadata_all_five_fields_fixed(self):
  for k in self.meta:
   meta=self.meta.copy();meta[k]='changed'
   with self.subTest(k=k),patch.object(h.urllib.request,'urlopen',return_value=Reply(json.dumps(meta).encode())) as op:
    with self.assertRaisesRegex(ValueError,'metadata_mismatch'):h.download_archive(self.root)
    self.assertEqual(op.call_count,1)
 def test_metadata_size_bound(self):
  with patch.object(h.urllib.request,'urlopen',return_value=Reply(b' '*(1048577))):
   with self.assertRaisesRegex(ValueError,'metadata_oversized'):h.download_archive(self.root)
 def test_download_short_bad_digest_rejected(self):
  with patch.object(h.urllib.request,'urlopen',side_effect=[Reply(json.dumps(self.meta).encode()),Reply(b'x')]):
   with self.assertRaisesRegex(ValueError,'archive_size_mismatch'):h.download_archive(self.root)
 def test_download_byte_limit(self):
  self.meta['size']=2
  with patch.object(h,'ARCHIVE_SIZE',2),patch.object(h.urllib.request,'urlopen',side_effect=[Reply(json.dumps(self.meta).encode()),Reply(b'xxx')]):
   with self.assertRaisesRegex(ValueError,'download_limit'):h.download_archive(self.root)
 def test_download_deadline_after_read(self):
  with patch.object(h.time,'monotonic',side_effect=[0,601]),patch.object(h.urllib.request,'urlopen',side_effect=[Reply(json.dumps(self.meta).encode()),Reply(b'x')]):
   with self.assertRaisesRegex(ValueError,'download_limit'):h.download_archive(self.root)
 def test_private_target_not_overwritten(self):
  p=self.root/'historical-fuel-cache.tar.zst';p.write_bytes(b'sentinel')
  with patch.object(h.urllib.request,'urlopen',side_effect=[Reply(json.dumps(self.meta).encode()),Reply(b'x')]):
   with self.assertRaises(FileExistsError):h.download_archive(self.root)
  self.assertEqual(p.read_bytes(),b'sentinel')

class Cache(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name)
  self.cache=self.root/'cache';self.cache.mkdir();self.key='a/b/model.sdf';self.data=b'original';p=self.cache/self.key;p.parent.mkdir(parents=True);p.write_bytes(self.data)
  self.selected={self.key:{'size':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest()}}
  self.mock=patch.object(h,'selected_files',return_value=self.selected);self.mock.start();self.addCleanup(self.mock.stop)
 def test_selected_parent_symlink_rejected(self):
  (self.cache/'a').rename(self.root/'outside');(self.cache/'a').symlink_to(self.root/'outside',target_is_directory=True)
  with self.assertRaisesRegex(ValueError,'link_rejected'):h.verify_cache(self.cache)
 def test_broken_selected_symlink_rejected(self):
  p=self.cache/self.key;p.unlink();p.symlink_to(self.root/'absent')
  with self.assertRaisesRegex(ValueError,'link_rejected'):h.verify_cache(self.cache)
 def test_materialize_symlink_parent_rejected_before_archive(self):
  (self.root/'link').symlink_to(self.cache,target_is_directory=True)
  with patch.object(h,'verify_archive') as v:
   with self.assertRaisesRegex(ValueError,'parent_link_rejected'):h.materialize(self.root/'archive',self.root/'link'/'new')
   v.assert_not_called()
 def test_materialize_broken_destination_symlink_rejected(self):
  target=self.root/'broken';target.symlink_to(self.root/'absent')
  with patch.object(h,'verify_archive') as v:
   with self.assertRaisesRegex(ValueError,'destination_exists'):h.materialize(self.root/'archive',target)
   v.assert_not_called()
 def test_unselected_alternate_model_rejected(self):
  p=self.cache/'a/999';p.mkdir();(p/'model.config').write_text('<model><sdf>alternate.sdf</sdf></model>');(p/'alternate.sdf').write_text('<sdf/>')
  with self.assertRaises(ValueError):h.verify_cache(self.cache)
 def test_unselected_symlink_rejected(self):
  (self.cache/'extra').symlink_to(self.root/'outside')
  with self.assertRaises(ValueError):h.verify_cache(self.cache)
 def test_unselected_empty_directory_rejected(self):
  (self.cache/'extra').mkdir()
  with self.assertRaises(ValueError):h.verify_cache(self.cache)

 def test_selected_hardlink_rejected(self):
  os.link(self.cache/self.key,self.root/'outside-hardlink')
  with self.assertRaisesRegex(ValueError,'link_rejected'):h.verify_cache(self.cache)
 def test_unselected_fifo_rejected(self):
  os.mkfifo(self.cache/'fifo')
  with self.assertRaisesRegex(ValueError,'special_entry_rejected'):h.verify_cache(self.cache)

class Alias(unittest.TestCase):
 def setUp(self):
  self.cache=pathlib.Path('/fixed/cache');self.selected=h.selected_files();self.report={'complete':True,'missing':[],'files':{str(self.cache/(h.JERSEY_PREFIX+s)):self.selected[h.JERSEY_PREFIX+s]['sha256'] for s in h.JERSEY_FILES}}
  self.mock=patch.object(h,'verify_cache',return_value={});self.mock.start();self.addCleanup(self.mock.stop)
 def test_unknown_jersey_suffix_rejected(self):
  self.report['files'][str(self.cache/h.JERSEY_PREFIX/'extra.dae')]='a'*64
  with self.assertRaisesRegex(ValueError,'unexpected_historical_alias_path'):h.canonicalize_assets(self.report,self.cache)
 def test_path_traversal_alias_rejected(self):
  k=next(iter(self.report['files']));v=self.report['files'].pop(k);self.report['files'][k.replace('/jersey barrier/','/jersey barrier/../jersey barrier/')]=v
  with self.assertRaisesRegex(ValueError,'unexpected_historical_alias_path'):h.canonicalize_assets(self.report,self.cache)
 def test_missing_alias_not_recreated(self):
  old=copy.deepcopy(self.report);k=next(iter(self.report['files']));self.report['files'].pop(k)
  canon=h.canonicalize_assets(self.report,self.cache)
  self.assertFalse(compare(h.canonicalize_assets(old,self.cache),canon)['resources_match_historical'])
 def test_nonjersey_duplicate_logical_key_caught_by_compare(self):
  r=h.canonicalize_assets(self.report,self.cache);r['files']['/x/fuel.gazebosim.org/o/models/a/1/model.sdf']='a'*64;r['files']['/y/fuel.gazebosim.org/o/models/a/1/model.sdf']='a'*64
  with self.assertRaisesRegex(ValueError,'collision'):normalized_files(r)
 def test_wrong_encoded_hash_rejected(self):
  r=h.canonicalize_assets(self.report,self.cache);r['files'][next(iter(r['files']))]='b'*64
  with self.assertRaisesRegex(ValueError,'reported_alias_identity_mismatch'):h.canonicalize_assets(r,self.cache)

class Public(unittest.TestCase):
 def good(self):return test_lifecycle.LifecycleTests().good()
 def test_new_schema_no_arbitrary_strings(self):
  obj={'raw_resource_manifest_sha256':'/private/path','resource_cache_cohort':'private URL','historical_alias_projection':'http://private','historical_fuel_archive_sha256':'x','historical_selected_manifest_sha256':'x','historical_materialized_file_count':True,'historical_cache_verified':'true','raw_resources_match_historical':1,'secret':'secret'}
  out=project(obj,require_runner=False)
  for k in obj:
   if k=='secret':self.assertNotIn(k,out)
   else:self.assertIsNone(out[k])
 def test_raw_pair_exact_boolean_and_hash(self):
  good=self.good()
  for hsh,b in [('2c3acde82c25c0372c8793225abcbc94d4e74be4b441ad130de7c3a475790d66',False),('64f4c3b93af78800aefc036c58035a8556401c82627f55409a16483d12dc7055',True)]:
   row=dict(good,raw_resource_manifest_sha256=hsh,raw_resources_match_historical=b)
   self.assertEqual(project(row,require_runner=False)['state'],'PASS')
   row['raw_resources_match_historical']=not b
   self.assertEqual(project(row,require_runner=False)['state'],'FAIL')

if __name__=='__main__':unittest.main(verbosity=2)

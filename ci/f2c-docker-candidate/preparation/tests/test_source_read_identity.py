"""Pure owned-file and mocked-stat regressions; no installation or Docker."""
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('source_read_preparation',ROOT/'prepare_image.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
spec_b=importlib.util.spec_from_file_location('source_read_bootstrap',ROOT/'bootstrap_tools.py')
b=importlib.util.module_from_spec(spec_b);spec_b.loader.exec_module(b)
FIELDS=('st_dev','st_ino','st_mode','st_nlink','st_uid','st_gid','st_size','st_mtime_ns','st_ctime_ns')

class SourceReadIdentityTests(unittest.TestCase):
    def test_real_old_atime_file_reads_without_false_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'owned';path.write_bytes(b'fixture')
            os.utime(path,ns=(0,path.stat().st_mtime_ns))
            before=path.stat()
            self.assertEqual(p.read_file(path),b'fixture')
            after=path.stat()
            self.assertEqual(tuple(getattr(before,k) for k in FIELDS),tuple(getattr(after,k) for k in FIELDS))
            # Some mounts disable atime: the controlled fixture below proves
            # the atime-only branch even on those filesystems.

    def test_atime_only_allowed_but_each_stable_field_change_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'owned';path.write_bytes(b'fixture')
            base={k:getattr(path.stat(),k) for k in FIELDS}
            base.update(st_atime=0,st_atime_ns=0)
            for field in ('st_atime','st_atime_ns',*FIELDS):
                with self.subTest(field=field):
                    after=dict(base);after[field]+=1
                    with patch.object(Path,'is_symlink',return_value=False), patch.object(Path,'lstat',side_effect=[SimpleNamespace(**base),SimpleNamespace(**after)]):
                        if field.startswith('st_atime'):
                            self.assertEqual(p.read_file(path),b'fixture')
                        else:
                            with self.assertRaisesRegex(RuntimeError,'SOURCE_CHANGED'):p.read_file(path)

    def test_actual_content_change_during_read_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'owned';path.write_bytes(b'fixture');original=Path.read_bytes
            def change(target):
                raw=original(target);target.write_bytes(b'changed-content');return raw
            with patch.object(Path,'read_bytes',change):
                with self.assertRaisesRegex(RuntimeError,'SOURCE_CHANGED'):p.read_file(path)

    def test_actual_identity_replacement_during_read_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'owned';path.write_bytes(b'fixture');original=Path.read_bytes
            def replace(target):
                raw=original(target);other=target.with_name('replacement');other.write_bytes(raw);os.replace(other,target);return raw
            with patch.object(Path,'read_bytes',replace):
                with self.assertRaisesRegex(RuntimeError,'SOURCE_CHANGED'):p.read_file(path)

    def test_post_read_symlink_replacement_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'owned';path.write_bytes(b'fixture');original=Path.read_bytes
            def replace(target):
                raw=original(target);other=target.with_name('replacement');other.write_bytes(raw);target.unlink();target.symlink_to(other.name);return raw
            with patch.object(Path,'read_bytes',replace):
                with self.assertRaisesRegex(RuntimeError,'SOURCE_CHANGED'):p.read_file(path)

class BootstrapReadIdentityTests(unittest.TestCase):
    def test_real_old_atime_collect_retains_digest_and_type_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'uv';path.write_bytes(b'fixture');path.chmod(0o700)
            os.utime(path,ns=(0,path.stat().st_mtime_ns))
            with patch.object(b,'version',return_value='0.12.23'):
                result=b.collect(path,root/'receipt.json')
            self.assertEqual(result,b.sha(root/'receipt.json'))

    def test_atime_only_allowed_and_every_stable_field_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'uv';path.write_bytes(b'fixture');path.chmod(0o700)
            base={k:getattr(path.stat(),k) for k in FIELDS};base.update(st_atime=0,st_atime_ns=0)
            for field in ('st_atime','st_atime_ns',*FIELDS):
                with self.subTest(field=field):
                    after=dict(base);after[field]+=1
                    with patch.object(b,'regular',side_effect=[SimpleNamespace(**base),SimpleNamespace(**after)]), patch.object(b,'version',return_value='0.12.23'):
                        if field.startswith('st_atime'):
                            b.collect(path,root/('receipt-'+field+'.json'))
                        else:
                            with self.assertRaisesRegex(RuntimeError,'BOOTSTRAP_CHANGED'):b.collect(path,root/'unused.json')

    def test_digest_reread_still_rejects_changed_content_with_stable_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'uv';path.write_bytes(b'fixture');path.chmod(0o700)
            with patch.object(b,'sha',side_effect=['a'*64,'b'*64]), patch.object(b,'version',return_value='0.12.23'):
                with self.assertRaisesRegex(RuntimeError,'BOOTSTRAP_CHANGED'):b.collect(path,root/'unused.json')
            self.assertFalse((root/'unused.json').exists())

if __name__=='__main__':unittest.main()

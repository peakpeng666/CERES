"""Temporary-root CA alias fixtures; never mutate or read host CA trust.

All SSL parsing is injected. The only alias ever created points to the fixed
distribution bundle, and tests inspect it with lstat/readlink, never resolve it.
"""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from test_bootstrap_substeps import (
    SYSTEM_CA_SUBSTEP_CODES, V11_SYSTEM_CA_BLOCK,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'preparation' / 'bootstrap' / 'provision_system_ca.py'
spec = importlib.util.spec_from_file_location('fixture_system_ca', SOURCE)
ca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ca)

BUNDLE = 'etc/ssl/certs/ca-certificates.crt'
ALIAS = 'usr/lib/ssl/cert.pem'
RECEIPT = 'opt/urp-bootstrap-evidence/system-ca-default.json'
TARGET = '/etc/ssl/certs/ca-certificates.crt'
PEM = b'-----BEGIN CERTIFICATE-----\nFIXTURE ONLY\n-----END CERTIFICATE-----\n'
DIRECTORIES = ('etc', 'etc/ssl', 'etc/ssl/certs', 'usr', 'usr/lib',
               'usr/lib/ssl', 'opt', 'opt/urp-bootstrap-evidence')


class FakeSSL:
    PROTOCOL_TLS_CLIENT = object()

    def __init__(self, count=2, default='/usr/lib/ssl/cert.pem', error=None):
        self.count, self.default, self.error = count, default, error
        self.loaded = []
        self.protocols = []

    def get_default_verify_paths(self):
        if self.error == 'defaults':
            raise RuntimeError('PRIVATE SSL defaults failure')
        return SimpleNamespace(openssl_cafile=self.default)

    def SSLContext(self, protocol):
        self.protocols.append(protocol)
        if self.error == 'context':
            raise RuntimeError('PRIVATE context failure')
        owner = self

        class Context:
            def load_verify_locations(self, **kwargs):
                if set(kwargs) != {'cadata'} or type(kwargs['cadata']) is not str:
                    raise AssertionError('Tests prohibit native file/path trust reads')
                owner.loaded.append(kwargs['cadata'])
                if owner.error == 'load':
                    raise RuntimeError('PRIVATE certificate parse failure')

            def cert_store_stats(self):
                if owner.error == 'stats':
                    raise RuntimeError('PRIVATE stats failure')
                if owner.error == 'missing_stats':
                    return {}
                return {'x509_ca': owner.count}

        return Context()


class SystemCAProvisionTests(unittest.TestCase):
    def setUp(self):
        clean_environment = mock.patch.dict(os.environ, {}, clear=True)
        clean_environment.start()
        self.addCleanup(clean_environment.stop)
        self.root = self.make_tree()
        self.ssl = FakeSSL()

    def make_tree(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'fixture-root'
        root.mkdir(mode=0o755)
        for relative in DIRECTORIES:
            (root / relative).mkdir(mode=0o755, parents=True, exist_ok=True)
        (root / BUNDLE).write_bytes(PEM)
        (root / BUNDLE).chmod(0o644)
        return root

    def call(self, name, root=None, ssl_api=None, **kwargs):
        return getattr(ca, name)(root=self.root if root is None else root,
                                 owner_uid=os.getuid(), owner_gid=os.getgid(),
                                 ssl_api=self.ssl if ssl_api is None else ssl_api,
                                 **kwargs)

    def rejected(self, code, name='provision', root=None, ssl_api=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(ca.Failure) as caught:
                self.call(name, root=root, ssl_api=ssl_api)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), '')

    def test_absent_alias_is_fixed_link_and_receipt_matches_return_value(self):
        result = self.call('provision')
        alias = self.root / ALIAS
        self.assertTrue(stat.S_ISLNK(alias.lstat().st_mode))
        self.assertEqual(os.readlink(alias), TARGET)
        self.assertEqual(json.loads((self.root / RECEIPT).read_text()), result)
        self.assertEqual(result['alias_action'], 'created')
        self.assertEqual(result['bundle_path'], TARGET)
        self.assertEqual(result['alias_path'], '/' + ALIAS)
        self.assertEqual(result['alias_target'], TARGET)
        self.assertEqual(result['ca_certificates_count'], 2)
        self.assertEqual(self.ssl.loaded, [PEM.decode('ascii')] * 3)
        self.assertEqual(self.ssl.protocols, [self.ssl.PROTOCOL_TLS_CLIENT] * 3)
        self.assertEqual((self.root / BUNDLE).read_bytes(), PEM)

    def test_exact_existing_alias_is_preserved_and_verified(self):
        alias = self.root / ALIAS
        alias.symlink_to(TARGET)
        before = alias.lstat()
        receipt = self.call('provision')
        self.assertEqual(receipt['alias_action'], 'already_correct')
        after = alias.lstat()
        self.assertEqual((before.st_ino, before.st_mtime_ns, before.st_ctime_ns),
                         (after.st_ino, after.st_mtime_ns, after.st_ctime_ns))
        self.assertEqual(os.readlink(alias), TARGET)
        self.assertEqual(self.call('verify'), receipt)

    def test_verify_is_read_only_and_never_follows_alias(self):
        receipt = self.call('provision')
        before = {str(path.relative_to(self.root)): path.lstat()
                  for path in self.root.rglob('*')}
        with mock.patch.object(ca.os, 'symlink', side_effect=AssertionError('verify must not write')):
            self.assertEqual(self.call('verify'), receipt)
        after = {str(path.relative_to(self.root)): path.lstat()
                 for path in self.root.rglob('*')}
        self.assertEqual(set(before), set(after))
        for path in before:
            self.assertEqual((before[path].st_ino, before[path].st_mode,
                              before[path].st_size, before[path].st_mtime_ns,
                              before[path].st_ctime_ns),
                             (after[path].st_ino, after[path].st_mode,
                              after[path].st_size, after[path].st_mtime_ns,
                              after[path].st_ctime_ns))
        self.assertEqual(self.ssl.loaded, [PEM.decode('ascii')] * 4)

    def test_ssl_environment_overrides_rejected_even_when_empty(self):
        for key in ('SSL_CERT_FILE', 'SSL_CERT_DIR'):
            for value in ('', '/PRIVATE/untrusted'):
                with self.subTest(key=key, value=value):
                    with mock.patch.dict(os.environ, {key: value}):
                        self.rejected(71)
                    self.assertFalse((self.root / ALIAS).is_symlink())

    def test_layout_rejects_group_and_world_writable_directories(self):
        for relative in ('', *DIRECTORIES):
            for mode in (0o775, 0o757, 0o777):
                with self.subTest(relative=relative, mode=oct(mode)):
                    root = self.make_tree()
                    (root / relative).chmod(mode)
                    self.rejected(71, root=root)
                    self.assertFalse((root / ALIAS).is_symlink())

    def test_layout_rejects_missing_or_symlink_directory(self):
        for relative in DIRECTORIES:
            with self.subTest(relative=relative):
                root = self.make_tree()
                target = root / relative
                moved = target.with_name(target.name + '.held')
                target.rename(moved)
                self.rejected(71, root=root)
                target.symlink_to(moved, target_is_directory=True)
                self.rejected(71, root=root)

    def test_layout_rejects_wrong_required_owner(self):
        for wrong in ({'owner_uid': os.getuid() + 1}, {'owner_gid': os.getgid() + 1}):
            with self.subTest(wrong=wrong):
                parameters = dict(root=self.root, owner_uid=os.getuid(), owner_gid=os.getgid(),
                                  ssl_api=self.ssl)
                parameters.update(wrong)
                with self.assertRaises(ca.Failure) as caught:
                    ca.provision(**parameters)
                self.assertEqual(caught.exception.code, 71)

    def test_bundle_rejects_missing_empty_oversized_and_directory(self):
        for replacement in (None, b'', b'x' * (4 * 1024 * 1024 + 1), 'directory'):
            with self.subTest(kind=type(replacement).__name__, size=len(replacement) if replacement else 0):
                root = self.make_tree()
                bundle = root / BUNDLE
                bundle.unlink()
                if replacement == 'directory':
                    bundle.mkdir()
                elif replacement is not None:
                    bundle.write_bytes(replacement)
                self.rejected(72, root=root)
                self.assertFalse((root / ALIAS).is_symlink())

    def test_bundle_rejects_symlink_hardlink_and_writable_permissions(self):
        for kind in ('symlink', 'hardlink', 'group_write', 'world_write'):
            with self.subTest(kind=kind):
                root = self.make_tree()
                bundle = root / BUNDLE
                if kind in ('symlink', 'hardlink'):
                    held = bundle.with_name('held-bundle')
                    bundle.rename(held)
                    if kind == 'symlink':
                        bundle.symlink_to(held)
                    else:
                        os.link(held, bundle)
                else:
                    bundle.chmod(0o664 if kind == 'group_write' else 0o646)
                self.rejected(72, root=root)

    def test_bundle_must_decode_and_load_as_usable_ca_data(self):
        (self.root / BUNDLE).write_bytes(b'\xff\xfePRIVATE')
        self.rejected(73)
        for error in ('context', 'load', 'stats', 'missing_stats'):
            with self.subTest(error=error):
                root = self.make_tree()
                self.rejected(73, root=root, ssl_api=FakeSSL(error=error))
        for count in (0, -1, 10001, None, '2', True):
            with self.subTest(count=count):
                self.rejected(73, root=self.make_tree(), ssl_api=FakeSSL(count=count))

    def test_compiled_default_must_be_exact_and_no_alias_is_written_on_rejection(self):
        for default in (None, '', '/etc/ssl/certs/ca-certificates.crt', '/PRIVATE/cert.pem'):
            with self.subTest(default=default):
                root = self.make_tree()
                self.rejected(74, root=root, ssl_api=FakeSSL(default=default))
                self.assertFalse((root / ALIAS).is_symlink())
        self.rejected(74, ssl_api=FakeSSL(error='defaults'))

    def test_alias_conflicts_are_never_overwritten(self):
        for kind in ('file', 'directory', 'relative_link', 'other_link', 'dangling_link'):
            with self.subTest(kind=kind):
                root = self.make_tree()
                alias = root / ALIAS
                if kind == 'file':
                    alias.write_bytes(b'PRIVATE existing file')
                elif kind == 'directory':
                    alias.mkdir()
                else:
                    alias.symlink_to({'relative_link': '../../../etc/ssl/certs/ca-certificates.crt',
                                      'other_link': '/PRIVATE/elsewhere',
                                      'dangling_link': '/missing/target'}[kind])
                before = alias.lstat()
                target = os.readlink(alias) if alias.is_symlink() else None
                self.rejected(75, root=root)
                after = alias.lstat()
                self.assertEqual((before.st_ino, before.st_mode, before.st_mtime_ns, before.st_ctime_ns),
                                 (after.st_ino, after.st_mode, after.st_mtime_ns, after.st_ctime_ns))
                if target is not None:
                    self.assertEqual(os.readlink(alias), target)
                self.assertFalse((root / RECEIPT).exists())

    def test_alias_creation_failure_is_fixed_code_and_no_receipt(self):
        with mock.patch.object(ca.os, 'symlink', side_effect=OSError('PRIVATE failure')):
            self.rejected(76)
        self.assertFalse((self.root / RECEIPT).exists())

    def test_existing_receipt_is_not_overwritten(self):
        receipt = self.root / RECEIPT
        receipt.write_bytes(b'PRIVATE prior receipt')
        before = receipt.read_bytes()
        self.rejected(77)
        self.assertEqual(receipt.read_bytes(), before)

    def test_verify_requires_receipt_and_alias_without_creating_either(self):
        self.rejected(75, name='verify')
        self.assertFalse((self.root / RECEIPT).exists())
        self.assertFalse((self.root / ALIAS).is_symlink())
        (self.root / ALIAS).symlink_to(TARGET)
        self.rejected(77, name='verify')
        (self.root / ALIAS).unlink()
        self.call('provision')
        (self.root / ALIAS).unlink()
        self.rejected(75, name='verify')
        self.assertFalse((self.root / ALIAS).is_symlink())

    def test_verify_detects_changed_bundle_count_and_alias(self):
        self.call('provision')
        (self.root / BUNDLE).write_bytes(PEM + b'changed\n')
        self.rejected(77, name='verify')
        (self.root / BUNDLE).write_bytes(PEM)
        self.rejected(77, name='verify', ssl_api=FakeSSL(count=3))
        (self.root / ALIAS).unlink()
        (self.root / ALIAS).symlink_to('/PRIVATE/replacement')
        self.rejected(75, name='verify')

    def test_verify_rejects_malformed_duplicate_and_extra_receipt_data(self):
        result = self.call('provision')
        receipt = self.root / RECEIPT
        for raw in (b'{', b'[]', b'null', b'x' * (16 * 1024 + 1),
                    json.dumps(dict(result, raw='PRIVATE')).encode(),
                    ('{"schema":1,' + json.dumps(result)[1:]).encode()):
            with self.subTest(prefix=raw[:24]):
                receipt.write_bytes(raw)
                self.rejected(77, name='verify')

    def test_held_file_stability_ignores_atime_but_rejects_all_security_changes(self):
        bundle = self.root / BUNDLE
        original = ca.os.fstat
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_uid', 'st_gid',
                  'st_size', 'st_mtime_ns', 'st_ctime_ns')

        def changed(info, field):
            values = {name: getattr(info, name) for name in (*fields, 'st_atime_ns')}
            values[field] += 1
            return SimpleNamespace(**values)

        for field in fields:
            calls = []

            def fstat(fd):
                info = original(fd)
                calls.append(fd)
                return changed(info, field) if len(calls) == 2 else info

            with self.subTest(field=field), mock.patch.object(ca.os, 'fstat', side_effect=fstat):
                with self.assertRaises(ca.Failure) as caught:
                    ca.read_regular(bundle, ca.MAX_BUNDLE, os.getuid(), os.getgid(), 72)
                self.assertEqual(caught.exception.code, 72)
        with mock.patch.object(ca.os, 'fstat',
                               side_effect=lambda fd: changed(original(fd), 'st_atime_ns')):
            self.assertEqual(ca.read_regular(bundle, ca.MAX_BUNDLE,
                                             os.getuid(), os.getgid(), 72), PEM)

    def test_bundle_change_during_ssl_validation_is_detected_before_alias_write(self):
        original = self.ssl.SSLContext

        def context(protocol):
            value = original(protocol)
            load = value.load_verify_locations

            def changing_load(**kwargs):
                load(**kwargs)
                (self.root / BUNDLE).write_bytes(PEM + b'changed\n')

            value.load_verify_locations = changing_load
            return value

        with mock.patch.object(self.ssl, 'SSLContext', side_effect=context):
            self.rejected(72)
        self.assertFalse((self.root / ALIAS).is_symlink())
        self.assertFalse((self.root / RECEIPT).exists())

    def test_receipt_schema_values_and_types_are_exact(self):
        record = self.call('provision')
        receipt = self.root / RECEIPT
        mutations = {
            'schema': (True, 1.0, '1', 2),
            'status': ('accepted',),
            'provenance': ('archive-owned',),
            'alias_action': ('overwrite', False),
            'bundle_path': ('/PRIVATE/bundle',),
            'bundle_sha256': ('0' * 64,),
            'bundle_bytes': (True, float(len(PEM)), len(PEM) + 1),
            'ca_certificates_count': (True, 2.0, 3),
            'alias_path': ('/PRIVATE/cert.pem',),
            'alias_target': ('/PRIVATE/target',),
        }
        self.assertEqual(set(record), set(mutations))
        for key, replacements in mutations.items():
            for value in replacements:
                with self.subTest(key=key, value=value):
                    receipt.write_text(json.dumps(dict(record, **{key: value})))
                    self.rejected(77, name='verify')
            with self.subTest(missing=key):
                receipt.write_text(json.dumps({k: v for k, v in record.items() if k != key}))
                self.rejected(77, name='verify')

    def test_receipt_link_and_permission_violations_are_rejected(self):
        for kind in ('symlink', 'hardlink', 'group_write', 'world_write', 'empty'):
            with self.subTest(kind=kind):
                root = self.make_tree()
                self.call('provision', root=root)
                receipt = root / RECEIPT
                if kind in ('symlink', 'hardlink'):
                    held = receipt.with_name('held-receipt')
                    receipt.rename(held)
                    if kind == 'symlink':
                        receipt.symlink_to(held)
                    else:
                        os.link(held, receipt)
                elif kind == 'empty':
                    receipt.write_bytes(b'')
                else:
                    receipt.chmod(0o664 if kind == 'group_write' else 0o646)
                self.rejected(77, name='verify', root=root)

    def test_provision_does_not_replace_a_preexisting_dangling_receipt_link(self):
        receipt = self.root / RECEIPT
        receipt.symlink_to('/PRIVATE/missing-receipt')
        before = receipt.lstat()
        self.rejected(77)
        self.assertEqual(os.readlink(receipt), '/PRIVATE/missing-receipt')
        self.assertEqual(receipt.lstat().st_ino, before.st_ino)
        self.assertFalse((self.root / ALIAS).is_symlink())

    def test_cli_requires_explicit_mode_and_emits_only_fixed_exit_codes(self):
        for arguments in ([], ['--verify'], ['--install-default-alias', 'extra']):
            with self.subTest(arguments=arguments):
                with mock.patch.object(ca.sys, 'argv', ['provision_system_ca.py', *arguments]):
                    with mock.patch.object(ca, 'provision') as provision:
                        self.assertEqual(ca.main(), 78)
                        provision.assert_not_called()
        with mock.patch.object(ca.sys, 'argv', ['provision_system_ca.py', '--install-default-alias']):
            for code in range(71, 79):
                with self.subTest(code=code), mock.patch.object(ca, 'provision',
                                                              side_effect=ca.Failure(code)):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        self.assertEqual(ca.main(), code)
                    self.assertEqual(stdout.getvalue(), '')
                    self.assertEqual(stderr.getvalue(), '')
            with mock.patch.object(ca, 'provision', side_effect=ValueError('PRIVATE')):
                self.assertEqual(ca.main(), 78)
            with mock.patch.object(ca, 'provision', return_value={}):
                self.assertEqual(ca.main(), 0)
            with mock.patch.object(ca, 'provision', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    ca.main()

    def test_extracted_system_ca_dispatch_exhausts_exit_codes(self):
        source = (ROOT / 'preparation/bootstrap/bootstrap.sh').read_text()
        self.assertEqual(source.count(V11_SYSTEM_CA_BLOCK), 1)
        self.assertIn(V11_SYSTEM_CA_BLOCK + 'substep=python_prerequisites\n', source)
        line = V11_SYSTEM_CA_BLOCK.splitlines()[2]
        self.assertEqual(shlex.split(line), [
            '/usr/bin/python3', '-I', '-B', '$HELPERS/provision_system_ca.py',
            '--install-default-alias', '>', '$OUT/logs/system-ca-default.log',
            '2>&1', '||', 'system_ca_result=$?',
        ])
        dispatch = 'case "$system_ca_result" in\n' + V11_SYSTEM_CA_BLOCK.split(
            'case "$system_ca_result" in\n', 1)[1]
        for code in range(256):
            with self.subTest(code=code):
                shell = '\n'.join((
                    'substep=system_ca_defaults', 'system_ca_result=' + str(code),
                    'fail() { printf "SAFE_BOOTSTRAP_DIAGNOSTIC:BLOCKED_post_install_verification__%s\\n" "$substep" >&2; exit 1; }',
                    dispatch, 'exit 0', '',
                ))
                result = subprocess.run(['/bin/sh', '-c', shell], cwd=self.root,
                                        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                                        capture_output=True, text=True, timeout=5, check=False)
                self.assertEqual(result.stdout, '')
                self.assertEqual(result.returncode, 0 if code == 0 else 1)
                label = SYSTEM_CA_SUBSTEP_CODES.get(code, 'system_ca_unknown')
                self.assertEqual(result.stderr, '' if code == 0 else
                                 'SAFE_BOOTSTRAP_DIAGNOSTIC:BLOCKED_post_install_verification__'
                                 + label + '\n')
        self.assertFalse((self.root / ALIAS).is_symlink())
        self.assertFalse((self.root / RECEIPT).exists())


if __name__ == '__main__':
    unittest.main()

"""Pure fixtures and anonymous memfd data only; no gpg/helper/auth invocation."""
import copy
import fcntl
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'keyring_input.py'
SPEC = importlib.util.spec_from_file_location('private_keyring_fixture', SOURCE)
K = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(K)
PREP_SPEC = importlib.util.spec_from_file_location('private_keyring_adapter_fixture', SOURCE.with_name('prepare_capability_inputs.py'))
P = importlib.util.module_from_spec(PREP_SPEC)
PREP_SPEC.loader.exec_module(P)
DATA = b'exact-public-keyring-data-for-hermetic-fixtures'


class KeyringFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.full_seals = K.seal_capabilities()['seals']
        (self.root / 'inputs').mkdir(mode=0o700)
        self.source = self.root / K.SOURCE_PATH
        self.source.write_bytes(DATA)
        self.source.chmod(0o600)
        for name, value in (('SHA256', hashlib.sha256(DATA).hexdigest()), ('SIZE', len(DATA))):
            patcher = mock.patch.object(K, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fails(self, code, operation, *args):
        with self.assertRaises(K.KeyringError) as caught:
            operation(*args)
        self.assertEqual(str(caught.exception), code)
        self.assertEqual(caught.exception.code, code)

    def held(self):
        held = K.create(self.root)
        self.addCleanup(held.close)
        return held

    def test_missing_required_seal_capability_rejected_before_io_or_verifier(self):
        required = [(K.fcntl, name) for name in ('F_ADD_SEALS', 'F_GET_SEALS', 'F_SEAL_SEAL',
                    'F_SEAL_SHRINK', 'F_SEAL_GROW', 'F_SEAL_WRITE', 'fcntl')]
        required.extend((K.os, name) for name in ('memfd_create', 'MFD_CLOEXEC', 'MFD_ALLOW_SEALING'))
        for namespace, name in required:
            with self.subTest(name=name), mock.patch.object(namespace, name):
                delattr(namespace, name)
                with mock.patch.object(K.os, 'mkdir') as mkdir, \
                     mock.patch.object(K.os, 'open') as opening, \
                     mock.patch.object(P.subprocess, 'run') as run:
                    self.fails('CAP_KEYRING_SEAL_REJECTED', K.create, self.root)
                mkdir.assert_not_called()
                opening.assert_not_called()
                run.assert_not_called()
        self.assertFalse((self.root / K.PRIVATE_DIRECTORY).exists())

    def test_module_load_defers_unavailable_seal_api(self):
        with mock.patch.object(K.fcntl, 'F_SEAL_SEAL'):
            delattr(K.fcntl, 'F_SEAL_SEAL')
            fresh_spec = importlib.util.spec_from_file_location('missing_seals_fixture', SOURCE)
            fresh = importlib.util.module_from_spec(fresh_spec)
            fresh_spec.loader.exec_module(fresh)
            with self.assertRaises(fresh.KeyringError) as caught:
                fresh.create(self.root)
        self.assertEqual(caught.exception.code, 'CAP_KEYRING_SEAL_REJECTED')
        self.assertFalse((self.root / K.PRIVATE_DIRECTORY).exists())

    def test_private_copy_sealed_exact_and_manifest_separate_from_package(self):
        held = self.held()
        self.assertEqual((self.root / K.PRIVATE_PATH).read_bytes(), DATA)
        self.assertEqual((self.root / K.PRIVATE_PATH).stat().st_mode & 0o7777, 0o600)
        self.assertEqual((self.root / K.PRIVATE_DIRECTORY).stat().st_mode & 0o7777, 0o700)
        self.assertEqual(os.pread(held.fd, len(DATA) + 1, 0), DATA)
        self.assertEqual(fcntl.fcntl(held.fd, fcntl.F_GET_SEALS), self.full_seals)
        self.assertFalse(os.get_inheritable(held.fd))
        self.assertEqual(os.lseek(held.fd, 0, os.SEEK_CUR), 0)
        self.assertEqual(K.verify_manifest(self.root, held.manifest), DATA)
        self.assertNotIn('package', held.manifest)
        for operation in (lambda: os.write(held.fd, b'x'),
                          lambda: os.ftruncate(held.fd, 0),
                          lambda: os.ftruncate(held.fd, len(DATA) + 1)):
            with self.assertRaises(OSError):
                operation()
        argv, fds = held.command('jammy')
        self.assertEqual(fds, (held.fd,))
        self.assertEqual(argv[3], '/proc/self/fd/' + str(held.fd))

    def test_context_closes_on_success_and_exception(self):
        held = self.held()
        fd = held.fd
        with held:
            self.assertGreater(fd, 2)
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.fails('CAP_KEYRING_FD_REJECTED', held.check_fd)
        second = self.root / 'second'
        second.mkdir(mode=0o700)
        (second / 'inputs').mkdir(mode=0o700)
        (second / K.SOURCE_PATH).write_bytes(DATA)
        (second / K.SOURCE_PATH).chmod(0o600)
        with self.assertRaises(RuntimeError):
            with K.create(second) as other:
                other_fd = other.fd
                raise RuntimeError('fixture')
        with self.assertRaises(OSError):
            os.fstat(other_fd)

    def test_no_host_permissions_repaired(self):
        with mock.patch.object(K.os, 'chmod', side_effect=AssertionError('chmod')), \
             mock.patch.object(K.os, 'chown', side_effect=AssertionError('chown')), \
             mock.patch.object(K.os, 'fchmod', side_effect=AssertionError('fchmod')), \
             mock.patch.object(K.os, 'fchown', side_effect=AssertionError('fchown')):
            self.held()

    def test_private_destination_is_exclusive(self):
        held = self.held()
        self.fails('CAP_KEYRING_PATH_REJECTED', K.create, self.root)
        self.assertEqual(K.verify_manifest(self.root, held.manifest), DATA)

    def test_source_tamper_and_wrong_size(self):
        self.source.write_bytes(b'x' * len(DATA))
        self.fails('CAP_KEYRING_SOURCE_REJECTED', K.create, self.root)
        self.source.write_bytes(DATA + b'x')
        self.fails('CAP_KEYRING_PATH_REJECTED', K.create, self.root)
        self.assertFalse((self.root / K.PRIVATE_DIRECTORY).exists())

    def test_missing_source(self):
        self.source.unlink()
        self.fails('CAP_KEYRING_PATH_REJECTED', K.create, self.root)

    def test_source_symlink_and_hardlink(self):
        target = self.root / 'target'
        self.source.rename(target)
        self.source.symlink_to(target)
        self.fails('CAP_KEYRING_PATH_REJECTED', K.create, self.root)
        self.source.unlink()
        os.link(target, self.source)
        self.fails('CAP_KEYRING_PATH_REJECTED', K.create, self.root)

    def test_source_directory_symlink(self):
        (self.root / 'inputs').rename(self.root / 'other')
        (self.root / 'inputs').symlink_to('other', target_is_directory=True)
        self.fails('CAP_KEYRING_PATH_REJECTED', K.create, self.root)

    def test_root_alias_and_path_escape(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        self.fails('CAP_KEYRING_PATH_REJECTED', K.create, alias)
        for root in (str(self.root) + '/../' + self.root.name,
                     str(self.root) + '/.', '/' + str(self.root), 'relative'):
            self.fails('CAP_KEYRING_PATH_REJECTED', K.create, root)
        self.fails('CAP_KEYRING_PATH_REJECTED', K._read, self.root, '../outside')

    def test_private_owner_and_modes_strict(self):
        for path, invalid_modes, correct in ((self.root, (0o750, 0o777), 0o700),
                (self.root / 'inputs', (0o750, 0o720, 0o777), 0o700),
                (self.source, (0o644, 0o660, 0o700, 0o4600), 0o600)):
            for mode in invalid_modes:
                path.chmod(mode)
                self.fails('CAP_KEYRING_PATH_REJECTED', K.create, self.root)
                path.chmod(correct)
        with mock.patch.object(K.os, 'getuid', return_value=os.getuid() + 1):
            self.fails('CAP_KEYRING_PATH_REJECTED', K.create, self.root)
        with mock.patch.object(K.os, 'getgid', return_value=os.getgid() + 1):
            self.fails('CAP_KEYRING_PATH_REJECTED', K.create, self.root)

    def test_fifo_source_never_blocks(self):
        self.source.unlink()
        os.mkfifo(self.source, 0o600)
        self.fails('CAP_KEYRING_PATH_REJECTED', K.create, self.root)

    def test_copy_tamper_swap_hardlink_and_manifest_change(self):
        held = self.held()
        target = self.root / K.PRIVATE_PATH
        for key, value in (('schema', True), ('signer', '0' * 40), ('path', '../escape'),
                           ('sha256', '0' * 64), ('size', len(DATA) + 1), ('package', 'ubuntu-keyring')):
            changed = copy.deepcopy(held.manifest)
            changed[key] = value
            self.fails('CAP_KEYRING_STATE_CHANGED', K.verify_manifest, self.root, changed)
        changed = copy.deepcopy(held.manifest)
        changed['file_identity']['nlink'] = True
        self.fails('CAP_KEYRING_STATE_CHANGED', K.verify_manifest, self.root, changed)
        target.write_bytes(b'x' * len(DATA))
        self.fails('CAP_KEYRING_SOURCE_REJECTED', held.command, 'jammy')
        # Sealed child data remains exact, but mutable-copy drift is terminal.
        self.assertEqual(os.pread(held.fd, len(DATA), 0), DATA)
        target.write_bytes(DATA)
        self.fails('CAP_KEYRING_STATE_CHANGED', held.command, 'jammy')
        target.unlink()
        target.write_bytes(DATA)
        target.chmod(0o600)
        self.fails('CAP_KEYRING_STATE_CHANGED', held.command, 'jammy')
        os.link(target, self.root / 'second-copy')
        self.fails('CAP_KEYRING_PATH_REJECTED', held.command, 'jammy')

    def test_source_replacement_after_copy_rejected(self):
        held = self.held()
        self.source.write_bytes(b'x' * len(DATA))
        self.fails('CAP_KEYRING_SOURCE_REJECTED', held.command, 'jammy')

    def test_check_read_name_swap_detected(self):
        real_pread = K.os.pread
        changed = [False]
        def read_and_swap(fd, count, offset):
            raw = real_pread(fd, count, offset)
            if not changed[0]:
                changed[0] = True
                self.source.rename(self.root / 'old-source')
                self.source.write_bytes(DATA)
                self.source.chmod(0o600)
            return raw
        with mock.patch.object(K.os, 'pread', side_effect=read_and_swap):
            self.fails('CAP_KEYRING_STATE_CHANGED', K.create, self.root)

    def test_directory_replacement_during_read_detected(self):
        real_pread = K.os.pread
        changed = [False]
        def read_and_swap(fd, count, offset):
            raw = real_pread(fd, count, offset)
            if not changed[0]:
                changed[0] = True
                (self.root / 'inputs').rename(self.root / 'old-inputs')
                (self.root / 'inputs').mkdir(mode=0o700)
                self.source.write_bytes(DATA)
                self.source.chmod(0o600)
            return raw
        with mock.patch.object(K.os, 'pread', side_effect=read_and_swap):
            self.fails('CAP_KEYRING_STATE_CHANGED', K.create, self.root)

    def test_missing_seal_rejected_and_no_descriptor_leak(self):
        real_fcntl = K.fcntl.fcntl
        fds = []
        real_memfd = K.os.memfd_create
        def create(*args):
            fd = real_memfd(*args)
            fds.append(fd)
            return fd
        def missing(fd, operation, *args):
            if operation == fcntl.F_GET_SEALS:
                return self.full_seals & ~fcntl.F_SEAL_WRITE
            return real_fcntl(fd, operation, *args)
        with mock.patch.object(K.os, 'memfd_create', side_effect=create), \
             mock.patch.object(K.fcntl, 'fcntl', side_effect=missing):
            self.fails('CAP_KEYRING_SEAL_REJECTED', K.create, self.root)
        self.assertEqual(len(fds), 1)
        with self.assertRaises(OSError):
            os.fstat(fds[0])

    def test_each_required_seal_and_cloexec_rechecked(self):
        held = self.held()
        real_fcntl = K.fcntl.fcntl
        for bit in (fcntl.F_SEAL_SEAL, fcntl.F_SEAL_SHRINK, fcntl.F_SEAL_GROW, fcntl.F_SEAL_WRITE):
            def missing(fd, operation, *args):
                return self.full_seals & ~bit if operation == fcntl.F_GET_SEALS else real_fcntl(fd, operation, *args)
            with mock.patch.object(K.fcntl, 'fcntl', side_effect=missing):
                self.fails('CAP_KEYRING_SEAL_REJECTED', held.check_fd)
        os.set_inheritable(held.fd, True)
        self.fails('CAP_KEYRING_FD_REJECTED', held.check_fd)
        os.set_inheritable(held.fd, False)

    def test_closed_or_replaced_fd_rejected(self):
        held = self.held()
        old_fd = held.fd
        os.close(old_fd)
        self.fails('CAP_KEYRING_FD_REJECTED', held.check_fd)
        other = os.memfd_create('replacement-fixture', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            os.write(other, DATA)
            fcntl.fcntl(other, fcntl.F_ADD_SEALS, self.full_seals)
            if other != old_fd:
                os.dup2(other, old_fd, inheritable=False)
            self.fails('CAP_KEYRING_FD_REJECTED', held.check_fd)
        finally:
            held.close()
            if other != old_fd:
                os.close(other)

    def test_default_extra_keyring_and_wrong_fd_or_release_rejected(self):
        held = self.held()
        argv, unused = held.command('jammy')
        variants = (argv[:2] + argv[4:], argv[:4] + ['--keyring', '/tmp/other'] + argv[4:],
                    argv[:3] + ['/usr/share/keyrings/ubuntu-archive-keyring.gpg'] + argv[4:],
                    argv[:3] + ['/proc/self/fd/0'] + argv[4:],
                    argv[:3] + ['/proc/self/fd/0' + str(held.fd)] + argv[4:],
                    argv[:3] + ['/proc/self/fd/' + str(held.fd + 1)] + argv[4:],
                    argv[:3] + ['/dev/fd/' + str(held.fd)] + argv[4:],
                    argv[:4] + ['/tmp/jammy.InRelease'],
                    ['/bin/sh'] + argv[1:], argv + ['--no-default-keyring'])
        for variant in variants:
            self.fails('CAP_KEYRING_COMMAND_REJECTED', held.command_fds, variant)
        self.fails('CAP_KEYRING_COMMAND_REJECTED', held.command, 'jammy/../other')

    def test_adapter_passes_only_live_fd_and_clean_environment(self):
        held = self.held()
        result = types.SimpleNamespace(returncode=0, stdout=b'[GNUPG:] NEWSIG\n', stderr=b'private')
        with mock.patch.object(P.subprocess, 'run', return_value=result) as run:
            self.assertEqual(P.signature_command(K, held, 'jammy', self.root), (0, result.stdout))
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs['pass_fds'], (held.fd,))
        self.assertTrue(kwargs['close_fds'])
        self.assertEqual(set(kwargs['env']), {'PATH', 'LC_ALL', 'LANG', 'HOME'})
        self.assertEqual(run.call_args.args[0], held.command('jammy')[0])
        self.assertFalse(os.get_inheritable(held.fd))

    def test_adapter_post_command_recheck_rejects_private_drift(self):
        held = self.held()
        def command(*args, **kwargs):
            (self.root / K.PRIVATE_PATH).write_bytes(b'x' * len(DATA))
            return types.SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        with mock.patch.object(P.subprocess, 'run', side_effect=command):
            with self.assertRaises(P.PreparationError) as caught:
                P.signature_command(K, held, 'jammy', self.root)
        self.assertEqual(caught.exception.code, 'CAP_KEYRING_SOURCE_REJECTED')

    def preparation_mocks(self, load_metadata, inventory):
        import contextlib
        stack = contextlib.ExitStack()
        module = mock.Mock()
        module.os_values.return_value = {'ID': 'ubuntu', 'VERSION_ID': '22.04'}
        module.inventory.side_effect = inventory
        patches = (
            mock.patch.object(P, 'source_anchor', return_value={}),
            mock.patch.object(P.platform, 'machine', return_value='x86_64'),
            mock.patch.object(P.sys, 'version_info', (3, 10)),
            mock.patch.object(P.sys, 'executable', P.TOOLS['python']),
            mock.patch.object(P, 'runtime_flags', return_value=str(self.root / 'unused-pycache')),
            mock.patch.object(P, 'metadata_module', return_value=module),
            mock.patch.object(P, 'keyring_module', return_value=K),
            mock.patch.object(P.Path, 'read_text', return_value='0' * 36),
            mock.patch.object(P.Path, 'is_file', return_value=True),
            mock.patch.object(P.os.path, 'lexists', return_value=False),
            mock.patch.object(P, 'measure', return_value=({}, b'')),
            mock.patch.object(P, 'loginuid_value', return_value='4294967295'),
            mock.patch.object(P, 'load_metadata', side_effect=load_metadata),
        )
        for patch in patches:
            stack.enter_context(patch)
        return stack

    def test_prepare_closes_fd_before_inventory_and_authentication(self):
        observed = []
        def metadata(root, module, out, keyring, held):
            observed.append(held.fd)
            self.assertEqual(os.pread(held.fd, len(DATA), 0), DATA)
            return {}, {}
        def inventory(*args):
            with self.assertRaises(OSError):
                os.fstat(observed[0])
            raise RuntimeError('stop before authentication')
        with self.preparation_mocks(metadata, inventory), mock.patch.object(P, 'Authenticator') as auth:
            with self.assertRaisesRegex(RuntimeError, 'stop before authentication'):
                P.prepare(self.root, '0' * 64)
        auth.assert_not_called()
        self.assertEqual(len(observed), 1)

    def test_prepare_metadata_failure_closes_fd_without_later_authentication(self):
        observed = []
        def metadata(root, module, out, keyring, held):
            observed.append(held.fd)
            raise P.PreparationError('CAP_METADATA_REJECTED')
        with self.preparation_mocks(metadata, AssertionError('inventory must not run')), \
             mock.patch.object(P, 'Authenticator') as auth:
            with self.assertRaises(P.PreparationError) as caught:
                P.prepare(self.root, '0' * 64)
        auth.assert_not_called()
        self.assertEqual(caught.exception.code, 'CAP_METADATA_REJECTED')
        self.assertEqual(len(observed), 1)
        with self.assertRaises(OSError):
            os.fstat(observed[0])

    def test_host_keyring_is_not_a_host_or_command_exemption(self):
        path = '/usr/share/keyrings/ubuntu-archive-keyring.gpg'
        self.assertNotIn(path, P.HOST_FILES)
        with mock.patch.object(P.subprocess, 'run') as run:
            with self.assertRaises(P.PreparationError):
                P.command(['/usr/bin/gpgv', '--status-fd=1', '--keyring', path, '/tmp/jammy.InRelease'], self.root, 'test')
        run.assert_not_called()


class SignatureStatusFixtures(unittest.TestCase):
    def valid(self, signer=None, primary=None):
        line = ('[GNUPG:] VALIDSIG ' + (signer or P.KEYRING_SIGNER) +
                ' 2026-10-21 1792540800 0 4 0 1 10 01')
        if primary is not None:
            line += ' ' + primary
        return (line + '\n').encode()

    def test_complete_direct_fingerprint_line(self):
        self.assertTrue(P.valid_signature_status(self.valid()))
        self.assertTrue(P.valid_signature_status(b'[GNUPG:] NEWSIG\n' + self.valid(primary=P.KEYRING_SIGNER)))

    def test_unapproved_signer_primary_fallback_embedded_and_malformed_rejected(self):
        for raw in (self.valid('A' * 40), self.valid('A' * 40, P.KEYRING_SIGNER),
                    b'UID prefix ' + self.valid(), b'[GNUPG:] GOODSIG ' + self.valid(),
                    b'[GNUPG:] VALIDSIG ' + P.KEYRING_SIGNER.encode() + b' \n',
                    self.valid().replace(P.KEYRING_SIGNER.encode(), P.KEYRING_SIGNER[-16:].encode()),
                    self.valid() + self.valid(), self.valid() + b'[GNUPG:] BADSIG 123\n',
                    self.valid().replace(b' 10 ', b' nan '), self.valid().replace(b' 01\n', b' xyz\n'),
                    b'x' * (1024 * 1024 + 1), b'[GNUPG:] ' + b'x' * 4096, b''):
            self.assertFalse(P.valid_signature_status(raw), repr(raw[:100]))


if __name__ == '__main__':
    unittest.main()

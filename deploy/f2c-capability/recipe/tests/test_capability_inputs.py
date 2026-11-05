"""Hermetic data fixtures only: no host preparation, helper, or namespace call."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import tarfile
import tempfile
import types
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'prepare_capability_inputs.py'
SPEC = importlib.util.spec_from_file_location('capability_inputs_tests_module', SOURCE)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def tar_data(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for name, content, kind, mode in entries:
            member = tarfile.TarInfo(name)
            member.mode = mode
            if kind == 'file':
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            else:
                member.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.FIFOTYPE
                member.linkname = content
                archive.addfile(member)
    return output.getvalue()


def elf_data(names=('libc.so.6',), interp=True, extra_tag=None):
    data = bytearray(1024)
    data[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', data, 18, 62)
    struct.pack_into('<Q', data, 32, 64)
    struct.pack_into('<HH', data, 54, 56, 3 if interp else 2)
    strings = b'\0' + b''.join(name.encode() + b'\0' for name in names)
    data[512:512 + len(strings)] = strings
    dynamic = [(5, 0x400200), (10, len(strings))]
    cursor = 1
    for name in names:
        dynamic.append((1, cursor))
        cursor += len(name) + 1
    if extra_tag is not None:
        dynamic.append((extra_tag, 0))
    dynamic.append((0, 0))
    for i, pair in enumerate(dynamic):
        struct.pack_into('<qQ', data, 256 + 16 * i, *pair)
    struct.pack_into('<IIQQQQQQ', data, 64, 1, 5, 0, 0x400000, 0, len(data), len(data), 4096)
    struct.pack_into('<IIQQQQQQ', data, 120, 2, 4, 256, 0x400100, 0, 16 * len(dynamic), 16 * len(dynamic), 8)
    if interp:
        interpreter = b'/lib64/ld-linux-x86-64.so.2\0'
        data[768:768 + len(interpreter)] = interpreter
        struct.pack_into('<IIQQQQQQ', data, 176, 3, 4, 768, 0x400300, 0, len(interpreter), len(interpreter), 1)
    return bytes(data)


def cache_data(entries=(('libc.so.6', '/lib/x86_64-linux-gnu/libc.so.6'),)):
    data = bytearray(48 + 24 * len(entries))
    data[:20] = b'glibc-ld.so.cache1.1'
    strings = bytearray()
    for i, (name, path) in enumerate(entries):
        key = len(data) + len(strings)
        strings.extend(name.encode() + b'\0')
        value = len(data) + len(strings)
        strings.extend(path.encode() + b'\0')
        struct.pack_into('<iIIIQ', data, 48 + i * 24, 0x303, key, value, 0, 0)
    struct.pack_into('<II', data, 20, len(entries), len(strings))
    data[28] = 2
    return bytes(data + strings)


def alias_record(path, target):
    return {'path': path, 'target': target, 'uid': 0, 'gid': 0, 'mode': 0o777,
            'device': 1, 'inode': 3}


def record(path='/usr/bin/newuidmap', content=b'authentic', mode=0o4755):
    return {'path': path, 'resolved_path': path, 'sha256': M.digest(content),
            'uid': 0, 'gid': 0, 'mode': mode, 'device': 1, 'inode': 2,
            'size': len(content), 'nlink': 1, 'aliases': []}


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fails(self, code, function, *args, **kwargs):
        with self.assertRaises(M.PreparationError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.code, code)
        self.assertEqual(str(error.exception), code)

    def sources(self):
        paths = ('recipe/prepare_capability_inputs.py', 'recipe/jammy_metadata.py',
                 'recipe/probe_namespace_capabilities.py', 'recipe/keyring_input.py',
                 M.KEYRING_SOURCE_PATH, M.KEYRING_PROVENANCE_PATH)
        (self.root / 'recipe').mkdir()
        files = {}
        for name, value in (('KEYRING_SOURCE_SHA256', M.digest(M.KEYRING_SOURCE_PATH.encode())),
                            ('KEYRING_SOURCE_SIZE', len(M.KEYRING_SOURCE_PATH.encode()))):
            patcher = mock.patch.object(M, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for path in paths:
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_bytes(path.encode())
            files[path] = {'sha256': M.digest(path.encode())}
        index = {'schema': 1, 'status': 'reviewed-source', 'files': files}
        raw = json.dumps(index).encode()
        (self.root / 'source-index.json').write_bytes(raw)
        return index, M.digest(raw), files['recipe/jammy_metadata.py']['sha256']

    def test_error_codes_are_finite_and_do_not_echo_detail(self):
        self.fails('CAP_OWNER_REJECTED', M.need, False, 'CAP_OWNER_REJECTED')
        with self.assertRaises(ValueError):
            M.PreparationError('secret-package-name')

    def test_success_cannot_be_an_error(self):
        with self.assertRaises(ValueError):
            M.PreparationError('CAP_INPUTS_VERIFIED')

    def test_fixed_tools_have_specific_failure_codes(self):
        for name in M.TOOLS:
            for suffix in ('MISSING', 'PACKAGE_UNAUTHENTICATED', 'OWNER_REJECTED', 'BYTES_REJECTED', 'MODE_REJECTED'):
                self.assertIn('CAP_' + name.upper() + '_' + suffix, M.PREPARATION_CODES)

    def test_json_duplicate_key_rejected(self):
        self.fails('CAP_LOCK_REJECTED', M.unique_json, b'{"schema":1,"schema":1}')

    def test_measure_regular_bytes(self):
        p = self.root / 'file'
        p.write_bytes(b'authentic')
        row, data = M.measure(p, root_owned=False)
        self.assertEqual(data, b'authentic')
        self.assertEqual(row['sha256'], M.digest(data))
        self.assertEqual(set(row), M.FILE_KEYS)

    def test_measure_alias_is_recorded(self):
        target = self.root / 'file'
        target.write_bytes(b'authentic')
        alias = self.root / 'alias'
        alias.symlink_to('file')
        row, unused = M.measure(alias, root_owned=False)
        self.assertEqual(row['resolved_path'], str(target))
        self.assertEqual(row['aliases'][0]['target'], 'file')

    def test_measure_size_bound(self):
        p = self.root / 'file'
        p.write_bytes(b'too long')
        self.fails('CAP_MODE_REJECTED', M.measure, p, 1, False)

    def test_measure_parent_traversal_rejected(self):
        self.fails('CAP_IO_FAILED', M.measure, self.root / '..' / 'file', root_owned=False)

    def test_measure_directory_rejected(self):
        self.fails('CAP_MODE_REJECTED', M.measure, self.root, root_owned=False)

    def test_source_anchor_accepts_exact_reviewed_files(self):
        unused, expected, metadata_hash = self.sources()
        with mock.patch.object(M, 'METADATA_SOURCE_SHA256', metadata_hash):
            result = M.source_anchor(self.root, expected)
        self.assertEqual(len(result), 7)

    def test_source_anchor_rejects_changed_source(self):
        unused, expected, metadata_hash = self.sources()
        (self.root / 'recipe/probe_namespace_capabilities.py').write_text('different')
        with mock.patch.object(M, 'METADATA_SOURCE_SHA256', metadata_hash):
            self.fails('CAP_SOURCE_REJECTED', M.source_anchor, self.root, expected)

    def test_source_anchor_rejects_wrong_digest(self):
        self.sources()
        self.fails('CAP_SOURCE_REJECTED', M.source_anchor, self.root, '0' * 64)

    def test_source_anchor_rejects_symlink(self):
        unused, expected, metadata_hash = self.sources()
        p = self.root / 'recipe/probe_namespace_capabilities.py'
        p.rename(p.with_suffix('.source'))
        p.symlink_to(p.with_suffix('.source').name)
        with mock.patch.object(M, 'METADATA_SOURCE_SHA256', metadata_hash):
            self.fails('CAP_SOURCE_REJECTED', M.source_anchor, self.root, expected)

    def test_source_anchor_rejects_hardlink(self):
        unused, expected, metadata_hash = self.sources()
        os.link(self.root / 'recipe/probe_namespace_capabilities.py', self.root / 'second')
        with mock.patch.object(M, 'METADATA_SOURCE_SHA256', metadata_hash):
            self.fails('CAP_SOURCE_REJECTED', M.source_anchor, self.root, expected)

    def test_source_anchor_boolean_schema_rejected(self):
        index, unused, metadata_hash = self.sources()
        index['schema'] = True
        raw = json.dumps(index).encode()
        (self.root / 'source-index.json').write_bytes(raw)
        with mock.patch.object(M, 'METADATA_SOURCE_SHA256', metadata_hash):
            self.fails('CAP_SOURCE_REJECTED', M.source_anchor, self.root, M.digest(raw))

    def test_metadata_executes_held_verified_bytes_not_a_second_read(self):
        raw = b'VALUE = 17\n'
        with mock.patch.object(M, 'measure', return_value=({}, raw)) as read, \
             mock.patch.object(M, 'METADATA_SOURCE_SHA256', M.digest(raw)), \
             mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('unverified reread')):
            result = M.metadata_module(self.root)
        self.assertEqual(result.VALUE, 17)
        read.assert_called_once()

    def test_metadata_wrong_hash_never_executes(self):
        with mock.patch.object(M, 'measure', return_value=({}, b'raise AssertionError("executed")')):
            self.fails('CAP_SOURCE_REJECTED', M.metadata_module, self.root)

    def test_keyring_executes_held_source_anchored_bytes_without_import_search(self):
        raw = ('PROTOCOL = ' + repr(M.PROTOCOL) + '\nSHA256 = ' + repr(M.KEYRING_SOURCE_SHA256) +
               '\nSIZE = ' + repr(M.KEYRING_SOURCE_SIZE) + '\nSIGNER = ' + repr(M.KEYRING_SIGNER) +
               '\nCODES = frozenset(' + repr(sorted(M.KEYRING_CODES)) + ')\nVALUE = 19\n').encode()
        row = {'sha256': M.digest(raw)}
        prior_path = list(M.sys.path)
        with mock.patch.object(M, 'measure', return_value=(row, raw)) as reading, \
             mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('unchecked reread')):
            result = M.keyring_module(self.root, {'recipe/keyring_input.py': row})
        self.assertEqual(result.VALUE, 19)
        self.assertEqual(M.sys.path, prior_path)
        reading.assert_called_once()

    def test_keyring_loader_rejects_unanchored_or_replaced_module_before_exec(self):
        raw = b'raise AssertionError("unverified module executed")'
        with mock.patch.object(M, 'measure', return_value=({'sha256': M.digest(raw)}, raw)) as reading:
            self.fails('CAP_SOURCE_REJECTED', M.keyring_module, self.root, {})
            reading.assert_not_called()
            self.fails('CAP_SOURCE_REJECTED', M.keyring_module, self.root,
                       {'recipe/keyring_input.py': {'sha256': '0' * 64}})

    def test_keyring_source_pin_independent_of_self_consistent_source_index(self):
        unused, expected, metadata_hash = self.sources()
        with mock.patch.object(M, 'METADATA_SOURCE_SHA256', metadata_hash), \
             mock.patch.object(M, 'KEYRING_SOURCE_SHA256', '0' * 64):
            self.fails('CAP_KEYRING_SOURCE_REJECTED', M.source_anchor, self.root, expected)

    def test_schema_one_or_wrong_protocol_cannot_reuse_old_lock(self):
        for schema, protocol in ((1, M.PROTOCOL), (2, 'legacy-host-keyring'), (2, None)):
            lock = {'schema': schema, 'protocol': protocol, 'status': 'authenticated-existing-tools',
                    'scope': 'namespace-capability-only', 'snapshot': M.SNAPSHOT}
            with mock.patch.object(M, 'verify_interpreter') as interpreter:
                self.fails('CAP_LOCK_REJECTED', M.verify_state, self.root, lock)
            interpreter.assert_not_called()

    def runtime_context(self, path=None, prefix=None):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(M.sys, 'flags', types.SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1)))
        stack.enter_context(mock.patch.object(M.sys, 'path', M.PYTHON_PATHS if path is None else path))
        stack.enter_context(mock.patch.object(M.sys, 'pycache_prefix', str(self.root / 'unused-pycache') if prefix is None else prefix))
        stack.enter_context(mock.patch.object(M.os.path, 'lexists', return_value=False))
        return stack

    def test_runtime_flags_exact_path_and_prefix(self):
        with self.runtime_context():
            self.assertEqual(M.runtime_flags(self.root), str(self.root / 'unused-pycache'))

    def test_runtime_flags_extra_search_path_rejected(self):
        with self.runtime_context(path=M.PYTHON_PATHS + ['/private/site']):
            self.fails('CAP_INTERPRETER_REJECTED', M.runtime_flags, self.root)

    def test_runtime_flags_existing_zip_or_cache_rejected(self):
        with self.runtime_context(), mock.patch.object(M.os.path, 'lexists', return_value=True):
            self.fails('CAP_INTERPRETER_REJECTED', M.runtime_flags, self.root)

    def test_runtime_flags_unrelated_prefix_rejected(self):
        with self.runtime_context(prefix='/tmp/unused-pycache'):
            self.fails('CAP_INTERPRETER_REJECTED', M.runtime_flags, self.root)

    def test_command_cannot_invoke_helper_or_apt(self):
        with mock.patch.object(M.subprocess, 'run') as run:
            for command in ('/usr/bin/newuidmap', '/usr/bin/unshare', '/usr/bin/apt-get', '/bin/sh'):
                self.fails('CAP_RUNTIME_UNSUPPORTED', M.command, [command], self.root, 'test')
        run.assert_not_called()

    def test_bootstrap_mutating_modes_are_rejected(self):
        with mock.patch.object(M.subprocess, 'run') as run:
            for argv in (['/usr/bin/dpkg-deb', '--extract', '/tmp/archive.deb'],
                         ['/usr/bin/gpgv', '--output', '/tmp/output'],
                         ['/usr/bin/dpkg-query', '--search', '--private-option']):
                self.fails('CAP_RUNTIME_UNSUPPORTED', M.command, argv, self.root, 'fixture')
        run.assert_not_called()

    def test_signature_failure_prevents_index_parse(self):
        module = mock.Mock()
        module.RELEASE_HASHES = {'jammy': '0' * 64}
        module.RELEASE_SIZES = {'jammy': 1}
        module.ORIGIN = 'https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/'
        module.KEYRING = '/usr/share/keyrings/ubuntu-archive-keyring.gpg'
        module.SIGNER = M.KEYRING_SIGNER
        with mock.patch.object(M, 'signature_command', return_value=(1, b'bad signature')):
            self.fails('CAP_METADATA_REJECTED', M.load_metadata, self.root, module, self.root, mock.Mock(), mock.Mock())
        module.package_index_rows.assert_not_called()
        self.assertEqual(module.download_verified.call_count, 1)

    def test_wrong_valid_signer_prevents_index_parse(self):
        module = mock.Mock()
        module.RELEASE_HASHES = {'jammy': '0' * 64}
        module.RELEASE_SIZES = {'jammy': 1}
        module.ORIGIN = 'https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/'
        module.KEYRING = '/usr/share/keyrings/ubuntu-archive-keyring.gpg'
        module.SIGNER = M.KEYRING_SIGNER
        with mock.patch.object(M, 'signature_command', return_value=(0, b'[GNUPG:] VALIDSIG ' + b'B' * 40 + b' ')):
            self.fails('CAP_METADATA_REJECTED', M.load_metadata, self.root, module, self.root, mock.Mock(), mock.Mock())
        module.package_index_rows.assert_not_called()

    def test_readonly_command_keeps_stderr_private(self):
        result = types.SimpleNamespace(returncode=0, stdout=b'uidmap: /usr/bin/newuidmap\n', stderr=b'private warning')
        with mock.patch.object(M.subprocess, 'run', return_value=result) as run:
            rc, stdout = M.command(['/usr/bin/dpkg-query', '--search', '/usr/bin/newuidmap'], self.root, 'fixture')
        self.assertEqual(rc, 0)
        self.assertNotIn(b'private', stdout)
        self.assertEqual((self.root / 'fixture.stderr').read_bytes(), b'private warning')
        self.assertEqual(run.call_args.kwargs['env']['LC_ALL'], 'C')

    def test_exact_installed_identity_required(self):
        installed = {'uidmap:amd64': {'name': 'uidmap', 'version': '1:4.8.1-2ubuntu2.2', 'architecture': 'amd64'}}
        row = {'Package': 'uidmap', 'Version': '1:4.8.1-2ubuntu2.2', 'Architecture': 'amd64'}
        records = {('uidmap', row['Version'], 'amd64'): [row]}
        self.assertEqual(M.select_record('uidmap', installed, records), row)
        installed['uidmap:amd64']['version'] = 'private-ppa-version'
        self.fails('CAP_PACKAGE_UNAUTHENTICATED', M.select_record, 'uidmap', installed, records)

    def test_ambiguous_installed_architecture_rejected(self):
        value = {'name': 'uidmap', 'version': '1', 'architecture': 'amd64'}
        self.fails('CAP_PACKAGE_UNAUTHENTICATED', M.select_record, 'uidmap', {'a': value, 'b': value}, {})

    def test_archive_regular_and_alias_members(self):
        data = tar_data([('./usr/bin/newuidmap', b'authentic', 'file', 0o4755),
                         ('./usr/bin/alias', 'newuidmap', 'symlink', 0o777)])
        members = M.archive_members(data)
        self.assertEqual(members['usr/bin/newuidmap']['mode'], 0o4755)
        self.assertEqual(members['usr/bin/alias']['target'], 'newuidmap')

    def test_archive_traversal_rejected(self):
        self.fails('CAP_ARCHIVE_REJECTED', M.archive_members, tar_data([('../escape', b'x', 'file', 0o644)]))

    def test_archive_duplicate_rejected(self):
        self.fails('CAP_ARCHIVE_REJECTED', M.archive_members, tar_data([('same', b'x', 'file', 0o644)] * 2))

    def test_archive_special_file_rejected(self):
        self.fails('CAP_ARCHIVE_REJECTED', M.archive_members, tar_data([('fifo', '', 'fifo', 0o644)]))

    def test_member_exact_bytes_mode_and_owner(self):
        members = M.archive_members(tar_data([('usr/bin/newuidmap', b'authentic', 'file', 0o4755)]))
        row = record()
        M.verify_member(row, members)
        for key, value in (('mode', 0o755), ('uid', 1000), ('sha256', '0' * 64), ('size', 0)):
            changed = dict(row, **{key: value})
            self.fails('CAP_BYTES_REJECTED', M.verify_member, changed, members)

    def test_member_usrmerge_requires_exact_alias(self):
        members = M.archive_members(tar_data([('bin/tool', b'authentic', 'file', 0o755)]))
        row = record('/bin/tool', mode=0o755)
        row['resolved_path'] = '/usr/bin/tool'
        row['aliases'] = [alias_record('/bin', 'usr/bin')]
        M.verify_member(row, members)
        row['aliases'][0]['target'] = 'private/bin'
        self.fails('CAP_BYTES_REJECTED', M.verify_member, row, members)

    def test_member_authenticated_sitecustomize_alias(self):
        members = M.archive_members(tar_data([('etc/python3.10/sitecustomize.py', b'authentic', 'file', 0o644),
          ('usr/lib/python3.10/sitecustomize.py', '/etc/python3.10/sitecustomize.py', 'symlink', 0o777)]))
        row = record('/usr/lib/python3.10/sitecustomize.py', mode=0o644)
        row['resolved_path'] = '/etc/python3.10/sitecustomize.py'
        row['aliases'] = [alias_record(row['path'], row['resolved_path'])]
        M.verify_member(row, members)

    def test_elf_needed_and_interpreter(self):
        self.assertEqual(M.elf_inputs(elf_data()), ('/lib64/ld-linux-x86-64.so.2', ['libc.so.6']))

    def test_elf_shared_object_without_interpreter(self):
        self.assertEqual(M.elf_inputs(elf_data(interp=False))[0], None)

    def test_elf_loader_injection_tags_rejected(self):
        for tag in (15, 29, 0x6ffffefb, 0x6ffffefc, 0x7ffffffd, 0x7fffffff):
            self.fails('CAP_RUNTIME_UNSUPPORTED', M.elf_inputs, elf_data(extra_tag=tag))

    def test_elf_needed_path_rejected(self):
        self.fails('CAP_RUNTIME_UNSUPPORTED', M.elf_inputs, elf_data(names=('/private/evil.so',)))

    def test_elf_wrong_architecture_rejected(self):
        data = bytearray(elf_data())
        struct.pack_into('<H', data, 18, 183)
        self.fails('CAP_RUNTIME_UNSUPPORTED', M.elf_inputs, data)

    def test_elf_truncated_rejected(self):
        self.fails('CAP_RUNTIME_UNSUPPORTED', M.elf_inputs, elf_data()[:180])

    def test_cache_keeps_every_same_soname_candidate(self):
        parsed = M.loader_cache(cache_data((('libc.so.6', '/lib/libc.so.6'), ('libc.so.6', '/vendor/libc.so.6'))))
        self.assertEqual(parsed, {'libc.so.6': {'/lib/libc.so.6', '/vendor/libc.so.6'}})

    def test_cache_wrong_magic_rejected(self):
        self.fails('CAP_RUNTIME_UNSUPPORTED', M.loader_cache, b'x' * 80)

    def test_cache_traversal_path_rejected(self):
        self.fails('CAP_RUNTIME_UNSUPPORTED', M.loader_cache, cache_data((('lib.so', '/lib/../private/lib.so'),)))

    def test_cache_string_out_of_range_rejected(self):
        data = bytearray(cache_data())
        struct.pack_into('<I', data, 52, 1)
        self.fails('CAP_RUNTIME_UNSUPPORTED', M.loader_cache, data)

    def authenticator(self):
        return M.Authenticator(self.root, mock.Mock(), self.root, {}, {})

    def test_owner_accepts_exact_package_architecture(self):
        auth = self.authenticator()
        with mock.patch.object(M, 'command', side_effect=[(0, b'uidmap:amd64: /usr/bin/newuidmap\n'), (1, b'')]):
            self.assertEqual(auth.owner('/usr/bin/newuidmap'), 'uidmap')

    def test_owner_rejects_arbitrary_warning_and_diversion(self):
        for line in (b'warning: secret\n', b'diversion by other from: /usr/bin/newuidmap\n'):
            with mock.patch.object(M, 'command', return_value=(0, line)):
                self.fails('CAP_OWNER_REJECTED', self.authenticator().owner, '/usr/bin/newuidmap')

    def test_owner_rejects_other_path(self):
        with mock.patch.object(M, 'command', return_value=(0, b'uidmap: /private/tool\n')):
            self.fails('CAP_OWNER_REJECTED', self.authenticator().owner, '/usr/bin/newuidmap')

    def test_owner_rejects_conflicting_usrmerge_owners(self):
        with mock.patch.object(M, 'command', side_effect=[(0, b'uidmap: /usr/bin/newuidmap\n'), (0, b'other: /bin/newuidmap\n')]):
            self.fails('CAP_OWNER_REJECTED', self.authenticator().owner, '/usr/bin/newuidmap')

    def test_owner_resolves_authenticated_inverse_usrmerge_alias(self):
        with mock.patch.object(M, 'command', side_effect=[(1, b''), (0, b'libnss-systemd:amd64: /usr/lib/x86_64-linux-gnu/libnss_systemd.so.2\n')]):
            self.assertEqual(self.authenticator().owner('/lib/x86_64-linux-gnu/libnss_systemd.so.2'), 'libnss-systemd')

    def test_archive_control_mismatch_prevents_payload_parse(self):
        auth = self.authenticator()
        signed = {'Package': 'uidmap', 'Version': '1', 'Architecture': 'amd64', 'Size': '1'}
        auth.m.fields.return_value = {'Package': 'unrelated', 'Version': '1', 'Architecture': 'amd64'}
        auth.m.package_identity.side_effect = lambda row: tuple(row[k] for k in ('Package', 'Version', 'Architecture'))
        with mock.patch.object(M, 'select_record', return_value=signed), \
             mock.patch.object(M, 'command', return_value=(0, b'control')) as command, \
             mock.patch.object(M, 'archive_members') as members:
            self.fails('CAP_ARCHIVE_REJECTED', auth.package, 'uidmap')
        self.assertEqual(command.call_count, 1)
        members.assert_not_called()

    def test_tool_owner_failure_is_specific(self):
        auth = mock.Mock()
        auth.authenticate.side_effect = M.PreparationError('CAP_OWNER_REJECTED')
        self.fails('CAP_PYTHON_OWNER_REJECTED', M.authenticate_tools, auth)

    def test_tool_setuid_is_required_but_never_installed(self):
        auth = mock.Mock()
        auth.authenticate.side_effect = lambda path, owner: record(path, mode=0o755)
        with mock.patch.object(M, 'command') as command:
            self.fails('CAP_NEWUIDMAP_MODE_REJECTED', M.authenticate_tools, auth)
        command.assert_not_called()

    def test_tool_newgidmap_identity_failure_is_specific(self):
        auth = mock.Mock()
        def checked(path, owner):
            if path == M.TOOLS['newgidmap']:
                raise M.PreparationError('CAP_PACKAGE_UNAUTHENTICATED')
            return record(path, mode=0o4755)
        auth.authenticate.side_effect = checked
        self.fails('CAP_NEWGIDMAP_PACKAGE_UNAUTHENTICATED', M.authenticate_tools, auth)

    def test_authentic_existing_setuid_helpers_do_not_trigger_install(self):
        auth = mock.Mock()
        auth.authenticate.side_effect = lambda path, owner: record(path, mode=0o4755)
        with mock.patch.object(M, 'command') as command:
            self.assertEqual(set(M.authenticate_tools(auth)), set(M.TOOLS))
        command.assert_not_called()

    def test_python_dev_owner_must_be_signed_same_source(self):
        auth = mock.Mock()
        auth.members = {x: {} for x in M.PYTHON_PACKAGES}
        auth.owner.return_value = 'libpython3.10-dev'
        with mock.patch.object(M, 'select_record', return_value={'Source': 'python3.10'}):
            M.authenticate_python(auth, ['/usr/lib/python3.10/config-x/python-config.py'])
        self.assertEqual(auth.authenticate.call_args.args, ('/usr/lib/python3.10/config-x/python-config.py',))
        self.assertEqual(auth.authenticate.call_args.kwargs['expected'], 'libpython3.10-dev')
        self.assertTrue(callable(auth.authenticate.call_args.kwargs['owner_policy']))

    def test_python_tk_owner_must_have_declared_extension_source(self):
        auth = mock.Mock()
        auth.members = {x: {} for x in M.PYTHON_PACKAGES}
        auth.owner.return_value = 'python3-tk'
        with mock.patch.object(M, 'select_record', return_value={'Source': 'python3-stdlib-extensions'}):
            M.authenticate_python(auth, ['/usr/lib/python3.10/tkinter/__init__.py'])
        self.assertTrue(auth.authenticate.called)

    def test_python_unlisted_owner_never_downloaded(self):
        auth = mock.Mock()
        auth.members = {x: {} for x in M.PYTHON_PACKAGES}
        auth.owner.return_value = 'private-ppa-package'
        self.fails('CAP_RUNTIME_UNAUTHENTICATED', M.authenticate_python, auth, ['/usr/lib/python3.10/private.py'])
        auth.authenticate.assert_not_called()

    def test_python_wrong_source_rejected(self):
        auth = mock.Mock()
        auth.members = {x: {} for x in M.PYTHON_PACKAGES}
        auth.owner.return_value = 'libpython3.10-dev'
        with mock.patch.object(M, 'select_record', return_value={'Source': 'unrelated-source'}):
            self.fails('CAP_RUNTIME_UNAUTHENTICATED', M.authenticate_python, auth, ['/usr/lib/python3.10/private.py'])

    def test_runtime_dependency_candidates_are_all_authenticated(self):
        auth = mock.Mock()
        binaries = {'/tool': elf_data(), '/lib64/ld-linux-x86-64.so.2': elf_data(names=(), interp=False),
                    '/lib/libc.so.6': elf_data(names=(), interp=False), '/alternate/libc.so.6': elf_data(names=(), interp=False)}
        with mock.patch.object(M, 'measure', side_effect=lambda path, **kwargs: ({}, binaries[path])), \
             mock.patch.object(M, 'library_paths', return_value=['/lib/libc.so.6', '/alternate/libc.so.6']):
            result = M.runtime_closure(auth, ['/tool'], {})
        self.assertEqual(set(result), set(binaries))
        self.assertEqual({call.args[0] for call in auth.authenticate.call_args_list}, set(binaries))

    def test_runtime_new_candidate_after_lock_rejected(self):
        with mock.patch.object(M, 'measure', return_value=({}, elf_data(interp=False))), \
             mock.patch.object(M, 'library_paths', return_value=['/new/libc.so.6']):
            self.fails('CAP_STATE_CHANGED', M.check_runtime_paths, ['/tool'], {}, ['/tool'])

    def test_same_original_interpreter_inode_via_fd_accepted(self):
        row = record('/usr/bin/python3.10')
        with mock.patch.object(M.os, 'stat', return_value=types.SimpleNamespace(st_dev=1, st_ino=2, st_size=9)), \
             mock.patch.object(M.sys, 'version_info', (3, 10)), \
             mock.patch.object(M.sys, 'executable', '/proc/self/fd/17'), \
             mock.patch.object(Path, 'read_bytes', return_value=b'authentic'):
            M.verify_interpreter(row)

    def test_copied_interpreter_same_bytes_rejected(self):
        with mock.patch.object(M.os, 'stat', return_value=types.SimpleNamespace(st_dev=1, st_ino=999, st_size=9)), \
             mock.patch.object(M.sys, 'version_info', (3, 10)):
            self.fails('CAP_INTERPRETER_REJECTED', M.verify_interpreter, record())

    def test_lock_write_is_exclusive_and_private(self):
        path = self.root / 'lock.json'
        M.write_new(path, {'schema': 1})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.stat().st_nlink, 1)
        with self.assertRaises(FileExistsError):
            M.write_new(path, {'schema': 2})
        self.assertEqual(json.loads(path.read_text()), {'schema': 1})

    def test_verify_lock_hash_before_any_runtime_validation(self):
        path = self.root / 'capability-inputs.lock.json'
        M.write_new(path, {'schema': 1})
        with mock.patch.object(M, 'verify_state') as state:
            self.fails('CAP_LOCK_REJECTED', M.verify_lock, self.root, '0' * 64)
        state.assert_not_called()

    def test_verify_lock_requires_anchored_file_and_state(self):
        path = self.root / 'capability-inputs.lock.json'
        M.write_new(path, {'schema': 1})
        with mock.patch.object(M, 'verify_state') as state:
            self.assertEqual(M.verify_lock(self.root, M.digest(path.read_bytes())), {'schema': 1})
        state.assert_called_once_with(self.root, {'schema': 1})

    def test_verify_lock_changed_runtime_is_not_accepted(self):
        path = self.root / 'capability-inputs.lock.json'
        M.write_new(path, {'schema': 1})
        with mock.patch.object(M, 'verify_state', side_effect=M.PreparationError('CAP_STATE_CHANGED')):
            self.fails('CAP_STATE_CHANGED', M.verify_lock, self.root, M.digest(path.read_bytes()))

    def test_verify_state_rejects_wrong_scope_before_host_read(self):
        lock = {'schema': 1, 'status': 'authenticated-existing-tools', 'scope': 'build-install', 'snapshot': M.SNAPSHOT}
        with mock.patch.object(M, 'measure') as measure:
            self.fails('CAP_LOCK_REJECTED', M.verify_state, self.root, lock)
        measure.assert_not_called()

    def test_verify_state_rejects_boolean_schema(self):
        lock = {'schema': True, 'status': 'authenticated-existing-tools', 'scope': 'namespace-capability-only', 'snapshot': M.SNAPSHOT}
        self.fails('CAP_LOCK_REJECTED', M.verify_state, self.root, lock)

    def test_unmodified_metadata_dependency_hash(self):
        actual = (SOURCE.parent / 'jammy_metadata.py').read_bytes()
        self.assertEqual(M.digest(actual), M.METADATA_SOURCE_SHA256)

    def test_loginuid_exact_sentinel_supported(self):
        with mock.patch.object(Path, 'open', return_value=io.BytesIO(b'4294967295')):
            self.assertEqual(M.loginuid_value(), '4294967295')

    def test_loginuid_zero_and_normal_uid_supported(self):
        for raw in (b'0', b'1001', b'4294967294'):
            with mock.patch.object(Path, 'open', return_value=io.BytesIO(raw)):
                self.assertEqual(M.loginuid_value(), raw.decode())

    def test_loginuid_newline_must_not_be_stripped(self):
        with mock.patch.object(Path, 'open', return_value=io.BytesIO(b'4294967295\n')):
            self.fails('CAP_LOGINUID_UNSUPPORTED', M.loginuid_value)

    def test_loginuid_noncanonical_or_overflow_rejected(self):
        for raw in (b'', b'01001', b'-1', b'+1', b' 1', b'1\0', b'4294967296', b'123456789012345'):
            with mock.patch.object(Path, 'open', return_value=io.BytesIO(raw)):
                self.fails('CAP_LOGINUID_UNSUPPORTED', M.loginuid_value)

    def test_nss_only_fixed_files_and_systemd_grammars(self):
        for passwd, group in (('files', 'files'), ('files systemd', 'files'), ('files', 'files systemd'), ('files systemd', 'files systemd')):
            result = M.nss_providers('passwd: ' + passwd + '\ngroup: ' + group + '\n')
            self.assertEqual(result, {'passwd': passwd.split(), 'group': group.split()})

    def test_nss_arbitrary_provider_rejected(self):
        for tokens in ('files ldap', 'systemd files', 'files sss', 'files systemd systemd', ''):
            self.fails('CAP_NSS_UNSUPPORTED', M.nss_providers, 'passwd: ' + tokens + '\ngroup: files\n')

    def test_nss_actions_and_subid_provider_rejected(self):
        for text in ('passwd: files [SUCCESS=continue] systemd\ngroup: files\n',
                     'passwd: files\ngroup: files\nsubid: sss\n',
                     'passwd: files\npasswd: files\ngroup: files\n'):
            self.fails('CAP_NSS_UNSUPPORTED', M.nss_providers, text)

    def test_nss_files_only_needs_no_dynamic_provider(self):
        with mock.patch.object(M, 'nss_candidates') as candidates:
            result = M.nss_inputs('passwd: files\ngroup: files\n', {})
        self.assertEqual(result['fallback_paths'], [])
        self.assertEqual(result['absent_paths'], [])
        candidates.assert_not_called()

    def test_nss_primary_and_versioned_fallback_are_both_bound(self):
        primary = '/usr/lib/x86_64-linux-gnu/libnss_systemd.so.2'
        legacy = '/usr/lib/x86_64-linux-gnu/libnss_systemd-2.35.so'
        with mock.patch.object(M, 'nss_candidates', side_effect=[({primary}, set()), (set(), {legacy})]) as candidates:
            result = M.nss_inputs('passwd: files systemd\ngroup: files\n', {})
        self.assertEqual(result['fallback_paths'], [primary])
        self.assertEqual(result['absent_paths'], [legacy])
        self.assertEqual([call.args[0] for call in candidates.call_args_list], list(M.NSS_SONAMES))

    def test_nss_existing_versioned_fallback_requires_authentication_too(self):
        with mock.patch.object(M, 'nss_candidates', side_effect=[({'/primary'}, set()), ({'/versioned'}, set())]):
            result = M.nss_inputs('passwd: files systemd\ngroup: files\n', {})
        auth = mock.Mock()
        M.authenticate_nss(auth, result)
        self.assertEqual(auth.authenticate.call_args_list, [mock.call('/primary', 'libnss-systemd'), mock.call('/versioned', 'libnss-systemd')])

    def test_nss_missing_primary_is_terminal(self):
        with mock.patch.object(M, 'nss_candidates', return_value=(set(), {'/missing'})):
            self.fails('CAP_NSS_PROVIDER_UNAUTHENTICATED', M.nss_inputs, 'passwd: files systemd\ngroup: files\n', {})

    def test_nss_unmatched_identity_is_typed_terminal(self):
        auth = mock.Mock()
        auth.authenticate.side_effect = M.PreparationError('CAP_PACKAGE_UNAUTHENTICATED')
        self.fails('CAP_NSS_PROVIDER_UNAUTHENTICATED', M.authenticate_nss, auth, {'fallback_paths': ['/primary']})

    def test_nss_missing_live_archive_member_is_typed_terminal(self):
        auth = mock.Mock()
        auth.authenticate.side_effect = FileNotFoundError('private path')
        self.fails('CAP_NSS_PROVIDER_UNAUTHENTICATED', M.authenticate_nss, auth, {'fallback_paths': ['/primary']})

    def test_nss_candidates_freeze_absence_without_executing_loader(self):
        with mock.patch.object(M, 'LIBDIRS', ('/fixture/lib',)), \
             mock.patch.object(Path, 'exists', return_value=False), \
             mock.patch.object(M.os.path, 'lexists', return_value=False), \
             mock.patch.object(M, 'command') as command:
            paths, absent = M.nss_candidates('libnss_systemd-2.35.so', {})
        self.assertEqual(paths, set())
        self.assertEqual(absent, {'/fixture/lib/libnss_systemd-2.35.so'})
        command.assert_not_called()

    def test_nss_cached_alternate_candidate_is_not_silently_ignored(self):
        with mock.patch.object(M, 'LIBDIRS', ()), mock.patch.object(M, 'command') as command:
            paths, absent = M.nss_candidates('libnss_systemd.so.2', {'libnss_systemd.so.2': {'/unmeasured/provider'}})
        self.assertEqual(paths, {'/unmeasured/provider'})
        command.assert_not_called()

    def test_nss_new_fallback_changes_locked_binding(self):
        policy = 'passwd: files systemd\ngroup: files\n'
        with mock.patch.object(M, 'nss_candidates', side_effect=[({'/primary'}, set()), (set(), {'/versioned'})]):
            before = M.nss_inputs(policy, {})
        with mock.patch.object(M, 'nss_candidates', side_effect=[({'/primary'}, set()), ({'/versioned'}, set())]):
            after = M.nss_inputs(policy, {})
        self.assertNotEqual(before, after)

    def test_nss_recheck_rejects_new_candidate_before_any_native_call(self):
        lock = {'nss': {'providers': {'passwd': ['files'], 'group': ['files']}, 'fallback_paths': [], 'absent_paths': []}}
        with mock.patch.object(Path, 'read_text', return_value='passwd: files systemd\ngroup: files\n'), \
             mock.patch.object(M, 'nss_inputs', return_value={'changed': True}), \
             mock.patch.object(M, 'command') as command:
            self.fails('CAP_STATE_CHANGED', M.check_nss_state, lock, {})
        command.assert_not_called()

    def test_nss_recheck_requires_authenticated_owner_and_elf_closure(self):
        nss = {'providers': {'passwd': ['files', 'systemd'], 'group': ['files']},
               'fallback_paths': ['/primary'], 'absent_paths': []}
        lock = {'nss': nss, 'runtime_files': {'/primary': {'package': 'libnss-systemd'}}, 'runtime_elf_paths': ['/primary']}
        with mock.patch.object(Path, 'read_text', return_value=''), mock.patch.object(M, 'nss_inputs', return_value=nss):
            M.check_nss_state(lock, {})
            lock['runtime_files']['/primary']['package'] = 'other'
            self.fails('CAP_LOCK_REJECTED', M.check_nss_state, lock, {})
            lock['runtime_files']['/primary']['package'] = 'libnss-systemd'
            lock['runtime_elf_paths'] = []
            self.fails('CAP_LOCK_REJECTED', M.check_nss_state, lock, {})

    def state_before_runtime(self):
        return {'schema': M.LOCK_SCHEMA, 'protocol': M.PROTOCOL, 'member_protocol': M.MEMBER_PROTOCOL, 'status': 'authenticated-existing-tools', 'scope': 'namespace-capability-only',
                'snapshot': M.SNAPSHOT, 'tools': {name: {'path': path} for name, path in M.TOOLS.items()},
                'bootstrap_files': {name: {'path': path} for name, path in M.BOOTSTRAP_TOOLS.items()},
                'host_files': {path: {} for path in M.HOST_FILES},
                'absent_paths': list(M.OPTIONAL_HOST_FILES) + ['/etc/ld.so.preload', M.PYTHON_PATHS[0], M.NSCD_SOCKET],
                'host': {'python_path': M.PYTHON_PATHS, 'loginuid': '4294967295'}}

    def test_verify_state_rechecks_anchored_private_anchor_before_runtime_files(self):
        lock = self.state_before_runtime()
        lock['host'].update(uid=os.getuid(), gid=os.getgid(), groups=sorted(os.getgroups()),
                            boot_id='0' * 36, pycache_prefix=str(self.root / 'unused-pycache'))
        lock.update(source_index_sha256='0' * 64, source_files={'anchored': True},
                    trust_anchor={'private-anchor': True}, runtime_files={})
        keyring = mock.Mock()
        with mock.patch.object(M.os.path, 'lexists', return_value=False), \
             mock.patch.object(M, 'loginuid_value', return_value=lock['host']['loginuid']), \
             mock.patch.object(M, 'verify_interpreter'), \
             mock.patch.object(M, 'runtime_flags', return_value=lock['host']['pycache_prefix']), \
             mock.patch.object(Path, 'read_text', return_value=lock['host']['boot_id']), \
             mock.patch.object(M, 'source_anchor', return_value=lock['source_files']) as anchor, \
             mock.patch.object(M, 'keyring_module', return_value=keyring) as loader, \
             mock.patch.object(M, 'keyring_checked', side_effect=M.PreparationError('CAP_KEYRING_STATE_CHANGED')) as checked, \
             mock.patch.object(M, 'measure') as measure:
            self.fails('CAP_KEYRING_STATE_CHANGED', M.verify_state, self.root, lock)
        anchor.assert_called_once_with(self.root, lock['source_index_sha256'])
        loader.assert_called_once_with(self.root, lock['source_files'])
        checked.assert_called_once_with(keyring, keyring.verify_manifest, self.root, lock['trust_anchor'])
        measure.assert_not_called()

    def test_prepare_failed_source_anchor_never_loads_keyring(self):
        with mock.patch.object(M, 'source_anchor', side_effect=M.PreparationError('CAP_SOURCE_REJECTED')), \
             mock.patch.object(M, 'keyring_module') as keyring, \
             mock.patch.object(M, 'metadata_module') as metadata:
            self.fails('CAP_SOURCE_REJECTED', M.prepare, self.root, '0' * 64)
        keyring.assert_not_called()
        metadata.assert_not_called()

    def test_state_requires_nscd_absence_in_lock(self):
        lock = self.state_before_runtime()
        lock['absent_paths'].remove(M.NSCD_SOCKET)
        self.fails('CAP_LOCK_REJECTED', M.verify_state, self.root, lock)

    def test_state_rejects_nscd_socket_before_interpreter_or_runtime(self):
        with mock.patch.object(M.os.path, 'lexists', return_value=True), \
             mock.patch.object(M, 'verify_interpreter') as interpreter:
            self.fails('CAP_NSCD_PRESENT', M.verify_state, self.root, self.state_before_runtime())
        interpreter.assert_not_called()

    def test_state_rejects_changed_loginuid_before_runtime(self):
        with mock.patch.object(M.os.path, 'lexists', return_value=False), \
             mock.patch.object(M, 'loginuid_value', return_value='1001'), \
             mock.patch.object(M, 'verify_interpreter') as interpreter:
            self.fails('CAP_STATE_CHANGED', M.verify_state, self.root, self.state_before_runtime())
        interpreter.assert_not_called()

    def test_member_inverse_usrmerge_alias_still_requires_signed_bytes(self):
        members = M.archive_members(tar_data([('usr/lib/x86_64-linux-gnu/libnss_systemd.so.2', b'authentic', 'file', 0o644)]))
        row = record('/lib/x86_64-linux-gnu/libnss_systemd.so.2', mode=0o644)
        row['resolved_path'] = '/usr/lib/x86_64-linux-gnu/libnss_systemd.so.2'
        row['aliases'] = [alias_record('/lib', 'usr/lib')]
        M.verify_member(row, members)
        row['sha256'] = '0' * 64
        self.fails('CAP_BYTES_REJECTED', M.verify_member, row, members)

    def test_mode_exports_match_fixed_reviewed_subjects(self):
        fixed = set(M.HOST_FILES) | set(M.OPTIONAL_HOST_FILES) | set(M.TOOLS.values()) | set(M.BOOTSTRAP_TOOLS.values()) | {'/etc/ld.so.preload', '/usr/share/keyrings/ubuntu-archive-keyring.gpg'}
        self.assertEqual(set(M.MODE_FIXED_SUBJECTS), fixed)
        self.assertEqual(M.MODE_SUBJECTS, set(M.MODE_FIXED_SUBJECTS.values()) | {'SOURCE_INPUT', 'RUNTIME_INPUT', 'UNKNOWN'})
        self.assertEqual(M.MODE_CODES, {code for code in M.PREPARATION_CODES if code.endswith('_MODE_REJECTED')})
        self.assertEqual(len(M.MODE_SUBJECTS), 20)
        self.assertEqual(len(M.MODE_CHECKS), 11)
        self.assertTrue(all(value.isupper() and '/' not in value for value in M.MODE_SUBJECTS | M.MODE_CHECKS))

    def test_mode_error_and_receipt_are_finite(self):
        for code in M.MODE_CODES:
            subject = 'ARCHIVE_KEYRING' if code == 'CAP_MODE_REJECTED' else code.removeprefix('CAP_').removesuffix('_MODE_REJECTED')
            error = M.PreparationError(code, subject, 'COMPONENT_WRITABLE')
            self.assertEqual(str(error), code)
            self.assertEqual(M.preparation_status(error), {'schema': 1, 'status': 'FAIL', 'code': code,
                'subject': subject, 'check': 'COMPONENT_WRITABLE'})
        self.assertEqual(M.preparation_status(M.PreparationError('CAP_MODE_REJECTED')),
            {'schema': 1, 'status': 'FAIL', 'code': 'CAP_MODE_REJECTED', 'subject': 'UNKNOWN', 'check': 'UNKNOWN'})

    def test_tool_mode_code_rejects_unrelated_known_subject(self):
        for name, path in M.TOOLS.items():
            code = 'CAP_' + name.upper() + '_MODE_REJECTED'
            expected = M.MODE_FIXED_SUBJECTS[path]
            for subject in M.MODE_SUBJECTS:
                valid = subject == expected
                self.assertEqual(M.mode_diagnostic_valid(code, subject, 'FILE_TYPE'), valid)
                if not valid:
                    with self.assertRaises(ValueError):
                        M.PreparationError(code, subject, 'FILE_TYPE')
            self.assertTrue(M.mode_diagnostic_valid(code, 'UNKNOWN', 'UNKNOWN'))
            self.assertEqual(M.PreparationError(code).subject, 'UNKNOWN')
        self.assertTrue(M.mode_diagnostic_valid('CAP_MODE_REJECTED', 'UNKNOWN', 'FILE_TYPE'))
        self.assertTrue(M.mode_diagnostic_valid('CAP_MODE_REJECTED', 'SOURCE_INPUT', 'UNKNOWN'))
        for value in ('private-secret', [], {}, 100123, None):
            self.assertFalse(M.mode_diagnostic_valid(value, 'UNKNOWN', 'UNKNOWN'))
            self.assertFalse(M.mode_diagnostic_valid('CAP_MODE_REJECTED', value, 'UNKNOWN'))
            self.assertFalse(M.mode_diagnostic_valid('CAP_MODE_REJECTED', 'UNKNOWN', value))

    def test_nonmode_and_success_receipts_have_no_diagnostics(self):
        self.assertEqual(M.preparation_status(), {'schema': 1, 'status': 'PASS', 'code': 'CAP_INPUTS_VERIFIED'})
        for code in M.PREPARATION_CODES - M.MODE_CODES - {'CAP_INPUTS_VERIFIED'}:
            self.assertEqual(set(M.preparation_status(M.PreparationError(code))), {'schema', 'status', 'code'})

    def test_mode_diagnostic_invalid_enum_never_echoes_input(self):
        secrets = ('/private/secret-user/keyring', '100123', '0777', 'token-secret', 'ARCHIVE_KEYRING\nsecret', 100123, [], {})
        for secret in secrets:
            for subject, check in ((secret, 'FILE_TYPE'), ('PASSWD', secret)):
                with self.subTest(subject=subject, check=check), self.assertRaises(ValueError) as raised:
                    M.PreparationError('CAP_MODE_REJECTED', subject, check)
                self.assertEqual(str(raised.exception), 'Unknown mode diagnostic')
        for pair in (('PASSWD', None), (None, 'FILE_TYPE')):
            with self.assertRaises(ValueError):
                M.PreparationError('CAP_MODE_REJECTED', *pair)
        with self.assertRaises(ValueError):
            M.PreparationError('CAP_SOURCE_REJECTED', 'PASSWD', 'FILE_TYPE')

    def test_forged_mutated_diagnostic_rejected_at_receipt_boundary(self):
        for attr in ('subject', 'check', 'code'):
            error = M.PreparationError('CAP_MODE_REJECTED', 'PASSWD', 'FILE_TYPE')
            setattr(error, attr, '/private/token-secret/uid-100123/mode-0777')
            with self.assertRaises(ValueError) as raised:
                M.preparation_status(error)
            self.assertNotIn('private', str(raised.exception))
        error = M.PreparationError('CAP_SOURCE_REJECTED')
        error.subject = 'PASSWD'
        with self.assertRaises(ValueError):
            M.preparation_status(error)
        with self.assertRaises(ValueError):
            M.preparation_status(RuntimeError('private secret'))

    def test_measure_subject_validation_precedes_io(self):
        with mock.patch.object(Path, 'lstat') as inspect, mock.patch.object(M.os, 'open') as opening:
            with self.assertRaises(ValueError) as raised:
                M.measure('/private/secret', subject='/private/secret')
        self.assertEqual(str(raised.exception), 'Unknown mode subject')
        inspect.assert_not_called()
        opening.assert_not_called()

    def test_official_image_writable_share_ancestor_is_finite_and_terminal(self):
        seen = []
        def inspect(path):
            seen.append(str(path))
            return types.SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | (0o777 if str(path) == '/usr/share' else 0o755))
        path = '/usr/share/keyrings/ubuntu-archive-keyring.gpg'
        with mock.patch.object(Path, 'lstat', inspect), mock.patch.object(M.os, 'open') as opening:
            with self.assertRaises(M.PreparationError) as raised:
                M.measure(path, subject=M.MODE_FIXED_SUBJECTS[path])
        self.assertEqual(seen, ['/usr', '/usr/share'])
        opening.assert_not_called()
        receipt = M.preparation_status(raised.exception)
        self.assertEqual(receipt, {'schema': 1, 'status': 'FAIL', 'code': 'CAP_MODE_REJECTED',
            'subject': 'ARCHIVE_KEYRING', 'check': 'COMPONENT_WRITABLE'})
        rendered = json.dumps(receipt)
        for value in ('/usr', '0777', 'keyring.gpg', 'st_uid'):
            self.assertNotIn(value, rendered)

    def test_component_owner_writable_and_type_rejection_order(self):
        cases = ((100123, stat.S_IFREG | 0o777, 'COMPONENT_OWNER'),
                 (0, stat.S_IFREG | 0o777, 'COMPONENT_WRITABLE'),
                 (0, stat.S_IFREG | 0o644, 'INTERMEDIATE_TYPE'))
        for uid, mode, check in cases:
            with self.subTest(check=check), mock.patch.object(Path, 'lstat', return_value=types.SimpleNamespace(st_uid=uid, st_mode=mode)), \
                 mock.patch.object(M.os, 'open') as opening:
                with self.assertRaises(M.PreparationError) as raised:
                    M.measure('/private-secret/file', subject='RUNTIME_INPUT')
            opening.assert_not_called()
            self.assertEqual((raised.exception.subject, raised.exception.check), ('RUNTIME_INPUT', check))
            self.assertNotIn('100123', json.dumps(M.preparation_status(raised.exception)))

    def test_symlink_hop_bound_keeps_original_limit(self):
        alias = types.SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFLNK | 0o777, st_dev=1, st_ino=2)
        with mock.patch.object(Path, 'lstat', return_value=alias) as inspect, \
             mock.patch.object(M.os, 'readlink', return_value='/alias') as links, mock.patch.object(M.os, 'open') as opening:
            with self.assertRaises(M.PreparationError) as raised:
                M.measure('/alias', subject='RUNTIME_INPUT')
        self.assertEqual(raised.exception.check, 'SYMLINK_LIMIT')
        self.assertEqual(inspect.call_count, 17)
        self.assertEqual(links.call_count, 16)
        opening.assert_not_called()

    def test_fd_type_size_owner_writable_rejection_order(self):
        cases = ((stat.S_IFIFO | 0o777, 999999999, 100123, 'FILE_TYPE'),
                 (stat.S_IFREG | 0o777, 999999999, 100123, 'FILE_SIZE'),
                 (stat.S_IFREG | 0o777, -1, 100123, 'FILE_SIZE'),
                 (stat.S_IFREG | 0o777, 0, 100123, 'FILE_OWNER'),
                 (stat.S_IFREG | 0o777, 0, 0, 'FILE_WRITABLE'))
        for mode, size, uid, check in cases:
            with self.subTest(check=check), \
                 mock.patch.object(Path, 'lstat', return_value=types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0)), \
                 mock.patch.object(M.os, 'open', return_value=123), \
                 mock.patch.object(M.os, 'fstat', return_value=types.SimpleNamespace(st_mode=mode, st_size=size, st_uid=uid)), \
                 mock.patch.object(M.os, 'read') as reading, mock.patch.object(M.os, 'close') as closing:
                with self.assertRaises(M.PreparationError) as raised:
                    M.measure('/private-secret', limit=64, subject='SOURCE_INPUT')
            self.assertEqual(raised.exception.check, check)
            reading.assert_not_called()
            closing.assert_called_once_with(123)

    def test_mode_predicate_acceptance_equivalence_matrix(self):
        # Execute the revised measure against the original component predicate.
        good = types.SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o644,
            st_size=0, st_dev=1, st_ino=2, st_nlink=1, st_mtime_ns=3, st_ctime_ns=4)
        for root_owned in (False, True):
            for uid in (0, 100123):
                for kind in (stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK):
                    for mode in (0o000, 0o644, 0o755, 0o777, 0o4755, 0o1777):
                        expected = not root_owned or (uid == 0 and (stat.S_ISLNK(kind | mode) or not (kind | mode) & 0o022))
                        component = types.SimpleNamespace(**vars(good))
                        component.st_uid, component.st_mode = uid, kind | mode
                        def inspect(path):
                            return component if str(path) == '/fixture' else good
                        with self.subTest(root_owned=root_owned, uid=uid, kind=kind, mode=mode), \
                             mock.patch.object(Path, 'lstat', inspect), \
                             mock.patch.object(M.os, 'readlink', return_value='/target'), \
                             mock.patch.object(M.os, 'open', return_value=123), \
                             mock.patch.object(M.os, 'fstat', return_value=good), \
                             mock.patch.object(M.os, 'read', return_value=b''), mock.patch.object(M.os, 'close'):
                            try:
                                M.measure('/fixture', root_owned=root_owned)
                                accepted = True
                            except M.PreparationError as error:
                                self.assertEqual(error.code, 'CAP_MODE_REJECTED')
                                accepted = False
                            self.assertEqual(accepted, bool(expected))
        # Execute the revised FD checks against the original type/size conjunction.
        for kind in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFIFO):
            for size in (-1, 0, 64, 65):
                expected = stat.S_ISREG(kind) and 0 <= size <= 64
                fd_stat = types.SimpleNamespace(**vars(good))
                fd_stat.st_mode, fd_stat.st_size = kind | 0o644, size
                with self.subTest(kind=kind, size=size), mock.patch.object(Path, 'lstat', return_value=good), \
                     mock.patch.object(M.os, 'open', return_value=123), mock.patch.object(M.os, 'fstat', return_value=fd_stat), \
                     mock.patch.object(M.os, 'read', return_value=b''), mock.patch.object(M.os, 'close'):
                    try:
                        M.measure('/fixture', limit=64)
                        accepted = True
                    except M.PreparationError as error:
                        self.assertEqual(error.code, 'CAP_MODE_REJECTED')
                        accepted = False
                    self.assertEqual(accepted, bool(expected))

    def test_tool_mode_remapping_retags_subject_preserves_check(self):
        auth = mock.Mock()
        auth.authenticate.side_effect = M.PreparationError('CAP_MODE_REJECTED', 'SOURCE_INPUT', 'FILE_SIZE')
        with self.assertRaises(M.PreparationError) as raised:
            M.authenticate_tools(auth)
        self.assertEqual(M.preparation_status(raised.exception), {'schema': 1, 'status': 'FAIL',
            'code': 'CAP_PYTHON_MODE_REJECTED', 'subject': 'PYTHON', 'check': 'FILE_SIZE'})

    def test_tool_executable_setuid_and_owner_check_details(self):
        cases = (('/usr/bin/python3.10', 0o644, 0, 'PYTHON', 'EXECUTABLE'),
                 ('/usr/bin/newuidmap', 0o755, 0, 'NEWUIDMAP', 'SETUID'),
                 ('/usr/bin/newuidmap', 0o4755, 100123, 'NEWUIDMAP', 'FILE_OWNER'))
        for target, mode, uid, subject, check in cases:
            auth = mock.Mock()
            def checked(path, owner):
                row = record(path, mode=mode if path == target else 0o4755)
                row['uid'] = uid if path == target else 0
                return row
            auth.authenticate.side_effect = checked
            with self.subTest(check=check), self.assertRaises(M.PreparationError) as raised:
                M.authenticate_tools(auth)
            self.assertEqual((raised.exception.subject, raised.exception.check), (subject, check))

    def test_fixed_subject_lookup_does_not_classify_similar_or_private_paths(self):
        auth = self.authenticator()
        for path in ('/private/secret/keyring.gpg', '/etc/passwd-copy', '/etc/./passwd'):
            with mock.patch.object(M, 'measure', side_effect=M.PreparationError('CAP_MODE_REJECTED')) as measuring:
                self.fails('CAP_MODE_REJECTED', auth.authenticate, path)
            measuring.assert_called_once_with(path, subject='RUNTIME_INPUT')
        for path, subject in M.MODE_FIXED_SUBJECTS.items():
            with mock.patch.object(M, 'measure', side_effect=M.PreparationError('CAP_MODE_REJECTED')) as measuring:
                self.fails('CAP_MODE_REJECTED', auth.authenticate, path)
            measuring.assert_called_once_with(path, subject=subject)

    def test_nested_failure_does_not_leave_stale_mode_context(self):
        original = M.PreparationError('CAP_MODE_REJECTED', 'SOURCE_INPUT', 'FILE_SIZE')
        auth = mock.Mock()
        auth.authenticate.side_effect = original
        with self.assertRaises(M.PreparationError) as remapped:
            M.authenticate_tools(auth)
        self.assertEqual(remapped.exception.subject, 'PYTHON')
        self.assertEqual(original.subject, 'SOURCE_INPUT')
        with mock.patch.object(Path, 'lstat', return_value=types.SimpleNamespace(st_uid=100123, st_mode=stat.S_IFREG | 0o777)):
            with self.assertRaises(M.PreparationError) as later:
                M.measure('/private-second')
        self.assertEqual((later.exception.subject, later.exception.check), ('UNKNOWN', 'COMPONENT_OWNER'))
        self.assertEqual(M.preparation_status(M.PreparationError('CAP_OWNER_REJECTED')),
                         {'schema': 1, 'status': 'FAIL', 'code': 'CAP_OWNER_REJECTED'})

    def test_main_mode_receipt_omits_exception_text_and_measured_values(self):
        error = M.PreparationError('CAP_MODE_REJECTED', 'ARCHIVE_KEYRING', 'COMPONENT_WRITABLE')
        error.args = ('/private/secret-user uid=100123 mode=0777 token-secret',)
        output = io.StringIO()
        with mock.patch.object(M.sys, 'argv', ['prepare', '--root', str(self.root), '--expected-source-index-sha256', '0' * 64]), \
             mock.patch.object(M, 'prepare', side_effect=error), mock.patch.object(M, 'write_new') as writing, \
             contextlib.redirect_stdout(output):
            self.assertEqual(M.main(), 2)
        expected = M.preparation_status(error)
        self.assertEqual(json.loads(output.getvalue()), expected)
        self.assertEqual(writing.call_args.args[1], expected)
        for secret in ('private', '100123', '0777', 'token-secret'):
            self.assertNotIn(secret, output.getvalue())

    def test_main_emits_only_finite_failure(self):
        output = io.StringIO()
        with mock.patch.object(M.sys, 'argv', ['prepare', '--root', str(self.root), '--expected-source-index-sha256', '0' * 64]), \
             mock.patch.object(M, 'prepare', side_effect=RuntimeError('secret host installed version')), \
             contextlib.redirect_stdout(output):
            self.assertEqual(M.main(), 2)
        expected = {'schema': 1, 'status': 'FAIL', 'code': 'CAP_UNEXPECTED_ERROR'}
        self.assertEqual(json.loads(output.getvalue()), expected)
        self.assertEqual(json.loads((self.root / 'capability-preparation-status.json').read_text()), expected)
        self.assertNotIn('secret', output.getvalue())


if __name__ == '__main__':
    unittest.main()

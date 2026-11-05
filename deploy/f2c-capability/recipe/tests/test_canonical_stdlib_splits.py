"""Pure-data coverage for three canonical Jammy optional stdlib packages.

The complete 124-member fixture contains reviewed public archive metadata, not
runtime acceptance records. Package member bytes are never imported or run.
Measured files, ownership observations, archive reads and command results below
are synthetic/mocked. No host authentication, package/helper or namespace action
is performed, and these tests do not identify the failed hosted runtime object.
"""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'canonical_stdlib_split_inputs', ROOT / 'recipe/prepare_capability_inputs.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

MAPPINGS = {
    'python3-distutils': 'python3-stdlib-extensions',
    'python3-lib2to3': 'python3-stdlib-extensions',
    'python3.10-venv': 'python3.10',
}
COUNTS = {'python3-distutils': 48, 'python3-lib2to3': 73, 'python3.10-venv': 3}
PREFIXES = {'python3-distutils': '/usr/lib/python3.10/distutils/',
            'python3-lib2to3': '/usr/lib/python3.10/lib2to3/',
            'python3.10-venv': '/usr/lib/python3.10/ensurepip/'}
PREVIOUS_OPTIONAL = {
    'libpython3.10-dev': 'python3.10', 'python3.10-dev': 'python3.10',
    'libpython3.10-testsuite': 'python3.10', 'python3.10-examples': 'python3.10',
    'python3-gdbm': 'python3-stdlib-extensions',
    'python3-tk': 'python3-stdlib-extensions',
}
MEMBER_KEYS = ('kind', 'sha256', 'size', 'mode', 'uid', 'gid')


def signed_member(member):
    return {key: member[key] for key in MEMBER_KEYS}


def measured(member, path=None, aliases=None):
    row = {key: member[key] for key in MEMBER_KEYS if key != 'kind'}
    row.update(path=path or member['path'], resolved_path=member['path'],
               device=1, inode=2, nlink=1, aliases=aliases or [])
    return row


class CanonicalStdlibSplits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((ROOT / 'inputs/canonical-python-stdlib-splits.json').read_text())
        cls.packages = {package['name']: package for package in cls.fixture['packages']}
        cls.inventory = [(package['name'], member) for package in cls.fixture['packages']
                         for member in package['members']]

    def setUp(self):
        # Accidental native execution must fail even if a test forgets a mock.
        for name in ('command', 'signature_command'):
            patcher = mock.patch.object(M, name, side_effect=AssertionError('native execution forbidden'))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(M.subprocess, 'run', side_effect=AssertionError('process execution forbidden'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def fails(self, code, function, *args, **kwargs):
        with self.assertRaises(M.PreparationError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.code, code)
        self.assertEqual(str(error.exception), code)

    def auth(self):
        installed, records = {}, {}
        for name, package in self.packages.items():
            index = package['index_reference']
            installed[name] = {'name': name, 'version': package['version'],
                               'architecture': package['architecture']}
            row = {'Package': name, 'Version': package['version'],
                   'Architecture': package['architecture'], 'Source': package['source'],
                   'SHA256': package['archive']['sha256'], 'Size': str(package['archive']['size']),
                   '_suite': index['suite'], '_component': index['component'],
                   '_stanza': index['stanza_ordinal_split_double_newline']}
            records[(name, package['version'], package['architecture'])] = [row]
        auth = M.Authenticator(Path('/fixture'), mock.Mock(), Path('/fixture'), installed, records)
        # Required base packages are already authenticated in this isolated
        # harness. They supply no members and are not part of the replay ledger.
        auth.members = {name: {} for name in M.PYTHON_PACKAGES}
        auth.members.update({name: {member['path'][1:]: signed_member(member)
                                   for member in package['members']}
                             for name, package in self.packages.items()})
        auth.packages = {name: {
            'name': name, 'version': package['version'], 'architecture': package['architecture'],
            'source': package['source'], 'suite': package['index_reference']['suite'],
            'component': package['index_reference']['component'],
            'stanza': package['index_reference']['stanza_ordinal_split_double_newline'],
            'archive': {key: package['archive'][key] for key in ('sha256', 'size')},
            'members': {}, 'owner_paths': [],
        } for name, package in self.packages.items()}
        auth.owners = {member['path']: name for name, member in self.inventory}
        return auth

    def record(self, auth, name):
        package = self.packages[name]
        return auth.records[(name, package['version'], package['architecture'])][0]

    def authenticate_all(self):
        auth = self.auth()
        rows = {member['path']: measured(member) for _, member in self.inventory}
        with mock.patch.object(M, 'measure', side_effect=lambda path, **kwargs: (copy.deepcopy(rows[path]), b'')):
            M.authenticate_python(auth, sorted(rows))
        return auth

    def ledger(self, auth):
        return {'schema': M.LOCK_SCHEMA, 'member_protocol': M.MEMBER_PROTOCOL,
                'member_attributions': auth.member_attributions, 'runtime_files': auth.files,
                'packages': auth.packages, 'tools': {}, 'bootstrap_files': {},
                'python_source_paths': sorted(auth.files)}

    def replay(self, ledger):
        # Fixed executable coverage is tested separately by the unchanged suite.
        with mock.patch.object(M, 'TOOLS', {}):
            M.verify_member_attributions(ledger)

    def alias_fixture(self):
        auth = self.auth()
        target_owner = 'python3-distutils'
        alias_owner = 'python3-lib2to3'
        member = self.packages[target_owner]['members'][0]
        path = '/usr/lib/python3.10/canonical-split-fixture-alias.py'
        link = {'kind': 'symlink', 'target': member['path'], 'mode': 0o777, 'uid': 0, 'gid': 0}
        auth.members[alias_owner][path[1:]] = link
        auth.owners[path] = alias_owner
        alias = {key: value for key, value in link.items() if key != 'kind'}
        alias.update(path=path, device=1, inode=3)
        return auth, path, measured(member, path, [alias]), alias_owner, target_owner

    def package_harness(self, name):
        auth = self.auth()
        del auth.members[name]
        del auth.packages[name]
        row = self.record(auth, name)
        auth.m.fields.return_value = dict(row)
        auth.m.package_identity.side_effect = lambda value: tuple(
            value[key] for key in ('Package', 'Version', 'Architecture'))
        members = {member['path'][1:]: signed_member(member) for member in self.packages[name]['members']}
        archive = {key: self.packages[name]['archive'][key] for key in ('sha256', 'size')}
        return auth, row, members, archive

    def test_exact_three_mappings_and_schema3_identity(self):
        self.assertEqual(M.PYTHON_OPTIONAL_PACKAGES, PREVIOUS_OPTIONAL | MAPPINGS)
        self.assertEqual(M.PYTHON_PACKAGES, ('libpython3.10-minimal', 'libpython3.10-stdlib'))
        self.assertNotIn('python3.10', M.PYTHON_OPTIONAL_PACKAGES)
        self.assertEqual(M.LOCK_SCHEMA, 3)
        self.assertEqual(M.MEMBER_PROTOCOL, 'signed-package-hop-attribution-v1')
        self.assertEqual(M.USRMERGE_LAYOUT_POLICY, 'root-usrmerge-v1')

    def test_fixture_has_all_124_exact_regular_members(self):
        self.assertEqual(self.fixture['schema'], 1)
        self.assertEqual(self.fixture['snapshot'], M.SNAPSHOT)
        self.assertEqual(len(self.fixture['packages']), 3)
        self.assertEqual(set(self.packages), set(MAPPINGS))
        self.assertEqual(len(self.inventory), 124)
        self.assertEqual(len({member['path'] for _, member in self.inventory}), 124)
        for name, package in self.packages.items():
            with self.subTest(package=name):
                self.assertEqual(package['source'].split()[0], MAPPINGS[name])
                self.assertEqual(len(package['members']), COUNTS[name])
                self.assertRegex(package['archive']['sha256'], r'^[0-9a-f]{64}$')
                self.assertGreater(package['archive']['size'], 0)
                self.assertLessEqual(package['archive']['size'], 64 * 1024 * 1024)
                self.assertTrue(package['archive']['url'].startswith('https://'))
                for member in package['members']:
                    self.assertTrue(member['path'].startswith(PREFIXES[name]))
                    self.assertTrue(member['path'].endswith('.py'))
                    self.assertEqual(member['kind'], 'file')
                    self.assertRegex(member['sha256'], r'^[0-9a-f]{64}$')
                    expected_mode = 0o755 if member['path'] == '/usr/lib/python3.10/lib2to3/pgen2/token.py' else 0o644
                    self.assertEqual((member['uid'], member['gid'], member['mode']), (0, 0, expected_mode))
                    self.assertIs(type(member['size']), int)
                    self.assertGreaterEqual(member['size'], 0)

    def test_all_124_members_bind_to_exact_owner_source_archive_and_member(self):
        auth = self.authenticate_all()
        self.assertEqual(len(auth.files), 124)
        for name, member in self.inventory:
            with self.subTest(package=name, path=member['path']):
                path = member['path']
                self.assertEqual(auth.files[path]['package'], name)
                proof = auth.member_attributions[path]
                self.assertEqual(proof['hops'], [])
                self.assertEqual(proof['target']['path'], path)
                self.assertEqual(proof['target']['member'], path[1:])
                self.assertEqual(proof['target']['package'], name)
                record = proof['target']['package_record']
                package = self.packages[name]
                for key in ('name', 'version', 'architecture', 'source'):
                    self.assertEqual(record[key], package[key])
                self.assertEqual(record['suite'], package['index_reference']['suite'])
                self.assertEqual(record['component'], package['index_reference']['component'])
                self.assertEqual(record['stanza'], package['index_reference']['stanza_ordinal_split_double_newline'])
                self.assertEqual(record['archive_sha256'], package['archive']['sha256'])
                self.assertEqual(record['archive_size'], package['archive']['size'])
                self.assertEqual(auth.packages[name]['members'][path[1:]], signed_member(member))

    def test_full_inventory_schema3_ledger_replays_without_native_commands(self):
        ledger = self.ledger(self.authenticate_all())
        self.replay(ledger)
        self.assertEqual(len(ledger['member_attributions']), 124)
        self.assertEqual(sum(len(package['members']) for package in ledger['packages'].values()), 124)

    def test_all_124_wrong_allowed_owners_cannot_borrow_another_archive(self):
        names = list(MAPPINGS)
        for name, member in self.inventory:
            with self.subTest(package=name, path=member['path']):
                auth = self.auth()
                auth.owners[member['path']] = names[(names.index(name) + 1) % len(names)]
                with mock.patch.object(M, 'measure', return_value=(measured(member), b'')):
                    self.fails('CAP_BYTES_REJECTED', M.authenticate_python, auth, [member['path']])

    def test_all_124_unlisted_owners_rejected_before_package_lookup(self):
        for _, member in self.inventory:
            with self.subTest(path=member['path']):
                auth = self.auth()
                auth.owners[member['path']] = 'unlisted-package'
                with mock.patch.object(auth, 'package', wraps=auth.package) as package:
                    self.fails('CAP_RUNTIME_UNAUTHENTICATED', M.authenticate_python, auth, [member['path']])
                self.assertNotIn(mock.call('unlisted-package'), package.call_args_list)

    def test_all_124_wrong_sources_fail_before_member_measurement(self):
        for name, member in self.inventory:
            with self.subTest(package=name, path=member['path']):
                auth = self.auth()
                self.record(auth, name)['Source'] = 'unrelated-source'
                with mock.patch.object(M, 'measure') as measurement:
                    self.fails('CAP_RUNTIME_UNAUTHENTICATED', M.authenticate_python, auth, [member['path']])
                measurement.assert_not_called()

    def test_source_identifier_is_exact_not_prefix_or_cross_mapping(self):
        for name, expected in MAPPINGS.items():
            for source in (expected, expected + ' (3.10.12-1~22.04.18)'):
                with self.subTest(package=name, valid_source=source):
                    M.python_package_policy(name, source)
            for source in (None, '', name, expected.upper(), expected + '-extra', 'prefix-' + expected,
                           'python3.10' if expected != 'python3.10' else 'python3-stdlib-extensions'):
                with self.subTest(package=name, invalid_source=source):
                    self.fails('CAP_RUNTIME_UNAUTHENTICATED', M.python_package_policy, name, source)

    def test_missing_source_does_not_default_new_binary_names_into_allowlist(self):
        for name, package in self.packages.items():
            auth = self.auth()
            del self.record(auth, name)['Source']
            self.fails('CAP_RUNTIME_UNAUTHENTICATED', M.authenticate_python, auth, [package['members'][0]['path']])

    def test_pdb_does_not_justify_adding_python310_binary_package(self):
        evidence = self.fixture['pdb_hypothesis']
        self.assertEqual(evidence['owner'], 'libpython3.10-stdlib')
        self.assertEqual(evidence['source'], 'python3.10')
        self.assertEqual(evidence['member']['path'], '/usr/lib/python3.10/pdb.py')
        self.assertEqual(evidence['excluded_python3_10_scanned_count'], 0)
        M.python_package_policy(evidence['owner'], evidence['source'])
        M.verify_member(measured(evidence['member']),
                        {evidence['member']['path'][1:]: signed_member(evidence['member'])})
        self.fails('CAP_RUNTIME_UNAUTHENTICATED', M.python_package_policy, 'python3.10', 'python3.10')

    def test_exact_installed_version_and_architecture_still_required(self):
        for name, package in self.packages.items():
            for key, value in (('version', package['version'] + '+changed'),
                               ('architecture', 'all' if package['architecture'] == 'amd64' else 'amd64'),
                               ('architecture', 'arm64')):
                with self.subTest(package=name, key=key, value=value):
                    auth = self.auth()
                    auth.installed[name][key] = value
                    self.fails('CAP_PACKAGE_UNAUTHENTICATED', M.authenticate_python,
                               auth, [package['members'][0]['path']])

    def test_absent_or_ambiguous_signed_package_selection_still_rejected(self):
        for name, package in self.packages.items():
            for change in ('no-record', 'no-installed', 'duplicate-installed'):
                with self.subTest(package=name, change=change):
                    auth = self.auth()
                    if change == 'no-record':
                        auth.records.pop((name, package['version'], package['architecture']))
                    elif change == 'no-installed':
                        auth.installed.pop(name)
                    else:
                        auth.installed['duplicate'] = dict(auth.installed[name])
                    self.fails('CAP_PACKAGE_UNAUTHENTICATED', M.authenticate_python,
                               auth, [package['members'][0]['path']])

    def test_all_124_member_byte_digests_are_required(self):
        for name, member in self.inventory:
            with self.subTest(package=name, path=member['path']):
                row = measured(member)
                row['sha256'] = '0' * 64
                with mock.patch.object(M, 'measure', return_value=(row, b'')):
                    self.fails('CAP_BYTES_REJECTED', M.authenticate_python, self.auth(), [member['path']])

    def test_size_mode_ownership_type_and_member_kind_checks_unchanged(self):
        for name, package in self.packages.items():
            member = package['members'][0]
            changes = [('size', member['size'] + 1), ('mode', 0o666), ('uid', 1), ('gid', 1), ('uid', False)]
            for key, value in changes:
                with self.subTest(package=name, key=key, value=value):
                    row = measured(member)
                    row[key] = value
                    with mock.patch.object(M, 'measure', return_value=(row, b'')):
                        self.fails('CAP_BYTES_REJECTED', M.authenticate_python, self.auth(), [member['path']])
            for kind in ('symlink', 'hardlink', 'directory', None):
                with self.subTest(package=name, kind=kind):
                    auth = self.auth()
                    if kind is None:
                        del auth.members[name][member['path'][1:]]
                    else:
                        auth.members[name][member['path'][1:]]['kind'] = kind
                    with mock.patch.object(M, 'measure', return_value=(measured(member), b'')):
                        self.fails('CAP_BYTES_REJECTED', M.authenticate_python, auth, [member['path']])

    def test_ambiguous_usrmerge_member_spellings_are_not_an_exemption(self):
        for name, package in self.packages.items():
            member = package['members'][0]
            auth = self.auth()
            auth.members[name][member['path'][5:]] = signed_member(member)
            with mock.patch.object(M, 'measure', return_value=(measured(member), b'')):
                self.fails('CAP_BYTES_REJECTED', M.authenticate_python, auth, [member['path']])

    def test_allowed_cross_package_alias_still_authenticates_each_hop(self):
        auth, path, row, alias_owner, target_owner = self.alias_fixture()
        with mock.patch.object(M, 'measure', return_value=(row, b'')):
            M.authenticate_python(auth, [path])
        proof = auth.member_attributions[path]
        self.assertEqual(proof['hops'][0]['package'], alias_owner)
        self.assertEqual(proof['target']['package'], target_owner)
        self.assertEqual(proof['hops'][0]['package_record']['source'], self.packages[alias_owner]['source'])
        self.assertEqual(proof['target']['package_record']['source'], self.packages[target_owner]['source'])

    def test_wrong_alias_or_target_owner_and_source_are_not_exempted(self):
        for subject in ('alias', 'target'):
            for change in ('wrong-owner', 'wrong-source', 'unknown-owner'):
                with self.subTest(subject=subject, change=change):
                    auth, path, row, alias_owner, target_owner = self.alias_fixture()
                    subject_path = path if subject == 'alias' else row['resolved_path']
                    owner = alias_owner if subject == 'alias' else target_owner
                    code = 'CAP_RUNTIME_UNAUTHENTICATED'
                    if change == 'wrong-source':
                        self.record(auth, owner)['Source'] = 'unrelated-source'
                    else:
                        auth.owners[subject_path] = 'python3.10-venv' if change == 'wrong-owner' else 'unlisted-package'
                        if change == 'wrong-owner':
                            code = 'CAP_BYTES_REJECTED'
                    with mock.patch.object(M, 'measure', return_value=(row, b'')):
                        self.fails(code, M.authenticate_python, auth, [path])

    def test_cached_python_path_rechecks_target_source(self):
        auth, path, row, _, target_owner = self.alias_fixture()
        with mock.patch.object(M, 'measure', return_value=(row, b'')):
            M.authenticate_python(auth, [path])
        self.record(auth, target_owner)['Source'] = 'unrelated-source'
        with mock.patch.object(M, 'measure') as measurement:
            self.fails('CAP_RUNTIME_UNAUTHENTICATED', M.authenticate_python, auth, [path])
        measurement.assert_not_called()

    def test_alias_literal_metadata_and_resolution_checks_unchanged(self):
        for change in ('literal', 'mode', 'uid', 'gid', 'resolved', 'unconsumed', 'too-many'):
            with self.subTest(change=change):
                auth, path, row, _, _ = self.alias_fixture()
                alias = row['aliases'][0]
                if change == 'literal':
                    alias['target'] = '/usr/lib/./python3.10/' + alias['target'].split('/python3.10/', 1)[1]
                elif change in ('mode', 'uid', 'gid'):
                    alias[change] = 0o700 if change == 'mode' else 1
                elif change == 'resolved':
                    row['resolved_path'] = '/private/elsewhere.py'
                elif change == 'unconsumed':
                    alias['path'] = '/private/unconsumed.py'
                else:
                    row['aliases'] *= 17
                with mock.patch.object(M, 'measure', return_value=(row, b'')):
                    self.fails('CAP_BYTES_REJECTED', M.authenticate_python, auth, [path])

    def test_replay_rejects_each_new_packages_wrong_source(self):
        valid = self.ledger(self.authenticate_all())
        for name in MAPPINGS:
            with self.subTest(package=name):
                changed = copy.deepcopy(valid)
                changed['packages'][name]['source'] = 'unrelated-source'
                for proof in changed['member_attributions'].values():
                    if proof['target']['package'] == name:
                        proof['target']['package_record']['source'] = 'unrelated-source'
                self.fails('CAP_RUNTIME_UNAUTHENTICATED', self.replay, changed)

    def test_mocked_archive_path_still_checks_exact_control_and_archive_identity(self):
        for name in MAPPINGS:
            with self.subTest(package=name):
                auth, _, members, archive = self.package_harness(name)
                with mock.patch.object(M, 'command', side_effect=[(0, b'control'), (0, b'archive')]) as command, \
                     mock.patch.object(M, 'archive_members', return_value=members), \
                     mock.patch.object(M, 'measure', return_value=(archive, b'')):
                    self.assertEqual(auth.package(name), members)
                self.assertEqual(command.call_count, 2)
                auth.m.download_payload.assert_called_once()
                self.assertEqual(auth.packages[name]['archive'], archive)

    def test_mocked_archive_control_mismatch_stops_before_member_parse(self):
        for name in MAPPINGS:
            for key, value in (('Package', 'unrelated-package'), ('Version', '0'), ('Architecture', 'arm64')):
                with self.subTest(package=name, key=key):
                    auth, row, _, _ = self.package_harness(name)
                    auth.m.fields.return_value = dict(row, **{key: value})
                    with mock.patch.object(M, 'command', return_value=(0, b'control')) as command, \
                         mock.patch.object(M, 'archive_members') as parse:
                        self.fails('CAP_ARCHIVE_REJECTED', auth.package, name)
                    self.assertEqual(command.call_count, 1)
                    parse.assert_not_called()

    def test_mocked_archive_sha256_size_and_command_failures_still_rejected(self):
        for name in MAPPINGS:
            for change in ('sha256', 'size', 'control-status', 'data-status'):
                with self.subTest(package=name, change=change):
                    auth, _, members, archive = self.package_harness(name)
                    responses = [(0, b'control'), (0, b'archive')]
                    if change == 'sha256':
                        archive['sha256'] = '0' * 64
                    elif change == 'size':
                        archive['size'] += 1
                    else:
                        responses[0 if change == 'control-status' else 1] = (1, b'failed')
                    with mock.patch.object(M, 'command', side_effect=responses), \
                         mock.patch.object(M, 'archive_members', return_value=members), \
                         mock.patch.object(M, 'measure', return_value=(archive, b'')):
                        self.fails('CAP_ARCHIVE_REJECTED', auth.package, name)

    def test_archive_size_bound_still_precedes_download(self):
        for name in MAPPINGS:
            for size in ('0', '-1', str(64 * 1024 * 1024 + 1)):
                with self.subTest(package=name, size=size):
                    auth, row, _, _ = self.package_harness(name)
                    row['Size'] = size
                    self.fails('CAP_ARCHIVE_REJECTED', auth.package, name)
                    auth.m.download_payload.assert_not_called()


if __name__ == '__main__':
    unittest.main()

"""Pure supplied-data tests. Never authenticate this host or execute archive code.

The canonical sysconfig/sitecustomize member metadata below was read from the
exact official Jammy archives identified by SHA256, after checking the retained
signed-snapshot package records. No archive bytes or generated Python source are
included or executed. Suite/stanza and inode fields in the harness are synthetic;
the member metadata, package versions and archive hashes/sizes are exact.
"""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('package_hop_inputs', Path(__file__).resolve().parents[1] / 'prepare_capability_inputs.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

MINIMAL = 'libpython3.10-minimal'
STDLIB = 'libpython3.10-stdlib'
ALIAS = '/usr/lib/python3.10/_sysconfigdata__linux_x86_64-linux-gnu.py'
TARGET = '/usr/lib/python3.10/_sysconfigdata__x86_64-linux-gnu.py'
LITERAL = '_sysconfigdata__x86_64-linux-gnu.py'
VERSION = '3.10.12-1~22.04.18'
ARCHIVES = {
    MINIMAL: ('ccbd08562769e0a38b34005dc8bde200838e6c6530cc04ac34525129ed42d847', 816508),
    STDLIB: ('ee09afbc981a4aaa00053f019eab6b45dcc4dbeb4ca4fcc4277eff539e09f228', 1850286),
}
REGULAR = {'kind': 'file', 'sha256': '3c8090c15a9174d8f7c5ce7707c94312cf72fd1cdf52af5257958223d0312d91',
           'size': 27484, 'mode': 0o644, 'uid': 0, 'gid': 0}
LINK = {'kind': 'symlink', 'target': LITERAL, 'mode': 0o777, 'uid': 0, 'gid': 0}
SITE = '/usr/lib/python3.10/sitecustomize.py'
SITE_TARGET = '/etc/python3.10/sitecustomize.py'
SITE_REGULAR = {'kind': 'file', 'sha256': '43d81125d92376b1a69d53a71126a041cc9a18d8080e92dea0a2ae23be138b1e',
                'size': 155, 'mode': 0o644, 'uid': 0, 'gid': 0}


def alias(path=ALIAS, target=LITERAL):
    return {'path': path, 'target': target, 'uid': 0, 'gid': 0, 'mode': 0o777, 'device': 1, 'inode': 3}


def measured(path=ALIAS, target=TARGET, regular=None, aliases=None):
    regular = REGULAR if regular is None else regular
    result = {k: v for k, v in regular.items() if k != 'kind'}
    result.update(path=path, resolved_path=target, device=1, inode=2, nlink=1,
                  aliases=[alias()] if aliases is None else aliases)
    return result


class PackageHopFixtures(unittest.TestCase):
    def fails(self, code, fn, *args, **kwargs):
        with self.assertRaises(M.PreparationError) as error:
            fn(*args, **kwargs)
        self.assertEqual(error.exception.code, code)
        self.assertEqual(str(error.exception), code)

    def auth(self, members=None, owners=None):
        installed = {name: {'name': name, 'version': VERSION, 'architecture': 'amd64'} for name in ARCHIVES}
        records = {(name, VERSION, 'amd64'): [{'Package': name, 'Version': VERSION, 'Architecture': 'amd64',
                    'Source': 'python3.10'}] for name in ARCHIVES}
        auth = M.Authenticator(Path('/fixture'), mock.Mock(), Path('/fixture'), installed, records)
        auth.members = copy.deepcopy(members or {STDLIB: {ALIAS[1:]: LINK}, MINIMAL: {TARGET[1:]: REGULAR}})
        auth.packages = {name: {'name': name, 'version': VERSION, 'architecture': 'amd64', 'source': 'python3.10',
                'suite': 'jammy-updates', 'component': 'main', 'stanza': 0,
                'archive': {'sha256': ARCHIVES[name][0], 'size': ARCHIVES[name][1]},
                'members': {}, 'owner_paths': []} for name in ARCHIVES}
        ownership = {ALIAS: STDLIB, TARGET: MINIMAL} if owners is None else owners
        def owner(path):
            M.need(path in ownership, 'CAP_OWNER_REJECTED')
            return ownership[path]
        auth.owner = mock.Mock(side_effect=owner)
        return auth

    def authenticated(self):
        auth = self.auth()
        row = measured()
        with mock.patch.object(M, 'measure', return_value=(row, b'')):
            M.authenticate_python(auth, [ALIAS])
        return auth

    def lock(self):
        auth = self.authenticated()
        return {'member_protocol': M.MEMBER_PROTOCOL, 'member_attributions': auth.member_attributions,
                'runtime_files': auth.files, 'packages': auth.packages, 'tools': {}, 'bootstrap_files': {},
                'python_source_paths': [ALIAS]}

    def replay(self, lock):
        # This unit isolates provenance, not the separate fixed executable set.
        with mock.patch.object(M, 'TOOLS', {}), mock.patch.object(M, 'command', side_effect=AssertionError('native execution')):
            M.verify_member_attributions(lock)

    def test_exact_official_cross_package_alias_now_attributes_both_members(self):
        auth = self.authenticated()
        proof = auth.member_attributions[ALIAS]
        self.assertEqual(auth.files[ALIAS]['package'], STDLIB)
        self.assertEqual(proof['hops'][0]['package'], STDLIB)
        self.assertEqual(proof['target']['package'], MINIMAL)
        self.assertEqual(proof['hops'][0]['member'], ALIAS[1:])
        self.assertEqual(proof['target']['member'], TARGET[1:])
        self.assertEqual(proof['target']['package_record']['archive_sha256'], ARCHIVES[MINIMAL][0])
        self.assertEqual(proof['target']['package_record']['version'], VERSION)
        self.assertEqual(auth.packages[MINIMAL]['members'][TARGET[1:]], REGULAR)
        self.assertEqual(auth.packages[STDLIB]['members'][ALIAS[1:]], LINK)

    def test_single_archive_does_not_gain_implicit_cross_package_fallback(self):
        self.fails('CAP_BYTES_REJECTED', M.verify_member, measured(), {ALIAS[1:]: LINK})

    def test_exact_official_absolute_sitecustomize_alias_preserved(self):
        link = dict(LINK, target=SITE_TARGET)
        auth = self.auth({MINIMAL: {SITE[1:]: link, SITE_TARGET[1:]: SITE_REGULAR}, STDLIB: {}},
                         {SITE: MINIMAL, SITE_TARGET: MINIMAL})
        row = measured(SITE, SITE_TARGET, SITE_REGULAR, [alias(SITE, SITE_TARGET)])
        with mock.patch.object(M, 'measure', return_value=(row, b'')):
            M.authenticate_python(auth, [SITE])
        self.assertEqual(auth.member_attributions[SITE]['target']['package'], MINIMAL)

    def test_each_nonlayout_hop_is_independently_authenticated(self):
        bridge = '/usr/lib/python3.10/bridge.py'
        auth = self.auth({STDLIB: {ALIAS[1:]: dict(LINK, target='bridge.py')},
                          MINIMAL: {bridge[1:]: LINK, TARGET[1:]: REGULAR}},
                         {ALIAS: STDLIB, bridge: MINIMAL, TARGET: MINIMAL})
        row = measured(aliases=[alias(target='bridge.py'), alias(bridge)])
        with mock.patch.object(M, 'measure', return_value=(row, b'')):
            M.authenticate_python(auth, [ALIAS])
        self.assertEqual([x['package'] for x in auth.member_attributions[ALIAS]['hops']], [STDLIB, MINIMAL])
        self.assertIn(mock.call(bridge), auth.owner.call_args_list)

    def test_signed_parent_relative_target_remains_supported(self):
        start = '/usr/lib/python3.10/config/link.py'
        literal = '../_sysconfigdata__x86_64-linux-gnu.py'
        auth = self.auth({STDLIB: {start[1:]: dict(LINK, target=literal)}, MINIMAL: {TARGET[1:]: REGULAR}},
                         {start: STDLIB, TARGET: MINIMAL})
        row = measured(start, TARGET, aliases=[alias(start, literal)])
        with mock.patch.object(M, 'measure', return_value=(row, b'')):
            auth.authenticate(start)

    def test_tampered_target_metadata_rejected_individually(self):
        for key, value in (('sha256', '0' * 64), ('size', 1), ('mode', 0o755), ('uid', 1), ('gid', 1)):
            with self.subTest(key=key):
                auth = self.auth()
                row = measured()
                row[key] = value
                with mock.patch.object(M, 'measure', return_value=(row, b'')):
                    self.fails('CAP_BYTES_REJECTED', auth.authenticate, ALIAS)

    def test_tampered_symlink_metadata_rejected_individually(self):
        for key, value in (('mode', 0o700), ('uid', 1), ('gid', 1)):
            with self.subTest(key=key):
                auth = self.auth()
                row = measured()
                row['aliases'][0][key] = value
                with mock.patch.object(M, 'measure', return_value=(row, b'')):
                    self.fails('CAP_BYTES_REJECTED', auth.authenticate, ALIAS)

    def test_changed_literal_target_not_hidden_by_same_resolution(self):
        row = measured(aliases=[alias(target='./' + LITERAL)])
        with mock.patch.object(M, 'measure', return_value=(row, b'')):
            self.fails('CAP_BYTES_REJECTED', self.auth().authenticate, ALIAS)

    def test_wrong_target_owner_never_unions_archive_members(self):
        auth = self.auth(owners={ALIAS: STDLIB, TARGET: STDLIB})
        with mock.patch.object(M, 'measure', return_value=(measured(), b'')):
            self.fails('CAP_BYTES_REJECTED', auth.authenticate, ALIAS)

    def test_wrong_alias_owner_never_unions_archive_members(self):
        auth = self.auth(owners={ALIAS: MINIMAL, TARGET: MINIMAL})
        with mock.patch.object(M, 'measure', return_value=(measured(), b'')):
            self.fails('CAP_BYTES_REJECTED', auth.authenticate, ALIAS)

    def test_unowned_target_rejected(self):
        with mock.patch.object(M, 'measure', return_value=(measured(), b'')):
            self.fails('CAP_OWNER_REJECTED', self.auth(owners={ALIAS: STDLIB}).authenticate, ALIAS)

    def test_unknown_python_target_owner_blocked_before_package_download(self):
        auth = self.auth(owners={ALIAS: STDLIB, TARGET: 'unlisted-package'})
        actual = auth.package
        auth.package = mock.Mock(side_effect=actual)
        with mock.patch.object(M, 'measure', return_value=(measured(), b'')):
            self.fails('CAP_RUNTIME_UNAUTHENTICATED', M.authenticate_python, auth, [ALIAS])
        self.assertNotIn(mock.call('unlisted-package'), auth.package.call_args_list)

    def test_python_target_source_policy_is_not_alias_owner_only(self):
        auth = self.auth()
        auth.records[(MINIMAL, VERSION, 'amd64')][0]['Source'] = 'different-source'
        with mock.patch.object(M, 'measure', return_value=(measured(), b'')):
            self.fails('CAP_RUNTIME_UNAUTHENTICATED', M.authenticate_python, auth, [ALIAS])

    def test_wrong_member_kinds_and_missing_members_rejected(self):
        for package, path, value in ((STDLIB, ALIAS, REGULAR), (MINIMAL, TARGET, LINK),
                                    (MINIMAL, TARGET, {'kind': 'hardlink', 'target': 'elsewhere'}),
                                    (MINIMAL, TARGET, None), (STDLIB, ALIAS, None)):
            auth = self.auth()
            if value is None:
                del auth.members[package][path[1:]]
            else:
                auth.members[package][path[1:]] = value
            with mock.patch.object(M, 'measure', return_value=(measured(), b'')):
                self.fails('CAP_BYTES_REJECTED', auth.authenticate, ALIAS)

    def test_ambiguous_usrmerge_archive_members_rejected_even_identical(self):
        auth = self.auth()
        auth.members[MINIMAL][TARGET[5:]] = REGULAR
        with mock.patch.object(M, 'measure', return_value=(measured(), b'')):
            self.fails('CAP_BYTES_REJECTED', auth.authenticate, ALIAS)

    def test_forged_resolution_and_unconsumed_alias_rejected_before_ownership(self):
        for changed in (measured(target='/private/elsewhere'), measured(aliases=[]),
                        measured(aliases=[alias('/private/unconsumed', LITERAL)])):
            auth = self.auth()
            with mock.patch.object(M, 'measure', return_value=(changed, b'')):
                self.fails('CAP_BYTES_REJECTED', auth.authenticate, ALIAS)
            auth.owner.assert_not_called()

    def test_repeated_hop_cycle_rejected(self):
        row = measured(aliases=[alias(target='b.py'), alias('/usr/lib/python3.10/b.py', ALIAS), alias()])
        self.fails('CAP_BYTES_REJECTED', M.member_chain, row)

    def test_repeated_usrmerge_hop_with_new_suffix_is_not_a_cycle(self):
        row = measured('/lib/front', '/usr/lib/back', aliases=[alias('/lib', 'usr/lib'),
                       alias('/usr/lib/front', '/lib/back'), alias('/lib', 'usr/lib')])
        members = {'lib/front': dict(LINK, target='/lib/back'), 'lib/back': REGULAR}
        M.verify_member(row, members)

    def test_alias_root_escape_rejected_but_not_parent_relative_in_general(self):
        row = measured(aliases=[alias(target='../../../../../../escape')])
        self.fails('CAP_BYTES_REJECTED', M.member_chain, row)

    def test_hop_count_is_bounded(self):
        self.fails('CAP_BYTES_REJECTED', M.member_chain, measured(aliases=[alias()] * 17))

    def test_noncanonical_paths_rejected(self):
        for path in ('usr/lib/a.py', '//usr/lib/a.py', '/usr/../lib/a.py', '/usr/./lib/a.py', '/usr//lib/a.py', '/x\0y'):
            self.fails('CAP_BYTES_REJECTED', M.member_chain, measured(path=path))

    def test_fixed_usrmerge_attribution_is_layout_not_signed_package(self):
        start, target = '/lib/tool', '/usr/lib/tool'
        auth = self.auth({MINIMAL: {'lib/tool': REGULAR}, STDLIB: {}}, {start: MINIMAL, target: MINIMAL})
        row = measured(start, target, aliases=[alias('/lib', 'usr/lib')])
        with mock.patch.object(M, 'measure', return_value=(row, b'')):
            auth.authenticate(start)
        self.assertEqual(auth.member_attributions[start]['hops'], [{'kind': 'layout-policy', 'path': '/lib',
                          'target': 'usr/lib', 'layout_policy': M.USRMERGE_LAYOUT_POLICY}])
        self.assertNotIn(mock.call('/lib'), auth.owner.call_args_list)
        self.assertEqual(auth.member_attributions[start]['target']['member'], 'lib/tool')

    def test_inverse_archive_spelling_requires_observed_usrmerge_resolution(self):
        # owner() may query both database spellings; that is not evidence that a
        # real unmerged /lib regular file resolves to /usr/lib.
        row = measured('/lib/tool', '/lib/tool', aliases=[])
        self.fails('CAP_BYTES_REJECTED', M.verify_member, row, {'usr/lib/tool': REGULAR})

    def test_all_four_root_layout_aliases_remain_exact(self):
        for name in ('bin', 'sbin', 'lib', 'lib64'):
            good = measured('/' + name + '/tool', '/usr/' + name + '/tool', aliases=[alias('/' + name, 'usr/' + name)])
            M.member_chain(good)
            for target in ('/usr/' + name, 'usr/../usr/' + name, 'private/' + name):
                bad = copy.deepcopy(good)
                bad['aliases'][0]['target'] = target
                self.fails('CAP_BYTES_REJECTED', M.member_chain, bad)

    def test_cached_authentication_rechecks_later_expected_owner(self):
        auth = self.authenticated()
        self.fails('CAP_OWNER_REJECTED', auth.authenticate, ALIAS, expected=MINIMAL)
        self.assertEqual(auth.authenticate(ALIAS, expected=STDLIB)['package'], STDLIB)

    def test_cached_authentication_rechecks_policy_for_all_packages(self):
        auth = self.authenticated()
        policy = mock.Mock()
        auth.authenticate(ALIAS, owner_policy=policy)
        self.assertEqual({x.args[0] for x in policy.call_args_list}, {STDLIB, MINIMAL})

    def test_expected_owner_mismatch_fails_before_package_download(self):
        auth = self.auth()
        auth.package = mock.Mock(side_effect=AssertionError('download before expected owner'))
        with mock.patch.object(M, 'measure', return_value=(measured(), b'')):
            self.fails('CAP_OWNER_REJECTED', auth.authenticate, ALIAS, expected=MINIMAL)
        auth.package.assert_not_called()

    def test_owner_query_cache_retains_unique_exact_observation(self):
        auth = M.Authenticator(Path('/fixture'), mock.Mock(), Path('/fixture'), {}, {})
        with mock.patch.object(M, 'command', side_effect=[(0, (STDLIB + ':amd64: ' + ALIAS + '\n').encode()), (1, b'')]) as command:
            self.assertEqual(auth.owner(ALIAS), STDLIB)
            self.assertEqual(auth.owner(ALIAS), STDLIB)
            self.assertEqual(command.call_count, 2)

    def test_owner_query_rejects_two_owners_on_one_path(self):
        auth = M.Authenticator(Path('/fixture'), mock.Mock(), Path('/fixture'), {}, {})
        output = (STDLIB + ': ' + ALIAS + '\n' + MINIMAL + ': ' + ALIAS + '\n').encode()
        with mock.patch.object(M, 'command', side_effect=[(0, output), (1, b'')]):
            self.fails('CAP_OWNER_REJECTED', auth.owner, ALIAS)

    def test_lock_replay_is_pure_and_exact(self):
        self.replay(self.lock())

    def test_lock_rejects_missing_or_extra_attribution(self):
        for replacement in ({}, {'/extra': {}}, None):
            lock = self.lock()
            lock['member_attributions'] = replacement
            self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_lock_rejects_wrong_member_protocol(self):
        lock = self.lock()
        for protocol in (None, 'private-public-keyring-v1', 'signed-package-hop-attribution-v0'):
            lock['member_protocol'] = protocol
            self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_lock_rejects_tampered_attribution_identity_or_member(self):
        for key, value in (('package', STDLIB), ('member', ALIAS[1:]), ('path', ALIAS), ('kind', 'layout-policy')):
            lock = self.lock()
            lock['member_attributions'][ALIAS]['target'][key] = value
            self.fails('CAP_LOCK_REJECTED', self.replay, lock)
        for key, value in (('name', STDLIB), ('version', '3.10.99'), ('source', 'other-source'),
                           ('archive_sha256', '0' * 64), ('archive_size', 1), ('stanza', 1), ('suite', 'jammy')):
            lock = self.lock()
            lock['member_attributions'][ALIAS]['target']['package_record'][key] = value
            self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_lock_rejects_ambiguous_owner_claims(self):
        lock = self.lock()
        lock['packages'][STDLIB]['owner_paths'].append(TARGET)
        lock['packages'][STDLIB]['owner_paths'].sort()
        self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_lock_rejects_conflicting_owners_across_usrmerge_spellings(self):
        lock = self.lock()
        legacy = TARGET[4:]
        row = measured(legacy, TARGET, aliases=[alias('/lib', 'usr/lib')])
        row['package'] = STDLIB
        lock['runtime_files'][legacy] = row
        target = lock['member_attributions'][ALIAS]['target']
        lock['member_attributions'][legacy] = M.attributed_members(row, lambda *args: dict(target))
        lock['packages'][STDLIB]['owner_paths'].append(legacy)
        lock['packages'][STDLIB]['owner_paths'].sort()
        self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_lock_rejects_duplicate_owner_path(self):
        lock = self.lock()
        lock['packages'][STDLIB]['owner_paths'] *= 2
        self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_lock_rejects_absent_owner_record(self):
        lock = self.lock()
        lock['packages'][MINIMAL]['owner_paths'] = []
        self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_lock_rejects_missing_or_altered_member_projection(self):
        for field, value in ((None, None), ('kind', 'symlink'), ('sha256', '0' * 64), ('mode', 0o755)):
            lock = self.lock()
            if field is None:
                lock['packages'][MINIMAL]['members'].clear()
            else:
                lock['packages'][MINIMAL]['members'][TARGET[1:]][field] = value
            self.fails('CAP_BYTES_REJECTED', self.replay, lock)

    def test_lock_rejects_unused_projected_members(self):
        lock = self.lock()
        lock['packages'][MINIMAL]['members']['private/unused'] = REGULAR
        self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_lock_rejects_unused_owner_observation(self):
        lock = self.lock()
        lock['packages'][MINIMAL]['owner_paths'].append('/usr/lib/unused')
        lock['packages'][MINIMAL]['owner_paths'].sort()
        self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_lock_rejects_forged_runtime_package(self):
        lock = self.lock()
        lock['runtime_files'][ALIAS]['package'] = MINIMAL
        self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_lock_rejects_inconsistent_tool_projection(self):
        lock = self.lock()
        lock['tools'] = {'fake': dict(lock['runtime_files'][ALIAS], package=MINIMAL)}
        self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_source_version_string_is_preserved_exactly(self):
        auth = self.auth()
        auth.packages[MINIMAL]['source'] = 'python3.10 (' + VERSION + ')'
        with mock.patch.object(M, 'measure', return_value=(measured(), b'')):
            auth.authenticate(ALIAS)
        self.assertEqual(auth.member_attributions[ALIAS]['target']['package_record']['source'], 'python3.10 (' + VERSION + ')')

    def test_nonreviewed_root_alias_is_not_a_layout_exception(self):
        row = measured('/lib32/tool', '/usr/lib32/tool', aliases=[alias('/lib32', 'usr/lib32')])
        self.fails('CAP_BYTES_REJECTED', M.verify_member, row, {'usr/lib32/tool': REGULAR})

    def test_member_projection_rejects_extra_unreviewed_fields(self):
        lock = self.lock()
        lock['packages'][MINIMAL]['members'][TARGET[1:]]['skip_owner'] = True
        self.fails('CAP_BYTES_REJECTED', self.replay, lock)

    def test_owner_paths_require_strings_not_coercions(self):
        lock = self.lock()
        lock['packages'][MINIMAL]['owner_paths'] = [True]
        self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_unused_package_cannot_be_added_as_a_fallback_archive(self):
        lock = self.lock()
        unused = copy.deepcopy(lock['packages'][MINIMAL])
        unused.update(name='unused-package', members={}, owner_paths=[])
        lock['packages']['unused-package'] = unused
        self.fails('CAP_LOCK_REJECTED', self.replay, lock)

    def test_explicit_legacy_archive_owner_cannot_bypass_owner_query(self):
        auth = self.auth()
        with mock.patch.object(M, 'measure') as measuring:
            with self.assertRaises(TypeError):
                auth.authenticate(ALIAS, archive_owner=STDLIB)
        measuring.assert_not_called()

    def test_producer_ledger_respects_existing_lock_size_limit(self):
        with mock.patch.object(M, 'LOCK_SIZE_LIMIT', 1):
            self.fails('CAP_LOCK_REJECTED', self.replay, self.lock())

    def package_download_fixture(self, archive_sha=None, archive_size=None):
        signed = {'Package': MINIMAL, 'Version': VERSION, 'Architecture': 'amd64',
                  'Source': 'python3.10 (' + VERSION + ')', 'Size': str(ARCHIVES[MINIMAL][1]),
                  'SHA256': ARCHIVES[MINIMAL][0], '_suite': 'jammy-updates', '_component': 'main', '_stanza': 0}
        auth = M.Authenticator(Path('/fixture'), mock.Mock(), Path('/fixture'), {}, {})
        auth.m.fields.return_value = signed
        auth.m.package_identity.side_effect = lambda row: tuple(row[k] for k in ('Package', 'Version', 'Architecture'))
        measured_archive = {'sha256': ARCHIVES[MINIMAL][0] if archive_sha is None else archive_sha,
                            'size': ARCHIVES[MINIMAL][1] if archive_size is None else archive_size}
        stack = __import__('contextlib').ExitStack()
        stack.enter_context(mock.patch.object(M, 'select_record', return_value=signed))
        stack.enter_context(mock.patch.object(M, 'command', return_value=(0, b'fixture archive metadata')))
        stack.enter_context(mock.patch.object(M, 'archive_members', return_value={TARGET[1:]: REGULAR}))
        stack.enter_context(mock.patch.object(M, 'measure', return_value=(measured_archive, b'')))
        return auth, stack

    def test_archive_measurement_is_compared_to_exact_signed_hash_and_size(self):
        for kwargs in ({'archive_sha': '0' * 64}, {'archive_size': 1}):
            auth, stack = self.package_download_fixture(**kwargs)
            with stack:
                self.fails('CAP_ARCHIVE_REJECTED', auth.package, MINIMAL)
            self.assertEqual(auth.packages, {})
            self.assertEqual(auth.members, {})

    def test_package_record_retains_signed_source_version_and_archive_anchor(self):
        auth, stack = self.package_download_fixture()
        with stack:
            auth.package(MINIMAL)
        self.assertEqual(auth.packages[MINIMAL]['source'], 'python3.10 (' + VERSION + ')')
        self.assertEqual(auth.packages[MINIMAL]['archive']['sha256'], ARCHIVES[MINIMAL][0])
        self.assertEqual(auth.packages[MINIMAL]['members'], {})
        self.assertEqual(auth.packages[MINIMAL]['owner_paths'], [])

    def test_lock_schema_and_keyring_protocol_remain_distinct(self):
        self.assertEqual(M.LOCK_SCHEMA, 3)
        self.assertEqual(M.PROTOCOL, 'private-public-keyring-v1')
        self.assertEqual(M.MEMBER_PROTOCOL, 'signed-package-hop-attribution-v1')
        for schema, member_protocol in ((2, M.MEMBER_PROTOCOL), (3, None)):
            lock = {'schema': schema, 'protocol': M.PROTOCOL, 'member_protocol': member_protocol,
                    'status': 'authenticated-existing-tools', 'scope': 'namespace-capability-only', 'snapshot': M.SNAPSHOT}
            with mock.patch.object(M, 'measure') as measure:
                self.fails('CAP_LOCK_REJECTED', M.verify_state, Path('/fixture'), lock)
            measure.assert_not_called()


if __name__ == '__main__':
    unittest.main()

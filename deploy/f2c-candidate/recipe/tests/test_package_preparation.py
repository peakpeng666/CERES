"""Stdlib fixtures only. Never invoke APT, install packages, or download payloads."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import types
import unittest
from unittest import mock
import hashlib
import sys
import ast
import lzma
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / 'prepare_jammy_packages.py'
spec = importlib.util.spec_from_file_location('prepare_jammy_packages', SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def row(name='a', version='1', arch='amd64', held=False):
    return {'name': name, 'version': version, 'architecture': arch, 'held': held}


def control(name='a', version='1', arch='amd64', status='install ok installed'):
    return f'Package: {name}\nVersion: {version}\nArchitecture: {arch}\nStatus: {status}\n'


def version(identifier, name, ver='1', dependencies=None):
    pkg = types.SimpleNamespace(id=identifier, name=name)
    return types.SimpleNamespace(parent_pkg=pkg, ver_str=ver, arch='amd64', depends_list=dependencies or {})


def dep(*versions):
    return types.SimpleNamespace(all_targets=lambda: list(versions))


class TestHostAndMetadata(unittest.TestCase):
    def test_host_jammy(self):
        p.check_host({'ID': 'ubuntu', 'VERSION_ID': '22.04'}, 'x86_64', 'amd64')

    def test_wrong_distribution(self):
        for release in ({'ID': 'debian', 'VERSION_ID': '13'}, {'ID': 'ubuntu', 'VERSION_ID': '24.04'}):
            with self.assertRaises(RuntimeError):
                p.check_host(release, 'x86_64', 'amd64')

    def test_wrong_architecture(self):
        with self.assertRaises(RuntimeError):
            p.check_host({'ID': 'ubuntu', 'VERSION_ID': '22.04'}, 'aarch64', 'arm64')

    def test_os_release_not_executed(self):
        self.assertEqual(p.os_values('ID="ubuntu"\nVERSION_ID="22.04"\n'), {'ID': 'ubuntu', 'VERSION_ID': '22.04'})

    def test_control_continuations(self):
        self.assertEqual(p.fields('Depends: a,\n b\nVersion: 1\n')['Depends'], 'a,\n b')

    def test_duplicate_fields_rejected(self):
        with self.assertRaises(RuntimeError):
            p.fields('Version: 1\nVersion: 2\n')

    def test_actual_index_parser_multiple_package_stanzas(self):
        data = ('Package: first\nVersion: 1\nArchitecture: amd64\n'
                'Depends: one,\n two\nDescription: First\n continuation\n\n'
                'Package: second\nVersion: 2\nArchitecture: all\nDescription: Second\n\n')
        rows = list(p.package_index_rows(lzma.compress(data.encode())))
        self.assertEqual([p.package_identity(r) for r in rows],
                         [('first', '1', 'amd64'), ('second', '2', 'all')])
        self.assertEqual(rows[0]['Depends'], 'one,\n two')

    def test_index_duplicate_within_stanza_remains_rejected(self):
        data = b'Package: first\nPackage: duplicate\nVersion: 1\nArchitecture: amd64\n'
        with self.assertRaisesRegex(RuntimeError, 'Duplicate control field'):
            list(p.package_index_rows(lzma.compress(data)))

    def test_index_invalid_encoding_and_compression_remain_rejected(self):
        for data, exception in ((b'not xz', lzma.LZMAError),
                                (lzma.compress(b'Package: \xff\n'), UnicodeDecodeError)):
            with self.subTest(exception=exception), self.assertRaises(exception):
                list(p.package_index_rows(data))

    def test_package_script_python310_syntax(self):
        ast.parse(SCRIPT.read_text(), feature_version=(3, 10))

    def test_cleartext_dash_escape(self):
        text = '-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\nOrigin: Ubuntu\n- -field\n-----BEGIN PGP SIGNATURE-----\nabc'
        self.assertEqual(p.cleartext(text), 'Origin: Ubuntu\n-field\n')

    def test_hash_entries(self):
        h = 'a' * 64
        self.assertEqual(p.hash_entries('SHA256:\n ' + h + ' 3 main/Packages\nSHA512:\n'), {'main/Packages': (h, 3)})

    def test_duplicate_hash_entries(self):
        entry = ' ' + 'a' * 64 + ' 3 main/Packages\n'
        with self.assertRaises(RuntimeError):
            p.hash_entries('SHA256:\n' + entry + entry)

    def test_safe_archive_paths(self):
        good = 'pool/main/a/abc/abc_1.0+dfsg-1_amd64.deb'
        self.assertEqual(p.safe_archive_path(good), good)
        for bad in ('https://bad/x.deb', '/pool/x.deb', 'pool/../x.deb', 'pool/a%2fb.deb',
                    'pool/a?x.deb', 'pool/a\\b.deb', 'pool/a b.deb', 'pool/a.txt'):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                p.safe_archive_path(bad)


class TestProvidersAndInventory(unittest.TestCase):
    def test_inventory_only_installed(self):
        status = control() + '\n' + control('old', status='deinstall ok config-files') + '\n' + control('held', status='hold ok installed')
        self.assertEqual(set(p.inventory(status)), {'a:amd64', 'held:amd64'})
        self.assertTrue(p.inventory(status)['held:amd64']['held'])

    def test_default_root_is_exact_pkg_config(self):
        roots, providers = p.choose_roots({}, {})
        self.assertEqual(roots['pkg-config'], '0.29.2-1ubuntu3')
        self.assertFalse(providers)

    def test_keep_authenticated_pkgconf(self):
        installed = {'pkgconf:amd64': row('pkgconf', '1.8.0-1')}
        records = {('pkgconf', '1.8.0-1', 'amd64'): [{'Provides': 'pkg-config (= 0.29-1)'}]}
        roots, providers = p.choose_roots(installed, records)
        self.assertNotIn('pkg-config', roots)
        self.assertEqual(roots['pkgconf'], '1.8.0-1')
        self.assertEqual(providers, {'pkg-config': 'pkgconf'})

    def test_both_providers_stop(self):
        with self.assertRaisesRegex(RuntimeError, 'Both'):
            p.choose_roots({'pkgconf:amd64': row('pkgconf'), 'pkg-config:amd64': row('pkg-config')}, {})

    def test_unknown_pkgconf_stop(self):
        with self.assertRaisesRegex(RuntimeError, 'absent'):
            p.choose_roots({'pkgconf:amd64': row('pkgconf')}, {})

    def test_invalid_provides_stop(self):
        with self.assertRaisesRegex(RuntimeError, 'provide'):
            p.choose_roots({'pkgconf:amd64': row('pkgconf')}, {('pkgconf', '1', 'amd64'): [{'Provides': 'pkg-config-fake'}]})

    def test_transaction_removal_downgrade_stop(self):
        for action in ('remove', 'downgrade', 'reinstall'):
            with self.subTest(action=action), self.assertRaises(RuntimeError):
                p.validate_transaction([{'name': 'a', 'version': '2', 'architecture': 'amd64', 'old_version': '1', 'action': action}], {'a:amd64': row()})

    def test_transaction_hold_stop(self):
        with self.assertRaisesRegex(RuntimeError, 'held'):
            p.validate_transaction([{'name': 'a', 'version': '2', 'architecture': 'amd64', 'old_version': '1', 'action': 'upgrade'}], {'a:amd64': row(held=True)})

    def test_transaction_old_version_stale_stop(self):
        with self.assertRaisesRegex(RuntimeError, 'Base'):
            p.validate_transaction([{'name': 'a', 'version': '2', 'architecture': 'amd64', 'old_version': '0', 'action': 'upgrade'}], {'a:amd64': row()})

    def test_inventory_exact_expected_upgrade(self):
        p.validate_inventory_delta({'a:amd64': row()}, {'a:amd64': row(version='2')},
                                   [{'name': 'a', 'version': '2', 'architecture': 'amd64'}])

    def test_inventory_unknown_addition_or_removal(self):
        for after in ({}, {'a:amd64': row(), 'x:amd64': row('x')}):
            with self.subTest(after=after), self.assertRaises(RuntimeError):
                p.validate_inventory_delta({'a:amd64': row()}, after, [])


class TestNativeClosureAndAPTPlans(unittest.TestCase):
    def test_actual_provider_selected_not_unsatisfied_candidate(self):
        a, b, old = version(1, 'a'), version(2, 'provider'), version(2, 'provider', '0')
        a.depends_list = {'Depends': [[dep(old, b)]]}
        actual = p.closure_versions({1: a, 2: b}, [a])
        self.assertEqual({v.parent_pkg.name for v in actual}, {'a', 'provider'})

    def test_all_installed_satisfied_alternatives_are_covered(self):
        a, b, c = version(1, 'a'), version(2, 'b'), version(3, 'c')
        a.depends_list = {'Depends': [[dep(b), dep(c)]]}
        self.assertEqual(len(p.closure_versions({1: a, 2: b, 3: c}, [a])), 3)

    def test_predepends_cycle_terminates(self):
        a, b = version(1, 'a'), version(2, 'b')
        a.depends_list = {'PreDepends': [[dep(b)]]}
        b.depends_list = {'Pre-Depends': [[dep(a)]]}
        self.assertEqual(len(p.closure_versions({1: a, 2: b}, [a])), 2)

    def test_missing_native_satisfier_stop(self):
        a, b = version(1, 'a'), version(2, 'b')
        a.depends_list = {'Depends': [[dep(b)]]}
        with self.assertRaisesRegex(RuntimeError, 'unsatisfied'):
            p.closure_versions({1: a}, [a])

    def test_simulation_versions(self):
        text = 'Inst a [1] (2 Ubuntu:22.04/jammy-updates [amd64])\nInst b (1 Ubuntu:22.04/jammy [amd64])\nConf a (2 Ubuntu [amd64])\n'
        self.assertEqual(p.parse_simulation(text), {'a': '2', 'b': '1'})

    def test_simulation_removal_stop(self):
        for text in ('Remv pkg-config [1]', 'Purg pkgconf [1]', 'Inst a unknown'):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                p.parse_simulation(text)

    def test_archive_uri_cache_filename(self):
        text = "Reading package lists...\n'file:/candidate/repo/pool/a.deb' a_1%3a2_amd64.deb 42 MD5Sum:abc\n"
        self.assertEqual(p.parse_archive_uris(text)[0]['cache_name'], 'a_1%3a2_amd64.deb')

    def test_archive_uri_external_or_traversal_stop(self):
        for text in ("'https://evil/a.deb' a.deb 42 MD5Sum:abc", "'file:/pool/a.deb' ../a.deb 42 MD5Sum:abc"):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                p.parse_archive_uris(text)


class TestClosureProvenanceReasons(unittest.TestCase):
    def setUp(self):
        self.context = dict(p.DIAGNOSTIC_CONTEXT)
        self.diagnostic_root = p.DIAGNOSTIC_ROOT
        p.phase('closure-provenance')

    def tearDown(self):
        p.DIAGNOSTIC_CONTEXT.clear()
        p.DIAGNOSTIC_CONTEXT.update(self.context)
        p.DIAGNOSTIC_ROOT = self.diagnostic_root

    def assert_reason(self, code, call):
        with self.assertRaises(p.ClosureProvenanceError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)
        receipt = p.failure_diagnostic(raised.exception)
        self.assertEqual(receipt['code'], code)
        self.assertEqual(receipt['operation'], 'closure-provenance')
        self.assertEqual(receipt['exception_class'], 'ClosureProvenanceError')
        self.assertEqual(set(receipt), {'schema', 'code', 'operation', 'exception_class',
                                       'http_status', 'errno', 'returncode', 'suite', 'component'})
        encoded = json.dumps(receipt)
        self.assertLess(len(encoded), 1024)
        for private in ('SECRET', '/private', 'https://'):
            self.assertNotIn(private, encoded)

    def test_missing_package_is_distinct_and_never_runs_native_command(self):
        roots = {'fixture-root': '1'}
        with mock.patch.object(p.subprocess, 'run') as native:
            self.assert_reason('APT_CLOSURE_PACKAGE_UNAVAILABLE', lambda: p.extend_provenance_roots(
                [row('SECRET_PACKAGE', 'SECRET_VERSION')], {}, roots))
            native.assert_not_called()
        self.assertEqual(roots, {'fixture-root': '1'})

    def test_architecture_mismatch_cannot_be_substituted(self):
        for selected, signed in (('amd64', 'all'), ('all', 'amd64'), ('i386', 'amd64')):
            with self.subTest(selected=selected, signed=signed), mock.patch.object(p.subprocess, 'run') as native:
                self.assert_reason('APT_CLOSURE_PACKAGE_UNAVAILABLE', lambda: p.extend_provenance_roots(
                    [row('SECRET_PACKAGE', 'SECRET_VERSION', selected)],
                    {('SECRET_PACKAGE', '2', signed): [{}]}, {}))
                native.assert_not_called()

    def test_downgrade_refusal_and_its_comparison_are_unchanged(self):
        roots = {}
        with mock.patch.object(p.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1)) as native:
            self.assert_reason('APT_CLOSURE_DOWNGRADE_REFUSED', lambda: p.extend_provenance_roots(
                [row('SECRET_PACKAGE', 'SECRET_INSTALLED_VERSION')],
                {('SECRET_PACKAGE', 'SECRET_SIGNED_VERSION', 'amd64'): [{}]}, roots))
            native.assert_called_once_with(['/usr/bin/dpkg', '--compare-versions',
                'SECRET_SIGNED_VERSION', 'ge', 'SECRET_INSTALLED_VERSION'])
        self.assertEqual(roots, {})

    def test_unsuccessful_version_comparison_still_fails_closed(self):
        with mock.patch.object(p.subprocess, 'run', return_value=types.SimpleNamespace(returncode=2)):
            self.assert_reason('APT_CLOSURE_DOWNGRADE_REFUSED', lambda: p.extend_provenance_roots(
                [row('SECRET_PACKAGE', 'SECRET_VERSION')], {('SECRET_PACKAGE', '2', 'amd64'): [{}]}, {}))

    def test_unresolved_original_root_is_not_replaced(self):
        roots = {}
        with mock.patch.object(p.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)):
            self.assert_reason('APT_CLOSURE_PIN_UNRESOLVED', lambda: p.extend_provenance_roots(
                [row('gcc-11', 'SECRET_VERSION')], {('gcc-11', 'SECRET_SIGNED_VERSION', 'amd64'): [{}]}, roots))
        self.assertEqual(roots, {})

    def test_unresolved_added_pin_is_not_replaced(self):
        roots = {'SECRET_PACKAGE': 'SECRET_EXISTING_PIN'}
        with mock.patch.object(p.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)):
            self.assert_reason('APT_CLOSURE_PIN_UNRESOLVED', lambda: p.extend_provenance_roots(
                [row('SECRET_PACKAGE', 'SECRET_VERSION')],
                {('SECRET_PACKAGE', 'SECRET_SIGNED_VERSION', 'amd64'): [{}]}, roots))
        self.assertEqual(roots, {'SECRET_PACKAGE': 'SECRET_EXISTING_PIN'})

    def test_no_progress_is_defensive_and_does_not_run_command(self):
        with mock.patch.object(p.subprocess, 'run') as native:
            self.assert_reason('APT_CLOSURE_NO_PROGRESS', lambda: p.extend_provenance_roots([], {}, {}))
            native.assert_not_called()

    def test_authenticated_upgrade_converges_on_next_exact_solution(self):
        roots = {}
        records = {('dependency', '2', 'amd64'): [{}], ('dependency', '3', 'amd64'): [{}]}
        solution = {'closure': [row('dependency', '1')]}
        with mock.patch.object(p.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)) as native:
            missing = [item for item in solution['closure'] if p.identity(item) not in records]
            p.extend_provenance_roots(missing, records, roots)
            self.assertEqual(roots, {'dependency': '3'})
            solution = {'closure': [row('dependency', roots['dependency'])]}
            self.assertEqual([item for item in solution['closure'] if p.identity(item) not in records], [])
            self.assertEqual(native.call_args_list, [
                mock.call(['/usr/bin/dpkg', '--compare-versions', '3', 'gt', '2']),
                mock.call(['/usr/bin/dpkg', '--compare-versions', '3', 'ge', '1'])])

    def test_best_version_selection_does_not_assume_index_sort_order(self):
        records = {('dependency', '3', 'amd64'): [{}], ('dependency', '2', 'amd64'): [{}]}
        roots = {}
        with mock.patch.object(p.subprocess, 'run', side_effect=[
                types.SimpleNamespace(returncode=1), types.SimpleNamespace(returncode=0)]) as native:
            p.extend_provenance_roots([row('dependency', '1')], records, roots)
        self.assertEqual(roots, {'dependency': '3'})
        self.assertEqual(native.call_args_list, [
            mock.call(['/usr/bin/dpkg', '--compare-versions', '2', 'gt', '3']),
            mock.call(['/usr/bin/dpkg', '--compare-versions', '3', 'ge', '1'])])

    def test_architecture_all_exact_identity_is_preserved(self):
        roots = {}
        with mock.patch.object(p.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)):
            p.extend_provenance_roots([row('dependency', '1', 'all')],
                {('dependency', '2', 'all'): [{}]}, roots)
        self.assertEqual(roots, {'dependency': '2'})

    def test_unknown_or_tampered_code_cannot_escape_finite_contract(self):
        with self.assertRaises(ValueError):
            p.ClosureProvenanceError('SECRET_CODE')
        error = p.ClosureProvenanceError('APT_CLOSURE_PIN_UNRESOLVED')
        error.code = 'SECRET_CODE'
        receipt = p.failure_diagnostic(error)
        self.assertEqual(receipt['code'], 'APT_INVARIANT_REJECTED')
        self.assertNotIn('SECRET', json.dumps(receipt))

    def test_best_signed_record_supplies_reference_and_actual_comparison_rc(self):
        records = {('SECRET_PACKAGE', '2', 'amd64'): [{'_suite': 'jammy', '_component': 'main', '_stanza': 1}],
                   ('SECRET_PACKAGE', '3', 'amd64'): [{'_suite': 'jammy-updates', '_component': 'main', '_stanza': 42}]}
        for rc in (1, 2, -9):
            with self.subTest(rc=rc), mock.patch.object(p.subprocess, 'run', side_effect=[
                    types.SimpleNamespace(returncode=0), types.SimpleNamespace(returncode=rc)]) as native:
                with self.assertRaises(p.ClosureProvenanceError) as raised:
                    p.extend_provenance_roots([row('SECRET_PACKAGE', 'SECRET_INSTALLED_VERSION')], records, {})
                error = raised.exception
                self.assertEqual(error.reference, {'schema': 1, 'snapshot': p.SNAPSHOT,
                    'suite': 'jammy-updates', 'component': 'main', 'stanza': 42})
                receipt = p.failure_diagnostic(error)
                self.assertEqual(receipt['returncode'], rc)
                self.assertNotIn('SECRET', json.dumps(receipt))
                self.assertEqual(native.call_args_list, [
                    mock.call(['/usr/bin/dpkg', '--compare-versions', '3', 'gt', '2']),
                    mock.call(['/usr/bin/dpkg', '--compare-versions', '3', 'ge', 'SECRET_INSTALLED_VERSION'])])

    def test_reference_contains_no_host_or_record_strings(self):
        record = {'Package': 'SECRET_PACKAGE', 'Version': 'SECRET_VERSION',
                  '_suite': 'jammy', '_component': 'main', '_stanza': 0}
        ref = p.canonical_target_reference(record)
        self.assertEqual(set(ref), {'schema', 'snapshot', 'suite', 'component', 'stanza'})
        self.assertNotIn('SECRET', json.dumps(ref))
        for changed in ({'_stanza': True}, {'_stanza': -1}, {'_stanza': 6090},
                        {'_suite': 'SECRET'}, {'_component': '../private'}, {'_suite': []}):
            self.assertIsNone(p.canonical_target_reference(dict(record, **changed)))

    def test_sidecar_is_exclusive_private_and_bound_to_adjacent_terminal_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            p.DIAGNOSTIC_ROOT = Path(temp)
            ref = p.canonical_target_reference({'_suite': 'jammy', '_component': 'main', '_stanza': 0})
            error = p.ClosureProvenanceError('APT_CLOSURE_DOWNGRADE_REFUSED', 1, ref)
            output = io.StringIO()
            with mock.patch.object(p.sys, 'stderr', output):
                receipt = p.emit_failure(error)
            target = Path(temp) / 'package-target-reference.json'
            data = target.read_bytes()
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(data), ref)
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], p.TARGET_REFERENCE_PREFIX + hashlib.sha256(data).hexdigest())
            self.assertEqual(json.loads(lines[1][len(p.DIAGNOSTIC_PREFIX):]), receipt)
            output = io.StringIO()
            with mock.patch.object(p.sys, 'stderr', output):
                p.emit_failure(error)
            self.assertEqual(target.read_bytes(), data)
            self.assertNotIn(p.TARGET_REFERENCE_PREFIX, output.getvalue())

    def test_sidecar_only_for_exact_downgrade_phase_and_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            p.DIAGNOSTIC_ROOT = Path(temp)
            ref = p.canonical_target_reference({'_suite': 'jammy', '_component': 'main', '_stanza': 0})
            for invalid in (dict(ref, schema=True), dict(ref, SECRET='private'), dict(ref, snapshot='SECRET')):
                error = p.ClosureProvenanceError('APT_CLOSURE_DOWNGRADE_REFUSED', 1, invalid)
                self.assertIsNone(p.write_target_reference(error, p.failure_diagnostic(error)))
            error = p.ClosureProvenanceError('APT_CLOSURE_PIN_UNRESOLVED', 1, ref)
            self.assertIsNone(p.write_target_reference(error, p.failure_diagnostic(error)))
            p.phase('native-solver')
            error = p.ClosureProvenanceError('APT_CLOSURE_DOWNGRADE_REFUSED', 1, ref)
            self.assertIsNone(p.write_target_reference(error, p.failure_diagnostic(error)))
            self.assertFalse((Path(temp) / 'package-target-reference.json').exists())

    def test_sidecar_does_not_follow_alias_or_wait_on_fifo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); p.DIAGNOSTIC_ROOT = root
            original = root / 'private'; original.write_bytes(b'SECRET')
            path = root / 'package-target-reference.json'; path.symlink_to(original)
            ref = p.canonical_target_reference({'_suite': 'jammy', '_component': 'main', '_stanza': 0})
            error = p.ClosureProvenanceError('APT_CLOSURE_DOWNGRADE_REFUSED', 1, ref)
            self.assertIsNone(p.write_target_reference(error, p.failure_diagnostic(error)))
            self.assertEqual(original.read_bytes(), b'SECRET')
            path.unlink(); os.mkfifo(path)
            self.assertIsNone(p.write_target_reference(error, p.failure_diagnostic(error)))


class TestProviderSelectionReasons(unittest.TestCase):
    def setUp(self):
        self.context = dict(p.DIAGNOSTIC_CONTEXT)
        self.diagnostic_root = p.DIAGNOSTIC_ROOT
        p.phase('provider-selection')

    def tearDown(self):
        p.DIAGNOSTIC_CONTEXT.clear()
        p.DIAGNOSTIC_CONTEXT.update(self.context)
        p.DIAGNOSTIC_ROOT = self.diagnostic_root

    def assert_reason(self, code, call):
        with mock.patch.object(p.subprocess, 'run') as native:
            with self.assertRaises(p.ProviderSelectionError) as raised:
                call()
            native.assert_not_called()
        self.assertEqual(raised.exception.code, code)
        receipt = p.failure_diagnostic(raised.exception)
        self.assertEqual(receipt['code'], code)
        self.assertEqual(receipt['exception_class'], 'ProviderSelectionError')
        self.assertEqual(receipt['operation'], 'provider-selection')
        self.assertEqual(set(receipt), {'schema', 'code', 'operation', 'exception_class',
                                       'http_status', 'errno', 'returncode', 'suite', 'component'})
        encoded = json.dumps(receipt)
        for private in ('SECRET', '/private', 'https://'):
            self.assertNotIn(private, encoded)

    def test_coexistence_rejected_without_removing_either_package(self):
        base = {'pkg-config:amd64': row('pkg-config', 'SECRET_VERSION_ONE'),
                'pkgconf:amd64': row('pkgconf', 'SECRET_VERSION_TWO')}
        before = json.dumps(base, sort_keys=True)
        self.assert_reason('APT_PROVIDER_COEXISTENCE_REJECTED', lambda: p.choose_roots(base, {}))
        self.assertEqual(json.dumps(base, sort_keys=True), before)

    def test_unauthenticated_version_is_distinct_without_version_output(self):
        self.assert_reason('APT_PROVIDER_VERSION_UNAUTHENTICATED', lambda: p.choose_roots(
            {'pkgconf:amd64': row('pkgconf', 'SECRET_VERSION')}, {}))

    def test_missing_capability_is_distinct_without_provides_output(self):
        self.assert_reason('APT_PROVIDER_CAPABILITY_MISSING', lambda: p.choose_roots(
            {'pkgconf:amd64': row('pkgconf', '1.8.0-1')},
            {('pkgconf', '1.8.0-1', 'amd64'): [{'Provides': 'SECRET_PROVIDER'}]}))

    def test_owner_mismatch_is_distinct_without_owner_or_path_output(self):
        executable = mock.Mock()
        self.assert_reason('APT_PROVIDER_OWNER_MISMATCH', lambda: p.validate_selected_provider(
            'SECRET_OWNER: /private/SECRET_PATH', executable))
        executable.is_file.assert_not_called()

    def test_missing_executable_is_distinct_without_path_output(self):
        self.assert_reason('APT_PROVIDER_EXECUTABLE_MISSING', lambda: p.validate_selected_provider(
            'pkgconf: /usr/bin/pkg-config', types.SimpleNamespace(is_file=lambda: False)))

    def test_known_signed_provider_and_owner_are_still_accepted(self):
        base = {'pkgconf:amd64': row('pkgconf', '1.8.0-1')}
        records = {('pkgconf', '1.8.0-1', 'amd64'): [{'Provides': 'pkg-config (= 0.29-1)'}]}
        roots, providers = p.choose_roots(base, records)
        self.assertEqual(providers, {'pkg-config': 'pkgconf'})
        self.assertEqual(roots['pkgconf'], '1.8.0-1')
        for owner in ('pkgconf', 'pkgconf:amd64'):
            p.validate_selected_provider(owner + ': /usr/bin/pkg-config',
                                         types.SimpleNamespace(is_file=lambda: True))

    def test_pkg_config_only_accepted_without_provider_replacement(self):
        roots, providers = p.choose_roots({'pkg-config:amd64': row('pkg-config')}, {})
        self.assertEqual(roots, p.ROOTS)
        self.assertEqual(providers, {})

    def test_unknown_reason_cannot_be_constructed(self):
        with self.assertRaisesRegex(ValueError, '^Unknown provider selection code$'):
            p.ProviderSelectionError('SECRET_CODE /private/path https://private.example')

    def test_exact_paired_pkgconf_diversion_accepted_provisionally(self):
        for owner in ('pkgconf', 'pkgconf:amd64'):
            output = ('diversion by pkgconf from: /usr/bin/pkg-config\n'
                      'diversion by pkgconf to: /usr/bin/pkg-config.real\n'
                      + owner + ': /usr/bin/pkg-config\n')
            p.validate_selected_provider(output, types.SimpleNamespace(is_file=lambda: True))

    def test_owner_grammar_rejects_every_unrecognized_or_partial_form(self):
        owner = 'pkgconf: /usr/bin/pkg-config\n'
        start = 'diversion by pkgconf from: /usr/bin/pkg-config\n'
        end = 'diversion by pkgconf to: /usr/bin/pkg-config.real\n'
        for output in ('', owner + owner, start + end, owner + start, owner + end,
                       owner + start + end + end, owner + 'warning: SECRET\n',
                       owner + start.replace('pkgconf', 'LOCAL') + end,
                       owner + start + end.replace('pkg-config.real', 'SECRET_TARGET'),
                       owner.replace('pkgconf', 'SECRET_OWNER'),
                       owner.replace('/usr/bin/pkg-config', '/private/SECRET_PATH'),
                       owner + '\n', owner + 'x' * 2000):
            with self.subTest(output=output):
                self.assert_reason('APT_PROVIDER_OWNER_MISMATCH', lambda: p.validate_selected_provider(
                    output, types.SimpleNamespace(is_file=lambda: True)))

    def mocked_preflight(self, bad_control=False, diversion=True):
        """Full main entry up to proof failure; all process calls and payloads are fixtures."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = root / 'metadata' / ('snapshot-' + p.SNAPSHOT); metadata.mkdir(parents=True)
            package_data = b'fixture archive, never executed or parsed as a real deb'
            package_hash = hashlib.sha256(package_data).hexdigest()
            dependency_data = b'second fixture archive'
            dependency_hash = hashlib.sha256(dependency_data).hexdigest()
            package = ('Package: pkgconf\nVersion: 1.8.0-1\nArchitecture: amd64\n'
                       'Provides: pkg-config (= 0.29-1)\nFilename: pool/universe/p/pkgconf/fixture.deb\n'
                       f'Size: {len(package_data)}\nSHA256: {package_hash}\n\n'
                       'Package: fixture-dependency\nVersion: 1\nArchitecture: amd64\n'
                       'Filename: pool/universe/f/fixture/fixture.deb\n'
                       f'Size: {len(dependency_data)}\nSHA256: {dependency_hash}\n\n')
            index = lzma.compress(package.encode()); index_hash = hashlib.sha256(index).hexdigest()
            release = ('-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n'
                       'Origin: Ubuntu\nSuite: jammy\nCodename: jammy\nMD5Sum:\nSHA256:\n'
                       f' {index_hash} {len(index)} main/binary-amd64/Packages.xz\n'
                       f' {index_hash} {len(index)} universe/binary-amd64/Packages.xz\n'
                       '-----BEGIN PGP SIGNATURE-----\nfixture\n').encode()
            (metadata / 'jammy.InRelease').write_bytes(release)
            for component in ('main', 'universe'):
                (metadata / ('jammy.' + component + '.amd64.Packages.xz')).write_bytes(index)
            keyring = root / 'fixture-keyring'; keyring.write_bytes(b'fixture')
            direct = 'pkgconf: /usr/bin/pkg-config\n'
            owner = ('diversion by pkgconf from: /usr/bin/pkg-config\n'
                     'diversion by pkgconf to: /usr/bin/pkg-config.real\n' + direct)
            if not diversion:
                owner = direct
            calls = []
            def process(argv, **kwargs):
                calls.append(list(argv))
                output, error = '', ''
                if argv[0] == '/usr/bin/dpkg-query':
                    self.assertEqual(kwargs['stderr'], subprocess.PIPE)
                    output, error = owner, 'warning: PRIVATE_WARNING is not ownership evidence\n'
                elif argv[0] == '/usr/bin/gpgv':
                    output = '[GNUPG:] VALIDSIG ' + p.SIGNER + ' fixture\n'
                elif argv[0] == '/usr/bin/dpkg-deb':
                    output = (control('pkgconf', 'WRONG' if bad_control else '1.8.0-1')
                              if package_hash in argv[2] else control('fixture-dependency'))
                elif argv[0] == '/usr/bin/apt-get':
                    self.assertIn(argv[1:], (['--version'], ['update']), 'Transaction work must not precede provider proof')
                    output = 'apt 2.4.fixture\n'
                elif '--native-solve' in argv:
                    solution = {'transaction': [{'name': 'fixture-dependency', 'version': '1',
                                'architecture': 'amd64', 'action': 'install', 'old_version': None}],
                                'closure': [row('pkgconf', '1.8.0-1'), row('fixture-dependency')]}
                    Path(argv[-1]).write_text(json.dumps(solution))
                else:
                    self.assertEqual(argv[:4], ['/usr/bin/python3', '-I', '-B', '-c'])
                return types.SimpleNamespace(returncode=0, stdout=output, stderr=error)
            def output(argv, **kwargs):
                return 'amd64\n' if argv[0] == '/usr/bin/dpkg' else b'fixture data tar'
            original_read = Path.read_bytes; original_is_file = Path.is_file
            def read(path):
                return control('pkgconf', '1.8.0-1').encode() if str(path) == '/var/lib/dpkg/status' else original_read(path)
            def is_file(path):
                return True if str(path) in ('/usr/bin/gpgv', '/usr/bin/pkg-config') else original_is_file(path)
            def payload(record, destination):
                destination.write_bytes(package_data if record['Package'] == 'pkgconf' else dependency_data)
            with mock.patch.object(p.sys, 'argv', [str(SCRIPT), '--root', str(root), '--install']), \
                 mock.patch.object(p, 'KEYRING', keyring), mock.patch.object(p, 'check_host'), \
                 mock.patch.object(p, 'RELEASE_HASHES', {'jammy': hashlib.sha256(release).hexdigest()}), \
                 mock.patch.object(p, 'RELEASE_SIZES', {'jammy': len(release)}), \
                 mock.patch.object(Path, 'read_bytes', read), mock.patch.object(Path, 'is_file', is_file), \
                 mock.patch.object(p.subprocess, 'run', side_effect=process), \
                 mock.patch.object(p.subprocess, 'check_output', side_effect=output), \
                 mock.patch.object(p, 'download_payload', side_effect=payload), \
                 mock.patch.object(p, 'verify_provider_member', side_effect=RuntimeError('Fixture byte mismatch')) as verify:
                with self.assertRaisesRegex(RuntimeError, 'Archive control identity mismatch' if bad_control else 'Fixture byte mismatch'):
                    p.main()
                if bad_control:
                    verify.assert_not_called()
                else:
                    verify.assert_called_once_with(b'fixture data tar', Path('/'))
            self.assertFalse(any('install' in argv or '--assume-yes' in argv for argv in calls))
            self.assertTrue((root / 'package-preparation/apt.conf').exists())
            self.assertTrue(json.loads((root / 'package-preparation/transaction.json').read_text())['transaction'])
            self.assertEqual((root / 'package-preparation/logs/pkg-config-owner.log').read_text(), owner)
            self.assertIn('PRIVATE_WARNING', (root / 'package-preparation/logs/pkg-config-owner.stderr.log').read_text())

    def test_provider_byte_failure_prevents_transaction_planning_and_install(self):
        self.mocked_preflight()

    def test_provider_control_mismatch_prevents_byte_proof_and_install(self):
        self.mocked_preflight(bad_control=True)

    def test_plain_pkgconf_owner_also_requires_preinstall_byte_proof(self):
        self.mocked_preflight(diversion=False)


class TestProviderArchiveBytes(unittest.TestCase):
    def archive(self, symlink=False):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:') as archive:
            info = tarfile.TarInfo('./usr/bin/' + ('pkgconf' if symlink else 'pkg-config'))
            info.size = 4
            archive.addfile(info, io.BytesIO(b'good'))
            if symlink:
                link = tarfile.TarInfo('./usr/bin/pkg-config'); link.type = tarfile.SYMTYPE; link.linkname = 'pkgconf'
                archive.addfile(link)
        return buffer.getvalue()

    def test_regular_provider_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'usr/bin').mkdir(parents=True); (root / 'usr/bin/pkg-config').write_bytes(b'good')
            p.verify_provider_member(self.archive(), root)
            (root / 'usr/bin/pkg-config').write_bytes(b'evil')
            with self.assertRaisesRegex(RuntimeError, 'differs'):
                p.verify_provider_member(self.archive(), root)

    def test_pkgconf_symlink_and_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'usr/bin').mkdir(parents=True); (root / 'usr/bin/pkgconf').write_bytes(b'good')
            (root / 'usr/bin/pkg-config').symlink_to('pkgconf')
            p.verify_provider_member(self.archive(True), root)
            (root / 'usr/bin/pkg-config').unlink(); (root / 'usr/bin/pkg-config').symlink_to('/usr/bin/pkgconf')
            with self.assertRaisesRegex(RuntimeError, 'alias'):
                p.verify_provider_member(self.archive(True), root)

    def test_candidate_file_row(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); file = root / 'file'; file.write_text('data')
            result = p.file_row(root, file)
            self.assertEqual(result['ref'], {'base': 'candidate', 'path': 'file'})
            self.assertEqual(result['sha256'], p.sha(file))


class TestVerifiedDownloads(unittest.TestCase):
    class Response(io.BytesIO):
        status = 200

    def test_hash_checked_before_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'package.deb'
            opener = types.SimpleNamespace(open=lambda *args, **kwargs: self.Response(b'good'))
            with mock.patch.object(p.urllib.request, 'build_opener', return_value=opener):
                p.download_verified(p.ORIGIN + 'pool/a.deb', path, 4, hashlib.sha256(b'good').hexdigest())
            self.assertEqual(path.read_bytes(), b'good')
            self.assertFalse(path.with_name(path.name + '.partial').exists())

    def test_bad_hash_never_committed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'package.deb'
            opener = types.SimpleNamespace(open=lambda *args, **kwargs: self.Response(b'evil'))
            with mock.patch.object(p.urllib.request, 'build_opener', return_value=opener), self.assertRaises(RuntimeError):
                p.download_verified(p.ORIGIN + 'pool/a.deb', path, 4, hashlib.sha256(b'good').hexdigest())
            self.assertFalse(path.exists())

    def test_oversize_never_committed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'package.deb'
            opener = types.SimpleNamespace(open=lambda *args, **kwargs: self.Response(b'too-large'))
            with mock.patch.object(p.urllib.request, 'build_opener', return_value=opener), self.assertRaises(RuntimeError):
                p.download_verified(p.ORIGIN + 'pool/a.deb', path, 4, 'a' * 64)
            self.assertFalse(path.exists())

    def test_other_origin_rejected_before_network(self):
        with mock.patch.object(p.urllib.request, 'build_opener') as network, self.assertRaises(RuntimeError):
            p.download_verified('https://evil/a', Path('/tmp/not-written'), 4, 'a' * 64)
        network.assert_not_called()

    def test_existing_bad_file_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'package.deb'; path.write_bytes(b'old!')
            with self.assertRaises(RuntimeError):
                p.download_verified(p.ORIGIN + 'pool/a.deb', path, 4, hashlib.sha256(b'good').hexdigest())
            self.assertEqual(path.read_bytes(), b'old!')


class TestNativeAPTHelper(unittest.TestCase):
    def test_native_api_plan_and_full_closure(self):
        a, old_a, b = version(1, 'a', '2'), version(1, 'a'), version(2, 'b')
        pkg_a, pkg_b = a.parent_pkg, b.parent_pkg
        pkg_a.current_ver = old_a; pkg_a.version_list = [a]; pkg_a.essential = False
        pkg_b.current_ver = b; pkg_b.version_list = [b]; pkg_b.essential = False
        a.depends_list = {'Depends': [[dep(b)]]}
        class Cache(dict):
            packages = [pkg_a, pkg_b]
        cache = Cache(a=pkg_a, b=pkg_b)
        class DC:
            broken_count = 0
            def __init__(self, cache): self.marked = set(); self.candidates = {}
            def set_candidate_ver(self, pkg, candidate): self.candidates[pkg.id] = candidate
            def get_candidate_ver(self, pkg): return self.candidates.get(pkg.id, pkg.current_ver)
            def mark_install(self, pkg, *args): self.marked.add(pkg.id)
            def marked_install(self, pkg): return pkg.id in self.marked
            def marked_upgrade(self, pkg): return False
            def marked_delete(self, pkg): return False
            def marked_downgrade(self, pkg): return False
            def marked_reinstall(self, pkg): return False
        fake = types.SimpleNamespace(VERSION='2.4.fixture', config={}, init_config=lambda: None,
            read_config_file=lambda *args: None, init_system=lambda: None, Cache=lambda *args: cache,
            DepCache=DC, ProblemResolver=lambda *args: types.SimpleNamespace(protect=lambda *args: None, resolve=lambda *args: True))
        with tempfile.TemporaryDirectory() as temp:
            input_path, output_path = Path(temp) / 'input', Path(temp) / 'output'
            input_path.write_text(json.dumps({'config': 'fixture', 'roots': {'a': '2'}}))
            with mock.patch.dict(sys.modules, {'apt_pkg': fake}):
                p.native_solve(input_path, output_path)
            result = json.loads(output_path.read_text())
        self.assertEqual([r['name'] for r in result['closure']], ['a', 'b'])
        self.assertEqual(result['transaction'][0]['old_version'], '1')
        self.assertEqual(result['transaction'][0]['action'], 'upgrade')


class TestSafeFailureDiagnostics(unittest.TestCase):
    SECRET = 'SECRET_TOKEN /private/path https://private.example/SECRET'

    def setUp(self):
        self.context = dict(p.DIAGNOSTIC_CONTEXT)
        self.root = p.DIAGNOSTIC_ROOT
        p.DIAGNOSTIC_ROOT = None
        p.phase('bootstrap')

    def tearDown(self):
        p.DIAGNOSTIC_CONTEXT.clear()
        p.DIAGNOSTIC_CONTEXT.update(self.context)
        p.DIAGNOSTIC_ROOT = self.root

    def check(self, error, operation, code):
        p.phase(operation, 'jammy-updates', 'universe')
        receipt = p.failure_diagnostic(error)
        self.assertEqual(set(receipt), {'schema', 'code', 'operation', 'exception_class',
                                       'http_status', 'errno', 'returncode', 'suite', 'component'})
        self.assertEqual(receipt['schema'], 1)
        self.assertEqual(receipt['code'], code)
        self.assertIn(receipt['code'], p.DIAGNOSTIC_CODES)
        self.assertIn(receipt['operation'], p.DIAGNOSTIC_OPERATIONS)
        self.assertIn(receipt['exception_class'], p.DIAGNOSTIC_CLASSES)
        encoded = json.dumps(receipt)
        self.assertNotIn('SECRET', encoded)
        self.assertNotIn('/private', encoded)
        self.assertNotIn('https://', encoded)
        self.assertLess(len(encoded), 1024)
        return receipt

    def test_http_status_without_url_reason_or_headers(self):
        error = p.urllib.error.HTTPError('https://private.example/SECRET', 403,
                                        self.SECRET, {'Authorization': self.SECRET}, None)
        receipt = self.check(error, 'release-download', 'APT_HTTP_ERROR')
        self.assertEqual(receipt['http_status'], 403)

    def test_dns_network_tls_and_timeout_categories(self):
        for error, code in (
            (p.urllib.error.URLError(OSError(-3, self.SECRET)), 'APT_NETWORK_ERROR'),
            (p.urllib.error.URLError(self.SECRET), 'APT_NETWORK_ERROR'),
            (p.urllib.error.URLError(p.ssl.SSLError(1, self.SECRET)), 'APT_TLS_ERROR'),
            (p.urllib.error.URLError(TimeoutError(self.SECRET)), 'APT_TIMEOUT'),
            (TimeoutError(self.SECRET), 'APT_TIMEOUT'),
        ):
            with self.subTest(code=code):
                self.check(error, 'release-download', code)

    def test_subprocess_rc_and_prerequisite_category(self):
        for operation, code in (('prerequisites', 'APT_PREREQUISITE_FAILED'),
                                ('apt-update', 'APT_SUBPROCESS_FAILED')):
            for error in (p.ProcessFailure(100), subprocess.CalledProcessError(
                    100, [self.SECRET], self.SECRET, self.SECRET)):
                receipt = self.check(error, operation, code)
                self.assertEqual(receipt['returncode'], 100)

    def test_file_error_errno_is_allowlisted_only(self):
        self.assertEqual(self.check(PermissionError(13, self.SECRET, self.SECRET),
                                   'base-inventory', 'APT_FILE_ACCESS_ERROR')['errno'], 13)
        self.assertIsNone(self.check(OSError(123456, self.SECRET),
                                    'base-inventory', 'APT_FILE_ACCESS_ERROR')['errno'])

    def test_parse_classes_are_projected_without_control_text(self):
        for error in (RuntimeError(self.SECRET), KeyError(self.SECRET), ValueError(self.SECRET),
                      lzma.LZMAError(self.SECRET), UnicodeDecodeError('utf-8', b'\xff', 0, 1, self.SECRET)):
            for operation in ('release-parse', 'index-parse'):
                self.check(error, operation, 'APT_METADATA_PARSE_ERROR')

    def test_unknown_exception_class_and_context_never_echoed(self):
        class SecretException(Exception):
            def __str__(self):
                raise AssertionError('Diagnostic must not stringify exceptions')
        self.assertEqual(self.check(SecretException(self.SECRET), 'bootstrap',
                                    'APT_UNEXPECTED_ERROR')['exception_class'], 'OtherError')
        p.phase(self.SECRET, self.SECRET, self.SECRET)
        receipt = p.failure_diagnostic(RuntimeError(self.SECRET))
        self.assertEqual(receipt['operation'], 'bootstrap')
        self.assertIsNone(receipt['suite'])
        self.assertIsNone(receipt['component'])
        self.assertNotIn('SECRET', json.dumps(receipt))

    def test_invalid_numeric_values_not_emitted(self):
        for rc in (True, 256, -256, self.SECRET):
            self.assertIsNone(self.check(p.ProcessFailure(rc), 'apt-update',
                                        'APT_SUBPROCESS_FAILED')['returncode'])
        for status in (True, 99, 600, self.SECRET):
            error = p.urllib.error.HTTPError(self.SECRET, status, self.SECRET, {}, None)
            self.assertIsNone(self.check(error, 'release-download', 'APT_HTTP_ERROR')['http_status'])

    def test_nested_native_error_preserves_closed_class_and_outer_exit(self):
        p.phase('native-solver')
        child = p.failure_diagnostic(AttributeError(self.SECRET))
        encoded = p.DIAGNOSTIC_PREFIX + json.dumps(child) + '\n'
        self.assertEqual(p.parse_child_receipt('private output\n' + encoded), child)
        receipt = p.failure_diagnostic(p.ProcessFailure(2, child))
        self.assertEqual(receipt['exception_class'], 'AttributeError')
        self.assertEqual(receipt['operation'], 'native-solver')
        self.assertEqual(receipt['returncode'], 2)
        self.assertNotIn('SECRET', json.dumps(receipt))

    def test_native_apt_error_class_is_explicit_not_arbitrary_name(self):
        native_error = type('Error', (Exception,), {'__module__': 'apt_pkg'})
        other_error = type('Error', (Exception,), {'__module__': 'private_module'})
        p.phase('native-solver')
        self.assertEqual(p.failure_diagnostic(native_error(self.SECRET))['exception_class'], 'AptPkgError')
        self.assertEqual(p.failure_diagnostic(other_error(self.SECRET))['exception_class'], 'OtherError')

    def test_invalid_child_records_are_not_propagated(self):
        p.phase('native-solver')
        good = p.failure_diagnostic(RuntimeError(self.SECRET))
        bad = [dict(good, extra=self.SECRET), dict(good, schema=True), dict(good, returncode=True),
               dict(good, http_status=999), dict(good, code=self.SECRET),
               dict(good, exception_class=self.SECRET), dict(good, errno=123456),
               dict(good, operation='installation'), dict(good, suite='jammy'),
               dict(good, code={}), dict(good, exception_class=[])]
        for value in bad:
            with self.subTest(value=value):
                self.assertIsNone(p.validated_child_receipt(value))
                receipt = p.failure_diagnostic(p.ProcessFailure(2, value))
                self.assertEqual(receipt['exception_class'], 'ProcessFailure')
                self.assertEqual(receipt['code'], 'APT_SUBPROCESS_FAILED')
        raw = json.dumps(good)
        for data in ('', p.DIAGNOSTIC_PREFIX + raw + '\nSECRET last line\n',
                     p.DIAGNOSTIC_PREFIX + raw[:-1] + ',"schema":1}',
                     p.DIAGNOSTIC_PREFIX + 'x' * 2000,
                     p.DIAGNOSTIC_PREFIX + raw.replace('RuntimeError', '\u79d8\u5bc6')):
            self.assertIsNone(p.parse_child_receipt(data))

    def test_child_receipt_is_used_only_in_native_solver_operation(self):
        p.phase('native-solver')
        child = p.failure_diagnostic(AttributeError(self.SECRET))
        p.phase('apt-update')
        receipt = p.failure_diagnostic(p.ProcessFailure(100, child))
        self.assertEqual(receipt['exception_class'], 'ProcessFailure')
        self.assertEqual(receipt['code'], 'APT_SUBPROCESS_FAILED')

    def test_receipt_is_exclusive_and_marker_is_last_safe_line(self):
        with tempfile.TemporaryDirectory() as temp:
            p.DIAGNOSTIC_ROOT = Path(temp)
            output = io.StringIO()
            with mock.patch.object(p.sys, 'stderr', output):
                receipt = p.emit_failure(RuntimeError(self.SECRET))
            target = Path(temp) / 'package-failure.json'
            self.assertEqual(json.loads(target.read_text()), receipt)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(output.getvalue(), p.DIAGNOSTIC_PREFIX +
                             json.dumps(receipt, sort_keys=True, separators=(',', ':')) + '\n')
            target.write_text('existing cohort')
            with mock.patch.object(p.sys, 'stderr', io.StringIO()):
                p.emit_failure(RuntimeError(self.SECRET))
            self.assertEqual(target.read_text(), 'existing cohort')

    def test_receipt_does_not_follow_symlink_or_wait_on_fifo(self):
        with tempfile.TemporaryDirectory() as temp:
            p.DIAGNOSTIC_ROOT = Path(temp)
            existing = Path(temp) / 'existing'; existing.write_text('preserve')
            target = Path(temp) / 'package-failure.json'; target.symlink_to(existing)
            with mock.patch.object(p.sys, 'stderr', io.StringIO()):
                p.emit_failure(RuntimeError(self.SECRET))
            self.assertEqual(existing.read_text(), 'preserve')
            target.unlink(); os.mkfifo(target)
            with mock.patch.object(p.sys, 'stderr', io.StringIO()):
                p.emit_failure(RuntimeError(self.SECRET))

    def test_main_wrong_host_emits_diagnostic_without_staging_or_native_apt(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(p.sys, 'argv', [str(SCRIPT), '--root', temp]), \
                 mock.patch.object(p.subprocess, 'check_output', return_value='amd64\n'), \
                 mock.patch.object(p, 'os_values', return_value={'ID': 'debian', 'VERSION_ID': '13'}), \
                 mock.patch.object(p.subprocess, 'run') as native, self.assertRaises(RuntimeError) as raised:
                p.main()
            native.assert_not_called()
            self.assertEqual(p.failure_diagnostic(raised.exception)['operation'], 'host-check')
            self.assertFalse((Path(temp) / 'package-preparation').exists())


if __name__ == '__main__':
    unittest.main()

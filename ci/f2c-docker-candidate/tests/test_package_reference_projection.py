"""Pure signed-coordinate projection fixtures; never run preparation or a build.

Only bounded diagnostic readers and the public receipt CLI are exercised. All
private logs and source-index-only relocations are synthetic temporary files.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import buildkit_diagnostic as diagnostic
import public_receipt as public

PREFIX = 'SAFE_PACKAGE_TARGET_REFERENCE_V2:'
LEGACY_PREFIX = 'SAFE_PACKAGE_TARGET_REFERENCE:'
PRIVATE = 'PRIVATE_PACKAGE PRIVATE_VERSION /PRIVATE_PATH https://private.invalid/ PRIVATE_TOKEN'
COUNTS = {
    ('jammy', 'main'): 6090, ('jammy', 'universe'): 58923,
    ('jammy-updates', 'main'): 21642, ('jammy-updates', 'universe'): 5960,
    ('jammy-security', 'main'): 20377, ('jammy-security', 'universe'): 5017,
}


def point(suite='jammy', component='main', stanza=1):
    return {'status': 'mapped', 'suite': suite, 'component': component, 'stanza': stanza}


def reference():
    return {'schema': 2, 'snapshot': '20261021T000000Z', 'kind': 'post-delta-version',
            'selection': 'first_sorted_key', 'mismatch_count': 'single',
            'expected_source': 'transaction', 'observed_matches_baseline': True,
            'baseline': point(), 'expected': point(stanza=2), 'observed': point()}


def detail(**updates):
    result = {'schema': 1, 'code': 'APT_INVARIANT_REJECTED',
              'operation': 'post-delta-version', 'exception_class': 'RuntimeError',
              'http_status': None, 'errno': None, 'returncode': None,
              'suite': None, 'component': None}
    result.update(updates)
    return result


def projection(ref=None, include_reference=True, **updates):
    result = {'schema': 1, 'source': 'package', 'diagnostic_only': True, 'detail': detail()}
    if include_reference:
        result['reference'] = reference() if ref is None else ref
    result.update(updates)
    return result


def header(source='package', vertex=15, command=None):
    command = diagnostic.RUN_COMMANDS[source] if command is None else command
    return '#{} [ 8/18] RUN {}\n'.format(vertex, command)


def output(payload, vertex=15):
    return '#{} 0.123 {}\n'.format(vertex, payload)


def ref_marker(ref=None, vertex=15):
    return output(PREFIX + json.dumps(reference() if ref is None else ref,
                                      sort_keys=True, separators=(',', ':')), vertex)


def failure(value=None, vertex=15):
    return output(diagnostic.PACKAGE_PREFIX + json.dumps(detail() if value is None else value,
                                                        sort_keys=True, separators=(',', ':')), vertex)


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackageReferenceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.log = self.root / 'preparation.log'

    def parse(self, raw):
        self.log.write_bytes(raw.encode('utf-8') if type(raw) is str else raw)
        return diagnostic.parse_log(self.log)

    def assert_invalid_reference(self, ref):
        self.assertIsNone(diagnostic.validate_projection(projection(ref)))
        self.assertIsNone(self.parse(header() + ref_marker(ref) + failure()))

    def test_shared_schema_is_static_bounded_and_exact(self):
        schema = load_file('reference_schema_fixture', ROOT / 'package_reference.py')
        self.assertEqual(schema.PREFIX, PREFIX)
        self.assertEqual(schema.MAX_REFERENCE_BYTES, 2048)
        # Independently pinned signed-index row bounds must not drift with logs.
        self.assertEqual(schema.INDEX_STANZA_COUNTS, COUNTS)

    def test_valid_reference_round_trips_and_returns_deep_fresh_data(self):
        value = projection()
        result = diagnostic.validate_projection(value)
        self.assertEqual(result, value)
        self.assertIsNot(result, value)
        self.assertIsNot(result['detail'], value['detail'])
        self.assertIsNot(result['reference'], value['reference'])
        for field in ('baseline', 'expected', 'observed'):
            self.assertIsNot(result['reference'][field], value['reference'][field])
        result['reference']['expected']['stanza'] = 3
        self.assertEqual(value['reference']['expected']['stanza'], 2)
        self.assertEqual(self.parse(header() + ref_marker() + failure()), value)

    def test_all_signed_index_boundary_coordinates_are_accepted(self):
        for (suite, component), count in COUNTS.items():
            for stanza in (0, count - 1):
                with self.subTest(suite=suite, component=component, stanza=stanza):
                    ref = reference()
                    ref['expected'] = point(suite, component, stanza)
                    self.assertEqual(diagnostic.validate_projection(projection(ref)), projection(ref))
                    self.assertEqual(self.parse(header() + ref_marker(ref) + failure()), projection(ref))

    def test_finite_reference_variants_and_unmapped_points_are_accepted(self):
        refs = []
        for count in ('single', 'multiple'):
            for source in ('baseline', 'transaction'):
                for matches in ((False,) if source == 'baseline' else (True, False)):
                    ref = reference()
                    ref.update(mismatch_count=count, expected_source=source,
                               observed_matches_baseline=matches)
                    if source == 'baseline':
                        ref['expected'] = deepcopy(ref['baseline'])
                    if not matches:
                        ref['observed'] = point(stanza=3)
                    refs.append(ref)
        unmapped = reference()
        for field in ('baseline', 'expected', 'observed'):
            unmapped[field] = {'status': 'unmapped'}
        refs.append(unmapped)
        absent = reference()
        absent.update(baseline={'status': 'absent'}, observed_matches_baseline=None)
        refs.append(absent)
        for ref in refs:
            with self.subTest(ref=ref):
                self.assertEqual(diagnostic.validate_projection(projection(ref)), projection(ref))

    def test_reference_missing_extra_and_wrong_container_shapes_are_rejected(self):
        for field in reference():
            ref = reference()
            del ref[field]
            with self.subTest(missing=field):
                self.assert_invalid_reference(ref)
        for invalid in ([], True, 2, PRIVATE, {'raw': PRIVATE}, dict(reference(), raw=PRIVATE)):
            with self.subTest(invalid=invalid):
                self.assert_invalid_reference(invalid)
        value = projection()
        value['reference'] = None
        self.assertIsNone(diagnostic.validate_projection(value))
        self.assertIsNone(diagnostic.validate_projection(dict(projection(), raw=PRIVATE)))

    def test_reference_finite_enums_and_exact_scalar_types_are_closed(self):
        cases = {
            'schema': (True, 2.0, '2', 1, 3, None),
            'snapshot': ('20261022T000000Z', PRIVATE, None, 20261021),
            'kind': ('post-delta-held', PRIVATE, None, []),
            'selection': ('last_sorted_key', PRIVATE, None, {}),
            'mismatch_count': ('none', '1', 1, True, PRIVATE, None),
            'expected_source': ('observed', PRIVATE, None, []),
            'observed_matches_baseline': (0, 1, 'true', 'false', PRIVATE, [], {}),
        }
        for field, values in cases.items():
            for invalid in values:
                ref = reference()
                ref[field] = invalid
                with self.subTest(field=field, invalid=invalid):
                    self.assert_invalid_reference(ref)

    def test_coordinate_status_shape_and_per_index_bounds_are_closed(self):
        malformed = [None, [], PRIVATE, True, {}, {'status': 'unknown'},
                     {'status': 'unmapped', 'stanza': 1},
                     dict(point(), raw=PRIVATE), dict(point(), suite=PRIVATE),
                     dict(point(), component=PRIVATE), dict(point(), status=True)]
        for field in point():
            item = point()
            del item[field]
            malformed.append(item)
        for field in ('baseline', 'expected', 'observed'):
            for item in malformed:
                ref = reference()
                ref[field] = item
                with self.subTest(field=field, item=item):
                    self.assert_invalid_reference(ref)
        for field in ('expected', 'observed'):
            ref = reference()
            ref[field] = {'status': 'absent'}
            self.assert_invalid_reference(ref)
        for (suite, component), count in COUNTS.items():
            for stanza in (True, False, -1, count, count + 1, 10**100, 1.0, '1', None):
                ref = reference()
                ref['expected'] = point(suite, component, stanza)
                with self.subTest(suite=suite, component=component, stanza=stanza):
                    self.assert_invalid_reference(ref)

    def test_reference_relationships_cannot_contradict_the_version_mismatch(self):
        malformed = []
        absent = reference()
        absent['baseline'] = {'status': 'absent'}
        malformed.append(absent)
        absent = deepcopy(absent)
        absent.update(expected_source='baseline', observed_matches_baseline=None)
        malformed.append(absent)
        unknown_match = reference()
        unknown_match['observed_matches_baseline'] = None
        malformed.append(unknown_match)
        contradictory_match = reference()
        contradictory_match['observed_matches_baseline'] = False
        malformed.append(contradictory_match)
        contradictory_match = reference()
        contradictory_match['observed'] = point(stanza=3)
        malformed.append(contradictory_match)
        no_mismatch = reference()
        no_mismatch['expected'] = deepcopy(no_mismatch['observed'])
        malformed.append(no_mismatch)
        baseline_source = reference()
        baseline_source['expected_source'] = 'baseline'
        malformed.append(baseline_source)
        for ref in malformed:
            with self.subTest(ref=ref):
                self.assert_invalid_reference(ref)

    def test_reference_is_allowed_only_on_exact_version_invariant_failure(self):
        wrong_details = [detail(code='APT_UNEXPECTED_ERROR'),
                         detail(operation='post-inventory-delta'),
                         detail(operation='post-delta-held'),
                         detail(exception_class='KeyError')]
        for key, value in (('http_status', 404), ('errno', 2), ('returncode', 1),
                           ('suite', 'jammy'), ('component', 'main')):
            wrong_details.append(detail(**{key: value}))
        for value in wrong_details:
            with self.subTest(detail=value):
                self.assertIsNotNone(diagnostic.validate_projection(projection(include_reference=False,
                                                                                detail=value)))
                self.assertIsNone(diagnostic.validate_projection(projection(detail=value)))
                self.assertIsNone(self.parse(header() + ref_marker() + failure(value)))
        self.assertIsNone(diagnostic.validate_projection(projection(source='bootstrap',
                                          detail={'code': 'BLOCKED_signed_metadata'})))
        nested = detail(reference=reference())
        self.assertIsNone(diagnostic.validate_projection(projection(include_reference=False, detail=nested)))

    def test_missing_reference_preserves_legacy_failure_and_v1_hash_is_ignored(self):
        expected = projection(include_reference=False)
        legacy = output(LEGACY_PREFIX + 'a' * 64)
        for raw in (header() + failure(), header() + legacy + failure(),
                    header() + failure() + legacy,
                    header() + legacy + ref_marker() + failure()):
            with self.subTest(raw=raw):
                actual = self.parse(raw)
                self.assertEqual(actual, projection() if PREFIX in raw else expected)
        self.assertEqual(diagnostic.validate_projection(expected), expected)

    def test_unrelated_vertex_interleaving_does_not_break_same_vertex_pairing(self):
        unrelated = (header(vertex=77, command='echo PRIVATE_UNRELATED') +
                     output(PRIVATE, vertex=77) +
                     output(PRIVATE, vertex=88))
        raw = header() + ref_marker() + unrelated + failure() + '#15 ERROR: ' + PRIVATE + '\n'
        self.assertEqual(self.parse(raw), projection())
        raw = (header('bootstrap', 14) + header() + ref_marker() +
               output(diagnostic.BOOTSTRAP_PREFIX + diagnostic.BOOTSTRAP_SUCCESS, 14) + failure())
        self.assertEqual(self.parse(raw), projection())

    def test_same_vertex_intervening_payload_invalidates_reference(self):
        for payload in (PRIVATE, '', 'progress', LEGACY_PREFIX + 'a' * 64,
                        PREFIX + '{}', diagnostic.BOOTSTRAP_PREFIX + diagnostic.BOOTSTRAP_SUCCESS):
            with self.subTest(payload=payload):
                self.assertIsNone(self.parse(header() + ref_marker() + output(payload) + failure()))

    def test_duplicates_conflicts_or_after_failure_reference_are_rejected(self):
        other = reference()
        other['mismatch_count'] = 'multiple'
        fixtures = [header() + ref_marker() + ref_marker() + failure(),
                    header() + ref_marker() + ref_marker(other) + failure(),
                    header() + failure() + ref_marker(),
                    header() + ref_marker() + failure() + ref_marker(),
                    header() + ref_marker() + failure() + failure(),
                    header() + ref_marker() + failure() + header('bootstrap', 14) +
                    output(diagnostic.BOOTSTRAP_PREFIX + 'BLOCKED_signed_metadata', 14)]
        for raw in fixtures:
            with self.subTest(raw=raw):
                self.assertIsNone(self.parse(raw))

    def test_wrong_recognized_vertex_cannot_supply_reference(self):
        for raw in (header('bootstrap', 14) + ref_marker(vertex=14),
                    header('bootstrap', 14) + ref_marker(vertex=14) + header() + failure(),
                    header() + ref_marker() + header('bootstrap', 14) +
                    output(diagnostic.BOOTSTRAP_PREFIX + 'BLOCKED_signed_metadata', 14)):
            self.assertIsNone(self.parse(raw))

    def test_unknown_unframed_replayed_and_nested_references_never_attach(self):
        bare = PREFIX + json.dumps(reference()) + '\n'
        impostors = [ref_marker(vertex=99), bare, '0.123 ' + bare,
                     ' > [8/18] RUN ' + diagnostic.RUN_COMMANDS['package'] + '\n' + bare,
                     output(ref_marker().rstrip('\n')),
                     output('PRIVATE ' + bare.rstrip('\n')),
                     ref_marker() + header(),
                     header(vertex=99, command='echo PRIVATE') + ref_marker(vertex=99)]
        for impostor in impostors:
            with self.subTest(impostor=impostor):
                # Put unknown references before the real header, not between
                # a recognized reference and its corresponding failure.
                raw = impostor + ('' if impostor.endswith(header()) else header()) + failure()
                self.assertEqual(self.parse(raw), projection(include_reference=False))
        self.assertIsNone(self.parse(header() + ref_marker()))

    def test_malformed_duplicate_json_and_unbounded_reference_are_rejected(self):
        compact = json.dumps(reference(), separators=(',', ':'))
        duplicate_root = compact.replace('"schema":2', '"schema":2,"schema":2')
        duplicate_point = compact.replace('"status":"mapped"',
                                          '"status":"mapped","status":"mapped"', 1)
        bodies = ['{', '{} trailing', 'null', 'NaN', 'Infinity', '[]',
                  duplicate_root, duplicate_point, '[' * 1100 + ']' * 1100,
                  compact + ' ' * 2048, compact.replace('jammy', 'jammy\x00', 1),
                  compact.replace('jammy', 'jámmy', 1)]
        for body in bodies:
            with self.subTest(body=body[:100]):
                self.assertIsNone(self.parse(header() + output(PREFIX + body) + failure()))
        raw = header() + ref_marker()
        self.assertIsNone(self.parse(raw[:-1]))
        self.assertIsNone(self.parse(header() + ref_marker().replace('\n', '\r\n') + failure()))

    def test_reference_byte_bound_includes_padding_and_is_not_package_marker_bound(self):
        body = json.dumps(reference(), separators=(',', ':'))
        at_limit = body + ' ' * (2048 - len(body.encode('ascii')))
        self.assertEqual(self.parse(header() + output(PREFIX + at_limit) + failure()), projection())
        self.assertIsNone(self.parse(header() + output(PREFIX + at_limit + ' ') + failure()))

    def test_reference_is_not_authenticated_provenance_or_acceptance(self):
        result = self.parse(PRIVATE + '\n' + header() + ref_marker() + failure() + PRIVATE + '\n')
        self.assertEqual(result, projection())
        self.assertIs(result['diagnostic_only'], True)
        encoded = json.dumps(result, sort_keys=True)
        for forbidden in ('PRIVATE', 'https:', 'sha256', 'accepted', 'authenticated',
                          'package_name', 'architecture'):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(set(result['reference']), set(reference()))


class PackageReferenceReceiptTests(unittest.TestCase):
    def state(self, root, raw=None):
        state = root / 'state'
        (state / 'prepared').mkdir(parents=True)
        (state / 'terminal.json').write_text(json.dumps({'phase': 'preparation', 'exit_code': 1}))
        (state / 'preparation-stderr.log').write_text(json.dumps({
            'schema': 1, 'stage': 'image_build', 'code': 'UNCLASSIFIED_PREPARATION_FAILURE',
            'exception_type': 'CalledProcessError'}))
        (state / 'prepared/preparation.log').write_text(
            PRIVATE + '\n' + header() + ref_marker() + failure() + '#15 ERROR: ' + PRIVATE + '\n'
            if raw is None else raw)
        return state

    def assert_safe_receipt(self, result):
        self.assertEqual(result['outcome'], 'failed_or_not_run')
        self.assertEqual(result['fresh_runtime_acceptance'], 'not_run')
        self.assertEqual(result['current_application_full_suite'], 'not_run')
        self.assertIs(result['raw_logs_durably_archived'], False)
        self.assertEqual(result['failure_category'], 'TRUSTED_PREPARATION_FAILED')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('private.invalid', json.dumps(result))

    def test_public_receipt_end_to_end_projects_only_closed_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.state(Path(temporary))
            result = public.project(state)
            self.assertEqual(result['buildkit_failure_detail'], projection())
            self.assertEqual(result['buildkit_structure']['failed_stage'], 'package')
            self.assert_safe_receipt(result)

    def test_public_receipt_independently_revalidates_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.state(Path(temporary))
            malformed = [dict(projection(), raw=PRIVATE),
                         projection(dict(reference(), private=PRIVATE)),
                         projection(detail=detail(operation='installation'))]
            for value in malformed:
                with self.subTest(value=value), patch.object(public._buildkit, 'parse_log', return_value=value):
                    result = public.project(state)
                self.assertIsNone(result['buildkit_failure_detail'])
                self.assert_safe_receipt(result)

    def test_public_receipt_cli_normal_and_optimized_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.state(root)
            for optimized in (False, True):
                with self.subTest(optimized=optimized):
                    command = [sys.executable, '-I', '-B'] + (['-O'] if optimized else [])
                    run = subprocess.run(command + [str(ROOT / 'public_receipt.py'), '--state', str(state)],
                                         cwd=root, capture_output=True, text=True, timeout=15)
                    self.assertEqual(run.returncode, 1, run.stderr)
                    self.assertEqual(run.stderr, '')
                    result = json.loads(run.stdout)
                    self.assertEqual(result['buildkit_failure_detail'], projection())
                    self.assert_safe_receipt(result)

    def test_source_index_only_relocation_has_all_diagnostic_dependencies(self):
        freeze = load_file('reference_source_index_fixture', ROOT / 'freeze_source_index.py')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staged = root / 'staged'
            staged.mkdir()
            # Package the actual dependency closure, then transfer only entries
            # named by its source index. No original-tree path is supplied to
            # the relocated isolated interpreter.
            members = ('public_receipt.py', 'preparation_diagnostic.py',
                       'buildkit_diagnostic.py', 'package_reference.py',
                       'preparation_stage_diagnostic.py', 'buildkit_census.py')
            for member in members:
                shutil.copyfile(ROOT / member, staged / member)
            source_hash = freeze.freeze(staged)
            index_bytes = (staged / 'source-index.json').read_bytes()
            self.assertEqual(hashlib.sha256(index_bytes).hexdigest(), source_hash)
            index = json.loads(index_bytes)
            self.assertEqual(set(index['files']), set(members))
            relocated = root / 'relocated'
            relocated.mkdir()
            for name, metadata in index['files'].items():
                data = (staged / name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), metadata['sha256'])
                (relocated / name).write_bytes(data)
            shutil.rmtree(staged)
            state = self.state(root)
            (state / 'reviewed-source.sha256').write_text(source_hash + '\n')
            for optimized in (False, True):
                with self.subTest(optimized=optimized):
                    command = [sys.executable, '-I', '-B'] + (['-O'] if optimized else [])
                    run = subprocess.run(command + [str(relocated / 'public_receipt.py'), '--state', str(state)],
                                         cwd=root, capture_output=True, text=True, timeout=15)
                    self.assertEqual(run.returncode, 1, run.stderr)
                    self.assertEqual(run.stderr, '')
                    result = json.loads(run.stdout)
                    self.assertEqual(result['buildkit_failure_detail'], projection())
                    self.assertEqual(result['source_index_sha256'], source_hash)
                    self.assert_safe_receipt(result)


if __name__ == '__main__':
    unittest.main()

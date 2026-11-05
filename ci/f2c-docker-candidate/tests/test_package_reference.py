"""Pure schema/producer tests; never run preparation, bootstrap, APT or Docker."""
import ast
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = 'PRIVATE_PACKAGE PRIVATE_VERSION /PRIVATE_PATH https://private.invalid/'
REJECTION = 'Unexpected installed inventory change; no readiness lock emitted'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


r = load('package_reference_test_schema', ROOT / 'package_reference.py')
p = load('package_reference_test_producer', ROOT / 'preparation' / 'prepare_packages.py')


def row(name='PRIVATE_package', version='PRIVATE_old', architecture='amd64', held=False):
    return dict(name=name, version=version, architecture=architecture, held=held)


def inv(*rows):
    return {item['name'] + ':' + item['architecture']: deepcopy(item) for item in rows}


def ident(item):
    return item['name'], item['version'], item['architecture']


def record(item, suite='jammy', component='main', stanza=1):
    return dict(Package=item['name'], Version=item['version'], Architecture=item['architecture'],
                _suite=suite, _component=component, _stanza=stanza, Filename=PRIVATE, SHA256=PRIVATE)


def point(suite='jammy', component='main', stanza=1):
    return dict(status='mapped', suite=suite, component=component, stanza=stanza)


def reference(**updates):
    value = dict(schema=2, snapshot=r.SNAPSHOT, kind='post-delta-version',
                 selection='first_sorted_key', mismatch_count='single',
                 expected_source='transaction', observed_matches_baseline=True,
                 baseline=point(stanza=1), expected=point(stanza=2), observed=point(stanza=1))
    value.update(updates)
    return value


def case():
    before = inv(row())
    transaction = [dict(row(version='PRIVATE_new'), action='upgrade', old_version='PRIVATE_old')]
    after = deepcopy(before)
    records = {ident(before['PRIVATE_package:amd64']): [record(row())],
               ident(transaction[0]): [record(transaction[0], stanza=2)]}
    return before, after, transaction, records


class ReferenceSchemaTests(unittest.TestCase):
    def test_constants_and_signed_stanza_bounds_match_original_exactly(self):
        self.assertEqual(r.PREFIX, 'SAFE_PACKAGE_TARGET_REFERENCE_V2:')
        self.assertEqual(r.MAX_REFERENCE_BYTES, 2048)
        self.assertEqual(r.INDEX_STANZA_COUNTS, p.INDEX_STANZA_COUNTS)
        self.assertEqual(r.INDEX_STANZA_COUNTS, {
            ('jammy', 'main'): 6090, ('jammy', 'universe'): 58923,
            ('jammy-updates', 'main'): 21642, ('jammy-updates', 'universe'): 5960,
            ('jammy-security', 'main'): 20377, ('jammy-security', 'universe'): 5017,
        })

    def test_fresh_deep_closed_output(self):
        original = reference()
        result = r.validate_reference(original)
        self.assertEqual(result, original)
        self.assertIsNot(result, original)
        for key in ('baseline', 'expected', 'observed'):
            self.assertIsNot(result[key], original[key])
        original['baseline']['stanza'] = 3
        self.assertEqual(result['baseline']['stanza'], 1)
        self.assertLess(len((r.PREFIX + json.dumps(result)).encode('ascii')), r.MAX_REFERENCE_BYTES)

    def test_entirely_unmapped_is_valid_and_carries_no_implied_equality(self):
        for source in ('baseline', 'transaction'):
            value = reference(baseline={'status': 'unmapped'}, expected={'status': 'unmapped'},
                              observed={'status': 'unmapped'}, observed_matches_baseline=False,
                              expected_source=source)
            self.assertEqual(r.validate_reference(value), value)
        value['expected_source'] = 'transaction'
        value['observed_matches_baseline'] = True
        self.assertEqual(r.validate_reference(value), value)

    def test_absent_baseline_only_for_new_transaction_with_null_match(self):
        value = reference(baseline={'status': 'absent'}, observed_matches_baseline=None)
        self.assertEqual(r.validate_reference(value), value)
        invalid = [dict(value, expected_source='baseline'), dict(value, observed_matches_baseline=False),
                   dict(value, observed_matches_baseline=True), reference(observed_matches_baseline=None),
                   reference(expected={'status': 'absent'}), reference(observed={'status': 'absent'})]
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(r.validate_reference(value))

    def test_schema_rejects_extra_missing_wrong_types_and_raw_fields(self):
        invalid = [None, [], {}, dict(reference(), private=PRIVATE)]
        for key in reference():
            value = reference(); del value[key]; invalid.append(value)
        for key, values in {
            'schema': (True, 2.0, '2', 1, None),
            'snapshot': ('20261022T000000Z', PRIVATE, [], None),
            'kind': (PRIVATE, 'post-delta-held', [], None),
            'selection': ('first', PRIVATE, [], None),
            'mismatch_count': ('many', 1, True, [], None),
            'expected_source': ('observed', [], None),
            'observed_matches_baseline': (0, 1, 'true', [], {}),
        }.items():
            invalid.extend(reference(**{key: value}) for value in values)
        class DictSubclass(dict):
            pass
        invalid.append(DictSubclass(reference()))
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(r.validate_reference(value))

    def test_points_reject_out_of_bounds_and_noncanonical_types(self):
        for suite, component in r.INDEX_STANZA_COUNTS:
            bound = r.INDEX_STANZA_COUNTS[(suite, component)]
            for stanza in (0, bound - 1):
                value = reference(expected=point(suite, component, stanza))
                if value['expected'] != value['observed']:
                    self.assertIsNotNone(r.validate_reference(value))
            for stanza in (-1, bound, bound + 1, True, 0.0, '0', None):
                self.assertIsNone(r.validate_reference(reference(expected=point(suite, component, stanza))))
        invalid = [dict(point(), private=PRIVATE), dict(point(), suite=PRIVATE),
                   dict(point(), component='restricted'), dict(point(), suite=[]),
                   dict(point(), component=[]), {'status': 'unmapped', 'stanza': 0},
                   {'status': 'absent', 'private': PRIVATE}, {'status': []}, {}, None]
        for value in invalid:
            self.assertIsNone(r.validate_reference(reference(expected=value)))

    def test_cross_field_contradictions_fail_closed(self):
        invalid = [reference(expected_source='baseline'),
                   reference(expected_source='baseline', expected=point(stanza=1)),
                   reference(observed_matches_baseline=True, observed=point(stanza=3)),
                   reference(observed_matches_baseline=False),
                   reference(observed=point(stanza=2), observed_matches_baseline=False),
                   reference(baseline={'status': 'unmapped'}, observed_matches_baseline=True)]
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(r.validate_reference(value))
        valid = reference(expected_source='baseline', expected=point(stanza=1),
                          observed=point(stanza=3), observed_matches_baseline=False)
        self.assertEqual(r.validate_reference(valid), valid)


class ReferenceProducerTests(unittest.TestCase):
    def test_old_observed_and_new_observed_relationships(self):
        before, after, transaction, records = case()
        self.assertEqual(r.make_reference(before, after, transaction, records), reference())
        after['PRIVATE_package:amd64']['version'] = 'PRIVATE_other'
        records[ident(after['PRIVATE_package:amd64'])] = [record(after['PRIVATE_package:amd64'], stanza=3)]
        self.assertEqual(r.make_reference(before, after, transaction, records),
                         reference(observed=point(stanza=3), observed_matches_baseline=False))

    def test_baseline_sourced_mismatch(self):
        before, after, _, records = case()
        after['PRIVATE_package:amd64']['version'] = 'PRIVATE_new'
        self.assertEqual(r.make_reference(before, after, [], records),
                         reference(expected_source='baseline', expected=point(),
                                   observed=point(stanza=2), observed_matches_baseline=False))

    def test_absent_and_unmapped_have_distinct_meanings(self):
        before, after, transaction, records = case()
        self.assertEqual(r.make_reference({}, after, transaction, records),
                         reference(baseline={'status': 'absent'}, observed_matches_baseline=None))
        result = r.make_reference(before, after, transaction, {})
        self.assertEqual(result, reference(baseline={'status': 'unmapped'},
            expected={'status': 'unmapped'}, observed={'status': 'unmapped'}))
        result = r.make_reference({}, after, transaction, {})
        self.assertEqual(result['baseline'], {'status': 'absent'})
        self.assertEqual(result['expected'], {'status': 'unmapped'})
        self.assertIsNone(result['observed_matches_baseline'])

    def test_only_equal_keyset_version_mismatches_can_produce(self):
        before, after, transaction, records = case()
        after['PRIVATE_package:amd64']['version'] = 'PRIVATE_new'
        self.assertIsNone(r.make_reference(before, after, transaction, records))
        for change in ('held', 'extra'):
            modified = deepcopy(after)
            modified['PRIVATE_package:amd64'][change] = PRIVATE
            self.assertIsNone(r.make_reference(before, modified, transaction, records))
        self.assertIsNone(r.make_reference(before, {}, transaction, records))
        after.update(inv(row('PRIVATE_extra')))
        self.assertIsNone(r.make_reference(before, after, transaction, records))
        self.assertIsNone(r.make_reference({}, {}, [], {}))

    def test_sorted_first_key_and_multiple_count_are_order_independent(self):
        a, z = row('PRIVATE_a'), row('PRIVATE_z')
        before = inv(z, a)
        after = inv(row('PRIVATE_z', 'PRIVATE_new'), row('PRIVATE_a', 'PRIVATE_new'))
        records = {ident(a): [record(a, stanza=5)], ident(z): [record(z, stanza=9)]}
        expected = reference(expected_source='baseline', mismatch_count='multiple',
                             baseline=point(stanza=5), expected=point(stanza=5),
                             observed={'status': 'unmapped'}, observed_matches_baseline=False)
        for reverse_before in (False, True):
            for reverse_after in (False, True):
                b = dict(reversed(list(before.items()))) if reverse_before else before
                a = dict(reversed(list(after.items()))) if reverse_after else after
                self.assertEqual(r.make_reference(b, a, [], records), expected)

    def test_duplicate_record_choice_has_fixed_suite_component_stanza_order(self):
        before, after, transaction, records = case()
        choices = [record(transaction[0], suite, component, stanza)
                   for suite, component, stanza in (
                       ('jammy-security', 'main', 0), ('jammy-updates', 'main', 0),
                       ('jammy', 'universe', 0), ('jammy', 'main', 9),
                       ('jammy', 'main', 8), ('jammy-security', 'universe', 0))]
        for rotate in range(len(choices)):
            for reverse in (False, True):
                candidates = choices[rotate:] + choices[:rotate]
                records[ident(transaction[0])] = list(reversed(candidates)) if reverse else candidates
                self.assertEqual(r.make_reference(before, after, transaction, records)['expected'],
                                 point(stanza=8))

    def test_exact_index_identity_is_rechecked_before_using_coordinates(self):
        for field in ('Package', 'Version', 'Architecture'):
            before, after, transaction, records = case()
            records[ident(transaction[0])][0][field] = 'PRIVATE_wrong'
            self.assertIsNone(r.make_reference(before, after, transaction, records))
        before, after, transaction, records = case()
        records[ident(transaction[0])][0]['_stanza'] = True
        self.assertIsNone(r.make_reference(before, after, transaction, records))
        before, after, transaction, records = case()
        records[(transaction[0]['name'], transaction[0]['version'], 'all')] = records.pop(ident(transaction[0]))
        self.assertEqual(r.make_reference(before, after, transaction, records)['expected'], {'status': 'unmapped'})

    def test_original_last_write_wins_expected_construction(self):
        before, after, transaction, records = case()
        before['PRIVATE_package:amd64']['extra'] = PRIVATE
        after['PRIVATE_package:amd64']['extra'] = PRIVATE
        transaction.insert(0, row(version='PRIVATE_superseded'))
        self.assertEqual(r.make_reference(before, after, transaction, records), reference())
        transaction.append(row(version='PRIVATE_old'))
        self.assertIsNone(r.make_reference(before, after, transaction, records))

    def test_inventory_transaction_and_candidate_bounds_are_inclusive(self):
        before = inv(*(row('PRIVATE_%04d' % n) for n in range(4096)))
        after = deepcopy(before)
        after['PRIVATE_0000:amd64']['version'] = 'PRIVATE_other'
        self.assertIsNotNone(r.make_reference(before, after, [], {}))
        before.update(inv(row('PRIVATE_4096')))
        after.update(inv(row('PRIVATE_4096')))
        self.assertIsNone(r.make_reference(before, after, [], {}))
        before, after, transaction, records = case()
        self.assertIsNotNone(r.make_reference(before, after, transaction * 4096, records))
        self.assertIsNone(r.make_reference(before, after, transaction * 4097, records))
        records[ident(transaction[0])] *= 16
        self.assertIsNotNone(r.make_reference(before, after, transaction, records))
        records[ident(transaction[0])].append(record(transaction[0], stanza=2))
        self.assertIsNone(r.make_reference(before, after, transaction, records))

    def test_malformed_bounded_inputs_produce_no_reference_or_private_output(self):
        before, after, transaction, records = case()
        invalid = [(None, after, transaction, records), (before, [], transaction, records),
                   (before, after, iter(transaction), records), (before, after, transaction, []),
                   ({'PRIVATE_wrong_key': row()}, after, transaction, records),
                   (before, {'PRIVATE_package:amd64': None}, transaction, records),
                   (before, after, [{'name': PRIVATE}], records)]
        for args in invalid:
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                self.assertIsNone(r.make_reference(*args))
            self.assertEqual(out.getvalue(), '')

    def test_inputs_are_not_mutated_and_raw_identity_cannot_escape(self):
        args = case()
        saved = deepcopy(args)
        result = r.make_reference(*args)
        self.assertEqual(args, saved)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('http', json.dumps(result))
        self.assertNotIn('version', json.dumps(result).replace('post-delta-version', ''))

    def test_diagnostic_exception_does_not_swallow_process_control(self):
        for exception in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(exception=exception), patch.object(r, '_indexed_point', side_effect=exception):
                with self.assertRaises(exception):
                    r.make_reference(*case())
        with patch.object(r, '_indexed_point', side_effect=ValueError(PRIVATE)):
            self.assertIsNone(r.make_reference(*case()))


class FailureIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.context = patch.object(p, 'DIAGNOSTIC_CONTEXT', {
            'operation': 'post-delta-version', 'suite': None, 'component': None})
        self.context.start(); self.addCleanup(self.context.stop)
        self.root = patch.object(p, 'DIAGNOSTIC_ROOT', None)
        self.root.start(); self.addCleanup(self.root.stop)

    def emit(self, error):
        output = io.StringIO()
        with redirect_stderr(output):
            receipt = p.emit_failure(error)
        return receipt, output.getvalue().splitlines()

    def test_one_valid_marker_immediately_precedes_unchanged_failure(self):
        error = RuntimeError(PRIVATE)
        p.attach_post_delta_reference(error, *case())
        receipt, lines = self.emit(error)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith(r.PREFIX))
        self.assertEqual(json.loads(lines[0][len(r.PREFIX):]), reference())
        self.assertEqual(lines[1], p.DIAGNOSTIC_PREFIX + json.dumps(receipt, sort_keys=True, separators=(',', ':')))
        self.assertEqual(receipt['operation'], 'post-delta-version')
        self.assertEqual(receipt['code'], 'APT_INVARIANT_REJECTED')
        self.assertEqual(receipt['exception_class'], 'RuntimeError')
        self.assertNotIn('PRIVATE', '\n'.join(lines))
        self.assertLessEqual(len(lines[0].encode('ascii')), r.MAX_REFERENCE_BYTES)

    def test_no_attribute_wrong_type_code_or_operation_never_emit_v2(self):
        self.assertEqual(len(self.emit(RuntimeError(PRIVATE))[1]), 1)
        class OtherRuntime(RuntimeError):
            pass
        for error in (OtherRuntime(PRIVATE), p.ProcessFailure(9), ValueError(PRIVATE)):
            error.package_target_reference_v2 = reference()
            self.assertEqual(len(self.emit(error)[1]), 1)
        for operation in ('post-delta-held', 'post-delta-match', 'post-inventory-delta', 'installation'):
            p.phase(operation)
            error = RuntimeError(PRIVATE)
            error.package_target_reference_v2 = reference()
            self.assertEqual(len(self.emit(error)[1]), 1)
            fresh = RuntimeError(PRIVATE)
            p.attach_post_delta_reference(fresh, *case())
            self.assertFalse(hasattr(fresh, 'package_target_reference_v2'))
        p.phase('post-delta-version')
        error = RuntimeError(PRIVATE); error.package_target_reference_v2 = reference()
        receipt = p.failure_diagnostic(error)
        for key, value in (('code', 'APT_UNEXPECTED_ERROR'), ('exception_class', 'OtherError'),
                           ('http_status', 404), ('errno', 2), ('returncode', 1),
                           ('suite', 'jammy'), ('component', 'main')):
            changed = dict(receipt, **{key: value})
            self.assertIsNone(p.post_delta_reference_marker(error, changed))

    def test_optional_marker_write_failure_keeps_original_terminal_marker(self):
        error = RuntimeError(PRIVATE)
        p.attach_post_delta_reference(error, *case())
        ordinary_print = print
        calls = []
        def fail_first(*args, **kwargs):
            calls.append(args[0])
            if len(calls) == 1:
                raise OSError(PRIVATE)
            return ordinary_print(*args, **kwargs)
        with patch('builtins.print', side_effect=fail_first):
            receipt, lines = self.emit(error)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].startswith(r.PREFIX))
        self.assertEqual(lines, [p.DIAGNOSTIC_PREFIX + json.dumps(receipt, sort_keys=True, separators=(',', ':'))])
        for exception in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(exception=exception), patch('builtins.print', side_effect=exception):
                with self.assertRaises(exception):
                    self.emit(error)

    def test_byte_limit_applies_to_json_body_excluding_prefix(self):
        error = RuntimeError(PRIVATE)
        error.package_target_reference_v2 = reference()
        receipt = p.failure_diagnostic(error)
        encoded = json.dumps(reference(), sort_keys=True, separators=(',', ':'))
        with patch.object(p, 'load_package_reference', return_value=r):
            with patch.object(r, 'MAX_REFERENCE_BYTES', len(encoded.encode('ascii'))):
                self.assertEqual(p.post_delta_reference_marker(error, receipt), r.PREFIX + encoded)
            with patch.object(r, 'MAX_REFERENCE_BYTES', len(encoded.encode('ascii')) - 1):
                self.assertIsNone(p.post_delta_reference_marker(error, receipt))

    def test_attribute_is_revalidated_not_blindly_serialized(self):
        for invalid in (dict(reference(), private=PRIVATE), reference(expected={'status': 'absent'}),
                        PRIVATE, None):
            error = RuntimeError(PRIVATE)
            error.package_target_reference_v2 = invalid
            self.assertEqual(len(self.emit(error)[1]), 1)

    def test_missing_or_broken_helper_preserves_original_failure_output(self):
        for failure in (FileNotFoundError(PRIVATE), ValueError(PRIVATE), RuntimeError(PRIVATE)):
            error = RuntimeError(PRIVATE)
            with patch.object(p, 'load_package_reference', side_effect=failure):
                p.attach_post_delta_reference(error, *case())
                receipt, lines = self.emit(error)
            self.assertFalse(hasattr(error, 'package_target_reference_v2'))
            self.assertEqual(len(lines), 1)
            self.assertEqual(receipt['code'], 'APT_INVARIANT_REJECTED')
            self.assertNotIn('PRIVATE', '\n'.join(lines))

    def test_process_control_exceptions_remain_unsuccessful(self):
        for exception in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(exception=exception), patch.object(p, 'load_package_reference', side_effect=exception):
                with self.assertRaises(exception):
                    p.attach_post_delta_reference(RuntimeError(PRIVATE), *case())
                with self.assertRaises(exception):
                    self.emit(RuntimeError(PRIVATE))

    def test_real_delta_catch_rethrows_same_error_and_does_not_continue(self):
        source = (ROOT / 'preparation' / 'prepare_packages.py').read_text()
        tree = ast.parse(source)
        block = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                     and len(node.body) == 1 and isinstance(node.body[0], ast.Expr)
                     and isinstance(node.body[0].value, ast.Call)
                     and isinstance(node.body[0].value.func, ast.Name)
                     and node.body[0].value.func.id == 'validate_inventory_delta')
        for optimize in (0, 1, 2):
            for helper_failure in (False, True):
                before, after, transaction, records = case()
                original = RuntimeError(REJECTION)
                def reject(*args):
                    raise original
                def broken(*args):
                    raise ValueError(PRIVATE)
                namespace = dict(base=before, after=after, solution={'transaction': transaction},
                                 records=records, validate_inventory_delta=reject, phase=p.phase,
                                 post_install_delta_operation=p.post_install_delta_operation,
                                 attach_post_delta_reference=broken if helper_failure else p.attach_post_delta_reference)
                with self.assertRaises(RuntimeError) as caught:
                    exec(compile(ast.Module(body=[block], type_ignores=[]), '<pure-delta-catch>', 'exec',
                                 optimize=optimize), namespace, namespace)
                self.assertIs(caught.exception, original)
                self.assertIs(type(caught.exception), RuntimeError)
                self.assertEqual(str(caught.exception), REJECTION)
                self.assertEqual(hasattr(original, 'package_target_reference_v2'), not helper_failure)

    def test_original_validator_and_v1_functions_are_byte_identical(self):
        source = (ROOT / 'preparation' / 'prepare_packages.py').read_text()
        expected = {
            'canonical_target_reference': 'd805b41794146645efa50ea16dba6673a1f6f8955bc551f8242d90e655dd7f2e',
            'write_target_reference': '8373f89665b7470a63fb65f3c809da9e1b5a00ebfc59de46554508c3eb5429ba',
            'validate_inventory_delta': 'dc758f88dbf69cc86e9cc4773b75f0e48303230e5b246dc0165aa26e64737362',
        }
        actual = {node.name: hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest()
                  for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
                  and node.name in expected}
        self.assertEqual(actual, expected)

    def test_v1_sidecar_hash_marker_and_no_overwrite_are_unchanged(self):
        ref = dict(schema=1, snapshot=p.SNAPSHOT, suite='jammy', component='main', stanza=1)
        error = p.ClosureProvenanceError('APT_CLOSURE_DOWNGRADE_REFUSED', 1, ref)
        p.phase('closure-provenance')
        with tempfile.TemporaryDirectory() as temporary, patch.object(p, 'DIAGNOSTIC_ROOT', Path(temporary)):
            receipt, lines = self.emit(error)
            self.assertEqual(len(lines), 2)
            data = (json.dumps(ref, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
            self.assertEqual((Path(temporary) / 'package-target-reference.json').read_bytes(), data)
            self.assertEqual(lines[0], p.TARGET_REFERENCE_PREFIX + hashlib.sha256(data).hexdigest())
            self.assertEqual(lines[1], p.DIAGNOSTIC_PREFIX + json.dumps(receipt, sort_keys=True, separators=(',', ':')))
            self.assertFalse(any(line.startswith(r.PREFIX) for line in lines))
            self.assertEqual(len(self.emit(error)[1]), 1)
            self.assertEqual((Path(temporary) / 'package-target-reference.json').read_bytes(), data)

    def test_frozen_source_copy_preserves_docker_control_helper_layout(self):
        freezer = load('package_reference_fixture_freezer', ROOT / 'freeze_source_index.py')
        image = load('package_reference_fixture_image', ROOT / 'preparation' / 'prepare_image.py')
        self.assertIn('COPY control/ /opt/urp-control/',
                      (ROOT / 'preparation' / 'Dockerfile').read_text())
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source'
            (source / 'preparation').mkdir(parents=True)
            names = ('package_reference.py', 'preparation/prepare_packages.py')
            for name in names:
                (source / name).write_bytes((ROOT / name).read_bytes())
            digest = freezer.freeze(source)
            indexed = json.loads((source / 'source-index.json').read_text())
            self.assertEqual(set(indexed['files']), set(names))
            target = Path(temporary) / 'opt' / 'urp-control'
            image.anchor_copy(source, target, digest)
            for name in names:
                self.assertEqual((target / name).read_bytes(), (ROOT / name).read_bytes())
            copied = load('package_reference_copied_producer', target / 'preparation' / 'prepare_packages.py')
            helper = copied.load_package_reference()
            self.assertEqual(Path(helper.__file__).resolve(), target / 'package_reference.py')
            self.assertEqual(helper.make_reference(*case()), reference())
            (source / 'package_reference.py').write_text('raise RuntimeError("PRIVATE_TAMPER")')
            with self.assertRaisesRegex(RuntimeError, 'SOURCE_BYTES'):
                image.anchor_copy(source, Path(temporary) / 'bad-control', digest)

    def test_isolated_python_loads_exact_sibling_not_cwd_or_pythonpath(self):
        script = '''import importlib.util, pathlib, sys
path = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('isolated_preparer', path)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
helper = p.load_package_reference()
assert pathlib.Path(helper.__file__).resolve() == path.resolve().parents[1] / 'package_reference.py'
assert helper.PREFIX == 'SAFE_PACKAGE_TARGET_REFERENCE_V2:'
'''
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / 'package_reference.py').write_text('raise RuntimeError("PRIVATE_HIJACK")\n')
            result = subprocess.run([sys.executable, '-I', '-B', '-c', script,
                                     str(ROOT / 'preparation' / 'prepare_packages.py')],
                                    cwd=temporary, env={**os.environ, 'PYTHONPATH': temporary},
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, '')
        tree = ast.parse((ROOT / 'preparation' / 'prepare_packages.py').read_text())
        loader = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == 'load_package_reference')
        self.assertNotIn('sys.path', ast.unparse(loader))


if __name__ == '__main__':
    unittest.main()

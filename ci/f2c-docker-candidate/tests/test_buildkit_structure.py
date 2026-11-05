"""Pure local fixtures for bounded, non-authoritative BuildKit observability."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import buildkit_diagnostic as d


BASE_IMAGE = ('docker.io/library/ubuntu@sha256:'
              '98510ac07bb4545e389be502a8918b385c018bfe4ceeb5ac7e0375c782268b04')
STAGES = ('base_metadata', 'base_pull', 'bootstrap', 'package')
KEYS = {'schema', 'diagnostic_only', 'log_state', 'log_bytes', 'size_capped',
        'buildkit_frames_seen', 'stages_reached', 'failed_stage'}


def header(stage, vertex=7):
    if stage == 'base_metadata':
        step, command = 'internal', 'load metadata for ' + BASE_IMAGE
    elif stage == 'base_pull':
        step, command = ' 1/17', 'FROM ' + BASE_IMAGE
    else:
        step = ' 8/17' if stage == 'bootstrap' else ' 9/17'
        command = 'RUN ' + d.RUN_COMMANDS[stage]
    return '#{} [{}] {}\n'.format(vertex, step, command)


def error(vertex=7, payload='PRIVATE_ERROR_PAYLOAD'):
    return '#{} ERROR: {}\n'.format(vertex, payload)


def receipt(**updates):
    value = {'schema': 1, 'diagnostic_only': True, 'log_state': 'read',
             'log_bytes': 128, 'size_capped': False,
             'buildkit_frames_seen': True, 'stages_reached': ['bootstrap'],
             'failed_stage': 'bootstrap'}
    value.update(updates)
    return value


class BuildkitStructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'PRIVATE_preparation.log'

    def inspect(self, data):
        raw = data.encode('utf-8') if type(data) is str else data
        self.path.write_bytes(raw)
        result = d.inspect_structure(self.path)
        self.assertEqual(set(result), KEYS)
        self.assertEqual(d.validate_structure(result), result)
        return result

    def assert_no_selected_failure(self, result):
        self.assertEqual(result['failed_stage'], 'unknown')
        self.assertTrue(result['diagnostic_only'])

    def test_exact_reviewed_base_image_matches_frozen_dockerfile(self):
        self.assertEqual(d.BASE_IMAGE, BASE_IMAGE)
        lines = (ROOT / 'preparation/Dockerfile').read_text().splitlines()
        self.assertEqual([line for line in lines if line.startswith('FROM ')],
                         ['FROM --platform=linux/amd64 ' + BASE_IMAGE])

    def test_empty_and_plain_docker_client_text_are_distinct_from_frames(self):
        empty = self.inspect(b'')
        self.assertEqual(empty, receipt(log_state='empty', log_bytes=0,
                                       buildkit_frames_seen=False,
                                       stages_reached=[], failed_stage='unknown'))
        text = 'Cannot connect to the Docker daemon at PRIVATE_SOCKET.\n'
        plain = self.inspect(text)
        self.assertEqual(plain['log_state'], 'read')
        self.assertEqual(plain['log_bytes'], len(text.encode('utf-8')))
        self.assertFalse(plain['buildkit_frames_seen'])
        self.assertEqual(plain['stages_reached'], [])
        self.assert_no_selected_failure(plain)

    def test_unrecognized_top_level_header_is_a_frame_not_a_known_stage(self):
        result = self.inspect('#2 [internal] load build definition from Dockerfile\n' + error(2))
        self.assertTrue(result['buildkit_frames_seen'])
        self.assertEqual(result['stages_reached'], [])
        self.assert_no_selected_failure(result)

    def test_each_exact_header_can_bind_a_top_level_error(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                raw = header(stage) + error()
                result = self.inspect(raw)
                self.assertEqual(result['log_state'], 'read')
                self.assertEqual(result['log_bytes'], len(raw.encode('utf-8')))
                self.assertFalse(result['size_capped'])
                self.assertTrue(result['buildkit_frames_seen'])
                self.assertEqual(result['stages_reached'], [stage])
                self.assertEqual(result['failed_stage'], stage)

    def test_stages_are_canonical_unique_not_log_execution_order(self):
        raw = ''.join(header(stage, vertex) for vertex, stage in
                      enumerate(reversed(STAGES), 1))
        result = self.inspect(raw)
        self.assertEqual(result['stages_reached'], list(STAGES))
        self.assert_no_selected_failure(result)
        repeated = self.inspect(raw + header('base_metadata', 20))
        self.assertEqual(repeated['stages_reached'], list(STAGES))

    def test_header_without_error_is_not_a_failure_or_acceptance(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                result = self.inspect(header(stage) + '#7 DONE 0.1s\n')
                self.assertEqual(result['stages_reached'], [stage])
                self.assert_no_selected_failure(result)
                self.assertNotIn('accepted', json.dumps(result))
                self.assertNotIn('passed', json.dumps(result))

    def test_wrong_image_and_modified_run_commands_cannot_bind_a_stage(self):
        wrong_images = [BASE_IMAGE[:-1] + '5', 'ubuntu:22.04',
                        BASE_IMAGE.replace('library/ubuntu', 'PRIVATE/ubuntu'),
                        BASE_IMAGE + ' PRIVATE_SUFFIX']
        for stage in ('base_metadata', 'base_pull'):
            for image in wrong_images:
                with self.subTest(stage=stage, image=image):
                    result = self.inspect(header(stage).replace(BASE_IMAGE, image) + error())
                    self.assertTrue(result['buildkit_frames_seen'])
                    self.assertEqual(result['stages_reached'], [])
                    self.assert_no_selected_failure(result)
        for stage in ('bootstrap', 'package'):
            for suffix in (' && echo PRIVATE', ' # PRIVATE'):
                result = self.inspect(header(stage).rstrip('\n') + suffix + '\n' + error())
                self.assertEqual(result['stages_reached'], [])
                self.assert_no_selected_failure(result)

    def test_nonanonymous_and_invalid_numbered_run_headers_are_not_stages(self):
        for step in ('internal', 'build 8/17', '0/17', '18/17', '08/17', '8/017', '8/9999'):
            with self.subTest(step=step):
                result = self.inspect(header('bootstrap').replace(' 8/17', step) + error())
                self.assertEqual(result['stages_reached'], [])
                self.assert_no_selected_failure(result)

    def test_nested_headers_and_summary_replay_cannot_establish_static_stages(self):
        for stage in STAGES:
            valid = header(stage)
            fixtures = ['#88 0.123 ' + valid + error(),
                        'PRIVATE ' + valid + error(),
                        '\x1b[0m' + valid + error(),
                        '------\n > ' + valid.split(' ', 1)[1].rstrip('\n') + ':\n' + error()]
            for raw in fixtures:
                with self.subTest(stage=stage, raw=raw):
                    result = self.inspect(raw)
                    self.assertEqual(result['stages_reached'], [])
                    self.assert_no_selected_failure(result)

    def test_nested_error_output_cannot_select_a_failure(self):
        fixtures = ['#7 0.123 ' + error(), 'PRIVATE ' + error(),
                    ' #7 ERROR: PRIVATE\n', '#7 0.123 ERROR: PRIVATE\n',
                    '\x1b[31m' + error(), '#7 ERROR_PRIVATE: payload\n']
        for line in fixtures:
            with self.subTest(line=line):
                result = self.inspect(header('bootstrap') + line)
                self.assertEqual(result['stages_reached'], ['bootstrap'])
                self.assert_no_selected_failure(result)

    def test_error_before_header_wrong_vertex_and_unbound_error_stay_unknown(self):
        fixtures = [error() + header('bootstrap'),
                    header('bootstrap') + error(8),
                    error(),
                    header('bootstrap') + '#8 [internal] PRIVATE_COMMAND\n' + error(8)]
        for raw in fixtures:
            with self.subTest(raw=raw):
                self.assert_no_selected_failure(self.inspect(raw))

    def test_error_payload_is_never_classified_or_republished(self):
        payloads = ['PRIVATE_TOKEN https://PRIVATE.invalid/PRIVATE /PRIVATE/path',
                    header('package', 99).rstrip('\n'),
                    d.BOOTSTRAP_PREFIX + 'BLOCKED_signed_metadata',
                    'load metadata for ' + BASE_IMAGE + ' failed',
                    'PRIVATE\x00\xff\r\t',
                    '{"failed_stage":"package","raw":"PRIVATE"}']
        for payload in payloads:
            with self.subTest(payload=payload):
                result = self.inspect(header('base_metadata') + error(payload=payload))
                self.assertEqual(result['failed_stage'], 'base_metadata')
                self.assertEqual(result['stages_reached'], ['base_metadata'])
                encoded = json.dumps(result)
                for private in ('PRIVATE', 'http', 'sha256', 'BLOCKED_', 'raw'):
                    self.assertNotIn(private, encoded)

    def test_duplicate_or_rebound_headers_make_failure_unknown(self):
        known = header('bootstrap')
        unknown = '#7 [internal] PRIVATE_COMMAND\n'
        for raw in (known + known + error(), known + error() + known,
                    known + error() + '#9 [internal] PRIVATE\n#9 [internal] PRIVATE\n'):
            self.assertEqual(self.inspect(raw)['failed_stage'], 'bootstrap')
        fixtures = [known + unknown + error(),
                    unknown + known + error(),
                    known + header('package') + error()]
        for raw in fixtures:
            with self.subTest(raw=raw):
                result = self.inspect(raw)
                self.assertTrue(result['buildkit_frames_seen'])
                self.assertIn('bootstrap', result['stages_reached'])
                self.assert_no_selected_failure(result)

    def test_multiple_errors_including_duplicate_and_unknown_are_ambiguous(self):
        fixtures = [header('bootstrap') + error() + error(),
                    header('bootstrap') + error() + error(99),
                    error(99) + header('bootstrap') + error(),
                    header('bootstrap') + error() + header('package', 8) + error(8)]
        for raw in fixtures:
            with self.subTest(raw=raw):
                self.assert_no_selected_failure(self.inspect(raw))

    def test_unterminated_final_fragments_cannot_establish_header_or_error(self):
        self.assertEqual(self.inspect(header('bootstrap').rstrip('\n'))['stages_reached'], [])
        result = self.inspect(header('bootstrap') + error().rstrip('\n'))
        self.assertEqual(result['stages_reached'], ['bootstrap'])
        self.assert_no_selected_failure(result)

    def test_missing_unreadable_and_unsupported_files_are_closed_receipts(self):
        missing = d.inspect_structure(self.path)
        self.assertEqual(missing, receipt(log_state='missing', log_bytes=None,
                                         buildkit_frames_seen=False,
                                         stages_reached=[], failed_stage='unknown'))
        self.path.write_text(header('bootstrap') + error())
        with patch.object(d.os, 'open', side_effect=PermissionError('PRIVATE_OS_ERROR')):
            unreadable = d.inspect_structure(self.path)
        self.assertEqual(unreadable['log_state'], 'unreadable')
        self.assertNotIn('PRIVATE', json.dumps(unreadable))
        alias = self.path.with_name('PRIVATE_alias')
        alias.symlink_to(self.path)
        fifo = self.path.with_name('PRIVATE_fifo')
        os.mkfifo(fifo)
        for path in (self.path.parent, alias, fifo):
            with self.subTest(path=path):
                with patch.object(d.os, 'open') as opened:
                    result = d.inspect_structure(path)
                opened.assert_not_called()
                self.assertEqual(result['log_state'], 'unsupported_file')
                self.assertEqual(result['stages_reached'], [])
                self.assert_no_selected_failure(result)

    def test_oversized_file_is_not_opened_and_reports_only_capped_size(self):
        with self.path.open('wb') as stream:
            stream.truncate(d.MAX_LOG_BYTES * 100)
        with patch.object(d.os, 'open') as opened:
            result = d.inspect_structure(self.path)
        opened.assert_not_called()
        self.assertEqual(result, receipt(log_state='oversized',
                                        log_bytes=d.MAX_LOG_BYTES + 1,
                                        size_capped=True,
                                        buildkit_frames_seen=False,
                                        stages_reached=[], failed_stage='unknown'))
        self.assertEqual(d.validate_structure(result), result)

    def test_exact_file_byte_limit_is_allowed_and_next_byte_is_capped(self):
        with patch.object(d, 'MAX_LOG_BYTES', 4096):
            result = self.inspect(b'x\n' * 2048)
            self.assertEqual(result['log_state'], 'read')
            self.assertEqual(result['log_bytes'], 4096)
            self.assertFalse(result['size_capped'])
            result = self.inspect(b'x\n' * 2048 + b'x')
            self.assertEqual(result['log_state'], 'oversized')
            self.assertEqual(result['log_bytes'], 4097)
            self.assertTrue(result['size_capped'])

    def test_overlong_line_and_excess_vertices_fail_closed(self):
        result = self.inspect(header('bootstrap') + 'x' * (d.MAX_LINE_BYTES + 1) + '\n')
        self.assertEqual(result['log_state'], 'invalid')
        self.assertEqual(result['stages_reached'], [])
        self.assert_no_selected_failure(result)
        with patch.object(d, 'MAX_VERTICES', 2):
            result = self.inspect(''.join('#{} [internal] PRIVATE\n'.format(n) for n in (1, 2, 3)))
            self.assertEqual(result['log_state'], 'invalid')
            self.assert_no_selected_failure(result)

    def test_file_mutation_during_read_loses_all_stage_evidence(self):
        self.path.write_text(header('bootstrap') + error())
        original = d._inspect_lines
        def changed(stream, size):
            result = original(stream, size)
            with self.path.open('ab') as destination:
                destination.write(b'PRIVATE_CHANGED\n')
            return result
        with patch.object(d, '_inspect_lines', changed):
            result = d.inspect_structure(self.path)
        self.assertEqual(result['log_state'], 'changed')
        self.assertEqual(result['stages_reached'], [])
        self.assert_no_selected_failure(result)
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_file_replacement_before_open_and_after_read_is_rejected(self):
        for timing in ('before_open', 'after_read'):
            with self.subTest(timing=timing):
                self.path.write_text(header('bootstrap') + error())
                replacement = self.path.with_name('replacement')
                replacement.write_text(header('package') + error())
                if timing == 'before_open':
                    real_open = os.open
                    def replace_open(path, flags):
                        replacement.replace(self.path)
                        return real_open(path, flags)
                    hook = patch.object(d.os, 'open', replace_open)
                else:
                    original = d._inspect_lines
                    def replace_after_read(stream, size):
                        result = original(stream, size)
                        replacement.replace(self.path)
                        return result
                    hook = patch.object(d, '_inspect_lines', replace_after_read)
                with hook:
                    result = d.inspect_structure(self.path)
                self.assertEqual(result['log_state'], 'changed')
                self.assertEqual(result['stages_reached'], [])
                self.assert_no_selected_failure(result)

    def test_existing_safe_marker_projection_remains_unchanged(self):
        raw = (header('bootstrap') + '#7 0.123 ' + d.BOOTSTRAP_PREFIX +
               'BLOCKED_signed_metadata\n' + error())
        structure = self.inspect(raw)
        self.assertEqual(structure['failed_stage'], 'bootstrap')
        marker = d.parse_log(self.path)
        self.assertEqual(marker, {'schema': 1, 'source': 'bootstrap',
                                  'diagnostic_only': True,
                                  'detail': {'code': 'BLOCKED_signed_metadata'}})
        self.assertEqual(set(marker), {'schema', 'source', 'diagnostic_only', 'detail'})


class StructureValidationTests(unittest.TestCase):
    def test_projection_is_closed_and_copies_mutable_list(self):
        value = receipt()
        result = d.validate_structure(value)
        self.assertEqual(result, value)
        self.assertIsNot(result, value)
        self.assertIsNot(result['stages_reached'], value['stages_reached'])
        value['stages_reached'].append('package')
        self.assertEqual(result['stages_reached'], ['bootstrap'])

    def test_missing_extra_and_nondictionary_values_are_rejected(self):
        valid = receipt()
        invalid = [None, [], True, 'PRIVATE', {}, dict(valid, raw='PRIVATE')]
        invalid.extend({key: value for key, value in valid.items() if key != omitted}
                       for omitted in KEYS)
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(d.validate_structure(value))

    def test_field_types_bounds_and_finite_domains_are_strict(self):
        invalid = {
            'schema': [True, False, 1.0, '1', 0, 2, None],
            'diagnostic_only': [False, 1, 0, 'true', None],
            'log_state': ['PRIVATE', '', [], {}, None, True],
            'log_bytes': [True, False, -1, d.MAX_LOG_BYTES + 2, 1.0, '1', []],
            'size_capped': [1, 0, 'false', None, []],
            'buildkit_frames_seen': [1, 0, 'true', None, []],
            'stages_reached': [None, (), 'bootstrap', {}, ['PRIVATE'], [None],
                               [True], [[]], ['bootstrap', 'bootstrap'],
                               ['package', 'base_metadata']],
            'failed_stage': ['PRIVATE', '', [], {}, None, True],
        }
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertIsNone(d.validate_structure(receipt(**{key: value})))

    def test_cross_field_contradictions_are_rejected(self):
        invalid = [receipt(size_capped=True),
                   receipt(log_bytes=d.MAX_LOG_BYTES + 1, size_capped=False),
                   receipt(log_bytes=None, size_capped=True),
                   receipt(stages_reached=[]),
                   receipt(failed_stage='package'),
                   receipt(buildkit_frames_seen=False),
                   receipt(buildkit_frames_seen=False, failed_stage='unknown')]
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(d.validate_structure(value))

    def test_all_states_and_size_boundaries_round_trip(self):
        cases = [('read', 1), ('read', d.MAX_LOG_BYTES), ('empty', 0),
                 ('missing', None), ('unreadable', None),
                 ('unsupported_file', None), ('oversized', d.MAX_LOG_BYTES + 1),
                 ('invalid', 0), ('invalid', d.MAX_LOG_BYTES), ('changed', None)]
        for state, size in cases:
            value = receipt(log_state=state, log_bytes=size,
                            size_capped=(size == d.MAX_LOG_BYTES + 1),
                            buildkit_frames_seen=False,
                            stages_reached=[], failed_stage='unknown')
            with self.subTest(state=state, size=size):
                self.assertEqual(d.validate_structure(value), value)

    def test_state_size_incoherence_and_nonread_evidence_are_rejected(self):
        cases = [('read', None), ('read', 0), ('read', d.MAX_LOG_BYTES + 1),
                 ('empty', None), ('empty', 1), ('oversized', None),
                 ('oversized', d.MAX_LOG_BYTES), ('invalid', None),
                 ('invalid', d.MAX_LOG_BYTES + 1), ('missing', 0),
                 ('unreadable', 1), ('unsupported_file', 0), ('changed', 1)]
        for state, size in cases:
            value = receipt(log_state=state, log_bytes=size,
                            size_capped=(size == d.MAX_LOG_BYTES + 1),
                            buildkit_frames_seen=False,
                            stages_reached=[], failed_stage='unknown')
            with self.subTest(state=state, size=size):
                self.assertIsNone(d.validate_structure(value))
        for state, size in [('empty', 0), ('oversized', d.MAX_LOG_BYTES + 1),
                            ('invalid', 10), ('missing', None),
                            ('unreadable', None), ('unsupported_file', None),
                            ('changed', None)]:
            value = receipt(log_state=state, log_bytes=size,
                            size_capped=(size == d.MAX_LOG_BYTES + 1))
            with self.subTest(state=state, size=size):
                self.assertIsNone(d.validate_structure(value))
                self.assertIsNone(d.validate_structure(dict(value,
                                  stages_reached=[], failed_stage='unknown')))


if __name__ == '__main__':
    unittest.main()

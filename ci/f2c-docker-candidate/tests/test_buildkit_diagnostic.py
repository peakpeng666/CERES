import ast
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import buildkit_diagnostic as d


def header(source='bootstrap', vertex=14, command=None, step=' 7/18'):
    command = 'RUN ' + d.RUN_COMMANDS[source] if command is None else command
    return '#{} [{}] {}\n'.format(vertex, step, command)


def output(payload, vertex=14, elapsed='0.123'):
    return '#{} {} {}\n'.format(vertex, elapsed, payload)


def bootstrap(code='BLOCKED_signed_metadata', vertex=14):
    return output(d.BOOTSTRAP_PREFIX + code, vertex)


def package_receipt(**updates):
    result = {'schema': 1, 'code': 'APT_HTTP_ERROR', 'operation': 'release-download',
              'exception_class': 'HTTPError', 'http_status': 404, 'errno': None,
              'returncode': None, 'suite': 'jammy', 'component': None}
    result.update(updates)
    return result


def package(value=None, vertex=15):
    return output(d.PACKAGE_PREFIX + json.dumps(package_receipt() if value is None else value,
                                              separators=(',', ':'), sort_keys=True), vertex)


class BuildkitDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'preparation.log'

    def parse(self, data):
        self.path.write_bytes(data.encode('utf-8') if type(data) is str else data)
        return d.parse_log(self.path)

    def test_each_finite_bootstrap_failure_projects(self):
        for code in d.BOOTSTRAP_FAILURES:
            with self.subTest(code=code):
                result = self.parse(header() + bootstrap(code))
                self.assertEqual(result, {'schema': 1, 'source': 'bootstrap',
                                         'diagnostic_only': True, 'detail': {'code': code}})

    def test_package_exact_schema_projects(self):
        value = package_receipt()
        self.assertEqual(self.parse(header('package', 15) + package(value)),
                         {'schema': 1, 'source': 'package', 'diagnostic_only': True,
                          'detail': value})

    def test_success_then_package_failure_is_normal(self):
        result = self.parse(header() + bootstrap(d.BOOTSTRAP_SUCCESS) +
                            header('package', 15) + package())
        self.assertEqual(result['source'], 'package')
        self.assertEqual(result['detail'], package_receipt())

    def test_bootstrap_success_is_never_failure_or_acceptance(self):
        self.assertIsNone(self.parse(header() + bootstrap(d.BOOTSTRAP_SUCCESS)))
        self.assertIsNone(d.validate_projection({'schema': 1, 'source': 'bootstrap',
                                                'diagnostic_only': True,
                                                'detail': {'code': d.BOOTSTRAP_SUCCESS}}))

    def test_normalized_exact_command_accepts_ascii_spaces_only(self):
        command = 'RUN    ' + d.RUN_COMMANDS['bootstrap'].replace(' ', '  ')
        self.assertIsNotNone(self.parse(header(command=command) + bootstrap()))
        self.assertIsNone(self.parse(header(command=command.replace(' ', '\t')) + bootstrap()))

    def test_summary_replay_does_not_duplicate_or_establish_source(self):
        replay = '------\n > [ 7/18] RUN ' + d.RUN_COMMANDS['bootstrap'] + ':\n'
        replay += '0.123 ' + d.BOOTSTRAP_PREFIX + 'BLOCKED_signed_metadata\n------\n'
        self.assertIsNotNone(self.parse(header() + bootstrap() + replay))
        self.assertIsNone(self.parse(replay))
        self.assertIsNone(self.parse(replay + bootstrap()))

    def test_only_failure_marker_without_source_is_ignored(self):
        for raw in (bootstrap(), d.BOOTSTRAP_PREFIX + 'BLOCKED_signed_metadata\n', package()):
            with self.subTest(raw=raw):
                self.assertIsNone(self.parse(raw))

    def test_wrong_vertex_and_marker_before_header_are_ignored(self):
        self.assertIsNone(self.parse(header(vertex=14) + bootstrap(vertex=15)))
        self.assertIsNone(self.parse(bootstrap() + header()))
        self.assertIsNone(self.parse(header('package', 15) + package(vertex=16)))

    def test_fake_source_and_unreviewed_command_never_match(self):
        expected = 'RUN ' + d.RUN_COMMANDS['bootstrap']
        commands = [expected + ' && echo PRIVATE', expected + ' # PRIVATE',
                    expected.replace('/bin/sh ', '/bin/bash '),
                    expected.replace('--install-bootstrap', '--internal-clean-bootstrap'),
                    'RUN echo ' + expected, expected.replace('/usr/bin/env', 'env'),
                    expected.replace('HOME=/nonexistent', 'HOME=/PRIVATE')]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(self.parse(header(command=command) + bootstrap()))
        for step in ('internal', 'fake 7/18', '0/18', '19/18', '7/9999', '07/18', '7/018'):
            self.assertIsNone(self.parse(header(step=step) + bootstrap()))

    def test_header_injection_nested_in_vertex_output_is_not_a_header(self):
        nested = output(header().rstrip('\n'), vertex=88)
        self.assertIsNone(self.parse(nested + bootstrap()))
        self.assertIsNone(self.parse(output(header().rstrip('\n'), vertex=14) + bootstrap()))
        self.assertIsNone(self.parse('PRIVATE ' + header() + bootstrap()))
        self.assertIsNone(self.parse('\x1b[0m' + header() + bootstrap()))

    def test_injected_marker_nested_in_output_is_not_a_marker(self):
        nested = output(bootstrap().rstrip('\n'))
        self.assertIsNone(self.parse(header() + nested))
        self.assertIsNone(self.parse(header() + output('PRIVATE ' + d.BOOTSTRAP_PREFIX + 'BLOCKED_signed_metadata')))

    def test_duplicate_and_rebound_headers_are_ambiguous(self):
        for data in (header() + header() + bootstrap(), header() + bootstrap() + header()):
            self.assertEqual(self.parse(data)['source'], 'bootstrap')
        fixtures = [header() + header(vertex=16) + bootstrap(),
                    header(command='RUN echo PRIVATE') + header() + bootstrap(),
                    header() + header(command='RUN echo PRIVATE') + bootstrap(),
                    header() + header('package', 14) + package(vertex=14)]
        for data in fixtures:
            self.assertIsNone(self.parse(data))

    def test_duplicate_and_conflicting_markers_are_rejected(self):
        fixtures = [header() + bootstrap() + bootstrap(),
                    header('package', 15) + package() + package(),
                    header() + bootstrap() + header('package', 15) + package()]
        for data in fixtures:
            self.assertIsNone(self.parse(data))

    def test_success_markers_do_not_participate_in_failure_selection(self):
        data = header() + bootstrap(d.BOOTSTRAP_SUCCESS) + bootstrap(d.BOOTSTRAP_SUCCESS)
        self.assertIsNone(self.parse(data))
        self.assertEqual(self.parse(data + header('package', 15) + package())['source'], 'package')

    def test_cross_source_marker_is_rejected(self):
        self.assertIsNone(self.parse(header() + package(vertex=14)))
        self.assertIsNone(self.parse(header('package', 15) + bootstrap(vertex=15)))

    def test_malformed_elapsed_and_vertex_prefixes_do_not_match(self):
        for elapsed in ('NaN', 'inf', '-0.1', '+0.1', '1e3', '1', '0.1234', '1000000.0'):
            self.assertIsNone(self.parse(header() + output(d.BOOTSTRAP_PREFIX + 'BLOCKED_signed_metadata', elapsed=elapsed)))
        for lead in ('#0', '#01', '#-1', '#1000000', ' #14', '#14\t'):
            self.assertIsNone(self.parse(header() + bootstrap().replace('#14', lead)))

    def test_non_ascii_controls_and_truncated_markers_are_rejected(self):
        valid = header() + bootstrap()
        for raw in (valid.replace('signed_metadata', 'signed_métadata'),
                    valid.replace('BLOCKED_', 'BLOCKED_\x00'),
                    valid[:-1], valid.replace('metadata\n', 'metadata\r\n'),
                    valid.replace('SAFE_BOOTSTRAP', '\x1b[31mSAFE_BOOTSTRAP')):
            self.assertIsNone(self.parse(raw))
        self.assertIsNone(self.parse(header().replace('RUN ', 'RUN\u00a0') + bootstrap()))
        self.assertIsNone(self.parse(header('package', 15) + package(package_receipt(code='APT_HTTP_ERRÖR'))))

    def test_finite_enums_and_numeric_bounds(self):
        for key, values in [('code', ['PRIVATE', [], None, 1]),
                            ('operation', ['PRIVATE', [], None, 1]),
                            ('exception_class', ['PRIVATE', [], None, 1]),
                            ('suite', ['PRIVATE', [], 1]), ('component', ['PRIVATE', [], 1]),
                            ('schema', [True, 1.0, '1', 2]),
                            ('http_status', [True, 99, 600, 404.0, '404']),
                            ('returncode', [False, -256, 256, 1.0, '1']),
                            ('errno', [True, 0, 999, 2.0, '2'])]:
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertIsNone(self.parse(header('package', 15) + package(package_receipt(**{key: value}))))
        for value in (package_receipt(http_status=100, returncode=-255, errno=-3),
                      package_receipt(http_status=599, returncode=255, errno=113),
                      package_receipt(http_status=None, returncode=None, errno=None)):
            self.assertIsNotNone(self.parse(header('package', 15) + package(value)))

    def test_package_unknown_missing_and_duplicate_keys(self):
        value = package_receipt()
        bad = [dict(value, raw='PRIVATE_PATH https://PRIVATE/PRIVATE'),
               {key: item for key, item in value.items() if key != 'errno'},
               [], None, 'PRIVATE', True]
        for row in bad:
            raw = output(d.PACKAGE_PREFIX + json.dumps(row), vertex=15)
            self.assertIsNone(self.parse(header('package', 15) + raw))
        raw = json.dumps(value).replace('"schema": 1', '"schema": 1, "schema": 1')
        self.assertIsNone(self.parse(header('package', 15) + output(d.PACKAGE_PREFIX + raw, vertex=15)))

    def test_malformed_and_deep_json_is_rejected(self):
        for body in ('{', '{} trailing', 'NaN', 'Infinity', '[' * 1000 + ']' * 1000,
                     '{"schema":1,"code":null}', '"\\ud800"'):
            self.assertIsNone(self.parse(header('package', 15) + output(d.PACKAGE_PREFIX + body, vertex=15)))

    def test_marker_line_and_file_bounds(self):
        self.assertIsNone(self.parse(header() + bootstrap('X' * d.MAX_MARKER_BYTES)))
        self.assertIsNone(self.parse('x' * (d.MAX_LINE_BYTES + 1) + '\n' + header() + bootstrap()))
        self.path.write_bytes(b'x' * (d.MAX_LOG_BYTES + 1))
        self.assertIsNone(d.parse_log(self.path))
        with patch.object(d, 'MAX_VERTICES', 2):
            data = ''.join(header(vertex=n, command='RUN echo PRIVATE', step='1/2') for n in (1, 2, 3))
            self.assertIsNone(self.parse(data + bootstrap(vertex=3)))

    def test_only_existing_regular_file_no_final_symlink(self):
        self.assertIsNone(d.parse_log(self.path))
        self.assertIsNone(d.parse_log(self.path.parent))
        self.path.write_text(header() + bootstrap())
        alias = self.path.with_name('alias')
        alias.symlink_to(self.path)
        self.assertIsNone(d.parse_log(alias))
        fifo = self.path.with_name('fifo')
        os.mkfifo(fifo)
        self.assertIsNone(d.parse_log(fifo))

    def test_changed_file_is_rejected(self):
        self.path.write_text(header() + bootstrap())
        original = d._parse_lines
        def changed(stream):
            result = original(stream)
            with self.path.open('ab') as output_file:
                output_file.write(b'changed\n')
            return result
        with patch.object(d, '_parse_lines', changed):
            self.assertIsNone(d.parse_log(self.path))

    def test_replaced_inode_is_rejected(self):
        self.path.write_text(header() + bootstrap())
        replacement = self.path.with_name('replacement')
        replacement.write_text(header() + bootstrap())
        real_open = os.open
        def replace_open(path, flags):
            replacement.replace(self.path)
            return real_open(path, flags)
        with patch.object(d.os, 'open', replace_open):
            self.assertIsNone(d.parse_log(self.path))

    def test_projection_revalidation_closed_and_fresh(self):
        valid = self.parse(header('package', 15) + package())
        clone = d.validate_projection(valid)
        self.assertEqual(clone, valid)
        self.assertIsNot(clone, valid)
        self.assertIsNot(clone['detail'], valid['detail'])
        invalid = [None, [], {}, dict(valid, extra='PRIVATE'), dict(valid, schema=True),
                   dict(valid, source='PRIVATE'), dict(valid, source=[]),
                   dict(valid, diagnostic_only=False), dict(valid, diagnostic_only=1),
                   dict(valid, detail=[]), dict(valid, detail=dict(valid['detail'], raw='PRIVATE'))]
        for row in invalid:
            self.assertIsNone(d.validate_projection(row))
        boot = self.parse(header() + bootstrap())
        self.assertIsNone(d.validate_projection(dict(boot, detail={'code': []})))
        self.assertIsNone(d.validate_projection(dict(boot, detail={'code': 'PRIVATE'})))

    def test_unrecognized_private_log_data_is_not_republished(self):
        private = 'PRIVATE_PATH PRIVATE_COMMAND https://private.invalid/ Authorization: PRIVATE\n'
        result = self.parse(private + header() + output('PRIVATE_RESPONSE') + bootstrap() + private)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('http', json.dumps(result))
        self.assertEqual(set(result), {'schema', 'source', 'diagnostic_only', 'detail'})
        self.assertIsNone(self.parse(private))


class SourceParityTests(unittest.TestCase):
    def test_reviewed_commands_match_dockerfile_exact_normalization(self):
        text = (ROOT / 'preparation/Dockerfile').read_text()
        commands = [' '.join(line[4:].split()) for line in text.replace('\\\n', '').splitlines()
                    if line.startswith('RUN ')]
        for source, expected in d.RUN_COMMANDS.items():
            with self.subTest(source=source):
                self.assertEqual(commands.count(expected), 1)

    def test_bootstrap_codes_match_reviewed_emitter_without_running_it(self):
        text = (ROOT / 'preparation/bootstrap/bootstrap.sh').read_text()
        phases = set(re.findall(r'^\s*phase=([a-z_]+)$', text, re.MULTILINE))
        self.assertEqual(phases, d.BOOTSTRAP_PHASES)
        self.assertIn("printf 'SAFE_BOOTSTRAP_DIAGNOSTIC:BLOCKED_%s__%s\\n' \"$phase\" \"$substep\"", text)
        self.assertIn("'SAFE_BOOTSTRAP_DIAGNOSTIC:EXPLICIT_CONTAINER_INSTALL_MODE_REQUIRED'", text)
        self.assertIn("'SAFE_BOOTSTRAP_DIAGNOSTIC:" + d.BOOTSTRAP_SUCCESS + "'", text)

    def test_package_enums_match_static_ast_without_importing_executable(self):
        tree = ast.parse((ROOT / 'preparation/prepare_packages.py').read_text())
        assignments = {node.targets[0].id: node.value for node in tree.body
                       if isinstance(node, ast.Assign) and len(node.targets) == 1
                       and isinstance(node.targets[0], ast.Name)}
        def literal(node):
            if isinstance(node, ast.Name):
                return literal(assignments[node.id])
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                return literal(node.left) | literal(node.right)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ('frozenset', 'set') and len(node.args) == 1 and not node.keywords):
                return frozenset(literal(node.args[0]))
            return ast.literal_eval(node)
        for source, local in [('DIAGNOSTIC_CODES', d.PACKAGE_CODES),
                              ('DIAGNOSTIC_OPERATIONS', d.PACKAGE_OPERATIONS),
                              ('DIAGNOSTIC_CLASSES', d.PACKAGE_CLASSES),
                              ('DIAGNOSTIC_ERRNOS', d.PACKAGE_ERRNOS)]:
            self.assertEqual(literal(assignments[source]), local)
        self.assertEqual(set(literal(assignments['RELEASE_HASHES'])), d.PACKAGE_SUITES)
        self.assertEqual(literal(assignments['DIAGNOSTIC_PREFIX']), d.PACKAGE_PREFIX)
        # The exact receipt keys also remain in the reviewed emitter return.
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'failure_diagnostic')
        returned = [node.value for node in function.body if isinstance(node, ast.Return)][-1]
        self.assertEqual({ast.literal_eval(key) for key in returned.keys}, d.PACKAGE_KEYS)


if __name__ == '__main__':
    unittest.main()

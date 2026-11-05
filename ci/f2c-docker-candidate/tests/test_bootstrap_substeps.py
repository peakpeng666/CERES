"""Pure v9/v10/v11 contracts; never execute the provisioning shell.

The pinned digest is the reviewed v8 bootstrap's exact bytes. Removing substep
assignments and reversing three explicit replacements must recover it, without
needing a sibling v8 checkout.
The v10 block and two helper-list additions first normalize to exact v9 bytes.
The prerequisite helper itself is exercised in its dedicated mocked test file.
"""
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import buildkit_diagnostic as d

BOOTSTRAP = ROOT / 'preparation' / 'bootstrap' / 'bootstrap.sh'
V8_SHA256 = '20f05e01919aef231194b97cdd274fe0994ec7e914b105531a6198aa190d4bb1'
V9_SHA256 = 'e41b8aaa97831d32f67bfeefd52b8d02adaa6791c2518cca2095ba03b8c806e4'
V10_SHA256 = '38651a731c2c9c1c6a6f965fba6d5b77f75690d63683940066dcc2ce5864c3d3'
ASSIGNMENT = re.compile(r'^[ \t]*(phase|substep)=([a-z][a-z0-9_]*)$')
SUBSTEP_LINE = re.compile(rb'^[ \t]*substep=[a-z][a-z0-9_]*\n', re.MULTILINE)
PREREQUISITE_PREFIX = 'run python-apt-prerequisites /usr/bin/python3 -I -B -c '
V8_PREREQUISITE = (
    'import sys, apt_pkg, ssl; assert sys.version_info[:2] == (3, 10); '
    'assert apt_pkg.VERSION.startswith("2.4."); '
    'assert ssl.get_default_verify_paths().cafile'
)
V9_PREREQUISITE = (
    'import sys, apt_pkg, ssl; sys.exit(0 if sys.version_info[:2] == (3, 10) '
    'and apt_pkg.VERSION.startswith("2.4.") '
    'and ssl.get_default_verify_paths().cafile else 1)'
)
V8_PRIVATE = (
    r'''            printf '{"schema":1,"status":"BLOCKED","phase":"%s","full_closure_verified":false}\n' "$phase" > "$OUT/failure.json"'''
)
V9_PRIVATE = (
    r'''            printf '{"schema":1,"status":"BLOCKED","phase":"%s","substep":"%s","full_closure_verified":false}\n' "$phase" "$substep" > "$OUT/failure.json"'''
)
V8_PUBLIC = r'''        printf 'SAFE_BOOTSTRAP_DIAGNOSTIC:BLOCKED_%s\n' "$phase" >&2'''
V9_PUBLIC = r'''        printf 'SAFE_BOOTSTRAP_DIAGNOSTIC:BLOCKED_%s__%s\n' "$phase" "$substep" >&2'''
V9_HELPER_LOOP = ('for helper in parse_release.awk parse_packages.awk parse_simulation.awk '
                  'parse_uris.awk parse_status.awk verify_delta.awk parse_ca_members.awk '
                  'verify_ca_pem.awk; do')
V10_HELPER_LOOP = V9_HELPER_LOOP.replace('; do', ' check_python_prerequisites.py; do')
V11_HELPER_LOOP = V10_HELPER_LOOP.replace('; do', ' provision_system_ca.py; do')
SYSTEM_CA_SUBSTEP_CODES = {
    71: 'system_ca_layout', 72: 'system_ca_bundle', 73: 'system_ca_usable',
    74: 'system_ca_default_path', 75: 'system_ca_alias_conflict',
    76: 'system_ca_alias_write', 77: 'system_ca_record', 78: 'system_ca_execution',
}
V11_SYSTEM_CA_BLOCK = r'''substep=system_ca_defaults
system_ca_result=0
/usr/bin/python3 -I -B "$HELPERS/provision_system_ca.py" --install-default-alias > "$OUT/logs/system-ca-default.log" 2>&1 || system_ca_result=$?
case "$system_ca_result" in
    0) ;;
    71)
        substep=system_ca_layout
        fail
        ;;
    72)
        substep=system_ca_bundle
        fail
        ;;
    73)
        substep=system_ca_usable
        fail
        ;;
    74)
        substep=system_ca_default_path
        fail
        ;;
    75)
        substep=system_ca_alias_conflict
        fail
        ;;
    76)
        substep=system_ca_alias_write
        fail
        ;;
    77)
        substep=system_ca_record
        fail
        ;;
    78)
        substep=system_ca_execution
        fail
        ;;
    *)
        substep=system_ca_unknown
        fail
        ;;
esac
'''
PYTHON_SUBSTEP_CODES = {
    61: 'python_import_sys', 62: 'python_import_apt_pkg', 63: 'python_import_ssl',
    64: 'python_version_check', 65: 'python_version_mismatch',
    66: 'apt_version_check', 67: 'apt_version_mismatch',
    68: 'ca_path_check', 69: 'ca_file_missing',
}
V10_PREREQUISITE_BLOCK = r'''prerequisite_result=0
/usr/bin/python3 -I -B "$HELPERS/check_python_prerequisites.py" > "$OUT/logs/python-apt-prerequisites.log" 2>&1 || prerequisite_result=$?
case "$prerequisite_result" in
    0) ;;
    61)
        substep=python_import_sys
        fail
        ;;
    62)
        substep=python_import_apt_pkg
        fail
        ;;
    63)
        substep=python_import_ssl
        fail
        ;;
    64)
        substep=python_version_check
        fail
        ;;
    65)
        substep=python_version_mismatch
        fail
        ;;
    66)
        substep=apt_version_check
        fail
        ;;
    67)
        substep=apt_version_mismatch
        fail
        ;;
    68)
        substep=ca_path_check
        fail
        ;;
    69)
        substep=ca_file_missing
        fail
        ;;
    *)
        substep=python_execution
        fail
        ;;
esac
'''


def projection(code):
    return {'schema': 1, 'source': 'bootstrap', 'diagnostic_only': True,
            'detail': {'code': code}}


def header(vertex=14, command=None):
    command = d.RUN_COMMANDS['bootstrap'] if command is None else command
    return '#{} [7/18] RUN {}\n'.format(vertex, command)


def marker(code, vertex=14):
    return '#{} 0.123 {}{}\n'.format(vertex, d.BOOTSTRAP_PREFIX, code)


class BootstrapSubstepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = BOOTSTRAP.read_bytes()
        cls.source = cls.raw.decode('ascii')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.log = Path(temporary.name) / 'private-buildkit.log'

    def parse(self, text):
        self.log.write_bytes(text.encode('utf-8'))
        return d.parse_log(self.log)

    def prerequisite(self):
        lines = [line for line in self.normalized_v9().decode('ascii').splitlines()
                 if line.startswith(PREREQUISITE_PREFIX)]
        self.assertEqual(len(lines), 1)
        argv = shlex.split(lines[0], posix=True)
        self.assertEqual(argv[:-1], ['run', 'python-apt-prerequisites',
                                    '/usr/bin/python3', '-I', '-B', '-c'])
        self.assertEqual(argv[-1], V9_PREREQUISITE)
        return argv[-1]

    def normalized_v9(self):
        normalized = self.normalized_v10()
        new = V10_PREREQUISITE_BLOCK.encode('ascii')
        old = (PREREQUISITE_PREFIX + "'" + V9_PREREQUISITE + "'\n").encode('ascii')
        self.assertEqual(normalized.count(new), 1)
        self.assertNotIn(old, normalized)
        normalized = normalized.replace(new, old, 1)
        new = (V10_HELPER_LOOP + '\n').encode('ascii')
        old = (V9_HELPER_LOOP + '\n').encode('ascii')
        self.assertEqual(normalized.count(new), 2)
        self.assertNotIn(old, normalized)
        normalized = normalized.replace(new, old, 2)
        self.assertEqual(hashlib.sha256(normalized).hexdigest(), V9_SHA256,
                         'A bootstrap byte changed outside the v10 diagnostic block and helper lists')
        return normalized

    def normalized_v10(self):
        normalized = self.raw
        block = V11_SYSTEM_CA_BLOCK.encode('ascii')
        self.assertEqual(normalized.count(block), 1)
        normalized = normalized.replace(block, b'', 1)
        new = (V11_HELPER_LOOP + '\n').encode('ascii')
        old = (V10_HELPER_LOOP + '\n').encode('ascii')
        self.assertEqual(normalized.count(new), 2)
        self.assertNotIn(old, normalized)
        normalized = normalized.replace(new, old, 2)
        self.assertEqual(hashlib.sha256(normalized).hexdigest(), V10_SHA256,
                         'A bootstrap byte changed outside the fixed v11 CA block and helper lists')
        return normalized

    def test_source_phase_substep_pairs_equal_exact_finite_mapping(self):
        found = {}
        current = None
        lines = self.source.splitlines()
        for index, line in enumerate(lines):
            if not re.match(r'^[ \t]*(?:phase|substep)=', line):
                continue
            match = ASSIGNMENT.fullmatch(line)
            self.assertIsNotNone(match, 'Assignments must be literal finite labels')
            kind, value = match.groups()
            if kind == 'phase':
                self.assertNotIn(value, found, 'Each phase has one source assignment')
                current = value
                found[current] = set()
                self.assertEqual(lines[index + 1].strip(), 'substep=entry',
                                 'A phase must immediately reset its substep')
            else:
                self.assertIsNotNone(current)
                found[current].add(value)
        expected = {phase: set(steps) for phase, steps in d.BOOTSTRAP_SUBSTEPS.items()}
        self.assertEqual(found, expected)
        self.assertEqual(set(found), d.BOOTSTRAP_PHASES)
        for phase, steps in d.BOOTSTRAP_SUBSTEPS.items():
            with self.subTest(phase=phase):
                self.assertIs(type(steps), tuple)
                self.assertEqual(steps, tuple(sorted(set(steps))))
                self.assertIn('entry', steps)

    def test_failure_allowlist_contains_only_new_pairs_and_legacy_codes(self):
        expected = {'EXPLICIT_CONTAINER_INSTALL_MODE_REQUIRED'}
        expected.update('BLOCKED_' + phase for phase in d.BOOTSTRAP_PHASES)
        expected.update('BLOCKED_{}__{}'.format(phase, substep)
                        for phase, steps in d.BOOTSTRAP_SUBSTEPS.items()
                        for substep in steps)
        self.assertEqual(d.BOOTSTRAP_FAILURES, frozenset(expected))

    def test_each_new_code_projects_from_anchored_log_and_closed_schema(self):
        for phase, steps in d.BOOTSTRAP_SUBSTEPS.items():
            for substep in steps:
                code = 'BLOCKED_{}__{}'.format(phase, substep)
                with self.subTest(code=code):
                    expected = projection(code)
                    projected = d.validate_projection(expected)
                    self.assertEqual(projected, expected)
                    self.assertIsNot(projected, expected)
                    self.assertIsNot(projected['detail'], expected['detail'])
                    self.assertEqual(self.parse(header() + marker(code)), expected)

    def test_every_legacy_code_remains_accepted(self):
        codes = ['EXPLICIT_CONTAINER_INSTALL_MODE_REQUIRED']
        codes.extend('BLOCKED_' + phase for phase in sorted(d.BOOTSTRAP_PHASES))
        for code in codes:
            with self.subTest(code=code):
                self.assertEqual(d.validate_projection(projection(code)), projection(code))
                self.assertEqual(self.parse(header() + marker(code)), projection(code))

    def test_cross_phase_substeps_are_not_a_cartesian_product(self):
        all_steps = set().union(*map(set, d.BOOTSTRAP_SUBSTEPS.values()))
        rejected = 0
        for phase, steps in d.BOOTSTRAP_SUBSTEPS.items():
            for substep in sorted(all_steps - set(steps)):
                code = 'BLOCKED_{}__{}'.format(phase, substep)
                with self.subTest(code=code):
                    self.assertIsNone(d.validate_projection(projection(code)))
                    self.assertIsNone(self.parse(header() + marker(code)))
                rejected += 1
        self.assertGreater(rejected, 0)

    def test_unknown_labels_and_free_text_are_never_projected(self):
        valid = 'BLOCKED_post_install_verification__python_prerequisites'
        codes = [
            'BLOCKED_unknown__entry', 'BLOCKED_post_install_verification__unknown',
            'BLOCKED_post_install_verification__', 'BLOCKED___entry',
            'BLOCKED_post_install_verification_python_prerequisites',
            valid + '__entry', valid + ' PRIVATE', valid + ':PRIVATE',
            valid + ' /private/path', valid + ' https://private.invalid/value',
            valid + '\nPRIVATE', valid + '\r', valid + '\x00',
            valid + '\t', valid + '\u00e9', ' ' + valid, valid.upper(),
        ]
        for code in codes:
            with self.subTest(code=code):
                self.assertIsNone(d.validate_projection(projection(code)))
                # A newline can delimit an otherwise valid marker. Put free text
                # before the line terminator to test rejection, not a new record.
                if '\n' not in code:
                    self.assertIsNone(self.parse(header() + marker(code)))
        for extra in ({'phase': 'post_install_verification'},
                      {'substep': 'python_prerequisites'}, {'raw': 'PRIVATE'}):
            value = projection(valid)
            value['detail'].update(extra)
            self.assertIsNone(d.validate_projection(value))

    def test_new_codes_still_require_exact_anchored_buildkit_scope(self):
        code = 'BLOCKED_post_install_verification__python_prerequisites'
        examples = [
            marker(code), d.BOOTSTRAP_PREFIX + code + '\n',
            marker(code) + header(), header() + marker(code, vertex=15),
            'PRIVATE ' + header() + marker(code),
            '#88 0.123 ' + header() + marker(code),
            header(command=d.RUN_COMMANDS['bootstrap'] + ' && echo PRIVATE') + marker(code),
            header() + '#14 0.123 PRIVATE ' + d.BOOTSTRAP_PREFIX + code + '\n',
            header() + '#14 0.123 ' + marker(code),
            header() + marker(code).rstrip('\n'),
            header() + marker(code) + marker(code),
        ]
        for text in examples:
            with self.subTest(text=text):
                self.assertIsNone(self.parse(text))

    def test_success_does_not_become_failure_or_acceptance(self):
        self.assertIsNone(d.validate_projection(projection(d.BOOTSTRAP_SUCCESS)))
        self.assertIsNone(self.parse(header() + marker(d.BOOTSTRAP_SUCCESS)))

    def test_failure_formats_are_bounded_and_private_json_remains_nonaccepting(self):
        self.assertEqual(self.source.count(V9_PRIVATE), 1)
        self.assertEqual(self.source.count(V9_PUBLIC), 1)
        private = shlex.split(V9_PRIVATE)
        public = shlex.split(V9_PUBLIC)
        self.assertEqual(private[2:], ['$phase', '$substep', '>', '$OUT/failure.json'])
        self.assertEqual(public[2:], ['$phase', '$substep', '>&2'])
        for phase, steps in d.BOOTSTRAP_SUBSTEPS.items():
            for substep in steps:
                with self.subTest(phase=phase, substep=substep):
                    value = json.loads((private[1] % (phase, substep)).removesuffix(r'\n'))
                    self.assertEqual(value, {'schema': 1, 'status': 'BLOCKED',
                                            'phase': phase, 'substep': substep,
                                            'full_closure_verified': False})
                    self.assertEqual(public[1] % (phase, substep),
                                     d.BOOTSTRAP_PREFIX + 'BLOCKED_' + phase + '__' + substep + r'\n')

    def test_extracted_failure_function_emits_finite_marker_and_never_zero(self):
        # Execute only this reviewed function, not its trap or bootstrap body.
        # started=false excludes evidence writes; OUT is defensive confinement
        # to this test's temporary directory rather than the provisioning path.
        matches = re.findall(r'^failure\(\) \{\n.*?^\}\n', self.source,
                             flags=re.MULTILINE | re.DOTALL)
        self.assertEqual(len(matches), 1)
        function = matches[0]
        self.assertTrue(function.endswith('    exit "$result"\n}\n'))
        for phase, steps in d.BOOTSTRAP_SUBSTEPS.items():
            for substep in steps:
                code = 'BLOCKED_{}__{}'.format(phase, substep)
                for prior_status in (0, 1, 7):
                    with self.subTest(code=code, prior_status=prior_status):
                        fixture = function + '\n'.join((
                            'phase=' + shlex.quote(phase),
                            'substep=' + shlex.quote(substep),
                            'completed=false', 'started=false',
                            'OUT=' + shlex.quote(str(self.log.parent)),
                            '(exit {})'.format(prior_status),
                            'failure', '',
                        ))
                        result = subprocess.run(
                            ['/bin/sh', '-c', fixture], check=False,
                            env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                            capture_output=True, text=True, timeout=5,
                        )
                        self.assertEqual(result.returncode, prior_status or 1)
                        self.assertEqual(result.stdout, '')
                        self.assertEqual(result.stderr, d.BOOTSTRAP_PREFIX + code + '\n')
                        self.assertFalse((self.log.parent / 'failure.json').exists())

    def test_each_post_install_operation_has_the_intended_label(self):
        region = self.normalized_v9().decode('ascii').split('phase=post_install_verification\n', 1)[1]
        region = region.split('phase=receipt\n', 1)[0]
        operations = {}
        current = None
        for line in region.splitlines():
            match = ASSIGNMENT.fullmatch(line)
            if match:
                self.assertEqual(match.group(1), 'substep')
                current = match.group(2)
                self.assertNotIn(current, operations)
                operations[current] = []
            elif line.strip():
                self.assertIsNotNone(current)
                operations[current].append(line.strip())
        expected = {
            'entry': [],
            'status_snapshot': ['/usr/bin/cp /var/lib/dpkg/status "$OUT/post-status"'],
            'status_parse': [r'''/usr/bin/awk -f "$HELPERS/parse_status.awk" "$OUT/post-status" > "$OUT/post.tsv" || fail'''],
            'transaction_delta': [r'''/usr/bin/awk -f "$HELPERS/verify_delta.awk" "$OUT/base.tsv" "$OUT/transaction.tsv" "$OUT/post.tsv" || fail'''],
            'root_versions': [
                r'''while IFS="$(printf '\t')" read -r package version; do''',
                r'''/usr/bin/awk -F '\t' -v p="$package" -v v="$version" '$1==p && $3==v {n++} END { if(n!=1) exit 56 }' "$OUT/post.tsv" || fail''',
                'done < "$OUT/roots.tsv"',
            ],
            'apt_check': ['run post-apt-check /usr/bin/apt-get check'],
            'dpkg_audit': ['run post-dpkg-audit /usr/bin/dpkg --audit'],
            'audit_clean': ['[ ! -s "$OUT/logs/post-dpkg-audit.log" ] || fail'],
            'python_prerequisites': [PREREQUISITE_PREFIX + "'" + self.prerequisite() + "'"],
            'final_snapshot': ['/usr/bin/cp /var/lib/dpkg/status "$OUT/final-status"'],
            'final_unchanged': ['/usr/bin/cmp -s "$OUT/post-status" "$OUT/final-status" || fail'],
            'timestamp': [r'''/usr/bin/date -u '+%Y-%m-%dT%H:%M:%SZ' > "$OUT/end-time-utc"'''],
        }
        self.assertEqual(operations, expected)
        self.assertEqual(set(operations), set(d.BOOTSTRAP_SUBSTEPS['post_install_verification'])
                         - set(PYTHON_SUBSTEP_CODES.values()) - {'python_execution'}
                         - set(SYSTEM_CA_SUBSTEP_CODES.values())
                         - {'system_ca_defaults', 'system_ca_unknown'})

    def test_only_declared_shell_changes_restore_exact_v8_bytes(self):
        normalized, annotations = SUBSTEP_LINE.subn(b'', self.normalized_v9())
        self.assertGreater(annotations, 0)
        replacements = (
            (V9_PRIVATE, V8_PRIVATE),
            (V9_PUBLIC, V8_PUBLIC),
            (PREREQUISITE_PREFIX + "'" + V9_PREREQUISITE + "'",
             PREREQUISITE_PREFIX + "'" + V8_PREREQUISITE + "'"),
        )
        for new, old in replacements:
            new = (new + '\n').encode('ascii')
            old = (old + '\n').encode('ascii')
            self.assertEqual(normalized.count(new), 1)
            self.assertNotIn(old, normalized)
            normalized = normalized.replace(new, old, 1)
        self.assertEqual(hashlib.sha256(normalized).hexdigest(), V8_SHA256,
                         'A non-diagnostic bootstrap byte changed outside the explicit prerequisite hardening')



if __name__ == '__main__':
    unittest.main()

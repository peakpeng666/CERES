"""Pure helper/guard equivalence and isolated shell dispatch fixtures.

Imports, version data and CA data are fake. No provisioning shell, installed apt
module, certificate store, package installation or native build is executed.
"""
import ast
import builtins
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from test_bootstrap_substeps import (
    BOOTSTRAP, PYTHON_SUBSTEP_CODES, ROOT, V9_PREREQUISITE,
    V11_HELPER_LOOP, V10_PREREQUISITE_BLOCK,
)

HELPER = ROOT / 'preparation' / 'bootstrap' / 'check_python_prerequisites.py'
PRIVATE_TEXT = 'PRIVATE fixture exception /private/path https://private.invalid/value'
CASES = (
    ('valid', {}, 0),
    ('valid_patch_versions', {'version': (3, 10, 99), 'apt': '2.4.0'}, 0),
    ('sys_import', {'import_error': 'sys'}, 61),
    ('apt_import', {'import_error': 'apt_pkg'}, 62),
    ('ssl_import', {'import_error': 'ssl'}, 63),
    ('ssl_import_before_bad_version', {'import_error': 'ssl', 'version': (3, 11, 0)}, 63),
    ('python_attribute', {'fault': 'python_get'}, 64),
    ('python_slice', {'fault': 'python_slice'}, 64),
    ('python_compare', {'fault': 'python_compare'}, 64),
    ('python_truth', {'fault': 'python_bool'}, 64),
    ('python_none', {'version': None}, 64),
    ('python_major', {'version': (2, 10, 0)}, 65),
    ('python_minor', {'version': (3, 11, 0)}, 65),
    ('python_short', {'version': (3,)}, 65),
    ('python_empty', {'version': ()}, 65),
    ('python_short_circuits_apt_fault', {'version': (3, 9, 0), 'fault': 'apt_get'}, 65),
    ('apt_attribute', {'fault': 'apt_get'}, 66),
    ('apt_startswith', {'fault': 'apt_startswith'}, 66),
    ('apt_truth', {'fault': 'apt_bool'}, 66),
    ('apt_none', {'apt': None}, 66),
    ('apt_minor', {'apt': '2.5.0'}, 67),
    ('apt_major', {'apt': '3.0.0'}, 67),
    ('apt_missing_dot', {'apt': '2.4'}, 67),
    ('apt_lookalike', {'apt': '2.40.1'}, 67),
    ('apt_empty', {'apt': ''}, 67),
    ('apt_short_circuits_ca_fault', {'apt': '2.5.0', 'fault': 'ca_query'}, 67),
    ('ca_query', {'fault': 'ca_query'}, 68),
    ('ca_attribute', {'fault': 'ca_get'}, 68),
    ('ca_truth', {'fault': 'ca_bool'}, 68),
    ('ca_query_none', {'paths_none': True}, 68),
    ('ca_none', {'cafile': None}, 69),
    ('ca_empty', {'cafile': ''}, 69),
    ('ca_false', {'cafile': False}, 69),
)


def fixture(options=None):
    """Fresh probes let helper and old guard expose their evaluation order."""
    options = {} if options is None else options
    events = []

    def touch(name):
        events.append(name)
        if options.get('fault') == name:
            raise options.get('exception', RuntimeError)(PRIVATE_TEXT)

    class Truth:
        def __init__(self, name, value):
            self.name, self.value = name, value

        def __bool__(self):
            touch(self.name)
            return bool(self.value)

    class ComparedVersion:
        def __eq__(self, other):
            touch('python_compare')
            return Truth('python_bool', options.get('version', (3, 10, 12))[:2] == other)

    class Version:
        def __getitem__(self, key):
            touch('python_slice')
            if key != slice(None, 2):
                raise AssertionError('Unexpected version selection')
            return ComparedVersion()

    class AptVersion:
        def startswith(self, prefix):
            touch('apt_startswith')
            if prefix != '2.4.':
                raise AssertionError('Unexpected APT predicate')
            return Truth('apt_bool', options.get('apt', '2.4.14').startswith(prefix))

    class System:
        @property
        def version_info(self):
            touch('python_get')
            return None if options.get('version', ()) is None else Version()

        @staticmethod
        def exit(status):
            raise SystemExit(status)

    class Apt:
        @property
        def VERSION(self):
            touch('apt_get')
            return None if options.get('apt', '') is None else AptVersion()

    class Paths:
        @property
        def cafile(self):
            touch('ca_get')
            return Truth('ca_bool', options.get('cafile', '/fixture/ca.pem'))

    class SSL:
        @staticmethod
        def get_default_verify_paths():
            touch('ca_query')
            return None if options.get('paths_none') else Paths()

    modules = {'sys': System(), 'apt_pkg': Apt(), 'ssl': SSL()}

    def importer(name, *args, **kwargs):
        events.append('import:' + name)
        if options.get('import_error') == name:
            raise options.get('exception', ModuleNotFoundError)(PRIVATE_TEXT)
        if name not in modules:
            raise AssertionError('A fixture must never import a real host module')
        return modules[name]

    return importer, events


class PythonPrerequisiteDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = HELPER.read_text(encoding='ascii')
        cls.shell = BOOTSTRAP.read_text(encoding='ascii')

    def test_v10_prerequisite_guard_is_byte_identical(self):
        self.assertEqual(hashlib.sha256(HELPER.read_bytes()).hexdigest(),
                         '2d26330f8b4c4e314f71a0fb4477ec9bb11e9407e9cbb8bc7cf1521554680e63')

    def helper_namespace(self, optimize, importer, main=False):
        safe_builtins = dict(vars(builtins), __import__=importer)
        namespace = {'__name__': '__main__' if main else 'fixture_helper',
                     '__builtins__': safe_builtins}
        exec(compile(self.source, '<actual-prerequisite-helper>', 'exec',
                     optimize=optimize), namespace, namespace)
        return namespace

    def old_guard_result(self, importer, optimize):
        namespace = {'__builtins__': {'__import__': importer}}
        try:
            exec(compile(V9_PREREQUISITE, '<frozen-v9-guard>', 'exec',
                         optimize=optimize), namespace, namespace)
        except SystemExit as error:
            return error.code == 0
        except Exception:
            return False
        self.fail('The frozen v9 guard must exit')

    def test_actual_helper_all_codes_match_frozen_guard_and_order_even_optimized(self):
        observed = set()
        for optimize in (0, 1):
            for name, options, expected in CASES:
                with self.subTest(optimize=optimize, case=name):
                    importer, events = fixture(options)
                    original_importer, original_events = fixture(options)
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        check = self.helper_namespace(optimize, importer)['check']
                        actual = check(importer=importer)
                        previous_success = self.old_guard_result(original_importer, optimize)
                    self.assertIs(type(actual), int)
                    self.assertEqual(actual, expected)
                    self.assertEqual(actual == 0, previous_success)
                    self.assertEqual(events, original_events)
                    self.assertEqual(stdout.getvalue(), '')
                    self.assertEqual(stderr.getvalue(), '')
                    observed.add(actual)
        self.assertEqual(observed, {0, *range(61, 70)})

    def test_imports_precede_predicates_and_predicates_keep_short_circuit_order(self):
        expected = ['import:sys', 'import:apt_pkg', 'import:ssl',
                    'python_get', 'python_slice', 'python_compare', 'python_bool',
                    'apt_get', 'apt_startswith', 'apt_bool', 'ca_query', 'ca_get', 'ca_bool']
        for optimize in (0, 1):
            for options, count, code in (({}, len(expected), 0),
                                         ({'version': (3, 11, 0)}, 7, 65),
                                         ({'apt': '2.5.0'}, 10, 67),
                                         ({'import_error': 'ssl', 'version': (3, 11, 0)}, 3, 63)):
                with self.subTest(optimize=optimize, options=options):
                    importer, events = fixture(options)
                    check = self.helper_namespace(optimize, importer)['check']
                    self.assertEqual(check(importer), code)
                    self.assertEqual(events, expected[:count])

    def test_only_exception_is_caught_not_base_exception(self):
        places = [{'import_error': name} for name in ('sys', 'apt_pkg', 'ssl')]
        places += [{'fault': name} for name in (
            'python_get', 'python_slice', 'python_compare', 'python_bool',
            'apt_get', 'apt_startswith', 'apt_bool', 'ca_query', 'ca_get', 'ca_bool')]
        for optimize in (0, 1):
            for place in places:
                for exception in (KeyboardInterrupt, SystemExit, GeneratorExit):
                    with self.subTest(optimize=optimize, place=place, exception=exception):
                        options = dict(place, exception=exception)
                        importer, events = fixture(options)
                        old_importer, old_events = fixture(options)
                        check = self.helper_namespace(optimize, importer)['check']
                        with self.assertRaises(exception):
                            check(importer)
                        namespace = {'__builtins__': {'__import__': old_importer}}
                        with self.assertRaises(exception):
                            exec(compile(V9_PREREQUISITE, '<frozen-v9-guard>', 'exec',
                                         optimize=optimize), namespace, namespace)
                        self.assertEqual(events, old_events)

    def test_real_entrypoint_uses_default_importer_and_emits_no_raw_output(self):
        for optimize in (0, 1):
            for name, options, expected in CASES:
                with self.subTest(optimize=optimize, case=name):
                    importer, _ = fixture(options)
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        with self.assertRaises(SystemExit) as raised:
                            self.helper_namespace(optimize, importer, main=True)
                    self.assertEqual(raised.exception.code, expected)
                    self.assertEqual(stdout.getvalue(), '')
                    self.assertEqual(stderr.getvalue(), '')

    def test_helper_has_no_assert_or_catch_all_handler(self):
        tree = ast.parse(self.source)
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
        handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
        self.assertEqual(len(handlers), 6)
        for handler in handlers:
            self.assertIsInstance(handler.type, ast.Name)
            self.assertEqual(handler.type.id, 'Exception')

    def test_helper_is_checked_and_copied_and_execution_uses_private_log(self):
        self.assertEqual(self.shell.count(V11_HELPER_LOOP + '\n'), 2)
        self.assertEqual(self.shell.count(V10_PREREQUISITE_BLOCK), 1)
        line = V10_PREREQUISITE_BLOCK.splitlines()[1]
        self.assertEqual(shlex.split(line), [
            '/usr/bin/python3', '-I', '-B', '$HELPERS/check_python_prerequisites.py',
            '>', '$OUT/logs/python-apt-prerequisites.log', '2>&1', '||', 'prerequisite_result=$?',
        ])
        self.assertNotIn('run python-apt-prerequisites ', self.shell)

    def test_extracted_shell_dispatch_exhausts_exit_codes_without_raw_output(self):
        # Extract only case dispatch; do not invoke Python or the bootstrap body.
        self.assertEqual(self.shell.count(V10_PREREQUISITE_BLOCK), 1)
        actual_block = self.shell.split('substep=python_prerequisites\n', 1)[1]
        actual_block = actual_block.split('substep=final_snapshot\n', 1)[0]
        self.assertEqual(actual_block, V10_PREREQUISITE_BLOCK)
        dispatch = actual_block.split('case "$prerequisite_result" in\n', 1)[1]
        dispatch = 'case "$prerequisite_result" in\n' + dispatch
        with tempfile.TemporaryDirectory() as directory:
            for code in range(256):
                with self.subTest(code=code):
                    shell = '\n'.join((
                        'substep=python_prerequisites',
                        'prerequisite_result=' + str(code),
                        'fail() { printf "SAFE_BOOTSTRAP_DIAGNOSTIC:BLOCKED_post_install_verification__%s\\n" "$substep" >&2; exit 1; }',
                        dispatch, 'exit 0', '',
                    ))
                    result = subprocess.run(['/bin/sh', '-c', shell],
                                            env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                                            cwd=directory, capture_output=True, text=True,
                                            timeout=5, check=False)
                    self.assertEqual(result.stdout, '')
                    if code == 0:
                        self.assertEqual(result.returncode, 0)
                        self.assertEqual(result.stderr, '')
                    else:
                        self.assertEqual(result.returncode, 1)
                        label = PYTHON_SUBSTEP_CODES.get(code, 'python_execution')
                        self.assertEqual(result.stderr,
                                         'SAFE_BOOTSTRAP_DIAGNOSTIC:BLOCKED_post_install_verification__'
                                         + label + '\n')
                    self.assertNotIn(PRIVATE_TEXT, result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()

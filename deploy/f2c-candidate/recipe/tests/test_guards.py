"""Synthetic guard tests only. No install, compiler, network namespace or native imports."""
import contextlib
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

RECIPE = pathlib.Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location(name, RECIPE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

g = load('guardlib')
n = load('build_guard')
b = load('run_build')

class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='guard-test-', dir=RECIPE / 'tests')
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
        return p

    def index(self, complete=False):
        for name in g.REQUIRED_INDEX:
            if name.startswith('recipe/'):
                p = self.root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(RECIPE / pathlib.Path(name).name, p)
            else:
                self.write(name, json.dumps({'schema': 1, 'status': 'incomplete'}))
        obj = {'schema': 1, 'status': 'reviewed-complete',
               'files': {name: {'sha256': g.sha256(self.root / name)} for name in sorted(g.REQUIRED_INDEX)}}
        p = self.write('artifact-index.json', json.dumps(obj))
        return g.sha256(p)

class IndexTests(Fixtures):
    def test_valid_anchored_index(self):
        anchor = self.index()
        self.assertEqual(g.verify_index(self.root, anchor)['schema'], 1)

    def test_wrong_anchor(self):
        self.index()
        with self.assertRaisesRegex(RuntimeError, 'anchor mismatch'):
            g.verify_index(self.root, '0' * 64)

    def test_changed_helper(self):
        anchor = self.index()
        self.write('recipe/guardlib.py', 'changed')
        with self.assertRaisesRegex(RuntimeError, 'artifact mismatch'):
            g.verify_index(self.root, anchor)

    def test_missing_required_helper(self):
        self.index()
        path = self.root / 'artifact-index.json'
        obj = json.loads(path.read_text())
        del obj['files']['recipe/run_build.py']
        path.write_text(json.dumps(obj))
        with self.assertRaisesRegex(RuntimeError, 'Missing indexed'):
            g.verify_index(self.root, g.sha256(path))

    def test_symlink_helper(self):
        anchor = self.index()
        p = self.root / 'recipe/guardlib.py'
        p.rename(p.with_suffix('.real'))
        p.symlink_to('guardlib.real')
        with self.assertRaisesRegex(RuntimeError, 'Symlink'):
            g.verify_index(self.root, anchor)

    def test_duplicate_json_keys(self):
        path = self.write('duplicate.json', '{"a": 1, "a": 2}')
        with self.assertRaisesRegex(RuntimeError, 'Duplicate JSON'):
            g.read_json(path)

    def test_traversal_and_absolute_paths_rejected(self):
        for name in ('../escape', '/tmp/escape', 'a//b', 'a/./b', 'a/../b', ''):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                g.relative_path(name)

    def test_index_world_writable_rejected(self):
        anchor = self.index()
        (self.root / 'recipe/guardlib.py').chmod(0o666)
        with self.assertRaisesRegex(RuntimeError, 'writable'):
            g.verify_index(self.root, anchor)

class TreeTests(Fixtures):
    def test_added_changed_removed_files_are_observed(self):
        tree = self.root / 'inputs'
        p = self.write('inputs/a', 'a')
        initial = g.snapshot(tree, self.root)
        self.write('inputs/b', 'b')
        self.assertNotEqual(initial, g.snapshot(tree, self.root))
        (tree / 'b').unlink()
        p.write_text('changed')
        self.assertNotEqual(initial, g.snapshot(tree, self.root))
        p.unlink()
        self.assertNotEqual(initial, g.snapshot(tree, self.root))

    def test_symlink_escape_rejected(self):
        self.write('inputs/a', 'a')
        (self.root / 'inputs/link').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(RuntimeError, 'escapes'):
            g.snapshot(self.root / 'inputs', self.root)

    def test_symlink_ancestor_rejected(self):
        self.write('real/tree/a', 'a')
        (self.root / 'alias').symlink_to('real', target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'ancestry'):
            g.snapshot(self.root / 'alias/tree', self.root)

    def test_internal_symlink_target_is_measured(self):
        self.write('inputs/a', 'a')
        (self.root / 'inputs/link').symlink_to('a')
        result = g.snapshot(self.root / 'inputs', self.root)
        row = next(x for x in result if x['path'] == 'link')
        self.assertEqual(row['target'], 'a')
        self.assertEqual(row['referent_sha256'], hashlib.sha256(b'a').hexdigest())

    def test_hard_link_and_special_file_rejected(self):
        p = self.write('inputs/a', 'a')
        os.link(p, self.root / 'inputs/b')
        with self.assertRaisesRegex(RuntimeError, 'Hard-linked'):
            g.snapshot(self.root / 'inputs', self.root)
        (self.root / 'inputs/b').unlink()
        os.mkfifo(self.root / 'inputs/pipe')
        with self.assertRaisesRegex(RuntimeError, 'Special file'):
            g.snapshot(self.root / 'inputs', self.root)

class OutputTests(Fixtures):
    def test_fresh_creation_and_no_resume(self):
        g.fresh_outputs(self.root)
        g.fresh_outputs(self.root, create=True)
        self.assertEqual(set(x.name for x in (self.root / 'output').iterdir()), set(g.OUTPUTS))
        for p in (self.root / 'output').iterdir():
            self.assertEqual(p.stat().st_mode & 0o777, 0o700)
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            g.fresh_outputs(self.root, create=True)

    def test_existing_symlink_even_dangling_refused(self):
        (self.root / 'output').symlink_to('missing')
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            g.fresh_outputs(self.root, create=True)
        self.assertFalse((self.root / 'missing').exists())

    def test_root_alias_and_delimiter_rejected(self):
        (self.root / 'real').mkdir()
        (self.root / 'alias').symlink_to('real', target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'Symlink'):
            g.root_guard(self.root / 'alias')
        (self.root / 'has space').mkdir()
        with self.assertRaisesRegex(RuntimeError, 'delimiters'):
            g.root_guard(self.root / 'has space')

class NetworkTests(Fixtures):
    def setup_proc(self):
        self.write('proc/sys/kernel/random/boot_id', 'boot-1\n')
        self.write('proc/net/dev', 'header\nheader\n lo: 0 0\n')
        self.write('proc/net/route', 'header\n')
        self.write('proc/net/ipv6_route', '')
        self.write('proc/self/fd/0', '')
        return {'kernel_boot_id': 'boot-1', 'netns': 'net:[host]'}

    def check(self, baseline=None, mine='net:[isolated]', flags=0, socket_fd=False):
        if baseline is None:
            baseline = self.setup_proc()
        def readlink(path):
            s = str(path)
            if s.endswith('self/ns/net'):
                return mine
            if s.endswith('1/ns/net'):
                return 'net:[init]'
            return 'socket:[1]' if socket_fd else '/dev/null'
        with mock.patch.object(n.socket, 'socket') as sock:
            sock.return_value.__enter__.return_value = object()
            return n.network_guard(baseline, self.root / 'proc', readlink,
                                   lambda *args: struct.pack('16sH14x', b'lo', flags))

    def test_synthetic_isolated_namespace_passes(self):
        self.assertTrue(self.check()['network_isolated'])

    def test_host_namespace_not_env_marker(self):
        with mock.patch.dict(os.environ, {'NETWORK_ISOLATED': '1'}), self.assertRaisesRegex(RuntimeError, 'original host'):
            self.check(mine='net:[host]')

    def test_loopback_up_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Loopback is UP'):
            self.check(flags=1)

    def test_boot_mismatch_rejected(self):
        baseline = self.setup_proc()
        baseline['kernel_boot_id'] = 'different'
        with self.assertRaisesRegex(RuntimeError, 'boot changed'):
            self.check(baseline)

    def test_extra_interface_rejected(self):
        baseline = self.setup_proc()
        self.write('proc/net/dev', 'header\nheader\n lo: 0\n eth0: 0\n')
        with self.assertRaisesRegex(RuntimeError, 'interfaces'):
            self.check(baseline)

    def test_route_rejected(self):
        baseline = self.setup_proc()
        self.write('proc/net/route', 'header\neth0 00000000\n')
        with self.assertRaisesRegex(RuntimeError, 'IPv4'):
            self.check(baseline)

    def test_inherited_socket_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'socket descriptor'):
            self.check(socket_fd=True)

class LaunchTests(Fixtures):
    def isolated(self, root, anchor):
        # Only a Python verifier runs. No shell, unshare, package manager, or compiler.
        return subprocess.run([sys.executable, '-I', '-B', str(RECIPE / 'verify_offline_inputs.py'),
                               '--check', '--root', str(root), '--python', sys.executable,
                               '--expected-index-sha256', anchor], capture_output=True, text=True, check=False)

    def test_isolated_import_resolves_only_anchored_helper(self):
        anchor = self.index()
        result = self.isolated(self.root, anchor)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Jammy preparation incomplete', result.stderr)
        self.assertNotIn('ModuleNotFoundError', result.stderr)
        self.assertFalse((self.root / 'output').exists())

    def test_wrong_anchor_never_imports_local_helper(self):
        self.index()
        self.write('recipe/guardlib.py', 'raise Exception("LOCAL_HELPER_EXECUTED")')
        result = self.isolated(self.root, '0' * 64)
        self.assertEqual(result.returncode, 2)
        self.assertIn('anchor mismatch', result.stderr)
        self.assertNotIn('LOCAL_HELPER_EXECUTED', result.stderr)

    def test_incomplete_actual_candidate_refuses_without_writes(self):
        self.assertFalse((RECIPE.parent / 'artifact-index.json').exists(), 'Test only applies to deliberately unprepared candidate')
        before = sorted(x.name for x in RECIPE.parent.iterdir())
        result = self.isolated(RECIPE.parent, '0' * 64)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Refusing:', result.stderr)
        self.assertEqual(before, sorted(x.name for x in RECIPE.parent.iterdir()))
        self.assertFalse((RECIPE.parent / 'output').exists())

    def test_inline_bootstrap_rejects_before_helper_execution(self):
        self.index()
        script = (RECIPE / 'build_offline.sh').read_text()
        code = script.split("<<'BOOTSTRAP'\n", 1)[1].split('\nBOOTSTRAP\n', 1)[0]
        self.write('recipe/guardlib.py', 'raise Exception("LOCAL_HELPER_EXECUTED")')
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(self.root), '0' * 64], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('anchor mismatch', result.stderr)
        self.assertNotIn('LOCAL_HELPER_EXECUTED', result.stderr)

    def test_no_build_for_incomplete_manifest(self):
        with mock.patch.object(b, 'invoke') as invoke:
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                g.require_complete({'schema': 1, 'status': 'incomplete'})
            invoke.assert_not_called()

    def test_clean_environment_drops_credentials_and_link_overrides(self):
        lock = {'build': {'source_date_epoch': 1788454736, 'parallel_jobs': 2}}
        with mock.patch.dict(os.environ, {'SECRET': 'fixture', 'LD_LIBRARY_PATH': '/bad', 'PYTHONPATH': '/bad'}):
            env = b.clean_environment(self.root, {'gcc': pathlib.Path('/usr/bin/gcc-11'), 'gxx': pathlib.Path('/usr/bin/g++-11')}, lock)
        for key in ('SECRET', 'LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'GITHUB_TOKEN', 'CFLAGS', 'LDFLAGS'):
            self.assertNotIn(key, env)
        self.assertEqual(env['PIP_NO_INDEX'], '1')

    def test_build_arguments_are_offline_and_relocatable(self):
        lock = {'inputs': {'trees': [{'name': name, 'ref': {'base': 'candidate', 'path': 'sources/' + name}}
                                    for name in ('fields2cover', 'steering_functions', 'matplot', 'json')]},
                'runtime': {'extracted_ref': {'base': 'candidate', 'path': 'provider'}},
                'interface': {'cmake_ref': {'base': 'candidate', 'path': 'interface/cmake'}}}
        argv = b.build_command(self.root, pathlib.Path('/usr'), {'gcc': '/usr/bin/gcc-11', 'gxx': '/usr/bin/g++-11'}, lock)
        self.assertIn('-Ccmake.define.CMAKE_INSTALL_RPATH=' + b.INSTALL_RPATH, argv)
        self.assertIn('-Ccmake.define.ZLIB_LIBRARY=' + str(self.root / 'provider/ortools/.libs/libz.so.1'), argv)
        for expected in ('FETCHCONTENT_FULLY_DISCONNECTED=ON', 'USE_ORTOOLS_FETCH_SRC=OFF',
                         'CMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF', 'MATPLOTPP_BUILD_TESTS=OFF',
                         'CMAKE_FIND_USE_PACKAGE_REGISTRY=OFF'):
            self.assertIn('-Ccmake.define.' + expected, argv)
        self.assertFalse(any('Wno-error' in x or 'LD_LIBRARY_PATH' in x for x in argv))

if __name__ == '__main__':
    unittest.main(verbosity=2)

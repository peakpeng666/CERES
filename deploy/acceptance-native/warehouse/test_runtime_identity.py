"""Synthetic metadata-only tests: no real package query or native simulation."""
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import runtime_identity as ri


LOG = (b'12:34:56: GL_VERSION = 4.5.0 (Core Profile) Mesa 23.2.1\n'
       b'12:34:56: GL_VENDOR = Mesa\n'
       b'12:34:56: GL_RENDERER = llvmpipe (LLVM 15.0.7, 256 bits)\n')
SOFTPIPE_LOG = LOG.replace(b'llvmpipe (LLVM 15.0.7, 256 bits)', b'softpipe')


def packages():
    return b''.join((name + '\tamd64\t1:6.6.3-1~jammy\tinstalled\t' + name + '\t1:6.6.3-1~jammy\n').encode() for name in ri.PACKAGES)


def query(data=None):
    return {'status': 'complete', 'stdout': packages() if data is None else data, 'returncode': 0}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.private = self.root / 'harness-private'
        self.private.mkdir()
        self.share = self.root / 'share'
        for name, relative in ri.FILES:
            path = self.share / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('private-file-' + name).encode())
        self.patch = patch.object(ri, '_SHARE', self.share)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.runner = Mock(return_value=query())
        self.identity = ri.RuntimeIdentity(self.private, runner=self.runner)
        self.log = self.home / '.ignition/rendering/ogre.log'

    def write_log(self, data=LOG):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_bytes(data)

    def finish(self, data=LOG):
        self.identity.before_gateway()
        self.write_log(data)
        self.identity.after_cleanup()
        return self.identity.result()

    def test_normal_identity_is_installed_not_loaded(self):
        out = self.finish()
        self.assertEqual(out['status'], 'complete')
        self.assertEqual(out['package_database']['status'], 'complete')
        self.assertEqual(out['installed_file_bytes']['status'], 'complete')
        self.assertEqual(len(out['package_database']['packages']), 21)
        self.assertEqual(len(out['installed_file_bytes']['files']), 14)
        claim = out['renderer_log_claim']
        self.assertEqual(claim['status'], 'available')
        self.assertEqual(claim['gl_api_version'], '4.5.0')
        self.assertEqual(claim['vendor_family'], 'mesa')
        self.assertEqual(claim['renderer_family'], 'llvmpipe')
        self.assertEqual(claim['mesa_version'], 'unavailable')
        self.assertEqual(out['loaded_library_identity']['status'], 'unavailable')
        self.assertEqual(self.runner.call_args.args, (ri.QUERY_ARGV,))
        self.assertEqual(self.runner.call_args.kwargs, {'timeout': 3.0, 'max_bytes': 65536})

    def test_exact_softpipe_fresh_owned_log_reaches_public_projection(self):
        self.assertEqual(ri._parse_log(SOFTPIPE_LOG), {
            'status': 'available', 'gl_api_version': '4.5.0',
            'vendor_family': 'mesa', 'renderer_family': 'softpipe'})
        out = self.finish(SOFTPIPE_LOG)
        self.assertEqual(out['status'], 'complete')
        self.assertEqual(out['package_database']['status'], 'complete')
        self.assertEqual(out['installed_file_bytes']['status'], 'complete')
        self.assertEqual(out['renderer_log_claim'], {
            'status': 'available', 'attribution': 'invocation_associated_not_pid_bound',
            'truncated': False, 'gl_api_version': '4.5.0',
            'vendor_family': 'mesa', 'renderer_family': 'softpipe',
            'mesa_version': 'unavailable', 'ogre_version': 'unavailable'})
        self.assertEqual(out['loaded_library_identity'], {
            'status': 'unavailable', 'reason': 'not_observed'})
        self.assertEqual(ri.sanitize(out), out)
        for hidden in (str(self.root), 'private-file', 'GL_RENDERER', 'Mesa 23.2.1'):
            self.assertNotIn(hidden, json.dumps(out))

    def test_softpipe_preexisting_log_is_never_claimed_even_after_replacement(self):
        self.write_log(SOFTPIPE_LOG)
        self.identity.before_gateway()
        self.log.unlink()
        self.write_log(SOFTPIPE_LOG)
        self.identity.after_cleanup()
        out = self.identity.result()
        claim = out['renderer_log_claim']
        self.assertEqual(claim['status'], 'preexisting')
        self.assertEqual(claim['attribution'], 'unavailable')
        self.assertIsNone(claim['gl_api_version'])
        self.assertEqual(claim['vendor_family'], 'unavailable')
        self.assertEqual(claim['renderer_family'], 'unavailable')
        self.assertEqual(ri.sanitize(out), out)

    def test_softpipe_truncated_log_is_never_claimed(self):
        out = self.finish(SOFTPIPE_LOG + b'x' * ri.LOG_BYTES)
        claim = out['renderer_log_claim']
        self.assertEqual(claim['status'], 'truncated')
        self.assertTrue(claim['truncated'])
        self.assertEqual(claim['attribution'], 'invocation_associated_not_pid_bound')
        self.assertIsNone(claim['gl_api_version'])
        self.assertEqual(claim['vendor_family'], 'unavailable')
        self.assertEqual(claim['renderer_family'], 'unavailable')
        self.assertEqual(ri.sanitize(out), out)

    def test_exact_home_path_not_ign_homedir_environment(self):
        with patch.dict(os.environ, {'HOME': '/secret', 'IGN_HOMEDIR': '/wrong-secret'}):
            self.assertEqual(self.identity.log, self.log)
            self.assertEqual(self.finish()['renderer_log_claim']['status'], 'available')

    def test_collection_and_cleanup_are_once(self):
        self.identity.before_gateway()
        self.identity.before_gateway()
        self.assertEqual(self.runner.call_count, 1)
        self.write_log()
        self.identity.after_cleanup()
        original = self.identity.result()
        self.write_log(LOG + b'GL_RENDERER = softpipe\n')
        self.identity.after_cleanup()
        self.assertEqual(self.identity.result(), original)

    def test_after_without_before_never_collects(self):
        self.identity.after_cleanup()
        self.identity.before_gateway()
        self.runner.assert_not_called()
        self.assertEqual(self.identity.result()['renderer_log_claim']['status'], 'preflight_failed')

    def test_preexisting_log_never_reused_even_replaced(self):
        self.write_log()
        self.identity.before_gateway()
        self.log.unlink()
        self.write_log(b'GL_VERSION = 4.6\n')
        self.identity.after_cleanup()
        self.assertEqual(self.identity.result()['renderer_log_claim']['status'], 'preexisting')
        self.assertIsNone(self.identity.result()['renderer_log_claim']['gl_api_version'])

    def test_missing_log(self):
        self.identity.before_gateway()
        self.identity.after_cleanup()
        self.assertEqual(self.identity.result()['renderer_log_claim']['status'], 'missing')

    def test_home_missing_at_preflight(self):
        self.home.rmdir()
        self.identity.before_gateway()
        self.write_log()
        self.identity.after_cleanup()
        self.assertEqual(self.identity.result()['renderer_log_claim']['status'], 'preflight_failed')

    def test_home_replaced_rejects_claim(self):
        self.identity.before_gateway()
        self.home.rename(self.root / 'old_home')
        self.write_log()
        self.identity.after_cleanup()
        self.assertEqual(self.identity.result()['renderer_log_claim']['status'], 'changed')

    def test_preexisting_parent_replaced_rejects_claim(self):
        self.log.parent.mkdir(parents=True)
        self.identity.before_gateway()
        self.log.parent.rename(self.home / '.ignition/old_rendering')
        self.write_log()
        self.identity.after_cleanup()
        self.assertEqual(self.identity.result()['renderer_log_claim']['status'], 'changed')

    def test_private_log_symlink_rejected_before_read(self):
        self.identity.before_gateway()
        self.log.parent.mkdir(parents=True)
        target = self.root / 'secret'
        target.write_bytes(LOG)
        self.log.symlink_to(target)
        self.identity.after_cleanup()
        self.assertEqual(self.identity.result()['renderer_log_claim']['status'], 'symlink')

    def test_log_parent_symlink_rejected(self):
        self.identity.before_gateway()
        target = self.root / 'private_elsewhere'
        target.mkdir()
        (target / 'rendering').mkdir()
        (target / 'rendering/ogre.log').write_bytes(LOG)
        (self.home / '.ignition').symlink_to(target)
        self.identity.after_cleanup()
        self.assertEqual(self.identity.result()['renderer_log_claim']['status'], 'symlink')

    def test_truncated_log_never_claims_identity(self):
        out = self.finish(LOG + b'x' * ri.LOG_BYTES)
        self.assertEqual(out['renderer_log_claim']['status'], 'truncated')
        self.assertTrue(out['renderer_log_claim']['truncated'])
        self.assertIsNone(out['renderer_log_claim']['gl_api_version'])
        self.assertEqual(out['renderer_log_claim']['renderer_family'], 'unavailable')

    def test_missing_conflicting_invalid_log_markers(self):
        for data, status in ((b'private unrelated text', 'markers_missing'),
                (LOG + b'GL_RENDERER = softpipe\n', 'conflicting'),
                (LOG + b'GL_VENDOR = NVIDIA Corporation\n', 'conflicting'),
                (LOG + b'GL_VERSION = 4.6\n', 'conflicting'),
                (LOG + b'GL_RENDERER = llvmpipe (other detail)\n', 'conflicting'),
                (b'GL_VERSION = secret-version\n', 'invalid'),
                (b'GL_VERSION = 4.5/secret\n', 'invalid'),
                (b'GL_VERSION = 9999.5\n', 'invalid'),
                (b'GL_VENDOR = ' + b'x' * 1025, 'invalid'),
                (b'\xff', 'invalid')):
            with self.subTest(status=status, data=data[:40]):
                self.assertEqual(ri._parse_log(data)['status'], status)

    def test_repeat_identical_markers_are_not_conflicts(self):
        self.assertEqual(ri._parse_log(LOG + LOG)['status'], 'available')

    def test_unknown_vendor_and_renderer_are_finite(self):
        out = self.finish(b'GL_VERSION = 4.6\nGL_VENDOR = private host name\nGL_RENDERER = secret GPU serial\n')
        self.assertEqual(out['renderer_log_claim']['vendor_family'], 'unknown')
        self.assertEqual(out['renderer_log_claim']['renderer_family'], 'unknown')
        self.assertNotIn('private host', json.dumps(out))
        self.assertNotIn('GPU serial', json.dumps(out))

    def test_file_hash_is_exact_and_no_content_or_paths_public(self):
        out = self.finish()
        row = out['installed_file_bytes']['files'][0]
        self.assertEqual(row['sha256'], hashlib.sha256(b'private-file-' + ri.FILE_IDS[0].encode()).hexdigest())
        encoded = json.dumps(out)
        for hidden in (str(self.root), 'private-file', 'LLVM', 'Mesa 23.2.1', 'GL_RENDERER', '/opt/ros'):
            self.assertNotIn(hidden, encoded)

    def test_file_missing_and_oversized(self):
        first = self.share / ri.FILES[0][1]
        second = self.share / ri.FILES[1][1]
        first.unlink()
        second.write_bytes(b'x' * (ri.FILE_BYTES + 1))
        out = self.finish()['installed_file_bytes']
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(out['files'][0]['status'], 'missing')
        self.assertEqual(out['files'][1]['status'], 'too_large')
        self.assertIsNone(out['files'][1]['sha256'])

    def test_file_symlink_parent_type_and_directory_rejected(self):
        first = self.share / ri.FILES[0][1]
        first.unlink()
        first.symlink_to(self.share / ri.FILES[1][1])
        self.assertEqual(ri._read(first, maximum=ri.FILE_BYTES)['status'], 'symlink')
        self.assertEqual(ri._read(first.parent, maximum=ri.FILE_BYTES)['status'], 'not_regular')
        plain = self.root / 'plain'
        plain.write_text('x')
        self.assertEqual(ri._read(plain / 'child', maximum=8)['status'], 'parent_not_directory')

    def test_special_file_never_opened(self):
        path = self.root / 'mock_special_leaf'
        original = ri.os.stat
        def fake_stat(value, *args, **kwargs):
            if value == path.name and kwargs.get('dir_fd') is not None:
                return SimpleNamespace(st_mode=stat.S_IFIFO | 0o600)
            return original(value, *args, **kwargs)
        with patch.object(ri.os, 'stat', side_effect=fake_stat), patch.object(ri.os, 'pread', side_effect=AssertionError('must not read')):
            self.assertEqual(ri._read(path, maximum=8)['status'], 'not_regular')

    def test_permission_and_invalid_paths(self):
        with patch.object(ri.os, 'open', side_effect=PermissionError(errno.EACCES, 'secret')):
            self.assertEqual(ri._read(self.log, maximum=8)['status'], 'permission_denied')
        self.assertEqual(ri._read(Path('relative'), maximum=8)['status'], 'invalid_path')
        self.assertEqual(ri._read(self.root / '../secret', maximum=8)['status'], 'invalid_path')

    def test_owner_mismatch_without_read(self):
        path = self.share / ri.FILES[0][1]
        original = ri.os.stat
        def wrong_owner(name, *args, **kwargs):
            info = original(name, *args, **kwargs)
            if name == path.name:
                items = list(info)
                items[4] = os.geteuid() + 10001
                return os.stat_result(items)
            return info
        with patch.object(ri.os, 'stat', side_effect=wrong_owner), patch.object(ri.os, 'pread', side_effect=AssertionError('must not read')):
            self.assertEqual(ri._read(path, maximum=100)['status'], 'owner_mismatch')

    def test_owner_mismatch_parent(self):
        with patch.object(ri.os, 'geteuid', return_value=987654):
            self.assertEqual(ri._read(self.log, maximum=8)['status'], 'owner_mismatch')

    def test_content_change_during_read(self):
        path = self.share / ri.FILES[0][1]
        original = ri.os.pread
        def change(fd, amount, offset):
            result = original(fd, amount, offset)
            path.write_bytes(b'changed-content')
            return result
        with patch.object(ri.os, 'pread', side_effect=change):
            self.assertEqual(ri._read(path, maximum=100)['status'], 'changed')

    def test_path_replacement_during_read(self):
        path = self.share / ri.FILES[0][1]
        original = ri.os.pread
        def change(fd, amount, offset):
            result = original(fd, amount, offset)
            path.rename(path.with_suffix('.old'))
            path.write_bytes(result)
            return result
        with patch.object(ri.os, 'pread', side_effect=change):
            self.assertEqual(ri._read(path, maximum=100)['status'], 'changed')

    def test_parent_replacement_during_read(self):
        path = self.share / ri.FILES[0][1]
        original = ri.os.pread
        def change(fd, amount, offset):
            result = original(fd, amount, offset)
            path.parent.rename(path.parent.with_name('old'))
            path.parent.mkdir()
            path.write_bytes(result)
            return result
        with patch.object(ri.os, 'pread', side_effect=change):
            self.assertEqual(ri._read(path, maximum=100)['status'], 'changed')

    def test_read_volume_cap(self):
        self.write_log(LOG + b'x' * (ri.LOG_BYTES * 5))
        original = ri.os.pread
        count = []
        def read(fd, amount, offset):
            count.append(amount)
            return original(fd, amount, offset)
        with patch.object(ri.os, 'pread', side_effect=read):
            out = ri._read(self.log, maximum=ri.LOG_BYTES, head_tail=True, owned_below=self.home)
        self.assertEqual(out['status'], 'truncated')
        self.assertLessEqual(sum(count), 131072)

    def test_cancellations_before_gateway_propagate_exactly_and_no_retry(self):
        for error in (RuntimeError('interrupted'), KeyboardInterrupt(), SystemExit(2), GeneratorExit()):
            with self.subTest(error=type(error).__name__):
                runner = Mock(side_effect=error)
                identity = ri.RuntimeIdentity(self.private, runner=runner)
                try:
                    identity.before_gateway()
                except BaseException as actual:
                    self.assertIs(actual, error)
                else:
                    self.fail('cancellation swallowed')
                identity.before_gateway()
                self.assertEqual(runner.call_count, 1)

    def test_cancellation_after_cleanup_preserved(self):
        self.identity.before_gateway()
        error = RuntimeError('interrupted')
        with patch.object(ri, '_read', side_effect=error):
            try:
                self.identity.after_cleanup()
            except BaseException as actual:
                self.assertIs(actual, error)
            else:
                self.fail('cancellation swallowed')

    def test_file_close_cancellation_precedence(self):
        path = self.share / ri.FILES[0][1]
        close = ri.os.close
        primary = RuntimeError('interrupted')
        def bad_close(fd):
            close(fd)
            raise ValueError('ordinary cleanup error')
        with patch.object(ri.os, 'pread', side_effect=primary), patch.object(ri.os, 'close', side_effect=bad_close):
            try:
                ri._read(path, maximum=100)
            except BaseException as actual:
                self.assertIs(actual, primary)
            else:
                self.fail('cancellation swallowed')

    def test_new_close_cancellation_overrides_ordinary_error(self):
        path = self.share / ri.FILES[0][1]
        close = ri.os.close
        cancellation = KeyboardInterrupt()
        def bad_close(fd):
            close(fd)
            raise cancellation
        with patch.object(ri.os, 'pread', side_effect=ValueError('ordinary')), patch.object(ri.os, 'close', side_effect=bad_close):
            try:
                ri._read(path, maximum=100)
            except BaseException as actual:
                self.assertIs(actual, cancellation)
            else:
                self.fail('cancellation swallowed')

    def test_enter_close_does_not_replace_cancellation(self):
        original = ri.os.open
        close = ri.os.close
        error = RuntimeError('interrupted')
        def opening(name, *args, **kwargs):
            if name == 'home':
                raise error
            return original(name, *args, **kwargs)
        def closing(fd):
            close(fd)
            raise OSError('close failure')
        with patch.object(ri.os, 'open', side_effect=opening), patch.object(ri.os, 'close', side_effect=closing):
            try:
                ri._home_identity(self.home)
            except BaseException as actual:
                self.assertIs(actual, error)
            else:
                self.fail('cancellation swallowed')


    def test_retained_child_gets_one_bounded_final_cleanup(self):
        proc = FakeProcess()
        self.identity._on_child(proc)
        self.identity.raw['query_cleanup_status'] = 'uncertain'
        self.identity.cleanup_query_child()
        self.assertEqual(proc.kill_calls, 1)
        self.assertEqual(proc.wait_calls, [1.0])
        self.assertEqual(self.identity.result()['query_cleanup_status'], 'reaped')
        self.assertIsNone(self.identity._query_child)
        self.identity.cleanup_query_child()
        self.assertEqual(proc.kill_calls, 1)

    def test_persistent_cleanup_uncertainty_is_explicit_and_bounded(self):
        proc = FakeProcess()
        proc.kill = Mock(side_effect=PermissionError('denied'))
        self.identity._on_child(proc)
        for _ in range(2):
            with self.assertRaises(ri.QueryCleanupUncertain):
                self.identity.cleanup_query_child()
        self.assertEqual(proc.kill.call_count, 1)
        self.assertEqual(proc.wait_calls, [1.0])
        self.assertIs(self.identity._query_child, proc)
        self.assertEqual(self.identity.result()['query_cleanup_status'], 'uncertain')

    def test_final_cleanup_preserves_exact_cancellation(self):
        proc = FakeProcess()
        error = RuntimeError('interrupted')
        proc.kill = Mock(side_effect=error)
        self.identity._on_child(proc)
        try:
            self.identity.cleanup_query_child()
        except BaseException as actual:
            self.assertIs(actual, error)
        else:
            self.fail('cancellation swallowed')
        self.assertEqual(proc.wait_calls, [1.0])

    def test_capture_callbacks_retain_handle_on_cancel_uncertain(self):
        proc = FakeProcess()
        error = KeyboardInterrupt()
        proc.kill = Mock(side_effect=PermissionError('denied'))
        with patch.object(ri.os, 'set_blocking', side_effect=error):
            try:
                ri._bounded_capture(ri.QUERY_ARGV, popen=Mock(return_value=proc),
                    on_child=self.identity._on_child, on_reaped=self.identity._on_reaped)
            except BaseException as actual:
                self.assertIs(actual, error)
            else:
                self.fail('cancellation swallowed')
        self.assertIs(self.identity._query_child, proc)
        proc.kill = lambda: setattr(proc, 'code', -9)
        self.identity.cleanup_query_child()
        self.assertEqual(self.identity.result()['query_cleanup_status'], 'reaped')

    def test_preflight_cleanup_uncertainty_is_sticky_after_final_reap(self):
        proc = FakeProcess()
        identity = ri.RuntimeIdentity(self.private)
        error = ri.QueryCleanupUncertain('metadata_query_cleanup_uncertain')
        def capture(argv, *, timeout, max_bytes, on_child, on_reaped):
            on_child(proc)
            raise error
        with patch.object(ri, '_bounded_capture', side_effect=capture):
            try:
                identity.before_gateway()
            except BaseException as actual:
                self.assertIs(actual, error)
            else:
                self.fail('containment uncertainty swallowed')
        self.assertEqual(identity.result()['query_cleanup_status'], 'uncertain')
        self.assertTrue(identity.result()['query_cleanup_was_uncertain'])
        identity.cleanup_query_child()
        out = identity.result()
        self.assertEqual(out['query_cleanup_status'], 'reaped')
        self.assertTrue(out['query_cleanup_was_uncertain'])
        self.assertEqual(ri.sanitize(out), out)

    def test_only_source_grounded_renderer_format_is_recognized(self):
        for value in ('llvmpipe secret', 'llvmpipe', 'llvmpipe (secret)',
                      'zink', 'NVIDIA Corporation', 'llvmpipe (LLVM 15.0.7, secret bits)'):
            result = ri._parse_log(('GL_RENDERER = ' + value + '\n').encode())
            self.assertEqual(result['renderer_family'], 'unknown')
        self.assertEqual(ri._parse_log(b'GL_RENDERER = llvmpipe (LLVM 15.0.7, 256 bits)\n')['renderer_family'], 'llvmpipe')

    def test_softpipe_renderer_requires_exact_name(self):
        for value in (b'prefix-softpipe', b'softpipe-suffix', b' softpipe', b'softpipe ',
                      b'Softpipe', b'SOFTPIPE', b'softPipe',
                      b'softpipe (LLVM 15.0.7, 256 bits)',
                      b'softpipe /private/host?token=private-token',
                      b'/private/host/softpipe', b'softpipeprivate-token'):
            with self.subTest(value=value):
                result = ri._parse_log(SOFTPIPE_LOG.replace(b'softpipe', value))
                self.assertEqual(result, {
                    'status': 'available', 'gl_api_version': '4.5.0',
                    'vendor_family': 'mesa', 'renderer_family': 'unknown'})
                out = ri.sanitize({'renderer_log_claim': dict(result,
                    attribution='invocation_associated_not_pid_bound', truncated=False)})
                self.assertEqual(out['renderer_log_claim']['renderer_family'], 'unknown')
                self.assertNotIn('private-token', json.dumps(out))
                self.assertNotIn('/private/host', json.dumps(out))
                self.assertEqual(ri.sanitize(out), out)

    def test_softpipe_control_or_non_ascii_name_is_invalid(self):
        for value in (b'\x00softpipe', b'softpipe\x00', b'soft\tpipe',
                      b'softpipe\x1b[0m', b'softpipe\x7f', 'softpipe秘密'.encode()):
            with self.subTest(value=value):
                parsed = ri._parse_log(SOFTPIPE_LOG.replace(b'softpipe', value))
                self.assertEqual(parsed, {'status': 'invalid'})
                out = ri.sanitize({'renderer_log_claim': dict(parsed,
                    attribution='invocation_associated_not_pid_bound', truncated=False)})
                self.assertEqual(out['renderer_log_claim']['status'], 'invalid')
                self.assertIsNone(out['renderer_log_claim']['gl_api_version'])
                self.assertEqual(out['renderer_log_claim']['vendor_family'], 'unavailable')
                self.assertEqual(out['renderer_log_claim']['renderer_family'], 'unavailable')
                self.assertEqual(ri.sanitize(out), out)

    def test_renderer_markers_accept_lf_crlf_or_unterminated_final_line(self):
        for log, family in ((LOG, 'llvmpipe'), (SOFTPIPE_LOG, 'softpipe')):
            for ending in (b'\n', b'\r\n', None):
                with self.subTest(family=family, ending=ending):
                    data = log[:-1] if ending is None else log.replace(b'\n', ending)
                    parsed = ri._parse_log(data)
                    self.assertEqual(parsed, {
                        'status': 'available', 'gl_api_version': '4.5.0',
                        'vendor_family': 'mesa', 'renderer_family': family})
                    out = ri.sanitize({'renderer_log_claim': dict(parsed,
                        attribution='invocation_associated_not_pid_bound', truncated=False)})
                    self.assertEqual(out['renderer_log_claim']['status'], 'available')
                    self.assertEqual(out['renderer_log_claim']['renderer_family'], family)
                    self.assertEqual(out['renderer_log_claim']['vendor_family'], 'mesa')
                    self.assertEqual(out['renderer_log_claim']['gl_api_version'], '4.5.0')
                    self.assertEqual(ri.sanitize(out), out)

    def test_embedded_line_separators_cannot_hide_private_renderer_suffix(self):
        secret = b'/private/host?token=private-token'
        separators = (b'\x0b', b'\x0c', b'\x1c', b'\x1d', b'\x1e', b'\r',
                      '\u0085'.encode(), '\u2028'.encode(), '\u2029'.encode())
        for log, family in ((LOG, 'llvmpipe'), (SOFTPIPE_LOG, 'softpipe')):
            for separator in separators:
                with self.subTest(family=family, separator=separator):
                    data = log[:-1] + separator + secret + b'\n'
                    parsed = ri._parse_log(data)
                    self.assertEqual(parsed, {'status': 'invalid'})
                    out = ri.sanitize({'renderer_log_claim': dict(parsed,
                        attribution='invocation_associated_not_pid_bound',
                        truncated=False, raw_log=data, path=secret.decode())})
                    claim = out['renderer_log_claim']
                    self.assertEqual(claim['status'], 'invalid')
                    self.assertIsNone(claim['gl_api_version'])
                    self.assertEqual(claim['vendor_family'], 'unavailable')
                    self.assertEqual(claim['renderer_family'], 'unavailable')
                    self.assertNotIn(secret.decode(), json.dumps(out))
                    self.assertNotIn('raw_log', json.dumps(out))
                    self.assertEqual(ri.sanitize(out), out)

    def test_renderer_crlf_normalization_strips_only_one_terminal_cr(self):
        for log, family in ((LOG, 'llvmpipe'), (SOFTPIPE_LOG, 'softpipe')):
            for ending in (b'\r', b'\r\r\n'):
                with self.subTest(family=family, ending=ending):
                    self.assertEqual(ri._parse_log(log[:-1] + ending), {'status': 'invalid'})

    def test_softpipe_mixed_renderer_markers_conflict_in_either_order(self):
        for data in (LOG + SOFTPIPE_LOG, SOFTPIPE_LOG + LOG):
            with self.subTest(data=data):
                parsed = ri._parse_log(data)
                self.assertEqual(parsed, {'status': 'conflicting'})
                out = ri.sanitize({'renderer_log_claim': dict(parsed,
                    attribution='invocation_associated_not_pid_bound', truncated=False)})
                self.assertEqual(out['renderer_log_claim']['status'], 'conflicting')
                self.assertIsNone(out['renderer_log_claim']['gl_api_version'])
                self.assertEqual(out['renderer_log_claim']['vendor_family'], 'unavailable')
                self.assertEqual(out['renderer_log_claim']['renderer_family'], 'unavailable')
                self.assertEqual(ri.sanitize(out), out)

    def test_duplicate_exact_softpipe_markers_remain_available(self):
        expected = ri._parse_log(SOFTPIPE_LOG)
        self.assertEqual(expected['renderer_family'], 'softpipe')
        self.assertEqual(ri._parse_log(SOFTPIPE_LOG + SOFTPIPE_LOG), expected)
        self.assertEqual(ri._parse_log(SOFTPIPE_LOG + b'GL_RENDERER = softpipe\n'), expected)
        out = self.finish(SOFTPIPE_LOG + SOFTPIPE_LOG)
        self.assertEqual(out['renderer_log_claim']['status'], 'available')
        self.assertEqual(out['renderer_log_claim']['renderer_family'], 'softpipe')
        self.assertEqual(ri.sanitize(out), out)

    def test_softpipe_marker_alone_remains_partial_without_inferred_vendor_or_api(self):
        out = self.finish(b'GL_RENDERER = softpipe\n')['renderer_log_claim']
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(out['renderer_family'], 'softpipe')
        self.assertIsNone(out['gl_api_version'])
        self.assertEqual(out['vendor_family'], 'unavailable')
        self.assertEqual(out['mesa_version'], 'unavailable')
        self.assertEqual(out['ogre_version'], 'unavailable')


class ProjectionTests(unittest.TestCase):
    def test_unavailable_plain_closed_idempotent(self):
        self.assertIs(type(ri.UNAVAILABLE), dict)
        self.assertEqual(ri.sanitize(None), ri.UNAVAILABLE)
        self.assertEqual(ri.sanitize(ri.UNAVAILABLE), ri.UNAVAILABLE)
        self.assertLess(len(json.dumps(ri.UNAVAILABLE)), 15000)

    def test_worst_case_projection_has_fixed_byte_bound(self):
        version='1' * 96
        longest=max(ri.PACKAGES,key=len)
        raw={'status':'complete','query_cleanup_status':'not_observed','query_cleanup_was_uncertain':True,
             'package_database':{'status':'complete','packages':[
                 {'id':name,'status':'available','architecture':'ppc64el','version':version,
                  'source_package':longest,'source_version':version} for name in ri.PACKAGES]},
             'installed_file_bytes':{'status':'complete','files':[
                 {'id':name,'status':'available','size_bytes':ri.FILE_BYTES,'sha256':'f'*64} for name in ri.FILE_IDS]},
             'renderer_log_claim':{'status':'available','attribution':'invocation_associated_not_pid_bound',
                 'truncated':False,'gl_api_version':'999.999.999.999','vendor_family':'unavailable','renderer_family':'unavailable'}}
        out=ri.sanitize(raw)
        self.assertEqual(ri.sanitize(out),out)
        self.assertLess(len(json.dumps(out,allow_nan=False).encode()),16384)

    def test_catalogue_bounds_exact_query(self):
        self.assertLessEqual(len(ri.PACKAGES), 24)
        self.assertLessEqual(len(ri.FILES), 16)
        self.assertEqual(len(set(ri.PACKAGES)), len(ri.PACKAGES))
        self.assertEqual(ri.QUERY_ARGV[:3], ('/usr/bin/dpkg-query', '--admindir=/var/lib/dpkg', '-W'))
        self.assertEqual(ri.QUERY_ARGV[4:], ri.PACKAGES)
        self.assertNotIn('*', ''.join(ri.QUERY_ARGV))
        self.assertTrue(all(not Path(relative).is_absolute() and '..' not in Path(relative).parts for _, relative in ri.FILES))

    def test_malformed_versions_and_unknown_sources(self):
        base = {'id': ri.PACKAGES[0], 'status': 'available', 'version': '23.2.1-1ubuntu3', 'architecture': 'amd64', 'source_package': 'mesa', 'source_version': '23.2.1-1ubuntu3'}
        for version in ('../../secret', 'abc', '1\nsecret', '1_secret', '1:2:3', '1-', '1.0-rev-?', '1' * 97, None, True):
            raw = {'package_database': {'status': 'complete', 'packages': [dict(base, version=version)]}}
            out = ri.sanitize(raw)
            self.assertEqual(out['package_database']['packages'][0]['status'], 'invalid')
            self.assertEqual(ri.sanitize(out), out)
        raw = {'package_database': {'status': 'complete', 'packages': [dict(base, source_package='secret-host')]}}
        row = ri.sanitize(raw)['package_database']['packages'][0]
        self.assertEqual(row['status'], 'available')
        self.assertIsNone(row['source_package'])
        self.assertEqual(row['source_status'], 'unavailable')

    def test_duplicate_rows_conflict_and_oversized_lists_drop(self):
        for key, name, rows_key in (('package_database', ri.PACKAGES[0], 'packages'), ('installed_file_bytes', ri.FILE_IDS[0], 'files')):
            row = {'id': name, 'status': 'available', 'version': '1.0', 'architecture': 'amd64', 'size_bytes': 1, 'sha256': 'a' * 64}
            out = ri.sanitize({key: {'status': 'complete', rows_key: [row, row]}})
            self.assertEqual(out[key][rows_key][0]['status'], 'conflicting')
            self.assertEqual(ri.sanitize(out), out)
            out = ri.sanitize({key: {'status': 'complete', rows_key: [row] * 1000}})
            self.assertEqual(out[key]['status'], 'unavailable')

    def test_projection_rejects_raw_content_paths_and_loaded_claim(self):
        secret = '/secret/host?token=secret'
        raw = {'secret': secret, 'loaded_library_identity': {'status': 'available', 'path': secret},
            'package_database': {'packages': [{'id': secret, 'version': secret}]},
            'installed_file_bytes': {'files': [{'id': secret, 'sha256': secret}]},
            'renderer_log_claim': {'status': secret, 'raw_log': secret, 'gl_api_version': secret,
                'vendor_family': secret, 'renderer_family': secret, 'attribution': secret}}
        out = ri.sanitize(raw)
        self.assertNotIn(secret, json.dumps(out))
        self.assertEqual(out['loaded_library_identity']['status'], 'unavailable')
        self.assertEqual(ri.sanitize(out), out)

    def test_log_projection_crossfield_trust_and_truncation(self):
        raw = {'status': 'available', 'attribution': 'invocation_associated_not_pid_bound',
            'truncated': False, 'gl_api_version': '4.5', 'vendor_family': 'mesa', 'renderer_family': 'llvmpipe'}
        for change in ({'attribution': 'pid_bound'}, {'truncated': True}, {'status': 'conflicting'}, {'status': 'preexisting'}):
            out = ri.sanitize({'renderer_log_claim': dict(raw, **change)})
            self.assertIsNone(out['renderer_log_claim']['gl_api_version'])
            self.assertEqual(out['renderer_log_claim']['renderer_family'], 'unavailable')
            self.assertEqual(ri.sanitize(out), out)

    def test_softpipe_projection_preserves_finite_identity_and_drops_private_junk(self):
        secret = '/private/host?token=private-token'
        raw = {'secret': secret, 'renderer_log_claim': {
            'status': 'available', 'attribution': 'invocation_associated_not_pid_bound',
            'truncated': False, 'gl_api_version': '4.5.0', 'vendor_family': 'mesa',
            'renderer_family': 'softpipe', 'raw_log': secret, 'path': secret,
            'mesa_version': '23.2.1', 'ogre_version': '1.9.0'},
            'loaded_library_identity': {'status': 'available', 'path': secret}}
        original = copy.deepcopy(raw)
        out = ri.sanitize(raw)
        self.assertEqual(raw, original)
        self.assertEqual(out['renderer_log_claim'], {
            'status': 'available', 'attribution': 'invocation_associated_not_pid_bound',
            'truncated': False, 'gl_api_version': '4.5.0', 'vendor_family': 'mesa',
            'renderer_family': 'softpipe', 'mesa_version': 'unavailable',
            'ogre_version': 'unavailable'})
        self.assertEqual(out['loaded_library_identity'], {
            'status': 'unavailable', 'reason': 'not_observed'})
        for hidden in (secret, 'raw_log', '"23.2.1"', '"1.9.0"'):
            self.assertNotIn(hidden, json.dumps(out))
        self.assertEqual(ri.sanitize(out), out)

    def test_softpipe_projection_cannot_bypass_unavailable_or_untrusted_status(self):
        raw = {'status': 'available', 'attribution': 'invocation_associated_not_pid_bound',
            'truncated': False, 'gl_api_version': '4.5.0',
            'vendor_family': 'mesa', 'renderer_family': 'softpipe'}
        changes = [{'status': status} for status in (
            'unavailable', 'missing', 'not_collected', 'preflight_failed',
            'preexisting', 'truncated', 'invalid', 'conflicting', 'changed',
            'owner_mismatch', 'symlink', 'permission_denied', 'markers_missing')]
        changes += [{'truncated': True}, {'truncated': None},
                    {'attribution': 'unavailable'}, {'attribution': 'pid_bound'}]
        for change in changes:
            with self.subTest(change=change):
                out = ri.sanitize({'renderer_log_claim': dict(raw, **change)})
                claim = out['renderer_log_claim']
                self.assertIsNone(claim['gl_api_version'])
                self.assertEqual(claim['vendor_family'], 'unavailable')
                self.assertEqual(claim['renderer_family'], 'unavailable')
                self.assertEqual(claim['mesa_version'], 'unavailable')
                self.assertEqual(claim['ogre_version'], 'unavailable')
                self.assertEqual(out['loaded_library_identity']['status'], 'unavailable')
                self.assertEqual(ri.sanitize(out), out)

    def test_nested_weird_types_are_safe(self):
        for raw in (None, 3, 'x', [], {'package_database': []}, {'installed_file_bytes': 'x'},
                    {'renderer_log_claim': {'attribution': [], 'status': {}, 'vendor_family': 'x' * 1000000}},
                    {'package_database': {'status': [], 'packages': [None, [], {'id': []}]}}):
            out = ri.sanitize(raw)
            self.assertEqual(ri.sanitize(out), out)

    def test_package_parsing_missing_unknown_and_conflict(self):
        one = (ri.PACKAGES[0] + '\tamd64\t23.2.1-1\tinstalled\tmesa\t23.2.1-1\n').encode()
        for data, status, row_status in ((b'', 'partial', 'missing'),
                (one, 'partial', 'available'), (one + one, 'invalid', 'conflicting'),
                (one + b'secret-host\tamd64\t1\tinstalled\tx\t1\n', 'invalid', 'available'),
                (ri.PACKAGES[0].encode() + b'\tmalformed\n', 'invalid', 'invalid')):
            with self.subTest(status=status, row_status=row_status):
                out = ri._package_metadata(lambda *a, **kw: query(data))
                self.assertEqual(out['status'], status)
                self.assertEqual(out['packages'][0]['status'], row_status)

    def test_package_caps_binary_unknown_status_and_not_installed(self):
        for result, status in ((query(b'x' * (ri.PACKAGE_BYTES + 1)), 'output_limit'),
                (query(b'\xff'), 'invalid'), (query(b'\n' * 25), 'invalid'),
                ({'status': 'secret', 'stdout': b''}, 'unavailable'),
                (query('not-bytes'), 'invalid'), (None, 'invalid')):
            self.assertEqual(ri._package_metadata(lambda *a, **kw: result)['status'], status)
        data = (ri.PACKAGES[0] + '\tamd64\t1.0\tconfig-files\tmesa\t1.0\n').encode()
        row = ri._package_metadata(lambda *a, **kw: query(data))['packages'][0]
        self.assertEqual(row['status'], 'not_installed')


class FakeProcess:
    def __init__(self, code=None):
        self.stdout = Mock()
        self.stdout.fileno.return_value = 101
        self.code = code
        self.kill_calls = 0
        self.wait_calls = []
    def poll(self):
        return self.code
    def kill(self):
        self.kill_calls += 1
        self.code = -9
    def wait(self, timeout):
        self.wait_calls.append(timeout)
        if self.code is None:
            raise subprocess.TimeoutExpired('fixed', timeout)
        return self.code


class CaptureTests(unittest.TestCase):
    def capture(self, proc, chunks, times=None, selector=None):
        popen = Mock(return_value=proc)
        selector = Mock() if selector is None else selector
        selector.select.return_value = [(SimpleNamespace(fileobj=proc.stdout), 1)]
        clock = Mock(side_effect=times) if times is not None else Mock(return_value=0)
        with patch.object(ri.os, 'set_blocking'), patch.object(ri.os, 'read', side_effect=chunks):
            result = ri._bounded_capture(ri.QUERY_ARGV, popen=popen, selector_factory=lambda: selector, clock=clock)
        return result, popen, selector

    def test_normal_capture_exact_command_no_shell_minimal_env(self):
        proc = FakeProcess(0)
        result, popen, selector = self.capture(proc, [b'one', b''])
        self.assertEqual(result['stdout'], b'one')
        self.assertEqual(result['status'], 'complete')
        args, kw = popen.call_args
        self.assertEqual(args, (list(ri.QUERY_ARGV),))
        self.assertFalse(kw['shell'])
        self.assertEqual(kw['env'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        self.assertEqual(kw['cwd'], '/')
        self.assertEqual(proc.kill_calls, 0)
        self.assertEqual(proc.wait_calls, [1.0])
        selector.close.assert_called_once()
        proc.stdout.close.assert_called_once()

    def test_timeout_kills_reaps_and_closes(self):
        proc = FakeProcess()
        result, _, selector = self.capture(proc, [], times=[0, 4])
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['stdout'], b'')
        self.assertEqual(proc.kill_calls, 1)
        self.assertEqual(proc.wait_calls, [1.0])
        selector.close.assert_called_once()
        proc.stdout.close.assert_called_once()

    def test_overflow_kills_reaps_without_unbounded_communicate(self):
        proc = FakeProcess()
        result, _, selector = self.capture(proc, [b'x' * 4096] * 16 + [b'x'])
        self.assertEqual(result['status'], 'output_limit')
        self.assertEqual(proc.kill_calls, 1)
        self.assertEqual(proc.wait_calls, [1.0])
        selector.close.assert_called_once()
        proc.stdout.close.assert_called_once()

    def test_cancellation_exact_object_and_cleanup_all_attempted(self):
        for error in (RuntimeError('interrupted'), KeyboardInterrupt(), SystemExit(3), GeneratorExit()):
            proc = FakeProcess()
            selector = Mock()
            with self.subTest(error=type(error).__name__):
                try:
                    self.capture(proc, [error], selector=selector)
                except BaseException as actual:
                    self.assertIs(actual, error)
                else:
                    self.fail('cancellation swallowed')
                self.assertEqual(proc.kill_calls, 1)
                self.assertEqual(proc.wait_calls, [1.0])
                selector.close.assert_called_once()
                proc.stdout.close.assert_called_once()

    def test_later_cleanup_cancellation_overrides_ordinary_failure(self):
        proc = FakeProcess()
        error = RuntimeError('interrupted')
        selector = Mock()
        selector.close.side_effect = error
        try:
            self.capture(proc, [ValueError('ordinary')], selector=selector)
        except BaseException as actual:
            self.assertIs(actual, error)
        else:
            self.fail('cancellation swallowed')
        self.assertEqual(proc.kill_calls, 1)
        self.assertEqual(proc.wait_calls, [1.0])
        proc.stdout.close.assert_called_once()

    def test_original_cancellation_outranks_cleanup_failure(self):
        proc = FakeProcess()
        original = KeyboardInterrupt()
        proc.wait = Mock(side_effect=ValueError('reap failed'))
        try:
            self.capture(proc, [original])
        except BaseException as actual:
            self.assertIs(actual, original)
        else:
            self.fail('cancellation swallowed')
        self.assertEqual(proc.kill_calls, 1)
        proc.stdout.close.assert_called_once()

    def test_kill_or_reap_failure_never_reports_success(self):
        proc = FakeProcess()
        error = PermissionError('kill failed')
        proc.kill = Mock(side_effect=error)
        try:
            self.capture(proc, [], times=[0, 4])
        except BaseException as actual:
            self.assertIsInstance(actual, ri.QueryCleanupUncertain)
        else:
            self.fail('cleanup uncertainty hidden')
        proc.kill.assert_called_once()
        self.assertEqual(proc.wait_calls, [1.0])
        proc.stdout.close.assert_called_once()

    def test_setup_failure_also_kills_and_reaps(self):
        proc = FakeProcess()
        error = RuntimeError('interrupted')
        with patch.object(ri.os, 'set_blocking', side_effect=error):
            try:
                ri._bounded_capture(ri.QUERY_ARGV, popen=Mock(return_value=proc))
            except BaseException as actual:
                self.assertIs(actual, error)
            else:
                self.fail('cancellation swallowed')
        self.assertEqual(proc.kill_calls, 1)
        self.assertEqual(proc.wait_calls, [1.0])
        proc.stdout.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()

"""Stdlib mocked fixtures only: never execute namespaces, helpers or mount/chown."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / 'probe_namespace_capabilities.py'
spec = importlib.util.spec_from_file_location('namespace_probe_under_test', MODULE_PATH)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def texts():
    return {
        '/etc/passwd': 'root:x:0:0:root:/root:/bin/sh\nworker:x:1001:1002::/home/worker:/bin/sh\n',
        '/etc/group': 'root:x:0:\nworker:x:1002:\n',
        '/etc/nsswitch.conf': 'passwd: files systemd\ngroup: files systemd\nshadow: files\n',
        '/etc/subuid': 'worker:100000:65536\n',
        '/etc/subgid': 'worker:200000:65536\n',
    }


def host():
    return {'uid': 1001, 'gid': 1002, 'groups': [1002], 'loginuid': '4294967295'}


def context():
    return {'protocol': 1, 'deadline': 100.0, 'tool_fds': {'python': 21, 'unshare': 22, 'newuidmap': 23, 'newgidmap': 24},
            'script_fd': 25, 'host_net_fd': 30, 'pycache_prefix': '/private/unused-pycache',
            'fixture': '/private/.fixture', 'baseline': {'net': [1, 10], 'mount': [1, 11], 'user': [1, 12], 'boot': 'boot'},
            'maps': {'uid': [[0, 1001, 1], [1, 100000, 65535]], 'gid': [[0, 1002, 1], [1, 200000, 65535]]},
            'mapped_user': [1, 20], 'outer_mount': [1, 21]}


class NoNativeCase(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for target in ('subprocess.Popen', 'os.execve', 'os.fchown', 'os.setgroups', 'os.pidfd_open', 'os.killpg', 'socket.socket', 'fcntl.ioctl'):
            self.stack.enter_context(mock.patch.object(getattr(p, target.split('.')[0]), target.split('.')[1],
                                                       side_effect=AssertionError('native execution forbidden in fixtures')))
        self.stack.enter_context(mock.patch.object(p, '_mount_tmpfs', side_effect=AssertionError('mount forbidden in fixtures')))

    def failure(self, code, call, *args):
        with self.assertRaises(p.ProbeFailure) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, code)


class MappingTests(NoNativeCase):
    def test_full_existing_mapping_and_files_systemd(self):
        self.assertEqual(p.select_mappings(texts(), host()), context()['maps'])

    def test_65535_suffices_but_65534_does_not(self):
        data = texts(); data['/etc/subuid'] = 'worker:100000:65535\n'
        self.assertEqual(p.select_mappings(data, host())['uid'][1][2], 65535)
        data['/etc/subuid'] = 'worker:100000:65534\n'
        self.failure('SUBUID_TOO_SHORT', p.select_mappings, data, host())

    def test_gid_short_is_distinct_terminal(self):
        data = texts(); data['/etc/subgid'] = 'worker:200000:1\n'
        self.failure('SUBGID_TOO_SHORT', p.select_mappings, data, host())

    def test_numeric_subordinate_owners_rejected_not_resolved(self):
        for path in ('/etc/subuid', '/etc/subgid'):
            for owner in ('1001', '1002'):
                data = texts(); data[path] = owner + ':200000:65536\n'
                self.failure('SUBID_OWNER_UNSUPPORTED', p.select_mappings, data, host())

    def test_unknown_overlapping_owner_regression_before_valid_runner_grant(self):
        data = {k: v.replace('worker', 'runner') for k, v in texts().items()}
        data['/etc/subuid'] = 'unknown:1001:1\nrunner:100000:65536\n'
        self.failure('SUBID_OWNER_UNSUPPORTED', p.select_mappings, data, host())
        p.subprocess.Popen.assert_not_called()

    def test_unknown_owner_rejected_even_without_overlap(self):
        data = texts(); data['/etc/subgid'] += 'unknown:900000:1\n'
        self.failure('SUBID_OWNER_UNSUPPORTED', p.select_mappings, data, host())

    def test_ambiguous_or_malformed_local_owner_rejected(self):
        for extra in ('worker:x:1003:1002::/:/bin/sh\n',
                      'other:x:1001:1002::/:/bin/sh\n',
                      '1003:x:1003:1002::/:/bin/sh\n',
                      'other:x:4294967295:1002::/:/bin/sh\n',
                      'other:x:01003:1002::/:/bin/sh\n',
                      'other\x00:x:1003:1002::/:/bin/sh\n'):
            data = texts(); data['/etc/passwd'] += extra
            self.failure('CALLER_UNSUPPORTED', p.select_mappings, data, host())

    def test_no_splicing_or_foreign_allocation(self):
        for value, code in (('worker:100000:32768\nworker:132768:32768\n', 'SUBUID_TOO_SHORT'),
                            ('root:100000:65536\n', 'SUBUID_MISSING'), ('', 'SUBUID_MISSING')):
            data = texts(); data['/etc/subuid'] = value
            self.failure(code, p.select_mappings, data, host())

    def test_overflow_zero_and_malformed_allocation_rejected(self):
        for value in ('worker:4294960000:65536', 'worker:0:65536', 'worker:100000:0', 'worker:1:not_a_number'):
            data = texts(); data['/etc/subuid'] = value
            self.failure('SUBID_INVALID', p.select_mappings, data, host())

    def test_no_collision_with_existing_account(self):
        data = texts(); data['/etc/passwd'] += 'service:x:100003:7::/:/bin/false\n'
        self.failure('SUBUID_TOO_SHORT', p.select_mappings, data, host())

    def test_no_collision_with_inherited_supplementary_group(self):
        value = host(); value['groups'].append(200003)
        self.failure('SUBGID_TOO_SHORT', p.select_mappings, texts(), value)

    def test_no_nss_dynamic_fallback_or_return_override(self):
        for value in ('passwd: systemd files\ngroup: files\n',
                      'passwd: files [SUCCESS=continue] systemd\ngroup: files\n',
                      'passwd: files\ngroup: files\nsubid: ldap\n',
                      'passwd: files\npasswd: files\ngroup: files\n',
                      'passwd: files ldap\ngroup: files\n',
                      'passwd: files systemd ldap\ngroup: files\n',
                      'passwd: files\ngroup: files files\n',
                      'passwd : files\npasswd: files\ngroup: files\n'):
            data = texts(); data['/etc/nsswitch.conf'] = value
            self.failure('NSS_UNSUPPORTED', p.select_mappings, data, host())

    def test_exact_files_subid_allowed(self):
        data = texts(); data['/etc/nsswitch.conf'] += 'subid: files\n'
        self.assertEqual(p.select_mappings(data, host()), context()['maps'])

    def test_missing_or_ambiguous_local_caller_rejected(self):
        data = texts(); data['/etc/passwd'] += 'duplicate:x:1001:1002::/:/bin/sh\n'
        self.failure('CALLER_UNSUPPORTED', p.select_mappings, data, host())
        value = host(); value['uid'] = 0
        self.failure('CALLER_UNSUPPORTED', p.select_mappings, texts(), value)


class NSSIdentityTests(NoNativeCase):
    def lock(self):
        path = '/lib/x86_64-linux-gnu/libnss_systemd.so.2'
        return {'host': host(), 'nss': {
            'providers': {'passwd': ['files', 'systemd'], 'group': ['files', 'systemd']},
            'fallback_paths': [path], 'absent_paths': ['/lib/versioned-absent-nss.so']},
            'runtime_files': {path: {'package': 'libnss-systemd'}},
            'runtime_elf_paths': [path], 'absent_paths': [p.NSCD_SOCKET]}

    def test_raw_loginuid_sentinel_and_known_local_uid_only(self):
        for value in ('4294967295', '1001', '0'):
            current = host(); current['loginuid'] = value
            self.assertEqual(p.select_mappings(texts(), current), context()['maps'])
        for value in ('1003', '4294967296', '1001\n', ' 1001', '01001', '-1', 'not-a-uid', ''):
            current = host(); current['loginuid'] = value
            self.failure('LOGINUID_UNSUPPORTED', p.select_mappings, texts(), current)

    def test_raw_loginuid_missing_or_newline_never_uses_strip_fallback(self):
        for raw in (b'', b'1001\n', b'4294967295\n', b'+1001', b'1001\x00', b'\xff'):
            self.failure('LOGINUID_UNSUPPORTED', p._parse_loginuid, raw)
        with mock.patch.object(p, 'Path') as path:
            path.return_value.open.side_effect = FileNotFoundError()
            self.failure('LOGINUID_UNSUPPORTED', p._read_loginuid)
        with mock.patch.object(p, 'Path') as path:
            path.return_value.open.return_value.__enter__.return_value.read.return_value = b'4294967295'
            self.assertEqual(p._read_loginuid(), '4294967295')

    def test_authenticated_nss_fallback_membership_required(self):
        lock = self.lock()
        p._require_nss_runtime(lock, texts())
        for key in ('runtime_files', 'runtime_elf_paths'):
            changed = self.lock(); changed[key] = {} if key == 'runtime_files' else []
            self.failure('LOCK_INVALID', p._require_nss_runtime, changed, texts())
        changed = self.lock(); changed['runtime_files'][changed['nss']['fallback_paths'][0]]['package'] = 'unrelated'
        self.failure('LOCK_INVALID', p._require_nss_runtime, changed, texts())
        changed = self.lock(); changed['nss']['fallback_paths'] = []
        self.failure('LOCK_INVALID', p._require_nss_runtime, changed, texts())
        changed = self.lock(); changed['nss']['providers']['passwd'] = ['files']
        self.failure('LOCK_INVALID', p._require_nss_runtime, changed, texts())
        changed = self.lock(); changed['absent_paths'] = []
        self.failure('LOCK_INVALID', p._require_nss_runtime, changed, texts())

    def test_files_only_requires_no_extra_nss_runtime(self):
        data = texts(); data['/etc/nsswitch.conf'] = 'passwd: files\ngroup: files\n'
        lock = self.lock()
        lock['nss'] = {'providers': {'passwd': ['files'], 'group': ['files']}, 'fallback_paths': [], 'absent_paths': []}
        p._require_nss_runtime(lock, data)
        lock['nss']['fallback_paths'] = ['/lib/x86_64-linux-gnu/libnss_systemd.so.2']
        self.failure('LOCK_INVALID', p._require_nss_runtime, lock, data)

    def test_immediate_guard_blocks_loginuid_change_and_nscd_or_retry_appearance(self):
        lock = self.lock()
        with mock.patch.object(p, '_read_loginuid', return_value='4294967295'), \
             mock.patch.object(p.os.path, 'lexists', return_value=False) as exists:
            p._guard_helper_identity(lock)
            self.assertEqual(exists.call_args_list, [mock.call(p.NSCD_SOCKET), mock.call('/lib/versioned-absent-nss.so')])
        with mock.patch.object(p, '_read_loginuid', return_value='1001'), \
             mock.patch.object(p.os.path, 'lexists', return_value=False):
            self.failure('HOST_CHANGED', p._guard_helper_identity, lock)
        for target in (p.NSCD_SOCKET, '/lib/versioned-absent-nss.so'):
            with mock.patch.object(p, '_read_loginuid', return_value='4294967295'), \
                 mock.patch.object(p.os.path, 'lexists', side_effect=lambda path: path == target):
                self.failure('HOST_CHANGED', p._guard_helper_identity, lock)


class IdentityTests(NoNativeCase):
    def test_ns_type_checked_before_identity(self):
        with mock.patch.object(p.fcntl, 'ioctl', return_value=p.CLONE_NEWNET) as ioctl, \
             mock.patch.object(p.os, 'fstat', return_value=types.SimpleNamespace(st_dev=4, st_ino=5)):
            self.assertEqual(p.namespace_identity(8, p.CLONE_NEWNET), [4, 5])
            ioctl.assert_called_once_with(8, 0xB703)
        with mock.patch.object(p.fcntl, 'ioctl', return_value=p.CLONE_NEWUSER):
            self.failure('NAMESPACE_IDENTITY_INVALID', p.namespace_identity, 8, p.CLONE_NEWNET)

    def test_proc_starttime_handles_space_and_parenthesis(self):
        tail = ['S'] + ['0'] * 18 + ['98765'] + ['0'] * 3
        self.assertEqual(p._process_starttime('22 (name ) with spaces) ' + ' '.join(tail)), '98765')

    def test_live_child_does_not_reap_and_rejects_dead_pidfd(self):
        with mock.patch.object(p.select, 'select', return_value=([7], [], [])), \
             mock.patch.object(p.os, 'open') as open_file:
            self.failure('USER_NAMESPACE_FAILED', p._live_child, 7, 8, '100')
            open_file.assert_not_called()

    def test_cleanup_never_kills_recycled_reaped_pid(self):
        process = mock.Mock(pid=123, returncode=0)
        p._kill_and_reap(process)
        p.os.killpg.assert_not_called()
        process.wait.assert_not_called()

    def test_cleanup_kills_group_and_reaps_live_process(self):
        process = mock.Mock(pid=123, returncode=None)
        with mock.patch.object(p.os, 'killpg') as kill:
            p._kill_and_reap(process)
            kill.assert_called_once_with(123, p.signal.SIGKILL)
            process.wait.assert_called_once_with(timeout=5)

    def test_helper_uses_exact_held_fd_no_path_search(self):
        process = mock.Mock(pid=123, returncode=0)
        process.wait.return_value = 0
        with mock.patch.object(p.subprocess, 'Popen', return_value=process) as popen, \
             mock.patch.object(p, '_remaining', return_value=5):
            p._run_helper(8, 'newuidmap', 123, [[0, 1001, 1], [1, 100000, 65535]], 100)
            args = popen.call_args
            self.assertEqual(args.args[0], ['newuidmap', '123', '0', '1001', '1', '1', '100000', '65535'])
            self.assertEqual(args.kwargs['executable'], '/proc/self/fd/8')
            self.assertEqual(args.kwargs['env'], p.CLEAN_ENV)
            self.assertTrue(args.kwargs['close_fds'])
            self.assertTrue(args.kwargs['start_new_session'])

    def test_helper_timeout_has_terminal_code_and_cleanup(self):
        process = mock.Mock(pid=123, returncode=None)
        process.wait.side_effect = [subprocess.TimeoutExpired('hidden', 5), 0]
        with mock.patch.object(p.subprocess, 'Popen', return_value=process), \
             mock.patch.object(p, '_remaining', return_value=5), \
             mock.patch.object(p.os, 'killpg') as kill:
            self.failure('TIMEOUT', p._run_helper, 8, 'newuidmap', 123, [[0, 1001, 1]], 100)
            kill.assert_called_once()


class MountAndNetworkTests(NoNativeCase):
    def test_private_mount_propagation(self):
        p._assert_private_mounts('1 2 0:3 / / rw - ext4 source rw\n')
        for extra in ('shared:2', 'master:3', 'propagate_from:4'):
            self.failure('MOUNT_NAMESPACE_FAILED', p._assert_private_mounts,
                         '1 2 0:3 / / rw ' + extra + ' - ext4 source rw\n')

    def test_routes_fail_closed(self):
        p._validate_routes('Iface Destination\n', '0 0 0 0 0 0 0 0 0 lo\n')
        self.failure('OFFLINE_NETWORK_FAILED', p._validate_routes, 'Iface Destination\nlo 0\n', '')
        self.failure('OFFLINE_NETWORK_FAILED', p._validate_routes, 'Iface Destination\n', '0 0 0 0 0 0 0 0 0 eth0\n')
        self.failure('OFFLINE_NETWORK_FAILED', p._validate_routes, 'Iface Destination\n', 'lo\n')

    def _network_stack(self, ctx, loopback_up=False, inherited_socket=False, netid=None, changed_boot=False):
        stack = contextlib.ExitStack()
        fake = mock.Mock()
        table = {
            '/proc/net/dev': 'header\nheader\n lo: counters\n',
            '/proc/net/route': 'Iface Destination\n',
            '/proc/net/ipv6_route': '',
        }
        def path(value):
            node = mock.Mock()
            node.read_text.side_effect = lambda: table[value]
            node.iterdir.return_value = [mock.Mock()]
            return node
        stack.enter_context(mock.patch.object(p, 'Path', side_effect=path))
        stack.enter_context(mock.patch.object(p, '_boot', return_value='changed' if changed_boot else 'boot'))
        stack.enter_context(mock.patch.object(p, '_open_namespace', return_value=(40, netid or [1, 99])))
        stack.enter_context(mock.patch.object(p, 'namespace_identity', return_value=[1, 10]))
        stack.enter_context(mock.patch.object(p.os, 'open', return_value=41))
        closed = stack.enter_context(mock.patch.object(p.os, 'close'))
        stack.enter_context(mock.patch.object(p.os, 'readlink', return_value='socket:[99]' if inherited_socket else 'pipe:[1]'))
        socket_mock = stack.enter_context(mock.patch.object(p.socket, 'socket'))
        socket_mock.return_value.__enter__.return_value = fake
        stack.enter_context(mock.patch.object(p.fcntl, 'ioctl', return_value=struct.pack('16sH14x', b'lo', 1 if loopback_up else 0)))
        return stack, closed

    def test_network_guard_checks_original_fd_and_closes_proof_fd(self):
        ctx = context()
        stack, closed = self._network_stack(ctx)
        with stack:
            p._network_proof(ctx)
            self.assertIn(mock.call(30), closed.call_args_list)
            self.assertEqual(ctx['host_net_fd'], -1)

    def test_network_guard_rejects_same_namespace(self):
        ctx = context(); stack, _ = self._network_stack(ctx, netid=[1, 10])
        with stack:
            self.failure('OFFLINE_NETWORK_FAILED', p._network_proof, ctx)

    def test_network_guard_rejects_up_loopback_socket_and_boot_change(self):
        for kwargs, code in (({'loopback_up': True}, 'OFFLINE_NETWORK_FAILED'),
                             ({'inherited_socket': True}, 'OFFLINE_NETWORK_FAILED'),
                             ({'changed_boot': True}, 'HOST_CHANGED')):
            ctx = context(); stack, _ = self._network_stack(ctx, **kwargs)
            with stack:
                self.failure(code, p._network_proof, ctx)

    def test_ownership_only_after_mount_and_checks_both_nonzero_ids(self):
        before = types.SimpleNamespace(st_dev=1, st_mode=stat.S_IFDIR | 0o700)
        after = types.SimpleNamespace(st_dev=2, st_uid=0, st_gid=0)
        info = [types.SimpleNamespace(st_uid=i, st_gid=i) for i in (1, 65534, 0)]
        events = []
        with mock.patch.object(p.os, 'stat', side_effect=[before, after]), \
             mock.patch.object(p, '_mount_tmpfs', side_effect=lambda x: events.append('mount')), \
             mock.patch.object(p, 'Path') as path, \
             mock.patch.object(p.os, 'open', side_effect=[8, 9]), \
             mock.patch.object(p.os, 'fchown', side_effect=lambda fd, u, g: events.append((u, g))) as chown, \
             mock.patch.object(p.os, 'fstat', side_effect=info), \
             mock.patch.object(p.os, 'close'), mock.patch.object(p.os, 'unlink'):
            path.return_value.read_text.return_value = '1 2 0:3 / /private/.fixture rw,nosuid,nodev,noexec - tmpfs capability-probe rw\n'
            p._ownership_proof('/private/.fixture')
            self.assertEqual(events, ['mount', (1, 1), (65534, 65534), (0, 0)])
            self.assertEqual(chown.call_args_list, [mock.call(9, i, i) for i in (1, 65534, 0)])

    def test_failed_mount_never_reaches_host_chown(self):
        before = types.SimpleNamespace(st_dev=1, st_mode=stat.S_IFDIR | 0o700)
        with mock.patch.object(p.os, 'stat', return_value=before), \
             mock.patch.object(p, '_mount_tmpfs', side_effect=p.ProbeFailure('TMPFS_FAILED')):
            self.failure('TMPFS_FAILED', p._ownership_proof, '/private/.fixture')
            p.os.fchown.assert_not_called()


class CompositionTests(NoNativeCase):
    def test_mounted_stage_proves_multi_id_ownership_before_inner_user_and_net(self):
        ctx = context(); events = []
        with mock.patch.object(p, '_check_child_runtime'), mock.patch.object(p, '_verify_maps') as maps, \
             mock.patch.object(p, '_open_namespace', side_effect=[(1, [1, 20]), (2, [1, 21]), (3, [1, 10])]), \
             mock.patch.object(p.os, 'close'), mock.patch.object(p, 'Path') as path, \
             mock.patch.object(p, '_assert_private_mounts'), \
             mock.patch.object(p, '_ownership_proof', side_effect=lambda x: events.append('ownership')), \
             mock.patch.object(p, '_send', side_effect=lambda *x: events.append('nesting')), \
             mock.patch.object(p.os, 'execve', side_effect=lambda *x: events.append('exec')) as execute:
            p._child_mounted(ctx, 10, 11)
            maps.assert_called_once_with(ctx)
            self.assertEqual(events, ['ownership', 'nesting', 'exec'])
            args = execute.call_args.args
            self.assertEqual(args[0], 22)
            self.assertEqual(args[1][:5], ['unshare', '--user', '--map-root-user', '--net', '--'])
            self.assertIn('pycache_prefix=/private/unused-pycache', args[1])
            self.assertEqual(args[2], p.CLEAN_ENV)

    def test_inner_requires_new_user_same_private_mount_then_network(self):
        ctx = context()
        with mock.patch.object(p, '_check_child_runtime'), mock.patch.object(p, '_verify_maps') as maps, \
             mock.patch.object(p, '_open_namespace', side_effect=[(1, [1, 50]), (2, [1, 21])]), \
             mock.patch.object(p.os, 'close'), mock.patch.object(p, 'Path') as path, \
             mock.patch.object(p, '_assert_private_mounts'), \
             mock.patch.object(p, '_network_proof') as network, mock.patch.object(p, '_send'):
            path.return_value.exists.return_value = False
            p._child_inner(ctx, 10, 11)
            maps.assert_called_once_with(ctx, inner=True)
            network.assert_called_once_with(ctx)

    def test_inner_rejects_same_outer_user(self):
        ctx = context()
        with mock.patch.object(p, '_check_child_runtime'), mock.patch.object(p, '_verify_maps'), \
             mock.patch.object(p, '_open_namespace', return_value=(1, [1, 20])), \
             mock.patch.object(p.os, 'close'):
            self.failure('USER_NAMESPACE_FAILED', p._child_inner, ctx, 10, 11)

    def test_outer_group_set_and_drop_are_private_and_verified(self):
        with mock.patch.object(p.os, 'setgroups') as setgroups, \
             mock.patch.object(p.os, 'getgroups', side_effect=[[65534, 1], []]):
            p._outer_groups_proof()
            self.assertEqual(setgroups.call_args_list, [mock.call([1, 65534]), mock.call([])])
        with mock.patch.object(p.os, 'setgroups'), mock.patch.object(p.os, 'getgroups', return_value=[1]):
            self.failure('GROUPS_CAPABILITY_FAILED', p._outer_groups_proof)

    def test_outer_and_inner_mapping_expectations_differ(self):
        ctx = context()
        with mock.patch.object(p, '_read_maps', side_effect=[[[0, 0, 1]], [[0, 0, 1]]]), \
             mock.patch.object(p.os, 'getuid', return_value=0), mock.patch.object(p.os, 'geteuid', return_value=0), \
             mock.patch.object(p.os, 'getgid', return_value=0), mock.patch.object(p.os, 'getegid', return_value=0), \
             mock.patch.object(p, 'Path') as path:
            path.return_value.read_text.return_value = 'deny\n'
            p._verify_maps(ctx, inner=True)
        with mock.patch.object(p, '_read_maps', return_value=[[0, 0, 1]]):
            self.failure('MAPPING_FAILED', p._verify_maps, ctx)


class OrchestrationTests(NoNativeCase):
    def test_old_or_unrecognized_lock_protocol_is_rejected_before_host_calls(self):
        for schema,protocol in ((1,'private-public-keyring-v1'),(2,'private-public-keyring-v1'),(3,None),(3,'host-keyring-v1')):
            with self.subTest(schema=schema,protocol=protocol):
                self.failure('LOCK_INVALID',p._execute_locked,Path('/unused'),
                    {'schema':schema,'protocol':protocol,'status':'authenticated-existing-tools'},
                    'a'*64,'b'*64,lambda:None)

    def test_missing_or_wrong_member_protocol_is_rejected_before_host_calls(self):
        for member_protocol in (None, 'unverified-package-union', 'private-public-keyring-v1'):
            with self.subTest(member_protocol=member_protocol):
                self.failure('LOCK_INVALID', p._execute_locked, Path('/unused'),
                    {'schema': 3, 'protocol': 'private-public-keyring-v1',
                     'member_protocol': member_protocol, 'status': 'authenticated-existing-tools'},
                    'a'*64, 'b'*64, lambda: None)

    def harness(self, helper_error=None, cleanup_error=None, exit_code=0):
        stack = contextlib.ExitStack()
        lock = {'schema': 3, 'protocol': 'private-public-keyring-v1', 'member_protocol': 'signed-package-hop-attribution-v1', 'status': 'authenticated-existing-tools', 'scope': 'namespace-capability-only',
                'source_index_sha256': 'a' * 64, 'host': {**host(), 'boot_id': 'boot', 'pycache_prefix': '/private/absent-pycache'},
                'tools': {n: {'name': n} for n in p.TOOL_NAMES}, 'source_files': {p.SOURCE_NAME: {}},
                'nss': {'providers': {'passwd': ['files', 'systemd'], 'group': ['files', 'systemd']},
                        'fallback_paths': ['/lib/x86_64-linux-gnu/libnss_systemd.so.2'], 'absent_paths': []},
                'absent_paths': [p.NSCD_SOCKET],
                'runtime_files': {'/lib/x86_64-linux-gnu/libnss_systemd.so.2': {'package': 'libnss-systemd'}},
                'runtime_elf_paths': ['/lib/x86_64-linux-gnu/libnss_systemd.so.2']}
        for name, value in (('getuid', 1001), ('geteuid', 1001), ('getgid', 1002), ('getegid', 1002), ('getgroups', [1002])):
            stack.enter_context(mock.patch.object(p.os, name, return_value=value))
        stack.enter_context(mock.patch.object(p, '_boot', return_value='boot'))
        stack.enter_context(mock.patch.object(p, '_read_loginuid', return_value='4294967295'))
        stack.enter_context(mock.patch.object(p.os.path, 'lexists', return_value=False))
        stack.enter_context(mock.patch.object(p.threading, 'active_count', return_value=1))
        stack.enter_context(mock.patch.object(p.signal, 'getsignal', return_value=p.signal.SIG_DFL))
        stack.enter_context(mock.patch.object(p, 'Path'))
        p.Path.return_value.exists.return_value = False
        stack.enter_context(mock.patch.object(p, '_read_host_records', return_value=texts()))
        stack.enter_context(mock.patch.object(p, '_hold_record', side_effect=[10, 11, 12, 13, 14]))
        stack.enter_context(mock.patch.object(p.os, 'fstat', return_value=types.SimpleNamespace(st_mode=stat.S_IFREG | 0o4755)))
        stack.enter_context(mock.patch.object(p, '_open_namespace', side_effect=[(20, [1, 10]), (21, [1, 11]), (22, [1, 12])]))
        stack.enter_context(mock.patch.object(p.tempfile, 'mkdtemp', return_value='/private/.fixture'))
        stack.enter_context(mock.patch.object(p.os, 'stat', return_value=types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1001)))
        stack.enter_context(mock.patch.object(p.os, 'pipe2', side_effect=[(30, 31), (32, 33)]))
        process = mock.Mock(pid=123, returncode=None)
        def wait(**kwargs):
            process.returncode = exit_code
            return exit_code
        process.wait.side_effect = wait
        popen = stack.enter_context(mock.patch.object(p.subprocess, 'Popen', return_value=process))
        closed = stack.enter_context(mock.patch.object(p.os, 'close'))
        stack.enter_context(mock.patch.object(p.os, 'pidfd_open', return_value=40))
        stack.enter_context(mock.patch.object(p.os, 'open', side_effect=[41, 42, 43, 44]))
        stack.enter_context(mock.patch.object(p.os, 'read', return_value=('123 (safe) S ' + '0 ' * 18 + '999 0').encode()))
        stack.enter_context(mock.patch.object(p, 'namespace_identity', side_effect=[[1, 20], [1, 21]]))
        sends = stack.enter_context(mock.patch.object(p, '_send'))
        receives = stack.enter_context(mock.patch.object(p, '_receive', side_effect=[{'event': x} for x in ('ready', 'mounting', 'nesting', 'complete')]))
        live = stack.enter_context(mock.patch.object(p, '_live_child'))
        helper = stack.enter_context(mock.patch.object(p, '_run_helper', side_effect=helper_error))
        killed = stack.enter_context(mock.patch.object(p.os, 'killpg'))
        removed = stack.enter_context(mock.patch.object(p.os, 'rmdir', side_effect=cleanup_error))
        verifier = mock.Mock(return_value=lock)
        return stack, lock, verifier, process, popen, closed, sends, receives, live, helper, killed, removed

    def test_all_stages_and_exact_original_fd_passed_without_stdio_or_env_leaks(self):
        values = self.harness()
        stack, lock, verifier, process, popen, closed, sends, receives, live, helper, killed, removed = values
        with stack:
            p._execute_locked('/private', lock, 'b' * 64, 'a' * 64, verifier)
            self.assertEqual(helper.call_count, 2)
            self.assertEqual(live.call_count, 4)
            self.assertEqual(verifier.call_count, 3)
            self.assertEqual(popen.call_args.args[0][:3], ['unshare', '--user', '--'])
            self.assertEqual(popen.call_args.kwargs['pass_fds'], (10, 11, 12, 13, 14, 20, 30, 33))
            self.assertEqual(popen.call_args.kwargs['env'], p.CLEAN_ENV)
            self.assertEqual(popen.call_args.kwargs['stdout'], subprocess.DEVNULL)
            self.assertEqual(popen.call_args.kwargs['stderr'], subprocess.DEVNULL)
            self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)
            self.assertTrue(popen.call_args.kwargs['start_new_session'])
            killed.assert_not_called()  # Already reaped PID must never be killed.
            removed.assert_called_once_with('/private/.fixture')
            self.assertEqual(set(c.args[0] for c in closed.call_args_list),
                             {10, 11, 12, 13, 14, 20, 21, 22, 30, 31, 32, 33, 40, 41, 42, 43, 44})
            self.assertEqual(sends.call_args_list[1].args[1], {'event': 'mapped'})

    def test_helper_failure_kills_unreaped_group_closes_fds_and_empty_mountpoint_only(self):
        values = self.harness(helper_error=p.ProbeFailure('MAPPING_FAILED'))
        stack, lock, verifier, process, popen, closed, sends, receives, live, helper, killed, removed = values
        with stack:
            self.failure('MAPPING_FAILED', p._execute_locked, '/private', lock, 'b' * 64, 'a' * 64, verifier)
            helper.assert_called_once()
            killed.assert_called_once_with(123, p.signal.SIGKILL)
            removed.assert_called_once_with('/private/.fixture')
            self.assertEqual(sends.call_count, 1)  # No mapped or later-stage release.

    def test_identity_guard_surrounds_each_helper_and_final_host_proof(self):
        values = self.harness()
        stack, lock, verifier, process, popen, closed, sends, receives, live, helper, killed, removed = values
        events = []
        with stack, mock.patch.object(p, '_guard_helper_identity', side_effect=lambda x: events.append('guard')):
            helper.side_effect = lambda fd, name, *args: events.append(name)
            p._execute_locked('/private', lock, 'b' * 64, 'a' * 64, verifier)
        self.assertEqual(events, ['guard', 'guard', 'newuidmap', 'guard', 'guard', 'newgidmap', 'guard', 'guard'])

    def test_identity_change_after_first_helper_stops_before_second_helper(self):
        values = self.harness()
        stack, lock, verifier, process, popen, closed, sends, receives, live, helper, killed, removed = values
        with stack, mock.patch.object(p, '_guard_helper_identity',
                                      side_effect=[None, None, p.ProbeFailure('HOST_CHANGED'), None]):
            self.failure('HOST_CHANGED', p._execute_locked, '/private', lock, 'b' * 64, 'a' * 64, verifier)
            helper.assert_called_once()
            killed.assert_called_once_with(123, p.signal.SIGKILL)
            self.assertEqual(sends.call_count, 1)

    def test_nonzero_child_exit_is_not_pass(self):
        values = self.harness(exit_code=4)
        stack, lock, verifier = values[:3]
        with stack:
            self.failure('CHILD_EXIT_FAILED', p._execute_locked, '/private', lock, 'b' * 64, 'a' * 64, verifier)

    def test_uncertain_cleanup_overrides_success(self):
        values = self.harness(cleanup_error=OSError('private path'))
        stack, lock, verifier = values[:3]
        with stack:
            self.failure('CLEANUP_FAILED', p._execute_locked, '/private', lock, 'b' * 64, 'a' * 64, verifier)


class IntegrityAndOutputTests(NoNativeCase):
    def test_exact_file_record_and_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'file'
            path.write_bytes(b'fixed bytes')
            info = path.stat()
            record = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), **p._record_info(info)}
            fd = p._hold_record(record, 'TOOL_CHANGED')
            os.close(fd)
            path.write_bytes(b'changed bytes')
            self.failure('TOOL_CHANGED', p._hold_record, record, 'TOOL_CHANGED')

    def test_verified_absent_allocation_files_become_missing_not_generic_failure(self):
        lock = {'host_files': {name: {'path': name} for name in p.HOST_PATHS[:3]},
                'absent_paths': list(p.HOST_PATHS[3:])}
        data = texts()
        with mock.patch.object(p, '_hold_record', side_effect=[1, 2, 3]), \
             mock.patch.object(p.os, 'fstat', return_value=types.SimpleNamespace(st_size=100)), \
             mock.patch.object(p.os, 'read', side_effect=[data[n].encode() for n in p.HOST_PATHS[:3]]), \
             mock.patch.object(p.os, 'close'), mock.patch.object(p.os.path, 'lexists', return_value=False):
            result = p._read_host_records(lock)
            self.assertEqual(result['/etc/subuid'], '')
            self.assertEqual(result['/etc/subgid'], '')
            self.failure('SUBUID_MISSING', p.select_mappings, result, host())

    def test_source_anchor_precedes_verifier_import_and_native_execution(self):
        with mock.patch.object(p, '_safe_root', return_value=Path('/private')), \
             mock.patch.object(p, 'verify_source_index', side_effect=p.ProbeFailure('SOURCE_INVALID')), \
             mock.patch.object(p, '_load_verifier') as load, mock.patch.object(p, '_execute_locked') as execute:
            self.failure('SOURCE_INVALID', p.run_probe, '/private', 'a' * 64, 'b' * 64)
            load.assert_not_called(); execute.assert_not_called()

    def test_verifier_executes_hashed_bytes_not_replaced_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'recipe').mkdir()
            source = b'def verify_lock(root, digest):\n    return {"source": "verified", "file": __file__}\n'
            replacement = b'def verify_lock(root, digest):\n    return {"source": "unverified"}\n'
            (root / p.PREP_NAME).write_bytes(replacement)
            verify = p._load_verifier(root, source)
            self.assertEqual(verify(root, 'digest'), {'source': 'verified', 'file': str(root / p.PREP_NAME)})
            self.assertEqual((root / p.PREP_NAME).read_bytes(), replacement)

    def test_source_index_retains_same_hashed_verifier_byte_string(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'recipe').mkdir()
            verifier = b'def verify_lock(root, digest): return "verified"\n'
            source = b'# indexed probe source\n'
            (root / p.PREP_NAME).write_bytes(verifier)
            (root / p.SOURCE_NAME).write_bytes(source)
            index = {'schema': 1, 'status': 'reviewed-source', 'files': {
                p.PREP_NAME: {'sha256': hashlib.sha256(verifier).hexdigest()},
                p.SOURCE_NAME: {'sha256': hashlib.sha256(source).hexdigest()}}}
            raw = json.dumps(index).encode()
            (root / 'source-index.json').write_bytes(raw)
            with mock.patch.object(p, '__file__', str(root / p.SOURCE_NAME)):
                checked, retained = p.verify_source_index(root, hashlib.sha256(raw).hexdigest())
            (root / p.PREP_NAME).write_bytes(b'# later untrusted replacement\n')
            self.assertEqual(checked, index)
            self.assertEqual(retained, verifier)
            self.assertEqual(p._load_verifier(root, retained)(root, 'digest'), 'verified')

    def test_invalid_lock_prevents_execution(self):
        with mock.patch.object(p, '_safe_root', return_value=Path('/private')), \
             mock.patch.object(p, 'verify_source_index', return_value=({}, b'verified')), \
             mock.patch.object(p, '_load_verifier', return_value=mock.Mock(side_effect=RuntimeError('private detail'))), \
             mock.patch.object(p, '_execute_locked') as execute:
            self.failure('LOCK_INVALID', p.run_probe, '/private', 'a' * 64, 'b' * 64)
            execute.assert_not_called()

    def test_duplicate_json_and_oversized_protocol_rejected(self):
        self.failure('CHILD_PROTOCOL_FAILED', p._json, b'{"x":1,"x":2}')
        self.failure('CHILD_PROTOCOL_FAILED', p._json, b' ' * (p.MAX_MESSAGE + 1))

    def test_explicit_execution_flag_mandatory(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(p, 'run_probe') as run, contextlib.redirect_stdout(io.StringIO()) as out:
            result = p.main(['--root', directory, '--expected-source-index-sha256', 'a' * 64, '--expected-lock-sha256', 'b' * 64])
            self.assertEqual(result, 1); run.assert_not_called()
            receipt = json.loads(out.getvalue())
            self.assertEqual(receipt, {'schema': 1, 'status': 'FAIL', 'code': 'EXECUTION_NOT_REQUESTED'})
            self.assertEqual(json.loads((Path(directory) / 'capability-probe-status.json').read_text()), receipt)

    def test_private_exception_never_reaches_output(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(p, 'run_probe', side_effect=RuntimeError('private uid1001 /secret/path environment token')), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            result = p.main(['--root', directory, '--expected-source-index-sha256', 'a' * 64,
                             '--expected-lock-sha256', 'b' * 64, '--execute-capability-probe'])
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(out.getvalue()), {'schema': 1, 'status': 'FAIL', 'code': 'INTERNAL_FAILURE'})

    def test_receipt_atomic_exclusive_and_mode_private(self):
        with tempfile.TemporaryDirectory() as directory:
            p._write_status(directory, 'OK')
            path = Path(directory) / 'capability-probe-status.json'
            first = path.read_bytes()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                p._write_status(directory, 'INTERNAL_FAILURE')
            self.assertEqual(path.read_bytes(), first)
            self.assertEqual([x.name for x in Path(directory).iterdir()], ['capability-probe-status.json'])

    def test_unknown_code_cannot_leak(self):
        self.assertEqual(p.ProbeFailure('private exception text').code, 'INTERNAL_FAILURE')


if __name__ == '__main__':
    unittest.main()

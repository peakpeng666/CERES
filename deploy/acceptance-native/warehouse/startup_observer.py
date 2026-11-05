"""Opt-in, observational wrapper. No session mutation or acceptance decision."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from startup_observation_schema import sanitize, STATE_PHASES

ENABLE = 'URP_WAREHOUSE_STARTUP_OBSERVER'
DELAY = 'URP_WAREHOUSE_STARTUP_OBSERVER_DELAY_S'
TRIGGER_S = 5.0
HARD_WALL_S = 12.0
RESULT_BYTES = 16384
KEYS = ('clock', 'odom', 'scan', 'dock', 'ground_truth', 'cmd_vel')


def is_cancellation(exc):
    return not isinstance(exc, Exception) or (type(exc) is RuntimeError and exc.args == ('interrupted',))


class Observer:
    def __init__(self, env, private, source, *, clock=time.monotonic, popen=subprocess.Popen):
        self.enabled = env.get(ENABLE) == '1'
        delay = env.get(DELAY, '5')
        self.trigger_s = {'5': TRIGGER_S, '30': 30.0}.get(delay) if type(delay) is str else None
        self.env = dict(env)
        self.private = Path(private)
        self.source = Path(source)
        self.clock, self.popen = clock, popen
        self.sid = self.origin = self.proc = self.timer = None
        self.closed = False
        self.confirmed = False
        self.buffer = bytearray()
        self.lock = threading.RLock()
        self.raw = {'status': 'not_started' if self.enabled else 'disabled',
                    'observer_trigger_wall_s': self.trigger_s,
                    'startup_observation_policy': 'starting_phase_v2',
                    'sampling_end_reason': 'unobserved' if self.enabled else 'disabled',
                    'observer_watchdog_expired': False, 'output_limited': False,
                    'observer_group_signal_requested': 'none',
                    'cleanup_delay_s': 0.0, 'ros_api': {'availability': 'no_samples', 'samples': 0,
                                                     'state_responses': 0, 'starting_error_responses': 0}}
        if self.enabled and self.trigger_s is None:
            self.closed = True
            self.raw.update(status='unavailable', sampling_end_reason='error')

    def safe(self, name, *args):
        """Ordinary diagnostic failures cannot replace control outcomes."""
        try:
            return getattr(self, name)(*args)
        except BaseException as exc:
            self.closed = True
            self.raw.update(status='interrupted' if is_cancellation(exc) else 'unavailable',
                            sampling_end_reason='error')
            if not self.confirmed:
                self.raw.pop('native_stats', None)
                self.raw.pop('ros_clock', None)
            try:
                self.kill_group(signal.SIGKILL)
            except BaseException:
                pass
            if is_cancellation(exc):
                raise
            # A separately armed watchdog still bounds a child after failure.
            return None

    def bind(self, sid):
        if not self.enabled or self.closed:
            return
        if (not isinstance(sid, str) or len(sid) > 128 or
                re.fullmatch(r'sim_[A-Za-z0-9_]+', sid) is None or
                (self.sid is not None and sid != self.sid)):
            self.cutoff('identity_mismatch')
            return
        if self.sid is None:
            self.sid, self.origin = sid, self.clock()
            self.raw['session_identity_verified'] = True

    def wrap(self, api):
        def call(method, path, data=None, timeout=5):
            cancellation = None
            if self.enabled and method == 'DELETE' and path == '/sim/session':
                try:
                    self.safe('before_cleanup')
                except BaseException as exc:
                    cancellation = exc  # still attempt the original cleanup once
            try:
                row = api(method, path, data, timeout)
            except BaseException:
                if self.enabled:
                    try:
                        self.safe('cutoff', 'error')
                    except BaseException:
                        pass  # preserve the original API exception as primary
                raise
            if cancellation is not None:
                raise cancellation
            if self.enabled and method == 'GET' and path == '/sim/state':
                self.safe('observe', row)
            return row
        return call

    def before_cleanup(self):
        began = self.clock()
        try:
            self.cutoff('cleanup_started')
        finally:
            self.raw['cleanup_delay_s'] += self.clock() - began

    def observe(self, row):
        if self.closed or self.sid is None:
            return
        if not isinstance(row, dict):
            self.raw['ros_api']['availability'] = 'invalid'
            self.cutoff('invalid_state')
            return
        if row.get('session_id') != self.sid or row.get('world') != 'warehouse':
            self.raw['ros_api']['availability'] = 'identity_mismatch'
            self.cutoff('identity_mismatch')
            return
        # These are cached gateway state observations, not exact transition times.
        errors = row.get('errors')
        state = {'receipt_wall_s': self.clock() - self.origin, 'phase': row.get('phase'),
                 'errors_present': bool(errors) if isinstance(errors, list) else None,
                 'error_count': len(errors) if isinstance(errors, list) else None,
                 'telemetry_present': isinstance(row.get('telemetry'), dict)}
        section = self.raw['ros_api']
        section['state_responses'] = min(128, section['state_responses'] + 1)
        section.setdefault('first_state', state)
        section['last_state'] = state
        # Exact source uses 'starting' until ready() and docking checks finish.
        # SimClient can temporarily add polling errors in that phase before the
        # control HTTP server is ready; errors alone are not phase departure.
        # In particular failed/stopping/ready/undocking are never sampled.
        if not isinstance(row.get('phase'), str) or row['phase'] not in STATE_PHASES:
            self.cutoff('invalid_state')
            return
        if row['phase'] != 'starting':
            self.cutoff('startup_left')
            return
        if state['errors_present']:
            section['starting_error_responses'] = min(128, section['starting_error_responses'] + 1)
        telemetry = row.get('telemetry')
        snap = None
        if isinstance(telemetry, dict):
            snap = {'receipt_wall_s': self.clock() - self.origin, 'phase': 'starting'}
            for key in ('counts', 'age_s'):
                source = telemetry.get(key)
                snap[key] = {k: source.get(k) for k in KEYS} if isinstance(source, dict) else {}
            section = self.raw['ros_api']
            section['samples'] = min(128, section['samples'] + 1)
            section['availability'] = 'available'
            section.setdefault('first', snap)
            section['last'] = snap
        if self.proc is None and self.raw['status'] == 'not_started' and self.clock() - self.origin >= self.trigger_s:
            self.start()
        if self.proc is not None and self.proc.returncode is None:
            if snap is not None:
                self.raw['ros_api'].setdefault('observer_window_first', snap)
                self.raw['ros_api']['observer_window_last'] = snap
            self.drain()
            if self.exited():
                # Completion is accepted only while a subsequent API response
                # still reports startup. Cached phase is not an exact stop time.
                self.finish_child()

    def start(self):
        env = dict(self.env, IGN_PARTITION='urp_' + self.sid,
                   ROS_LOG_DIR=str(self.private / 'observer-ros-log'),
                   ROS_HOME=str(self.private / 'observer-ros'),
                   IGN_HOMEDIR=str(self.private / 'observer-ignition'),
                   PYTHONDONTWRITEBYTECODE='1')
        # Separate observer ownership from the application's cleanup accounting.
        env.pop('URP_ACCEPTANCE_OWNER', None)
        # Use exactly the source application's ROS setup and simulation profile.
        # Positional parameters are quoted; no ID/path is interpolated in shell text.
        command = ['/bin/bash', '-c',
                   'source /opt/ros/humble/setup.bash && exec /bin/bash "$1" simulation /usr/bin/python3 "$2" --origin "$3"',
                   'urp-readonly-observer', str(self.source / 'setup/profile.sh'),
                   str(Path(__file__).with_name('startup_observer_child.py')), repr(self.origin)]
        self.proc = self.popen(command, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        self.timer = threading.Timer(HARD_WALL_S, lambda: self.safe('expire'))
        self.timer.daemon = True
        try:
            self.timer.start()
            os.set_blocking(self.proc.stdout.fileno(), False)
            self.raw['status'] = 'running'
        except BaseException:
            self.kill_group(signal.SIGKILL)
            raise

    def exited(self):
        # WNOWAIT deliberately keeps our child PID unreaped, anchoring ownership
        # of its new process group until the final group kill and wait below.
        return os.waitid(os.P_PID, self.proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None

    def kill_group(self, sig):
        with self.lock:
            if self.proc is None or self.proc.returncode is not None:
                return
            os.waitid(os.P_PID, self.proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if os.getpgid(self.proc.pid) != self.proc.pid:
                raise RuntimeError('observer_group_changed')
            try:
                self.raw['observer_group_signal_requested'] = 'sigkill' if sig == signal.SIGKILL else 'sigterm'
                os.killpg(self.proc.pid, sig)
            except ProcessLookupError:
                pass

    def expire(self):
        with self.lock:
            if self.proc is not None and self.proc.returncode is None:
                self.raw.update(status='bounded_stop', observer_watchdog_expired=True,
                                sampling_end_reason='bounded_stop')
                self.kill_group(signal.SIGKILL)

    def drain(self):
        for _ in range(5):
            try:
                chunk = os.read(self.proc.stdout.fileno(), 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            if len(self.buffer) + len(chunk) > RESULT_BYTES:
                self.raw.update(output_limited=True, status='unavailable')
                self.kill_group(signal.SIGKILL)
                break
            self.buffer.extend(chunk)

    def finish_child(self):
        with self.lock:
            self.drain()
            self.kill_group(signal.SIGKILL)  # clean any descendant before reaping anchor
            self.proc.wait(timeout=0.5)
            self.proc.stdout.close()
            if self.timer:
                self.timer.cancel()
            self.raw.update(observer_exit_code=self.proc.returncode, observer_leader_reaped=True,
                            observer_cleanup_complete=None)
            if self.proc.returncode == 0 and not self.raw['output_limited'] and not self.raw['observer_watchdog_expired']:
                parsed = json.loads(self.buffer)
                if not isinstance(parsed, dict):
                    raise ValueError('observer_result_invalid')
                checked = sanitize(parsed)
                for key in ('native_stats', 'ros_clock', 'observation_duration_s', 'output_bytes'):
                    self.raw[key] = checked[key]
                self.raw.update(status='complete', sampling_end_reason='window_complete')
                self.confirmed = True
            elif self.raw['status'] != 'bounded_stop':
                self.raw['status'] = 'unavailable'

    def cutoff(self, reason):
        if self.closed:
            return
        self.closed = True
        if not self.confirmed and self.raw['status'] != 'bounded_stop':
            self.raw.update(sampling_end_reason=reason, status='interrupted' if self.proc else 'not_started')
            # Unconfirmed window is not promoted to pre-cleanup evidence.
            self.raw.pop('native_stats', None)
            self.raw.pop('ros_clock', None)
        if self.proc is not None and self.proc.returncode is None:
            self.kill_group(signal.SIGTERM)

    def close(self):
        if not self.enabled:
            return sanitize(self.raw)
        began = self.clock()
        try:
            self.cutoff('cleanup_started')
            with self.lock:
                if self.proc is not None and self.proc.returncode is None:
                    # Immediate group kill avoids delaying the application's DELETE.
                    self.kill_group(signal.SIGKILL)
                    self.proc.wait(timeout=0.5)
                    self.proc.stdout.close()
                    self.raw.update(observer_exit_code=self.proc.returncode, observer_leader_reaped=True,
                                    observer_cleanup_complete=None)
                elif self.proc is None:
                    self.raw['observer_cleanup_complete'] = True
                    self.raw['observer_leader_reaped'] = False
                if self.timer:
                    self.timer.cancel()
        finally:
            self.raw['cleanup_delay_s'] += self.clock() - began
        return sanitize(self.raw)

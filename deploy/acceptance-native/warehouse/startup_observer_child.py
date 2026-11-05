"""Bounded read-only child: native world statistics and direct ROS /clock.

ROS imports occur only in main; pure fixture tests never start ROS or Ignition.
"""
import argparse
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from startup_observation_schema import parse_native_stats, parse_topic_info, sanitize

TOPIC = '/world/warehouse/stats'
ECHO = ('/usr/bin/ign', 'topic', '-e', '-t', TOPIC, '--json-output', '-d', '8', '--force-version', '11')
INFO = ('/usr/bin/ign', 'topic', '-i', '-t', TOPIC, '--force-version', '11')
WINDOW_S = 8.0
MAX_BYTES = 262144
MAX_SAMPLES = 128


class Measurements:
    def __init__(self, origin, clock=time.monotonic):
        self.origin, self.clock = origin, clock
        self.pending = bytearray()
        self.info = bytearray()
        self.raw = {'status': 'running', 'output_bytes': 0, 'output_limited': False,
                    'native_stats': {'availability': 'no_messages', 'publisher_state': 'unobserved',
                                     'samples': 0, 'invalid_samples': 0},
                    'ros_clock': {'availability': 'no_messages', 'messages': 0, 'invalid_messages': 0}}

    def feed(self, kind, chunk):
        if self.raw['output_bytes'] + len(chunk) > MAX_BYTES:
            self.raw['output_limited'] = True
            self.raw['native_stats']['availability'] = 'output_limited'
            return False
        self.raw['output_bytes'] += len(chunk)
        if kind == 'info':
            if len(self.info) + len(chunk) > 8192:
                self.raw['native_stats']['publisher_state'] = 'probe_error'
                return False
            self.info.extend(chunk)
            return True
        self.pending.extend(chunk)
        while b'\n' in self.pending:
            line, _, rest = self.pending.partition(b'\n')
            self.pending = bytearray(rest)
            try:
                item = parse_native_stats(line.decode('utf-8'))
            except UnicodeError:
                item = None
            sec = self.raw['native_stats']
            if item is None:
                sec['invalid_samples'] = min(10000, sec['invalid_samples'] + 1)
                continue
            if sec['samples'] >= MAX_SAMPLES:
                self.raw['output_limited'] = True
                sec['availability'] = 'output_limited'
                return False
            item['receipt_wall_s'] = self.clock() - self.origin
            sec['samples'] += 1
            sec.setdefault('first', item)
            sec['last'] = item
            sec['availability'] = 'available'
        if len(self.pending) > 16384:
            self.raw['output_limited'] = True
            self.raw['native_stats']['availability'] = 'output_limited'
            return False
        return True

    def ros(self, sec, nsec):
        out = self.raw['ros_clock']
        if type(sec) is not int or type(nsec) is not int or not 0 <= sec <= 86400 or not 0 <= nsec < 1000000000:
            out['invalid_messages'] = min(10000, out['invalid_messages'] + 1)
            return
        stamp = sec + nsec / 1e9
        if stamp > 86400:
            out['invalid_messages'] = min(10000, out['invalid_messages'] + 1)
            return
        item = {'receipt_wall_s': self.clock() - self.origin, 'sim_time_s': stamp}
        out['messages'] = min(1000000000, out['messages'] + 1)
        out.setdefault('first', item)
        out['last'] = item
        out['availability'] = 'available'

    def finish(self, info_code, echo_code, sampling_ended=None):
        native = self.raw['native_stats']
        try:
            native['publisher_state'] = parse_topic_info(self.info.decode('utf-8')) if info_code == 0 else 'probe_error'
        except UnicodeError:
            native['publisher_state'] = 'probe_error'
        if native['publisher_state'] != 'expected_type':
            native['availability'] = 'type_mismatch' if native['publisher_state'] in ('mixed_types', 'unknown_type') else 'unavailable'
            native.pop('first', None)
            native.pop('last', None)
        elif echo_code != 0:
            native['availability'] = 'command_error'
        elif self.pending or (native['invalid_samples'] and not native['samples']):
            native['availability'] = 'invalid'
        self.raw['status'] = 'complete'
        sampling_ended = self.clock() if sampling_ended is None else sampling_ended
        for key in ('native_stats', 'ros_clock'):
            last = self.raw[key].get('last')
            self.raw[key]['last_receipt_age_at_window_end_s'] = sampling_ended - self.origin - last['receipt_wall_s'] if last else None
        return sanitize(self.raw)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--origin', type=float, required=True)
    args = parser.parse_args(argv)
    if not math.isfinite(args.origin) or not 0 < args.origin < 1e12:
        return 2
    began = time.monotonic()
    measure = Measurements(args.origin)
    processes = {}
    selector = selectors.DefaultSelector()
    node = ros = None
    completed = False
    sampling_ended = None
    codes = {'info': None, 'echo': None}
    stopped_by_observer = set()

    def interrupted(_sig, _frame):
        raise InterruptedError('observer_interrupted')

    signal.signal(signal.SIGTERM, interrupted)
    try:
        try:
            import rclpy as ros
            from rclpy.qos import qos_profile_sensor_data
            from rclpy.signals import SignalHandlerOptions
            from rosgraph_msgs.msg import Clock
            ros.init(args=[], signal_handler_options=SignalHandlerOptions.NO)
            node = ros.create_node('urp_readonly_clock_observer', enable_rosout=False, start_parameter_services=False)
            node.create_subscription(Clock, '/clock', lambda msg: measure.ros(msg.clock.sec, msg.clock.nanosec), qos_profile_sensor_data)
        except Exception:
            measure.raw['ros_clock']['availability'] = 'import_error'
            node = None
        for name, command in (('echo', ECHO), ('info', INFO)):
            try:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                processes[name] = proc
                os.set_blocking(proc.stdout.fileno(), False)
                selector.register(proc.stdout, selectors.EVENT_READ, name)
            except OSError:
                codes[name] = -1
        deadline = began + WINDOW_S
        while time.monotonic() < deadline:
            if node is not None:
                try:
                    ros.spin_once(node, timeout_sec=0)
                except Exception:
                    measure.raw['ros_clock']['availability'] = 'invalid'
                    node.destroy_node()
                    node = None
            for key, _ in selector.select(timeout=0):
                try:
                    chunk = os.read(key.fileobj.fileno(), 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if not measure.feed(key.data, chunk):
                    raise ValueError('observer_input_bound')
            if 'info' in processes and codes['info'] is None and time.monotonic() - began > 3:
                proc = processes['info']
                if proc.poll() is None:
                    proc.kill()
                codes['info'] = proc.wait(timeout=.2)
            time.sleep(.005)
        completed = True
        sampling_ended = time.monotonic()
    except BaseException:
        measure.raw['status'] = 'interrupted'
        sampling_ended = time.monotonic()
    finally:
        for name, proc in processes.items():
            try:
                if proc.poll() is None:
                    # The CLI's eight-second duration starts after child import
                    # and transport startup. Our wall window is the hard bound.
                    stopped_by_observer.add(name)
                    proc.terminate()
                code = proc.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait(timeout=.2)
            codes[name] = code
            proc.stdout.close()
        selector.close()
        if node is not None:
            node.destroy_node()
        if ros is not None:
            try:
                ros.shutdown()
            except Exception:
                pass
    measure.raw['observation_duration_s'] = sampling_ended - began
    # A normally timed-out echo is expected: our independent wall window starts
    # before CLI startup. Only the parent-confirmed window may be published.
    result = measure.finish(codes['info'], 0 if completed and 'echo' in stopped_by_observer and codes['echo'] == -signal.SIGTERM else codes['echo'], sampling_ended)
    if not completed:
        result['status'] = 'interrupted'
    print(json.dumps(result, allow_nan=False, separators=(',', ':')), flush=True)
    return 0 if completed else 1


if __name__ == '__main__':
    raise SystemExit(main())

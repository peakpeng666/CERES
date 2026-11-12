#!/usr/bin/python3
"""Isolated real Gazebo/bridge shutdown comparison; every exit and raw log retained.

Run with ROS environment sourced. No exit-code normalization or exit guard by default.
Only the subprocess groups created here are signaled.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = '/opt/ros/humble/lib/ros_gz_bridge/parameter_bridge'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cycles', type=int, default=1)
    p.add_argument('--bridges', type=int, default=4)
    p.add_argument('--settle', type=float, default=3)
    p.add_argument('--library', type=Path)
    p.add_argument('--executable', type=Path, default=Path(BRIDGE))
    p.add_argument('--signal', choices=['SIGINT', 'SIGTERM'], default='SIGINT')
    p.add_argument('--preload', type=Path, help='Diagnostic instrumentation only; recorded in summary')
    p.add_argument('--memcheck', type=Path, help='Locally extracted Valgrind root directory')
    a = p.parse_args()
    a.output = a.output.resolve()
    a.output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, ROS_DOMAIN_ID='178', ROS_LOCALHOST_ONLY='1',
               IGN_PARTITION=f'ceres_bridge_lifecycle_{os.getpid()}',
               ROS_LOG_DIR=str(a.output / 'ros-log'), IGN_HOMEDIR=str(a.output / 'ignition'))
    env.pop('LD_PRELOAD', None)
    Path(env['IGN_HOMEDIR']).mkdir()
    summary = {'unit': 'individual parameter_bridge process exit', 'cycles': [],
               'bridge_sha256': hashlib.sha256(a.executable.read_bytes()).hexdigest(),
               'signal': a.signal, 'preload': str(a.preload.resolve()) if a.preload else None,
               'library': str(a.library.resolve()) if a.library else None,
               'exit_guard': False, 'memcheck': bool(a.memcheck)}
    if a.preload:
        env['LD_PRELOAD'] = str(a.preload.resolve())
    if a.library:
        lib = a.library.resolve()
        env['LD_LIBRARY_PATH'] = str(lib.parent) + ':' + env.get('LD_LIBRARY_PATH', '')
        summary['library_sha256'] = hashlib.sha256(lib.read_bytes()).hexdigest()
    prefix = []
    if a.memcheck:
        vg = a.memcheck.resolve()
        env['VALGRIND_LIB'] = str(vg / 'usr/libexec/valgrind')
        prefix = [str(vg / 'usr/bin/valgrind'), '--tool=memcheck', '--leak-check=no',
                  '--track-origins=yes', '--num-callers=35', '--error-exitcode=97',
                  '--log-file=' + str(a.output / 'memcheck-%p.log')]
    topics = ['/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock',
              '/world/ceres_setup_smoke/stats@ros_gz_interfaces/msg/WorldStatistics[ignition.msgs.WorldStatistics',
              '/world/ceres_setup_smoke/pose/info@tf2_msgs/msg/TFMessage[ignition.msgs.Pose_V',
              '/ceres_repro_cmd@geometry_msgs/msg/Twist]ignition.msgs.Twist']
    children = []
    with (a.output / 'server.log').open('w') as log:
        server = subprocess.Popen(['ign', 'gazebo', '-s', '-r', '-v', '1',
                                   str(ROOT / 'setup/2026-10-15/minimal-world.sdf')],
                                  env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        time.sleep(4)
        if server.poll() is not None:
            raise RuntimeError('Gazebo exited before bridge startup')
        for cycle in range(a.cycles):
            batch = []
            for i in range(a.bridges):
                path = a.output / f'c{cycle:03d}-b{i:02d}.log'
                cmd = prefix + [str(a.executable.resolve()), topics[i % len(topics)], '--ros-args', '-r', f'__node:=repro_{cycle}_{i}']
                with path.open('w') as stream:
                    child = subprocess.Popen(cmd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                             start_new_session=True)
                batch.append((child, path, cmd))
                children.append(child)
            deadline = time.monotonic() + (90 if a.memcheck else 15)
            while time.monotonic() < deadline:
                if all(b'Creating ' in path.read_bytes() or child.poll() is not None for child, path, _ in batch):
                    break
                time.sleep(.1)
            time.sleep(a.settle)
            rows = []
            for child, path, cmd in batch:
                row = {'pid': child.pid, 'log': path.name, 'command': cmd,
                       'ready': b'Creating ' in path.read_bytes(), 'premature_exit': child.poll(), 'escalated': False}
                if child.poll() is None:
                    child.send_signal(getattr(signal, a.signal))
                rows.append(row)
            for (child, path, cmd), row in zip(batch, rows):
                try:
                    row['returncode'] = child.wait(timeout=60 if a.memcheck else 15)
                except subprocess.TimeoutExpired:
                    row['escalated'] = True
                    os.killpg(child.pid, signal.SIGKILL)
                    row['returncode'] = child.wait()
            summary['cycles'].append(rows)
            (a.output / 'summary.json').write_text(json.dumps(summary, indent=2))
            print(cycle, [(r['pid'], r['returncode']) for r in rows], flush=True)
    finally:
        for child in [*children, server]:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGINT)
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
        summary['server_returncode'] = server.returncode
        rows = [r for batch in summary['cycles'] for r in batch]
        summary.update(exits=len(rows), abnormal=sum(r['returncode'] != 0 for r in rows),
                       not_ready=sum(not r['ready'] for r in rows),
                       premature_exits=sum(r['premature_exit'] is not None for r in rows))
        (a.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print({k: v for k, v in summary.items() if k != 'cycles'}, flush=True)


if __name__ == '__main__':
    main()

"""One local physics session per user, before ROS subscriptions or children exist.

Shared by TB4, field missions and response probes, regardless of gateway/ROS
domain/runtime directory. flock is released by the OS even on process failure;
the lock file is never unlinked (unlinking would permit two independent locks).
"""
from contextlib import contextmanager
import fcntl
from functools import wraps
import json
import os
from pathlib import Path
import sys


class PhysicsBusy(RuntimeError):
    pass


@contextmanager
def physical_session(path=None):
    path = Path(path) if path else Path.home() / '.cache' / 'ceres' / 'physical-session.lock'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner = os.read(fd, 4096).decode(errors='replace')
            raise PhysicsBusy(f'已有 CERES 物理会话运行，拒绝并发启动；请先关闭该会话。持有者：{owner}') from None
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({'pid': os.getpid(), 'entry': Path(sys.argv[0]).name}).encode())
        yield
    finally:
        os.close(fd)


def exclusive_physics(main):
    @wraps(main)
    def run():
        try:
            with physical_session():
                return main()
        except PhysicsBusy as error:
            print(str(error), file=sys.stderr, flush=True)
            return 2
    return run

"""POSIX names the process code relies on, provided on Windows.

The gateway groups, signals and kills child processes the POSIX way. On Windows a child's tree is ended with taskkill, so
``os.killpg`` becomes that and the signal names it needs exist. Importing this module on Linux changes nothing.
"""
from __future__ import annotations

import os
import signal
import subprocess


class _Kill(int):
    """Windows has no SIGKILL; the shutdown code reads the name for its log."""
    name = 'SIGKILL'


def _kill_tree(pid: int, sig: int):
    """A child without a console has no handler a polite request could reach, so every signal ends the tree."""
    done = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, text=True, check=False,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    if done.returncode == 128:          # no such process
        raise ProcessLookupError(pid)


if os.name == 'nt':
    if not hasattr(signal, 'SIGKILL'):
        signal.SIGKILL = _Kill(9)
    os.killpg = _kill_tree

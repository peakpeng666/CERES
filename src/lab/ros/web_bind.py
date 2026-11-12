"""Where the browser opens live ROS and Gazebo sockets.

The laptop default is loopback for both the listen address and the URL the
panel is given. Docker Desktop and WSL publish or forward those ports to the
Windows browser, so the process may listen on all interfaces while the page
still opens ``ws://127.0.0.1``.
"""
from __future__ import annotations

import os


def foxglove_address() -> str:
    return os.environ.get('CERES_FOXGLOVE_BIND', '127.0.0.1') or '127.0.0.1'


def browser_ws(port: int) -> str:
    host = os.environ.get('CERES_BROWSER_HOST', '127.0.0.1') or '127.0.0.1'
    return f'ws://{host}:{port}'

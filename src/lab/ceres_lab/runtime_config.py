"""Settings for `python -m ceres_lab serve`. Unset variables keep the laptop on loopback."""
from __future__ import annotations

import os

from .services import ARCHIVE_PORT


def extra_hosts() -> set[str]:
    return {item.strip() for item in os.environ.get('CERES_EXTRA_HOSTS', '').split(',') if item.strip()}


def archive_url() -> str:
    return os.environ.get('CERES_ARCHIVE_URL', f'http://127.0.0.1:{ARCHIVE_PORT}').rstrip('/')


def archive_autostart(disabled_by_flag: bool) -> bool:
    if disabled_by_flag:
        return False
    return os.environ.get('CERES_ARCHIVE_AUTOSTART', '1') != '0'


def admin_token() -> str:
    return os.environ.get('CERES_ADMIN_TOKEN', '')

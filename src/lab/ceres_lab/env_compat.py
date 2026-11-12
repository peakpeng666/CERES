"""Read existing installations without moving their data or credentials."""
from __future__ import annotations

import os
from pathlib import Path


def normalize_environment(env):
    """CERES names win, including deliberately empty values."""
    result = dict(env)
    for key, value in env.items():
        if key.startswith('URP_'):
            result.setdefault('CERES_' + key[4:], value)
    return result


def config_directory():
    explicit = os.environ.get('CERES_CONFIG_DIR') or os.environ.get('URP_CONFIG_DIR')
    if explicit:
        return Path(explicit).expanduser()
    current = Path.home() / '.config' / 'ceres-workbench'
    legacy = Path.home() / '.config' / 'urp-workbench'
    return legacy if not current.exists() and legacy.is_dir() else current

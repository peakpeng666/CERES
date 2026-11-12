"""Map-only cloud checks may use a verified local asset copy, not a fake ROS install."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ceres_lab import paths
from ceres_lab.scenarios import maps


def read_map_path(env):
    return subprocess.check_output(
        [sys.executable, '-c', 'from ceres_lab.paths import ROS_MAPS; print(ROS_MAPS)'],
        cwd=Path(__file__).resolve().parents[1], env=env, text=True,
    ).strip()


def test_default_official_map_location():
    env = dict(os.environ)
    env.pop('CERES_ROS_MAPS', None)
    assert read_map_path(env) == str(paths.ROS_INSTALL_MAPS.resolve() if paths.ROS_INSTALL_MAPS.is_dir() else paths.BUNDLED_MAPS)


def test_machine_without_ros_uses_the_bundled_copies(tmp_path, monkeypatch):
    monkeypatch.delenv('CERES_ROS_MAPS', raising=False)
    assert paths.official_maps(tmp_path / 'no-ros') == paths.BUNDLED_MAPS
    for world in maps.WORLDS:
        assert (paths.BUNDLED_MAPS / f'{world}.yaml').exists()
        assert (paths.BUNDLED_MAPS / f'{world}.pgm').exists()


def test_bundled_scenarios_build_without_ros(monkeypatch):
    monkeypatch.setattr(paths, 'ROS_MAPS', paths.BUNDLED_MAPS)
    maps.load.cache_clear()
    try:
        assert set(maps.known_worlds()) == set(maps.WORLDS)
        assert maps.load('maze').free.any()
    finally:
        maps.load.cache_clear()


@pytest.mark.skipif(not paths.ROS_INSTALL_MAPS.is_dir(), reason='ROS is not installed here')
def test_bundled_copies_match_the_installed_maps():
    for copy in [*paths.BUNDLED_MAPS.glob('*.yaml'), *paths.BUNDLED_MAPS.glob('*.pgm')]:
        assert copy.read_bytes() == (paths.ROS_INSTALL_MAPS / copy.name).read_bytes()


def test_explicit_map_copy_is_resolved(tmp_path):
    env = dict(os.environ, CERES_ROS_MAPS=str(tmp_path / 'maps'))
    assert read_map_path(env) == str((tmp_path / 'maps').resolve())
